# Scoring Engine Implementation Approach

## Integration Strategy for Naked Put Ranking Rules

**Document Version:** 1.0  
**Created:** January 29, 2026  
**Purpose:** Define how to integrate the new scoring rules into the existing trading agent  

---

## Current State Analysis

### What Exists

| Component | Status | Location |
|-----------|--------|----------|
| CSV Parser | ✅ Built | `src/tools/barchart_csv_parser.py` |
| BarchartCandidate dataclass | ✅ Built | `src/data/candidates.py` |
| IBKR Validator | ✅ Built | `src/tools/ibkr_validator.py` |
| ScanOpportunity model | ✅ Built | `src/data/models.py` |
| CLI scan command | ✅ Built | `src/cli/main.py` |
| Database schema | ⚠️ Partial | Missing Phase 2.6 data fields |

### What's Missing

| Component | Priority | Notes |
|-----------|----------|-------|
| **Scoring Engine** | 🔴 Critical | New module to score candidates |
| **Ranked output** | 🔴 Critical | CLI should show ranked results |
| **Diversification logic** | 🟡 Important | Max 3 per symbol rule |
| **Extended data capture** | 🟡 Important | Score components for learning |
| **Phase 2.6 DB schema** | 🟢 Future | Full 125-field data collection |

---

## Recommended Implementation Approach

### Phase A: Standalone Scoring Engine (1-2 hours)

Create a new scoring module that operates on `BarchartCandidate` objects:

```
src/
└── scoring/
    ├── __init__.py
    ├── scorer.py           # Main scoring engine
    ├── score_rules.py      # Individual scoring functions
    └── score_config.py     # Configurable weights & thresholds
```

**Key Design Decisions:**

1. **Score at parse time** - Add scores immediately after CSV parsing
2. **Store scores in BarchartCandidate** - Extend dataclass with score fields
3. **Configurable weights** - Allow tuning via YAML config
4. **Return breakdown** - Preserve individual dimension scores for analysis

### Phase B: CLI Integration (1 hour)

Modify existing `scan --from-csv` command to:

1. Score all candidates after parsing
2. Apply diversification rules (max 3 per symbol)
3. Display ranked table with scores
4. Support `--top N` flag to limit output

### Phase C: Database Extension (Future - with Phase 2.6)

When you implement the full data collection schema:

1. Add score fields to `ScanOpportunity` model
2. Store scoring rule version for reproducibility
3. Enable learning engine to correlate scores with outcomes

---

## Detailed Implementation Specification

### 1. Score Configuration (`src/scoring/score_config.py`)

```python
"""Scoring configuration with tunable weights and thresholds."""

from dataclasses import dataclass
from typing import Dict, Tuple

@dataclass
class ScoreWeights:
    """Weights for composite score calculation."""
    risk_adjusted_return: float = 0.25
    probability: float = 0.20
    iv_rank: float = 0.15
    liquidity: float = 0.15
    capital_efficiency: float = 0.15
    safety_buffer: float = 0.10
    
    def validate(self) -> bool:
        """Ensure weights sum to 1.0."""
        total = (
            self.risk_adjusted_return +
            self.probability +
            self.iv_rank +
            self.liquidity +
            self.capital_efficiency +
            self.safety_buffer
        )
        return abs(total - 1.0) < 0.001

@dataclass 
class ScoreThresholds:
    """Thresholds for individual dimension scoring."""
    
    # Annualized return thresholds (as decimal, e.g., 0.30 = 30%)
    ann_return_optimal: Tuple[float, float] = (0.30, 0.50)
    ann_return_good: Tuple[float, float] = (0.25, 0.75)
    ann_return_red_flag: float = 1.50  # >150% is suspicious
    
    # Probability thresholds
    prob_excellent: float = 0.90
    prob_good: float = 0.85
    prob_acceptable: float = 0.70
    
    # IV Rank thresholds
    iv_rank_optimal: Tuple[float, float] = (0.60, 0.80)
    iv_rank_good: Tuple[float, float] = (0.50, 0.90)
    
    # Liquidity minimums
    oi_excellent: int = 5000
    oi_good: int = 2000
    oi_adequate: int = 1000
    oi_minimum: int = 250
    volume_excellent: int = 500
    volume_good: int = 200
    volume_adequate: int = 100
    volume_minimum: int = 25
    
    # Capital efficiency (return %)
    efficiency_excellent: float = 0.03
    efficiency_good: float = 0.02
    efficiency_acceptable: float = 0.01
    
    # Safety buffer (OTM %)
    otm_optimal: Tuple[float, float] = (0.12, 0.18)
    otm_good: Tuple[float, float] = (0.10, 0.22)
    otm_too_close: float = 0.05
    otm_too_far: float = 0.28

@dataclass
class DiversificationRules:
    """Rules for portfolio diversification."""
    max_positions_per_symbol: int = 3
    max_same_expiration_pct: float = 0.50  # Max 50% in same expiration
    max_sector_concentration_pct: float = 0.40  # Future: sector tracking

# Default configuration
DEFAULT_WEIGHTS = ScoreWeights()
DEFAULT_THRESHOLDS = ScoreThresholds()
DEFAULT_DIVERSIFICATION = DiversificationRules()
```

