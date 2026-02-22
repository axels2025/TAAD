#!/usr/bin/env python3
"""Database migration: Add order reconciliation columns.

This migration adds 5 new columns to the 'trades' table required for
Phase C order reconciliation functionality:
- reconciled_at: When reconciliation last ran
- tws_status: Actual status from TWS
- commission: Commission charged
- fill_time: Exact time of fill
- fill_price_discrepancy: Price difference if changed

Usage:
    python scripts/migrate_reconciliation_columns.py
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config.base import get_config


def check_columns_exist(cursor):
    """Check if reconciliation columns already exist."""
    cursor.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}

    required_columns = {
        'reconciled_at',
        'tws_status',
        'commission',
        'fill_time',
        'fill_price_discrepancy'
    }

    return required_columns.issubset(columns)


def add_reconciliation_columns(db_path: str):
    """Add reconciliation columns to the trades table.

    Args:
        db_path: Path to SQLite database file
    """
    print(f"🔍 Checking database: {db_path}")

    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        print("   Create the database first with: python -m src.cli.main init")
        return False

    # Backup database first
    backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"📦 Creating backup: {backup_path}")

    import shutil
    shutil.copy2(db_path, backup_path)
    print(f"✅ Backup created successfully")

    # Connect to database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if columns already exist
        if check_columns_exist(cursor):
            print("✅ Reconciliation columns already exist - no migration needed")
            conn.close()
            return True

        print("📝 Adding reconciliation columns...")

        # Add columns one by one (SQLite doesn't support multiple columns in one ALTER)
        migrations = [
            ("reconciled_at", "ALTER TABLE trades ADD COLUMN reconciled_at DATETIME"),
            ("tws_status", "ALTER TABLE trades ADD COLUMN tws_status VARCHAR(50)"),
            ("commission", "ALTER TABLE trades ADD COLUMN commission FLOAT"),
            ("fill_time", "ALTER TABLE trades ADD COLUMN fill_time DATETIME"),
            ("fill_price_discrepancy", "ALTER TABLE trades ADD COLUMN fill_price_discrepancy FLOAT"),
        ]

        for column_name, sql in migrations:
            try:
                cursor.execute(sql)
                print(f"  ✅ Added column: {column_name}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print(f"  ⏭️  Column '{column_name}' already exists - skipping")
                else:
                    raise

        # Commit changes
        conn.commit()
        print("✅ Migration completed successfully")

        # Verify
        print("\n🔍 Verifying migration...")
        if check_columns_exist(cursor):
            print("✅ All reconciliation columns verified")

            # Show sample of new columns
            cursor.execute("SELECT id, reconciled_at, tws_status, commission FROM trades LIMIT 5")
            rows = cursor.fetchall()
            print(f"\n📊 Sample data (first 5 rows):")
            print(f"{'ID':<6} {'reconciled_at':<20} {'tws_status':<15} {'commission':<10}")
            print("-" * 60)
            for row in rows:
                print(f"{row[0]:<6} {str(row[1] or 'NULL'):<20} {str(row[2] or 'NULL'):<15} {str(row[3] or 'NULL'):<10}")

            if not rows:
                print("  (No trades in database yet)")
        else:
            print("❌ Verification failed - some columns missing")
            return False

        conn.close()

        print(f"\n🎉 Migration complete!")
        print(f"\n📍 Next steps:")
        print(f"   1. Test reconciliation: python -m src.cli.main sync-orders")
        print(f"   2. If something goes wrong, restore backup:")
        print(f"      cp {backup_path} {db_path}")

        return True

    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        conn.rollback()
        conn.close()

        print(f"\n🔄 Restoring from backup...")
        import shutil
        shutil.copy2(backup_path, db_path)
        print(f"✅ Database restored from backup")

        return False


def main():
    """Run migration."""
    print("=" * 70)
    print("📊 Order Reconciliation Columns Migration")
    print("=" * 70)
    print()

    # Load config to get database path
    try:
        config = get_config()
        db_url = config.database_url

        # Extract path from SQLite URL
        if db_url.startswith('sqlite:///'):
            db_path = db_url.replace('sqlite:///', '')
            # Convert relative path to absolute
            if not os.path.isabs(db_path):
                db_path = os.path.join(project_root, db_path)
        else:
            print(f"❌ Only SQLite databases are supported by this migration script")
            print(f"   Your DATABASE_URL: {db_url}")
            print(f"\n   For PostgreSQL, use Alembic migrations instead:")
            print(f"   alembic upgrade head")
            return 1

        success = add_reconciliation_columns(db_path)

        if success:
            print("\n✅ Migration successful - you can now use order reconciliation features!")
            return 0
        else:
            print("\n❌ Migration failed - see errors above")
            return 1

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
