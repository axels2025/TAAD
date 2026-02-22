# CLAUDE.md - AI Development Assistant Configuration

## Project Overview

**Project Name:** Self-Learning Agentic AI Trading System  
**Primary Goal:** Build an autonomous trading system that executes a proven naked put options strategy, learns from outcomes, and continuously improves performance over time.

**Development Model:** Autonomous step-by-step implementation following a detailed specification. Claude Code will implement each phase, validate success criteria, and only proceed to the next phase after validation passes.

---

## System Prompt for Claude Code

You are an expert Python developer tasked with building a self-learning agentic AI trading system. Your role is to:

1. **Read the specification** (`docs/SPEC_TRADING_SYSTEM.md`) carefully before starting any work
2. **Implement each phase sequentially** - never skip ahead
3. **Validate completion criteria** after each step before proceeding
4. **Write production-quality code** following the standards in this document
5. **Create comprehensive tests** for all components
6. **Document your work** as you go
7. **Ask for human approval** at major phase checkpoints
8. **Self-correct** if validation fails

### Key Principles

- **Safety First:** This system will eventually trade real money - write defensive code
- **Test Everything:** No code ships without tests
- **Log Everything:** Rich logging for debugging and learning
- **Fail Gracefully:** Never crash, always provide fallback behavior
- **Be Explicit:** No magic numbers, clear variable names, extensive comments
- **Modular Design:** Small, focused functions/classes with single responsibilities

---

## Tech Stack

### Core Language & Framework
```yaml
Language: Python 3.11+
Package Manager: pip
Virtual Environment: venv
Dependency Management: requirements.txt
```

### Key Libraries

#### Trading & Market Data
```python
ib_insync==0.9.86          # Interactive Brokers API (primary)
pandas==2.2.0              # Data manipulation
numpy==1.26.4              # Numerical computation
```

#### AI & Machine Learning
```python
anthropic==0.76.0          # Claude API for AI agents
scikit-learn==1.4.0        # Statistical analysis, pattern detection
scipy==1.12.0              # Statistical tests (t-tests, p-values)
```

#### Database & Storage
```python
sqlalchemy==2.0.25         # ORM for database operations
alembic==1.13.1            # Database migrations
sqlite3                    # Built-in (for development/paper trading)
psycopg2-binary==2.9.9     # PostgreSQL (optional, for production)
```

#### Utilities
```python
python-dotenv==1.0.0       # Environment variable management
pydantic==2.5.0            # Data validation
loguru==0.7.2              # Better logging
schedule==1.2.0            # Job scheduling
typer==0.9.0               # CLI framework
rich==13.7.0               # Beautiful terminal output
```

#### Testing & Quality
```python
pytest==7.4.3              # Testing framework
pytest-cov==4.1.0          # Code coverage
pytest-asyncio==0.21.1     # Async testing
black==23.12.1             # Code formatting
ruff==0.1.9                # Fast linting
mypy==1.8.0                # Type checking
```

#### Development Tools
```python
ipython==8.19.0            # Interactive shell
jupyter==1.0.0             # Notebooks (optional for analysis)
```

### Database Schema

**Primary Database:** SQLite (development), PostgreSQL (production optional)

**Schema Overview:**
```sql
-- Core tables
trades              -- All trade entries and exits
experiments         -- A/B test tracking
learning_history    -- What AI learned when
config_versions     -- Parameter evolution over time
patterns            -- Detected patterns with confidence scores
market_snapshots    -- Market conditions at trade time

-- Supporting tables
positions           -- Current open positions
audit_log           -- All AI decisions and reasoning
risk_events         -- Limit breaches, stop losses, etc.
performance_metrics -- Daily/weekly/monthly statistics
```

---

## Project Structure

