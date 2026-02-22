"""Quick script to check opportunity and trade status."""
from src.data.database import get_db_session
from src.data.models import ScanOpportunity, Trade

with get_db_session() as session:
    # Check opportunity #1
    opp = session.query(ScanOpportunity).filter(ScanOpportunity.id == 1).first()
    print('Opportunity #1:')
    print(f'  Symbol: {opp.symbol}')
    print(f'  Strike: {opp.strike}')
    print(f'  Executed: {opp.executed}')
    print(f'  Trade ID: {opp.trade_id}')
    print()

    # Check the linked trade
    if opp.trade_id:
        trade = session.query(Trade).filter(Trade.trade_id == opp.trade_id).first()
        if trade:
            print('Linked Trade:')
            print(f'  Symbol: {trade.symbol}')
            print(f'  Strike: {trade.strike}')
            print(f'  Entry Date: {trade.entry_date}')
        else:
            print('Trade not found in database')

    # Show all recent trades
    print('\nAll recent trades:')
    trades = session.query(Trade).order_by(Trade.entry_date.desc()).limit(5).all()
    for t in trades:
        print(f'  {t.trade_id}: {t.symbol} ${t.strike} - {t.entry_date}')
