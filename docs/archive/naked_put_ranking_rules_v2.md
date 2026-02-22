# Naked Put Trade Ranking Rules (Score & Rank Methodology)

## Top Trade Selection System

**Document Version:** 2.0  
**Created:** January 29, 2026  
**Updated:** January 29, 2026  
**Purpose:** Systematic scoring and ranking of naked put trades to maximize premium collection while managing risk and capital efficiency

---

## Philosophy

This methodology **ranks all candidates** rather than eliminating them. Trades outside optimal parameters receive lower scores but remain in consideration. This approach:

1. **Preserves optionality** - no good trade lost due to rigid filters
2. **Adapts to margin reality** - high-margin trades may be skipped at execution
3. **Maximizes capital efficiency** - optimize premium per dollar of margin used
4. **Maintains risk discipline** - poor risk/reward candidates rank lower naturally

---

## Scoring Framework Overview

All candidates are scored across **6 dimensions**, producing a composite score (0-100):

| Dimension | Weight | Optimizes For |
|-----------|--------|---------------|
| Risk-Adjusted Return | 25% | Premium vs. downside risk |
| Probability of Profit | 20% | Win rate / safety |
| IV Environment | 15% | Premium richness |
| Liquidity | 15% | Execution quality |
| Capital Efficiency | 15% | Premium per margin dollar |
| Safety Buffer | 10% | Distance from danger |

**Key Insight:** The scoring weights balance **return optimization** (40%) against **risk management** (60%), ensuring that chasing premium doesn't override safety.

---

## Dimension 1: Risk-Adjusted Return Score (25% weight)

**Purpose:** Reward high annualized returns while penalizing extreme values that signal excessive risk

**Primary Metric:** Annualized Return (Ann Rtn from Barchart)

### Scoring Table

| Ann Rtn Range | Score | Interpretation |
|---------------|-------|----------------|
| 30-50% | 100 | Sweet spot - strong return, reasonable risk |
| 50-75% | 90 | Excellent return, slightly elevated risk |
| 25-30% | 85 | Good return, conservative |
| 75-100% | 75 | High return, notable risk (IV crush opportunity?) |
| 20-25% | 70 | Acceptable return |
| 100-150% | 60 | Very high return - verify risk factors |
| 15-20% | 50 | Below target but viable |
| 150-200% | 40 | Extreme - likely high IV/event risk |
| 10-15% | 30 | Low return, poor capital use |
| > 200% | 20 | Danger zone - investigate before trading |
| < 10% | 10 | Insufficient return for risk taken |

**Rationale:** Returns above 30% annualized represent strong premium collection. However, extremely high returns (>100%) often signal elevated risk (earnings, high IV due to uncertainty) and receive diminishing scores. The sweet spot is 30-50% where risk/reward is optimal.

---

## Dimension 2: Probability of Profit Score (20% weight)

**Purpose:** Prioritize trades with high likelihood of success

**Primary Metric:** Profit Probability (from Barchart)

### Scoring Table

| Profit Prob | Score | Interpretation |
|-------------|-------|----------------|
| ≥ 90% | 100 | Excellent - high confidence |
| 85-90% | 90 | Very good |
| 80-85% | 80 | Good - standard target |
| 75-80% | 65 | Acceptable |
| 70-75% | 50 | Below target |
| 65-70% | 35 | Elevated risk |
| 60-65% | 20 | High risk |
| < 60% | 10 | Near coin-flip - avoid |

**Rationale:** Profit probability directly indicates likelihood of trade success. Target is 80%+ (equivalent to ~0.20 delta). Below 70% represents unacceptable risk for a premium-selling strategy.

---

## Dimension 3: IV Rank Score (15% weight)

**Purpose:** Identify premium-rich environments where selling options provides edge

**Primary Metric:** IV Rank (from Barchart)

### Scoring Table

| IV Rank | Score | Interpretation |
|---------|-------|----------------|
| 60-80% | 100 | Optimal - high premium, likely to contract |
| 80-100% | 90 | Very high - excellent premium but watch for events |
| 50-60% | 85 | Good selling environment |
| 40-50% | 70 | Moderate - acceptable |
| 30-40% | 50 | Below average premium |
| 20-30% | 30 | Low premium environment |
| < 20% | 15 | Poor selling conditions |

**Rationale:** IV Rank above 50% indicates options are expensive relative to history - ideal for selling. The 60-80% range is optimal as it offers rich premium with high likelihood of IV contraction. Extremely high IV (>80%) scores slightly lower due to potential event risk driving the elevated volatility.

---

## Dimension 4: Liquidity Score (15% weight)

**Purpose:** Ensure positions can be entered and exited efficiently

**Primary Metrics:** Open Interest + Daily Volume

### Scoring Table