```
trading_agent/
│
├── .env                          # Environment variables (not in git)
├── .env.example                  # Template for .env
├── .gitignore                    # Git ignore rules
├── requirements.txt              # Python dependencies
├── pyproject.toml                # Project metadata & tool config
├── README.md                     # User-facing documentation
├── CLAUDE.md                     # This file - AI assistant config
├── SPEC_TRADING_SYSTEM.md        # Complete specification
│
├── config/                       # Configuration files
│   └── daily_spx_options.yaml    # NakedTrader strategy config
│
├── src/                          # Source code
│   ├── __init__.py
│   │
│   ├── config/                   # Configuration management
│   │   ├── __init__.py
│   │   ├── base.py               # Base configuration class
│   │   ├── baseline_strategy.py  # User's proven strategy
│   │   ├── learning_config.py    # Learning parameters
│   │   └── ibkr_config.py        # IBKR connection settings
│   │
│   ├── strategies/               # Trading strategies
│   │   ├── __init__.py
│   │   ├── base.py               # Strategy interface
│   │   └── naked_put.py          # Baseline naked put strategy
│   │
│   ├── execution/                # Trade execution
│   │   ├── __init__.py
│   │   ├── order_executor.py     # Place orders via IBKR
│   │   ├── position_monitor.py   # Track open positions
│   │   ├── exit_manager.py       # Handle exits
│   │   └── risk_governor.py      # Enforce risk limits
│   │
│   ├── learning/                 # Self-learning engine
│   │   ├── __init__.py
│   │   ├── pattern_detector.py   # Find what works
│   │   ├── experiment_engine.py  # A/B testing
│   │   ├── parameter_optimizer.py # Tune parameters
│   │   ├── statistical_validator.py # Ensure significance
│   │   └── learning_metrics.py   # Track learning progress
│   │
│   ├── agents/                   # AI agents
│   │   ├── __init__.py
│   │   ├── base_agent.py         # Base AI agent class
│   │   ├── performance_analyzer.py # Analyze results
│   │   └── market_intelligence.py # Optional regime detection
│   │
│   ├── data/                     # Data layer
│   │   ├── __init__.py
│   │   ├── database.py           # Database connection
│   │   ├── models.py             # SQLAlchemy models
│   │   ├── repositories.py       # Data access layer
│   │   └── migrations/           # Alembic migrations
│   │
│   ├── nakedtrader/              # Daily SPX/XSP/SPY put selling
│   │   ├── __init__.py
│   │   ├── config.py             # YAML + Pydantic config
│   │   ├── chain.py              # Index option chain + Greeks
│   │   ├── strike_selector.py    # Delta-based strike selection
│   │   ├── order_manager.py      # Bracket order placement
│   │   ├── trade_recorder.py     # Trade DB recording
│   │   ├── watcher.py            # Position monitoring
│   │   └── workflow.py           # Daily workflow orchestrator
│   │
│   ├── tools/                    # Utility tools
│   │   ├── __init__.py
│   │   ├── screener.py           # Stock screening
│   │   ├── options_finder.py     # Options chain search
│   │   ├── data_aggregator.py    # Market data collection
│   │   └── ibkr_client.py        # IBKR wrapper
│   │
│   └── cli/                      # Command-line interface
│       ├── __init__.py
│       └── main.py               # Main CLI entry point
│
├── tests/                        # Test suite
│   ├── __init__.py
│   ├── conftest.py               # Pytest fixtures
│   ├── unit/                     # Unit tests
│   │   ├── test_strategies.py
│   │   ├── test_execution.py
│   │   ├── test_learning.py
│   │   └── ...
│   ├── integration/              # Integration tests
│   │   ├── test_ibkr.py
│   │   ├── test_database.py
│   │   └── ...
│   └── e2e/                      # End-to-end tests
│       └── test_full_workflow.py
│
├── scripts/                      # Utility scripts
│   ├── setup_database.py         # Initialize database
│   ├── seed_test_data.py         # Create test data
│   ├── backfill_trades.py        # Import historical trades
│   └── reset_environment.py      # Clean reset for development
│
├── data/                         # Data directory (not in git)
│   ├── databases/                # SQLite databases
│   │   ├── trades.db
│   │   ├── experiments.db
│   │   └── learning.db
│   ├── cache/                    # Cached market data
│   └── exports/                  # Data exports
│
├── logs/                         # Log files (not in git)
│   ├── app.log
│   ├── trades.log
│   ├── learning.log
│   └── errors.log
│
└── docs/                         # Documentation
    ├── architecture.md           # System architecture
    ├── database_schema.md        # Database documentation
    ├── api_reference.md          # API documentation
    └── deployment.md             # Deployment guide
```

