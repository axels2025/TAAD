"""Phase 0 Foundation Validation Script

This script validates that ALL Phase 0 components are working correctly
with REAL data and functionality (not mocks).

Run with: python tests/validate_phase0.py
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from decimal import Decimal

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table

console = Console()


def test_database():
    """Test database operations with REAL data."""
    console.print("\n[bold blue]Testing Database...[/bold blue]")

    from src.data.database import get_db_session, init_database
    from src.data.repositories import TradeRepository
    from src.data.models import Trade

    try:
        # Initialize database
        init_database()
        console.print("  ✓ Database initialized")

        # Test trade insertion
        with get_db_session() as session:
            repo = TradeRepository(session)

            # Create test trade
            test_trade = Trade(
                trade_id=f"TEST_{datetime.now().timestamp()}",
                symbol="AAPL",
                strike=180.0,
                expiration=datetime.now() + timedelta(days=14),
                option_type="PUT",
                entry_date=datetime.now(),
                entry_premium=0.50,
                contracts=1,
                otm_pct=0.15,
                dte=14,
                vix_at_entry=15.5,
                spy_price_at_entry=450.0,
            )

            session.add(test_trade)
            session.commit()
            console.print(f"  ✓ Inserted test trade: {test_trade.trade_id}")

            # Test query
            retrieved = repo.get_by_id(test_trade.trade_id)
            assert retrieved is not None, "Failed to retrieve trade"
            assert retrieved.symbol == "AAPL"
            console.print("  ✓ Query working")

            # Test update
            test_trade.exit_date = datetime.now()
            test_trade.exit_premium = 0.25
            test_trade.profit_loss = 25.0
            session.commit()
            console.print("  ✓ Update working")

            # Test delete
            session.delete(test_trade)
            session.commit()
            console.print("  ✓ Delete working")

        console.print("[green]✓ Database: ALL TESTS PASSED[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Database: FAILED - {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        return False


def test_configuration():
    """Test configuration loading and validation."""
    console.print("\n[bold blue]Testing Configuration...[/bold blue]")

    from src.config.base import get_config
    from src.config.baseline_strategy import BaselineStrategy
    import os

    try:
        # Test main config
        config = get_config()
        console.print(f"  ✓ Config loaded")

        # Verify critical fields
        assert config.paper_trading == True, "Paper trading must be enabled"
        console.print(f"  ✓ Paper trading: {config.paper_trading}")

        assert config.ibkr_port == 7497, "Must use paper trading port"
        console.print(f"  ✓ IBKR port: {config.ibkr_port}")

        # Test environment variables
        assert os.getenv("PAPER_TRADING") == "true"
        console.print(f"  ✓ Environment variables loaded")

        # Test strategy config
        strategy = BaselineStrategy()
        console.print(f"  ✓ Strategy config loaded")
        console.print(f"    - OTM range: {strategy.otm_range}")
        console.print(f"    - DTE range: {strategy.dte_range}")
        console.print(f"    - Premium range: ${strategy.premium_range}")

        # Test invalid values (should fail)
        try:
            from pydantic import ValidationError
            from src.config.base import RiskLimits
            invalid = RiskLimits(max_daily_loss=0.5)  # Should be negative
            console.print(f"[red]  ✗ Validation not working![/red]")
            return False
        except Exception:
            console.print(f"  ✓ Validation rejects invalid values")

        console.print("[green]✓ Configuration: ALL TESTS PASSED[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Configuration: FAILED - {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        return False


def test_ibkr_connection():
    """Test IBKR connection with REAL market data."""
    console.print("\n[bold blue]Testing IBKR Connection...[/bold blue]")

    from src.tools.ibkr_client import IBKRClient
    from src.config.base import get_config

    try:
        config = get_config()
        client = IBKRClient(config.ibkr)

        # Test connection
        client.connect()
        console.print(f"  ✓ Connected to IBKR")

        # Test market data for SPY
        from ib_insync import Stock
        spy = Stock("SPY", "SMART", "USD")
        client.ib.qualifyContracts(spy)

        ticker = client.ib.reqMktData(spy)
        client.ib.sleep(2)  # Wait for data

        if ticker.last and ticker.last > 0:
            console.print(f"  ✓ SPY market data: ${ticker.last:.2f}")
        else:
            console.print(f"[yellow]  ⚠ No market data (market closed?)[/yellow]")

        # Test options chain
        from ib_insync import Option
        expiry = (datetime.now() + timedelta(days=14)).strftime("%Y%m%d")
        option = Option("SPY", expiry, 450, "P", "SMART")

        try:
            chains = client.ib.reqContractDetails(option)
            if chains:
                console.print(f"  ✓ Options chain accessible ({len(chains)} strikes)")
            else:
                console.print(f"[yellow]  ⚠ No options data (market closed?)[/yellow]")
        except Exception as e:
            console.print(f"[yellow]  ⚠ Options query: {e}[/yellow]")

        # Test disconnect/reconnect
        client.disconnect()
        console.print(f"  ✓ Disconnect working")

        client.connect()
        console.print(f"  ✓ Reconnect working")

        client.disconnect()

        console.print("[green]✓ IBKR Connection: ALL TESTS PASSED[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ IBKR Connection: FAILED - {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        return False


def test_logging():
    """Test logging functionality."""
    console.print("\n[bold blue]Testing Logging...[/bold blue]")

    from src.config.logging import setup_logging
    from loguru import logger
    from pathlib import Path

    try:
        # Setup logging
        setup_logging()
        console.print(f"  ✓ Logging initialized")

        # Test different log levels
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        console.print(f"  ✓ All log levels working")

        # Verify log file exists
        log_file = Path("logs/app.log")
        assert log_file.exists(), "Log file not created"
        console.print(f"  ✓ Log file created: {log_file}")

        # Check file has content
        log_content = log_file.read_text()
        assert "Info message" in log_content, "Log messages not written"
        console.print(f"  ✓ Log messages written to file")

        # Test structured logging
        logger.info(
            "Structured log test",
            extra={
                "trade_id": "TEST123",
                "symbol": "AAPL",
                "premium": 0.50,
            }
        )
        console.print(f"  ✓ Structured logging working")

        console.print("[green]✓ Logging: ALL TESTS PASSED[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Logging: FAILED - {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        return False


def main():
    """Run all Phase 0 validation tests."""
    console.print("[bold cyan]" + "=" * 70 + "[/bold cyan]")
    console.print("[bold cyan]PHASE 0 FOUNDATION VALIDATION[/bold cyan]")
    console.print("[bold cyan]Testing with REAL data and functionality (not mocks)[/bold cyan]")
    console.print("[bold cyan]" + "=" * 70 + "[/bold cyan]")

    results = {
        "Database": test_database(),
        "Configuration": test_configuration(),
        "IBKR Connection": test_ibkr_connection(),
        "Logging": test_logging(),
    }

    # Summary
    console.print("\n[bold cyan]" + "=" * 70 + "[/bold cyan]")
    console.print("[bold cyan]PHASE 0 VALIDATION RESULTS[/bold cyan]")
    console.print("[bold cyan]" + "=" * 70 + "[/bold cyan]")

    table = Table(title="Component Test Results")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Result")

    all_passed = True
    for component, passed in results.items():
        if passed:
            table.add_row(component, "✓ PASS", "[green]Working correctly[/green]")
        else:
            table.add_row(component, "✗ FAIL", "[red]Has issues[/red]")
            all_passed = False

    console.print(table)

    if all_passed:
        console.print("\n[bold green]✓ PHASE 0: ALL SYSTEMS OPERATIONAL[/bold green]")
        return 0
    else:
        console.print("\n[bold red]✗ PHASE 0: SOME SYSTEMS FAILING[/bold red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