| Open Interest | Volume | Score | Interpretation |
|---------------|--------|-------|----------------|
| ≥ 5,000 | ≥ 500 | 100 | Excellent liquidity |
| 3,000-5,000 | 300-500 | 90 | Very good |
| 2,000-3,000 | 200-300 | 80 | Good |
| 1,000-2,000 | 100-200 | 65 | Adequate |
| 500-1,000 | 50-100 | 45 | Marginal - wider spreads expected |
| 250-500 | 25-50 | 25 | Poor - execution risk |
| < 250 | < 25 | 10 | Very poor - avoid if possible |

**Scoring Logic:** Use the LOWER of the two sub-scores (OI score and Volume score) to ensure both metrics meet minimums.

**Rationale:** Low liquidity leads to wider bid-ask spreads, poor fills, and difficulty exiting positions. Minimum acceptable: OI 500+ and Volume 100+. Ideal: OI 2000+ and Volume 300+.

---

## Dimension 5: Capital Efficiency Score (15% weight)

**Purpose:** Maximize premium collection per dollar of margin committed

**Primary Metric:** Premium Return % (Return field from Barchart = Bid / Strike)

### Scoring Table

| Return % | Score | Interpretation |
|----------|-------|----------------|
| ≥ 3.0% | 100 | Excellent capital efficiency |
| 2.5-3.0% | 90 | Very good |
| 2.0-2.5% | 80 | Good |
| 1.5-2.0% | 65 | Acceptable |
| 1.0-1.5% | 50 | Below target |
| 0.5-1.0% | 30 | Poor efficiency |
| < 0.5% | 15 | Very poor - too much capital for premium |

**Note:** This is a proxy for margin efficiency using available Barchart data. The actual margin efficiency will be calculated at execution time using IBKR margin requirements:

```
Actual Margin Efficiency = (Premium × 100) / IBKR Margin Requirement
```

**Rationale:** Higher premium relative to capital at risk means better return on margin. This dimension ensures we're not tying up excessive capital for small premiums.

---

## Dimension 6: Safety Buffer Score (10% weight)

**Purpose:** Reward adequate distance between current price and strike

**Primary Metric:** Moneyness (OTM % from Barchart)

### Scoring Table

| OTM % | Score | Interpretation |
|-------|-------|----------------|
| 12-18% | 100 | Optimal buffer |
| 18-22% | 90 | Very conservative |
| 10-12% | 85 | Good buffer |
| 22-28% | 75 | Ultra-conservative (lower premium) |
| 8-10% | 60 | Moderate buffer |
| 5-8% | 40 | Thin buffer - elevated risk |
| > 28% | 30 | Too far OTM - minimal premium |
| < 5% | 15 | Danger zone - near ATM |

**Rationale:** The safety buffer represents how much the stock can drop before the option goes ITM. Sweet spot is 10-18% OTM - enough cushion for normal volatility while still collecting meaningful premium. Too far OTM (>25%) sacrifices premium; too close (<8%) increases assignment risk.

---

## Composite Score Calculation

### Formula

```python
def calculate_composite_score(candidate):
    return (
        risk_adjusted_return_score(candidate.ann_rtn) * 0.25 +
        probability_score(candidate.profit_prob) * 0.20 +
        iv_rank_score(candidate.iv_rank) * 0.15 +
        liquidity_score(candidate.open_interest, candidate.volume) * 0.15 +
        capital_efficiency_score(candidate.return_pct) * 0.15 +
        safety_buffer_score(candidate.moneyness) * 0.10
    )
```

### Score Interpretation

| Composite Score | Rating | Recommendation |
|-----------------|--------|----------------|
| 85-100 | A+ | Excellent - prioritize |
| 75-84 | A | Very good - strong candidate |
| 65-74 | B | Good - solid trade |
| 55-64 | C | Acceptable - consider if top picks exhausted |
| 45-54 | D | Below average - only if necessary |
| < 45 | F | Poor - avoid unless special circumstances |

---

## Tie-Breaking Rules

When candidates have identical composite scores, rank by:

1. **Higher Profit Probability** (safety first)
2. **Higher Capital Efficiency** (premium optimization)
3. **Higher Open Interest** (liquidity)
4. **Lower DTE** (faster capital turnover)

---

## Diversification Guidelines

Apply these as **soft preferences**, not hard filters:

### Symbol Concentration
- **Preference:** Maximum 2-3 positions per underlying symbol
- **Action:** If symbol appears 4+ times in top 20, consider spreading across different strikes/expirations

### DTE Distribution
- **Preference:** Stagger expirations for regular income
- **Target Mix:**
  - 30-40% in 7-14 DTE (quick theta decay)
  - 40-50% in 15-30 DTE (balanced)
  - 10-20% in 31-45 DTE (if premium attractive)

### Sector Awareness
- **Preference:** Avoid heavy concentration in single sector
- **Action:** Monitor if 50%+ of positions are in same sector (e.g., all tech)

---

## Execution Workflow

### Step 1: Score All Candidates
```
Input: Barchart CSV (e.g., 234 candidates)
Process: Calculate composite score for each
Output: Ranked list, best to worst
```

