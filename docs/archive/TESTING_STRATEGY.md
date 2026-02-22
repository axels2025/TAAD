# Testing Strategy

**Last Updated:** 2026-01-21
**Status:** Active
**Test Coverage:** 68.82% (199 tests passing)

---

## Table of Contents

1. [Overview](#overview)
2. [Test Types](#test-types)
3. [Running Tests](#running-tests)
4. [Live Test Setup](#live-test-setup)
5. [Test Organization](#test-organization)
6. [Coverage Goals](#coverage-goals)
7. [CI/CD Integration](#cicd-integration)
8. [Troubleshooting](#troubleshooting)

---

## Overview

Our testing strategy uses a three-tier approach to ensure code quality while maintaining fast development velocity:

1. **Unit Tests** - Fast, isolated, always run
2. **Mock Integration Tests** - Fast, simulated workflows, always run
3. **Live Integration Tests** - Slow, real IBKR connection, run manually

**Key Principle:** Tests should be fast by default, with slower tests opt-in only.

---

## Test Types

### 1. Unit Tests (Fast, Always Run ✅)

**Purpose:** Test individual functions/classes in isolation
**Speed:** <5 seconds for all 162 tests
**Location:** `tests/unit/`
**Dependencies:** Mocked
**Run Frequency:** Every commit

**Components Tested:**
- OrderExecutor (19 tests, 77% coverage)
- PositionMonitor (33 tests, 91% coverage)
- ExitManager (40 tests, 99% coverage)
- RiskGovernor (30 tests, 99% coverage)
- Configuration (23 tests, 92-100% coverage)
- Strategy components (13 tests)

**Example:**
```bash
# Run all unit tests
pytest tests/unit/ -v

# Run specific component
pytest tests/unit/test_order_executor.py -v
```

---

### 2. Mock Integration Tests (Fast, Always Run ✅)

**Purpose:** Test component interactions with mocked external dependencies
**Speed:** ~15 seconds for all 37 tests
**Location:** `tests/integration/` (excluding `test_ibkr_live.py`)
**Dependencies:** Mocked IBKR, mocked database
**Run Frequency:** Every commit

**Workflows Tested:**
- Complete trade lifecycle (scan → execute → monitor → exit)
- Risk rejection workflows
- Position monitoring and P&L calculation
- Exit decision generation
- Error handling and recovery
- Data flow between components

**Example:**
```bash
# Run all integration tests (excluding live)
pytest tests/integration/ -m "not live" -v

# Run specific workflow
pytest tests/integration/test_full_workflow.py -v
```

---

### 3. Live Integration Tests (Slow, Manual Only ⚠️)

**Purpose:** Validate actual IBKR API behavior with real connections
**Speed:** Variable (depends on network, market hours)
**Location:** `tests/integration/test_ibkr_live.py`
**Dependencies:** **REAL IBKR paper trading connection required**
**Run Frequency:** Before deployment, when debugging IBKR issues

**What's Tested:**
- Real IBKR connection establishment
- Actual account summary retrieval
- Real market data formats
- Position monitoring with live data
- Risk calculations with real account values
- Error handling with actual API responses
- Data type consistency (catches issues like "LLC" parsing)

**CRITICAL SAFETY:**
- ✅ Requires port 7497 (paper trading only)
- ✅ Orders are intentionally far OTM and won't fill
- ✅ Tests are skipped by default
- ✅ Most destructive tests require explicit manual confirmation

**Example:**
```bash
# Run all live tests
pytest -m live -v

# Run specific live test class
pytest tests/integration/test_ibkr_live.py::TestLiveConnection -m live -v
```

---

## Running Tests

### Default: Run All Standard Tests

```bash
# Option 1: Using pytest directly
pytest

# Option 2: Using test runner script
python scripts/run_tests.py

# Option 3: Using shell script
./scripts/run_tests.sh all
```

**What runs:** All unit tests + mock integration tests (199 tests)
**What's skipped:** Live IBKR tests
**Time:** ~20 seconds

---

### Run Only Live Tests

```bash
# Run all live tests
pytest -m live -v

# Run specific live test file
pytest tests/integration/test_ibkr_live.py -m live -v

# Run specific test class
pytest tests/integration/test_ibkr_live.py::TestLiveConnection -m live -v

# Run specific test
pytest tests/integration/test_ibkr_live.py::TestLiveConnection::test_connection_establishes -v
```

**Requirements:**
1. IBKR TWS/Gateway running on port 7497
2. Logged into paper trading account
3. API connections enabled

**What happens if IBKR not running:** Tests are automatically skipped with clear message

---

### Run Everything (Including Live)

```bash
# Override the default marker filter
pytest -m "" -v

# Or explicitly
pytest --override-ini="addopts=-ra -q --strict-markers" -v
```

**Warning:** This will attempt to run live tests. If IBKR not running, they'll be skipped.

---

### Run with Coverage

```bash
# Standard tests with coverage
pytest --cov=src --cov-report=html

# Open coverage report
open htmlcov/index.html

# With test runner
python scripts/run_tests.py --coverage --html
```

---

### Skip Slow Tests

```bash
# Skip both live and slow tests
pytest -m "not live and not slow"

# Skip only slow tests (includes live)
pytest -m "not slow"
```

---

## Live Test Setup

### Prerequisites

**1. IBKR TWS/Gateway Running**
- Download from: https://www.interactivebrokers.com/en/trading/tws.php
- Use TWS or IB Gateway
- Paper trading account required

**2. Paper Trading Configuration**

```bash
# In .env file
IBKR_HOST=127.0.0.1
IBKR_PORT=7497          # CRITICAL: 7497 for paper, 7496 for live
PAPER_TRADING=true      # CRITICAL: Must be true
```

**3. TWS/Gateway Settings**

Enable API access:
1. Open TWS/Gateway
2. Go to: **Global Configuration → API → Settings**
3. Enable: **"Enable ActiveX and Socket Clients"**
4. Add to **Trusted IP Addresses**: `127.0.0.1`
5. Verify port: `7497` (paper trading)
6. Click **OK** and restart TWS/Gateway

**4. Login to Paper Trading Account**
- Use paper trading credentials
- Verify "Paper Trading" appears in TWS title bar

---

### Running Live Tests Safely

**Step 1: Verify Setup**
```bash
# Test connection only
pytest tests/integration/test_ibkr_live.py::TestLiveConnection::test_connection_establishes -m live -v
```

**Expected Output:**
```
✅ test_connection_establishes PASSED
```

**If Failed:**
```
SKIPPED - IBKR not available: [error message]
```

**Step 2: Run Safe Tests**
```bash
# Run all tests except order placement
pytest -m "live and not slow" -v
```

**Step 3: Run All Live Tests (Including Order Placement)**
```bash
# Order tests are SKIPPED by default
pytest -m live -v
```

**Step 4: Manual Order Test (Optional, Explicit Confirmation Required)**
```bash
# Only if you explicitly want to test order placement
pytest tests/integration/test_ibkr_live.py::TestLiveOrderPlacement::test_place_small_limit_order -v
```

**What happens:**
- Small order placed (1 contract, far OTM)
- Order will NOT fill (limit price too low)
- Order canceled immediately
- Validates complete order lifecycle

---

## Test Organization

### Directory Structure

```
tests/
├── __init__.py
├── conftest.py                       # Pytest configuration & fixtures
│
├── unit/                             # Unit tests (fast, mocked)
│   ├── test_order_executor.py
│   ├── test_position_monitor.py
│   ├── test_exit_manager.py
│   ├── test_risk_governor.py
│   ├── test_config.py
│   ├── test_naked_put_strategy.py
│   ├── test_options_finder.py
│   └── test_screener.py
│
├── integration/                      # Integration tests
│   ├── test_database.py              # Database integration (mocked)
│   ├── test_full_workflow.py         # Complete workflows (mocked)
│   ├── test_ibkr.py                  # IBKR client (mocked)
│   ├── test_strategy_workflow.py     # Strategy workflows (mocked)
│   └── test_ibkr_live.py             # 🔴 LIVE IBKR tests (real connection)
│
└── e2e/                              # End-to-end tests (future)
    └── (planned for Phase 3+)
```

---

### Test File Naming

- `test_*.py` - Test files
- `test_*_live.py` - Live integration tests
- `conftest.py` - Pytest configuration

---

### Test Class Organization

**Unit Tests:**
```python
class TestComponentName:
    """Test specific component functionality."""

    def test_initialization(self):
        """Test component initializes correctly."""
        pass

    def test_specific_feature(self):
        """Test specific feature works."""
        pass
```

**Live Tests:**
```python
@pytest.mark.live
class TestLiveComponent:
    """Test component with real IBKR connection."""

    def test_with_real_data(self, live_ibkr_client):
        """Test using real IBKR data."""
        pass
```

---

## Coverage Goals

### Current Coverage (2026-01-21)

**Overall:** 68.82% (1,152/1,674 statements)

**Phase 2 Components (Core Execution Engine):**

| Component | Statements | Coverage | Goal | Status |
|-----------|-----------|----------|------|--------|
| ExitManager | 103 | 99.03% | >90% | ✅ Excellent |
| RiskGovernor | 121 | 99.17% | >90% | ✅ Excellent |
| PositionMonitor | 159 | 90.57% | >90% | ✅ Excellent |
| OrderExecutor | 153 | 77.12% | >70% | ✅ Good |
| BaselineStrategy | 43 | 100.00% | >90% | ✅ Perfect |
| Config | 79 | 92.41% | >90% | ✅ Excellent |

**Other Components:**

| Component | Coverage | Note |
|-----------|----------|------|
| CLI | 0% | Not yet tested (future work) |
| Learning modules | 0% | Phase 3 work |
| Data repositories | 67.65% | Partial coverage |
| Tools (screener, options_finder) | 28-32% | Partial coverage |

**Coverage Strategy:**
- ✅ Core execution engine: >90% (achieved)
- ⏳ Data layer: >80% (work in progress)
- ⏳ Tools: >70% (future work)
- ⏳ CLI: >60% (future work)

---

## CI/CD Integration

### GitHub Actions Example

```yaml
# .github/workflows/tests.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run tests
        run: |
          # Run standard tests only (skips live tests)
          pytest -m "not live" --cov=src --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml
```

**Key Points:**
- ✅ Always use `-m "not live"` in CI
- ✅ Live tests require manual execution
- ✅ Coverage reports help track progress

---

### Pre-Deployment Checklist

Before deploying to production or starting Phase 3:

```bash
# 1. Run all unit tests
pytest tests/unit/ -v
# ✅ Expected: 162/162 passing

# 2. Run mock integration tests
pytest tests/integration/ -m "not live" -v
# ✅ Expected: 37/37 passing

# 3. Run live integration tests (requires TWS)
pytest -m live -v
# ✅ Expected: All tests pass or skip gracefully

# 4. Check coverage
pytest --cov=src --cov-report=term-missing
# ✅ Expected: >68% overall, >90% for core components

# 5. Verify no regressions
python scripts/run_tests.py --summary
# ✅ Expected: 199 tests passing, 0 failures
```

**All green?** ✅ Ready to deploy!

---

## Troubleshooting

### Live Tests Always Skip

**Symptoms:**
```
SKIPPED - IBKR not available: connection refused
```

**Solutions:**

1. **Check TWS/Gateway is Running**
   ```bash
   # Look for process
   ps aux | grep -i tws
   ps aux | grep -i ibgateway
   ```

2. **Verify Port 7497**
   - Open TWS → Global Configuration → API → Settings
   - Check port number is 7497
   - Verify "Enable ActiveX and Socket Clients" is checked

3. **Check Firewall**
   ```bash
   # Mac/Linux: Test port accessibility
   nc -zv 127.0.0.1 7497
   ```

4. **Verify Paper Trading**
   - TWS title bar should say "Paper Trading"
   - .env file should have `IBKR_PORT=7497`

5. **Check API Settings**
   - Add `127.0.0.1` to Trusted IP Addresses
   - Remove any IP restrictions
   - Restart TWS after changes

---

### Live Tests Fail with "Invalid Symbol"

**Symptoms:**
```
AssertionError: Expected market data, got None
```

**Solutions:**

1. **Market Hours**
   - Live market data only available during market hours
   - Some tests may fail outside 9:30 AM - 4:00 PM ET
   - Solution: Run during market hours or check test logic

2. **Market Data Subscriptions**
   - Paper accounts need market data subscriptions
   - Subscribe to US Securities Snapshot
   - Check: Account → Market Data Subscriptions

3. **Symbol Issues**
   - SPY should always work for paper trading
   - Try different symbol if one fails
   - Check test logic for hardcoded symbols

---

### Live Tests Fail with "Insufficient Margin"

**Symptoms:**
```
Risk check rejected: Insufficient margin
```

**Solutions:**

1. **Check Paper Account Balance**
   - Paper accounts should have $1,000,000
   - If depleted, reset paper account
   - IBKR → Account Management → Reset Paper Trading

2. **Too Many Open Positions**
   - Check open positions in TWS
   - Close unnecessary positions
   - Max positions limit is 10

---

### Tests Pass Locally But Fail in CI

**Symptoms:**
```
CI: 189 tests pass, 10 fail
Local: 199 tests pass
```

**Solutions:**

1. **Check CI Configuration**
   ```yaml
   # Ensure using -m "not live" in CI
   pytest -m "not live" --cov=src
   ```

2. **Python Version Mismatch**
   - CI should use Python 3.11+
   - Check GitHub Actions `python-version: '3.11'`

3. **Dependency Issues**
   - Verify `requirements.txt` is up-to-date
   - Check for missing dependencies

---

### Coverage Drops After Adding Tests

**Symptoms:**
```
Coverage dropped from 70% to 68%
```

**This is normal!** Coverage percentage = covered lines / total lines.

**Why it happens:**
- Added new code (increases total lines)
- Coverage percentage can drop even if coverage improves

**What to check:**
```bash
# Check absolute numbers, not just percentage
pytest --cov=src --cov-report=term-missing

# Look for:
# - Total statements increased (good)
# - Covered statements increased (good)
# - Percentage may fluctuate (okay)
```

---

## Best Practices

### Writing New Tests

**1. Start with Unit Tests**
```python
def test_new_feature():
    """Test new feature in isolation with mocks."""
    # Arrange - create mocks
    # Act - call function
    # Assert - verify behavior
```

**2. Add Integration Test**
```python
def test_new_feature_integration():
    """Test new feature with other components (mocked IBKR)."""
    # Test workflow with mocked external dependencies
```

**3. Optionally Add Live Test**
```python
@pytest.mark.live
def test_new_feature_live(live_ibkr_client):
    """Test new feature with real IBKR connection."""
    # Only if feature directly interacts with IBKR API
```

---

### Test Markers

Use markers to categorize tests:

```python
@pytest.mark.unit
def test_calculation():
    """Unit test."""
    pass

@pytest.mark.integration
def test_workflow():
    """Integration test."""
    pass

@pytest.mark.live
def test_with_ibkr():
    """Live IBKR test."""
    pass

@pytest.mark.slow
def test_long_running():
    """Slow test."""
    pass
```

---

### Fixtures

**Use shared fixtures from conftest.py:**
```python
def test_with_config(mock_config):
    """Uses mock_config fixture."""
    assert mock_config.paper_trading is True
```

**Create custom fixtures for live tests:**
```python
@pytest.fixture
def live_custom_component(live_ibkr_client):
    """Custom fixture for live tests."""
    return CustomComponent(live_ibkr_client)
```

---

## Summary

**Test Strategy:**
- 📊 **199 tests total**
- ⚡ **199 fast tests** (unit + mock integration) - run always
- 🔴 **8 test classes with live tests** - run manually
- ✅ **100% pass rate** (all standard tests)
- 🎯 **68.82% overall coverage** (>90% for core components)

**Default Behavior:**
```bash
pytest  # Runs 199 tests, skips live tests (~20 seconds)
```

**When to Run Live Tests:**
- Before deployment
- When debugging IBKR integration issues
- After fixing IBKR-related bugs
- When adding new IBKR features

**Safety:**
- ✅ Live tests clearly marked with `@pytest.mark.live`
- ✅ Skipped by default (explicit opt-in required)
- ✅ Paper trading only (port 7497)
- ✅ Orders won't fill (far OTM, low limit prices)

---

**Last Updated:** 2026-01-21
**Document Version:** 1.0
**Maintained By:** Trading Agent Team