---

## Core Commands

### Environment Setup
```bash
# Create virtual environment
python3.11 -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Setup environment variables
cp .env.example .env
# Edit .env with your API keys

# Initialize database
python scripts/setup_database.py

# Run migrations
alembic upgrade head
```

### Development Commands
```bash
# Run tests
pytest                              # All tests
pytest tests/unit                   # Unit tests only
pytest tests/integration            # Integration tests
pytest --cov=src --cov-report=html  # With coverage

# Code quality
black src/ tests/                   # Format code
ruff check src/ tests/              # Lint code
mypy src/                           # Type check

# Run application
python -m src.cli.main --help       # Show CLI help
python -m src.cli.main scan         # Run stock scanner
python -m src.cli.main trade        # Execute trading workflow
python -m src.cli.main analyze      # Analyze performance

# Naked Put Selling (daily SPX/XSP/SPY)
nakedtrader sell XSP --dry-run             # Sell a daily put (dry run)
nakedtrader sell XSP --live --yes          # Sell a daily put (paper trading)
nakedtrader sell-watch                     # Monitor open naked put positions
nakedtrader sell-status                    # Show naked put trade history
```

### Database Commands
```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1

# Reset database (careful!)
python scripts/reset_environment.py
```

---

## Coding Standards

### Python Style Guide

**Follow PEP 8** with these specific rules:

```python
# Line length: 88 characters (Black default)
# Indentation: 4 spaces
# Quotes: Double quotes for strings
# Imports: Organized in groups (stdlib, third-party, local)

# Example:
import os
from datetime import datetime
from typing import List, Optional

import pandas as pd
from anthropic import Anthropic

from src.config.base import Config
from src.data.models import Trade
```

### Type Hints (Required)

```python
# Always use type hints for function signatures
def calculate_profit(
    entry_price: float,
    exit_price: float,
    contracts: int
) -> dict[str, float]:
    """Calculate profit/loss for a trade.
    
    Args:
        entry_price: Premium received at entry
        exit_price: Premium paid at exit
        contracts: Number of contracts
        
    Returns:
        Dictionary with profit, profit_pct, roi
    """
    profit = (entry_price - exit_price) * contracts * 100
    profit_pct = (entry_price - exit_price) / entry_price
    
    return {
        "profit": profit,
        "profit_pct": profit_pct,
        "roi": profit_pct
    }
```

### Error Handling

```python
# Use specific exceptions
class TradingSystemError(Exception):
    """Base exception for trading system"""
    pass

class InsufficientDataError(TradingSystemError):
    """Not enough data for analysis"""
    pass

class RiskLimitExceededError(TradingSystemError):
    """Risk limit would be exceeded"""
    pass

# Always handle errors gracefully
def execute_trade(trade: Trade) -> bool:
    """Execute a trade with comprehensive error handling."""
    try:
        # Validate inputs
        if not self.validate_trade(trade):
            raise ValueError("Invalid trade parameters")
        
        # Check risk limits
        if not self.risk_governor.check_limits(trade):
            raise RiskLimitExceededError(
                f"Trade would exceed risk limits: {trade}"
            )
        
        # Execute
        result = self.ibkr_client.place_order(trade)
        
        # Log success
        logger.info(f"Trade executed successfully: {trade.id}")
        return True
        
    except RiskLimitExceededError as e:
        logger.warning(f"Trade rejected due to risk limits: {e}")
        return False
        
    except Exception as e:
        logger.error(f"Unexpected error executing trade: {e}", exc_info=True)
        return False
```

### Logging Standards

```python
from loguru import logger

# Use structured logging with context
logger.info(
    "Trade executed",
    extra={
        "trade_id": trade.id,
        "symbol": trade.symbol,
        "contracts": trade.contracts,
        "premium": trade.premium
    }
)

# Log levels:
# - DEBUG: Detailed diagnostic info
# - INFO: General operational events
# - WARNING: Warning messages (e.g., approaching limits)
# - ERROR: Error events (with traceback)
# - CRITICAL: Critical errors requiring immediate attention
```

