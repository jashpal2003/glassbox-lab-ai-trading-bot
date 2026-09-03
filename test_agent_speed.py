import time
from dotenv import load_dotenv

load_dotenv()

from perception.alpaca_client import AlpacaClient
from reasoning.agent import LLMReasoningAgent

print("Testing agent.propose_intent() latency...", flush=True)
client = AlpacaClient()
ctx = client.get_market_context("SPY")

agent = LLMReasoningAgent()
t0 = time.time()
intent = agent.propose_intent(ctx)
dt = time.time() - t0

print(f"Time taken: {dt:.2f}s", flush=True)
print(f"Intent structure: {intent.structure}", flush=True)
print(f"Intent rationale: {intent.rationale}", flush=True)
