"""
tests/test_replay.py - Unit tests for Cryptographic Hasher and Independent Replay Verifier.
"""

from shared.schemas import Intent, OptionLeg, MarketContext, AccountState, KernelDecision
from verification.snapshot import create_audit_snapshot, compute_decision_hash
from verification.replay_verifier import ReplayVerifier

def test_hash_integrity_and_replay_verification():
    intent = Intent(
        underlying="SPY",
        structure="iron_condor",
        legs=[
            OptionLeg(action="sell", type="put", strike=640, expiry="2026-09-19"),
            OptionLeg(action="buy", type="put", strike=635, expiry="2026-09-19"),
            OptionLeg(action="sell", type="call", strike=660, expiry="2026-09-19"),
            OptionLeg(action="buy", type="call", strike=665, expiry="2026-09-19"),
        ],
        size=2,
        rationale="IV rank 78, VRP +5.2pts, no earnings in window",
        conviction=0.71,
        regime_tags=["sell_premium"]
    )
    
    account = AccountState(
        buying_power=48210.55,
        cash=48210.55,
        portfolio_value=48210.55,
        positions=[]
    )
    
    ctx = MarketContext(
        underlying="SPY",
        underlying_price=651.20,
        iv_rank=78.0,
        realized_vol=17.2,
        vrp=5.2,
        vix=16.4,
        account_state=account
    )
    
    decision = KernelDecision(
        intent_id=intent.intent_id,
        approved=True,
        decision="APPROVED",
        kernel_checks=[]
    )
    
    # Create immutable snapshot
    snapshot = create_audit_snapshot(ctx, account, intent, decision, order_id="alpaca-test-123")
    assert snapshot.hash.startswith("sha256:")
    
    # Replay verify
    verifier = ReplayVerifier()
    result = verifier.verify_decision(snapshot)
    
    assert result.integrity is True
    assert result.condition_supported is True
    assert result.overall_status == "PASS"
    assert result.recomputed_hash == snapshot.hash


def test_tamper_detection_fails_integrity():
    intent = Intent(
        underlying="SPY",
        structure="iron_condor",
        legs=[
            OptionLeg(action="sell", type="put", strike=640, expiry="2026-09-19"),
            OptionLeg(action="buy", type="put", strike=635, expiry="2026-09-19"),
            OptionLeg(action="sell", type="call", strike=660, expiry="2026-09-19"),
            OptionLeg(action="buy", type="call", strike=665, expiry="2026-09-19"),
        ],
        size=1,
        rationale="IV rank 78, VRP +5.2pts",
        conviction=0.7
    )
    account = AccountState(buying_power=50000.0, cash=50000.0, portfolio_value=50000.0)
    ctx = MarketContext(underlying="SPY", underlying_price=651.2, iv_rank=78, realized_vol=17, vrp=5.2, vix=16.4, account_state=account)
    decision = KernelDecision(intent_id=intent.intent_id, approved=True, decision="APPROVED", kernel_checks=[])
    
    snapshot = create_audit_snapshot(ctx, account, intent, decision)
    
    # Tamper with stored market price after recording
    snapshot.market_state["SPY_price"] = 999.99
    
    verifier = ReplayVerifier()
    result = verifier.verify_decision(snapshot)
    
    # Cryptographic integrity must detect discrepancy
    assert result.integrity is False
    assert result.overall_status == "FAIL"
