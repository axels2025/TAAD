#!/bin/bash

# Test runner shell script for Phase 2 components
# Simple wrapper around pytest for quick testing

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_header() {
    echo ""
    echo -e "${BLUE}=====================================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}=====================================================================${NC}"
    echo ""
}

echo_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

echo_error() {
    echo -e "${RED}✗ $1${NC}"
}

echo_info() {
    echo -e "${YELLOW}→ $1${NC}"
}

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo_error "pytest is not installed"
    echo_info "Install with: pip install pytest pytest-cov"
    exit 1
fi

# Parse arguments
case "$1" in
    "all")
        echo_header "RUNNING ALL TESTS"
        pytest tests/ -v
        ;;

    "unit")
        if [ -z "$2" ]; then
            echo_header "RUNNING ALL UNIT TESTS"
            pytest tests/unit/ -v
        else
            echo_header "RUNNING UNIT TESTS: $2"
            pytest tests/unit/test_$2.py -v
        fi
        ;;

    "integration")
        echo_header "RUNNING INTEGRATION TESTS"
        pytest tests/integration/ -v
        ;;

    "coverage")
        echo_header "RUNNING TESTS WITH COVERAGE"
        pytest tests/ --cov=src/execution --cov-report=term-missing --cov-report=html
        echo ""
        echo_success "Coverage report generated"
        echo_info "View HTML report: open htmlcov/index.html"
        ;;

    "quick")
        echo_header "QUICK TEST RUN (NO VERBOSE)"
        pytest tests/ -q
        ;;

    "summary")
        echo_header "TEST SUITE SUMMARY"
        echo ""
        echo "Unit Tests:"
        echo "  • OrderExecutor    (19 tests)   - tests/unit/test_order_executor.py"
        echo "  • PositionMonitor  (35+ tests)  - tests/unit/test_position_monitor.py"
        echo "  • ExitManager      (40+ tests)  - tests/unit/test_exit_manager.py"
        echo "  • RiskGovernor     (30+ tests)  - tests/unit/test_risk_governor.py"
        echo ""
        echo "Integration Tests:"
        echo "  • Full Workflow    (15+ tests)  - tests/integration/test_full_workflow.py"
        echo ""
        echo "Total: 124+ tests"
        echo ""
        ;;

    "help"|"-h"|"--help"|"")
        echo_header "TEST RUNNER - USAGE"
        echo ""
        echo "Usage: ./scripts/run_tests.sh [command] [options]"
        echo ""
        echo "Commands:"
        echo "  all                    Run all tests (unit + integration)"
        echo "  unit                   Run all unit tests"
        echo "  unit <component>       Run tests for specific component"
        echo "                         Components: order_executor, position_monitor,"
        echo "                                     exit_manager, risk_governor"
        echo "  integration            Run integration tests"
        echo "  coverage               Run tests with coverage report"
        echo "  quick                  Quick test run (no verbose output)"
        echo "  summary                Show test suite summary"
        echo "  help                   Show this help message"
        echo ""
        echo "Examples:"
        echo "  ./scripts/run_tests.sh all"
        echo "  ./scripts/run_tests.sh unit"
        echo "  ./scripts/run_tests.sh unit order_executor"
        echo "  ./scripts/run_tests.sh integration"
        echo "  ./scripts/run_tests.sh coverage"
        echo ""
        ;;

    *)
        echo_error "Unknown command: $1"
        echo_info "Run './scripts/run_tests.sh help' for usage"
        exit 1
        ;;
esac
