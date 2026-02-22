"""Test runner script for Phase 2 components.

This script provides easy commands to run tests:
- All tests
- Specific component tests
- Integration tests
- With coverage reports
"""

import argparse
import subprocess
import sys
from pathlib import Path


# ANSI color codes
class Colors:
    """Terminal color codes."""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def print_header(text):
    """Print section header."""
    print()
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.ENDC}")
    print()


def print_success(text):
    """Print success message."""
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_error(text):
    """Print error message."""
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_info(text):
    """Print info message."""
    print(f"{Colors.OKCYAN}→ {text}{Colors.ENDC}")


def run_command(cmd, description):
    """Run a command and report results.

    Args:
        cmd: Command to run (list)
        description: Description of what's being run

    Returns:
        bool: True if successful
    """
    print_info(f"{description}...")
    print(f"  Command: {' '.join(cmd)}")
    print()

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=False,
            text=True,
        )
        print()
        print_success(f"{description} completed successfully")
        return True

    except subprocess.CalledProcessError as e:
        print()
        print_error(f"{description} failed with exit code {e.returncode}")
        return False

    except FileNotFoundError:
        print()
        print_error("pytest not found. Please install: pip install pytest pytest-cov")
        return False


def run_all_tests(verbose=False, coverage=False):
    """Run all tests.

    Args:
        verbose: Show verbose output
        coverage: Include coverage report
    """
    print_header("RUNNING ALL TESTS")

    cmd = ["pytest", "tests/"]

    if verbose:
        cmd.append("-v")

    if coverage:
        cmd.extend(["--cov=src/execution", "--cov-report=term-missing"])

    return run_command(cmd, "All tests")


def run_unit_tests(component=None, verbose=False):
    """Run unit tests.

    Args:
        component: Specific component to test (or None for all)
        verbose: Show verbose output
    """
    if component:
        print_header(f"RUNNING UNIT TESTS: {component}")
        test_file = f"tests/unit/test_{component}.py"
        cmd = ["pytest", test_file]
    else:
        print_header("RUNNING ALL UNIT TESTS")
        cmd = ["pytest", "tests/unit/"]

    if verbose:
        cmd.append("-v")

    return run_command(cmd, f"Unit tests{f' for {component}' if component else ''}")


def run_integration_tests(verbose=False):
    """Run integration tests.

    Args:
        verbose: Show verbose output
    """
    print_header("RUNNING INTEGRATION TESTS")

    cmd = ["pytest", "tests/integration/"]

    if verbose:
        cmd.append("-v")

    return run_command(cmd, "Integration tests")


def run_with_coverage(html=False):
    """Run tests with coverage report.

    Args:
        html: Generate HTML coverage report
    """
    print_header("RUNNING TESTS WITH COVERAGE")

    cmd = [
        "pytest",
        "tests/",
        "--cov=src/execution",
        "--cov-report=term-missing",
    ]

    if html:
        cmd.append("--cov-report=html")

    success = run_command(cmd, "Tests with coverage")

    if success and html:
        print()
        print_info("HTML coverage report generated in: htmlcov/index.html")
        print_info("Open with: open htmlcov/index.html")

    return success


def run_specific_test(test_path, verbose=False):
    """Run specific test file or test function.

    Args:
        test_path: Path to test file or test::function
        verbose: Show verbose output
    """
    print_header(f"RUNNING SPECIFIC TEST: {test_path}")

    cmd = ["pytest", test_path]

    if verbose:
        cmd.append("-v")

    return run_command(cmd, f"Test {test_path}")


def check_pytest_installed():
    """Check if pytest is installed."""
    try:
        result = subprocess.run(
            ["pytest", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        print_success(f"pytest is installed: {result.stdout.strip()}")
        return True

    except (subprocess.CalledProcessError, FileNotFoundError):
        print_error("pytest is not installed")
        print()
        print_info("Install with: pip install pytest pytest-cov")
        return False


def show_test_summary():
    """Show summary of available tests."""
    print_header("TEST SUITE SUMMARY")

    print(f"{Colors.BOLD}Unit Tests:{Colors.ENDC}")
    print("  • OrderExecutor    (19 tests)   - tests/unit/test_order_executor.py")
    print("  • PositionMonitor  (35+ tests)  - tests/unit/test_position_monitor.py")
    print("  • ExitManager      (40+ tests)  - tests/unit/test_exit_manager.py")
    print("  • RiskGovernor     (30+ tests)  - tests/unit/test_risk_governor.py")
    print()

    print(f"{Colors.BOLD}Integration Tests:{Colors.ENDC}")
    print("  • Full Workflow    (15+ tests)  - tests/integration/test_full_workflow.py")
    print()

    print(f"{Colors.BOLD}Total: 124+ tests{Colors.ENDC}")
    print()


def main():
    """Main test runner."""
    parser = argparse.ArgumentParser(
        description="Test runner for Phase 2 components",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all tests
  python scripts/run_tests.py

  # Run all tests with verbose output
  python scripts/run_tests.py -v

  # Run all tests with coverage
  python scripts/run_tests.py --coverage

  # Run all tests with HTML coverage report
  python scripts/run_tests.py --coverage --html

  # Run only unit tests
  python scripts/run_tests.py --unit

  # Run specific component tests
  python scripts/run_tests.py --unit order_executor
  python scripts/run_tests.py --unit position_monitor
  python scripts/run_tests.py --unit exit_manager
  python scripts/run_tests.py --unit risk_governor

  # Run only integration tests
  python scripts/run_tests.py --integration

  # Run specific test file
  python scripts/run_tests.py --test tests/unit/test_order_executor.py

  # Run specific test function
  python scripts/run_tests.py --test tests/unit/test_order_executor.py::TestOrderCreation::test_create_limit_order

  # Show test summary
  python scripts/run_tests.py --summary
        """,
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run with coverage report",
    )

    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate HTML coverage report (requires --coverage)",
    )

    parser.add_argument(
        "--unit",
        nargs="?",
        const="all",
        metavar="COMPONENT",
        help="Run unit tests (optionally for specific component: order_executor, position_monitor, exit_manager, risk_governor)",
    )

    parser.add_argument(
        "--integration",
        action="store_true",
        help="Run integration tests",
    )

    parser.add_argument(
        "--test",
        metavar="PATH",
        help="Run specific test file or test function",
    )

    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show test suite summary",
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if pytest is installed",
    )

    args = parser.parse_args()

    # Check if pytest is installed
    if args.check:
        sys.exit(0 if check_pytest_installed() else 1)

    # Show summary
    if args.summary:
        show_test_summary()
        sys.exit(0)

    # Run specific test
    if args.test:
        success = run_specific_test(args.test, args.verbose)
        sys.exit(0 if success else 1)

    # Run integration tests
    if args.integration:
        success = run_integration_tests(args.verbose)
        sys.exit(0 if success else 1)

    # Run unit tests
    if args.unit:
        if args.unit == "all":
            success = run_unit_tests(None, args.verbose)
        else:
            success = run_unit_tests(args.unit, args.verbose)
        sys.exit(0 if success else 1)

    # Run with coverage
    if args.coverage:
        success = run_with_coverage(args.html)
        sys.exit(0 if success else 1)

    # Default: run all tests
    success = run_all_tests(args.verbose, False)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
