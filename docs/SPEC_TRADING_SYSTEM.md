# SPEC_TRADING_SYSTEM.md - System Specification

## Project Mission

Build a **self-learning agentic AI trading system** that:
1. Executes a proven naked put options strategy autonomously
2. Learns from every trade outcome
3. Continuously improves through pattern detection and parameter optimization
4. Operates safely within strict risk limits
5. Requires minimal human intervention after initial setup

---

## Documentation Structure

This specification references several detailed planning documents:

1. **CLAUDE.md** ← You are here reading this alongside the spec
   - Development standards and workflow
   - Tech stack and tooling
   - Quality gates and checkpoints
   
2. **AI_Trading_System_Implementation_Plan.md**
   - Complete 8-phase implementation plan
   - Detailed architecture diagrams
   - Component specifications
   - Agentic AI extension design
   
3. **Implementation_Plan_Modifications_for_Self_Learning_Goal.md**
   - Phase reordering for self-learning priority
   - Enhanced learning engine specifications
   - Simplified intelligence agents (made optional)
   
4. **PRE_INSTALLATION_CHECKLIST.md**
   - Software prerequisites
   - Account setup (IBKR, Anthropic)
   - Installation verification
   - Connection testing

---

## Quick Start for Claude Code

### Before You Begin
1. ✅ Verify all prerequisites from `PRE_INSTALLATION_CHECKLIST.md` are complete
2. ✅ Read `CLAUDE.md` to understand development standards
3. ✅ Review `Implementation_Plan_Modifications_for_Self_Learning_Goal.md` for phase ordering
4. ✅ Understand this spec defines WHAT to build, other docs define HOW

### Your Development Workflow
1. **Read current phase** in this spec
2. **Plan implementation** - break into steps
3. **Implement incrementally** - small commits
4. **Test continuously** - every function
5. **Validate completion** - check success criteria
6. **Request approval** - at major checkpoints
7. **Proceed to next phase** - only after approval

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                   User Interface                         │
│              (CLI + Dashboard)                           │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│              Agentic Control Loop (24/7)                │
│   Perceive → Reason → Plan → Execute → Learn            │
└─────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┴─────────────────┐
        │                                   │
┌───────────────────┐            ┌──────────────────────┐
│  Learning Engine  │            │  Execution Engine    │
│  • Pattern Detect │            │  • Order Executor    │
│  • Experiments    │            │  • Position Monitor  │
│  • Optimization   │            │  • Exit Manager      │
└───────────────────┘            └──────────────────────┘
        │                                   │
        └─────────────────┬─────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│                  Data Layer                             │
│  SQLite: Trades | Experiments | Learning | Patterns    │
└─────────────────────────────────────────────────────────┘
```

---

## Phase-Based Implementation (Reordered for Self-Learning)

### Phase 0: Foundation Setup (Week 1)
**Goal:** Clean foundation for autonomous development  
**Time:** 3-5 days

**What to Build:**
1. Project structure following `CLAUDE.md`
2. Database schema and migrations
3. Configuration system with validation
4. Logging infrastructure
5. IBKR connection wrapper with retry logic
6. Basic CLI framework

**Success Criteria:**
- ✅ Can connect to IBKR paper trading
- ✅ Database initialized with schema
- ✅ Configuration loads from .env
- ✅ Logging captures events to file
- ✅ Tests pass with >90% coverage

**Deliverables:**
```
src/
  config/base.py              # Configuration classes
  data/database.py            # Database connection
  data/models.py              # SQLAlchemy models
  tools/ibkr_client.py        # IBKR API wrapper
  cli/main.py                 # CLI entry point
tests/
  unit/test_config.py
  integration/test_database.py
  integration/test_ibkr.py
