"""Best-strike selection algorithm with composite scoring.

Given loaded option chain data from the IBKR scanner, filters candidates
using ScannerSettings criteria, scores them on safety/liquidity/efficiency,
and picks the single best strike per symbol.

The scoring intentionally does NOT reuse src/scoring/score_rules.py because
the scanner pipeline works with raw chain data (delta, bid/ask spread, OI)
while the scoring pipeline works with Barchart-enriched data. Keeping them
separate avoids coupling the two pipelines.

During best-strike selection, the AI weight is unavailable (Claude hasn't
been called yet). The three available weights are normalized to sum to 1.0:
  safety_norm  = safety / (safety + liquidity + efficiency)
  liquidity_norm = liquidity / (safety + liquidity + efficiency)
  efficiency_norm = efficiency / (safety + liquidity + efficiency)

With defaults (40/30/10): safety=0.500, liquidity=0.375, efficiency=0.125.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from src.agentic.scanner_settings import ScannerSettings


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ScannerStrikeCandidate:
    """A single put option candidate extracted from chain data."""

    symbol: str
    stock_price: float
    strike: float
    expiration: str  # "2026-02-28"
    dte: int
    bid: float
    ask: float
    mid: float
    delta: float | None
    iv: float | None
    theta: float | None
    volume: int | None
    open_interest: int | None
    otm_pct: float
    margin: float | None = None  # From IBKR whatIfOrder
    margin_source: str = "none"  # "ibkr_whatif", "estimated", or "none"


@dataclass
class BestStrikeResult:
    """Result of best-strike selection for a single symbol."""

    symbol: str
    stock_price: float
    strike: float
    expiration: str
    dte: int
    bid: float
    ask: float
    delta: float | None
    iv: float | None
    otm_pct: float
    volume: int | None
    open_interest: int | None
    margin: float
    margin_source: str
    safety_score: float  # 0.0-1.0
    liquidity_score: float  # 0.0-1.0
    efficiency_score: float  # 0.0-1.0
    composite_score: float  # Weighted sum
    premium_margin_ratio: float
    annualized_return_pct: float
    contracts: int
    sector: str
    status: str = "selected"  # "selected" or "skipped"
    skip_reason: str | None = None


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def compute_safety_score(
    delta: float | None, otm_pct: float, delta_target: float
) -> float:
    """Score safety based on delta proximity to target and OTM distance.

    60% weight: delta proximity to target (1.0 at target, linear decay).
           Hard penalty if delta > 0.20: score capped at 0.3.
    40% weight: OTM distance (1.0 at >= 20%, 0.8 at 15%, 0.5 at 10%).

    Args:
        delta: Absolute delta (e.g. 0.10). None returns 0.0.
        otm_pct: Out-of-the-money percentage (e.g. 0.15 = 15%).
        delta_target: Target delta from settings (e.g. 0.065).

    Returns:
        Score between 0.0 and 1.0.
    """
    if delta is None:
        return 0.0

    # Delta proximity (60%)
    distance = abs(delta - delta_target)
    # Normalize: 0 distance = 1.0, distance of delta_target = 0.0
    max_distance = max(delta_target, 0.30)  # reasonable range
    delta_score = max(0.0, 1.0 - distance / max_distance)

    # Penalty for high delta (> 0.20 = closer to ATM = riskier)
    if delta > 0.20:
        delta_score = min(delta_score, 0.3)

    # OTM distance (40%)
    if otm_pct >= 0.20:
        otm_score = 1.0
    elif otm_pct >= 0.15:
        otm_score = 0.8
    elif otm_pct >= 0.10:
        otm_score = 0.5
    elif otm_pct >= 0.05:
        otm_score = 0.3
    else:
        otm_score = 0.1

    return round(0.6 * delta_score + 0.4 * otm_score, 4)


def compute_liquidity_score(
    open_interest: int | None,
    volume: int | None,
    bid: float,
    ask: float,
) -> float:
    """Score liquidity based on open interest, spread, and volume.

    Equal 1/3 weight to each component:
    - OI: >=1000 -> 1.0, 500-999 -> 0.7, 100-499 -> 0.4, <100 -> 0.1
    - Spread %: <=5% -> 1.0, 5-10% -> 0.7, 10-20% -> 0.4, >20% -> 0.1
    - Volume: >=500 -> 1.0, 100-499 -> 0.6, >0 -> 0.3, 0/None -> 0.1

    Args:
        open_interest: Option open interest count.
        volume: Daily trading volume.
        bid: Bid price.
        ask: Ask price.

    Returns:
        Score between 0.0 and 1.0.
    """
    # OI sub-score
    oi = open_interest or 0
    if oi >= 1000:
        oi_score = 1.0
    elif oi >= 500:
        oi_score = 0.7
    elif oi >= 100:
        oi_score = 0.4
    else:
        oi_score = 0.1

    # Spread sub-score
    mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0
    if mid > 0:
        spread_pct = (ask - bid) / mid
    else:
        spread_pct = 1.0  # worst case

    if spread_pct <= 0.05:
        spread_score = 1.0
    elif spread_pct <= 0.10:
        spread_score = 0.7
    elif spread_pct <= 0.20:
        spread_score = 0.4
    else:
        spread_score = 0.1

    # Volume sub-score
    vol = volume or 0
    if vol >= 500:
        vol_score = 1.0
    elif vol >= 100:
        vol_score = 0.6
    elif vol > 0:
        vol_score = 0.3
    else:
        vol_score = 0.1

    return round((oi_score + spread_score + vol_score) / 3, 4)


def compute_efficiency_score(premium_bid: float, margin: float | None, dte: int) -> float:
    """Score capital efficiency based on annualized return on margin.

    Annualized return = (bid * 100 / margin) * (365 / dte).
    >=30% -> 1.0, 20-30% -> 0.8, 10-20% -> 0.6, 5-10% -> 0.4, <5% -> 0.2.

    Args:
        premium_bid: Bid price of the option (e.g. 0.50).
        margin: Margin requirement per contract in dollars (e.g. 2500.0).
        dte: Days to expiration.

    Returns:
        Score between 0.0 and 1.0. Returns 0.0 if margin is None or 0.
    """
    if not margin or margin <= 0 or dte <= 0:
        return 0.0

    annualized = (premium_bid * 100 / margin) * (365 / dte)

    if annualized >= 0.30:
        return 1.0
    elif annualized >= 0.20:
        return 0.8
    elif annualized >= 0.10:
        return 0.6
    elif annualized >= 0.05:
        return 0.4
    else:
        return 0.2


# ---------------------------------------------------------------------------
# AutoSelector
# ---------------------------------------------------------------------------


class AutoSelector:
    """Best-strike selection engine using ScannerSettings.

    Filters chain candidates, scores them with a composite metric
    (safety + liquidity + efficiency, AI excluded), and picks the
    single best strike per symbol.
    """

    def __init__(self, settings: ScannerSettings):
        self.settings = settings
        self._compute_normalized_weights()

    def _compute_normalized_weights(self) -> None:
        """Normalize safety/liquidity/efficiency weights excluding AI."""
        r = self.settings.ranking
        available = r.safety + r.liquidity + r.efficiency
        if available <= 0:
            # Fallback to equal weights
            self.w_safety = 1 / 3
            self.w_liquidity = 1 / 3
            self.w_efficiency = 1 / 3
        else:
            self.w_safety = r.safety / available
            self.w_liquidity = r.liquidity / available
            self.w_efficiency = r.efficiency / available

    def filter_candidates(self, chain_data: dict) -> list[ScannerStrikeCandidate]:
        """Extract and filter candidates from chain data.

        Collects all puts across all expirations and filters by:
        - delta in [delta_min, delta_max]
        - bid >= min_premium
        - otm_pct >= min_otm_pct
        - delta is not None
        - bid > 0

        Args:
            chain_data: Dict from IBKRScannerService.get_option_chain()
                        with keys: symbol, stock_price, expirations.

        Returns:
            List of ScannerStrikeCandidate objects that pass all filters.
        """
        symbol = chain_data.get("symbol", "")
        stock_price = chain_data.get("stock_price")
        if not stock_price:
            return []

        f = self.settings.filters
        candidates = []

        for exp in chain_data.get("expirations", []):
            dte = exp.get("dte", 0)
            for put in exp.get("puts", []):
                delta = put.get("delta")
                bid = put.get("bid", 0)
                otm_pct = put.get("otm_pct", 0)

                # Apply filters
                if delta is None:
                    continue
                if bid <= 0:
                    continue
                if not (f.delta_min <= delta <= f.delta_max):
                    continue
                if bid < f.min_premium:
                    continue
                if otm_pct < f.min_otm_pct:
                    continue

                candidates.append(ScannerStrikeCandidate(
                    symbol=symbol,
                    stock_price=stock_price,
                    strike=put.get("strike", 0),
                    expiration=exp.get("date", ""),
                    dte=dte,
                    bid=bid,
                    ask=put.get("ask", 0),
                    mid=put.get("mid", 0),
                    delta=delta,
                    iv=put.get("iv"),
                    theta=put.get("theta"),
                    volume=put.get("volume"),
                    open_interest=put.get("open_interest"),
                    otm_pct=otm_pct,
                ))

        return candidates

    def score_candidate(self, candidate: ScannerStrikeCandidate) -> float:
        """Compute composite score for a single candidate.

        Args:
            candidate: A filtered ScannerStrikeCandidate (with margin attached).

        Returns:
            Composite score between 0.0 and 1.0.
        """
        safety = compute_safety_score(
            candidate.delta, candidate.otm_pct, self.settings.filters.delta_target
        )
        liquidity = compute_liquidity_score(
            candidate.open_interest, candidate.volume, candidate.bid, candidate.ask
        )
        efficiency = compute_efficiency_score(
            candidate.bid, candidate.margin, candidate.dte
        )

        return round(
            self.w_safety * safety
            + self.w_liquidity * liquidity
            + self.w_efficiency * efficiency,
            4,
        )

    def select_best_per_symbol(
        self,
        all_candidates: dict[str, list[ScannerStrikeCandidate]],
        margins: dict[str, float | None],
    ) -> list[BestStrikeResult]:
        """Select the single best strike for each symbol.

        For each symbol:
        1. Attach margin data from whatIfOrder results
        2. Score each candidate
        3. Sort by (expiration ASC, composite DESC) — shortest DTE first
        4. Pick top candidate from shortest DTE group

        Args:
            all_candidates: symbol -> list of filtered candidates.
            margins: "SYMBOL|STRIKE|EXP" -> margin_per_contract (or None).

        Returns:
            List of BestStrikeResult, one per symbol.
        """
        results: list[BestStrikeResult] = []

        for symbol, candidates in all_candidates.items():
            if not candidates:
                results.append(self._skipped_result(symbol, "no_candidates"))
                continue

            # Attach margins
            for c in candidates:
                key = f"{c.symbol}|{c.strike}|{c.expiration}"
                margin_val = margins.get(key)
                if margin_val is not None:
                    c.margin = margin_val
                    c.margin_source = "ibkr_whatif"
                else:
                    # Reg-T fallback
                    c.margin = self._estimate_margin_regt(c)
                    c.margin_source = "estimated"

            # Score and sort
            scored: list[tuple[ScannerStrikeCandidate, float, float, float, float]] = []
            for c in candidates:
                safety = compute_safety_score(
                    c.delta, c.otm_pct, self.settings.filters.delta_target
                )
                liquidity = compute_liquidity_score(
                    c.open_interest, c.volume, c.bid, c.ask
                )
                efficiency = compute_efficiency_score(c.bid, c.margin, c.dte)
                composite = round(
                    self.w_safety * safety
                    + self.w_liquidity * liquidity
                    + self.w_efficiency * efficiency,
                    4,
                )
                scored.append((c, composite, safety, liquidity, efficiency))

            # Sort: shortest DTE first (prefer faster capital turnover),
            # then highest composite score
            scored.sort(key=lambda x: (x[0].expiration, -x[1]))

            if self.settings.filters.dte_prefer_shortest:
                # Pick from shortest DTE group
                shortest_exp = scored[0][0].expiration
                same_exp = [s for s in scored if s[0].expiration == shortest_exp]
                best = same_exp[0]
            else:
                # Pick globally best composite score
                scored.sort(key=lambda x: -x[1])
                best = scored[0]

            c, composite, safety, liquidity, efficiency = best

            # Compute derived metrics
            margin = c.margin or 0
            premium_margin_ratio = (
                (c.bid * 100 / margin) if margin > 0 else 0.0
            )
            annualized = (
                premium_margin_ratio * (365 / c.dte) if c.dte > 0 else 0.0
            )

            results.append(BestStrikeResult(
                symbol=c.symbol,
                stock_price=c.stock_price,
                strike=c.strike,
                expiration=c.expiration,
                dte=c.dte,
                bid=c.bid,
                ask=c.ask,
                delta=c.delta,
                iv=c.iv,
                otm_pct=c.otm_pct,
                volume=c.volume,
                open_interest=c.open_interest,
                margin=margin,
                margin_source=c.margin_source,
                safety_score=round(safety, 4),
                liquidity_score=round(liquidity, 4),
                efficiency_score=round(efficiency, 4),
                composite_score=composite,
                premium_margin_ratio=round(premium_margin_ratio, 4),
                annualized_return_pct=round(annualized * 100, 2),
                contracts=1,  # Caller enriches with PositionSizer
                sector="",  # Caller enriches with get_sector()
            ))

        return results

    def _skipped_result(self, symbol: str, reason: str) -> BestStrikeResult:
        """Create a placeholder result for symbols with no viable candidates."""
        return BestStrikeResult(
            symbol=symbol,
            stock_price=0,
            strike=0,
            expiration="",
            dte=0,
            bid=0,
            ask=0,
            delta=None,
            iv=None,
            otm_pct=0,
            volume=None,
            open_interest=None,
            margin=0,
            margin_source="none",
            safety_score=0,
            liquidity_score=0,
            efficiency_score=0,
            composite_score=0,
            premium_margin_ratio=0,
            annualized_return_pct=0,
            contracts=0,
            sector="",
            status="skipped",
            skip_reason=reason,
        )

    @staticmethod
    def _estimate_margin_regt(c: ScannerStrikeCandidate) -> float:
        """Estimate margin using Reg-T formula.

        Reg-T naked put margin:
          max(20% of stock_price - OTM_amount + premium, 10% of stock_price) * 100
        """
        otm_amount = max(0, c.stock_price - c.strike)
        premium = c.bid  # Use bid as conservative premium estimate
        margin = (0.20 * c.stock_price - otm_amount + premium) * 100
        min_margin = 0.10 * c.stock_price * 100
        return round(max(margin, min_margin), 2)


# ---------------------------------------------------------------------------
# Phase 3: Portfolio-level 4-weight scoring and auto-selection
# ---------------------------------------------------------------------------


@dataclass
class PortfolioCandidate:
    """A BestStrikeResult enriched with AI scoring and portfolio selection.

    Extends the per-symbol best strike with Claude's AI assessment and
    the full 4-weight composite score for portfolio-level ranking.
    """

    # Inherited from BestStrikeResult
    symbol: str
    stock_price: float
    strike: float
    expiration: str
    dte: int
    bid: float
    ask: float
    delta: float | None
    iv: float | None
    otm_pct: float
    volume: int | None
    open_interest: int | None
    margin: float
    margin_source: str
    safety_score: float
    liquidity_score: float
    efficiency_score: float
    premium_margin_ratio: float
    annualized_return_pct: float
    contracts: int
    sector: str

    # AI enrichment (from Claude)
    ai_score: float | None = None  # 1-10 from Claude
    ai_recommendation: str | None = None  # strong_buy/buy/neutral/avoid
    ai_reasoning: str | None = None
    ai_risk_flags: list[str] = field(default_factory=list)

    # Portfolio selection fields
    composite_score: float = 0.0  # Full 4-weight score
    selected: bool = False
    skip_reason: str | None = None
    total_margin: float = 0.0  # margin × contracts
    portfolio_rank: int = 0

    @classmethod
    def from_best_strike(
        cls,
        bs: BestStrikeResult,
        ai_data: dict | None = None,
    ) -> "PortfolioCandidate":
        """Create a PortfolioCandidate from a BestStrikeResult + AI data.

        Args:
            bs: The per-symbol best strike result.
            ai_data: Dict with keys: score, recommendation, reasoning,
                     risk_flags (from Claude).

        Returns:
            PortfolioCandidate with AI fields populated.
        """
        ai = ai_data or {}
        return cls(
            symbol=bs.symbol,
            stock_price=bs.stock_price,
            strike=bs.strike,
            expiration=bs.expiration,
            dte=bs.dte,
            bid=bs.bid,
            ask=bs.ask,
            delta=bs.delta,
            iv=bs.iv,
            otm_pct=bs.otm_pct,
            volume=bs.volume,
            open_interest=bs.open_interest,
            margin=bs.margin,
            margin_source=bs.margin_source,
            safety_score=bs.safety_score,
            liquidity_score=bs.liquidity_score,
            efficiency_score=bs.efficiency_score,
            premium_margin_ratio=bs.premium_margin_ratio,
            annualized_return_pct=bs.annualized_return_pct,
            contracts=bs.contracts,
            sector=bs.sector,
            ai_score=ai.get("score"),
            ai_recommendation=ai.get("recommendation"),
            ai_reasoning=ai.get("reasoning"),
            ai_risk_flags=ai.get("risk_flags") or [],
            total_margin=bs.margin * bs.contracts,
        )


def compute_composite_score_4w(
    safety: float,
    liquidity: float,
    efficiency: float,
    ai_score_raw: float | None = None,
    w_safety: int = 40,
    w_liquidity: int = 30,
    w_ai: int = 20,
    w_efficiency: int = 10,
) -> float:
    """Compute 4-weight composite score for portfolio-level ranking.

    When ai_score_raw is None, falls back to 3-weight scoring
    (safety + liquidity + efficiency normalized to sum 1.0).

    Args:
        safety: Safety score (0.0-1.0).
        liquidity: Liquidity score (0.0-1.0).
        efficiency: Efficiency score (0.0-1.0).
        ai_score_raw: Claude AI score (1-10), or None if unavailable.
        w_safety: Weight for safety (default 40).
        w_liquidity: Weight for liquidity (default 30).
        w_ai: Weight for AI score (default 20).
        w_efficiency: Weight for efficiency (default 10).

    Returns:
        Composite score between 0.0 and 1.0.
    """
    if ai_score_raw is not None:
        # Normalize AI score from 1-10 to 0.0-1.0
        ai_norm = max(0.0, min(1.0, (ai_score_raw - 1) / 9))
        total_weight = w_safety + w_liquidity + w_ai + w_efficiency
        if total_weight <= 0:
            return 0.0
        score = (
            w_safety * safety
            + w_liquidity * liquidity
            + w_ai * ai_norm
            + w_efficiency * efficiency
        ) / total_weight
    else:
        # Fallback: 3-weight (exclude AI)
        total_weight = w_safety + w_liquidity + w_efficiency
        if total_weight <= 0:
            return 0.0
        score = (
            w_safety * safety
            + w_liquidity * liquidity
            + w_efficiency * efficiency
        ) / total_weight

    return round(score, 4)


def build_auto_select_portfolio(
    candidates: list[PortfolioCandidate],
    available_budget: float,
    max_positions: int = 10,
    max_per_sector: int = 5,
) -> tuple[list[PortfolioCandidate], list[PortfolioCandidate], list[str]]:
    """Greedy portfolio selection by composite score within budget.

    Sorts candidates by composite_score descending, then greedily selects
    trades that fit within the available margin budget while respecting
    position and sector limits.

    Args:
        candidates: List of PortfolioCandidates with composite_score set.
        available_budget: Maximum total margin for all selected trades.
        max_positions: Maximum number of trades in portfolio.
        max_per_sector: Maximum trades per sector.

    Returns:
        Tuple of (selected, skipped, warnings):
        - selected: Candidates chosen for portfolio (with selected=True,
          portfolio_rank set).
        - skipped: Candidates not chosen (with skip_reason set).
        - warnings: List of warning messages.
    """
    if not candidates:
        return [], [], []

    # Sort by composite score descending
    sorted_candidates = sorted(
        candidates, key=lambda c: c.composite_score, reverse=True
    )

    selected: list[PortfolioCandidate] = []
    skipped: list[PortfolioCandidate] = []
    warnings: list[str] = []

    used_budget = 0.0
    sector_counts: dict[str, int] = {}
    seen_symbols: set[str] = set()

    for candidate in sorted_candidates:
        # Check max positions
        if len(selected) >= max_positions:
            candidate.skip_reason = "max_positions"
            candidate.selected = False
            skipped.append(candidate)
            continue

        # Check duplicate symbol
        if candidate.symbol in seen_symbols:
            candidate.skip_reason = "duplicate_symbol"
            candidate.selected = False
            skipped.append(candidate)
            continue

        # Check sector limit
        sector = candidate.sector or "Unknown"
        if sector_counts.get(sector, 0) >= max_per_sector:
            candidate.skip_reason = "max_per_sector"
            candidate.selected = False
            skipped.append(candidate)
            continue

        # Check budget
        candidate.total_margin = candidate.margin * candidate.contracts
        if used_budget + candidate.total_margin > available_budget:
            candidate.skip_reason = "budget_exceeded"
            candidate.selected = False
            skipped.append(candidate)
            continue

        # Select this candidate
        candidate.selected = True
        candidate.portfolio_rank = len(selected) + 1
        used_budget += candidate.total_margin
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        seen_symbols.add(candidate.symbol)
        selected.append(candidate)

    if not selected and candidates:
        warnings.append("No candidates fit within the available budget")

    return selected, skipped, warnings
