# Learning Module Improvement Plan

**Created:** 2026-03-14
**Status:** In Progress
**Context:** Evaluated against professional trading system standards (quant firms, SEC/MiFID II, academic research)

## Current State

- 3,676+ trades in database (historical imports + live)
- Pattern detector finds 93 patterns across 19 dimensions
- ALL 93 patterns rejected at validation (0 saved, 0 proposed, 0 applied)
- No audit trail for findings — detected patterns discarded in memory
- `--proposals` CLI queries wrong event_type, shows nothing
- No alpha decay monitoring, no regime adaptation, no multiple testing correction

## Phase A: Make the Current System Useful (Fix Data Loss) — COMPLETE (2026-03-14)

**Priority:** HIGHEST — blocks all other phases
**Goal:** Persist findings, fix CLI output, add early-stage pattern tier

| ID | Change | Status | Details |
|----|--------|--------|---------|
| A1 | Persist ALL detected patterns | DONE | Saves to `learning_history` with `event_type="pattern_candidate"`, including stats and rejection reason as JSON. 93 patterns now persisted per analysis run. |
| A2 | Persist proposals even when not applied | DONE | Saves to `learning_history` with `event_type="proposal_generated"`. `--proposals` shows both PENDING and APPLIED. |
| A3 | Add early-stage validation tier | DONE | PRELIMINARY status for patterns with n>=10, p<0.20. 24/93 patterns now surface as preliminary (were all rejected before). |
| A4 | Fix `--analyse` output | DONE | Shows "Top 15 Patterns" table with status, stats, p-value, effect size, and what each pattern needs. |
| A5 | Fix `--proposals` query | DONE | Queries `event_type IN ("proposal_generated", "parameter_adjusted")`. Shows both pending and applied. |

**Key finding:** 3,623 KALA-imported trades have `otm_pct=NULL`, `delta_at_entry=NULL`, `iv_at_entry=NULL`. These fields aren't populated during import, so delta/IV/OTM pattern dimensions are empty. However, `vix_at_entry`, `sector`, `dte`, `roi`, `market_regime` are 99%+ populated. The 93 patterns detected come from well-populated dimensions. All fail validation because the strategy is so consistent (85% win rate) that bucket-to-bucket differences are tiny (Cohen's d < 0.2 across the board).

## Phase B: Add Alpha Decay Monitoring — COMPLETE (2026-03-14)

**Priority:** HIGH — detect strategy degradation early
**Goal:** Rolling metrics, regime splits, change detection

| ID | Change | Status | Details |
|----|--------|--------|---------|
| B1 | Rolling performance metrics | DONE | 30/90/365-day rolling win rate, avg ROI, Sharpe, max drawdown, loss streak. Accessible via `learn --health`. |
| B2 | Regime performance splits | DONE | VIX-based regime buckets (low/normal/elevated/high/extreme) with per-regime win rate, ROI, Sharpe. |
| B3 | CUSUM change detection | DONE | Two-sided CUSUM (degradation + improvement), using first-half reference period. Alerts surfaced in health report. |

**First run findings:**
- 30d win rate at 75.8% vs 90.7% historical → CRITICAL health status
- CUSUM degradation signal at 214.3 (threshold: 4.0) — sustained shift
- Extreme VIX regime (>35) has 97.1% win rate, highest avg P&L
- Normal VIX (15-20) has worst ROI at -4.54%
- Health report persisted to `learning_history` with `event_type="alpha_decay_analysis"`

## Phase C: Statistical Rigor — COMPLETE (2026-03-14)

**Priority:** MEDIUM — becomes critical as trade count grows
**Goal:** Prevent false discoveries, improve validation

| ID | Change | Status | Details |
|----|--------|--------|---------|
| C1 | Multiple testing correction | DONE | Benjamini-Hochberg FDR applied before validation. Reduced significant patterns from 16 to 6 (eliminated 10 likely false discoveries from 93 tests). |
| C2 | Adaptive validation thresholds | DONE | Effect size threshold scales with sample size: `d = 2.8/√n`, floor 0.10, ceiling 0.50. At n=2553, threshold drops to 0.10 (from fixed 0.50). |
| C3 | Walk-forward validation | DONE | Replaced random k-fold CV with expanding-window walk-forward. Train on past, test on future — matches real trading. |

**Impact:** 16 PRELIMINARY patterns (vs 24 before FDR). 0 VALIDATED — walk-forward CV correctly blocks patterns that haven't been consistently exploitable across time periods. The top signal is `moderate_fomc_proximity` (n=514, p=0.0001, d=0.283) — trades entered near FOMC meetings show a meaningful directional effect.

## Phase D: Regime-Aware Adaptation

**Priority:** MEDIUM — the single biggest performance improvement for options selling
**Goal:** Different parameters per market regime

| ID | Change | Details |
|----|--------|---------|
| D1 | VIX regime parameter tables | Different delta/DTE/size targets per VIX regime. |
| D2 | Term structure monitoring | Track VIX contango/backwardation as entry gate. Backwardation = near-term fear = reduce selling. |
| D3 | Auto-experiment on regime shifts | When entering a new regime, spawn experiment comparing adapted vs static params. Builds evidence for regime-specific tuning. |

## Professional Standards Reference

| Area | Standard | Threshold |
|------|----------|-----------|
| Minimum trades before learning | CLT minimum | 30 trades absolute minimum per bucket |
| Minimum trades for confidence | Statistical power | 100-200 for reliable metrics |
| Significance level | p-value threshold | p < 0.05, corrected for multiple comparisons |
| Multiple testing | Correction method | Benjamini-Hochberg FDR or Bonferroni |
| Overfitting check | PBO | Probability of Backtest Overfitting < 50% |
| Validation protocol | Three-stage | In-sample / Walk-forward / Out-of-sample |
| Parameter changes | Canary deployment | 5-20% allocation to experimental, with kill switch |
| Alpha decay monitoring | Rolling metrics | 30/90/365-day rolling Sharpe, win rate, avg P/L |
| Regime detection | VIX-based | Minimum 4 regimes (low/normal/high/extreme) |
| Audit trail | Every decision logged | Timestamp, reasoning, inputs, config version, outcome |
| Parameter versioning | Full history | Old value, new value, evidence, expected vs actual improvement |

## Sources

- AlgoXpert Alpha Research Framework (arXiv, March 2026)
- Bailey & Lopez de Prado: Deflated Sharpe Ratio
- SEC Rule 15c3-5 (pre-trade risk controls)
- FINRA Regulatory Notice 15-09 (algorithmic trading records)
- MiFID II Article 17 (algorithmic trading)
- Maven Securities: Alpha Decay research
