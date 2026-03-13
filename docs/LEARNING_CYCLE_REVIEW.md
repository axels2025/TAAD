# Self-Improvement Cycle: Status Review

**Date:** 2026-03-12
**Purpose:** Evaluate the learning/self-improvement loop — what's built, what's wired up, what's not running, and what's needed to activate it.

---

## TL;DR

The learning infrastructure is **~80% built but only ~20% wired into the live daemon.** Pattern detection, statistical validation, A/B experiment tracking, and parameter optimization all exist as working code. But the automated triggers to actually *run* them are missing — the weekly learning cycle never fires, and the EOD reflection (which runs Claude to evaluate today's decisions) is emitted as an event but never calls the learning code.

**Trade outcome feedback does work** — when positions close, they feed into experiment tracking. So A/B tests accumulate data. But nobody runs the evaluation step.

---

## What Exists Today

### Learning Engine (`src/learning/`)

| Component | File | Status | What It Does |
|-----------|------|--------|-------------|
| Pattern Detector | `pattern_detector.py` | Fully implemented | Scans trade history across 23+ dimensions (delta, IV rank, DTE, VIX regime, sector, day-of-week, RSI, MACD, earnings timing, FOMC proximity, etc.) |
| Statistical Validator | `statistical_validator.py` | Fully implemented | 4-layer validation: min samples (30), p-value (<0.05), effect size (Cohen's d > 0.5), cross-validation across time periods |
| Experiment Engine | `experiment_engine.py` | Fully implemented | A/B testing with 80/20 control/test split. Creates experiments, assigns trades, evaluates outcomes (ADOPT/REJECT/CONTINUE) |
| Parameter Optimizer | `parameter_optimizer.py` | Fully implemented | Converts validated patterns into config change proposals. Auto-applies at 90% confidence threshold |
| Learning Orchestrator | `learning_orchestrator.py` | Fully implemented | Runs the full 5-step weekly cycle: detect -> validate -> evaluate experiments -> propose changes -> auto-apply |
| Path Analyzer | `path_analyzer.py` | Fully implemented | Multi-point P&L trajectory analysis across position lifetime |
| Learning Loop | `learning_loop.py` | Implemented, **not called** | EOD reflection (Claude Sonnet), weekly learning trigger, trade outcome recording |

### AI Analysis (`src/agents/`)

| Component | Status | What It Does |
|-----------|--------|-------------|
| Performance Analyzer | Fully implemented | Sends aggregated patterns + metrics to Claude for narrative insights, hypothesis generation, risk identification |
| 3 analysis depths | Working | QUICK (Haiku), STANDARD (Sonnet), DEEP (Opus) — configurable cost/quality tradeoff |
| Cost tracking | Working | Every Claude API call logged to `claude_api_costs` table, daily cap enforced |

### Database Tables

All learning tables exist and have proper schemas:

- `patterns` — detected patterns with win_rate, avg_roi, p_value, confidence, status
- `experiments` — A/B tests with control/test values, sample counts, p_value, effect_size, decision
- `learning_history` — log of what changed, when, why, with old/new values
- `decision_audit` — every Claude decision with reasoning, confidence, key factors
- `working_memory` — daemon state including recent decisions, active patterns, reflections

---

## What's Actually Running

### Working
1. **Trade outcome feedback** — when a position closes, `daemon._record_trade_outcome()` calls `learning.record_trade_outcome()`, which updates experiment tracking via `orchestrator.on_trade_closed()`
2. **Decision auditing** — every Claude reasoning call is logged to `decision_audit` with full reasoning chain
3. **Working memory assembly** — `assemble_context()` pulls active patterns and experiments into Claude's reasoning context (so Claude *can* see them, if any existed)

### Not Running
1. **Weekly learning cycle** — `WEEKLY_LEARNING` event type exists in the EventBus enum but is **never emitted**. No scheduler creates it.
2. **EOD reflection** — `EOD_REFLECTION` event is emitted daily at 4:30 PM ET, but it flows through the normal reasoning pipeline (producing MONITOR_ONLY). It **does not** call `learning_loop.run_eod_reflection()` which would have Claude reflect on today's decisions.
3. **Pattern detection** — never triggered automatically. Must be called manually.
4. **Experiment evaluation** — experiments accumulate trade data (via outcome feedback), but nobody calls `evaluate_experiment()` to check results.
5. **Parameter optimization** — proposals are never generated because the orchestrator never runs.

---

## The Intended Flow (If Activated)

```
Trades close throughout the week
    |
    v
daemon._record_trade_outcome()
    |-- Updates working memory with outcome
    |-- Tracks experiment group assignment
    v
[Friday 4 PM or weekly trigger]
    |
    v
learning_orchestrator.run_weekly_analysis()
    |
    |-- Step 1: pattern_detector.detect_patterns()
    |           Scans all trades across 23+ dimensions
    |           Finds: "PUT sells on stocks with RSI < 30 have 92% win rate vs 78% baseline"
    |
    |-- Step 2: statistical_validator.validate_pattern()
    |           Checks p-value, effect size, cross-validation
    |           Rejects patterns that are noise
    |
    |-- Step 3: experiment_engine.evaluate_experiment()
    |           Checks active A/B tests
    |           Decision: ADOPT (use new param), REJECT (revert), CONTINUE (need more data)
    |
    |-- Step 4: parameter_optimizer.propose_changes()
    |           Converts validated patterns -> config proposals
    |           "Lower delta target from -0.20 to -0.15 when VIX > 25"
    |
    |-- Step 5: Auto-apply if confidence >= 0.90
    |           Or flag for human review if < 0.90
    v
Results stored in: patterns, experiments, learning_history tables
    |
    v
[Optional] performance_analyzer.analyze()
    |-- Claude generates narrative insights
    |-- New hypotheses proposed
    |-- Risks and contradictions flagged
    v
Working memory updated with new patterns + experiment results
    |
    v
Next week's Claude reasoning sees updated patterns in context
    |-- "Active pattern: RSI < 30 entries have +14% higher win rate (p=0.02, n=45)"
    |-- Claude factors this into STAGE_CANDIDATES and EXECUTE_TRADES decisions
```

---

## Activation Status: COMPLETE (2026-03-13)

All three activation steps have been implemented:

### 1. WEEKLY_LEARNING emission — DONE
- Emits every Friday at 17:00 ET (configurable via dashboard: `weekly_learning_day`, `weekly_learning_hour`)
- Added to `_time_based_emitter()` in daemon.py

### 2. WEEKLY_LEARNING handler — DONE
- Routes to `learning.run_weekly_learning()` bypassing Claude reasoning pipeline
- Runs 5-step orchestrator (detect → validate → evaluate → propose → auto-apply)
- Includes Claude-powered hypothesis generation via `PerformanceAnalyzer`
- Auto-apply threshold set to 80% (configurable via dashboard)

### 3. EOD_REFLECTION handler — DONE
- Routes to `learning.run_eod_reflection()` instead of generic reasoning
- Runs daily by default (configurable: `eod_reflection_frequency` = daily/weekly)
- Can be disabled via dashboard toggle

### 4. Dashboard integration — DONE
- New `/learning` page shows patterns, hypotheses, experiments, reflections, timeline
- Config page expanded with all learning parameters (auto-apply threshold, reflection frequency, hypothesis toggle, etc.)
- "Learning" nav link added to main dashboard

### Configuration (all dashboard-editable)
- `auto_apply_threshold`: 0.80 (was 0.90)
- `eod_reflection_enabled`: true
- `eod_reflection_frequency`: daily
- `hypothesis_generation_enabled`: true
- `weekly_learning_day`: Friday
- `weekly_learning_hour`: 17

---

## Expected Results When Active

### Short-term (first month)
- EOD reflections capture decision quality daily
- Pattern detector identifies strongest signals (if enough trade history)
- Most patterns likely rejected by statistical validator (insufficient samples)
- Working memory starts accumulating reflection reports

### Medium-term (2-3 months, ~60-100+ trades)
- First validated patterns emerge (likely delta range, VIX regime, DTE preferences)
- First A/B experiment proposed (e.g., "test -0.15 delta vs current -0.20 delta")
- Claude reasoning starts incorporating pattern data into decisions
- Parameter proposals generated but likely need human review (< 90% confidence)

### Long-term (6+ months, 200+ trades)
- Multiple validated patterns with high confidence
- Completed experiments with clear ADOPT/REJECT decisions
- Auto-applied parameter changes for highest-confidence findings
- Measurable improvement in win rate or ROI vs baseline period
- Claude's reasoning quality improves as working memory gets richer

### What Success Looks Like
- Win rate trending up over baseline (currently ~78% target)
- Fewer losses from avoidable patterns (e.g., entering before earnings, wrong VIX regime)
- Parameter drift: delta, DTE, premium targets evolve based on evidence
- Experiment log shows clear decisions (not endless CONTINUE)

---

## Cost Considerations

| Activity | Frequency | Model | Est. Cost |
|----------|-----------|-------|-----------|
| EOD Reflection | Daily | Sonnet | ~$0.10-0.30/day |
| Weekly Learning (orchestrator) | Weekly | None (pure Python) | $0 |
| Weekly Analysis (Claude insights) | Weekly | Sonnet/Opus | ~$0.50-2.00/week |
| Trade outcome recording | Per close | None | $0 |

The learning orchestrator (pattern detection, validation, experiments, optimization) is pure Python — no API costs. Only the Claude-powered analysis and reflection steps cost money. Total: roughly **$3-5/week** additional.

---

## Open Questions for Review

1. **How many closed trades exist?** If < 30, should we defer activation until we have enough data?
2. **Weekly learning trigger timing?** Friday after close vs Sunday evening vs Monday pre-market?
3. **Auto-apply threshold** — 90% confidence is very conservative. Lower to 80% for faster adaptation, or keep 90% for safety?
4. **EOD reflection value** — is the $0.10-0.30/day Claude cost worth it for daily decision review? Could start with weekly reflection only.
5. **Should Claude generate hypotheses?** The Performance Analyzer can propose new experiments. Enable this, or keep experiments manual?
6. **Human approval workflow** — parameter changes below 90% confidence need approval. How should this surface? Dashboard notification? Email? CLI prompt?

---

## Files Reference

| Path | Purpose |
|------|---------|
| `src/learning/learning_orchestrator.py` | Main 5-step weekly cycle |
| `src/learning/pattern_detector.py` | 23-dimension pattern scanning |
| `src/learning/statistical_validator.py` | 4-layer validation |
| `src/learning/experiment_engine.py` | A/B testing framework |
| `src/learning/parameter_optimizer.py` | Config change proposals |
| `src/agentic/learning_loop.py` | Daemon integration layer |
| `src/agentic/daemon.py` | Event processing (needs wiring) |
| `src/agentic/event_bus.py` | WEEKLY_LEARNING event (not emitted) |
| `src/agents/performance_analyzer.py` | Claude-powered insights |
| `src/agentic/working_memory.py` | Context assembly for Claude |