```

**Detailed Specification:** See `AI_Trading_System_Implementation_Plan.md` Phase 0

---

### Phase 1: Baseline Strategy Implementation (Weeks 2-3) ⭐ NEW
**Goal:** Perfectly replicate user's proven naked put strategy  
**Time:** 7-10 days

**User's Strategy Parameters:**
```python
BASELINE_STRATEGY = {
    "name": "Naked Put - Weekly",
    "option_type": "PUT",
    "otm_range": (0.15, 0.20),      # 15-20% out-of-the-money
    "premium_range": (0.30, 0.50),   # $0.30-$0.50 per share
    "dte_range": (7, 14),            # 7-14 days to expiration
    "position_size": 5,              # 5 contracts per trade
    "max_positions": 10,             # Maximum open positions
    "trend_filter": "uptrend",       # Only stocks in uptrend
    "exit_rules": {
        "profit_target": 0.50,       # 50% of max profit
        "stop_loss": -2.00,          # -200% of premium
        "time_exit": 3               # Exit 3 days before expiration
    }
}
```

**What to Build:**

1. **NakedPutStrategy Class** (`src/strategies/naked_put.py`)
   - Implements baseline strategy logic
   - Entry criteria validation
   - Exit criteria evaluation
   - Configuration management

2. **Stock Screener** (`src/tools/screener.py`)
   - Scan for uptrend stocks (Price > SMA20 > SMA50)
   - Filter by price range
   - Filter by volume/liquidity
   - Return ranked candidates

3. **Options Finder** (`src/tools/options_finder.py`)
   - Query IBKR options chains
   - Filter by OTM %, premium, DTE
   - Query IBKR for margin requirements
   - Rank by margin efficiency

4. **Strategy Validator** (`src/strategies/validator.py`)
   - Validate strategy against historical data
   - Ensure criteria match user's manual approach
   - Generate validation report

**Success Criteria:**
- ✅ Strategy identifies same trades as user would manually
- ✅ Entry criteria 95%+ match rate with manual selection
- ✅ Exit rules trigger correctly in backtests
- ✅ Can generate 5-10 trade opportunities daily
- ✅ All components have >90% test coverage

**Deliverables:**
```
src/
  strategies/
    base.py                   # Strategy interface
    naked_put.py              # Baseline strategy
    validator.py              # Strategy validation
  tools/
    screener.py               # Stock screening
    options_finder.py         # Options search
    margin_calculator.py      # Margin calculations
tests/
  unit/test_naked_put.py
  integration/test_strategy_workflow.py
```

**Detailed Specification:** See `Implementation_Plan_Modifications_for_Self_Learning_Goal.md` Phase 1

---

### Phase 2: Autonomous Execution Engine (Weeks 4-5)
**Goal:** Execute trades automatically with safety mechanisms  
**Time:** 7-10 days

**What to Build:**

1. **OrderExecutor** (`src/execution/order_executor.py`)
   - Place orders via IBKR API
   - Pre-flight validation checks
   - Order status tracking
   - Fill confirmation
   - Slippage monitoring

2. **PositionMonitor** (`src/execution/position_monitor.py`)
   - Real-time position tracking
   - P&L calculation (realized & unrealized)
   - Greeks monitoring (delta, theta, gamma, vega)
   - Position aging
   - Alert generation

3. **ExitManager** (`src/execution/exit_manager.py`)
   - Profit target detection & execution
   - Stop loss monitoring & execution
   - Time-based exit scheduling
   - Emergency exit capabilities
   - Exit reason logging

4. **RiskGovernor** (`src/execution/risk_governor.py`)
   - Pre-trade risk checks
   - Real-time limit enforcement
   - Circuit breakers
   - Position sizing validation
   - Emergency shutdown

**Risk Limits (Configuration):**
```python
RISK_LIMITS = {
    "max_daily_loss": -0.02,          # -2% daily circuit breaker
    "max_position_loss": -500,         # $500 per position stop loss
    "max_portfolio_var_95": 0.10,      # 10% VaR limit
    "max_sector_concentration": 0.30,  # 30% max in any sector
    "max_correlation": 0.70,           # 0.70 max between positions
    "max_margin_utilization": 0.80,    # 80% max margin use
    "max_positions_per_day": 10        # Limit new positions
}
```

**Success Criteria:**
- ✅ Can place orders successfully in paper trading
- ✅ All orders logged with full context
- ✅ Positions monitored in real-time (15-min updates)
- ✅ Exits execute at correct triggers
- ✅ Risk limits enforced 100% of time (no violations)
- ✅ 20+ successful autonomous trades completed

**Deliverables:**
```
src/
  execution/
    order_executor.py         # Order placement
    position_monitor.py       # Position tracking
    exit_manager.py           # Exit handling
    risk_governor.py          # Risk enforcement
  data/
    trade_logger.py           # Rich trade logging
