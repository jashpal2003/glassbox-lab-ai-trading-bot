import os
import sys
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("ALPACA_API_KEY")
secret_key = os.getenv("ALPACA_SECRET_KEY")
paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

print("Connecting to Alpaca Paper Trading API...", flush=True)

try:
    from alpaca.trading.client import TradingClient
    client = TradingClient(api_key, secret_key, paper=paper)
    acct = client.get_account()
    print("SUCCESS: Alpaca Account connected!", flush=True)
    print(f" - Account Status: {acct.status}", flush=True)
    print(f" - Buying Power: ${float(acct.buying_power):,.2f}", flush=True)
    print(f" - Cash: ${float(acct.cash):,.2f}", flush=True)
    print(f" - Portfolio Value: ${float(acct.portfolio_value):,.2f}", flush=True)
except Exception as e:
    print(f"Alpaca API Exception: {e}", flush=True)
