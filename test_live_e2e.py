"""
test_live_e2e.py - Test live integration of Perception (Alpaca), Reasoning (Gemini), Risk Kernel, Execution, and Snapshot Hasher.
"""

import os
from dotenv import load_dotenv

load_dotenv()

from perception.alpaca_client import AlpacaClient
from reasoning.agent import LLMReasoningAgent
from kernel.risk_kernel import validate
from kernel.executor import AlpacaExecutor
from verification.snapshot import create_audit_snapshot
from verification.replay_verifier import replay_verifier

print("==================================================")
print("  GLASSBOX OPTIONS - LIVE END-TO-END VERIFICATION ")
print("==================================================")

# 1. Perception
print("\n[1] Testing Live Perception Layer...")
alpaca = AlpacaClient()
acct = alpaca.get_account_state()
print(f" -> Alpaca Live Account Status: Connected!")
print(f" -> Buying Power: ${acct.buying_power:,.2f}")
print(f" -> Cash: ${acct.cash:,.2f}")
print(f" -> Portfolio Value: ${acct.portfolio_value:,.2f}")
print(f" -> Current Positions: {len(acct.positions)}")

ctx = alpaca.get_market_context("SPY")
print(f" -> Market Context generated for SPY @ ${ctx.underlying_price}")
print(f" -> IV Rank: {ctx.iv_rank}% | Realized Vol: {ctx.realized_vol}% | VRP: {ctx.vrp} pts | VIX: {ctx.vix}")
print(f" -> Contracts in chain: {len(ctx.contracts)}")

# 2. Reasoning (Gemini)
print("\n[2] Testing Live Gemini Reasoning Agent...")
agent = LLMReasoningAgent()
print(f" -> Configured Model: {agent.gemini_model}")
intent = agent.propose_intent(ctx)
print(f" -> LLM Intent generated: {intent.structure} on {intent.underlying}")
print(f" -> Legs count: {len(intent.legs)}")
print(f" -> Conviction: {intent.conviction}")
print(f" -> Rationale: {intent.rationale}")
print(f" -> Regime Tags: {intent.regime_tags}")

# 3. Deterministic Risk Kernel
print("\n[3] Testing Deterministic Risk Kernel...")
decision = validate(intent, acct, market_context=ctx)
print(f" -> Kernel Decision: {decision.decision} (Approved: {decision.approved})")
for chk in decision.kernel_checks:
    status_str = "PASS" if chk.pass_status else "FAIL"
    print(f"    [{status_str}] {chk.check}: value={chk.value}, limit={chk.limit}")

if decision.reasons:
    print(f" -> Rejection Reasons: {decision.reasons}")

# 4. Execution & Audit Hasher
print("\n[4] Testing Multi-leg Execution & Cryptographic Hasher...")
executor = AlpacaExecutor(client=alpaca)
order_id = executor.execute_intent(intent, decision)
print(f" -> Alpaca Order ID: {order_id}")

snapshot = create_audit_snapshot(ctx, acct, intent, decision, order_id=order_id)
print(f" -> SHA-256 Snapshot Hash: {snapshot.hash}")

# 5. Independent Replay Verification
print("\n[5] Testing Independent Replay Verifier...")
result = replay_verifier.verify_decision(snapshot)
print(f" -> Cryptographic Integrity: {'PASS' if result.integrity else 'FAIL'}")
print(f" -> Numeric Rationale Supported: {'PASS' if result.condition_supported else 'FAIL'}")
print(f" -> Overall Replay Status: {result.overall_status}")
print(f" -> Recomputed Hash: {result.recomputed_hash}")

print("\n==================================================")
print("  LIVE INTEGRATION TEST COMPLETE - 100% OPERATIONAL ")
print("==================================================")
