"""Mark a manual trade as executed in the database.

Use this to manually update the database when a trade was executed
but the database wasn't updated.
"""

import sys
from datetime import datetime

from src.data.database import get_db_session
from src.data.models import ScanOpportunity
from src.data.repositories import ScanRepository, TradeRepository


def mark_trade_executed(opportunity_id: int, trade_id: str = None):
    """Mark an opportunity as executed.

    Args:
        opportunity_id: ID of the opportunity to mark
        trade_id: Optional trade ID to link (will use most recent if not provided)
    """
    with get_db_session() as session:
        scan_repo = ScanRepository(session)

        # Get the opportunity
        opp = session.query(ScanOpportunity).filter(
            ScanOpportunity.id == opportunity_id
        ).first()

        if not opp:
            print(f"✗ Opportunity #{opportunity_id} not found")
            return

        print(f"Found: {opp.symbol} ${opp.strike} expiring {opp.expiration}")

        # Get most recent trade if trade_id not provided
        if not trade_id:
            trade_repo = TradeRepository(session)
            recent_trades = trade_repo.get_recent_trades(days=7)  # Get last 7 days
            recent_trades = recent_trades[:5]  # Limit to 5 most recent

            if not recent_trades:
                print("✗ No recent trades found. Provide trade_id manually.")
                return

            # Show recent trades to help user choose
            print("\nRecent trades:")
            for i, trade in enumerate(recent_trades, 1):
                print(f"  {i}. {trade.symbol} ${trade.strike} - {trade.trade_id}")

            # Use most recent
            trade_id = recent_trades[0].trade_id
            print(f"\nUsing most recent trade: {trade_id}")

        # Mark as executed
        scan_repo.mark_opportunity_executed(opportunity_id, trade_id)
        print(f"✓ Marked opportunity #{opportunity_id} as executed (trade: {trade_id})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/mark_trade_executed.py <opportunity_id> [trade_id]")
        print("\nExample:")
        print("  python scripts/mark_trade_executed.py 1")
        print("  python scripts/mark_trade_executed.py 1 TRADE_20260126_001")
        sys.exit(1)

    opp_id = int(sys.argv[1])
    trade_id = sys.argv[2] if len(sys.argv) > 2 else None

    mark_trade_executed(opp_id, trade_id)