### Testing Standards

```python
# Every function needs tests
# Use descriptive test names
# Follow Arrange-Act-Assert pattern

def test_calculate_profit_for_winning_trade():
    """Test profit calculation when trade is profitable."""
    # Arrange
    entry_price = 0.50
    exit_price = 0.25
    contracts = 5
    
    # Act
    result = calculate_profit(entry_price, exit_price, contracts)
    
    # Assert
    assert result["profit"] == 125.0  # (0.50 - 0.25) * 5 * 100
    assert result["profit_pct"] == 0.50
    assert result["roi"] == 0.50

def test_calculate_profit_for_losing_trade():
    """Test profit calculation when trade loses money."""
    # Arrange
    entry_price = 0.30
    exit_price = 0.60
    contracts = 5
    
    # Act
    result = calculate_profit(entry_price, exit_price, contracts)
    
    # Assert
    assert result["profit"] == -150.0
    assert result["profit_pct"] == -1.0
    assert result["roi"] == -1.0
```

### Documentation Standards

```python
# Use docstrings for all public functions/classes
# Follow Google docstring format

class PatternDetector:
    """Detects profitable patterns in trading history.
    
    The PatternDetector analyzes trade outcomes to identify
    statistically significant patterns across multiple dimensions:
    OTM range, sectors, DTE, market conditions, etc.
    
    Attributes:
        min_samples: Minimum trades needed for pattern validity
        confidence_threshold: Minimum confidence score (0.0-1.0)
        
    Example:
        >>> detector = PatternDetector(min_samples=30)
        >>> patterns = detector.analyze_trades(trade_history)
        >>> for pattern in patterns:
        ...     print(f"{pattern.name}: {pattern.confidence}")
    """
    
    def __init__(
        self,
        min_samples: int = 30,
        confidence_threshold: float = 0.95
    ):
        """Initialize pattern detector.
        
        Args:
            min_samples: Minimum number of trades for valid pattern
            confidence_threshold: Minimum statistical confidence
        """
        self.min_samples = min_samples
        self.confidence_threshold = confidence_threshold
```

---

## Configuration Management

### Environment Variables (.env)

```bash
# IBKR Configuration
IBKR_HOST=127.0.0.1
IBKR_PORT=7497                    # 7497 for paper, 7496 for live
IBKR_CLIENT_ID=1
IBKR_ACCOUNT=DU123456             # Your paper trading account

# Anthropic API
ANTHROPIC_API_KEY=sk-ant-xxxxx   # Your Claude API key

# Database
DATABASE_URL=sqlite:///data/databases/trades.db
# DATABASE_URL=postgresql://user:pass@localhost/trading  # For production

# Logging
LOG_LEVEL=INFO                    # DEBUG, INFO, WARNING, ERROR
LOG_FILE=logs/app.log

# Trading Configuration
PAPER_TRADING=true                # Always true initially
MAX_DAILY_LOSS=-0.02              # -2% circuit breaker
MAX_POSITION_SIZE=5000            # Max $ per position

# Learning Configuration
LEARNING_ENABLED=true
MIN_TRADES_FOR_LEARNING=30        # Before patterns are detected
EXPERIMENT_ALLOCATION=0.20        # 20% to experiments
```

### Configuration Classes

```python
# src/config/base.py
from pydantic import BaseModel, Field
from typing import Optional

class BaseConfig(BaseModel):
    """Base configuration with validation."""
    
    # IBKR Settings
    ibkr_host: str = Field(default="127.0.0.1")
    ibkr_port: int = Field(default=7497, ge=1, le=65535)
    ibkr_client_id: int = Field(default=1)
    
    # Trading Settings
    paper_trading: bool = Field(default=True)
    max_daily_loss: float = Field(default=-0.02, ge=-1.0, le=0.0)
    
    # Learning Settings
    learning_enabled: bool = Field(default=True)
    min_trades_for_learning: int = Field(default=30, ge=10)
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
```

---

## Database Design

### Core Tables Schema