tests/
  unit/test_execution/
  integration/test_autonomous_trading.py
```

**Detailed Specification:** See `AI_Trading_System_Implementation_Plan.md` Phase 6

---

### Phase 3: Learning Engine (Weeks 6-7) ⭐ CORE
**Goal:** Enable self-learning from trade outcomes  
**Time:** 7-10 days

**What to Build:**

1. **PatternDetector** (`src/learning/pattern_detector.py`)
   - Analyze trades by OTM range, DTE, sector, timing
   - Statistical significance testing
   - Confidence scoring
   - Pattern persistence validation

```python
class Pattern:
    """Discovered trading pattern"""
    pattern_type: str        # "otm_range", "sector", "timing", etc.
    pattern_name: str        # "18-20% OTM outperforms"
    sample_size: int         # Number of trades
    win_rate: float          # Win percentage
    avg_roi: float           # Average ROI
    confidence: float        # Statistical confidence (0-1)
    p_value: float           # Statistical significance
    context: dict            # Market conditions, etc.
```

2. **ExperimentEngine** (`src/learning/experiment_engine.py`)
   - A/B test framework
   - Experiment creation & tracking
   - Result analysis
   - Adoption decision logic

```python
class Experiment:
    """A/B test for strategy improvement"""
    experiment_id: str
    hypothesis: str          # "18-20% OTM better than 15-17%"
    parameter: str           # "otm_range"
    control_value: Any       # (0.15, 0.17)
    test_value: Any          # (0.18, 0.20)
    allocation: float        # 0.20 = 20% to test group
    min_samples: int         # 30 trades required
    status: str              # "active", "complete", "adopted", "rejected"
```

3. **ParameterOptimizer** (`src/learning/parameter_optimizer.py`)
   - Parameter search algorithms
   - Gradient-based optimization
   - Multi-objective optimization
   - Change proposal generation

4. **StatisticalValidator** (`src/learning/statistical_validator.py`)
   - T-tests for significance
   - Effect size calculation
   - Sample size adequacy checks
   - Multiple testing correction

**Learning Workflow:**
```
1. Collect trades (minimum 30 for any analysis)
2. Detect patterns across dimensions
3. Validate statistical significance
4. Generate improvement hypotheses
5. Create experiments (80/20 split)
6. Execute experiments for 30+ trades
7. Analyze results
8. Adopt if p < 0.05 AND effect > 0.5%
9. Update configuration
10. Monitor performance
```

**Success Criteria:**
- ✅ Can detect patterns after 30+ trades
- ✅ Statistical validation working (p-values, confidence)
- ✅ A/B experiments running correctly
- ✅ Parameter optimization generates reasonable proposals
- ✅ First validated improvement detected and adopted

**Deliverables:**
```
src/
  learning/
    pattern_detector.py       # Pattern discovery
    experiment_engine.py      # A/B testing
    parameter_optimizer.py    # Parameter tuning
    statistical_validator.py  # Statistical rigor
    learning_metrics.py       # Progress tracking
tests/
  unit/test_learning/
  integration/test_learning_workflow.py
