# Log Analysis: US Trading Day March 2, 2026

**Log dates:** March 3 AEST (06:08 - 10:03+)
**US Market hours:** 09:30 - 16:00 ET (March 2)
**Analysed:** March 4, 2026

---

## Summary

The daemon was **restarted 8+ times** during US market hours and suffered from **three code bugs** that prevented trade execution until 9 minutes before market close. 4 out of 12 orders filled but **none were saved to the database** due to a UniqueViolation crash.

---

## Timeline

| AEST Time | ET Time | Event |
|---|---|---|
| 06:08 | 14:08 | Daemon start #1. AI → CLOSE_POSITION. Human approved but exits failed: "Position not found" |
| 06:19 | 14:19 | Daemon start #2. Execution gate **blocked** EXECUTE_TRADES |
| 06:28 | 14:28 | Daemon start #3. Exit attempted but **BUG**: `_record_fill_in_db() takes 4 args but 5 given` |
| 06:40 | 14:40 | Daemon start #4. AI → EXECUTE_TRADES, escalated to human |
| 07:31 | 15:31 | Human approved. **BUG**: `'NoneType' object has no attribute 'execute_all'` — RapidFireExecutor was None |
| 07:37 | 15:37 | Another attempt. **BUG**: `RapidFireExecutor.__init__() missing 1 required positional argument: 'adaptive_executor'` |
| **07:51** | **15:51** | Finally executed! 12 orders placed via RapidFire (only 9 min before close) |
| 07:51-07:59 | 15:51-15:59 | **4 fills**: RKLB @ $0.36, CRWV @ $0.65, ALAB @ $0.74, OKLO @ $0.33 |
| 08:00 | 16:00 | Market closed. 8 unfilled orders entered "Market closed" retry loop |
| 08:00-08:02 | 16:00-16:02 | **RKLB retry storm**: 60+ failed price modifications in 2 min |
| **08:01:59** | **16:01:59** | **DAEMON CRASH**: `UniqueViolation` on `IREN_35.0_20260306_P` — all 12 pending trade records lost |
| 10:03 | 18:03 | Post-close restart. Position mismatches: OKLO (DB=4, IBKR=8), RKLB (DB=4, IBKR=8) |

---

## Critical Issues

### 1. Three Code Bugs Blocked Execution for 1h43m (06:08 → 07:51)

- **`ExitManager._record_fill_in_db()`** — signature mismatch (4 positional args but 5 given)
  - File: `src/execution/exit_manager.py:571`
- **`RapidFireExecutor` was `None`** when `execute_all` was called
  - File: `src/agentic/daemon.py:952`
  - Root cause: executor not initialized in daemon
- **`RapidFireExecutor.__init__()`** missing `adaptive_executor` argument
  - File: `src/agentic/daemon.py:952`
  - Root cause: constructor signature changed but caller not updated

### 2. DB Crash — UniqueViolation (All Fills Lost)

- `_save_pending_trades_to_db` bulk-inserts ALL 12 candidates in one transaction
- `IREN_35.0_20260306_P` already existed from a prior session
- The entire INSERT rolled back, including 4 actual fills (RKLB, CRWV, ALAB, OKLO)
- File: `src/services/two_tier_execution_scheduler.py:1056`
- **Fix needed**: Use `INSERT ... ON CONFLICT DO UPDATE` (upsert) or save individually with error handling

### 3. RKLB Infinite Retry Loop After Market Close

- `_monitor_and_adjust` loop doesn't check market hours
- After close: bid=0, so it cancels order, tries to reprice, gets "Market closed", repeats
- 60+ iterations in ~2 minutes before 10-minute timer expired
- File: `src/services/rapid_fire_executor.py:716`
- **Fix needed**: Break the monitoring loop when market closes or when bid=0

### 4. Late Execution Timing

- Orders not placed until 15:51 ET — only 9 minutes before close
- Caused by 8 daemon restarts and 3 code bugs blocking execution
- Only 4/12 orders filled in the narrow window

### 5. Position Mismatches Post-Crash

- OKLO: DB=4, IBKR=8 (4 new fills not recorded)
- RKLB: DB=4, IBKR=8 (4 new fills not recorded)
- These orphan positions need manual reconciliation

---

## Fills That Were Lost

| Symbol | Strike | Expiry | Fill Price | Contracts | Status |
|---|---|---|---|---|---|
| RKLB | $59.0 | 2026-03-06 | $0.36 | 4 | Filled but NOT in DB |
| CRWV | ? | 2026-03-06 | $0.65 | 4 | Filled but NOT in DB |
| ALAB | $105.0 | 2026-03-06 | $0.74 | 2 | Filled but NOT in DB |
| OKLO | ? | 2026-03-06 | $0.33 | 4 | Filled but NOT in DB |

---

## Other Warnings

- 7 orphan orders found during reconciliation (NBIS, IREN x2, ALAB x2, KTOS, NBIS)
- ALAB assignment detected (500 shares, avg cost $148.85)
- ALAB_150.0_20260313_C in DB but not in IBKR (ghost position)
- IBKR "Peer closed connection" at 10:46 AEST
- IBKR "Sec-def data farm connection is broken" at 15:45 AEST
