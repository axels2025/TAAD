"""IBKR Market Scanner service for discovering trade candidates.

Wraps reqScannerData() with TagValue filter options to find stocks
matching naked put criteria (high IV, adequate liquidity, mid/large cap).

Uses CLIENT_ID=21 to avoid conflicts with the daemon (10) and other scripts (20).
Connect-per-scan pattern: each scan opens and closes its own IBKR connection.

IMPORTANT: Additional filters (market cap, volume, option volume, stock type)
must be passed via scannerSubscriptionFilterOptions (TagValue pairs), NOT via
the native ScannerSubscription fields. Market cap values are in MILLIONS
(e.g., marketCapAbove1e6=2000 means market cap >= $2B).
"""

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

try:
    from ib_insync import IB, Index, Option, ScannerSubscription, Stock, TagValue, util

    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False

from src.utils.market_data import safe_field, safe_price

ET = ZoneInfo("America/New_York")


@dataclass
class ScannerConfig:
    """Scanner parameters for a single scan."""

    scan_code: str = "HIGH_OPT_IMP_VOLAT"
    instrument: str = "STK"
    location: str = "STK.US.MAJOR"
    min_price: float = 20.0
    max_price: float = 200.0
    num_rows: int = 50
    market_cap_above: float = 0  # In MILLIONS (2000 = $2B)
    market_cap_below: float = 0
    avg_volume_above: int = 0
    avg_opt_volume_above: int = 0
    stock_type: str = ""  # CORP, ADR, ETF, REIT, CEF


@dataclass
class ScannerResult:
    """Structured result from a single scanner row."""

    rank: int
    symbol: str
    con_id: int
    sec_type: str
    exchange: str
    long_name: str = ""
    industry: str = ""
    category: str = ""
    distance: str = ""
    benchmark: str = ""
    projection: str = ""
    legs_str: str = ""


@dataclass
class OptionChainRow:
    """A single PUT option from an IBKR option chain."""

    symbol: str
    expiration: str  # "2026-02-28"
    dte: int
    strike: float
    bid: float
    ask: float
    mid: float
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    iv: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    otm_pct: float = 0.0  # (stock_price - strike) / stock_price
    meets_criteria: bool = False  # delta 0.15-0.30, bid >= $0.30, OTM >= 5%


# Preset filter configurations for common use cases
# Note: market_cap_above is in MILLIONS (XML tag unit), so 2000 = $2B
SCANNER_PRESETS: dict[str, dict] = {
    "naked-put": {
        "label": "Naked Put Candidates",
        "description": "High IV stocks suitable for naked put selling ($2B+ cap, 500K+ vol)",
        "scan_code": "HIGH_OPT_IMP_VOLAT",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 200.0,
        "num_rows": 50,
        "market_cap_above": 2000,
        "avg_volume_above": 500000,
        "avg_opt_volume_above": 1000,
    },
    "iv-over-hist": {
        "label": "IV Over Historical",
        "description": "Stocks with IV significantly above historical levels ($5B+ cap)",
        "scan_code": "HIGH_OPT_IMP_VOLAT_OVER_HIST",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 300.0,
        "num_rows": 50,
        "market_cap_above": 5000,
        "avg_volume_above": 1000000,
        "avg_opt_volume_above": 5000,
    },
    "hot-options": {
        "label": "Hot by Option Volume",
        "description": "Stocks with unusual option activity ($5B+ cap)",
        "scan_code": "HOT_BY_OPT_VOLUME",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 500.0,
        "num_rows": 50,
        "market_cap_above": 5000,
        "avg_volume_above": 1000000,
        "avg_opt_volume_above": 5000,
    },
    "opt-volume-most-active": {
        "label": "Most Active Options",
        "description": "Highest option volume today ($2B+ cap)",
        "scan_code": "OPT_VOLUME_MOST_ACTIVE",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 500.0,
        "num_rows": 50,
        "market_cap_above": 2000,
        "avg_volume_above": 500000,
        "avg_opt_volume_above": 1000,
    },
    "high-dividend-yield": {
        "label": "High Dividend Yield",
        "description": "High dividend stocks for covered put strategies ($5B+ cap)",
        "scan_code": "HIGH_DIVIDEND_YIELD_IB",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 300.0,
        "num_rows": 50,
        "market_cap_above": 5000,
        "avg_volume_above": 500000,
        "avg_opt_volume_above": 500,
    },
    "put-call-ratio": {
        "label": "High Put/Call Ratio",
        "description": "Stocks with elevated put/call ratio ($2B+ cap)",
        "scan_code": "HIGH_OPT_VOLUME_PUT_CALL_RATIO",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 300.0,
        "num_rows": 50,
        "market_cap_above": 2000,
        "avg_volume_above": 500000,
        "avg_opt_volume_above": 1000,
    },
}

