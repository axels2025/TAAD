"""Strike finder for optimal naked put strike selection.

This module automates Axel's process of scrolling through option chains
to find the sweet spot between premium and OTM distance.

The StrikeFinder takes a list of symbols and finds the best strike for
each based on configurable preferences for:
- Premium range ($0.30-$0.60 default)
- OTM distance (15-20%+ default)
- DTE (same-week Friday preferred)
- Margin efficiency
- Liquidity
"""

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from loguru import logger

from src.data.candidates import BarchartCandidate
from src.scoring.scorer import ScoredCandidate
from src.services.limit_price_calculator import LimitPriceCalculator
from src.services.position_sizer import PositionSizer


@dataclass
class StrikePreferences:
    """User-configurable strike selection preferences.

    All values loaded from environment variables with sensible defaults.
    These preferences guide the strike finder in selecting optimal strikes.

    Attributes:
        min_premium: Minimum acceptable premium (default $0.30)
        max_premium: Maximum preferred premium (default $0.60)
        target_premium: Ideal premium target (default $0.40)
        min_otm_pct: Minimum OTM percentage (default 15%)
        target_otm_pct: Ideal OTM percentage (default 20%)
        rare_min_otm_pct: Absolute minimum for rare cases (default 10%)
        max_dte: Maximum days to expiration (default 14)
        target_dte: Ideal DTE - same-week Friday (default 7)
        contract_price_threshold: Price above which max contracts reduced (default $90)
        contract_max_expensive: Max contracts for expensive stocks (default 3)
        contract_max_cheap: Max contracts for cheaper stocks (default 5)
        allow_higher_premium_if_further_otm: Allow $0.60+ if OTM > 25% (default True)
    """

    min_premium: float = 0.30
    max_premium: float = 0.60
    target_premium: float = 0.40
    min_otm_pct: float = 0.15
    target_otm_pct: float = 0.20
    rare_min_otm_pct: float = 0.10
    max_dte: int = 14
    target_dte: int = 7
    contract_price_threshold: float = 90.0
    contract_max_expensive: int = 3
    contract_max_cheap: int = 5
    allow_higher_premium_if_further_otm: bool = True

    @classmethod
    def from_env(cls) -> "StrikePreferences":
        """Load preferences from the central Config singleton.

        Premium values come from ``get_config()`` so there is one source
        of truth.  OTM/DTE/contract-sizing values stay as ``os.getenv``
        since they are only read here.

        Returns:
            StrikePreferences instance
        """
        from src.config.base import get_config

        cfg = get_config()
        return cls(
            min_premium=cfg.premium_min,
            max_premium=cfg.premium_max,
            target_premium=cfg.premium_target,
            min_otm_pct=float(os.getenv("OTM_MIN_PCT", "0.15")),
            target_otm_pct=float(os.getenv("OTM_TARGET_PCT", "0.20")),
            rare_min_otm_pct=float(os.getenv("OTM_RARE_MIN_PCT", "0.10")),
            max_dte=int(os.getenv("DTE_MAX", "14")),
            target_dte=int(os.getenv("DTE_TARGET", "7")),
            contract_price_threshold=float(os.getenv("CONTRACT_PRICE_THRESHOLD", "90.0")),
            contract_max_expensive=int(os.getenv("CONTRACT_MAX_EXPENSIVE", "3")),
            contract_max_cheap=int(os.getenv("CONTRACT_MAX_CHEAP", "5")),
            allow_higher_premium_if_further_otm=os.getenv(
                "ALLOW_HIGHER_PREMIUM_IF_FURTHER_OTM", "true"
            ).lower() == "true",
        )


