# TAAD - The Autonomous Agentic Trading Daemon

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1800%2B-brightgreen.svg)]()
[![Status](https://img.shields.io/badge/status-active%20development-orange.svg)]()
[![Built with Claude](https://img.shields.io/badge/built%20with-Claude%20Code-blueviolet.svg)](https://claude.ai/claude-code)

A self-learning autonomous trading system that sells naked put options, learns from outcomes, and continuously improves. Built with Python, Interactive Brokers, and Claude AI.

> **Status: Under very active development.** This project is being built and iterated on daily. APIs, database schemas, and behavior may change without notice. Use at your own risk.

> **Disclaimer:** This software is for educational and research purposes. Trading options involves substantial risk of loss. The authors are not responsible for any financial losses incurred through the use of this software. Always paper trade first.

<p align="center">
  <img src="docs/images/dashboard-overview.png" alt="TAAD Dashboard" width="800">
</p>

---

## What It Does

TAAD is an event-driven daemon that:

1. **Scans** for naked put opportunities using delta-based strike selection
2. **Stages** candidates for human review or autonomous execution
3. **Reasons** about market conditions using Claude AI every 15 minutes
4. **Executes** trades through Interactive Brokers with bracket orders (profit target + stop loss)
5. **Learns** from every trade outcome — detecting patterns, running A/B experiments, and optimizing parameters over time
6. **Self-corrects** by adjusting strategy parameters based on statistically validated improvements

The system implements a proven strategy: selling short-dated (0-7 DTE) put options on indices (SPX, XSP, SPY) and quality stocks, targeting specific delta ranges and premium levels.

## Architecture

```
                    +-----------------+
                    |  Event Bus      |  MARKET_OPEN, SCHEDULED_CHECK,
                    |  (time-based)   |  MARKET_CLOSE, EOD_REFLECTION
                    +--------+--------+
                             |
                    +--------v--------+
                    |  Working Memory |  Positions, decisions, anomalies,
                    |  (state store)  |  market context, reflections
                    +--------+--------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v------+  +----v--------+
     | Guardrails |  | Claude      |  | Autonomy    |
     | (Phase 6)  |  | Reasoning   |  | Governor    |
     | Pre/post   |  | Engine      |  | (L1-L4)     |
     +--------+---+  +------+------+  +----+--------+
              |              |              |
              +--------------+--------------+
                             |
                    +--------v--------+
                    | Action Executor |  MONITOR_ONLY, EXECUTE_TRADES,
                    | (order mgmt)   |  CLOSE_POSITION, ADJUST_STOPS
                    +--------+--------+
                             |
                    +--------v--------+
                    |  IBKR Client    |  Orders, quotes, positions
                    |  (ib_insync)    |  via TWS/Gateway
                    +-----------------+
```

### Key Components

- **Daemon** (`src/agentic/daemon.py`): Main event loop with 8-step pipeline
- **Reasoning Engine** (`src/agentic/reasoning_engine.py`): Claude-powered decision making
- **Working Memory** (`src/agentic/working_memory.py`): Persistent state across events
- **Guardrails** (`src/agentic/guardrails/`): Context validation, output validation, execution gates, numerical grounding
- **Learning Loop** (`src/agentic/learning_loop.py`): Pattern detection, experiments, parameter optimization
- **Scanner** (`src/services/ibkr_scanner.py`): IBKR market scanner integration
- **NakedTrader** (`src/nakedtrader/`): Daily put-selling workflow with bracket orders
- **Dashboard** (`src/agentic/dashboard_api.py`): Flask web UI for monitoring decisions

### Autonomy Levels

| Level | Behavior |
|-------|----------|
| L1 | Monitor only — all actions require human approval |
| L2 | Can close positions and adjust stops autonomously |
| L3 | Can execute pre-approved (staged) trades |
| L4 | Full autonomy within risk limits |

## Screenshots

<details>
<summary><strong>AI Decision Log</strong> — Every 15 minutes, Claude reasons about market conditions and open positions</summary>
<br>
<img src="docs/images/decisions-log.png" alt="AI Decisions" width="800">
</details>

<details>
<summary><strong>Staged Candidates</strong> — Scanned opportunities awaiting human review or auto-execution</summary>
<br>
<img src="docs/images/staged-candidates.png" alt="Staged Candidates" width="800">
</details>

<details>
<summary><strong>Option Scanner</strong> — Configurable scanner with delta, premium, and budget filters</summary>
<br>
<img src="docs/images/scanner.png" alt="Option Scanner" width="800">
</details>

<details>
<summary><strong>Settings</strong> — Claude model, autonomy levels, and risk parameters</summary>
<br>
<img src="docs/images/settings.png" alt="Settings" width="800">
</details>

## Prerequisites

- **Python 3.11+**
- **Interactive Brokers** account with TWS or IB Gateway
  - Paper trading account recommended for development
  - API connections enabled (Edit > Global Configuration > API > Settings)
- **Anthropic API key** for Claude reasoning ([console.anthropic.com](https://console.anthropic.com))

## Installation

```bash
# Clone the repository
git clone https://github.com/axels2025/trading_agent.git
cd trading_agent

# Create and activate virtual environment
python3.11 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment template and fill in your credentials
cp .env.example .env
# Edit .env with your IBKR and Anthropic settings

# Initialize the database
python scripts/setup_database.py
alembic upgrade head
```

### Environment Variables

Create a `.env` file in the project root:

```bash
# IBKR Connection
IBKR_HOST=127.0.0.1
IBKR_PORT=7497              # 7497 = paper trading, 7496 = live
IBKR_CLIENT_ID=1
IBKR_ACCOUNT=DU123456       # Your paper trading account ID

# Anthropic API
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Database
DATABASE_URL=sqlite:///data/databases/trades.db

# Safety
PAPER_TRADING=true
LOG_LEVEL=INFO
```

## Usage

### Start the Daemon

```bash
# Start the autonomous trading daemon
python -m src.cli.main daemon start

# Check daemon status
python -m src.cli.main daemon status

# Stop the daemon
python -m src.cli.main daemon stop
```

### Manual Trading Commands

```bash
# Scan for opportunities
python -m src.cli.main scanner run

# Sell a naked put (dry run)
python -m src.cli.main nakedtrader sell XSP --dry-run

# Sell a naked put (paper trading)
python -m src.cli.main nakedtrader sell XSP --live --yes

# Monitor open positions
python -m src.cli.main nakedtrader sell-watch

# View trade history
python -m src.cli.main nakedtrader sell-status

# Analyze performance
python -m src.cli.main analyze --ai
```

### Dashboard

```bash
# Start the web dashboard (default: http://localhost:5100)
python -m src.cli.main daemon start  # Dashboard starts with daemon
```

The dashboard shows:
- Recent AI decisions with full reasoning
- Open positions and P&L
- Event history and system health
- Staged candidates awaiting execution

## Development

### Running Tests

```bash
# All tests
pytest

# Unit tests only
pytest tests/unit

# With coverage
pytest --cov=src --cov-report=html

# Specific test file
pytest tests/unit/test_pattern_detector.py -v
```

### Code Quality

```bash
# Format code
black src/ tests/

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

### Project Structure

```
trading_agent/
├── src/
│   ├── agentic/          # Daemon, reasoning, memory, guardrails
│   ├── cli/              # Command-line interface (Typer)
│   ├── config/           # Configuration management
│   ├── data/             # Database models, repositories, migrations
│   ├── execution/        # Order lifecycle management
│   ├── learning/         # Pattern detection, experiments, optimization
│   ├── nakedtrader/      # Daily put-selling workflow
│   ├── services/         # Market conditions, scanners, reconciliation
│   ├── strategies/       # Strategy definitions
│   ├── tools/            # IBKR client, screener, options finder
│   └── web/              # Flask dashboard
├── tests/
│   ├── unit/             # ~1800+ unit tests
│   ├── integration/      # IBKR and DB integration tests
│   └── e2e/              # End-to-end workflow tests
├── config/               # YAML configuration files
├── scripts/              # Database setup, data import utilities
└── docs/                 # Architecture and design documentation
```

## Tech Stack

| Category | Technology |
|----------|-----------|
| Language | Python 3.11+ |
| Broker API | ib_insync (Interactive Brokers) |
| AI Reasoning | Anthropic Claude (Sonnet) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| ORM | SQLAlchemy 2.0 + Alembic migrations |
| CLI | Typer + Rich |
| Web UI | Flask |
| ML/Stats | scikit-learn, scipy |
| Testing | pytest (1800+ tests) |
| Code Quality | Black, Ruff, MyPy |

## Configuration

The system is configured via `config/phase5.yaml` for daemon behavior and `config/daily_spx_options.yaml` for strategy parameters. Key settings:

- **Autonomy level** (L1-L4): Controls what the daemon can do without human approval
- **Claude model**: Which Claude model to use for reasoning (default: Sonnet)
- **Risk limits**: Max margin utilization, position limits, daily loss caps
- **Scheduling**: Event check intervals, market hours

## Contributing

This project is under very active development. If you're interested in contributing:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Write tests for your changes
4. Ensure all tests pass (`pytest`)
5. Run code quality checks (`black`, `ruff`, `mypy`)
6. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with [Claude Code](https://claude.ai/claude-code) by Anthropic
- Uses [ib_insync](https://github.com/erdewit/ib_insync) for Interactive Brokers integration
- Market data provided by Interactive Brokers
