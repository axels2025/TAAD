#!/usr/bin/env python3
"""
Cleanup script for invalid trade entries.

This script handles trades that were closed in IBKR but not properly
recorded in the database due to the emergency exit bug.

Options:
1. Delete trades that are missing entry snapshots
2. Delete all unclosed trades (no exit_date)
3. Full database reset (keep schema, delete all data)
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from datetime import datetime
from sqlalchemy import create_engine, text
from src.data.database import get_db_session
from src.data.models import (
    Trade,
    TradeEntrySnapshot,
    TradeExitSnapshot,
    PositionSnapshot
)


def show_database_status():
    """Show current database status."""
    with get_db_session() as session:
        total_trades = session.query(Trade).count()
        closed_trades = session.query(Trade).filter(Trade.exit_date.isnot(None)).count()
        open_trades = total_trades - closed_trades

        entry_snapshots = session.query(TradeEntrySnapshot).count()
        exit_snapshots = session.query(TradeExitSnapshot).count()
        position_snapshots = session.query(PositionSnapshot).count()

        print("\n" + "=" * 60)
        print("DATABASE STATUS")
        print("=" * 60)
        print(f"Trades:              {total_trades} total")
        print(f"  - Open:            {open_trades}")
        print(f"  - Closed:          {closed_trades}")
        print(f"Entry Snapshots:     {entry_snapshots}")
        print(f"Exit Snapshots:      {exit_snapshots}")
        print(f"Position Snapshots:  {position_snapshots}")
        print("=" * 60 + "\n")


def delete_trades_missing_entry_snapshots():
    """Delete trades that don't have entry snapshots (orphaned records)."""
    with get_db_session() as session:
        # Find trades without entry snapshots
        trades_without_entry = session.query(Trade).filter(
            ~Trade.id.in_(
                session.query(TradeEntrySnapshot.trade_id)
            )
        ).all()

        count = len(trades_without_entry)
        if count == 0:
            print("✓ No orphaned trades found (all have entry snapshots)")
            return

        print(f"\nFound {count} trades without entry snapshots:")
        for trade in trades_without_entry:
            print(f"  - Trade ID {trade.id}: {trade.symbol} {trade.strike} "
                  f"(entry: {trade.entry_date})")

        confirm = input(f"\nDelete these {count} orphaned trades? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Cancelled.")
            return

        # Delete trades
        for trade in trades_without_entry:
            session.delete(trade)

        session.commit()
        print(f"✓ Deleted {count} orphaned trades")


def delete_all_open_trades():
    """Delete all trades that don't have exit data (not closed in system)."""
    with get_db_session() as session:
        open_trades = session.query(Trade).filter(
            Trade.exit_date.is_(None)
        ).all()

        count = len(open_trades)
        if count == 0:
            print("✓ No open trades found (all trades are closed)")
            return

        print(f"\nFound {count} open trades (no exit data in database):")
        for trade in open_trades:
            print(f"  - Trade ID {trade.id}: {trade.symbol} ${trade.strike} "
                  f"{trade.option_type} (entry: {trade.entry_date})")

        print("\n⚠️  WARNING: This will delete all trades without exit data.")
        print("   If these positions were closed in IBKR but not recorded,")
        print("   you will lose the entry data as well.")

        confirm = input(f"\nDelete these {count} open trades? (yes/no): ")
        if confirm.lower() != 'yes':
            print("Cancelled.")
            return

        # Delete associated snapshots first (due to foreign keys)
        for trade in open_trades:
            # Delete entry snapshots
            session.query(TradeEntrySnapshot).filter(
                TradeEntrySnapshot.trade_id == trade.id
            ).delete()

            # Delete position snapshots
            session.query(PositionSnapshot).filter(
                PositionSnapshot.trade_id == trade.id
            ).delete()

        # Delete trades
        for trade in open_trades:
            session.delete(trade)

        session.commit()
        print(f"✓ Deleted {count} open trades and their snapshots")


def reset_all_data():
    """Complete database reset - delete all data but keep schema."""
    with get_db_session() as session:
        trades_count = session.query(Trade).count()
        entry_count = session.query(TradeEntrySnapshot).count()
        exit_count = session.query(TradeExitSnapshot).count()
        position_count = session.query(PositionSnapshot).count()

        total = trades_count + entry_count + exit_count + position_count

        if total == 0:
            print("✓ Database is already empty")
            return

        print("\n⚠️  WARNING: COMPLETE DATABASE RESET")
        print("=" * 60)
        print("This will delete ALL data:")
        print(f"  - {trades_count} trades")
        print(f"  - {entry_count} entry snapshots")
        print(f"  - {exit_count} exit snapshots")
        print(f"  - {position_count} position snapshots")
        print("=" * 60)
        print("\nThe database schema will be preserved.")
        print("This action CANNOT be undone.")

        confirm = input("\nType 'RESET' to confirm complete reset: ")
        if confirm != 'RESET':
            print("Cancelled.")
            return

        # Delete in order (foreign key dependencies)
        session.query(PositionSnapshot).delete()
        session.query(TradeExitSnapshot).delete()
        session.query(TradeEntrySnapshot).delete()
        session.query(Trade).delete()

        session.commit()
        print("\n✓ Database reset complete - all data deleted")


def main():
    """Main menu for cleanup operations."""
    while True:
        show_database_status()

        print("CLEANUP OPTIONS:")
        print("1. Delete trades missing entry snapshots (orphaned records)")
        print("2. Delete all open trades (no exit data)")
        print("3. RESET ALL DATA (complete wipe)")
        print("4. Show status and exit")
        print("0. Exit")

        choice = input("\nSelect option (0-4): ").strip()

        if choice == '0':
            print("\nExiting...")
            break
        elif choice == '1':
            delete_trades_missing_entry_snapshots()
        elif choice == '2':
            delete_all_open_trades()
        elif choice == '3':
            reset_all_data()
        elif choice == '4':
            print("\nExiting...")
            break
        else:
            print("\n❌ Invalid option")


if __name__ == "__main__":
    main()