@dataclass
class StrikeCandidate:
    """A potential strike price with full details.

    Represents a candidate strike price for a naked put trade,
    including all relevant metrics for ranking and selection.

    Attributes:
        symbol: Stock ticker symbol
        stock_price: Current stock price
        strike: Strike price of the option
        expiration: Option expiration date
        dte: Days to expiration
        bid: Current bid price
        ask: Current ask price
        mid: Mid price ((bid + ask) / 2)
        suggested_limit: Calculated limit price (between bid and mid)
        otm_pct: Out-of-the-money percentage (e.g., 0.20 = 20%)
        delta: Option delta (negative for puts)
        iv: Implied volatility
        iv_rank: IV rank (percentile)
        volume: Daily option volume
        open_interest: Open interest
        margin_estimate: Estimated margin from formula
        margin_actual: Actual margin from IBKR (None if not checked)
        contracts: Recommended number of contracts
        total_margin: margin * contracts
        premium_income: suggested_limit * 100 * contracts
        margin_efficiency: premium_income / total_margin
        sector: Stock sector
        score: Composite ranking score (0-100)
        source: Data source ('ibkr' or 'barchart')
    """

    symbol: str
    stock_price: float
    strike: float
    expiration: date
    dte: int
    bid: float
    ask: float
    mid: float
    suggested_limit: float
    otm_pct: float
    delta: float
    iv: float
    iv_rank: float
    volume: int
    open_interest: int
    margin_estimate: float
    margin_actual: float | None
    contracts: int
    total_margin: float
    premium_income: float
    margin_efficiency: float
    sector: str = "Unknown"
    score: float = 0.0
    source: str = "barchart"

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "stock_price": self.stock_price,
            "strike": self.strike,
            "expiration": self.expiration.isoformat(),
            "dte": self.dte,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "suggested_limit": self.suggested_limit,
            "otm_pct": self.otm_pct,
            "delta": self.delta,
            "iv": self.iv,
            "iv_rank": self.iv_rank,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "margin_estimate": self.margin_estimate,
            "margin_actual": self.margin_actual,
            "contracts": self.contracts,
            "total_margin": self.total_margin,
            "premium_income": self.premium_income,
            "margin_efficiency": self.margin_efficiency,
            "sector": self.sector,
            "score": self.score,
            "source": self.source,
        }

    @property
    def _margin_sanity_floor(self) -> float:
        """Minimum believable margin per contract (5% of notional).

        IBKR's whatIfOrder sometimes returns near-zero values after hours.
        Any margin below this floor is treated as unreliable.
        """
        return 0.05 * self.strike * 100

    @property
    def effective_margin(self) -> float:
        """Get the effective margin (actual if available, else estimate).

        Never returns 0 — falls back to Reg-T minimum (10% of notional).
        Rejects IBKR margin values below the sanity floor.
        """
        floor = self._margin_sanity_floor
        if self.margin_actual and self.margin_actual >= floor:
            return self.margin_actual
        if self.margin_estimate and self.margin_estimate > 0:
            return self.margin_estimate
        return 0.10 * self.stock_price * 100

    @property
    def margin_source(self) -> str:
        """Get the margin source ('ibkr_whatif' or 'estimated')."""
        floor = self._margin_sanity_floor
        if self.margin_actual and self.margin_actual >= floor:
            return "ibkr_whatif"
        return "estimated"


class IBKRClientProtocol(Protocol):
    """Protocol for IBKR client dependency injection."""

    def get_stock_price(self, symbol: str) -> float | None:
        """Get current stock price."""
        ...

    def get_option_chain(
        self, symbol: str, expiration: date
    ) -> list[dict] | None:
        """Get option chain for symbol and expiration."""
        ...

    def get_actual_margin(
        self, symbol: str, strike: float, expiration: date
    ) -> float | None:
        """Get actual margin requirement via whatIfOrder."""
        ...