### Step 2: Review Top Candidates
```
Review top 20-30 candidates
Note any concerns (earnings dates, news events, sector concentration)
Prepare execution list
```

### Step 3: Margin-Constrained Execution

```
Available Capital: $100,000
Target Margin: 50% ± 10% = $40,000 - $60,000
Hard Stop: 60% = $60,000

cumulative_margin = 0
trades_executed = []

FOR each candidate in ranked_list (best to worst):
    
    margin_required = GET_IBKR_MARGIN(candidate)
    
    IF (cumulative_margin + margin_required) <= $60,000:
        EXECUTE trade
        cumulative_margin += margin_required
        trades_executed.append(candidate)
        
        IF cumulative_margin >= $40,000:
            # Reached target zone, can stop or continue
            IF cumulative_margin >= $50,000:
                BREAK  # Stop at target
    ELSE:
        SKIP  # Would exceed limit, try next candidate
        
    IF len(trades_executed) >= 15:
        BREAK  # Position count limit

RETURN trades_executed, cumulative_margin
```

### Step 4: Margin Efficiency Re-Ranking (At Execution)

Once actual IBKR margin requirements are known, calculate true margin efficiency:

```
True Margin Efficiency = (Bid × 100) / IBKR_Margin_Requirement × 100%
```

If two candidates have similar composite scores, prefer the one with higher margin efficiency - this maximizes premium per dollar committed.

---

## Risk Management Integration

### Pre-Trade Checklist

Before executing any trade, verify:

- [ ] No earnings within DTE period (unless intentional IV play)
- [ ] No major scheduled events (FDA decisions, product launches)
- [ ] Stock not in active downtrend (check 20/50 SMA relationship)
- [ ] Not adding to existing position in same underlying
- [ ] Cumulative margin within 50% target

### Position Monitoring Triggers

| Condition | Action |
|-----------|--------|
| Stock drops 5%+ | Review position, consider early exit |
| Delta increases to 0.40+ | Evaluate roll or close |
| 50% profit achieved | Consider closing early |
| IV drops 30%+ | Consider closing (captured IV crush) |
| 7 DTE remaining | Decide: hold to expiry or close |

### Emergency Exit Rules

| Condition | Action |
|-----------|--------|
| Stock gaps down 10%+ | Immediate review, likely close |
| Delta exceeds 0.50 | Close or roll |
| Unexpected earnings/event | Close before event |
| Portfolio margin exceeds 60% | Close lowest-conviction position |

---

## Implementation: CSV Processing Steps

### Input Fields from Barchart CSV

| Field | Use |
|-------|-----|
| Symbol | Identification |
| Price~ | Underlying reference |
| Exp Date | Contract specification |
| DTE | Time scoring input |
| Strike | Contract specification |
| Moneyness | Safety buffer scoring |
| Bid | Premium / Capital efficiency |
| Volume | Liquidity scoring |
| Open Int | Liquidity scoring |
| IV Rank | IV environment scoring |
| Delta | Cross-reference with Profit Prob |
| Return | Capital efficiency scoring |
| Ann Rtn | Risk-adjusted return scoring |
| Profit Prob | Probability scoring |

### Output: Ranked Candidate List

| Rank | Symbol | Strike | Exp | DTE | Bid | Composite | Return | Prob | IV Rank | Liquidity | Efficiency | Safety |
|------|--------|--------|-----|-----|-----|-----------|--------|------|---------|-----------|------------|--------|
| 1 | NVDA | 850P | 2/14 | 16 | 4.20 | 87.5 | 95 | 90 | 85 | 90 | 80 | 85 |
| 2 | AAPL | 210P | 2/7 | 9 | 1.85 | 85.2 | 85 | 95 | 80 | 100 | 75 | 80 |
| 3 | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

---

## Quick Reference Card

### Optimal Ranges (Highest Scores)

| Metric | Optimal Range |
|--------|---------------|
| Ann Rtn | 30-50% |
| Profit Prob | 85%+ |
| IV Rank | 60-80% |
| Open Interest | 2,000+ |
| Volume | 300+ |
| Return % | 2.0%+ |
| Moneyness (OTM) | 12-18% |
| Delta | 0.12-0.18 |
| DTE | 14-30 days |

### Scoring Weights

```
Composite = (Risk-Adj Return × 0.25) +
            (Probability × 0.20) +
            (IV Rank × 0.15) +
            (Liquidity × 0.15) +
            (Capital Efficiency × 0.15) +
            (Safety Buffer × 0.10)
```

### Margin Execution Rule

```
Execute trades in rank order until:
- Cumulative margin reaches ~50% of account
- OR 15 positions reached
- Hard stop at 60% margin utilization
```

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-29 | Claude | Initial specification |
| 2.0 | 2026-01-29 | Claude | Changed from Filter to Score & Rank methodology; Added margin-constrained execution; Restructured scoring to penalize rather than eliminate |

---

**End of Naked Put Ranking Rules**
