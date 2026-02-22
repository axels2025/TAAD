#!/usr/bin/env python
"""Reset environment script.

WARNING: This script will delete all data! Use only for development/testing.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import shutil

import typer
from rich.console import Console

from src.data.database import reset_database

console = Console()


def main() -> None:
    """Reset the development environment."""
    console.print("[bold red]WARNING: This will DELETE ALL DATA![/bold red]")
    console.print("This includes:")
    console.print("  - All database records")
    console.print("  - All log files")
    console.print("  - All cached data")

    confirm = typer.confirm("\nAre you absolutely sure you want to continue?")

    if not confirm:
        console.print("[yellow]Reset cancelled[/yellow]")
        return

    try:
        console.print("\n[bold blue]Resetting environment...[/bold blue]\n")

        # Reset database
        console.print("Resetting database...")
        reset_database()
        console.print("✓ Database reset")

        # Clear logs
        console.print("Clearing logs...")
        logs_dir = Path("logs")
        if logs_dir.exists():
            for log_file in logs_dir.glob("*.log*"):
                log_file.unlink()
        console.print("✓ Logs cleared")

        # Clear cache
        console.print("Clearing cache...")
        cache_dir = Path("data/cache")
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            cache_dir.mkdir()
        console.print("✓ Cache cleared")

        # Clear exports
        console.print("Clearing exports...")
        exports_dir = Path("data/exports")
        if exports_dir.exists():
            shutil.rmtree(exports_dir)
            exports_dir.mkdir()
        console.print("✓ Exports cleared")

        console.print(
            "\n[bold green]✓ Environment reset complete![/bold green]"
        )
        console.print("\n[yellow]Run setup_database.py to reinitialize[/yellow]")

    except Exception as e:
        console.print(f"\n[bold red]✗ Reset failed: {e}[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