### 2. Individual Score Functions (`src/scoring/score_rules.py`)

```python
"""Individual dimension scoring functions.

Each function returns a score from 0-100 based on the research-backed
criteria defined in naked_put_ranking_rules_v2.md.
"""

from src.scoring.score_config import ScoreThresholds

def score_risk_adjusted_return(ann_return: float, thresholds: ScoreThresholds = None) -> float:
    """Score annualized return. Sweet spot is 30-50%.
    
    Args:
        ann_return: Annualized return as decimal (0.35 = 35%)
        thresholds: Optional custom thresholds
        
    Returns:
        Score from 0-100
    """
    t = thresholds or ScoreThresholds()
    pct = ann_return * 100  # Convert to percentage for readability
    
    # Sweet spot: 30-50%
    if 30 <= pct <= 50:
        return 100.0
    elif 50 < pct <= 75:
        return 90.0
    elif 25 <= pct < 30:
        return 85.0
    elif 75 < pct <= 100:
        return 75.0
    elif 20 <= pct < 25:
        return 70.0
    elif 100 < pct <= 150:
        return 60.0  # Getting risky
    elif 15 <= pct < 20:
        return 50.0
    elif 150 < pct <= 200:
        return 40.0  # Red flag territory
    elif 10 <= pct < 15:
        return 30.0
    elif pct > 200:
        return 20.0  # Almost certainly hidden risk
    else:  # < 10%
        return 10.0


def score_probability(profit_prob: float, thresholds: ScoreThresholds = None) -> float:
    """Score profit probability. Higher is better, target 85%+.
    
    Args:
        profit_prob: Probability of profit as decimal (0.85 = 85%)
        thresholds: Optional custom thresholds
        
    Returns:
        Score from 0-100
    """
    t = thresholds or ScoreThresholds()
    pct = profit_prob * 100
    
    if pct >= 90:
        return 100.0
    elif pct >= 85:
        return 90.0
    elif pct >= 80:
        return 80.0
    elif pct >= 75:
        return 65.0
    elif pct >= 70:
        return 50.0
    elif pct >= 65:
        return 35.0
    elif pct >= 60:
        return 20.0
    else:
        return 10.0


def score_iv_rank(iv_rank: float, thresholds: ScoreThresholds = None) -> float:
    """Score IV Rank. Optimal is 60-80% (rich premium, likely contraction).
    
    Args:
        iv_rank: IV Rank as decimal (0.72 = 72%)
        thresholds: Optional custom thresholds
        
    Returns:
        Score from 0-100
    """
    t = thresholds or ScoreThresholds()
    pct = iv_rank * 100
    
    # Optimal: 60-80%
    if 60 <= pct <= 80:
        return 100.0
    elif pct > 80:
        return 90.0  # Very high IV - good premium but watch for events
    elif 50 <= pct < 60:
        return 85.0
    elif 40 <= pct < 50:
        return 70.0
    elif 30 <= pct < 40:
        return 50.0
    elif 20 <= pct < 30:
        return 30.0
    else:  # < 20%
        return 15.0  # Poor selling environment


def score_liquidity(open_interest: int, volume: int, thresholds: ScoreThresholds = None) -> float:
    """Score liquidity based on OI and Volume. Uses minimum of both scores.
    
    Args:
        open_interest: Open interest
        volume: Daily volume
        thresholds: Optional custom thresholds
        
    Returns:
        Score from 0-100 (minimum of OI and Volume scores)
    """
    t = thresholds or ScoreThresholds()
    
    # Score Open Interest
    if open_interest >= t.oi_excellent:
        oi_score = 100.0
    elif open_interest >= 3000:
        oi_score = 90.0
    elif open_interest >= t.oi_good:
        oi_score = 80.0
    elif open_interest >= t.oi_adequate:
        oi_score = 65.0
    elif open_interest >= 500:
        oi_score = 45.0
    elif open_interest >= t.oi_minimum:
        oi_score = 25.0
    else:
        oi_score = 10.0
    
    # Score Volume
    if volume >= t.volume_excellent:
        vol_score = 100.0
    elif volume >= 300:
        vol_score = 90.0
    elif volume >= t.volume_good:
        vol_score = 80.0
    elif volume >= t.volume_adequate:
        vol_score = 65.0
    elif volume >= 50:
        vol_score = 45.0
    elif volume >= t.volume_minimum:
        vol_score = 25.0
    else:
        vol_score = 10.0
    
    # Return minimum - both dimensions must be adequate
    return min(oi_score, vol_score)


def score_capital_efficiency(bid: float, strike: float, thresholds: ScoreThresholds = None) -> float:
    """Score capital efficiency (premium return %).
    
    Note: This is a proxy. True margin efficiency requires IBKR margin data.
    
    Args:
        bid: Option bid price
        strike: Strike price
        thresholds: Optional custom thresholds
        
    Returns:
        Score from 0-100
    """
    t = thresholds or ScoreThresholds()
    
    return_pct = (bid / strike) * 100 if strike > 0 else 0
    
    if return_pct >= 3.0:
        return 100.0
    elif return_pct >= 2.5:
        return 90.0
    elif return_pct >= 2.0:
        return 80.0
    elif return_pct >= 1.5:
        return 65.0
    elif return_pct >= 1.0:
        return 50.0
    elif return_pct >= 0.5:
        return 30.0
    else:
        return 15.0


def score_safety_buffer(moneyness_pct: float, thresholds: ScoreThresholds = None) -> float:
    """Score safety buffer (OTM distance). Optimal is 12-18% OTM.
    
    Args:
        moneyness_pct: OTM percentage as decimal (negative from Barchart)
        thresholds: Optional custom thresholds
        
    Returns:
        Score from 0-100
    """
    t = thresholds or ScoreThresholds()
    
    # Convert to positive OTM percentage
    otm = abs(moneyness_pct) * 100
    
    # Optimal: 12-18% OTM
    if 12 <= otm <= 18:
        return 100.0
    elif 18 < otm <= 22:
        return 90.0
    elif 10 <= otm < 12:
        return 85.0
    elif 22 < otm <= 28:
        return 75.0
    elif 8 <= otm < 10:
        return 60.0
    elif 5 <= otm < 8:
        return 40.0
    elif otm > 28:
        return 30.0  # Too far - minimal premium
    else:  # < 5%
        return 15.0  # Too close to ATM
```

