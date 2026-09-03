"""
tests/test_reconciliation.py - Unit tests for Automated Reconciliation Loop & Fault Injection (Act 2).
"""

from verification.reconciliation import ReconciliationEngine
from verification.fault_injector import fault_injector
from kernel.kill_switch import kill_switch
from perception.alpaca_client import AlpacaClient

def test_reconciliation_match_and_fault_injection_halt():
    kill_switch.reset()
    fault_injector.clear()
    
    client = AlpacaClient()
    engine = ReconciliationEngine(client=client)
    
    # 1. Normal state -> Match is True, Kill switch is NOT engaged
    event = engine.run_check()
    assert event.match is True
    assert event.action_taken == "CONTINUE_TRADING"
    assert kill_switch.get_status()["is_halted"] is False
    
    # 2. Inject Fault (TradeTrap corruption) -> Match is False, Auto-Halt triggered
    fault_injector.inject_phantom_position("AAPL", 9)
    corrupted_event = engine.run_check()
    
    assert corrupted_event.match is False
    assert corrupted_event.action_taken == "HALT_NEW_ORDERS"
    assert corrupted_event.fault_injected is True
    assert kill_switch.get_status()["is_halted"] is True
    assert "Reconciliation Divergence" in kill_switch.get_status()["halt_reason"] or "Position mismatch" in kill_switch.get_status()["halt_reason"]
    
    # Clean up
    fault_injector.clear()
    kill_switch.reset()
