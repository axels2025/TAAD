"""
Test script to verify setup and connections
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def test_imports():
    """Test that all required packages are installed"""
    print("Testing package imports...")
    try:
        import ib_insync
        print("  ✓ ib_insync")
    except ImportError:
        print("  ✗ ib_insync - Run: pip install ib_insync")
        return False

    try:
        import langgraph
        print("  ✓ langgraph")
    except ImportError:
        print("  ✗ langgraph - Run: pip install langgraph")
        return False

    try:
        import langchain_anthropic
        print("  ✓ langchain_anthropic")
    except ImportError:
        print("  ✗ langchain_anthropic - Run: pip install langchain-anthropic")
        return False

    try:
        import anthropic
        print("  ✓ anthropic")
    except ImportError:
        print("  ✗ anthropic - Run: pip install anthropic")
        return False

    try:
        import pandas
        print("  ✓ pandas")
    except ImportError:
        print("  ✗ pandas - Run: pip install pandas")
        return False

    try:
        import numpy
        print("  ✓ numpy")
    except ImportError:
        print("  ✗ numpy - Run: pip install numpy")
        return False

    print("All packages installed!\n")
    return True


def test_env():
    """Test environment variables"""
    print("Testing environment variables...")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key and api_key.startswith("sk-ant-"):
        print("  ✓ ANTHROPIC_API_KEY is set")
    else:
        print("  ✗ ANTHROPIC_API_KEY not found or invalid")
        print("    Add to .env file: ANTHROPIC_API_KEY=sk-ant-xxxxx")
        return False

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = os.getenv("IBKR_PORT", "7497")
    print(f"  ✓ IBKR settings: {host}:{port}")

    print("Environment configured!\n")
    return True


def test_ibkr():
    """Test IBKR connection"""
    print("Testing IBKR connection...")

    try:
        from config.ibkr_connection import create_ibkr_connection

        with create_ibkr_connection() as ib:
            if ib.isConnected():
                account = ib.managedAccounts()[0] if ib.managedAccounts() else "Unknown"
                print(f"  ✓ Connected to IBKR")
                print(f"    Account: {account}")
                print("IBKR connection successful!\n")
                return True
            else:
                print("  ✗ Failed to connect to IBKR")
                return False

    except Exception as e:
        print(f"  ✗ IBKR connection error: {e}")
        print("    Make sure TWS/Gateway is running on port 7497")
        return False


def test_project_structure():
    """Test project structure"""
    print("Testing project structure...")

    required_files = [
        "config/ibkr_connection.py",
        "tools/uptrend_screener.py",
        "tools/options_finder.py",
        "tools/margin_calculator.py",
        "agents/trading_agent.py",
        "main.py",
        "utils.py",
        "requirements.txt"
    ]

    all_exist = True
    for file in required_files:
        if os.path.exists(file):
            print(f"  ✓ {file}")
        else:
            print(f"  ✗ {file} - Missing!")
            all_exist = False

    if all_exist:
        print("Project structure complete!\n")
    return all_exist


def main():
    """Run all tests"""
    print("="*60)
    print("AI Trading Agent - Setup Verification")
    print("="*60)
    print()

    results = []

    # Test imports
    results.append(("Package Imports", test_imports()))

    # Test environment
    results.append(("Environment Variables", test_env()))

    # Test project structure
    results.append(("Project Structure", test_project_structure()))

    # Test IBKR (optional)
    try:
        results.append(("IBKR Connection", test_ibkr()))
    except:
        print("  ⚠ IBKR test skipped (connection not available)\n")

    # Summary
    print("="*60)
    print("Test Summary")
    print("="*60)

    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status} - {name}")
        if not passed:
            all_passed = False

    print("="*60)

    if all_passed:
        print("\n✓ All tests passed! You're ready to run the agent.")
        print("  Run: python main.py")
        return 0
    else:
        print("\n✗ Some tests failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
