"""
tests/test_kernel.py - Unit tests for deterministic Risk Kernel checks and boundaries.
"""

import pytest
from shared.schemas import Intent, OptionLeg, AccountState, MarketContext, Position
from kernel.risk_kernel import validate
from kernel.kill_switch import kill_switch

def test_valid_iron_condor_approval():
    kill_switch.reset()
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
        rationale="IV rank 78, VRP +5.2pts, no earnings in window",
        conviction=0.75,
        regime_tags=["sell_premium"]
    )
    
    account = AccountState(
        buying_power=50000.0,
        cash=50000.0,
        portfolio_value=50000.0,
        positions=[],
        portfolio_delta=0.0,
        portfolio_vega=-50.0
    )
    
    decision = validate(intent, account)
    assert decision.approved is True
    assert decision.decision == "APPROVED"
    assert len(decision.reasons) == 0


def test_naked_leg_rejected():
    kill_switch.reset()
    # Credit spread put missing long protection leg
    intent = Intent(
        underlying="SPY",
        structure="credit_spread_put",
        legs=[
            OptionLeg(action="sell", type="put", strike=640, expiry="2026-09-19"),
            OptionLeg(action="sell", type="put", strike=635, expiry="2026-09-19"), # Both sells -> naked!
        ],
        size=1,
        rationale="Naked test",
        conviction=0.5
    )
    
    account = AccountState(
        buying_power=50000.0,
        cash=50000.0,
        portfolio_value=50000.0,
        positions=[]
    )
    
    decision = validate(intent, account)
    assert decision.approved is False
    assert decision.decision == "REJECTED"
    assert any("naked" in r.lower() or "defined-risk" in r.lower() for r in decision.reasons)


def test_excessive_vega_rejected():
    kill_switch.reset()
    intent = Intent(
        underlying="SPY",
        structure="iron_condor",
        legs=[
            OptionLeg(action="sell", type="put", strike=640, expiry="2026-09-19"),
            OptionLeg(action="buy", type="put", strike=635, expiry="2026-09-19"),
            OptionLeg(action="sell", type="call", strike=660, expiry="2026-09-19"),
            OptionLeg(action="buy", type="call", strike=665, expiry="2026-09-19"),
        ],
        size=10, # Large size will push vega beyond limit
        rationale="High size test",
        conviction=0.8
    )
    
    # Starting with heavy short vega already at -200
    account = AccountState(
        buying_power=100000.0,
        cash=100000.0,
        portfolio_value=100000.0,
        positions=[],
        portfolio_vega=-200.0
    )
    
    decision = validate(intent, account)
    assert decision.approved is False
    assert decision.decision == "REJECTED"
    assert any("Vega would push portfolio" in r for r in decision.reasons)
