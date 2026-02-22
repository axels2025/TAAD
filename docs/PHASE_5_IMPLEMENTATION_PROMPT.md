# Phase 5: Continuous Agentic Loop — Implementation Prompt for Claude Code

**Version:** 2.0 (Enhanced with Claude LLM as ReasoningEngine Core)
**Prerequisite:** Phases 0–4 complete, 1424+ tests passing, PostgreSQL migrated

---

## Objective

Implement Phase 5: a production-grade autonomous trading daemon that operates continuously for weeks without human intervention. The system must trade naked puts, monitor positions, learn from outcomes, and self-improve — with Claude LLM as the core reasoning intelligence, not just a reporting layer.

This is high-stakes, long-horizon agentic AI operating on real money. Every design decision must reflect that gravity.

---

## Architecture Overview

```
AgenticOrchestrator (daemon process)
├── EventBus              — event-driven triggers (market open/close, fills, thresholds)
├── WorkingMemory         — PostgreSQL-backed crash-safe context store + pgvector semantic search
├── ClaudeReasoningEngine — LLM-powered decision making with structured JSON outputs + chain-of-thought
├── ActionExecutor        — routes decisions to existing CLI commands (stage, execute, sync, reconcile)
├── LearningLoop          — statistical pattern validation + Claude-designed experiment framework
├── AutonomyGovernor      — enforces autonomy level, escalation rules, minimal footprint principle
└── OversightInterface    — CLI + web dashboard for human monitoring and override
```

---

## Critical Design Principles

### Principle 0: Minimal Footprint
**When in doubt, do nothing.** The system must prefer inaction over uncertain action. Reversible actions are always preferred over irreversible ones. If market data is stale, connectivity is degraded, confidence is below threshold, or conditions are outside the training distribution — **the correct action is to skip the trade, not force one.**

This must be encoded as an explicit default in the ReasoningEngine prompt and as a hard guard in the ActionExecutor.

### Principle 1: Claude as the Brain, Not the Reporter
Claude LLM is the ReasoningEngine — the decision-making core. It receives structured market state, open positions, detected patterns, experiment results, and working memory context, and returns structured JSON decisions with full chain-of-thought reasoning. Rules and patterns are *inputs to Claude's judgment*, not substitutes for it. This is the fundamental upgrade from Phase 4.

### Principle 2: Explainable, Auditable Decisions
Every decision (trade, skip, exit, hold, experiment proposal) is logged with: what data Claude saw, what reasoning it applied, what it decided, and why. No black-box decisions. The audit log is a first-class deliverable, not an afterthought.

### Principle 3: Graduated Autonomy
The system starts in Level 1 (recommend only) and earns its way to higher autonomy levels through demonstrated reliability. Autonomy is never assumed — it is granted by performance.

### Principle 4: Conservative, Statistical Learning
The learning engine (Phase 3) already has p < 0.05 + effect size gates. Phase 5 adds Claude-designed experiments and end-of-day reflection, but no parameter changes are applied directly — only through statistically validated A/B tests.

---

## Component Specifications

---

### 1. EventBus (`src/agentic/event_bus.py`)

Event-driven, not polling. The daemon is mostly idle, waking on events.

**Event types to implement:**

| Event | Trigger | Priority |
|---|---|---|
| `MARKET_OPEN` | 9:30 AM ET, market days | Critical |
| `MARKET_CLOSE` | 4:00 PM ET | Critical |
| `PRE_MARKET_PREP` | 9:15 AM ET | High |
| `ORDER_FILLED` | IBKR callback on fill | Critical |
| `POSITION_STOP_APPROACHING` | Underlying within 5% of stop level | High |
| `UNDERLYING_SIGNIFICANT_MOVE` | Underlying moves >3% since entry | High |
| `BARCHART_DATA_AVAILABLE` | New CSV detected in configured watch folder | Medium |
| `END_OF_DAY_REFLECTION` | 4:30 PM ET | Medium |
| `WEEKLY_LEARNING_TRIGGER` | Sunday 8:00 PM ET | Medium |
| `TWS_DISCONNECTED` | ib_insync disconnect callback | Critical |
| `TWS_RECONNECTED` | ib_insync reconnect callback | Critical |
| `STALE_MARKET_DATA` | No quote update in >5 minutes during market hours | High |
| `EXPERIMENT_RESULT_READY` | A/B test reaches statistical significance | Medium |
| `ANOMALY_DETECTED` | Conditions outside training distribution | High |