### 3. Main Scoring Engine (`src/scoring/scorer.py`)

```python
"""Main scoring engine for naked put candidates.

Calculates composite scores for BarchartCandidate objects using
research-backed scoring rules.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from loguru import logger

from src.data.candidates import BarchartCandidate
from src.scoring.score_rules import (
    score_risk_adjusted_return,
    score_probability,
    score_iv_rank,
    score_liquidity,
    score_capital_efficiency,
    score_safety_buffer,
)
from src.scoring.score_config import (
    ScoreWeights,
    ScoreThresholds,
    DiversificationRules,
    DEFAULT_WEIGHTS,
    DEFAULT_THRESHOLDS,
    DEFAULT_DIVERSIFICATION,
)


@dataclass
class ScoredCandidate:
    """Candidate with scoring breakdown."""
    
    candidate: BarchartCandidate
    
    # Individual dimension scores (0-100)
    return_score: float = 0.0
    probability_score: float = 0.0
    iv_rank_score: float = 0.0
    liquidity_score: float = 0.0
    efficiency_score: float = 0.0
    safety_score: float = 0.0
    
    # Composite score (0-100)
    composite_score: float = 0.0
    
    # Ranking info
    rank: Optional[int] = None
    diversified_rank: Optional[int] = None
    
    # Grade interpretation
    grade: str = ""
    
    @property
    def symbol(self) -> str:
        return self.candidate.symbol
    
    @property
    def strike(self) -> float:
        return self.candidate.strike
    
    @property
    def expiration(self):
        return self.candidate.expiration
    
    @property
    def dte(self) -> int:
        return self.candidate.dte
    
    @property
    def bid(self) -> float:
        return self.candidate.bid
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage/display."""
        return {
            **self.candidate.to_dict(),
            "return_score": round(self.return_score, 1),
            "probability_score": round(self.probability_score, 1),
            "iv_rank_score": round(self.iv_rank_score, 1),
            "liquidity_score": round(self.liquidity_score, 1),
            "efficiency_score": round(self.efficiency_score, 1),
            "safety_score": round(self.safety_score, 1),
            "composite_score": round(self.composite_score, 1),
            "rank": self.rank,
            "diversified_rank": self.diversified_rank,
            "grade": self.grade,
        }


class NakedPutScorer:
    """Scores naked put candidates using research-backed rules."""
    
    SCORING_VERSION = "1.0"  # Track for reproducibility
    
    def __init__(
        self,
        weights: Optional[ScoreWeights] = None,
        thresholds: Optional[ScoreThresholds] = None,
        diversification: Optional[DiversificationRules] = None,
    ):
        """Initialize scorer with configuration.
        
        Args:
            weights: Score dimension weights (must sum to 1.0)
            thresholds: Scoring thresholds
            diversification: Diversification rules
        """
        self.weights = weights or DEFAULT_WEIGHTS
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.diversification = diversification or DEFAULT_DIVERSIFICATION
        
        if not self.weights.validate():
            raise ValueError("Score weights must sum to 1.0")
        
        logger.info(
            f"Initialized NakedPutScorer v{self.SCORING_VERSION} with weights: "
            f"return={self.weights.risk_adjusted_return}, "
            f"prob={self.weights.probability}, "
            f"iv={self.weights.iv_rank}, "
            f"liq={self.weights.liquidity}, "
            f"eff={self.weights.capital_efficiency}, "
            f"safety={self.weights.safety_buffer}"
        )
    
    def score_candidate(self, candidate: BarchartCandidate) -> ScoredCandidate:
        """Score a single candidate.
        
        Args:
            candidate: BarchartCandidate to score
            
        Returns:
            ScoredCandidate with all scores calculated
        """
        scored = ScoredCandidate(candidate=candidate)
        
        # Calculate individual dimension scores
        scored.return_score = score_risk_adjusted_return(
            candidate.annualized_return_pct, self.thresholds
        )
        scored.probability_score = score_probability(
            candidate.profit_probability, self.thresholds
        )
        scored.iv_rank_score = score_iv_rank(
            candidate.iv_rank, self.thresholds
        )
        scored.liquidity_score = score_liquidity(
            candidate.open_interest, candidate.volume, self.thresholds
        )
        scored.efficiency_score = score_capital_efficiency(
            candidate.bid, candidate.strike, self.thresholds
        )
        scored.safety_score = score_safety_buffer(
            candidate.moneyness_pct, self.thresholds
        )
        
        # Calculate weighted composite
        scored.composite_score = (
            scored.return_score * self.weights.risk_adjusted_return +
            scored.probability_score * self.weights.probability +
            scored.iv_rank_score * self.weights.iv_rank +
            scored.liquidity_score * self.weights.liquidity +
            scored.efficiency_score * self.weights.capital_efficiency +
            scored.safety_score * self.weights.safety_buffer
        )
        
        # Assign grade
        scored.grade = self._get_grade(scored.composite_score)
        
        return scored
    
    def score_all(self, candidates: List[BarchartCandidate]) -> List[ScoredCandidate]:
        """Score and rank all candidates.
        
        Args:
            candidates: List of BarchartCandidate objects
            
        Returns:
            List of ScoredCandidate sorted by composite score (descending)
        """
        logger.info(f"Scoring {len(candidates)} candidates...")
        
        # Score all
        scored = [self.score_candidate(c) for c in candidates]
        
        # Sort by composite score (descending), then by tiebreakers
        scored.sort(key=lambda x: (
            -x.composite_score,
            -x.probability_score,  # Tiebreaker 1: Higher probability
            -x.efficiency_score,   # Tiebreaker 2: Higher efficiency
            -x.candidate.open_interest,  # Tiebreaker 3: Higher OI
            x.candidate.dte,       # Tiebreaker 4: Lower DTE
        ))
        
        # Assign ranks
        for i, s in enumerate(scored, 1):
            s.rank = i
        
        logger.info(
            f"Scoring complete. Top score: {scored[0].composite_score:.1f} "
            f"({scored[0].symbol}), Bottom: {scored[-1].composite_score:.1f}"
        )
        
        return scored
    
    def apply_diversification(
        self, 
        scored: List[ScoredCandidate],
        max_per_symbol: Optional[int] = None,
    ) -> List[ScoredCandidate]:
        """Apply diversification rules to scored candidates.
        
        Args:
            scored: List of ScoredCandidate (should be pre-sorted by score)
            max_per_symbol: Override max positions per symbol
            
        Returns:
            Filtered list respecting diversification rules
        """
        max_sym = max_per_symbol or self.diversification.max_positions_per_symbol
        
        symbol_counts = {}
        diversified = []
        
        for s in scored:
            sym = s.symbol
            current_count = symbol_counts.get(sym, 0)
            
            if current_count < max_sym:
                symbol_counts[sym] = current_count + 1
                s.diversified_rank = len(diversified) + 1
                diversified.append(s)
        
        logger.info(
            f"Diversification applied: {len(diversified)} of {len(scored)} "
            f"candidates retained (max {max_sym} per symbol)"
        )
        
        return diversified
    
    def _get_grade(self, score: float) -> str:
        """Convert numeric score to letter grade."""
        if score >= 85:
            return "A+"
        elif score >= 75:
            return "A"
        elif score >= 65:
            return "B"
        elif score >= 55:
            return "C"
        elif score >= 45:
            return "D"
        else:
            return "F"
    
    def get_scoring_metadata(self) -> dict:
        """Return scoring configuration for audit/reproducibility."""
        return {
            "scoring_version": self.SCORING_VERSION,
            "weights": {
                "risk_adjusted_return": self.weights.risk_adjusted_return,
                "probability": self.weights.probability,
                "iv_rank": self.weights.iv_rank,
                "liquidity": self.weights.liquidity,
                "capital_efficiency": self.weights.capital_efficiency,
                "safety_buffer": self.weights.safety_buffer,
            },
            "diversification": {
                "max_per_symbol": self.diversification.max_positions_per_symbol,
            }
        }
```