# Common scan codes for the UI dropdown
SCAN_CODES: dict[str, str] = {
    "HIGH_OPT_IMP_VOLAT": "High Option Implied Volatility",
    "HIGH_OPT_IMP_VOLAT_OVER_HIST": "High IV Over Historical",
    "HOT_BY_OPT_VOLUME": "Hot by Option Volume",
    "OPT_VOLUME_MOST_ACTIVE": "Most Active Options",
    "HIGH_DIVIDEND_YIELD_IB": "High Dividend Yield",
    "HIGH_OPT_VOLUME_PUT_CALL_RATIO": "High Put/Call Ratio",
    "TOP_PERC_GAIN": "Top % Gainers",
    "TOP_PERC_LOSE": "Top % Losers",
    "MOST_ACTIVE": "Most Active (Stock Volume)",
    "HOT_BY_VOLUME": "Hot by Stock Volume",
    "TOP_TRADE_RATE": "Top Trade Rate",
    "TOP_PRICE_RANGE": "Top Price Range",
}


class IBKRScannerService:
    """IBKR Market Scanner service with connect-per-scan lifecycle.

    Each scan creates its own IBKR connection (CLIENT_ID=21), runs the
    scanner subscription, parses results, and disconnects. This avoids
    holding connections open between user-triggered scans.
    """

    CLIENT_ID = 21

    def __init__(self):
        self._ib: Optional[IB] = None

    def connect(self) -> None:
        """Connect to IBKR TWS/Gateway."""
        if not IB_AVAILABLE:
            raise ImportError("ib_insync not installed. Run: pip install ib_insync")

        util.patchAsyncio()
        self._ib = IB()
        host = os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(os.getenv("IBKR_PORT", "7497"))

        logger.info(f"Scanner connecting to IBKR at {host}:{port} (client_id={self.CLIENT_ID})")
        self._ib.connect(host, port, clientId=self.CLIENT_ID, timeout=10)
        logger.info("Scanner connected to IBKR")

    def disconnect(self) -> None:
        """Disconnect from IBKR."""
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            logger.info("Scanner disconnected from IBKR")
        self._ib = None

    def is_connected(self) -> bool:
        """Check if connected to IBKR."""
        return self._ib is not None and self._ib.isConnected()

    def run_scan(self, config: ScannerConfig) -> list[ScannerResult]:
        """Run a market scanner with the given configuration.

        Connects to IBKR, executes the scan, parses results, and disconnects.

        Args:
            config: Scanner parameters (scan code, filters, etc.)

        Returns:
            List of ScannerResult objects

        Raises:
            ConnectionError: If IBKR connection fails
            RuntimeError: If scanner request fails
        """
        self.connect()
        try:
            return self._execute_scan(config)
        finally:
            self.disconnect()

    def run_preset(self, preset_name: str) -> list[ScannerResult]:
        """Run a scan using a named preset.

        Args:
            preset_name: Key from SCANNER_PRESETS

        Returns:
            List of ScannerResult objects

        Raises:
            ValueError: If preset_name is not found
        """
        if preset_name not in SCANNER_PRESETS:
            raise ValueError(f"Unknown preset: {preset_name}. Available: {list(SCANNER_PRESETS.keys())}")

        preset = SCANNER_PRESETS[preset_name]
        config = ScannerConfig(
            scan_code=preset.get("scan_code", "HIGH_OPT_IMP_VOLAT"),
            instrument=preset.get("instrument", "STK"),
            location=preset.get("location", "STK.US.MAJOR"),
            min_price=preset.get("min_price", 20.0),
            max_price=preset.get("max_price", 200.0),
            num_rows=preset.get("num_rows", 50),
            market_cap_above=preset.get("market_cap_above", 0),
            market_cap_below=preset.get("market_cap_below", 0),
            avg_volume_above=preset.get("avg_volume_above", 0),
            avg_opt_volume_above=preset.get("avg_opt_volume_above", 0),
            stock_type=preset.get("stock_type", ""),
        )
        return self.run_scan(config)

    def get_available_presets(self) -> dict:
        """Return available presets with metadata for UI display."""
        return {
            name: {
                "label": preset["label"],
                "description": preset["description"],
                "scan_code": preset["scan_code"],
                "defaults": {
                    k: v
                    for k, v in preset.items()
                    if k not in ("label", "description")
                },
            }
            for name, preset in SCANNER_PRESETS.items()
        }

    def get_account_summary(self) -> dict:
        """Get account summary from IBKR. Connect-per-call pattern.

        Returns:
            Dict of account summary tags to values. Numeric values are
            converted to float; others remain as strings.

        Raises:
            ConnectionError: If IBKR connection fails
        """
        self.connect()
        try:
            summary = {}
            for item in self._ib.accountSummary():
                try:
                    summary[item.tag] = float(item.value)
                except (ValueError, TypeError):
                    summary[item.tag] = item.value
            return summary
        finally:
            self.disconnect()

    def get_option_chain(
        self,
        symbol: str,
        max_dte: int = 7,
    ) -> dict:
        """Fetch PUT option chain with Greeks for a stock symbol.

        Connects to IBKR, discovers available expirations within max_dte,
        fetches Greeks for OTM put strikes, and returns structured chain data.

        Args:
            symbol: Stock ticker symbol (e.g., "AKAM")
            max_dte: Maximum days to expiration to include (default 7)

        Returns:
            Dict with keys: symbol, stock_price, expirations (list of
            {date, dte, puts: [OptionChainRow...]})

        Raises:
            ConnectionError: If IBKR connection fails
        """
        self.connect()
        try:
            return self._fetch_chain(symbol, max_dte)
        finally:
            self.disconnect()

    def get_vix(self) -> float | None:
        """Fetch current VIX price from IBKR. Connect-per-call pattern.

        Creates an Index contract for VIX on CBOE, requests a snapshot,
        and returns the price. Used for PositionSizer VIX scaling and
        config snapshots.

        Returns:
            VIX price as float, or None if unavailable.
        """
        self.connect()
        try:
            vix = Index("VIX", "CBOE")
            qualified = self._ib.qualifyContracts(vix)
            if not qualified or not qualified[0].conId:
                logger.warning("VIX: could not qualify contract")
                return None

            ticker = self._ib.reqMktData(qualified[0], "", True, False)
            self._ib.sleep(2)
            price = safe_price(ticker)
            self._ib.cancelMktData(qualified[0])

            if price:
                logger.info(f"VIX: {price:.2f}")
            else:
                logger.warning("VIX: no price data")
            return price
        finally:
            self.disconnect()

    def get_option_chains_batch(
        self, symbols: list[str], max_dte: int = 7
    ) -> dict[str, dict]:
        """Fetch PUT option chains for multiple symbols in a single connection.

        Connects once to IBKR, calls _fetch_chain() for each symbol, and
        disconnects once. Saves ~1s reconnection overhead per symbol
        compared to individual get_option_chain() calls.

        Args:
            symbols: List of stock ticker symbols.
            max_dte: Maximum days to expiration to include.

        Returns:
            Dict mapping symbol to chain data (same format as get_option_chain).
            Symbols that fail are included with empty expirations.
        """
        if not symbols:
            return {}

        results: dict[str, dict] = {}
        self.connect()
        try:
            for i, symbol in enumerate(symbols):
                logger.info(
                    f"Batch chains: loading {symbol} ({i + 1}/{len(symbols)})"
                )
                try:
                    results[symbol] = self._fetch_chain(symbol, max_dte)
                except Exception as e:
                    logger.warning(f"Batch chains: {symbol} failed — {e}")
                    results[symbol] = {
                        "symbol": symbol,
                        "stock_price": None,
                        "expirations": [],
                    }
        finally:
            self.disconnect()

        loaded = sum(
            1 for v in results.values()
            if v.get("stock_price") and v.get("expirations")
        )
        logger.info(
            f"Batch chains: {loaded}/{len(symbols)} loaded successfully"
        )
        return results

    def _fetch_chain(self, symbol: str, max_dte: int) -> dict:
        """Internal chain fetch (must be connected)."""
        if not self._ib or not self._ib.isConnected():
            raise ConnectionError("Not connected to IBKR")

        # Step 1: Qualify stock and get price
        stock = Stock(symbol, "SMART", "USD")
        qualified_list = self._ib.qualifyContracts(stock)
        if not qualified_list or not qualified_list[0].conId:
            logger.warning(f"Chain: Could not qualify {symbol}")
            return {"symbol": symbol, "stock_price": None, "expirations": []}

        qualified = qualified_list[0]

        # Get stock price via snapshot
        ticker = self._ib.reqMktData(qualified, "", True, False)
        self._ib.sleep(2)
        stock_price = safe_price(ticker)
        self._ib.cancelMktData(qualified)

        if not stock_price:
            logger.warning(f"Chain: No stock price for {symbol}")
            return {"symbol": symbol, "stock_price": None, "expirations": []}

        logger.info(f"Chain: {symbol} stock price = ${stock_price:.2f}")

        # Step 2: Get option chain definitions
        chains = self._ib.reqSecDefOptParams(
            qualified.symbol, "", "STK", qualified.conId
        )
        if not chains:
            logger.warning(f"Chain: No option chains for {symbol}")
            return {"symbol": symbol, "stock_price": stock_price, "expirations": []}

        # Collect all expirations and strikes across chains
        today = datetime.now(ET).date()
        all_expirations: dict[str, set[float]] = {}  # exp_yyyymmdd → strikes

        for chain in chains:
            for exp_str in chain.expirations:
                exp_date = date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
                dte = (exp_date - today).days
                if 1 <= dte <= max_dte:
                    if exp_str not in all_expirations:
                        all_expirations[exp_str] = set()
                    all_expirations[exp_str].update(chain.strikes)

        if not all_expirations:
            logger.info(f"Chain: No expirations within {max_dte} DTE for {symbol}")
            return {"symbol": symbol, "stock_price": stock_price, "expirations": []}

        # Step 3: For each expiration, filter to OTM put strikes
        lower_bound = stock_price * 0.50  # Far OTM limit (50%)
        upper_bound = stock_price * 0.99  # Near ATM limit

        expirations_data = []
        for exp_str in sorted(all_expirations.keys()):
            exp_date = date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
            dte = (exp_date - today).days
            strikes = sorted(all_expirations[exp_str])

            # Filter to OTM puts in range, keep up to 25 strikes
            candidates = [s for s in strikes if lower_bound <= s <= upper_bound]
            candidates = candidates[-25:]  # Keep closest to ATM if more than 25

            if not candidates:
                continue

            # Step 4: Fetch Greeks for these candidates
            puts = self._fetch_greeks_for_strikes(
                symbol, exp_str, dte, candidates, stock_price, qualified
            )

            exp_formatted = f"{exp_str[:4]}-{exp_str[4:6]}-{exp_str[6:8]}"
            expirations_data.append({
                "date": exp_formatted,
                "dte": dte,
                "puts": puts,
            })

        return {
            "symbol": symbol,
            "stock_price": round(stock_price, 2),
            "expirations": expirations_data,
        }

    def _fetch_greeks_for_strikes(
        self,
        symbol: str,
        expiration: str,
        dte: int,
        strikes: list[float],
        stock_price: float,
        qualified_stock,
    ) -> list[dict]:
        """Fetch Greeks for a list of option strikes.

        Builds Option contracts, requests market data, waits for Greeks,
        and returns structured rows.

        Args:
            symbol: Stock ticker
            expiration: Expiration in YYYYMMDD format
            dte: Days to expiration
            strikes: List of strike prices to fetch
            stock_price: Current stock price
            qualified_stock: Qualified stock contract (for trading class lookup)

        Returns:
            List of dicts (serializable OptionChainRow data)
        """
        # Build option contracts
        contracts: list[tuple[float, object]] = []
        for strike in strikes:
            opt = Option(symbol, expiration, strike, "P", "SMART")
            contracts.append((strike, opt))

        # Qualify all at once
        raw_contracts = [c for _, c in contracts]
        try:
            qualified_list = self._ib.qualifyContracts(*raw_contracts)
        except Exception as e:
            logger.debug(f"Chain {symbol}: Failed to qualify options: {e}")
            return []

        # Map back to strikes
        qualified_map: dict[float, object] = {}
        for (strike, _), qual in zip(contracts, qualified_list):
            if qual and qual.conId:
                qualified_map[strike] = qual

        if not qualified_map:
            return []

        # Request market data with Greeks for all candidates
        tickers: dict[float, tuple] = {}
        for strike, contract in qualified_map.items():
            try:
                tk = self._ib.reqMktData(contract, "", False, False)
                tickers[strike] = (tk, contract)
            except Exception as e:
                logger.debug(f"Chain {symbol} ${strike}: reqMktData failed: {e}")

        # Wait for Greeks (up to 4 seconds)
        for _ in range(8):
            self._ib.sleep(0.5)
            all_have = all(
                hasattr(t, "modelGreeks") and t.modelGreeks and t.modelGreeks.delta is not None
                for t, _ in tickers.values()
            )
            if all_have:
                break

        # Read data and cancel subscriptions
        rows: list[dict] = []
        for strike, (ticker, contract) in tickers.items():
            try:
                delta_val = None
                gamma_val = None
                theta_val = None
                iv_val = None

                if hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
                    greeks = ticker.modelGreeks
                    if greeks.delta is not None:
                        delta_val = round(abs(greeks.delta), 4)
                    if greeks.impliedVol is not None:
                        iv_val = round(greeks.impliedVol, 4)
                    if greeks.gamma is not None:
                        gamma_val = round(greeks.gamma, 6)
                    if greeks.theta is not None:
                        theta_val = round(greeks.theta, 4)

                bid = safe_field(ticker, "bid")
                ask = safe_field(ticker, "ask")
                vol = safe_field(ticker, "volume")
                oi = safe_field(ticker, "openInterest")

                bid = round(bid, 2) if bid and bid > 0 else 0.0
                ask = round(ask, 2) if ask and ask > 0 else 0.0
                mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else bid or ask

                otm_pct = round((stock_price - strike) / stock_price, 4) if stock_price > 0 else 0.0

                # Criteria: delta 0.15-0.30, bid >= $0.30, OTM >= 5%
                meets = (
                    delta_val is not None
                    and 0.15 <= delta_val <= 0.30
                    and bid >= 0.30
                    and otm_pct >= 0.05
                )

                exp_formatted = f"{expiration[:4]}-{expiration[4:6]}-{expiration[6:8]}"

                rows.append({
                    "symbol": symbol,
                    "expiration": exp_formatted,
                    "dte": dte,
                    "strike": strike,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "delta": delta_val,
                    "gamma": gamma_val,
                    "theta": theta_val,
                    "iv": iv_val,
                    "volume": int(vol) if vol is not None else None,
                    "open_interest": int(oi) if oi is not None else None,
                    "otm_pct": otm_pct,
                    "meets_criteria": meets,
                })

            except Exception as e:
                logger.debug(f"Chain {symbol} ${strike}: Error reading data: {e}")
            finally:
                try:
                    self._ib.cancelMktData(contract)
                except Exception:
                    pass

        # Sort by strike descending (closest to ATM first)
        rows.sort(key=lambda r: r["strike"], reverse=True)

        got_greeks = sum(1 for r in rows if r["delta"] is not None)
        logger.info(
            f"Chain {symbol} {expiration} (DTE {dte}): "
            f"{len(rows)} strikes, {got_greeks} with Greeks"
        )

        return rows

    def _execute_scan(self, config: ScannerConfig) -> list[ScannerResult]:
        """Execute a scanner subscription (must be connected)."""
        if not self._ib or not self._ib.isConnected():
            raise ConnectionError("Not connected to IBKR")

        sub = ScannerSubscription(
            instrument=config.instrument,
            locationCode=config.location,
            scanCode=config.scan_code,
            abovePrice=config.min_price,
            belowPrice=config.max_price,
            numberOfRows=config.num_rows,
        )

        # Build TagValue filter options (these work correctly unlike native fields)
        filter_options: list[TagValue] = []
        if config.market_cap_above > 0:
            filter_options.append(TagValue("marketCapAbove1e6", str(config.market_cap_above)))
        if config.market_cap_below > 0:
            filter_options.append(TagValue("marketCapBelow1e6", str(config.market_cap_below)))
        if config.avg_volume_above > 0:
            filter_options.append(TagValue("avgVolumeAbove", str(config.avg_volume_above)))
        if config.avg_opt_volume_above > 0:
            filter_options.append(TagValue("avgOptVolumeAbove", str(config.avg_opt_volume_above)))
        if config.stock_type:
            filter_options.append(TagValue("stkTypes", config.stock_type))

        logger.info(
            f"Running scanner: {config.scan_code} | "
            f"${config.min_price:.0f}-${config.max_price:.0f} | "
            f"{config.num_rows} rows | {len(filter_options)} filters"
        )

        raw_results = self._ib.reqScannerData(
            sub,
            scannerSubscriptionFilterOptions=filter_options,
        )

        logger.info(f"Scanner returned {len(raw_results)} results")

        # Parse into structured results
        results = []
        for item in raw_results:
            cd = item.contractDetails
            c = cd.contract
            results.append(
                ScannerResult(
                    rank=item.rank,
                    symbol=c.symbol,
                    con_id=c.conId,
                    sec_type=c.secType,
                    exchange=c.primaryExchange or c.exchange,
                    long_name=cd.longName or "",
                    industry=cd.industry or "",
                    category=cd.category or "",
                    distance=item.distance or "",
                    benchmark=item.benchmark or "",
                    projection=item.projection or "",
                    legs_str=item.legsStr or "",
                )
            )

        return results

    # ------------------------------------------------------------------
    # Margin queries
    # ------------------------------------------------------------------

    def get_option_margins_batch(
        self,
        candidates: list[dict],
    ) -> dict[str, float | None]:
        """Query IBKR whatIfOrder margin for multiple option contracts.

        Uses the connect-per-call pattern: connects once, queries all
        candidates, disconnects. Each candidate dict needs:
          symbol, strike, expiration_yyyymmdd, stock_price, bid

        Returns dict mapping "SYMBOL|STRIKE|EXP_FORMATTED" to
        margin_per_contract (float). None for failed queries.
        Uses Reg-T estimate fallback for individual failures.

        Args:
            candidates: List of dicts with symbol, strike,
                        expiration_yyyymmdd (e.g. "20260228"),
                        stock_price, and bid.

        Returns:
            Dict[str, float | None] — key is "SYMBOL|STRIKE|EXP" where
            EXP is formatted as "2026-02-28".
        """
        if not candidates:
            return {}

        results: dict[str, float | None] = {}
        self.connect()
        try:
            for cand in candidates:
                symbol = cand["symbol"]
                strike = cand["strike"]
                exp_raw = cand["expiration_yyyymmdd"]
                stock_price = cand.get("stock_price", 0)
                bid = cand.get("bid", 0)

                # Format key as "SYMBOL|STRIKE|2026-02-28"
                exp_formatted = f"{exp_raw[:4]}-{exp_raw[4:6]}-{exp_raw[6:8]}"
                key = f"{symbol}|{strike}|{exp_formatted}"

                try:
                    opt = Option(symbol, exp_raw, strike, "P", "SMART")
                    qualified = self._ib.qualifyContracts(opt)
                    if not qualified or not qualified[0].conId:
                        logger.debug(f"Margin: could not qualify {key}")
                        results[key] = self._regt_fallback(
                            stock_price, strike, bid
                        )
                        continue

                    from ib_insync import MarketOrder

                    order = MarketOrder("SELL", 1)
                    wif = self._ib.whatIfOrder(qualified[0], order)

                    margin_val = None
                    if wif and wif.initMarginChange:
                        try:
                            margin_val = abs(float(wif.initMarginChange))
                        except (ValueError, TypeError):
                            pass

                    # Sanity floor: 5% of strike * 100
                    floor = 0.05 * strike * 100
                    if margin_val is not None and margin_val < floor:
                        logger.debug(
                            f"Margin {key}: ${margin_val:.0f} below floor "
                            f"${floor:.0f}, using Reg-T fallback"
                        )
                        margin_val = self._regt_fallback(
                            stock_price, strike, bid
                        )

                    if margin_val is None:
                        margin_val = self._regt_fallback(
                            stock_price, strike, bid
                        )

                    results[key] = margin_val

                except Exception as e:
                    logger.debug(f"Margin {key}: whatIfOrder failed — {e}")
                    results[key] = self._regt_fallback(
                        stock_price, strike, bid
                    )

                # Pace IBKR requests
                self._ib.sleep(0.1)

        finally:
            self.disconnect()

        queried = len(results)
        ibkr_count = sum(1 for v in results.values() if v is not None)
        logger.info(
            f"Margin batch: {queried} queried, {ibkr_count} resolved"
        )

        return results

    @staticmethod
    def _regt_fallback(
        stock_price: float, strike: float, premium: float
    ) -> float:
        """Estimate margin using Reg-T formula.

        Reg-T naked put:
          max(20% of stock - OTM_amount + premium, 10% of stock) * 100
        """
        otm_amount = max(0, stock_price - strike)
        margin = (0.20 * stock_price - otm_amount + premium) * 100
        min_margin = 0.10 * stock_price * 100
        return round(max(margin, min_margin), 2)
