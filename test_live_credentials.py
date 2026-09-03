import os
from dotenv import load_dotenv

load_dotenv()

print("--- Testing Alpaca Credentials ---")
api_key = os.getenv("ALPACA_API_KEY")
secret_key = os.getenv("ALPACA_SECRET_KEY")
base_url = os.getenv("ALPACA_BASE_URL")
paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

print(f"API Key: {api_key[:6]}... (length {len(api_key)})")
print(f"Secret Key: {secret_key[:6]}... (length {len(secret_key)})")

try:
    from alpaca.trading.client import TradingClient
    client = TradingClient(api_key, secret_key, paper=paper)
    acct = client.get_account()
    print("Alpaca Account fetched successfully!")
    print(f"Account Number: {acct.account_number}")
    print(f"Status: {acct.status}")
    print(f"Buying Power: ${float(acct.buying_power):,.2f}")
    print(f"Cash: ${float(acct.cash):,.2f}")
    print(f"Portfolio Value: ${float(acct.portfolio_value):,.2f}")
    positions = client.get_all_positions()
    print(f"Positions count: {len(positions)}")
except Exception as e:
    print(f"Alpaca Error: {e}")

print("\n--- Testing Gemini Credentials ---")
gemini_key = os.getenv("GEMINI_API_KEY")
gemini_model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
print(f"Gemini Key: {gemini_key[:6]}... (length {len(gemini_key)})")
print(f"Gemini Model: {gemini_model}")

try:
    import google.generativeai as genai
    genai.configure(api_key=gemini_key)
    
    # List available models
    print("Listing available models:")
    available = []
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            available.append(m.name)
            print(f" - {m.name}")
            
    # Test generation
    test_model = gemini_model
    if not test_model.startswith("models/"):
        test_model_name = f"models/{test_model}" if f"models/{test_model}" in available else test_model
    else:
        test_model_name = test_model
        
    print(f"Testing generation with {gemini_model}...")
    model = genai.GenerativeModel(model_name=gemini_model)
    res = model.generate_content("Respond with exactly: 'GlassBox AI Ready'")
    print(f"Gemini Response: {res.text.strip()}")
except Exception as e:
    print(f"Gemini Error: {e}")