### 4. CLI Integration

Modify `src/cli/main.py` to add scoring to the scan command:

```python
# In scan_from_csv() or equivalent function:

from src.scoring.scorer import NakedPutScorer

def scan_from_csv(filepath: Path, validate: bool = False, top_n: int = 20):
    """Scan from Barchart CSV with scoring."""
    
    # Parse CSV (existing code)
    candidates = parse_barchart_csv(filepath)
    console.print(f"[green]Parsed {len(candidates)} candidates[/green]")
    
    # NEW: Score all candidates
    scorer = NakedPutScorer()
    scored = scorer.score_all(candidates)
    
    # Apply diversification (max 3 per symbol)
    diversified = scorer.apply_diversification(scored)
    
    # Display ranked table
    display_ranked_table(diversified[:top_n], console)
    
    # If --validate flag, continue with IBKR validation
    if validate:
        # Existing validation code...
        pass
    
    return diversified


def display_ranked_table(scored: List[ScoredCandidate], console: Console):
    """Display ranked candidates in a Rich table."""
    
    table = Table(title="Ranked Naked Put Candidates (Diversified)")
    
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Symbol", style="green")
    table.add_column("Strike", justify="right")
    table.add_column("Exp", justify="center")
    table.add_column("DTE", justify="right")
    table.add_column("Bid", justify="right")
    table.add_column("Score", justify="right", style="bold")
    table.add_column("Grade", justify="center")
    table.add_column("Ret", justify="right")  # Return score
    table.add_column("Prob", justify="right")  # Probability score
    table.add_column("IV", justify="right")    # IV Rank score
    table.add_column("Liq", justify="right")   # Liquidity score
    
    for s in scored:
        grade_style = {
            "A+": "bold green",
            "A": "green", 
            "B": "yellow",
            "C": "orange",
            "D": "red",
            "F": "bold red",
        }.get(s.grade, "white")
        
        table.add_row(
            str(s.diversified_rank or s.rank),
            s.symbol,
            f"${s.strike:.2f}",
            str(s.expiration),
            str(s.dte),
            f"${s.bid:.2f}",
            f"{s.composite_score:.1f}",
            f"[{grade_style}]{s.grade}[/{grade_style}]",
            f"{s.return_score:.0f}",
            f"{s.probability_score:.0f}",
            f"{s.iv_rank_score:.0f}",
            f"{s.liquidity_score:.0f}",
        )
    
    console.print(table)
```