```

**Detailed Specification:** See `Implementation_Plan_Modifications_for_Self_Learning_Goal.md` Phase 3

---

### Phase 4: Performance Analysis (Week 8)
**Goal:** Generate insights from trading history  
**Time:** 5-7 days

**What to Build:**

1. **PerformanceAnalyzer Agent** (`src/agents/performance_analyzer.py`)
   - Uses Claude API to analyze results
   - Generates insights and recommendations
   - Identifies winning/losing patterns
   - Suggests parameter changes

**Analysis Dimensions:**
- Overall statistics (win rate, avg ROI, Sharpe)
- Performance by sector
- Performance by OTM range
- Performance by DTE
- Performance by market regime
- Performance by entry timing
- Winner vs loser pattern comparison

**Example Output:**
```
PERFORMANCE ANALYSIS (Last 30 Days)

Overall Stats:
- Trades: 45
- Win Rate: 75.6% (target: 75%)
- Avg ROI: 4.2% (baseline: 3.5%) ✓ +0.7% improvement
- Sharpe Ratio: 1.85

Sector Performance:
- Tech: 85% win rate (18 trades) 📈 Outperforming
- Healthcare: 62% win rate (13 trades) ⚠️ Underperforming
- Financials: 78% win rate (14 trades) ✓ On target

OTM Range Analysis:
- 18-20%: 82% win rate ✓ Sweet spot
- 15-17%: 68% win rate ⚠️ Below target
- 21-23%: 71% win rate

RECOMMENDATIONS:
1. INCREASE Tech allocation from 25% → 35%
   Reason: Consistently outperforming (p=0.02)
   Expected impact: +0.8% ROI

2. REDUCE Healthcare allocation from 20% → 15%
   Reason: Win rate below threshold (p=0.04)
   Expected impact: -0.3% drag avoidance

3. SHIFT OTM range from (15-20%) → (18-22%)
   Reason: 18-20% range shows superior results
   Expected impact: +0.5% ROI
```

**Success Criteria:**
- ✅ Generates meaningful insights from trade history
- ✅ Recommendations are actionable
- ✅ Statistical significance properly calculated
- ✅ Insights align with observed patterns
- ✅ Can run on-demand and automatically (weekly)

**Deliverables:**
```
src/
  agents/
    base_agent.py             # Base AI agent
    performance_analyzer.py   # Performance analysis
  cli/
    analyze_command.py        # CLI for analysis
tests/
  unit/test_performance_analyzer.py
```

**Detailed Specification:** See `AI_Trading_System_Implementation_Plan.md` Phase 4

---

### Phase 5: Continuous Agentic Loop (Weeks 9-10)
**Goal:** 24/7 autonomous operation with continuous learning  
**Time:** 7-10 days

**What to Build:**

1. **AgenticOrchestrator** (`src/agentic/orchestrator.py`)
   - Main control loop (Perceive → Reason → Act → Learn)
   - Decision-making logic
   - Tool/agent selection
   - State management

2. **EventDetector** (`src/agentic/event_detector.py`)
   - Market events (VIX spikes, regime shifts)
   - Portfolio events (stop losses, profit targets)
   - System events (errors, anomalies)

3. **WorkingMemory** (`src/agentic/working_memory.py`)
   - Current goals and plans
   - Recent history (actions, events)
   - Active experiments
   - Discovered patterns

4. **Daemon Process** (`src/agentic/daemon.py`)
   - Background service
   - Scheduling (active during market hours)
   - Health monitoring
   - Auto-restart on failure

**Agentic Loop (Simplified):**
```python
while True:
    # 1. PERCEIVE
    state = get_current_state()
    
    # 2. REASON (AI decides what to do)
    decision = ai_decide_action(state)
    
    # 3. ACT
    if decision.action == "SCAN_FOR_OPPORTUNITIES":
        execute_scan_workflow()
    elif decision.action == "CLOSE_POSITIONS":
        execute_exit_workflow()
    elif decision.action == "ANALYZE_PERFORMANCE":
        execute_analysis_workflow()
    # ... more actions
    
    # 4. LEARN
    if new_trade_outcomes():
        learning_engine.analyze_outcomes()
    
    # 5. SLEEP until next check
    sleep(get_interval())  # 15 min during hours, 1 hr after hours
