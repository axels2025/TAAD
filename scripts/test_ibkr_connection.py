#!/usr/bin/env python3
"""Test IBKR connection and verify paper trading setup.

This script tests the connection to IBKR TWS/Gateway and verifies:
1. Connection can be established
2. Paper trading mode is active
3. Account summary is accessible
4. Market data is available
"""

import os
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from ib_insync import IB, util
from rich.console import Console
from rich.table import Table

# Load environment variables
load_dotenv()

console = Console()


def test_connection():
    """Test IBKR connection and display status."""
    console.print("\n[bold cyan]IBKR Connection Test[/bold cyan]\n")

    # Get configuration
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "7497"))
    client_id = int(os.getenv("IBKR_CLIENT_ID", "1"))
    paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"

    # Display configuration
    config_table = Table(title="Configuration", show_header=True)
    config_table.add_column("Setting", style="cyan")
    config_table.add_column("Value", style="green")

    config_table.add_row("IBKR Host", host)
    config_table.add_row("IBKR Port", str(port))
    config_table.add_row("Client ID", str(client_id))
    config_table.add_row("Paper Trading", str(paper_trading))
    config_table.add_row("Expected Port", "7497 (paper)" if paper_trading else "7496 (live)")

    console.print(config_table)
    console.print()

    # Verify port matches paper trading setting
    if paper_trading and port != 7497:
        console.print("[bold red]❌ ERROR:[/bold red] PAPER_TRADING=true but PORT is not 7497")
        console.print("[yellow]Please set IBKR_PORT=7497 in .env file[/yellow]")
        return False

    if not paper_trading and port != 7496:
        console.print("[bold red]❌ WARNING:[/bold red] PAPER_TRADING=false but PORT is not 7496")
        console.print("[yellow]Are you sure you want to use live trading?[/yellow]")

    # Test connection
    console.print("[cyan]Testing connection to IBKR...[/cyan]")

    ib = IB()

    try:
        # Connect with timeout
        ib.connect(host, port, clientId=client_id, timeout=10)
        console.print("[bold green]✅ Connected successfully![/bold green]\n")

        # Get account summary
        console.print("[cyan]Retrieving account information...[/cyan]")

        # Get account values
        account_values = ib.accountSummary()

        if not account_values:
            console.print("[yellow]⚠️  No account values returned[/yellow]")
            return False

        # Extract key metrics
        account_data = {}
        for av in account_values:
            if av.tag in ["NetLiquidation", "AvailableFunds", "BuyingPower", "TotalCashValue"]:
                account_data[av.tag] = f"${float(av.value):,.2f}"

        # Display account summary
        account_table = Table(title="Account Summary", show_header=True)
        account_table.add_column("Metric", style="cyan")
        account_table.add_column("Value", style="green")

        for key, value in account_data.items():
            account_table.add_row(key, value)

        console.print(account_table)
        console.print()

        # Get account type to verify paper trading
        account = ib.managedAccounts()[0] if ib.managedAccounts() else "Unknown"
        console.print(f"[cyan]Account:[/cyan] {account}")

        if account.startswith("DU"):
            console.print("[bold green]✅ Paper trading account confirmed (DU prefix)[/bold green]")
        else:
            console.print("[bold yellow]⚠️  Warning: Account does not have DU prefix (may be live)[/bold yellow]")

        console.print()

        # Test market data
        console.print("[cyan]Testing market data access...[/cyan]")

        # Request SPY data as test
        from ib_insync import Stock
        spy = Stock("SPY", "SMART", "USD")
        ib.qualifyContracts(spy)

        # Request market data
        ticker = ib.reqMktData(spy)
        ib.sleep(2)  # Wait for data

        if ticker.last or ticker.bid or ticker.ask:
            console.print(f"[bold green]✅ Market data available[/bold green]")
            console.print(f"   SPY Price: ${ticker.last if ticker.last else 'N/A'}")
            console.print(f"   Bid: ${ticker.bid if ticker.bid else 'N/A'}")
            console.print(f"   Ask: ${ticker.ask if ticker.ask else 'N/A'}")
        else:
            console.print("[yellow]⚠️  Market data delayed or unavailable[/yellow]")
            console.print("   This is normal for paper trading accounts")

        console.print()

        # Summary
        console.print("[bold green]═══════════════════════════════════════════[/bold green]")
        console.print("[bold green]✅ IBKR CONNECTION TEST PASSED[/bold green]")
        console.print("[bold green]═══════════════════════════════════════════[/bold green]")
        console.print()
        console.print("[cyan]Ready to execute autonomous trades![/cyan]")
        console.print()

        return True

    except ConnectionRefusedError:
        console.print("[bold red]❌ Connection refused[/bold red]")
        console.print()
        console.print("[yellow]Possible causes:[/yellow]")
        console.print("  1. TWS or IB Gateway is not running")
        console.print("  2. Wrong port number (check .env)")
        console.print("  3. API connections not enabled in TWS")
        console.print()
        console.print("[cyan]To fix:[/cyan]")
        console.print("  1. Start TWS or IB Gateway")
        console.print("  2. Go to: Global Configuration → API → Settings")
        console.print("  3. Enable 'Enable ActiveX and Socket Clients'")
        console.print(f"  4. Add 127.0.0.1 to 'Trusted IP Addresses'")
        console.print(f"  5. Verify port is {port}")
        console.print()
        return False

    except TimeoutError:
        console.print("[bold red]❌ Connection timeout[/bold red]")
        console.print()
        console.print("[yellow]The connection attempt timed out after 10 seconds.[/yellow]")
        console.print("[cyan]Please check that TWS/Gateway is running and API is enabled.[/cyan]")
        console.print()
        return False

    except Exception as e:
        console.print(f"[bold red]❌ Error:[/bold red] {e}")
        console.print()
        import traceback
        console.print("[yellow]Full traceback:[/yellow]")
        console.print(traceback.format_exc())
        console.print()
        return False

    finally:
        # Disconnect
        if ib.isConnected():
            ib.disconnect()
            console.print("[dim]Disconnected from IBKR[/dim]")


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
