"""
tests/test_api.py - Integration tests against the live FastAPI app.

NOTE: with real Alpaca credentials configured in .env, these tests exercise the REAL paper
account - test_act1_pipeline_run_and_replay_verification places a real multi-leg options order,
and test_api_harvest closes any real open position with unrealized profit (paper money, no real
capital, but real side effects on the account's open positions/orders each run).
"""

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from fastapi.testclient import TestClient
from web.app import app
from kernel.kill_switch import kill_switch
from verification.fault_injector import fault_injector

client = TestClient(app)

def test_api_status():
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "account" in data
    assert "kill_switch" in data
    assert "risk_limits" in data


def test_api_market():
    resp = client.get("/api/market/SPY")
    assert resp.status_code == 200
    data = resp.json()
    assert data["underlying"] == "SPY"
    assert data["iv_rank"] > 0
    assert len(data["contracts"]) > 0


def test_act1_pipeline_run_and_replay_verification():
    kill_switch.reset()
    fault_injector.clear()
    
    # 1. Run Act 1 pipeline
    resp = client.post("/api/pipeline/run", json={"symbol": "SPY"})
    assert resp.status_code == 200
    data = resp.json()

    # Do NOT assert APPROVED unconditionally. The verdict legitimately depends on live account
    # exposure and live market conditions - if the account already sits near its delta/vega
    # limits, REJECTED is the CORRECT answer and a test demanding APPROVED would just be
    # asserting that the safety layer stays out of the way. Assert the real invariant instead:
    # the decision is coherent, and execution follows the decision rather than diverging from it.
    decision = data["decision"]["decision"]
    assert decision in ("APPROVED", "REJECTED")
    if decision == "APPROVED":
        assert data["intent"]["size"] == 0 or data["order_id"] is not None, \
            "an approved non-zero-size intent must produce a broker order id"
    else:
        assert data["order_id"] is None, "a rejected intent must never reach the broker"
        assert len(data["decision"]["reasons"]) > 0, "a rejection must state numeric reasons"
    assert data["snapshot"]["hash"].startswith("sha256:")

    snapshot_id = data["snapshot"]["snapshot_id"]
    
    # 2. Replay verify the snapshot
    verify_resp = client.post(f"/api/audit/verify/{snapshot_id}")
    assert verify_resp.status_code == 200
    vdata = verify_resp.json()
    assert vdata["integrity"] is True
    assert vdata["condition_supported"] is True
    assert vdata["overall_status"] == "PASS"


def test_act1_guaranteed_rejection():
    kill_switch.reset()
    resp = client.post("/api/pipeline/guaranteed_rejection")
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"]["decision"] == "REJECTED"
    assert len(data["decision"]["reasons"]) > 0
    assert any("Vega would push portfolio" in r or "Position size" in r for r in data["decision"]["reasons"])


def test_act2_fault_injection_and_reconciliation():
    kill_switch.reset()
    fault_injector.clear()
    
    # Inject fault
    resp = client.post("/api/act2/inject_fault", json={"symbol": "AAPL", "qty": 9})
    assert resp.status_code == 200
    
    # Trigger reconciliation
    recon_resp = client.post("/api/act2/reconcile_now")
    assert recon_resp.status_code == 200
    rdata = recon_resp.json()
    assert rdata["event"]["match"] is False
    assert rdata["event"]["action_taken"] == "HALT_NEW_ORDERS"
    assert rdata["kill_switch"]["is_halted"] is True
    
    # Clean up
    reset_resp = client.post("/api/act2/reset_killswitch")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["kill_switch"]["is_halted"] is False


def test_act3_blindfold_batch():
    """
    Structural check only. With real market data and a real (low-temperature but non-zero) LLM,
    the exact agreement rate is a genuine empirical result that varies run to run - asserting a
    specific hard threshold here would either be flaky or would pressure the implementation to
    fake consistency. The dashboard surfaces the live number for judges to inspect directly.
    """
    resp = client.post("/api/act3/blindfold_batch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_samples"] >= 4
    assert 0.0 <= data["agreement_rate_pct"] <= 100.0
    assert data["matched_samples"] <= data["total_samples"]
    assert len(data["details"]) == data["total_samples"]


def test_api_memory_and_reflection():
    # 1. Get memory state
    resp = client.get("/api/memory")
    assert resp.status_code == 200
    mdata = resp.json()
    assert "win_rate_pct" in mdata
    assert "parameters" in mdata
    
    # 2. Trigger manual reflection
    ref_resp = client.post("/api/memory/reflect", json={
        "trade_id": "TEST-TR-01",
        "exit_price": 652.0,
        "realized_pnl": 115.0
    })
    assert ref_resp.status_code == 200
    rdata = ref_resp.json()
    assert rdata["status"] == "SUCCESS"
    assert rdata["trade"]["status"] == "WIN"
    assert rdata["trade"]["realized_pnl"] == 115.0


def test_api_chart_and_positions():
    chart_resp = client.get("/api/chart/SPY?timeframe=1M")
    assert chart_resp.status_code == 200
    cdata = chart_resp.json()
    assert cdata["symbol"] == "SPY"
    assert len(cdata["bars"]) > 0
    assert "open" in cdata["bars"][0]
    assert "close" in cdata["bars"][0]
    
    pos_resp = client.get("/api/positions")
    assert pos_resp.status_code == 200
    assert "positions" in pos_resp.json()


def test_api_harvest():
    resp = client.post("/api/positions/harvest")
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"

