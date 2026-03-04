# Pivot to MCP: From "Claude as Consultant" to "Claude as Pilot"

**Date:** 2026-03-03
**Status:** Research / Exploration
**Author:** Claude Code analysis of existing codebase + MCP SDK research

---

## Executive Summary

This document explores pivoting the trading system from its current architecture — where a Python daemon drives a linear event loop and calls Claude as a "consultant" — to an **MCP-based agentic architecture** where Claude autonomously drives the trading workflow by calling tools exposed via the Model Context Protocol.

**The key inversion:** control flow moves from Python code to Claude's reasoning. Claude decides what data to fetch, when to scan, which strikes to evaluate, and when to execute — all within hardcoded risk guardrails enforced at the tool level.

---

## Table of Contents

1. [Current Architecture](#1-current-architecture)
2. [Target Architecture](#2-target-architecture-mcp)
3. [What is MCP?](#3-what-is-mcp)
4. [Existing Functions → MCP Tools](#4-existing-functions--mcp-tools)
5. [MCP Server Design](#5-mcp-server-design)
6. [The Agentic Loop](#6-the-agentic-loop)
7. [Example Claude-Driven Session](#7-example-claude-driven-session)
8. [Critical Design Decisions](#8-critical-design-decisions)
9. [What We Keep vs What Changes](#9-what-we-keep-vs-what-changes)
10. [Migration Path](#10-migration-path)
11. [Appendix: MCP SDK Reference](#appendix-mcp-sdk-reference)

---

## 1. Current Architecture

### The Event-Driven Daemon Loop

The `TAADDaemon.run()` implements an 8-step event processing pipeline:

```
Event Stream (from EventBus, priority-ordered)
    │
    ├── 1. Mark Event as Processing (atomic DB claim)
    ├── 2. Assemble Context (from DB + WorkingMemory)
    ├── 3. Enrich Context (live IBKR data, staged candidates)
    ├── 4. Context Validation (guardrails)
    ├── 5. Reason with Claude (if context passes guardrails)
    ├── 6. Output Validation (guardrails)
    ├── 7. Execute Action (via ActionExecutor → existing CLI functions)
    └── 8. Update Memory & Mark Complete
```

### How Claude Fits Today

- **Called every 15 minutes** via `SCHEDULED_CHECK` events during market hours
- **Receives a single JSON blob** (`ReasoningContext`) containing open positions, market data, staged candidates, recent decisions, and patterns
- **Returns a single action enum** (`DecisionOutput`) — one of: `MONITOR_ONLY`, `STAGE_CANDIDATES`, `EXECUTE_TRADES`, `CLOSE_POSITION`, `ADJUST_PARAMETERS`, `RUN_EXPERIMENT`, `REQUEST_HUMAN_REVIEW`, `EMERGENCY_STOP`
- **Controls nothing** — the daemon decides when to ask, what context to provide, and how to execute the chosen action

### Limitations of This Model

1. **Claude sees everything at once** — one massive context blob, not iterative exploration
2. **Claude gets one shot** — returns a single action, cannot reason in steps
3. **Claude cannot drill down** — if it sees a position with unknown P/L, it cannot fetch a fresh quote
4. **Context is pre-assembled** — the daemon decides what's relevant, not Claude
5. **No iterative refinement** — Claude cannot say "scan with tighter delta" after seeing initial results

---

## 2. Target Architecture (MCP)

### Claude-Driven Agentic Loop

```
Scheduler (triggers sessions at market events)
    │
    └── Agentic Loop (Claude controls the flow)
            │
            ├── Claude calls get_market_conditions()
            ├── Claude calls scan_for_options(delta_max=0.15, dte_min=7)
            ├── Claude calls get_option_chain("AAPL", dte_max=14)
            ├── Claude calls check_risk_limits(symbol="AAPL", proposed_margin=4300)
            ├── Claude calls place_naked_put("AAPL", 215.0, ..., dry_run=True)
            ├── Claude calls get_open_positions()
            ├── Claude calls get_position_pnl("trade-xyz")
            ├── Claude calls close_position("trade-xyz", reason="approaching target")
            └── Claude produces final summary with reasoning
```

### How Claude Fits in the New Model

- **Called at key market events** (open, close, every 15 min) with a task prompt
- **Fetches its own context** by calling read-only tools as needed
- **Iterates in steps** — can scan, drill down, compare, then decide
- **Executes directly** via execution tools (gated by hardcoded risk checks inside the tools)
- **Full reasoning visible** in the conversation trace (natural language, not enum codes)

---

## 3. What is MCP?

The **Model Context Protocol** is an open protocol created by Anthropic that standardizes how AI applications connect to external tools and data sources. It uses JSON-RPC 2.0 over stdio (local) or Streamable HTTP (remote).

### Architecture

```
MCP Host (Claude Desktop, Claude Code, or custom Python app)
  ├── MCP Client 1 ──→ MCP Server: TradingSystem (our server)
  ├── MCP Client 2 ──→ MCP Server: Database (optional)
  └── MCP Client 3 ──→ MCP Server: Notifications (optional)
```

### Three Primitives

| Primitive | Controlled By | Side Effects | Use Case |
|-----------|--------------|--------------|----------|
| **Tools** | Model (Claude decides when to call) | Yes | Execute trade, scan market, close position |
| **Resources** | Application (loaded as context) | No (read-only) | Trading config, risk limits, DB schema |
| **Prompts** | User (via slash commands) | No | "Daily review", "EOD reflection" |

### Python SDK

```bash
pip install "mcp[cli]"
```

The official SDK provides **FastMCP** — a decorator-based framework:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("TradingSystem")

@mcp.tool()
async def get_open_positions() -> str:
    """Get all open naked put positions with P/L."""
    # Implementation calls existing code
    ...

@mcp.resource("trading://config")
def get_trading_config() -> str:
    """Current strategy parameters and risk limits (read-only)."""
    ...

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Type hints and docstrings are automatically converted to JSON Schema for tool input definitions — no manual schema writing required.

---

## 4. Existing Functions → MCP Tools

### Tier 1: Market Intelligence (Read-Only, Low Risk)

| MCP Tool | Current Function | Source Module |
|---|---|---|
| `get_market_conditions` | `MarketConditionMonitor.check_conditions()` | `services/market_conditions.py` |
| `scan_for_options` | `run_scan_and_persist()` + `run_auto_select_pipeline()` | `services/ibkr_scanner.py`, `auto_select_pipeline.py` |
| `get_option_chain` | `get_chain_with_greeks()` | `nakedtrader/chain.py` |
| `get_quote` | `IBKRClient.get_quote()` | `tools/ibkr_client.py` |
| `get_open_positions` | `WorkingMemory.assemble_context().open_positions` | `agentic/working_memory.py` |
| `get_position_pnl` | `_enrich_position_pnl()` logic | `agentic/daemon.py` |
| `get_trade_history` | DB query on `trades` table | `data/repositories.py` |

### Tier 2: Analysis & Decision Support (Computed, No Side Effects)

| MCP Tool | Current Function | Source Module |
|---|---|---|
| `evaluate_exit` | `ExitManager.evaluate_exits()` | `execution/exit_manager.py` |
| `check_risk_limits` | `RiskGovernor.pre_trade_check()` | `execution/risk_governor.py` |
| `check_margin` | `RiskGovernor.check_post_trade_margin()` | `execution/risk_governor.py` |
| `select_best_strike` | `select_strike(chain, config)` | `nakedtrader/strike_selector.py` |
| `get_performance_summary` | Trade P&L aggregation | `data/repositories.py` |

### Tier 3: Execution (Side Effects, Gated by Risk Governor)

| MCP Tool | Current Function | Source Module |
|---|---|---|
| `place_naked_put` | `OrderManager.place_bracket_order()` | `nakedtrader/order_manager.py` |
| `close_position` | `ExitManager.execute_exit()` | `execution/exit_manager.py` |
| `emergency_stop` | `KillSwitch.halt()` | (existing) |

---

## 5. MCP Server Design

### File Structure

```
src/mcp/
├── __init__.py
├── server.py              ← FastMCP server entry point
├── tools/
│   ├── __init__.py
│   ├── market.py          ← scan_for_options, get_market_conditions, get_quote
│   ├── chain.py           ← get_option_chain, select_best_strike
│   ├── positions.py       ← get_open_positions, get_position_pnl, get_trade_history
│   ├── execution.py       ← place_naked_put, close_position (risk-gated)
│   └── risk.py            ← check_risk_limits, check_margin, emergency_stop
├── resources/
│   ├── __init__.py
│   └── config.py          ← trading config, risk params (read-only context)
└── guards.py              ← Pre-execution validation (wraps RiskGovernor)
```

### Tool Definitions (Key Examples)

#### scan_for_options

```python
@mcp.tool()
async def scan_for_options(
    delta_min: float = 0.05,
    delta_max: float = 0.15,
    dte_min: int = 7,
    dte_max: int = 14,
    min_premium: float = 0.30,
    max_results: int = 20,
) -> str:
    """Scan IBKR for naked put candidates matching the given criteria.

    Returns ranked opportunities with symbol, strike, delta, premium,
    OTM%, and margin requirement. Results are sorted by portfolio score.

    Args:
        delta_min: Minimum delta (absolute value), e.g. 0.05
        delta_max: Maximum delta (absolute value), e.g. 0.15
        dte_min: Minimum days to expiration
        dte_max: Maximum days to expiration
        min_premium: Minimum acceptable premium in dollars
        max_results: Maximum number of results to return
    """
    # Internally calls:
    #   1. ibkr_scanner.run_scan_and_persist(preset="naked-put")
    #   2. auto_select_pipeline.run_auto_select_pipeline(scan_id)
    #   3. Filters by delta/DTE/premium criteria
    #   4. Returns JSON with ranked results
    ...
```

#### get_option_chain

```python
@mcp.tool()
async def get_option_chain(
    symbol: str,
    dte_min: int = 0,
    dte_max: int = 14,
) -> str:
    """Get the full put option chain for a symbol with Greeks.

    Returns all put strikes with delta, IV, bid/ask/mid, theta,
    gamma, and OTM% for each valid expiration.

    Args:
        symbol: Underlying ticker (e.g. "AAPL", "MSFT", "XSP")
        dte_min: Minimum DTE to include
        dte_max: Maximum DTE to include
    """
    # Internally calls:
    #   1. chain.get_valid_expirations(client, symbol, config)
    #   2. chain.get_chain_with_greeks(client, symbol, exp, config)
    #   3. Returns JSON with all strikes and Greeks
    ...
```

#### place_naked_put (Risk-Gated)

```python
@mcp.tool()
async def place_naked_put(
    symbol: str,
    strike: float,
    expiration: str,
    contracts: int,
    limit_price: float,
    profit_target_pct: float = 50.0,
    stop_loss_pct: float = 200.0,
    dry_run: bool = True,
) -> str:
    """Place a naked put sell order with bracket exits (profit target + stop loss).

    IMPORTANT: This tool places a real trade when dry_run=False.
    Risk limits are enforced internally and cannot be bypassed.
    Always call with dry_run=True first to simulate.

    Args:
        symbol: Underlying ticker
        strike: Put strike price
        expiration: Expiration date (YYYYMMDD format)
        contracts: Number of contracts to sell
        limit_price: Limit price for the sell order
        profit_target_pct: Close at this % of max profit (default 50%)
        stop_loss_pct: Close at this % loss (default 200%)
        dry_run: If True, simulate only — do not place real orders
    """
    # HARDCODED RISK ENFORCEMENT (inside the tool, not in the prompt):
    #   1. RiskGovernor.pre_trade_check(opportunity)
    #   2. RiskGovernor.check_post_trade_margin(opportunity)
    #   3. If any limit fails → return error, do NOT place order
    #   4. If dry_run=True → return simulated result
    #   5. If dry_run=False → OrderManager.place_bracket_order(...)
    ...
```

#### close_position

```python
@mcp.tool()
async def close_position(
    trade_id: str,
    reason: str,
    order_type: str = "limit",
    limit_price: float | None = None,
) -> str:
    """Close an open position by placing a buy-to-close order.

    Args:
        trade_id: The trade identifier to close
        reason: Why you're closing (logged for learning/audit)
        order_type: "limit" or "market"
        limit_price: Required if order_type is "limit"
    """
    # Internally calls:
    #   1. ExitManager.execute_exit(position_id, decision)
    #   2. Records exit reason in trade DB
    ...
```

#### get_market_conditions

```python
@mcp.tool()
async def get_market_conditions() -> str:
    """Get current market conditions: VIX, SPY price, and whether
    conditions are favorable for selling puts.

    Returns VIX level, SPY price (US market only), bid-ask spread
    quality, and a favorable/unfavorable assessment with reasoning.
    """
    # Internally calls:
    #   MarketConditionMonitor.check_conditions()
    ...
```

#### get_open_positions

```python
@mcp.tool()
async def get_open_positions() -> str:
    """Get all currently open naked put positions.

    Returns each position's symbol, strike, expiry, entry premium,
    current mid price, unrealized P/L, P/L%, DTE, and days held.
    """
    # Internally calls:
    #   1. DB query for open trades
    #   2. Live quote enrichment for P/L
    ...
```

#### check_risk_limits

```python
@mcp.tool()
async def check_risk_limits(
    symbol: str = "",
    proposed_margin: float = 0.0,
) -> str:
    """Check current risk utilization and whether a proposed trade
    would violate any limits.

    Returns: daily P/L, open position count, sector concentration,
    margin utilization, and pass/fail for each risk limit.

    Args:
        symbol: Symbol for sector concentration check (optional)
        proposed_margin: Proposed additional margin for the new trade
    """
    # Internally calls:
    #   RiskGovernor.pre_trade_check(opportunity)
    #   RiskGovernor.check_post_trade_margin(opportunity)
    ...
```

### Resources (Read-Only Context)

```python
@mcp.resource("trading://config")
def get_trading_config() -> str:
    """Current strategy parameters: delta range, DTE range, premium
    minimums, profit targets, stop losses, max positions, etc."""
    ...

@mcp.resource("trading://risk-limits")
def get_risk_limits() -> str:
    """Hardcoded risk limits (cannot be overridden):
    - Max daily loss: -2%
    - Max positions: 10
    - Max margin utilization: 80%
    - Max sector concentration: 30%
    - Max weekly loss: -5%
    - Max drawdown: -10%
    """
    ...
```

---

## 6. The Agentic Loop

### How It Works

The agentic loop is a `while` loop where Claude calls tools iteratively until it has gathered enough information and completed its task:

```python
"""src/mcp/pilot.py — The agentic loop that lets Claude drive."""

import anthropic

SYSTEM_PROMPT = """
You are an autonomous naked put options trader. You have access to tools
that let you scan markets, analyze options, check risk, and execute trades.

## Your Strategy
- Sell naked puts on liquid US equities
- Target delta: 0.05 to 0.15 (far OTM)
- Target DTE: 7 to 14 days
- Minimum premium: $0.30
- Profit target: 50% of max profit
- Stop loss: 200% of premium received

## Your Workflow
1. Check market conditions (VIX, SPY trend)
2. If favorable, scan for candidates
3. For promising candidates, pull the full option chain
4. Verify risk limits before any trade
5. Place orders (always dry_run=True first, then confirm)
6. Monitor open positions
7. Close positions that hit targets or show adverse movement

## Risk Rules (HARDCODED — you cannot override these)
- Max 10 concurrent positions
- Max 80% margin utilization
- Max -2% daily loss (circuit breaker)
- Max 30% in any single sector
- ALWAYS call check_risk_limits() before placing any trade
- ALWAYS use dry_run=True first, then confirm with the human

## When to Escalate to Human
- VIX > 30 (extreme volatility)
- Any single position loss > $500
- Unusual market conditions
- Margin utilization > 70%
"""


def run_trading_session(task_prompt: str):
    """Run one agentic session where Claude drives the workflow.

    Args:
        task_prompt: What Claude should focus on this session.
            Examples:
            - "Market is open. Scan for new candidates."
            - "Monitor open positions and manage exits."
            - "Market closing. Run EOD review."
    """
    client = anthropic.Anthropic()
    tools = discover_tools_from_mcp_server()  # MCP tools/list

    messages = [{"role": "user", "content": task_prompt}]

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            # Claude wants to call tools — execute them via MCP
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = call_mcp_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            # Claude is done — return final summary
            return response.content[0].text
```

### Scheduler (Replaces the Daemon Event Loop)

```python
"""src/mcp/scheduler.py — Triggers agentic sessions at market events."""

import schedule

def start_scheduler():
    # Market open: scan for new trades
    schedule.every().day.at("09:30").do(
        run_trading_session,
        task_prompt="Market just opened. Check conditions and scan for candidates."
    )

    # Every 15 minutes: monitor positions
    schedule.every(15).minutes.do(
        run_trading_session,
        task_prompt="Monitor all open positions. Close any that hit targets."
    )

    # Market close: EOD review
    schedule.every().day.at("16:00").do(
        run_trading_session,
        task_prompt="Market closing. Close expiring positions. Run EOD summary."
    )

    while True:
        schedule.run_pending()
        time.sleep(10)
```

---

## 7. Example Claude-Driven Session

What a market-open session looks like with Claude as pilot:

```
Human: Market just opened. Check conditions and scan for candidates.

Claude: Let me start by checking market conditions.
  → calls get_market_conditions()
  ← {"vix": 14.2, "spy_price": 582.30, "favorable": true,
      "reason": "VIX low (14.2), spreads tight ($0.032)"}

Claude: VIX at 14.2 — favorable environment. Let me scan for candidates.
  → calls scan_for_options(delta_min=0.05, delta_max=0.15,
                           dte_min=7, dte_max=14)
  ← {"candidates": [
       {"symbol": "AAPL", "strike": 215, "delta": 0.08,
        "premium": 0.45, "otm_pct": 7.2, "margin": 4300},
       {"symbol": "MSFT", "strike": 400, "delta": 0.11,
        "premium": 0.52, "otm_pct": 5.8, "margin": 6000},
       ...12 total candidates
     ]}

Claude: AAPL and MSFT look promising. Let me examine the full chains.
  → calls get_option_chain("AAPL", dte_min=7, dte_max=14)
  ← {"symbol": "AAPL", "underlying_price": 231.50,
      "expirations": [
        {"exp": "20260310", "dte": 7, "strikes": [
          {"strike": 210, "delta": 0.05, "mid": 0.32, "iv": 0.22},
          {"strike": 215, "delta": 0.08, "mid": 0.45, "iv": 0.24},
          {"strike": 220, "delta": 0.12, "mid": 0.68, "iv": 0.26}
        ]}
      ]}

Claude: The 215P at delta 0.08 is the sweet spot — 7.1% OTM, $0.45
  premium, reasonable IV. Before trading, let me verify risk.
  → calls check_risk_limits(symbol="AAPL", proposed_margin=4300)
  ← {"approved": true, "positions": "4/10", "margin_util": "52%",
      "sector_TECH": "18%", "daily_pnl": "+$125",
      "all_limits_pass": true}

Claude: All risk limits pass. Let me simulate the trade first.
  → calls place_naked_put("AAPL", 215.0, "20260310", 5, 0.45,
                           profit_target_pct=50, dry_run=True)
  ← {"dry_run": true, "status": "SIMULATED",
      "order": "SELL 5x AAPL 215P Mar10 @ $0.45",
      "premium_received": 225.00, "margin_required": 4300,
      "profit_target": "BTC @ $0.225", "stop_loss": "BTC @ $1.35"}

Claude: Simulation looks good. Now let me check existing positions.
  → calls get_open_positions()
  ← {"positions": [
       {"trade_id": "XSP-540P-0303", "symbol": "XSP", "strike": 540,
        "entry_premium": 0.85, "current_mid": 0.52, "pnl_pct": 38.8,
        "dte": 4},
       {"trade_id": "GOOGL-170P-0228", "symbol": "GOOGL", "strike": 170,
        "entry_premium": 0.40, "current_mid": 0.35, "pnl_pct": 12.5,
        "dte": 11},
       ...4 positions total
     ]}

Claude: XSP 540P is at +38.8% — approaching the 50% profit target
  with only 4 DTE. I'll let it decay further. GOOGL is healthy at +12.5%.

  ═══ SESSION SUMMARY ═══
  Market: Favorable (VIX 14.2, SPY $582.30)
  New Candidate: AAPL 215P Mar10 — simulated successfully
    → Premium: $225, Margin: $4,300, Delta: 0.08
    → Awaiting human confirmation to execute
  Open Positions: 4 total, all healthy
    → XSP 540P: +38.8% (approaching target)
    → GOOGL 170P: +12.5% (on track)
    → AMZN 185P: +22.1% (on track)
    → META 550P: +5.3% (early)
  Risk: 52% margin, 4/10 positions, all limits pass
```

---

## 8. Critical Design Decisions

### 8.1 Where Does the Loop Run?

| Option | How It Works | Pros | Cons |
|---|---|---|---|
| **A) Claude Desktop / Claude Code** | Register MCP server, chat naturally | Zero loop code, interactive | Manual — you type prompts |
| **B) Custom Python host** | Your code runs the agentic loop | Fully automated, schedulable | You build the loop + cost tracking |
| **C) Claude Agent SDK** | `claude_agent_sdk.Agent()` | Built-in loop, tool routing | Newer SDK, less battle-tested |

**Recommendation:** Start with **A** (Claude Code) to test tools interactively, then graduate to **B** for the fully automated 15-minute loop.

### 8.2 Risk Enforcement: Trust Claude or Enforce in Code?

**Never trust the model for risk enforcement.** MCP tools that execute trades must have hardcoded guards *inside the tool implementation*, not just instructions in the system prompt.

Claude might hallucinate risk checks or skip them under edge cases. The `RiskGovernor.pre_trade_check()` runs *inside* `place_naked_put()` — if limits are breached, the tool returns an error, not an order.

This is identical to the current pattern where `ActionExecutor._handle_execute()` calls `risk_governor.pre_trade_check()` before executing.

```python
@mcp.tool()
async def place_naked_put(..., dry_run: bool = True) -> str:
    # Step 1: HARDCODED risk check (cannot be bypassed by Claude)
    risk_check = risk_governor.pre_trade_check(opportunity)
    if not risk_check.approved:
        return json.dumps({
            "error": "RISK_LIMIT_VIOLATED",
            "limit": risk_check.limit_name,
            "current": risk_check.current_value,
            "max": risk_check.limit_value,
        })

    # Step 2: Margin check
    margin_check = risk_governor.check_post_trade_margin(opportunity)
    if not margin_check.is_healthy:
        return json.dumps({"error": "MARGIN_UNHEALTHY", ...})

    # Step 3: Execute only if all checks pass
    if dry_run:
        return json.dumps({"dry_run": True, "status": "SIMULATED", ...})
    else:
        result = order_manager.place_bracket_order(...)
        return json.dumps({"status": "FILLED", ...})
```

### 8.3 What Happens to the Daemon?

The daemon doesn't disappear — it changes roles:

| Current Daemon Role | MCP Equivalent |
|---|---|
| Event emitter (timers) | External scheduler triggers agentic sessions |
| Context assembly (`_assemble_context`) | Claude calls tools to gather context on demand |
| Claude reasoning call (`reason()`) | Claude IS the loop — no separate "reasoning" call |
| Action execution (`ActionExecutor`) | Claude calls execution tools directly |
| Guardrails (pre/post Claude) | Hardcoded inside MCP tool implementations |
| Working memory persistence | MCP Resources + conversation history |

### 8.4 The `dry_run` Gate

For safety, execution tools use a two-phase pattern:

```
Phase 1: Claude calls place_naked_put(..., dry_run=True)
         → Tool returns simulated result, no real order

Phase 2: Human confirms (or auto-confirm at high autonomy level)

Phase 3: Claude calls place_naked_put(..., dry_run=False)
         → Tool executes, RiskGovernor enforces limits internally
```

This mirrors the existing autonomy levels (L1-L4):
- At L1/L2: Phase 2 always requires human input
- At L3: Auto-confirm for low-risk trades
- At L4: Auto-confirm for all trades within risk limits

### 8.5 Cost Control

Each agentic session involves multiple Claude API calls (one per tool-use round-trip). The existing `CostTracker` from `reasoning_engine.py` should be wired into the agentic loop:

```python
# Before each Claude API call in the loop:
if not cost_tracker.can_call():
    return "Daily API cost limit reached. Session ending."
```

The current daily cap of $10 USD should comfortably cover ~50-100 agentic sessions at ~$0.05-0.15 each (most tool results are small JSON payloads).

---

## 9. What We Keep vs What Changes

### Keep (Reuse As-Is)

| Component | Why |
|---|---|
| `IBKRClient` | Core IBKR API wrapper — MCP tools call it |
| `RiskGovernor` | Risk enforcement — runs inside MCP tools |
| `ExitManager` | Exit logic — wrapped by `close_position` tool |
| `OrderManager` | Order placement — wrapped by `place_naked_put` tool |
| `chain.py`, `strike_selector.py` | Option chain analysis — wrapped by `get_option_chain` tool |
| `ibkr_scanner.py` | Market scanning — wrapped by `scan_for_options` tool |
| `MarketConditionMonitor` | Market data — wrapped by `get_market_conditions` tool |
| Trade DB + SQLAlchemy models | Data persistence — all tools read/write through it |
| `CostTracker` | API cost control — wired into agentic loop |

### Change (New Wrapper Layer)

| Component | What Changes |
|---|---|
| `daemon.py` | Becomes a scheduler that triggers agentic sessions (not an event processor) |
| `ActionExecutor` | Logic moves into individual MCP tool implementations |
| `WorkingMemory` | Becomes MCP Resources (read-only context for Claude) |
| Event-driven loop | Becomes agentic tool-calling loop |

### Remove (Replaced by Claude's Native Behavior)

| Component | Why It's No Longer Needed |
|---|---|
| `ReasoningContext` assembly | Claude fetches its own context via tools |
| `ClaudeReasoningEngine.reason()` | Claude IS the reasoning loop |
| `DecisionOutput` enum routing | Claude calls tools directly instead of returning enum codes |
| `_enrich_context()` | Claude calls `get_market_conditions()`, `get_position_pnl()` as needed |
| Pre/post output validation guardrails | Risk enforcement moves into tool implementations |
| Hardcoded system prompts (flowcharts) | Replaced by natural system prompt + tool descriptions |

---

## 10. Migration Path

### Phase 0: Setup (1 hour)

- Install `mcp[cli]>=1.26.0` in requirements.txt
- Create `src/mcp/server.py` skeleton with FastMCP
- Test basic connectivity with Claude Code: `claude mcp add trading python src/mcp/server.py`

### Phase 1: Read-Only Tools (1-2 days)

Expose tools that read but don't modify state:
- `get_open_positions`
- `get_market_conditions`
- `get_option_chain`
- `get_quote`
- `get_trade_history`
- `get_performance_summary`

**Validation:** Use Claude Code interactively to ask questions about positions and market data.

### Phase 2: Interactive Testing (30 minutes)

Register the MCP server with Claude Code and test:
```bash
claude mcp add trading-system python /path/to/src/mcp/server.py
```
Then chat: "What are my open positions?" / "How's the market looking?" / "Show me AAPL put chains for next week."

### Phase 3: Analysis + Execution Tools (1-2 days)

Add tools with side effects (risk-gated):
- `check_risk_limits`
- `check_margin`
- `evaluate_exit`
- `place_naked_put` (with `dry_run` gate)
- `close_position`
- `emergency_stop`

**Validation:** Use `dry_run=True` extensively. Verify RiskGovernor blocks invalid trades.

### Phase 4: Build the Automated Loop (2-3 days)

Create `src/mcp/pilot.py`:
- Agentic while-loop calling Claude API with MCP tools
- Session types: market_open, position_monitor, market_close, eod_reflection
- Cost tracking integration
- Audit logging (every tool call + Claude response)

### Phase 5: Scheduler (1-2 days)

Replace daemon event loop with time-based session triggers:
- 09:30 → `run_session("market_open")`
- Every 15 min → `run_session("position_monitor")`
- 16:00 → `run_session("market_close")`
- 16:30 → `run_session("eod_reflection")`

### Phase 6: Production Hardening (1-2 days)

- Human confirmation gates (at lower autonomy levels)
- Circuit breakers (API cost, consecutive errors, risk breaches)
- Graceful degradation when IBKR disconnects
- Session transcript storage for audit/learning

---

## Appendix: MCP SDK Reference

### Installation

```bash
pip install "mcp[cli]"
```

### Registering with Claude Code

```bash
# Local stdio server
claude mcp add trading-system python /path/to/src/mcp/server.py

# Remote HTTP server
claude mcp add --transport http trading-system http://localhost:8000/mcp
```

### Registering with Claude Desktop

File: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "trading-system": {
      "command": "python",
      "args": ["/Users/axel/projects/trading/trading_agent/src/mcp/server.py"],
      "env": {
        "IBKR_HOST": "127.0.0.1",
        "IBKR_PORT": "7497"
      }
    }
  }
}
```

### Key SDK Links

- Python SDK: https://github.com/modelcontextprotocol/python-sdk
- MCP Specification: https://modelcontextprotocol.io/specification
- MCP Tools Docs: https://modelcontextprotocol.io/docs/concepts/tools
- Claude Tool Use: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
