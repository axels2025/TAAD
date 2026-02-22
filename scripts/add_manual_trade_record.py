"""Manually add a trade record to the database for an already-placed IBKR order."""
import sys
from datetime import datetime, date
from src.data.database import get_db_session
from src.data.models import Trade
from src.data.repositories import TradeRepository

def add_trade_record(
    symbol: str,
    strike: float,
    expiration: str,  # YYYY-MM-DD
    entry_premium: float,
    contracts: int,
    ibkr_order_id: int = None,
    otm_pct: float = None,
    dte: int = None,
):
    """Add a trade record to the database.

    Args:
        symbol: Stock symbol
        strike: Strike price
        expiration: Expiration date (YYYY-MM-DD)
        entry_premium: Premium received
        contracts: Number of contracts
        ibkr_order_id: IBKR order ID (optional)
        otm_pct: OTM percentage (optional)
        dte: Days to expiration (optional)
    """
    with get_db_session() as session:
        repo = TradeRepository(session)

        # Generate trade_id
        if ibkr_order_id:
            trade_id = f"T{ibkr_order_id}"
        else:
            # Use timestamp
            trade_id = f"MANUAL_{datetime.now().timestamp()}"

        # Parse expiration
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()

        # Calculate DTE if not provided
        if dte is None:
            dte = (exp_date - date.today()).days

        # Create trade record
        trade = Trade(
            trade_id=trade_id,
            symbol=symbol,
            strike=strike,
            expiration=exp_date,
            option_type="PUT",
            entry_date=datetime.now(),
            entry_premium=entry_premium,
            contracts=contracts,
            otm_pct=otm_pct,
            dte=dte,
            ai_reasoning="Manually entered trade",
        )

        repo.create(trade)
        print(f"✓ Created trade record: {trade_id}")
        print(f"  Symbol: {symbol} ${strike} PUT")
        print(f"  Expiration: {expiration}")
        print(f"  Premium: ${entry_premium}")
        print(f"  Contracts: {contracts}")
        print(f"  DTE: {dte}")
        if otm_pct:
            print(f"  OTM: {otm_pct*100:.1f}%")

        print(f"\n  No entry snapshot captured (no IBKR connection).")
        print(f"  Run: python -m scripts.backfill_entry_snapshots")
        print(f"  to backfill snapshot data while IBKR is connected.")

        return trade_id

if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Usage: python scripts/add_manual_trade_record.py <symbol> <strike> <expiration> <premium> <contracts> [ibkr_order_id] [otm_pct]")
        print("\nExample:")
        print("  python scripts/add_manual_trade_record.py SLV 80 2026-01-30 0.43 5")
        print("  python scripts/add_manual_trade_record.py SLV 80 2026-01-30 0.43 5 123456 0.192")
        sys.exit(1)

    symbol = sys.argv[1]
    strike = float(sys.argv[2])
    expiration = sys.argv[3]
    premium = float(sys.argv[4])
    contracts = int(sys.argv[5])
    ibkr_order_id = int(sys.argv[6]) if len(sys.argv) > 6 else None
    otm_pct = float(sys.argv[7]) if len(sys.argv) > 7 else None

    trade_id = add_trade_record(symbol, strike, expiration, premium, contracts, ibkr_order_id, otm_pct)

    # Ask if should link to opportunity
    opp_id_input = input("\nLink to opportunity ID? (press Enter to skip): ")
    if opp_id_input.strip():
        opp_id = int(opp_id_input)

        with get_db_session() as session:
            from src.data.repositories import ScanRepository
            scan_repo = ScanRepository(session)
            scan_repo.mark_opportunity_executed(opp_id, trade_id)
            print(f"✓ Linked opportunity #{opp_id} to trade {trade_id}")