```

**Success Criteria:**
- ✅ System runs continuously without crashes (24 hours)
- ✅ Responds to events within defined timeframes
- ✅ Makes reasonable decisions based on state
- ✅ Learning from every trade automatically
- ✅ Human can monitor via dashboard
- ✅ Emergency stop works reliably

**Deliverables:**
```
src/
  agentic/
    orchestrator.py           # Main loop
    event_detector.py         # Event monitoring
    working_memory.py         # Context persistence
    daemon.py                 # Background service
  dashboard/                  # Web dashboard (optional)
    app.py
tests/
  integration/test_agentic_loop.py
  e2e/test_full_system.py
```

**Detailed Specification:** See `AI_Trading_System_Implementation_Plan.md` Phase 7

---

### Phases 6-8: Intelligence Agents (Weeks 11+) → OPTIONAL

These phases add sophisticated market intelligence but are **not required** for self-learning functionality:

**Phase 6:** Market Regime Detection (Optional)
**Phase 7:** Event Risk Analysis (Optional)
**Phase 8:** Portfolio Optimization (Optional)

**Recommendation:** Build Phases 0-5 first, validate self-learning works over 3-6 months of paper trading, then decide if intelligence agents are needed.

**Detailed Specification:** See `AI_Trading_System_Implementation_Plan.md` Phases 1-3, 5

---

## Database Schema

### Core Tables

**trades** - Complete trade lifecycle
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    trade_id TEXT UNIQUE,
    
    -- Trade details
    symbol TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration DATE NOT NULL,
    
    -- Entry/Exit
    entry_date TIMESTAMP NOT NULL,
    entry_premium REAL NOT NULL,
    contracts INTEGER NOT NULL,
    exit_date TIMESTAMP,
    exit_premium REAL,
    exit_reason TEXT,
    
    -- Outcomes
    profit_loss REAL,
    profit_pct REAL,
    roi REAL,
    days_held INTEGER,
    
    -- Context
    otm_pct REAL,
    dte INTEGER,
    vix_at_entry REAL,
    market_regime TEXT,
    
    -- Learning
    is_experiment BOOLEAN,
    experiment_id INTEGER,
    
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);
```

**experiments** - A/B test tracking
```sql
CREATE TABLE experiments (
    id INTEGER PRIMARY KEY,
    experiment_id TEXT UNIQUE,
    
    name TEXT NOT NULL,
    parameter_name TEXT NOT NULL,
    control_value TEXT NOT NULL,
    test_value TEXT NOT NULL,
    
    status TEXT DEFAULT 'active',
    start_date TIMESTAMP NOT NULL,
    end_date TIMESTAMP,
    
    -- Results
    control_trades INTEGER,
    test_trades INTEGER,
    p_value REAL,
    effect_size REAL,
    decision TEXT
);
```

**learning_history** - What AI learned when
```sql
CREATE TABLE learning_history (
    id INTEGER PRIMARY KEY,
    
    event_type TEXT NOT NULL,
    event_date TIMESTAMP NOT NULL,
    
    pattern_name TEXT,
    confidence REAL,
    sample_size INTEGER,
    
    parameter_changed TEXT,
    old_value TEXT,
    new_value TEXT,
    
    reasoning TEXT
);
```

**patterns** - Detected patterns
```sql
CREATE TABLE patterns (
    id INTEGER PRIMARY KEY,
    
    pattern_type TEXT NOT NULL,
    pattern_name TEXT NOT NULL,
    
    sample_size INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    avg_roi REAL NOT NULL,
    confidence REAL NOT NULL,
    p_value REAL NOT NULL,
    
    status TEXT DEFAULT 'active',
    date_detected TIMESTAMP NOT NULL
);
```

---

## Testing Requirements

### Unit Tests
Every function/method must have unit tests with >90% coverage:

