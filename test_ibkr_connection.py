from ib_insync import IB
import os
from dotenv import load_dotenv
load_dotenv()
print("Testing IBKR connection...")
ib = IB()
try:
ib.connect(
host=os.getenv("IBKR_HOST", "127.0.0.1"),
port=int(os.getenv("IBKR_PORT", 7497)),
clientId=int(os.getenv("IBKR_CLIENT_ID", 1))
)
print(" Connection successful!")
print(f" Account: {ib.managedAccounts()}")
# Test market data
from ib_insync import Stock
spy = Stock('SPY', 'SMART', 'USD')
ib.qualifyContracts(spy)
ticker = ib.reqMktData(spy)
ib.sleep(2)
print(f" Market data working!")
print(f" SPY: ${ticker.last}")
ib.disconnect()
print(" All tests passed!")
except Exception as e:
print(f" Error: {e}")
print("\nTroubleshooting:")
print("1. Is TWS/IB Gateway running?")
print("2. Is it logged into PAPER TRADING account?")
print("3. Is API enabled in settings?")
print("4. Is port 7497 correct?")