class StrikeFinder:
    """Find optimal strike prices for naked put candidates.

    This class automates the process of selecting the best strike price
    for each symbol based on configurable preferences for premium,
    OTM distance, DTE, and margin efficiency.

    The finder can work in two modes:
    1. Online mode: Uses live IBKR data for current prices and margins
    2. Offline mode: Uses Barchart CSV data with estimated margins

    Example:
        >>> finder = StrikeFinder()
        >>> candidates = finder.find_best_strikes(
        ...     symbols=['AAPL', 'MSFT'],
        ...     barchart_data={'AAPL': [...], 'MSFT': [...]},
        ... )
        >>> for c in candidates:
        ...     print(f"{c.symbol} ${c.strike} @ ${c.suggested_limit}")
    """

    # Scoring weights (must sum to 1.0)
    WEIGHT_OTM = 0.35
    WEIGHT_PREMIUM = 0.25
    WEIGHT_MARGIN_EFFICIENCY = 0.20
    WEIGHT_IV_RANK = 0.10
    WEIGHT_LIQUIDITY = 0.10

    def __init__(
        self,
        ibkr_client: IBKRClientProtocol | None = None,
        preferences: StrikePreferences | None = None,
        limit_calculator: LimitPriceCalculator | None = None,
        account_equity: float | None = None,
    ):
        """Initialize the strike finder.

        Args:
            ibkr_client: Optional IBKR client for live data.
                        If None, operates in offline mode using Barchart data.
            preferences: Strike selection preferences. Loads from .env if None.
            limit_calculator: Limit price calculator. Creates new if None.
            account_equity: Account NetLiquidation for risk-based position sizing.
                           If None, uses price-based sizing only.
        """
        self.ibkr_client = ibkr_client
        self.preferences = preferences or StrikePreferences.from_env()
        self.limit_calculator = limit_calculator or LimitPriceCalculator()
        self.position_sizer = PositionSizer(account_equity) if account_equity else None

        logger.info(
            f"StrikeFinder initialized: "
            f"premium=${self.preferences.min_premium}-${self.preferences.max_premium}, "
            f"otm={self.preferences.min_otm_pct*100:.0f}%-{self.preferences.target_otm_pct*100:.0f}%, "
            f"dte={self.preferences.target_dte}-{self.preferences.max_dte}, "
            f"ibkr={'connected' if ibkr_client else 'offline'}"
        )

    def find_best_strikes(
        self,
        symbols: list[str],
        barchart_data: dict[str, list[ScoredCandidate]],
        sector_data: dict[str, str] | None = None,
    ) -> list[StrikeCandidate]:
        """Find the best strike for each symbol.

        For each symbol:
        1. Get all candidate strikes from Barchart data
        2. Filter by OTM and premium criteria
        3. Calculate suggested limit prices
        4. Score and rank candidates
        5. Return the top candidate per symbol

        Args:
            symbols: List of symbols to find strikes for
            barchart_data: Dictionary mapping symbol to list of ScoredCandidate
            sector_data: Optional dictionary mapping symbol to sector/industry name

        Returns:
            List of StrikeCandidate, one per symbol, sorted by score descending
        """
        # Store sector data for use in conversion
        self._sector_data = sector_data or {}

        results: list[StrikeCandidate] = []

        for symbol in symbols:
            if symbol not in barchart_data:
                logger.warning(f"No Barchart data for {symbol}, skipping")
                continue

            scored_candidates = barchart_data[symbol]
            if not scored_candidates:
                logger.warning(f"Empty candidate list for {symbol}, skipping")
                continue

            best = self._find_best_for_symbol(symbol, scored_candidates)
            if best:
                results.append(best)
            else:
                logger.warning(f"No valid strikes found for {symbol}")

        # Sort by score descending
        results.sort(key=lambda x: x.score, reverse=True)

        logger.info(
            f"Strike finder complete: {len(results)} symbols with valid strikes "
            f"(requested {len(symbols)})"
        )

        return results

    def _find_best_for_symbol(
        self,
        symbol: str,
        scored_candidates: list[ScoredCandidate],
    ) -> StrikeCandidate | None:
        """Find the best strike for a single symbol.

        Uses two-tier filtering:
        1. First pass: Try to find strikes with preferred OTM (min_otm_pct)
        2. Second pass: If none found, accept strikes with rare minimum OTM

        Args:
            symbol: Stock symbol
            scored_candidates: List of ScoredCandidate from Barchart

        Returns:
            Best StrikeCandidate or None if no valid strikes
        """
        # First pass: Use preferred minimum OTM
        candidates: list[StrikeCandidate] = []

        for sc in scored_candidates:
            candidate = self._convert_to_strike_candidate(sc)
            if candidate and self._passes_filters(candidate, use_rare_min=False):
                candidate.score = self._calculate_score(candidate)
                candidates.append(candidate)

        # Second pass: If no candidates found, try with rare minimum OTM
        if not candidates and self.preferences.rare_min_otm_pct < self.preferences.min_otm_pct:
            logger.debug(
                f"{symbol}: No strikes found with preferred OTM {self.preferences.min_otm_pct*100:.0f}%+, "
                f"trying rare minimum {self.preferences.rare_min_otm_pct*100:.0f}%+"
            )
            for sc in scored_candidates:
                candidate = self._convert_to_strike_candidate(sc)
                if candidate and self._passes_filters(candidate, use_rare_min=True):
                    candidate.score = self._calculate_score(candidate)
                    candidates.append(candidate)

            if candidates:
                logger.info(
                    f"{symbol}: Accepted strike with rare minimum OTM filter "
                    f"({self.preferences.rare_min_otm_pct*100:.0f}%+)"
                )

        if not candidates:
            return None

        # Sort by score descending and return best
        candidates.sort(key=lambda x: x.score, reverse=True)

        best = candidates[0]
        logger.debug(
            f"{symbol}: Selected ${best.strike} @ ${best.suggested_limit:.2f} "
            f"(OTM {best.otm_pct*100:.1f}%, score {best.score:.1f})"
        )

        return best

    def _convert_to_strike_candidate(
        self,
        sc: ScoredCandidate,
    ) -> StrikeCandidate | None:
        """Convert a ScoredCandidate to a StrikeCandidate.

        Args:
            sc: ScoredCandidate from scoring engine

        Returns:
            StrikeCandidate or None if conversion fails
        """
        try:
            candidate = sc.candidate

            # Calculate mid and suggested limit
            # Note: Barchart only provides bid, so we estimate ask
            bid = candidate.bid
            # Estimate ask as bid + typical spread (we'll use 10% spread estimate)
            ask = bid * 1.10 if bid > 0 else 0.01

            mid = (bid + ask) / 2
            suggested_limit = self.limit_calculator.calculate_sell_limit(bid, ask)

            # Calculate OTM percentage
            otm_pct = abs(candidate.moneyness_pct)

            # Determine contracts based on stock price and risk limits
            contracts = self._determine_contracts(candidate.underlying_price, candidate.strike)

            # Estimate margin using Reg-T formula
            margin_estimate = self._estimate_margin(
                candidate.underlying_price,
                candidate.strike,
                bid,
            )

            total_margin = margin_estimate * contracts
            premium_income = suggested_limit * 100 * contracts
            margin_efficiency = (
                premium_income / total_margin if total_margin > 0 else 0
            )

            # Get sector from sector_data if available
            sector = getattr(self, '_sector_data', {}).get(candidate.symbol, "Unknown")

            return StrikeCandidate(
                symbol=candidate.symbol,
                stock_price=candidate.underlying_price,
                strike=candidate.strike,
                expiration=candidate.expiration,
                dte=candidate.dte,
                bid=bid,
                ask=ask,
                mid=mid,
                suggested_limit=suggested_limit,
                otm_pct=otm_pct,
                delta=candidate.delta,
                iv=0.0,  # Not available in BarchartCandidate
                iv_rank=candidate.iv_rank,
                volume=candidate.volume,
                open_interest=candidate.open_interest,
                margin_estimate=margin_estimate,
                margin_actual=None,  # Will be filled by portfolio builder
                contracts=contracts,
                total_margin=total_margin,
                premium_income=premium_income,
                margin_efficiency=margin_efficiency,
                sector=sector,
                source="barchart",
            )
        except Exception as e:
            logger.error(f"Error converting candidate: {e}")
            return None

    def _passes_filters(self, candidate: StrikeCandidate, use_rare_min: bool = False) -> bool:
        """Check if a candidate passes all filters.

        Args:
            candidate: StrikeCandidate to check
            use_rare_min: If True, use rare_min_otm_pct instead of min_otm_pct

        Returns:
            True if passes all filters, False otherwise
        """
        # Check minimum OTM (use rare minimum if fallback enabled)
        min_otm = self.preferences.rare_min_otm_pct if use_rare_min else self.preferences.min_otm_pct
        if candidate.otm_pct < min_otm:
            logger.debug(
                f"{candidate.symbol} ${candidate.strike}: "
                f"OTM {candidate.otm_pct*100:.1f}% < min {min_otm*100:.0f}%"
            )
            return False

        # Check minimum premium
        if candidate.bid < self.preferences.min_premium:
            logger.debug(
                f"{candidate.symbol} ${candidate.strike}: "
                f"bid ${candidate.bid:.2f} < min ${self.preferences.min_premium:.2f}"
            )
            return False

        # Check maximum premium (unless further OTM allows it)
        if candidate.bid > self.preferences.max_premium:
            if not self.preferences.allow_higher_premium_if_further_otm:
                return False
            # Allow higher premium only if significantly further OTM (>25%)
            if candidate.otm_pct < 0.25:
                logger.debug(
                    f"{candidate.symbol} ${candidate.strike}: "
                    f"premium ${candidate.bid:.2f} > max ${self.preferences.max_premium:.2f} "
                    f"and OTM {candidate.otm_pct*100:.1f}% < 25%"
                )
                return False

        # Check DTE
        if candidate.dte > self.preferences.max_dte:
            logger.debug(
                f"{candidate.symbol} ${candidate.strike}: "
                f"DTE {candidate.dte} > max {self.preferences.max_dte}"
            )
            return False

        return True

    def _calculate_score(self, candidate: StrikeCandidate) -> float:
        """Calculate composite score for a candidate.

        Scoring dimensions:
        - OTM% (35%): Further OTM = better, up to target
        - Premium (25%): Higher premium = better, within range
        - Margin efficiency (20%): Higher = better
        - IV Rank (10%): Lower = better (less event risk)
        - Liquidity (10%): Higher volume + OI = better

        Args:
            candidate: StrikeCandidate to score

        Returns:
            Composite score 0-100
        """
        # OTM score: 100 at target, decreasing below and above
        # Ideal is target_otm_pct (20%), penalty for being too close or too far
        otm_score = self._score_otm(candidate.otm_pct)

        # Premium score: 100 at target, decreasing away from it
        premium_score = self._score_premium(candidate.bid)

        # Margin efficiency score: Linear scaling
        efficiency_score = self._score_efficiency(candidate.margin_efficiency)

        # IV Rank score: Lower is better (less event risk)
        iv_score = self._score_iv_rank(candidate.iv_rank)

        # Liquidity score: Based on volume and OI
        liquidity_score = self._score_liquidity(
            candidate.volume, candidate.open_interest
        )

        # Weighted composite
        score = (
            otm_score * self.WEIGHT_OTM
            + premium_score * self.WEIGHT_PREMIUM
            + efficiency_score * self.WEIGHT_MARGIN_EFFICIENCY
            + iv_score * self.WEIGHT_IV_RANK
            + liquidity_score * self.WEIGHT_LIQUIDITY
        )

        logger.debug(
            f"{candidate.symbol} ${candidate.strike} scoring: "
            f"OTM={otm_score:.0f}, Premium={premium_score:.0f}, "
            f"Eff={efficiency_score:.0f}, IV={iv_score:.0f}, "
            f"Liq={liquidity_score:.0f} → {score:.1f}"
        )

        return score

    def _score_otm(self, otm_pct: float) -> float:
        """Score OTM percentage. Higher OTM = better (more safety).

        Safety-first scoring philosophy:
        - 100 at target (20%) and above (maximum safety)
        - Decreases below target (closer to money = more risk)
        - No penalty for being far OTM (if premium passes filters, high OTM is ideal)

        Scoring:
        - 20%+ OTM: 100 (maximum score - maximum safety)
        - 15-20% OTM: 80-100 (linear scale - good range)
        - <15% OTM: filtered out before scoring
        """
        target = self.preferences.target_otm_pct
        min_otm = self.preferences.min_otm_pct

        if otm_pct >= target:
            # At or above target: maximum score (highest safety)
            # No penalty for being far OTM - if it passes premium filters,
            # high OTM with good premium is ideal
            return 100.0
        else:
            # Below target: penalty for being too close to money
            # Linear scale from min_otm (80) to target (100)
            range_size = target - min_otm
            if range_size <= 0:
                return 90.0
            position = (otm_pct - min_otm) / range_size
            # Scale from 80 to 100
            return 80.0 + (position * 20.0)

    def _score_premium(self, premium: float) -> float:
        """Score premium. Target is best, decreases away from it."""
        target = self.preferences.target_premium
        min_prem = self.preferences.min_premium
        max_prem = self.preferences.max_premium

        if premium < min_prem:
            return 0
        elif premium <= target:
            # Below target: linear increase to 100
            range_size = target - min_prem
            if range_size <= 0:
                return 100
            return ((premium - min_prem) / range_size) * 100
        elif premium <= max_prem:
            # Above target but below max: decrease from 100
            range_size = max_prem - target
            if range_size <= 0:
                return 100
            return 100 - ((premium - target) / range_size) * 30  # Max 30% penalty
        else:
            # Above max: significant penalty but not zero
            return max(40, 70 - ((premium - max_prem) / max_prem) * 100)

    def _score_efficiency(self, efficiency: float) -> float:
        """Score margin efficiency. Higher = better.

        Typical range: 2% - 15%
        """
        # Linear scaling: 5% = 50, 10% = 100
        return min(100, efficiency * 1000)

    def _score_iv_rank(self, iv_rank: float) -> float:
        """Score IV rank. Lower = better (less event risk).

        - IV rank < 30%: Score 100 (low IV, good)
        - IV rank 30-60%: Score 70-100
        - IV rank > 60%: Score drops (elevated IV, potential event)
        """
        if iv_rank <= 0.30:
            return 100
        elif iv_rank <= 0.60:
            # Linear decrease from 100 to 70
            return 100 - ((iv_rank - 0.30) / 0.30) * 30
        else:
            # Above 60%: steeper penalty
            return max(20, 70 - ((iv_rank - 0.60) / 0.40) * 50)

    def _score_liquidity(self, volume: int, open_interest: int) -> float:
        """Score liquidity based on volume and open interest.

        Good liquidity: volume > 500, OI > 1000
        """
        # Volume score (50% weight)
        if volume >= 1000:
            vol_score = 100
        elif volume >= 500:
            vol_score = 75
        elif volume >= 200:
            vol_score = 50
        else:
            vol_score = max(0, volume / 200 * 50)

        # OI score (50% weight)
        if open_interest >= 2000:
            oi_score = 100
        elif open_interest >= 1000:
            oi_score = 75
        elif open_interest >= 500:
            oi_score = 50
        else:
            oi_score = max(0, open_interest / 500 * 50)

        return (vol_score + oi_score) / 2

    def _determine_contracts(self, stock_price: float, strike: float = 0.0) -> int:
        """Determine contracts using price-based rule and risk-based cap.

        Price-based rule:
        - Stock price > $90: max 3 contracts
        - Stock price <= $90: max 5 contracts

        If account_equity is available, also applies fixed fractional risk
        sizing and returns the smaller of the two.
        """
        if stock_price > self.preferences.contract_price_threshold:
            price_based_max = self.preferences.contract_max_expensive
        else:
            price_based_max = self.preferences.contract_max_cheap

        if self.position_sizer and strike > 0:
            return self.position_sizer.calculate_contracts(strike, price_based_max)

        return price_based_max

    def _estimate_margin(
        self,
        stock_price: float,
        strike: float,
        premium: float,
    ) -> float:
        """Estimate margin requirement using Reg-T formula.

        Reg-T Formula for naked puts:
        margin = max(
            20% × stock_price - OTM_amount + premium,
            10% × stock_price
        ) × 100

        Args:
            stock_price: Current stock price
            strike: Option strike price
            premium: Option premium (bid)

        Returns:
            Estimated margin per contract
        """
        otm_amount = max(0, stock_price - strike)

        margin_method_1 = (0.20 * stock_price - otm_amount + premium) * 100
        margin_method_2 = 0.10 * stock_price * 100

        return max(margin_method_1, margin_method_2)