```python
# Example: test_naked_put.py
def test_should_enter_trade_with_valid_opportunity():
    """Test entry criteria validation."""
    strategy = NakedPutStrategy()
    
    opportunity = {
        "otm_pct": 0.18,
        "premium": 0.45,
        "dte": 10,
        "trend": "uptrend"
    }
    
    assert strategy.should_enter_trade(opportunity) == True

def test_should_reject_trade_below_premium_threshold():
    """Test entry rejection for low premium."""
    strategy = NakedPutStrategy()
    
    opportunity = {
        "otm_pct": 0.18,
        "premium": 0.25,  # Below 0.30 minimum
        "dte": 10,
        "trend": "uptrend"
    }
    
    assert strategy.should_enter_trade(opportunity) == False
```

### Integration Tests
Test component interactions:

```python
# Example: test_autonomous_trading.py
def test_full_trade_workflow(ibkr_client, database):
    """Test complete autonomous trade execution."""
    # Setup
    strategy = NakedPutStrategy()
    executor = OrderExecutor(ibkr_client)
    
    # Find opportunity
    opportunities = strategy.find_opportunities()
    assert len(opportunities) > 0
    
    # Execute trade
    trade = executor.place_order(opportunities[0])
    assert trade.status == "FILLED"
    
    # Verify database logging
    saved_trade = database.query(Trade).filter_by(
        trade_id=trade.id
    ).first()
    assert saved_trade is not None
    assert saved_trade.symbol == opportunities[0].symbol
```

### End-to-End Tests
Test complete workflows:

```python
# Example: test_full_system.py
def test_self_learning_cycle(system):
    """Test that system learns from outcomes."""
    # Execute 50 trades
    system.run_for_n_trades(50)
    
    # Verify patterns detected
    patterns = system.learning_engine.get_patterns()
    assert len(patterns) > 0
    
    # Verify experiments created
    experiments = system.learning_engine.get_active_experiments()
    assert len(experiments) > 0
    
    # Verify at least one parameter changed
    history = system.learning_engine.get_learning_history()
    assert any(h.event_type == "parameter_adjusted" for h in history)
```

---

## Validation & Approval Checkpoints

### After Each Phase:

1. **Code Quality Gates**
   ```bash
   # All must pass
   black src/ tests/           # Code formatting
   ruff check src/ tests/      # Linting
   mypy src/                   # Type checking
   pytest --cov=src --cov-report=term-missing
   ```

2. **Functional Validation**
   - Manually test key workflows
   - Verify outputs match expectations
   - Check error handling works

3. **Checkpoint Report**
   Create markdown report:
   ```markdown
   ## Phase X Checkpoint
   
   **Completed:** YYYY-MM-DD
   **Components:** [list]
   **Tests:** X/Y passing (Z% coverage)
   **Known Issues:** [list or "none"]
   **Ready for Approval:** YES/NO
   ```

4. **Human Approval**
   - Present checkpoint report
   - Demo functionality
   - Request proceed/revise decision
   - Address feedback if revision needed

---

## Risk Management & Safety

### Circuit Breakers
```python
CIRCUIT_BREAKERS = {
    "daily_loss": {
        "threshold": -0.02,      # -2% daily loss
        "action": "HALT_TRADING",
        "notification": "IMMEDIATE"
    },
    "position_loss": {
        "threshold": -500,        # $500 per position
        "action": "EXIT_POSITION",
        "notification": "EMAIL"
    },
    "api_errors": {
        "threshold": 5,           # 5 errors in 1 hour
        "action": "HALT_TRADING",
        "notification": "SMS"
    }
}
```

### Emergency Stop
- Accessible via CLI: `python -m src.cli.main emergency-stop`
- Accessible via dashboard: Big red button
- Via SMS: Text "STOP TRADING" to registered number
- Automatic triggers: Circuit breakers

### Audit Trail
Every action logged:
```python
audit_logger.log({
    "timestamp": now(),
    "action": "ORDER_PLACED",
    "actor": "orchestrator",
    "context": {...},
    "decision": {...},
    "outcome": {...}
})
```

---

## Success Metrics

### Phase 0-2 (Foundation + Execution)
- System executes baseline strategy autonomously
- 20+ successful trades in paper trading
- No risk limit violations
- All safety mechanisms working