---

## Database Extension (For Phase 2.6)

When you're ready to implement full data collection, extend `ScanOpportunity`:

```python
# Add to ScanOpportunity model in src/data/models.py:

class ScanOpportunity(Base):
    # ... existing fields ...
    
    # === Scoring Fields (Phase 2.5B) ===
    composite_score = Column(Float, nullable=True)
    return_score = Column(Float, nullable=True)
    probability_score = Column(Float, nullable=True)
    iv_rank_score = Column(Float, nullable=True)
    liquidity_score = Column(Float, nullable=True)
    efficiency_score = Column(Float, nullable=True)
    safety_score = Column(Float, nullable=True)
    score_grade = Column(String(5), nullable=True)
    scoring_version = Column(String(20), nullable=True)
    scoring_config = Column(JSON, nullable=True)  # Store weights/thresholds used
    
    # Ranking
    raw_rank = Column(Integer, nullable=True)  # Rank before diversification
    diversified_rank = Column(Integer, nullable=True)  # Rank after diversification
```

This allows the learning engine to:
1. Correlate individual score components with trade outcomes
2. Identify which dimensions are most predictive
3. Test different weight configurations

---

## Implementation Checklist

### Phase A: Scoring Engine
- [ ] Create `src/scoring/` directory
- [ ] Implement `score_config.py` with dataclasses
- [ ] Implement `score_rules.py` with scoring functions
- [ ] Implement `scorer.py` with NakedPutScorer class
- [ ] Add unit tests for scoring functions
- [ ] Test with sample CSV data