Events must be durable: if the daemon crashes during event processing, the event must be replayed on restart. Store event queue in PostgreSQL.

---

### 2. WorkingMemory (`src/agentic/working_memory.py`)

The trader's notebook that survives a power outage. Backed by PostgreSQL, never in-memory.

**Schema — `working_memory` table:**
```sql
id                    SERIAL PRIMARY KEY
session_id            UUID NOT NULL          -- current daemon session
strategy_state        JSONB                  -- current parameter set (deltas, premiums, DTE ranges)
open_experiments      JSONB                  -- active A/B tests with current counts
recent_decisions      JSONB                  -- last 50 decisions with reasoning + outcomes
market_context        JSONB                  -- VIX level, regime, notable events
performance_window    JSONB                  -- rolling 30-day win rate, avg ROI, Sharpe
active_anomalies      JSONB                  -- unresolved anomaly flags
autonomy_level        INTEGER DEFAULT 1      -- current L1-L4 autonomy level
autonomy_metrics      JSONB                  -- consecutive days without override, etc.
updated_at            TIMESTAMP
```

**Semantic memory via pgvector:**
- Enable the `pgvector` PostgreSQL extension
- Embed each decision summary (using Claude's embedding API or `text-embedding-3-small`) as a vector
- Add a `decision_embeddings` table with `embedding VECTOR(1536)` column
- Implement `WorkingMemory.retrieve_similar_context(query: str, k: int = 5)` for retrieval-augmented reasoning
- Example use: "What happened the last 5 times we considered a naked put on NVDA in a VIX > 25 regime?"

**On startup:** Load existing WorkingMemory from DB. If session_id is new (fresh start), initialize from last known state. Never start with empty context if history exists.

---

### 3. ClaudeReasoningEngine (`src/agentic/reasoning_engine.py`)

**This is the core upgrade.** Claude LLM replaces hard-coded if/else rule trees as the decision-making intelligence.

#### 3a. Structured Input Context

Before each Claude call, assemble a `ReasoningContext` object:

```python
@dataclass
class ReasoningContext:
    # Current state
    open_positions: list[PositionSummary]      # all open naked puts with Greeks
    account_metrics: AccountSummary            # NLV, margin used, buying power
    market_context: MarketContext              # VIX, regime, time of day
    
    # Candidates (if applicable)
    candidates: list[CandidateSummary]         # from Barchart + IBKR validation
    
    # Memory
    recent_decisions: list[DecisionRecord]     # last 20 decisions
    similar_past_contexts: list[str]           # pgvector semantic retrieval
    
    # Learning state
    active_patterns: list[PatternSummary]      # patterns with p < 0.05
    active_experiments: list[ExperimentSummary]
    strategy_state: StrategyState              # current parameter set
    
    # Constraints
    autonomy_level: int                        # 1-4, constrains what Claude can authorize
    pending_anomalies: list[AnomalyFlag]       # unresolved flags
    
    def to_prompt_context(self) -> str:
        """Serialize to structured text for Claude system prompt."""
```

#### 3b. Claude API Call Pattern

```python
class ClaudeReasoningEngine:
    
    SYSTEM_PROMPT = """
    You are the reasoning core of an autonomous naked put options trading system.
    You are disciplined, risk-conscious, and deeply skeptical of overtrading.
    
    Your governing philosophy:
    - When in doubt, do nothing. Missing a trade is never as costly as a bad trade.
    - Prefer reversible actions over irreversible ones.
    - One bad month can undo six good ones. Protect capital above all else.
    - Every decision must be explainable to a human reviewer.
    
    You will receive structured trading context and must return a JSON decision object.
    Your response must be valid JSON matching the DecisionOutput schema.
    Do not include any text outside the JSON object.
    Include your full chain-of-thought reasoning in the 'reasoning' field.
    """
    
    async def reason(
        self,
        event: TradingEvent,
        context: ReasoningContext,
    ) -> DecisionOutput:
        response = await anthropic_client.messages.create(
            model="claude-opus-4-6",   # Use Opus for reasoning, Sonnet for reflection
            max_tokens=2000,
            system=self.SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": self._build_user_prompt(event, context)
            }]
        )
        return DecisionOutput.model_validate_json(response.content[0].text)
```

#### 3c. Decision Output Schema

```python
class DecisionOutput(BaseModel):
    # What to do
    action: Literal[
        "EXECUTE_TRADES",      # place orders for staged candidates
        "STAGE_CANDIDATES",    # run sunday-session workflow
        "CLOSE_POSITION",      # exit a specific position
        "MONITOR_ONLY",        # no action, continue watching
        "SKIP_SESSION",        # skip today's trading entirely
        "PROPOSE_EXPERIMENT",  # suggest A/B test for LearningLoop
        "REQUEST_HUMAN_REVIEW", # escalate to human
        "EMERGENCY_HALT",      # stop all trading immediately
    ]
    
    # What targets (if applicable)
    target_symbols: list[str] = []
    target_position_ids: list[int] = []
    
    # Confidence and reasoning (MANDATORY)
    confidence: float          # 0.0 - 1.0
    reasoning: str             # full chain-of-thought (stored in audit log)
    key_factors: list[str]     # bullet points for human-readable summary
    risks_considered: list[str]
    
    # Experiment proposal (if action == PROPOSE_EXPERIMENT)
    experiment: ExperimentProposal | None = None
    
    # Human escalation (if action == REQUEST_HUMAN_REVIEW)
    escalation_reason: str | None = None
    escalation_urgency: Literal["low", "medium", "high"] | None = None
```

#### 3d. Cost Management

- Reasoning calls (market open, position check): ~$0.05–0.10 each using claude-opus-4-6
- Reflection calls (end of day): ~$0.10–0.15 using claude-sonnet-4-6
- Estimated daily cost: ~$0.50–1.50 for 5–10 reasoning events
- Estimated monthly cost: ~$15–45 (negligible vs. trading capital at risk)
- Implement a daily cost cap in config: `CLAUDE_DAILY_COST_CAP_USD: 5.00`
- Log every API call cost to the database for monitoring

---

### 4. AutonomyGovernor (`src/agentic/autonomy_governor.py`)

**Defines what the system can do without asking permission.**

#### Autonomy Levels

| Level | Name | What Requires Human Approval |
|---|---|---|
| L1 | Recommend | Everything — Claude reasons, human executes |
| L2 | Supervised | Positions > 1x average size, new symbols, all exits |
| L3 | Semi-Autonomous | Positions > 2x average, anomaly-triggered decisions |
| L4 | Autonomous | Only anomaly flags + weekly summary review |

**Autonomy escalation rules:**
- Start at L1 on first deployment (paper trading)
- Promote to L2: 30 consecutive trading days without manual override AND win rate ≥ 70%
- Promote to L3: 60 additional days at L2 without override AND Sharpe > 1.5
- Promote to L4: Explicit human CLI command only — never automatic
- **Demotion is automatic and immediate:** any manual override, consecutive loss streak (3+ losses in 5 trades), or anomaly flag triggers demotion one level

**Hardcoded anomaly escalation rules — ALWAYS require human sign-off regardless of autonomy level:**

```python
MANDATORY_HUMAN_REVIEW_TRIGGERS = [
    "first_trade_new_symbol",           # symbol never traded before
    "position_size_3x_recent_average",  # abnormally large position
    "three_consecutive_losses_sector",  # sector underperformance
    "vix_spike_30pct_intraday",         # extreme volatility event
    "market_data_gap_30min",            # extended data outage during hours
    "regime_never_seen_in_training",    # OOD market conditions
    "margin_utilization_above_40pct",   # approaching position limit
    "claude_confidence_below_0_4",      # low-confidence decision
    "consecutive_fill_failures_3",      # execution system degradation
]
```

**Minimal footprint enforcement:**
- If any mandatory trigger is active: action must be `MONITOR_ONLY` or `REQUEST_HUMAN_REVIEW`
- ActionExecutor checks AutonomyGovernor before executing any order
- AutonomyGovernor decisions are logged separately from Claude reasoning decisions

---

### 5. LearningLoop (`src/agentic/learning_loop.py`)

Extends the existing Phase 3 LearningOrchestrator with Claude-powered experiment design and end-of-day reflection.

#### 5a. End-of-Day Claude Reflection

Triggered by `END_OF_DAY_REFLECTION` event at 4:30 PM ET.

```python
async def run_end_of_day_reflection(self, date: date) -> ReflectionReport:
    """
    Claude reviews today's decisions and outcomes qualitatively.
    This is DISTINCT from the statistical LearningLoop.
    """
    today_decisions = self.working_memory.get_decisions_for_date(date)
    today_outcomes = self.db.get_position_changes_for_date(date)
    
    reflection = await self.claude.reflect(
        system="""
        You are reviewing today's trading decisions and their early outcomes.
        Your goal is to:
        1. Identify decisions where your reasoning was correct vs. where you got lucky or were wrong
        2. Flag any patterns that the statistical engine should investigate
        3. Update your priors for tomorrow's reasoning
        4. Note any market conditions worth remembering
        
        Be intellectually honest. Acknowledge uncertainty. 
        Do not rationalize bad outcomes as good decisions.
        """,
        context={"decisions": today_decisions, "outcomes": today_outcomes}
    )
    
    # Store reflection in working_memory.recent_decisions for next day's context
    self.working_memory.update_reflection(date, reflection)
    
    # If reflection identifies a hypothesis, propose it as an experiment
    if reflection.proposed_experiments:
        for exp in reflection.proposed_experiments:
            self.learning_engine.propose_experiment(exp, source="claude_reflection")
    
    return reflection
```

#### 5b. Claude-Designed A/B Experiments

When the statistical engine detects a pattern worth testing, Claude designs the experiment:

```python
async def design_experiment(self, pattern: DetectedPattern) -> ExperimentDesign:
    """
    Claude designs the A/B test for a detected pattern.
    Statistical engine detects, Claude designs, statistics validates.
    """
    design = await self.claude.design(
        system="Design a statistically rigorous A/B experiment to validate this pattern...",
        pattern=pattern,
        current_strategy=self.working_memory.strategy_state,
    )
    # Returns: control_parameters, test_parameters, min_sample_size, 
    #          success_metric, confounders_to_control, expected_duration
    return design
```

#### 5c. Outcome Feedback to Claude

When a position closes, the outcome is stored and linked to the original reasoning decision. Before the next similar decision, the ReasoningEngine retrieves:
- "We skipped AAPL last Tuesday because IV was too low. AAPL moved up 2% — the skip was correct."
- "We entered NVDA on Monday. IV rank was 72, delta 0.24. It expired worthless. ✓"

This closes the feedback loop between Claude's reasoning and actual trade outcomes.

---

### 6. ActionExecutor (`src/agentic/action_executor.py`)

Routes Claude's `DecisionOutput` to existing CLI infrastructure. **Do not reimplement existing commands.**

```python
class ActionExecutor:
    
    async def execute(self, decision: DecisionOutput, context: ReasoningContext) -> ActionResult:
        # 1. Check autonomy level FIRST
        if not self.governor.can_execute(decision, context):
            return self._queue_for_human_approval(decision)
        
        # 2. Check minimal footprint conditions
        if self._should_defer(context):
            return ActionResult(status="deferred", reason="minimal_footprint_triggered")
        
        # 3. Route to existing CLI commands
        match decision.action:
            case "STAGE_CANDIDATES":
                return await self._run_sunday_session(decision)
            case "EXECUTE_TRADES":
                return await self._run_execute_staged(decision)
            case "CLOSE_POSITION":
                return await self._run_close_position(decision)
            case "REQUEST_HUMAN_REVIEW":
                return await self._alert_human(decision)
            case "MONITOR_ONLY":
                return ActionResult(status="no_action")
        
        # 4. Capture fill snapshots IMMEDIATELY on order fill event
        # Do not defer. Greeks decay in real-time.
    
    async def _capture_fill_snapshot(self, fill_event: OrderFilledEvent):
        """Called synchronously when ORDER_FILLED event fires."""
        # Capture Greeks, IV, underlying price while market data is live
        # This is the fix for the currently broken snapshot capture
```

**Existing commands to reuse (do not reimplement):**
- `sunday-session` → stage candidates from Barchart CSV
- `execute-staged --live --yes` → execute staged trades
- `sync-orders` → sync order status post-fill
- `reconcile-positions` → verify DB vs IBKR
- `learn --analyze` → run weekly statistical analysis

---

### 7. Graceful Degradation

**Every failure mode has a defined behavior. None of them is "crash."**

| Failure | Behavior |
|---|---|
| TWS disconnects | Pause all trading, set `STATUS=PAUSED_TWS_DISCONNECT`, attempt reconnect every 60s, alert human, resume when reconnected |
| Database unavailable | Queue decisions in local SQLite fallback, replay to PostgreSQL on reconnect, never trade without DB |
| Claude API error/timeout | Fall back to L1 mode (recommend only), alert human, log incident, retry next event cycle |
| Market data stale (>5 min) | Set `STALE_DATA` flag, all subsequent decisions must be `MONITOR_ONLY` until flag cleared |
| Unhandled exception | Catch at orchestrator level, log full traceback, alert human, continue other event processing |
| Claude returns invalid JSON | Retry once with explicit format correction, then fall back to `MONITOR_ONLY` |
| Cost cap reached | Disable Claude calls for remainder of day, fall back to `MONITOR_ONLY`, alert human |

**Health monitoring:**
```python
# Heartbeat table in PostgreSQL
daemon_health: {
    pid: int
    status: Literal["running", "paused", "error", "shutdown"]
    last_heartbeat: datetime   # updated every 60s
    current_activity: str
    event_queue_depth: int
    autonomy_level: int
    active_anomalies: list[str]
}
```

---

### 8. OversightInterface

#### 8a. CLI Commands (implement these)

```bash
# Show current daemon state
python -m src.cli.main daemon status
# Output: status, autonomy level, open positions, queued events, 
#         last Claude decision + reasoning, active anomalies, recent alerts

# Show current working memory context
python -m src.cli.main daemon context
# Output: strategy_state, active_experiments, recent_decisions summary

# Pause/resume trading (daemon keeps running, just stops trading)
python -m src.cli.main daemon pause --reason "Reviewing performance"
python -m src.cli.main daemon resume

# Override a pending decision (queued for human approval)
python -m src.cli.main daemon override <decision_id> --approve
python -m src.cli.main daemon override <decision_id> --reject --reason "Market too volatile"

# Manually adjust autonomy level
python -m src.cli.main daemon set-autonomy --level 2

# Show full audit log
python -m src.cli.main daemon audit --days 7
python -m src.cli.main daemon audit --decision-id <id>  # show full Claude reasoning

# Show cost report
python -m src.cli.main daemon costs --days 30

# Emergency stop (all positions + daemon halt)
python -m src.cli.main daemon emergency-stop
```

#### 8b. Web Dashboard (required, not optional)

A simple FastAPI + React web dashboard that shows daemon state without requiring SSH. This was deferred from earlier phases — it belongs in Phase 5.

**FastAPI backend:** `src/agentic/dashboard_api.py`

Endpoints:
- `GET /api/status` — daemon health, autonomy level, current activity
- `GET /api/positions` — open positions with traffic light risk status (existing from planned dashboard)
- `GET /api/decisions` — recent decisions with reasoning
- `GET /api/queue` — decisions pending human approval
- `POST /api/decisions/{id}/approve` — approve queued decision
- `POST /api/decisions/{id}/reject` — reject with reason
- `POST /api/daemon/pause` — pause trading
- `POST /api/daemon/resume` — resume trading
- `GET /api/costs` — Claude API cost tracking

**React frontend:** Simple, functional. No fancy animations. Focus on information density. Use the existing traffic light visualization concept (green/yellow/red) for position risk status.

#### 8c. Alert System

Alerts for decisions requiring human attention:

```python
AlertChannel = Literal["log", "email", "slack"]  # configure in YAML

alert_triggers = {
    "human_review_required": AlertLevel.CRITICAL,
    "anomaly_detected": AlertLevel.HIGH,
    "autonomy_demoted": AlertLevel.HIGH,
    "claude_api_error": AlertLevel.MEDIUM,
    "tws_disconnect": AlertLevel.MEDIUM,
    "cost_cap_approaching": AlertLevel.LOW,
    "end_of_day_summary": AlertLevel.INFO,
}
```

Implement at minimum: log-based alerts (always), email alerts (YAML-configured SMTP), optional Slack webhook.

---

### 9. Daemon Process (`src/agentic/daemon.py`)

```python
class TAADDaemon:
    """
    Trade Archaeology & Alpha Discovery — Continuous Agentic Loop
    
    Start with: python -m src.cli.main daemon start
    """
    
    async def run(self):
        await self.startup()
        async for event in self.event_bus.stream():
            try:
                await self.process_event(event)
            except Exception as e:
                logger.exception(f"Unhandled error processing {event}: {e}")
                await self.alert_system.send(AlertLevel.HIGH, f"Event processing error: {e}")
                # Continue — do not crash
    
    async def process_event(self, event: TradingEvent):
        # 1. Assemble context (WorkingMemory + semantic retrieval)
        context = await self.working_memory.assemble_context(event)
        
        # 2. Check if event requires autonomous response or mandatory escalation
        if self.governor.requires_mandatory_human_review(event, context):
            await self.overseer.escalate(event, context)
            return
        
        # 3. Reason with Claude
        decision = await self.reasoning_engine.reason(event, context)
        
        # 4. Log decision BEFORE acting (audit trail)
        decision_id = await self.audit_log.record(event, context, decision)
        
        # 5. Check autonomy gate
        if not self.governor.can_execute(decision, context):
            await self.overseer.queue_for_approval(decision_id, decision, context)
            return
        
        # 6. Execute
        result = await self.action_executor.execute(decision, context)
        
        # 7. Update working memory with outcome
        await self.working_memory.record_outcome(decision_id, result)
        
        # 8. Update heartbeat
        await self.health_monitor.heartbeat()
```

**Process management:**
- Run as a background process: `nohup python -m src.cli.main daemon start &`
- PID file at `run/taad.pid`
- Loguru logging to `logs/daemon.log` with rotation
- `systemd` service file generated at `config/taad.service` for production deployment

---

## Database Schema Additions

New tables required (add Alembic migrations):

```sql
-- Persistent event queue
CREATE TABLE daemon_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',  -- pending, processing, done, failed
    created_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP
);

-- Claude decision audit log
CREATE TABLE decision_audit (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    reasoning_context JSONB NOT NULL,     -- full context sent to Claude
    claude_response JSONB NOT NULL,        -- full decision output
    action_taken VARCHAR(50),
    action_result JSONB,
    autonomy_level INTEGER,
    claude_cost_usd DECIMAL(10,6),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Working memory (single row, always updated)
CREATE TABLE working_memory (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    strategy_state JSONB,
    open_experiments JSONB,
    recent_decisions JSONB,
    market_context JSONB,
    performance_window JSONB,
    active_anomalies JSONB,
    autonomy_level INTEGER DEFAULT 1,
    autonomy_metrics JSONB,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Semantic decision embeddings (pgvector)
CREATE TABLE decision_embeddings (
    id SERIAL PRIMARY KEY,
    decision_audit_id INTEGER REFERENCES decision_audit(id),
    summary TEXT NOT NULL,                 -- short description of decision context
    embedding VECTOR(1536),               -- OpenAI text-embedding-3-small
    created_at TIMESTAMP DEFAULT NOW()
);

-- Daemon health heartbeat
CREATE TABLE daemon_health (
    id SERIAL PRIMARY KEY,
    pid INTEGER,
    status VARCHAR(20),
    last_heartbeat TIMESTAMP,
    current_activity TEXT,
    autonomy_level INTEGER,
    active_anomalies JSONB,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Claude API cost tracking
CREATE TABLE claude_api_costs (
    id SERIAL PRIMARY KEY,
    call_type VARCHAR(50),                 -- reasoning, reflection, experiment_design
    model VARCHAR(50),
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd DECIMAL(10,6),
    decision_audit_id INTEGER REFERENCES decision_audit(id),
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## Configuration (`config/phase5.yaml`)

```yaml
daemon:
  autonomy_level: 1                  # Start at L1 (recommend only)
  heartbeat_interval_seconds: 60
  event_queue_poll_seconds: 5

claude:
  reasoning_model: "claude-opus-4-6"
  reflection_model: "claude-sonnet-4-6"
  daily_cost_cap_usd: 5.00
  max_tokens_reasoning: 2000
  max_tokens_reflection: 1500

autonomy_escalation:
  l1_to_l2_min_days: 30
  l1_to_l2_min_win_rate: 0.70
  l2_to_l3_min_days: 60
  l2_to_l3_min_sharpe: 1.5
  l3_to_l4_manual_only: true

minimal_footprint:
  skip_on_stale_data_minutes: 5
  skip_on_confidence_below: 0.40
  skip_on_margin_above_pct: 40
  prefer_no_trade_on_tie: true

alerts:
  email_enabled: false
  email_smtp_host: ""
  email_to: ""
  slack_enabled: false
  slack_webhook_url: ""

web_dashboard:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  auth_token: ""                     # Set this — dashboard exposes trading state

pgvector:
  enabled: true
  embedding_model: "text-embedding-3-small"
  semantic_retrieval_k: 5
```

---

## Fill Snapshot Fix (Critical Bug)

**This is currently broken and must be fixed as part of Phase 5.**

When `ORDER_FILLED` event fires, `_capture_fill_snapshot` must run **synchronously** before the event processing returns. Do not defer, do not batch — capture immediately.

```python
async def _capture_fill_snapshot(self, fill: OrderFilledEvent):
    """
    Greeks, IV, and underlying price are time-sensitive. 
    They must be captured the moment the fill is confirmed.
    Delta, IV, and underlying price at this exact moment
    are irreplaceable for learning analytics.
    """
    snapshot = await self.ibkr_client.get_option_snapshot(
        symbol=fill.symbol,
        expiry=fill.expiry,
        strike=fill.strike,
        right=fill.right,
    )
    await self.db.save_entry_snapshot(fill.trade_id, snapshot)
    logger.info(f"Fill snapshot captured: {fill.symbol} delta={snapshot.delta:.3f} iv={snapshot.iv:.1%}")
```

---

## File Structure

```
src/agentic/
├── __init__.py
├── daemon.py                     # Main daemon process + startup
├── event_bus.py                  # Event detection, queuing, dispatch
├── working_memory.py             # PostgreSQL-backed context store + pgvector
├── reasoning_engine.py           # Claude API integration + structured outputs
├── autonomy_governor.py          # Autonomy levels, escalation, minimal footprint
├── action_executor.py            # Routes decisions to existing CLI commands
├── learning_loop.py              # End-of-day reflection + Claude experiment design
├── dashboard_api.py              # FastAPI web dashboard backend
├── alert_system.py               # Multi-channel alert dispatch
└── health_monitor.py             # Heartbeat, health checks

src/dashboard/                    # React frontend
├── package.json
├── src/
│   ├── App.jsx
│   ├── components/
│   │   ├── DaemonStatus.jsx
│   │   ├── PositionTrafficLights.jsx
│   │   ├── DecisionAuditLog.jsx
│   │   ├── PendingApprovals.jsx
│   │   └── CostMonitor.jsx

tests/
├── unit/
│   ├── test_event_bus.py
│   ├── test_working_memory.py
│   ├── test_reasoning_engine.py
│   ├── test_autonomy_governor.py
│   └── test_action_executor.py
├── integration/
│   └── test_agentic_loop.py
└── e2e/
    └── test_full_daemon_cycle.py

config/
├── phase5.yaml
└── taad.service                  # systemd unit file

alembic/versions/
└── xxxx_phase5_agentic_tables.py
```

---

## Success Criteria

**The system is complete when:**

- [ ] Daemon starts, loads WorkingMemory from PostgreSQL, and resumes prior state
- [ ] Event-driven loop fires correctly for all 13 event types
- [ ] Claude receives structured context and returns valid `DecisionOutput` JSON
- [ ] Full chain-of-thought reasoning is stored in `decision_audit` table for every decision
- [ ] AutonomyGovernor correctly gates actions at L1 (blocks all execution)
- [ ] All 9 mandatory human review triggers correctly escalate
- [ ] Minimal footprint: system chooses `MONITOR_ONLY` when confidence < 0.40
- [ ] `ORDER_FILLED` event immediately captures fill snapshot (Greeks, IV, underlying)
- [ ] TWS disconnect → pause → reconnect → resume without data loss
- [ ] Database outage → local queue → replay on reconnect
- [ ] `daemon status` CLI shows complete current state
- [ ] Web dashboard renders at localhost:8080 with live position status
- [ ] End-of-day reflection runs at 4:30 PM and stores to WorkingMemory
- [ ] Claude-designed experiments fed to Phase 3 LearningOrchestrator
- [ ] Outcome feedback linked from closed positions back to originating decisions
- [ ] pgvector semantic search returns relevant past contexts
- [ ] Claude API costs tracked per-call in database
- [ ] Daily cost cap enforced with graceful fallback
- [ ] Daemon runs 24+ hours paper trading without crash
- [ ] All existing 1424+ tests still pass
- [ ] New unit tests achieve ≥85% coverage on all Phase 5 modules

---

## Implementation Order

Implement in this sequence (each step must pass tests before proceeding):

1. **Database migrations** — all new tables + pgvector extension
2. **EventBus** — event detection + durable PostgreSQL queue
3. **WorkingMemory** — basic CRUD + semantic embedding
4. **AutonomyGovernor** — rules engine (no Claude yet)
5. **ClaudeReasoningEngine** — API integration + schema validation
6. **ActionExecutor** — route to existing CLI commands
7. **Daemon skeleton** — event loop + health monitor
8. **Fill snapshot fix** — `ORDER_FILLED` → immediate capture
9. **LearningLoop extensions** — end-of-day reflection + experiment design
10. **OversightInterface CLI** — all `daemon *` commands
11. **Alert system** — log + email + Slack
12. **Web dashboard** — FastAPI backend + React frontend
13. **End-to-end integration tests** — full 24-hour simulated cycle
14. **systemd service file** — production deployment config

---

## What NOT to Build

- Do not reimplement `sunday-session`, `execute-staged`, `sync-orders`, `reconcile-positions`, or `learn` — call them
- Do not reimplement pattern detection, StatisticalValidator, or ExperimentEngine — call them
- Do not build a new database ORM layer — use existing SQLAlchemy models
- Do not make Claude execute orders directly — all execution routes through ActionExecutor → existing CLI commands

---

## Notes for Claude Code

- Read `CLAUDE.md`, `SPEC_TRADING_SYSTEM.md`, and `docs/DEVELOPMENT_HISTORY.md` before starting
- All existing quality gates must continue to pass: `black`, `ruff`, `mypy`, `pytest` (1424+ tests)
- This will eventually handle real money — safety, correctness, and auditability are non-negotiable
- When uncertain between two approaches, choose the more conservative one and document why
- Create checkpoints after each numbered implementation step above and request approval before proceeding
- The Claude API key for the ReasoningEngine is the same Anthropic API key already configured in `.env`