### Phase 3-5 (Learning + Continuous)
- Patterns detected from trade history
- First validated improvement adopted
- System running continuously 24/7
- Observable learning behavior

### 3-6 Months Paper Trading
- 100-200+ trades executed
- Win rate improved 5%+ over baseline
- ROI improved 1%+ over baseline
- Self-learning demonstrably working
- Ready for careful live deployment

---

## Deployment Path

### Development → Paper Trading → Live

**Months 1-3: Development**
- Build Phases 0-5
- Test in paper trading
- Validate core functionality

**Months 4-9: Extended Paper Trading**
- Run continuously in paper account
- Collect 100-200 trades minimum
- Validate learning improvements
- Build confidence in system

**Months 10+: Live Trading (Optional)**
- Start with 10-20% of intended capital
- Keep paper trading running in parallel
- Monitor closely for differences
- Scale up gradually over 3-6 months

---

## Development Priorities

### Must Have (Core)
- ✅ Phases 0-5 (Foundation → Continuous Loop)
- ✅ All safety mechanisms
- ✅ Complete test coverage
- ✅ Comprehensive logging
- ✅ Self-learning capabilities

### Nice to Have (Optional)
- Market regime intelligence (Phase 6)
- Event risk analysis (Phase 7)
- Portfolio optimization (Phase 8)
- Web dashboard
- Mobile notifications
- Advanced visualizations

### Don't Need (Yet)
- Multi-strategy support
- Live trading (use paper for 6+ months)
- Cloud deployment
- High-frequency capabilities
- Complex derivatives

---

## Key Constraints

### Safety Constraints
- Always paper trading during development
- Always validate before executing
- Always log everything
- Always enforce risk limits
- Always fail gracefully

### Learning Constraints
- Minimum 30 trades before pattern detection
- p < 0.05 for statistical significance
- Effect size > 0.5% for adoption
- Maximum 1 parameter change per month
- Maximum 20% parameter shift at once

### Operational Constraints
- Only trade during market hours (9:30-4:00 ET)
- Maximum 10 new positions per day
- Must maintain emergency stop access
- Must maintain complete audit trail

---

## Getting Started

### For Claude Code:

1. **Initialize Project**
   ```bash
   # Verify prerequisites completed
   # Create project structure
   # Initialize database
   # Setup configuration
   ```

2. **Start Phase 0**
   ```bash
   # Read Phase 0 specification
   # Create task breakdown
   # Implement incrementally
   # Test continuously
   # Validate completion criteria
   # Request approval before Phase 1
   ```

3. **Continue Through Phases**
   - Complete Phase 0 → Approval → Phase 1
   - Complete Phase 1 → Approval → Phase 2
   - And so on...

4. **Checkpoint Protocol**
   - End of each step: quick validation
   - End of each phase: full checkpoint + approval
   - Any blockers: request human input immediately

---

## Final Notes

- This is a **living specification** - may evolve based on learnings
- **Safety first** - this will trade real money eventually
- **Test everything** - no untested code in production paths
- **Document as you go** - future you will thank you
- **Ask when unsure** - better to clarify than assume

**Remember:** The goal is a system that gets better over time by learning from actual market feedback. Quality and safety matter more than speed.

---

**Specification Version:** 1.0  
**Created:** January 2025  
**Last Updated:** January 2025  
**Status:** Ready for Phase 0 implementation

---

## Quick Reference: Phase Checklist

- [ ] Phase 0: Foundation (Week 1)
- [ ] Phase 1: Baseline Strategy (Weeks 2-3)
- [ ] Phase 2: Autonomous Execution (Weeks 4-5)
- [ ] Phase 3: Learning Engine (Weeks 6-7)
- [ ] Phase 4: Performance Analysis (Week 8)
- [ ] Phase 5: Continuous Loop (Weeks 9-10)
- [ ] Extended Paper Trading (Months 4-9)
- [ ] Optional: Intelligence Agents (Weeks 11+)
- [ ] Optional: Live Trading (Months 10+)
