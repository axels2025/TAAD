# Future Ideas & Notes

**Use this file for quick brainstorming and rough notes. Move to ROADMAP.md when ready to plan.**

---

## Quick Capture

### [Idea Name] - [Date]

**Quick Thought:**
[What's the idea?]

**Why:**
[What problem would it solve?]

**Next Step:**
[Research? Test? Add to roadmap?]

---

## Resolved Items (Kept for Reference)

### ✅ Flex Queries for EOD Sync - 2026-02-07 (Resolved)
Resolved by TAAD system — full Flex Query integration implemented.

### ✅ IBKR Execution History Limitation (Resolved)
TAAD with Flex Queries retrieves fills beyond 24h.

### ✅ Imported Position Entry Premium Accuracy (Resolved)
Reconciliation now uses `reqExecutions()` for actual fill prices.

### ✅ Database Schema Missing Commission Field (Resolved)
`commission` column added to Trade model, populated from fills.

---

### Same-Day Round-Trip Trade Validation - 2026-02-16

**Quick Thought:**
Add specific validation in reconciliation for same-day round-trip trades (open + close on same day).

**Why:**
- If a manual same-day round-trip occurs, reconcile may miss it entirely
- Infrastructure exists (`reqExecutions`, `reqCompletedOrders`, TAAD trade matcher) but no dedicated logic
- Current workaround: "don't do same-day manual trades"

**Next Step:**
Enhance reconcile to detect same-day STO/BTC pairs from executions, or rely on nightly TAAD Flex Query sync to catch them retroactively.

---

### Hardcoded min_samples in Experiment.is_complete() - 2026-02-16

**Quick Thought:**
`Experiment.is_complete()` in `src/data/models.py` has `min_samples = 30` hardcoded instead of reading from config.

**Why:**
- Most other config values are now env-var driven
- This one was missed during the configuration cleanup
- Minor: only affects when experiments are considered complete

**Next Step:**
Move to `MIN_TRADES_FOR_LEARNING` env var or a dedicated experiment config.

---

### Multi-Contract Position Edge Case Tests - 2026-02-16

**Quick Thought:**
Reconciliation and position handling lack dedicated tests for multi-contract edge cases.

**Why:**
- Reconciliation tests exist and cover basic scenarios
- But multi-contract partial fills, partial closes, and quantity mismatches need more targeted coverage
- Paper trading rarely produces partial fills, so these paths are under-tested

**Next Step:**
Add test cases for: partial fill reconciliation, multi-leg position close, quantity mismatch with partial BTC.

---

### Commission Impact Analysis - 2026-02-07

**Quick Thought:**
Learning engine should analyze commission impact on profitability

**Why:**
- Commission field now exists on Trade model (resolved 2026-02-16)
- Learning engine should use net P/L instead of gross P/L
- Small premium trades might not be worth it after commissions

**Next Step:**
Update learning engine P/L calculations to use net-of-commission figures. Add commission-aware pattern detection.

---

## Scratchpad

[Use this space for random thoughts, observations, or questions that don't fit elsewhere]

- Check if IBKR has webhook API for real-time fills
- Research other trading platforms' APIs for comparison
- Consider building a simple web dashboard for monitoring
- Look into automated backup solution for database
