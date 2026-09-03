"""
tests/test_self_improvement.py - Unit tests for Meta-Cognitive Self-Improving Memory Engine.
"""

import pytest
import os
import tempfile
from reasoning.self_improvement import SelfImprovingMemory, TradeRecord, AdaptiveStrategyParameters

@pytest.fixture
def temp_memory():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        temp_path = tmp.name
    
    # Remove file so constructor initializes cleanly
    if os.path.exists(temp_path):
        os.remove(temp_path)
        
    memory = SelfImprovingMemory(memory_path=temp_path)
    yield memory
    
    if os.path.exists(temp_path):
        os.remove(temp_path)

def test_initial_state_is_honestly_empty(temp_memory):
    """A fresh memory store must start with zero trades - no fabricated seed track record."""
    metrics = temp_memory.get_metrics_summary()
    assert metrics["total_trades"] == 0
    assert metrics["closed_trades"] == 0
    assert metrics["win_count"] == 0
    assert metrics["loss_count"] == 0
    assert metrics["win_rate_pct"] == 0.0
    assert metrics["total_realized_pnl"] == 0.0
    assert "parameters" in metrics
    assert metrics["parameters"]["wing_buffer_multiplier"] == 1.0

def test_record_entry_and_close_win(temp_memory):
    initial_cycles = temp_memory.learning_iterations
    initial_pnl = temp_memory.total_realized_pnl
    
    # 1. Record Entry
    intent = {
        "underlying": "SPY",
        "structure": "iron_condor",
        "delta": -10.0,
        "vega": -160.0,
        "max_profit": 240.0,
        "max_loss": 760.0,
        "rationale": "High VRP entry."
    }
    mkt = {"underlying_price": 651.0, "vrp": 5.4, "iv_rank": 74.0}
    trade = temp_memory.record_entry(intent, mkt, order_id="test-ord-12345")
    
    assert trade.status == "OPEN"
    assert trade.underlying == "SPY"
    
    # 2. Close Trade with Win (+$120.0)
    closed = temp_memory.close_and_reflect(trade.trade_id, exit_price=651.50, realized_pnl=120.0)
    
    assert closed.status == "WIN"
    assert closed.realized_pnl == 120.0
    assert temp_memory.total_realized_pnl == initial_pnl + 120.0
    assert temp_memory.learning_iterations == initial_cycles + 1
    assert closed.reflection != ""
    assert len(closed.lessons_learned) > 0

def test_adaptation_on_loss(temp_memory):
    initial_wing_mult = temp_memory.params.wing_buffer_multiplier
    initial_vrp_thresh = temp_memory.params.vrp_entry_threshold
    
    # Simulate a loss
    closed = temp_memory.close_and_reflect(
        trade_id="TR-SIM-LOSS",
        exit_price=130.0,
        realized_pnl=-100.0
    )
    
    assert closed.status == "LOSS"
    # Wing multiplier should widen and VRP threshold should raise for safety
    assert temp_memory.params.wing_buffer_multiplier >= initial_wing_mult
    assert temp_memory.params.vrp_entry_threshold >= initial_vrp_thresh

def test_prompt_context_injection(temp_memory):
    context = temp_memory.get_learned_prompt_context()
    assert "[SELF-IMPROVING MEMORY CONTEXT]" in context
    assert "Historical Win Rate:" in context
    assert "Learned Wing Buffer Multiplier:" in context
    assert "Recent Meta-Cognitive Lessons Learned:" in context
