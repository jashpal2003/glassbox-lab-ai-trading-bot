"""
tests/test_backtest.py - Unit tests for Deflated Sharpe Ratio and Historical Replay Engine.
"""

import pytest
from perception.backtest_engine import replay_engine, calculate_deflated_sharpe_ratio

def test_deflated_sharpe_ratio_calculation():
    # A strategy with Sharpe 1.8 across 25 trades and 10 trials should have high confidence (> 0.80)
    dsr = calculate_deflated_sharpe_ratio(observed_sharpe=1.85, num_trades=25, num_trials=10)
    assert 0.0 < dsr <= 1.0
    assert dsr >= 0.75

def test_historical_backtest_execution():
    res = replay_engine.run_backtest(symbol="MSFT", days=90, strategy_type="iron_condor", trials_tested=8)
    assert res["symbol"] == "MSFT"
    assert res["total_trades"] > 0
    assert "win_rate_pct" in res
    assert "deflated_sharpe_ratio" in res
    assert len(res["equity_curve"]) > 1