### Phase B: CLI Integration  
- [ ] Add scoring to `scan --from-csv` command
- [ ] Implement ranked table display
- [ ] Add `--top N` flag for output limit
- [ ] Add `--no-diversify` flag to skip diversification
- [ ] Update help text and examples

### Phase C: Database (Future)
- [ ] Create Alembic migration for score fields
- [ ] Update ScanOpportunity model
- [ ] Store scoring metadata with each scan
- [ ] Enable score-outcome correlation queries

---

## CLI Usage After Implementation

```bash
# Score and rank candidates (default: top 20, diversified)
python -m src.cli.main scan --from-csv export.csv

# Show top 50 with scores
python -m src.cli.main scan --from-csv export.csv --top 50

# Skip diversification (show all SLV trades if best)
python -m src.cli.main scan --from-csv export.csv --no-diversify

# Score, then validate top candidates with IBKR
python -m src.cli.main scan --from-csv export.csv --validate

# Full workflow: score -> validate -> show execution-ready list
python -m src.cli.main scan --from-csv export.csv --validate --top 10
```

---

## Learning Engine Integration (Future)

Once you have 50+ closed trades with score data:

```sql
-- Which score dimension correlates most with wins?
SELECT 
    'return_score' as dimension,
    AVG(CASE WHEN t.profit_loss > 0 THEN return_score ELSE NULL END) as avg_when_win,
    AVG(CASE WHEN t.profit_loss <= 0 THEN return_score ELSE NULL END) as avg_when_loss,
    AVG(CASE WHEN t.profit_loss > 0 THEN return_score ELSE NULL END) - 
    AVG(CASE WHEN t.profit_loss <= 0 THEN return_score ELSE NULL END) as difference
FROM scan_opportunities so
JOIN trades t ON so.trade_id = t.trade_id
WHERE so.composite_score IS NOT NULL

UNION ALL

SELECT 'probability_score', ... -- repeat for each dimension
```

---

## Document Control

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-29 | Initial specification |