```sql
-- trades: Complete trade lifecycle
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    
    -- Trade details
    symbol TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration DATE NOT NULL,
    option_type TEXT DEFAULT 'PUT',
    
    -- Entry
    entry_date TIMESTAMP NOT NULL,
    entry_premium REAL NOT NULL,
    contracts INTEGER NOT NULL,
    
    -- Exit
    exit_date TIMESTAMP,
    exit_premium REAL,
    exit_reason TEXT,
    
    -- P&L
    profit_loss REAL,
    profit_pct REAL,
    roi REAL,
    days_held INTEGER,
    
    -- Strategy parameters at entry
    otm_pct REAL NOT NULL,
    dte INTEGER NOT NULL,
    config_version INTEGER,
    
    -- Market context
    vix_at_entry REAL,
    vix_at_exit REAL,
    spy_price_at_entry REAL,
    market_regime TEXT,
    
    -- Experiment tracking
    is_experiment BOOLEAN DEFAULT FALSE,
    experiment_id INTEGER,
    
    -- AI context
    ai_confidence REAL,
    ai_reasoning TEXT,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
);

-- experiments: A/B test tracking
CREATE TABLE experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT UNIQUE NOT NULL,
    
    -- Hypothesis
    name TEXT NOT NULL,
    description TEXT,
    parameter_name TEXT NOT NULL,
    control_value TEXT NOT NULL,
    test_value TEXT NOT NULL,
    
    -- Status
    status TEXT DEFAULT 'active',  -- active, completed, rejected
    start_date TIMESTAMP NOT NULL,
    end_date TIMESTAMP,
    
    -- Results
    control_trades INTEGER DEFAULT 0,
    test_trades INTEGER DEFAULT 0,
    p_value REAL,
    effect_size REAL,
    decision TEXT,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- learning_history: What AI learned when
CREATE TABLE learning_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Learning event
    event_type TEXT NOT NULL,  -- pattern_detected, parameter_adjusted, etc.
    event_date TIMESTAMP NOT NULL,
    
    -- Details
    pattern_name TEXT,
    confidence REAL,
    sample_size INTEGER,
    
    -- Change made
    parameter_changed TEXT,
    old_value TEXT,
    new_value TEXT,
    
    -- Justification
    reasoning TEXT,
    expected_improvement REAL,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- patterns: Detected patterns with confidence
CREATE TABLE patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Pattern identification
    pattern_type TEXT NOT NULL,  -- sector, otm_range, dte, timing, etc.
    pattern_name TEXT NOT NULL,
    
    -- Statistics
    sample_size INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    avg_roi REAL NOT NULL,
    confidence REAL NOT NULL,
    p_value REAL NOT NULL,
    
    -- Context
    market_regime TEXT,
    date_detected TIMESTAMP NOT NULL,
    
    -- Status
    status TEXT DEFAULT 'active',  -- active, invalidated, superseded
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Development Workflow

### Phase-Based Development

1. **Read Specification**
   - Review `docs/SPEC_TRADING_SYSTEM.md`
   - Understand current phase requirements
   - Identify success criteria

2. **Plan Implementation**
   - Break phase into small steps
   - Identify dependencies
   - Create task checklist

3. **Implement Step-by-Step**
   - Write code following standards
   - Add inline documentation
   - Create tests alongside code

4. **Validate Success Criteria**
   - Run all tests (must pass)
   - Check code quality (Black, Ruff, MyPy)
   - Verify functional requirements
   - Create validation report

5. **Request Human Approval**
   - Present completion summary
   - Show validation results
   - Request proceed/revise decision

6. **Proceed or Revise**
   - If approved: commit and move to next step
   - If revise needed: fix issues and re-validate

### Git Commit Standards

```bash
# Commit message format:
# [PHASE-X.Y] Brief description
#
# Detailed explanation of changes
# - What was added/changed
# - Why it was needed
# - Any important notes

# Example:
git commit -m "[PHASE-1.1] Implement baseline naked put strategy

- Created NakedPutStrategy class with entry/exit rules
- Added configuration for OTM range, premium, DTE
- Implemented strategy validation against user criteria
- Added unit tests with 95% coverage

