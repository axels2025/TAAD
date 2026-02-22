#!/usr/bin/env python3
"""Phase 2 Validation Script - Autonomous Trading Validation.

This script executes the Phase 2 validation plan:
1. Execute 20+ autonomous trades in paper trading
2. Validate risk limits enforcement
3. Test emergency stop response time
4. Generate validation report

SAFETY:
- Runs in paper trading only (port 7497)
- Validates PAPER_TRADING=true before execution
- All trades are real paper trading orders
- Results saved to validation report

Usage:
    python scripts/phase2_validation.py --mode initial    # 5 test trades
    python scripts/phase2_validation.py --mode full       # 20+ trades
    python scripts/phase2_validation.py --mode risk-test  # Test risk limits
    python scripts/phase2_validation.py --mode emergency  # Test emergency stop
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.config.base import Config, IBKRConfig
from src.config.baseline_strategy import BaselineStrategy
from src.execution.exit_manager import ExitManager
from src.execution.order_executor import OrderExecutor
from src.execution.position_monitor import PositionMonitor
from src.execution.risk_governor import RiskGovernor
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient

# Load environment
load_dotenv()

console = Console()


class Phase2Validator:
    """Phase 2 validation orchestrator."""

    def __init__(self, dry_run: bool = False):
        """Initialize validator.

        Args:
            dry_run: If True, simulate orders without placing them
        """
        self.dry_run = dry_run
        self.results = {
            "start_time": datetime.now().isoformat(),
            "trades": [],
            "risk_tests": [],
            "emergency_tests": [],
            "errors": [],
        }

        # Verify paper trading
        self._verify_paper_trading()

        # Initialize components
        console.print("\n[bold cyan]Initializing Trading System...[/bold cyan]\n")
        self._initialize_components()

    def _verify_paper_trading(self):
        """Verify paper trading configuration."""
        paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"
        port = int(os.getenv("IBKR_PORT", "0"))

        if not paper_trading:
            console.print("[bold red]❌ SAFETY ERROR:[/bold red] PAPER_TRADING is not true")
            console.print("Set PAPER_TRADING=true in .env file")
            sys.exit(1)

        if port != 7497:
            console.print(
                f"[bold red]❌ SAFETY ERROR:[/bold red] Port {port} is not paper trading port"
            )
            console.print("Set IBKR_PORT=7497 in .env file")
            sys.exit(1)

        console.print("[green]✓ Paper trading verified[/green]")

    def _initialize_components(self):
        """Initialize all trading components."""
        try:
            # IBKR Client
            ibkr_config = IBKRConfig(
                host=os.getenv("IBKR_HOST", "127.0.0.1"),
                port=int(os.getenv("IBKR_PORT", "7497")),
                client_id=1,
            )
            self.ibkr_client = IBKRClient(config=ibkr_config)

            console.print("Connecting to IBKR...")
            self.ibkr_client.connect()

            if not self.ibkr_client.is_connected():
                raise ConnectionError("Failed to connect to IBKR")

            console.print("[green]✓ Connected to IBKR[/green]")

            # Configuration
            self.config = Config()
            self.strategy_config = BaselineStrategy()

            # Components
            self.position_monitor = PositionMonitor(
                ibkr_client=self.ibkr_client,
                config=self.strategy_config,
            )

            self.risk_governor = RiskGovernor(
                ibkr_client=self.ibkr_client,
                position_monitor=self.position_monitor,
                config=self.config,
            )

            self.order_executor = OrderExecutor(
                ibkr_client=self.ibkr_client,
                config=self.config,
                dry_run=self.dry_run,
            )

            self.exit_manager = ExitManager(
                ibkr_client=self.ibkr_client,
                position_monitor=self.position_monitor,
                config=self.strategy_config,
            )

            console.print("[green]✓ All components initialized[/green]\n")

        except Exception as e:
            console.print(f"[bold red]❌ Initialization failed:[/bold red] {e}")
            sys.exit(1)

    def create_test_opportunity(self, symbol: str = "SPY", offset: int = 0) -> TradeOpportunity:
        """Create a test trade opportunity.

        Args:
            symbol: Stock symbol
            offset: Offset for strike price variation

        Returns:
            TradeOpportunity for testing
        """
        # Get current stock price
        stock_price = self._get_stock_price(symbol)

        # Create moderately OTM opportunity (15-20% OTM)
        # Use 5-dollar strike increments for SPY
        otm_pct = 0.15 + (offset * 0.01)  # 15%, 16%, 17%, etc.
        strike_price = stock_price * (1 - otm_pct)

        # Round to nearest $5 strike for SPY
        strike = round(strike_price / 5) * 5

        expiration = datetime.now() + timedelta(days=7)

        return TradeOpportunity(
            symbol=symbol,
            strike=float(strike),
            expiration=expiration,
            option_type="PUT",
            premium=0.10,
            contracts=1,
            otm_pct=otm_pct,
            dte=7,
            stock_price=stock_price,
            trend="uptrend",
            confidence=0.80,
            reasoning=f"Phase 2 validation trade #{offset + 1}",
            margin_required=100.0,
        )

    def _get_stock_price(self, symbol: str) -> float:
        """Get current stock price from IBKR.

        Args:
            symbol: Stock symbol

        Returns:
            Current stock price
        """
        try:
            from ib_insync import Stock

            contract = Stock(symbol, "SMART", "USD")
            qualified = self.ibkr_client.ib.qualifyContracts(contract)[0]
            ticker = self.ibkr_client.ib.reqMktData(qualified)

            # Wait for price data
            for _ in range(10):
                self.ibkr_client.ib.sleep(0.5)
                if ticker.last and ticker.last > 0:
                    self.ibkr_client.ib.cancelMktData(qualified)
                    return ticker.last
                if ticker.close and ticker.close > 0:
                    self.ibkr_client.ib.cancelMktData(qualified)
                    return ticker.close

            # Fallback to close price
            if ticker.close and ticker.close > 0:
                self.ibkr_client.ib.cancelMktData(qualified)
                return ticker.close

            # Ultimate fallback - estimate based on symbol
            console.print(f"[yellow]⚠ Could not get price for {symbol}, using estimate[/yellow]")
            return 600.0 if symbol == "SPY" else 100.0

        except Exception as e:
            console.print(f"[yellow]⚠ Error getting price for {symbol}: {e}[/yellow]")
            return 600.0 if symbol == "SPY" else 100.0

    def execute_trade(self, opportunity: TradeOpportunity) -> dict:
        """Execute a single trade with full tracking.

        Args:
            opportunity: Trade opportunity to execute

        Returns:
            dict: Trade result with all details
        """
        trade_result = {
            "timestamp": datetime.now().isoformat(),
            "symbol": opportunity.symbol,
            "strike": opportunity.strike,
            "premium": opportunity.premium,
            "contracts": opportunity.contracts,
            "reasoning": opportunity.reasoning,
        }

        try:
            # Step 1: Risk check
            console.print(f"\n[cyan]Trade {opportunity.reasoning}[/cyan]")
            console.print(f"  Symbol: {opportunity.symbol} ${opportunity.strike} PUT")

            risk_check = self.risk_governor.pre_trade_check(opportunity)
            trade_result["risk_check"] = {
                "approved": risk_check.approved,
                "reason": risk_check.reason,
            }

            if not risk_check.approved:
                console.print(f"  [yellow]✗ Risk check failed: {risk_check.reason}[/yellow]")
                trade_result["status"] = "rejected"
                trade_result["success"] = False
                return trade_result

            console.print(f"  [green]✓ Risk check passed[/green]")

            # Step 2: Execute order
            result = self.order_executor.execute_trade(
                opportunity=opportunity,
                order_type="LIMIT",
                limit_price=0.05,  # Low price, likely won't fill
            )

            trade_result["order_result"] = {
                "success": result.success,
                "order_id": result.order_id,
                "status": result.status.value,
                "dry_run": result.dry_run,
            }

            if result.success:
                console.print(
                    f"  [green]✓ Order {'simulated' if result.dry_run else 'placed'}: "
                    f"ID {result.order_id}[/green]"
                )
                trade_result["status"] = "success"
                trade_result["success"] = True

                # Record trade with risk governor
                self.risk_governor.record_trade(opportunity)

            else:
                console.print(f"  [red]✗ Order failed: {result.error_message}[/red]")
                trade_result["status"] = "failed"
                trade_result["success"] = False
                trade_result["error"] = result.error_message

        except Exception as e:
            console.print(f"  [red]✗ Error: {e}[/red]")
            trade_result["status"] = "error"
            trade_result["success"] = False
            trade_result["error"] = str(e)
            self.results["errors"].append(str(e))

        return trade_result

    def run_initial_trades(self, count: int = 5):
        """Execute initial test trades.

        Args:
            count: Number of trades to execute
        """
        console.print(f"\n[bold cyan]═══ Initial Validation: {count} Trades ═══[/bold cyan]\n")

        for i in range(count):
            opportunity = self.create_test_opportunity(offset=i)
            result = self.execute_trade(opportunity)
            self.results["trades"].append(result)

            # Brief pause between trades
            if i < count - 1:
                time.sleep(2)

        self._print_summary()

    def run_full_validation(self, count: int = 20):
        """Execute full validation with 20+ trades.

        Args:
            count: Number of trades to execute
        """
        console.print(f"\n[bold cyan]═══ Full Validation: {count} Trades ═══[/bold cyan]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Executing {count} trades...", total=count)

            for i in range(count):
                opportunity = self.create_test_opportunity(offset=i)
                result = self.execute_trade(opportunity)
                self.results["trades"].append(result)

                progress.update(task, advance=1)

                # Brief pause
                time.sleep(1)

        self._print_summary()

    def test_risk_limits(self):
        """Test risk limit enforcement."""
        console.print("\n[bold cyan]═══ Testing Risk Limits ═══[/bold cyan]\n")

        # Get current risk status
        status = self.risk_governor.get_risk_status()

        console.print("[cyan]Current Risk Status:[/cyan]")
        console.print(f"  Positions: {status['current_positions']}/{status['max_positions']}")
        console.print(f"  Trades today: {status['trades_today']}/{status['max_trades_today']}")
        console.print(f"  Daily P&L: ${status['daily_pnl']:.2f} ({status['daily_pnl_pct']:.2%})")
        console.print()

        # Test 1: Max positions limit
        console.print("[cyan]Test 1: Max Positions Limit[/cyan]")

        if status["current_positions"] >= status["max_positions"]:
            console.print(
                f"  [green]✓ At max positions ({status['current_positions']}), "
                "should reject new trades[/green]"
            )

            # Try to place trade
            opportunity = self.create_test_opportunity()
            risk_check = self.risk_governor.pre_trade_check(opportunity)

            if not risk_check.approved and "Max positions" in risk_check.reason:
                console.print(f"  [green]✓ Trade correctly rejected: {risk_check.reason}[/green]")
                self.results["risk_tests"].append({
                    "test": "max_positions",
                    "result": "pass",
                    "reason": risk_check.reason,
                })
            else:
                console.print(
                    f"  [red]✗ Trade not rejected! Approved: {risk_check.approved}[/red]"
                )
                self.results["risk_tests"].append({
                    "test": "max_positions",
                    "result": "fail",
                    "reason": "Limit not enforced",
                })
        else:
            console.print(
                f"  [yellow]⊘ Not at max positions "
                f"({status['current_positions']}/{status['max_positions']})[/yellow]"
            )
            self.results["risk_tests"].append({
                "test": "max_positions",
                "result": "skip",
                "reason": "Not at limit",
            })

        console.print()

        # Test 2: Max trades per day
        console.print("[cyan]Test 2: Max Trades Per Day[/cyan]")

        if status["trades_today"] >= status["max_trades_today"]:
            console.print(
                f"  [green]✓ At max trades/day ({status['trades_today']}), "
                "should reject new trades[/green]"
            )

            opportunity = self.create_test_opportunity()
            risk_check = self.risk_governor.pre_trade_check(opportunity)

            if not risk_check.approved and "Max trades" in risk_check.reason:
                console.print(f"  [green]✓ Trade correctly rejected: {risk_check.reason}[/green]")
                self.results["risk_tests"].append({
                    "test": "max_trades_per_day",
                    "result": "pass",
                    "reason": risk_check.reason,
                })
            else:
                console.print(f"  [red]✗ Trade not rejected![/red]")
                self.results["risk_tests"].append({
                    "test": "max_trades_per_day",
                    "result": "fail",
                    "reason": "Limit not enforced",
                })
        else:
            console.print(
                f"  [yellow]⊘ Not at max trades "
                f"({status['trades_today']}/{status['max_trades_today']})[/yellow]"
            )
            self.results["risk_tests"].append({
                "test": "max_trades_per_day",
                "result": "skip",
                "reason": "Not at limit",
            })

        console.print()

    def test_emergency_stop(self):
        """Test emergency stop functionality."""
        console.print("\n[bold cyan]═══ Testing Emergency Stop ═══[/bold cyan]\n")

        # Test 1: Emergency halt
        console.print("[cyan]Test 1: Emergency Halt Response Time[/cyan]")

        start_time = time.time()
        self.risk_governor.emergency_halt("Phase 2 validation test")
        halt_time = time.time() - start_time

        console.print(f"  [green]✓ Emergency halt triggered in {halt_time*1000:.2f}ms[/green]")

        # Verify halt is active
        if self.risk_governor.is_halted():
            console.print(f"  [green]✓ Trading halted: {self.risk_governor._halt_reason}[/green]")
        else:
            console.print("  [red]✗ Trading not halted![/red]")

        self.results["emergency_tests"].append({
            "test": "halt_response_time",
            "result": "pass" if halt_time < 1.0 else "fail",
            "response_time_ms": halt_time * 1000,
            "target_ms": 1000,
        })

        # Test 2: Trade rejection while halted
        console.print("\n[cyan]Test 2: Trade Rejection While Halted[/cyan]")

        opportunity = self.create_test_opportunity()
        risk_check = self.risk_governor.pre_trade_check(opportunity)

        if not risk_check.approved and "halted" in risk_check.reason.lower():
            console.print(f"  [green]✓ Trade correctly rejected: {risk_check.reason}[/green]")
            self.results["emergency_tests"].append({
                "test": "reject_while_halted",
                "result": "pass",
                "reason": risk_check.reason,
            })
        else:
            console.print("  [red]✗ Trade not rejected while halted![/red]")
            self.results["emergency_tests"].append({
                "test": "reject_while_halted",
                "result": "fail",
                "reason": "Trade approved during halt",
            })

        # Test 3: Resume trading
        console.print("\n[cyan]Test 3: Resume Trading[/cyan]")

        start_time = time.time()
        self.risk_governor.resume_trading()
        resume_time = time.time() - start_time

        console.print(f"  [green]✓ Trading resumed in {resume_time*1000:.2f}ms[/green]")

        if not self.risk_governor.is_halted():
            console.print("  [green]✓ Trading active again[/green]")
            self.results["emergency_tests"].append({
                "test": "resume_trading",
                "result": "pass",
                "response_time_ms": resume_time * 1000,
            })
        else:
            console.print("  [red]✗ Trading still halted![/red]")
            self.results["emergency_tests"].append({
                "test": "resume_trading",
                "result": "fail",
                "reason": "Still halted after resume",
            })

        console.print()

    def _print_summary(self):
        """Print validation summary."""
        console.print("\n[bold cyan]═══ Validation Summary ═══[/bold cyan]\n")

        # Trade statistics
        total_trades = len(self.results["trades"])
        successful = sum(1 for t in self.results["trades"] if t.get("success"))
        rejected = sum(
            1 for t in self.results["trades"] if t.get("status") == "rejected"
        )
        failed = sum(1 for t in self.results["trades"] if t.get("status") == "failed")

        table = Table(title="Trade Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total Trades", str(total_trades))
        table.add_row("Successful", f"{successful} ({successful/total_trades*100:.1f}%)" if total_trades > 0 else "0")
        table.add_row("Rejected (Risk)", f"{rejected} ({rejected/total_trades*100:.1f}%)" if total_trades > 0 else "0")
        table.add_row("Failed", str(failed))
        table.add_row("Errors", str(len(self.results["errors"])))

        console.print(table)
        console.print()

        # Risk tests
        if self.results["risk_tests"]:
            risk_table = Table(title="Risk Limit Tests")
            risk_table.add_column("Test", style="cyan")
            risk_table.add_column("Result", style="green")

            for test in self.results["risk_tests"]:
                result_color = "green" if test["result"] == "pass" else "yellow" if test["result"] == "skip" else "red"
                risk_table.add_row(
                    test["test"],
                    f"[{result_color}]{test['result'].upper()}[/{result_color}]"
                )

            console.print(risk_table)
            console.print()

        # Emergency tests
        if self.results["emergency_tests"]:
            emergency_table = Table(title="Emergency Stop Tests")
            emergency_table.add_column("Test", style="cyan")
            emergency_table.add_column("Result", style="green")
            emergency_table.add_column("Details", style="white")

            for test in self.results["emergency_tests"]:
                result_color = "green" if test["result"] == "pass" else "red"
                details = ""
                if "response_time_ms" in test:
                    details = f"{test['response_time_ms']:.2f}ms"

                emergency_table.add_row(
                    test["test"],
                    f"[{result_color}]{test['result'].upper()}[/{result_color}]",
                    details
                )

            console.print(emergency_table)
            console.print()

    def save_results(self, filename: str = "phase2_validation_results.json"):
        """Save validation results to file.

        Args:
            filename: Output filename
        """
        self.results["end_time"] = datetime.now().isoformat()

        output_path = project_root / "data" / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(self.results, f, indent=2)

        console.print(f"\n[green]✓ Results saved to {output_path}[/green]")

    def cleanup(self):
        """Cleanup and disconnect."""
        if hasattr(self, "ibkr_client") and self.ibkr_client.is_connected():
            self.ibkr_client.disconnect()
            console.print("[dim]Disconnected from IBKR[/dim]")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Phase 2 Validation")
    parser.add_argument(
        "--mode",
        choices=["initial", "full", "risk-test", "emergency", "all"],
        default="initial",
        help="Validation mode to run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate orders without placing them",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Number of trades (overrides defaults)",
    )

    args = parser.parse_args()

    console.print("\n[bold cyan]╔═══════════════════════════════════════╗[/bold cyan]")
    console.print("[bold cyan]║   Phase 2 Validation - Autonomous    ║[/bold cyan]")
    console.print("[bold cyan]║      Execution Engine Testing        ║[/bold cyan]")
    console.print("[bold cyan]╚═══════════════════════════════════════╝[/bold cyan]")

    validator = Phase2Validator(dry_run=args.dry_run)

    try:
        if args.mode == "initial":
            count = args.count or 5
            validator.run_initial_trades(count=count)

        elif args.mode == "full":
            count = args.count or 20
            validator.run_full_validation(count=count)

        elif args.mode == "risk-test":
            validator.test_risk_limits()

        elif args.mode == "emergency":
            validator.test_emergency_stop()

        elif args.mode == "all":
            # Complete validation
            validator.run_initial_trades(count=5)
            time.sleep(5)
            validator.run_full_validation(count=15)
            time.sleep(5)
            validator.test_risk_limits()
            time.sleep(5)
            validator.test_emergency_stop()

        # Save results
        validator.save_results()

    except KeyboardInterrupt:
        console.print("\n\n[yellow]Validation interrupted by user[/yellow]")

    except Exception as e:
        console.print(f"\n[bold red]Validation failed:[/bold red] {e}")
        import traceback
        traceback.print_exc()

    finally:
        validator.cleanup()


if __name__ == "__main__":
    main()
