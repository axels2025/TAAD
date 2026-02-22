#!/usr/bin/env python
"""Database setup script.

This script initializes the database, runs migrations, and sets up
the initial schema for the trading system.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from rich.console import Console

from src.config.base import get_config
from src.config.logging import setup_logging
from src.data.database import init_database

console = Console()


def main() -> None:
    """Setup database."""
    try:
        console.print("[bold blue]Setting up database...[/bold blue]\n")

        # Load configuration
        config = get_config()
        console.print(f"✓ Configuration loaded")
        console.print(f"  Database URL: {config.database_url}")

        # Setup logging
        setup_logging(log_level="INFO", log_file="logs/setup.log")
        console.print(f"✓ Logging configured")

        # Ensure directories exist
        config.ensure_directories()
        console.print(f"✓ Required directories created")

        # Initialize database
        engine = init_database()
        console.print(f"✓ Database initialized")
        console.print(f"  Engine: {engine.url}")

        console.print("\n[bold green]✓ Database setup complete![/bold green]")
        console.print(
            "\n[yellow]Next steps:[/yellow]"
        )
        console.print("1. Run migrations: alembic upgrade head")
        console.print("2. Test IBKR connection: python -m src.cli.main test-ibkr")
        console.print("3. Check system status: python -m src.cli.main status")

    except Exception as e:
        console.print(f"\n[bold red]✗ Setup failed: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