Validation: All tests pass, strategy matches manual approach"
```

---

## Quality Gates (Must Pass Before Proceeding)

### Code Quality
- ✅ Black formatting applied (no changes when run)
- ✅ Ruff linting passes with no errors
- ✅ MyPy type checking passes
- ✅ No hardcoded secrets or API keys in code

### Testing
- ✅ All existing tests pass
- ✅ New code has >90% test coverage
- ✅ Unit tests for all functions
- ✅ Integration tests for workflows
- ✅ Edge cases covered

### Documentation
- ✅ All public functions have docstrings
- ✅ Complex logic has inline comments
- ✅ README updated if needed
- ✅ API documentation current

### Functionality
- ✅ Meets phase success criteria
- ✅ No regressions in existing functionality
- ✅ Error handling comprehensive
- ✅ Logging appropriate

---

## Checkpoint Protocol

At the end of each major step, create a checkpoint report:

```markdown
## Checkpoint Report: Phase X - Step Y

**Date:** YYYY-MM-DD
**Step:** [Description]
**Status:** [COMPLETE | BLOCKED | NEEDS_REVISION]

### Implemented
- [ ] Component A
- [ ] Component B
- [ ] Tests

### Validation Results
- Code Quality: [PASS/FAIL]
- Tests: [X/Y passing]
- Coverage: [X%]
- Functional: [PASS/FAIL]

### Issues Found
1. [Issue description and resolution]

### Next Steps
1. [Next action]
2. [Following action]

### Human Approval Required?
[YES/NO] - [Reason if yes]
```

---

## Emergency Procedures

### If Something Breaks
1. **STOP** - Don't proceed to next step
2. **Document** the error fully (logs, traces, context)
3. **Diagnose** root cause
4. **Fix** the issue
5. **Re-validate** all quality gates
6. **Report** what broke and how it was fixed

### If Stuck
1. **Document** what you tried
2. **Identify** knowledge gap or blocker
3. **Request human input** with specific questions
4. **Don't guess** - ask rather than implement incorrectly

### Rollback Procedure
```bash
# If phase is broken beyond repair
git log --oneline  # Find last good commit
git reset --hard COMMIT_HASH
git clean -fd  # Remove untracked files
# Report rollback reason and request guidance
```

---

## Success Criteria Reference

### Phase 0: Foundation
- ✅ IBKR paper trading connection working
- ✅ Database initialized with schema
- ✅ Configuration system functional
- ✅ Logging capturing events
- ✅ All tests pass

### Phase 1: Baseline Strategy
- ✅ User's strategy perfectly encoded
- ✅ Strategy identifies same trades as manual approach
- ✅ Entry/exit rules match user's criteria
- ✅ Validation with historical data passes

### Phase 2: Autonomous Execution
- ✅ Orders placed successfully in paper trading
- ✅ Positions monitored in real-time
- ✅ Exits execute at profit targets / stop losses
- ✅ Risk limits enforced (no violations)
- ✅ 20+ successful autonomous trades

### Phase 3: Learning Engine
- ✅ Patterns detected from trade history
- ✅ Statistical validation working correctly
- ✅ A/B experiments running successfully
- ✅ Parameter optimization generating proposals

### Phase 4: Performance Analysis
- ✅ Performance analyzer generating insights
- ✅ Weekly review workflow functional
- ✅ Hypothesis testing working
- ✅ Reports actionable and accurate

### Phase 5: Continuous Loop
- ✅ System runs continuously without crashes
- ✅ Learning from every trade
- ✅ Adaptive behavior observable
- ✅ Human oversight mechanisms working

---

## Notes for Claude Code

1. **Always read the spec first** - Don't start coding until you understand the full context
2. **Small steps** - Implement incrementally, not all at once
3. **Test as you go** - Don't wait until the end to test
4. **Ask when unsure** - Better to ask than implement incorrectly
5. **Document decisions** - Explain why you chose a particular approach
6. **Validate constantly** - Run tests frequently, not just at checkpoints
7. **Be conservative** - This will handle real money eventually - safety first

---

**Document Version:** 1.0  
**Last Updated:** January 2025  
**Status:** Ready for autonomous development
