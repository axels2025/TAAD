"""Reset an opportunity to unexecuted status."""
import sys
from src.data.database import get_db_session
from src.data.models import ScanOpportunity

def reset_opportunity(opportunity_id: int):
    """Reset an opportunity to unexecuted status.

    Args:
        opportunity_id: ID of the opportunity to reset
    """
    with get_db_session() as session:
        opp = session.query(ScanOpportunity).filter(
            ScanOpportunity.id == opportunity_id
        ).first()

        if not opp:
            print(f"✗ Opportunity #{opportunity_id} not found")
            return

        print(f"Found: {opp.symbol} ${opp.strike} expiring {opp.expiration}")
        print(f"Current status: executed={opp.executed}, trade_id={opp.trade_id}")

        # Reset to unexecuted
        opp.executed = False
        opp.trade_id = None
        opp.validation_status = "pending"

        session.commit()
        print(f"✓ Reset opportunity #{opportunity_id} to unexecuted status")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/reset_opportunity.py <opportunity_id>")
        print("\nExample:")
        print("  python scripts/reset_opportunity.py 1")
        sys.exit(1)

    opp_id = int(sys.argv[1])
    reset_opportunity(opp_id)
