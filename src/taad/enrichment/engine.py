"""Historical trade enrichment engine.

Orchestrates the enrichment of imported historical trades by populating
TradeEntrySnapshot and TradeExitSnapshot records with reconstructed market
context from yfinance, IBKR historical data, and Black-Scholes approximations.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import Trade, TradeEntrySnapshot, TradeExitSnapshot
from src.taad.enrichment.bs_iv_solver import (
    solve_iv_and_greeks,
    get_risk_free_rate,
    BSResult,
)
from src.taad.enrichment.historical_context import (
    build_historical_context,
    HistoricalMarketContext,
)
from src.taad.enrichment.historical_indicators import (
    calculate_indicators_from_bars,
    calculate_trend_from_bars,
    calculate_hv_20,
    calculate_hv_rank,
    calculate_beta,
    TechnicalIndicators,
)
from src.taad.enrichment.providers import (
    HistoricalDataProvider,
    OptionSnapshot,
)


@dataclass
class EnrichmentResult:
    """Result of enriching a single trade."""

    trade_id: int
    symbol: str
    success: bool = False
    entry_snapshot_created: bool = False
    entry_snapshot_merged: bool = False
    exit_snapshot_created: bool = False
    quality_score: float = 0.0
    errors: list[str] = field(default_factory=list)
    fields_populated: int = 0
    fields_total: int = 86  # Total fields in TradeEntrySnapshot


@dataclass
class EnrichmentBatchResult:
    """Result of enriching a batch of trades."""

    total: int = 0
    enriched: int = 0
    merged: int = 0
    skipped: int = 0
    failed: int = 0
    avg_quality: float = 0.0
    results: list[EnrichmentResult] = field(default_factory=list)


class HistoricalEnrichmentEngine:
    """Enriches historical trades with reconstructed market context.

    Populates TradeEntrySnapshot and TradeExitSnapshot records using
    historical data from providers (yfinance, IBKR, B-S approximation).
    """

    # Columns to never overwrite during merge (metadata/identity)
    _MERGE_EXCLUDE_COLS = frozenset({
        "id", "trade_id", "opportunity_id", "captured_at", "source",
        "data_quality_score", "notes",
    })

    # Non-nullable identity columns — always set from Trade, never change
    _IDENTITY_COLS = frozenset({
        "symbol", "strike", "expiration", "option_type",
        "entry_premium", "stock_price", "dte", "contracts",
    })

    # Exit snapshot columns to never overwrite during merge
    _EXIT_MERGE_EXCLUDE = frozenset({
        "id", "trade_id", "captured_at", "trade_quality_score",
        "exit_date", "exit_premium", "exit_reason", "days_held",
    })

    def __init__(
        self,
        provider: HistoricalDataProvider,
        session: Session,
    ):
        """Initialize enrichment engine.

        Args:
            provider: Historical data provider (YFinance, FallbackChain, etc.)
            session: SQLAlchemy database session
        """
        self.provider = provider
        self.session = session

    def _merge_snapshot_fields(
        self,
        existing: TradeEntrySnapshot,
        fresh: TradeEntrySnapshot,
    ) -> int:
        """Merge fresh snapshot data into existing snapshot (gap-fill only).

        For each column on TradeEntrySnapshot:
        - If fresh value is not None and existing value IS None, copy fresh into existing.
        - If existing value is already non-None, keep it (preserve).
        - Metadata and identity columns are never overwritten.

        Args:
            existing: The existing snapshot row (mutated in-place via SQLAlchemy).
            fresh: The freshly-built snapshot (read-only source of new data).

        Returns:
            Number of fields that were filled in (gap-filled count).
        """
        filled = 0
        skip = self._MERGE_EXCLUDE_COLS | self._IDENTITY_COLS
        for col in existing.__table__.columns:
            name = col.name
            if name in skip:
                continue
            existing_val = getattr(existing, name, None)
            fresh_val = getattr(fresh, name, None)
            if existing_val is None and fresh_val is not None:
                setattr(existing, name, fresh_val)
                filled += 1
        return filled

    def _force_merge_snapshot_fields(
        self,
        existing: TradeEntrySnapshot,
        fresh: TradeEntrySnapshot,
    ) -> int:
        """Overwrite existing snapshot with fresh data, preserving old where fresh is None.

        Like _merge_snapshot_fields but fresh values WIN over existing:
        - If fresh value is not None, overwrite existing (even if existing was non-None).
        - If fresh value is None and existing is non-None, keep existing (preserve).
        - Metadata and identity columns are never overwritten.

        Args:
            existing: The existing snapshot row (mutated in-place via SQLAlchemy).
            fresh: The freshly-built snapshot (source of new data; fresh wins).

        Returns:
            Number of fields that were updated from fresh data.
        """
        updated = 0
        skip = self._MERGE_EXCLUDE_COLS | self._IDENTITY_COLS
        for col in existing.__table__.columns:
            name = col.name
            if name in skip:
                continue
            fresh_val = getattr(fresh, name, None)
            if fresh_val is not None:
                setattr(existing, name, fresh_val)
                updated += 1
            # else: keep existing value (old data preserved where fresh can't populate)
        return updated

    def _merge_exit_snapshot_fields(
        self,
        existing: TradeExitSnapshot,
        fresh: TradeExitSnapshot,
    ) -> int:
        """Merge fresh exit snapshot data into existing (gap-fill only).

        Returns:
            Number of fields that were filled in.
        """
        filled = 0
        for col in existing.__table__.columns:
            name = col.name
            if name in self._EXIT_MERGE_EXCLUDE:
                continue
            existing_val = getattr(existing, name, None)
            fresh_val = getattr(fresh, name, None)
            if existing_val is None and fresh_val is not None:
                setattr(existing, name, fresh_val)
                filled += 1
        return filled

    def _force_merge_exit_snapshot_fields(
        self,
        existing: TradeExitSnapshot,
        fresh: TradeExitSnapshot,
    ) -> int:
        """Overwrite existing exit snapshot with fresh data, preserving old where fresh is None.

        Returns:
            Number of fields updated from fresh data.
        """
        updated = 0
        for col in existing.__table__.columns:
            name = col.name
            if name in self._EXIT_MERGE_EXCLUDE:
                continue
            fresh_val = getattr(fresh, name, None)
            if fresh_val is not None:
                setattr(existing, name, fresh_val)
                updated += 1
        return updated

    def enrich_trade(
        self, trade: Trade, force: bool = False
    ) -> EnrichmentResult:
        """Enrich a single trade with historical market context.

        Args:
            trade: Trade object to enrich
            force: If True, delete existing snapshots and re-enrich

        Returns:
            EnrichmentResult with outcome details
        """
        result = EnrichmentResult(
            trade_id=trade.id,
            symbol=trade.symbol,
        )

        try:
            # Check for existing snapshots
            existing_entry = (
                self.session.query(TradeEntrySnapshot)
                .filter_by(trade_id=trade.id)
                .first()
            )

            if existing_entry and not force:
                # MERGE mode: build fresh data, fill gaps in existing snapshot
                fresh_entry = self._build_entry_snapshot(trade, result)
                if fresh_entry:
                    fields_filled = self._merge_snapshot_fields(existing_entry, fresh_entry)

                    # Recalculate quality score after merge
                    existing_entry.data_quality_score = (
                        existing_entry.calculate_data_quality_score()
                    )
                    result.quality_score = existing_entry.data_quality_score
                    result.fields_populated = self._count_populated_fields(existing_entry)

                    if fields_filled > 0:
                        result.entry_snapshot_merged = True
                        logger.info(
                            f"Merged trade {trade.id} ({trade.symbol}): "
                            f"{fields_filled} fields filled, "
                            f"quality={result.quality_score:.3f}"
                        )
                    else:
                        logger.debug(
                            f"Trade {trade.id} ({trade.symbol}) fully enriched, "
                            f"no new fields to fill"
                        )
                else:
                    result.quality_score = existing_entry.data_quality_score or 0.0

                # Handle exit snapshot: merge if exists, create if missing
                if trade.is_closed():
                    existing_exit = (
                        self.session.query(TradeExitSnapshot)
                        .filter_by(trade_id=trade.id)
                        .first()
                    )
                    if existing_exit:
                        fresh_exit = self._build_exit_snapshot(
                            trade, fresh_entry or existing_entry, result
                        )
                        if fresh_exit:
                            exit_filled = self._merge_exit_snapshot_fields(
                                existing_exit, fresh_exit
                            )
                            if exit_filled > 0:
                                existing_exit.trade_quality_score = (
                                    existing_exit.calculate_quality_score()
                                )
                    else:
                        fresh_exit = self._build_exit_snapshot(
                            trade, existing_entry, result
                        )
                        if fresh_exit:
                            self.session.add(fresh_exit)
                            result.exit_snapshot_created = True

                # Update trade columns from the (now improved) existing snapshot
                self._update_trade_columns(trade, existing_entry)

                self.session.flush()
                result.success = True
                return result

            if existing_entry and force:
                # FORCE mode: build fresh data, overwrite existing with all
                # fresh non-null values, but preserve old values where fresh
                # can't populate (e.g. margin_requirement from IBKR backfill).
                fresh_entry = self._build_entry_snapshot(trade, result)
                if fresh_entry:
                    updated = self._force_merge_snapshot_fields(
                        existing_entry, fresh_entry
                    )
                    logger.info(
                        f"Force-enriched trade {trade.id} ({trade.symbol}): "
                        f"{updated} fields refreshed"
                    )

                # Recalculate quality score
                existing_entry.data_quality_score = (
                    existing_entry.calculate_data_quality_score()
                )
                result.quality_score = existing_entry.data_quality_score
                result.fields_populated = self._count_populated_fields(existing_entry)
                result.entry_snapshot_created = True

                # Handle exit snapshot
                if trade.is_closed():
                    existing_exit = (
                        self.session.query(TradeExitSnapshot)
                        .filter_by(trade_id=trade.id)
                        .first()
                    )
                    if existing_exit:
                        fresh_exit = self._build_exit_snapshot(
                            trade, fresh_entry or existing_entry, result
                        )
                        if fresh_exit:
                            self._force_merge_exit_snapshot_fields(
                                existing_exit, fresh_exit
                            )
                            existing_exit.trade_quality_score = (
                                existing_exit.calculate_quality_score()
                            )
                    else:
                        fresh_exit = self._build_exit_snapshot(
                            trade, existing_entry, result
                        )
                        if fresh_exit:
                            self.session.add(fresh_exit)
                            result.exit_snapshot_created = True

                # Update trade columns
                self._update_trade_columns(trade, existing_entry)

                self.session.flush()
                result.success = True
                logger.info(
                    f"Enriched trade {trade.id} ({trade.symbol} {trade.strike}P "
                    f"exp={trade.expiration}): quality={result.quality_score:.3f}"
                )
                return result

            # Build entry snapshot (no existing — fresh creation)
            entry_snapshot = self._build_entry_snapshot(trade, result)
            if entry_snapshot:
                self.session.add(entry_snapshot)
                result.entry_snapshot_created = True

            # Build exit snapshot (if trade is closed)
            if trade.is_closed():
                exit_snapshot = self._build_exit_snapshot(trade, entry_snapshot, result)
                if exit_snapshot:
                    self.session.add(exit_snapshot)
                    result.exit_snapshot_created = True

            # Update trade columns
            if entry_snapshot:
                self._update_trade_columns(trade, entry_snapshot)

            self.session.flush()
            result.success = True
            logger.info(
                f"Enriched trade {trade.id} ({trade.symbol} {trade.strike}P "
                f"exp={trade.expiration}): quality={result.quality_score:.3f}"
            )

        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"Failed to enrich trade {trade.id} ({trade.symbol}): {e}")

        return result

    def enrich_batch(
        self, trades: list[Trade], force: bool = False
    ) -> EnrichmentBatchResult:
        """Enrich a batch of trades.

        Args:
            trades: List of Trade objects to enrich
            force: If True, re-enrich already-enriched trades

        Returns:
            EnrichmentBatchResult with aggregate statistics
        """
        batch = EnrichmentBatchResult(total=len(trades))
        quality_scores = []

        for i, trade in enumerate(trades):
            logger.info(f"Enriching trade {i + 1}/{len(trades)}: {trade.symbol} {trade.strike}P")

            result = self.enrich_trade(trade, force=force)
            batch.results.append(result)

            if result.success:
                if result.entry_snapshot_created:
                    batch.enriched += 1
                    quality_scores.append(result.quality_score)
                elif result.entry_snapshot_merged:
                    batch.merged += 1
                    quality_scores.append(result.quality_score)
                else:
                    batch.skipped += 1
            else:
                batch.failed += 1

        if quality_scores:
            batch.avg_quality = round(sum(quality_scores) / len(quality_scores), 3)

        logger.info(
            f"Enrichment batch complete: {batch.enriched} enriched, "
            f"{batch.merged} merged, "
            f"{batch.skipped} skipped, {batch.failed} failed, "
            f"avg quality={batch.avg_quality:.3f}"
        )

        return batch

    def _build_entry_snapshot(
        self, trade: Trade, result: EnrichmentResult
    ) -> Optional[TradeEntrySnapshot]:
        """Build TradeEntrySnapshot from historical data.

        Populates fields in order:
        1. From Trade itself
        2. Stock OHLCV (yfinance)
        3. Derived metrics
        4. Trend indicators
        5. Technical indicators
        6. Market context (indices, sector, regime, calendar)
        7. Earnings
        8. Historical volatility
        9. B-S IV approximation
        """
        entry_date = trade.entry_date
        if hasattr(entry_date, "date"):
            trade_date = entry_date.date()
        else:
            trade_date = entry_date

        snapshot = TradeEntrySnapshot(
            trade_id=trade.id,
            # 1. From Trade
            symbol=trade.symbol,
            strike=trade.strike,
            expiration=trade.expiration,
            option_type=trade.option_type or "PUT",
            entry_premium=trade.entry_premium,
            contracts=trade.contracts,
            dte=trade.dte,
            captured_at=datetime.now(),
            source="historical_enrichment",
        )

        # 2. Stock OHLCV
        try:
            stock_bar = self.provider.get_stock_bar(trade.symbol, trade_date)
            if stock_bar:
                snapshot.stock_price = stock_bar.close
                snapshot.stock_open = stock_bar.open
                snapshot.stock_high = stock_bar.high
                snapshot.stock_low = stock_bar.low

                # Previous day for change calculation
                from datetime import timedelta
                prev_bar = self.provider.get_stock_bar(
                    trade.symbol, trade_date - timedelta(days=1)
                )
                if prev_bar:
                    snapshot.stock_prev_close = prev_bar.close
                    if prev_bar.close > 0:
                        snapshot.stock_change_pct = round(
                            (stock_bar.close - prev_bar.close) / prev_bar.close, 6
                        )
            else:
                result.errors.append(f"No stock bar for {trade.symbol} on {trade_date}")
                # Can't proceed without stock price
                snapshot.stock_price = 0.0
        except Exception as e:
            result.errors.append(f"Stock OHLCV: {e}")
            snapshot.stock_price = 0.0

        # 3. Derived metrics
        if snapshot.stock_price and snapshot.stock_price > 0:
            snapshot.otm_pct = round(
                (snapshot.stock_price - trade.strike) / snapshot.stock_price, 6
            )
            snapshot.otm_dollars = round(snapshot.stock_price - trade.strike, 2)

        # 4+5. Trend + Technical indicators (from historical bars)
        try:
            bars = self.provider.get_historical_bars(trade.symbol, trade_date, 130)
            if bars is not None and len(bars) >= 50:
                closes = bars["Close"].values
                highs = bars["High"].values
                lows = bars["Low"].values
                price = snapshot.stock_price or closes[-1]

                # Trend
                trend = calculate_trend_from_bars(bars, price)
                snapshot.sma_20 = trend["sma_20"]
                snapshot.sma_50 = trend["sma_50"]
                snapshot.trend_direction = trend["trend_direction"]
                snapshot.trend_strength = trend["trend_strength"]
                snapshot.price_vs_sma20_pct = trend["price_vs_sma20_pct"]
                snapshot.price_vs_sma50_pct = trend["price_vs_sma50_pct"]

                # Technical indicators
                indicators = calculate_indicators_from_bars(closes, highs, lows, price)
                snapshot.rsi_14 = indicators.rsi_14
                snapshot.rsi_7 = indicators.rsi_7
                snapshot.macd = indicators.macd
                snapshot.macd_signal = indicators.macd_signal
                snapshot.macd_histogram = indicators.macd_histogram
                snapshot.adx = indicators.adx
                snapshot.plus_di = indicators.plus_di
                snapshot.minus_di = indicators.minus_di
                snapshot.atr_14 = indicators.atr_14
                snapshot.atr_pct = indicators.atr_pct
                snapshot.bb_upper = indicators.bb_upper
                snapshot.bb_lower = indicators.bb_lower
                snapshot.bb_position = indicators.bb_position
                snapshot.support_1 = indicators.support_1
                snapshot.support_2 = indicators.support_2
                snapshot.resistance_1 = indicators.resistance_1
                snapshot.resistance_2 = indicators.resistance_2
                snapshot.distance_to_support_pct = indicators.distance_to_support_pct

                # Historical volatility
                snapshot.hv_20 = calculate_hv_20(closes)

            else:
                result.errors.append(
                    f"Insufficient bars for {trade.symbol}: "
                    f"{len(bars) if bars is not None else 0}"
                )
        except Exception as e:
            result.errors.append(f"Technical indicators: {e}")

        # 6+7. Market context (indices, sector, regime, calendar, earnings)
        try:
            ctx = build_historical_context(
                trade.symbol, trade_date, trade.expiration, self.provider
            )
            snapshot.spy_price = ctx.spy_price
            snapshot.spy_change_pct = ctx.spy_change_pct
            snapshot.vix = ctx.vix
            snapshot.vix_change_pct = ctx.vix_change_pct
            snapshot.qqq_price = ctx.qqq_price
            snapshot.qqq_change_pct = ctx.qqq_change_pct
            snapshot.iwm_price = ctx.iwm_price
            snapshot.iwm_change_pct = ctx.iwm_change_pct
            snapshot.sector = ctx.sector
            snapshot.sector_etf = ctx.sector_etf
            snapshot.sector_change_1d = ctx.sector_change_1d
            snapshot.sector_change_5d = ctx.sector_change_5d
            snapshot.vol_regime = ctx.vol_regime
            snapshot.market_regime = ctx.market_regime
            snapshot.day_of_week = ctx.day_of_week
            snapshot.is_opex_week = ctx.is_opex_week
            snapshot.days_to_fomc = ctx.days_to_fomc
            snapshot.earnings_date = ctx.earnings_date
            snapshot.days_to_earnings = ctx.days_to_earnings
            snapshot.earnings_in_dte = ctx.earnings_in_dte
            snapshot.earnings_timing = ctx.earnings_timing
        except Exception as e:
            result.errors.append(f"Market context: {e}")

        # 9. Option data — try provider (Barchart) first, fall back to B-S
        option_data_populated = False
        try:
            opt_snap = self.provider.get_option_snapshot(
                trade.symbol,
                trade.strike,
                trade.expiration,
                trade.option_type or "P",
                trade_date,
            )
            if opt_snap and opt_snap.iv is not None:
                snapshot.iv = opt_snap.iv
                snapshot.delta = opt_snap.delta
                snapshot.gamma = opt_snap.gamma
                snapshot.theta = opt_snap.theta
                snapshot.vega = opt_snap.vega
                snapshot.rho = opt_snap.rho
                if opt_snap.bid is not None:
                    snapshot.option_bid = opt_snap.bid
                if opt_snap.ask is not None:
                    snapshot.option_ask = opt_snap.ask
                if opt_snap.volume is not None:
                    snapshot.option_volume = opt_snap.volume
                if opt_snap.open_interest is not None:
                    snapshot.open_interest = opt_snap.open_interest

                # IV/HV ratio
                if snapshot.hv_20 and snapshot.hv_20 > 0:
                    snapshot.iv_hv_ratio = round(opt_snap.iv / snapshot.hv_20, 4)
                option_data_populated = True
        except Exception as e:
            result.errors.append(f"Option snapshot: {e}")

        # 10. Black-Scholes IV approximation (fallback if no real option data)
        if not option_data_populated:
            try:
                if (
                    snapshot.stock_price
                    and snapshot.stock_price > 0
                    and trade.entry_premium > 0
                    and trade.dte > 0
                ):
                    r = get_risk_free_rate(trade_date.year)
                    T = trade.dte / 365.0
                    bs = solve_iv_and_greeks(
                        option_price=trade.entry_premium,
                        S=snapshot.stock_price,
                        K=trade.strike,
                        T=T,
                        r=r,
                        option_type=trade.option_type or "P",
                    )
                    if bs.iv is not None:
                        snapshot.iv = bs.iv
                        snapshot.delta = bs.delta
                        snapshot.gamma = bs.gamma
                        snapshot.theta = bs.theta
                        snapshot.vega = bs.vega
                        snapshot.rho = bs.rho

                        # IV/HV ratio
                        if snapshot.hv_20 and snapshot.hv_20 > 0:
                            snapshot.iv_hv_ratio = round(bs.iv / snapshot.hv_20, 4)
                    elif trade.dte <= 5:
                        # B-S IV intentionally skipped for low DTE — note why
                        note = (
                            f"B-S IV/Greeks omitted: DTE={trade.dte} <= 5, "
                            f"B-S approximation unreliable at low DTE"
                        )
                        snapshot.notes = (
                            f"{snapshot.notes}; {note}" if snapshot.notes else note
                        )
            except Exception as e:
                result.errors.append(f"B-S IV: {e}")

        # Calculate quality score
        snapshot.data_quality_score = snapshot.calculate_data_quality_score()
        result.quality_score = snapshot.data_quality_score

        # Count populated fields
        result.fields_populated = self._count_populated_fields(snapshot)

        return snapshot

    def _build_exit_snapshot(
        self,
        trade: Trade,
        entry_snapshot: Optional[TradeEntrySnapshot],
        result: EnrichmentResult,
    ) -> Optional[TradeExitSnapshot]:
        """Build TradeExitSnapshot from trade data.

        Uses trade P&L fields and historical data for context changes.
        """
        if not trade.exit_date or trade.exit_premium is None:
            return None

        exit_date = trade.exit_date
        if hasattr(exit_date, "date"):
            exit_date_d = exit_date.date()
        else:
            exit_date_d = exit_date

        snapshot = TradeExitSnapshot(
            trade_id=trade.id,
            exit_date=trade.exit_date,
            exit_premium=trade.exit_premium,
            exit_reason=trade.exit_reason or "unknown",
            days_held=trade.days_held,
            captured_at=datetime.now(),
        )

        # P&L
        if trade.profit_loss is not None:
            snapshot.gross_profit = trade.profit_loss
            snapshot.net_profit = trade.profit_loss
            if trade.commission:
                snapshot.net_profit = trade.profit_loss - trade.commission

        if trade.profit_pct is not None:
            snapshot.roi_pct = trade.profit_pct

        snapshot.win = trade.is_profitable() if trade.profit_loss is not None else None

        # Context changes during trade
        try:
            # Stock price at exit
            stock_exit = self.provider.get_stock_bar(trade.symbol, exit_date_d)
            if stock_exit:
                snapshot.stock_price_at_exit = stock_exit.close
                if entry_snapshot and entry_snapshot.stock_price and entry_snapshot.stock_price > 0:
                    snapshot.stock_change_during_trade_pct = round(
                        (stock_exit.close - entry_snapshot.stock_price) / entry_snapshot.stock_price, 6
                    )

            # VIX at exit
            vix_exit = self.provider.get_vix_close(exit_date_d)
            if vix_exit:
                snapshot.vix_at_exit = vix_exit
                if entry_snapshot and entry_snapshot.vix:
                    snapshot.vix_change_during_trade = round(vix_exit - entry_snapshot.vix, 2)

        except Exception as e:
            result.errors.append(f"Exit context: {e}")

        # Quality score
        snapshot.trade_quality_score = snapshot.calculate_quality_score()

        return snapshot

    def _update_trade_columns(
        self, trade: Trade, entry_snapshot: TradeEntrySnapshot
    ) -> None:
        """Update trade-level columns from enrichment data."""
        trade.vix_at_entry = entry_snapshot.vix
        trade.spy_price_at_entry = entry_snapshot.spy_price
        trade.sector = entry_snapshot.sector
        trade.market_regime = entry_snapshot.market_regime
        trade.enrichment_status = "complete"
        trade.enrichment_quality = entry_snapshot.data_quality_score

    def _count_populated_fields(self, snapshot: TradeEntrySnapshot) -> int:
        """Count the number of non-None fields in a snapshot."""
        # Fields to exclude from count (metadata, not data)
        exclude = {"id", "trade_id", "opportunity_id", "captured_at", "source", "notes"}

        count = 0
        for col in snapshot.__table__.columns:
            if col.name in exclude:
                continue
            val = getattr(snapshot, col.name, None)
            if val is not None:
                count += 1
        return count


def calculate_historical_quality(
    snapshot: TradeEntrySnapshot, era: str = "pre2023"
) -> float:
    """Calculate quality score adjusted for historical data availability.

    Pre-2023 trades can't have live option data (bid/ask, real Greeks, IV rank).
    This scorer adjusts the denominator to be fair.

    Args:
        snapshot: TradeEntrySnapshot to score
        era: "pre2023" or "post2023"

    Returns:
        Quality score 0.0-1.0
    """
    if era == "post2023":
        # Full scoring — all fields weighted normally
        return snapshot.calculate_data_quality_score()

    # Pre-2023: B-S approximations count at 50% weight
    # Exclude live-only fields from denominator

    def completeness(field_values: list, weight_factor: float = 1.0) -> float:
        if not field_values:
            return 0.0
        non_none = sum(1 for f in field_values if f is not None)
        return (non_none / len(field_values)) * weight_factor

    # Critical fields (B-S provides iv, delta but not iv_rank, margin)
    critical = [
        snapshot.delta,          # B-S approximation
        snapshot.iv,             # B-S approximation
        None,                    # iv_rank — not available historically
        snapshot.vix,            # yfinance
        snapshot.dte,            # from trade
        snapshot.trend_direction,  # from bars
        snapshot.days_to_earnings,  # yfinance
        None,                    # margin — not available historically
    ]

    # Greeks from B-S at 50% weight
    greeks = [snapshot.delta, snapshot.gamma, snapshot.theta, snapshot.vega, snapshot.rho]
    volatility = [snapshot.iv, None, None, snapshot.hv_20, snapshot.iv_hv_ratio]
    stock_prices = [
        snapshot.stock_open, snapshot.stock_high, snapshot.stock_low,
        snapshot.stock_prev_close, snapshot.stock_change_pct,
    ]
    trend = [
        snapshot.sma_20, snapshot.sma_50, snapshot.trend_direction,
        snapshot.trend_strength, snapshot.price_vs_sma20_pct, snapshot.price_vs_sma50_pct,
    ]
    market = [snapshot.spy_price, snapshot.spy_change_pct, snapshot.vix, snapshot.vix_change_pct]
    event = [snapshot.earnings_date, snapshot.days_to_earnings, snapshot.earnings_in_dte]

    weights = {
        "critical": 0.40,
        "greeks": 0.05,       # Reduced from 0.10 — B-S approximation
        "volatility": 0.05,   # Reduced — partial availability
        "stock_prices": 0.10, # Increased — always available
        "trend": 0.15,        # Increased — always available
        "market": 0.15,       # Increased — always available
        "event": 0.10,        # Increased — partially available
    }

    scores = {
        "critical": completeness(critical),
        "greeks": completeness(greeks, 0.5),     # 50% weight for B-S
        "volatility": completeness(volatility, 0.5),
        "stock_prices": completeness(stock_prices),
        "trend": completeness(trend),
        "market": completeness(market),
        "event": completeness(event),
    }

    total = sum(scores[k] * weights[k] for k in scores)
    return round(total, 3)
