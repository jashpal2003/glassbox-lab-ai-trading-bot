"""
web/app.py - FastAPI Backend Service for GlassBox Options.
Exposes APIs for the 3 Demo Acts, Perception, Reasoning, Risk Kernel, Audit Replay, and Dashboard.
"""

import os
import numpy as np
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared.schemas import (
    Intent, OptionLeg, MarketContext, AccountState, KernelDecision,
    AuditSnapshot, ReconciliationEvent, ReplayVerificationResult
)
from perception.alpaca_client import AlpacaClient
from reasoning.agent import LLMReasoningAgent
from reasoning.blindfold import BlindfoldExperiment
from reasoning.regime_engine import classify_regime_detailed
from reasoning.strategy_arena import strategy_arena
from kernel.risk_kernel import validate, load_risk_config
from kernel.kill_switch import kill_switch
from kernel.executor import AlpacaExecutor
from verification.snapshot import create_audit_snapshot
from verification.audit_log import audit_store
from verification.replay_verifier import replay_verifier
from verification.reconciliation import reconciliation_engine
from verification.fault_injector import fault_injector

# Initialize singletons
alpaca_client = AlpacaClient()
reasoning_agent = LLMReasoningAgent()
blindfold_experiment = BlindfoldExperiment(agent=reasoning_agent)
executor = AlpacaExecutor(client=alpaca_client)

import threading
import time

from reasoning.self_improvement import self_improving_memory, TradeRecord

class AutonomousTrader:
    def __init__(self):
        self.is_running = False
        self.interval_seconds = 45
        self.tickers = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT", "META", "AMZN"]
        self._thread = None
        self.last_run_time = None
        self.trades_executed = 0

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        
        def _loop():
            print("[AUTONOMOUS TRADER] Continuous loop started. Trading & Self-Improving unattended.")
            while self.is_running:
                try:
                    clock = alpaca_client.get_market_clock()
                    if not clock.get("is_open", True):
                        print(f"[AUTONOMOUS TRADER] Market closed (next open: {clock.get('next_open')}). Skipping this cycle.")
                        time.sleep(max(30, self.interval_seconds))
                        continue
                except Exception as e:
                    print(f"[AUTONOMOUS TRADER] Market clock check failed ({e}); proceeding cautiously.")

                # 1. Manage existing open positions (Profit-Taking & Stop-Loss)
                try:
                    acct = alpaca_client.get_account_state()
                    for p in acct.positions:
                        # Auto Take-Profit: close once the position hits the learned profit target
                        if hit_profit_target(p):
                            print(f"[AUTO-HARVEST] Position {p.symbol} achieved profit target (+${p.unrealized_pl:.2f}). Closing & reflecting...")
                            alpaca_client.close_position(p.symbol)
                            self_improving_memory.close_and_reflect(
                                trade_id=p.symbol,
                                exit_price=p.current_price,
                                realized_pnl=p.unrealized_pl
                            )
                except Exception as e:
                    print(f"[POSITION MANAGER NOTE] {e}")

                # 2. Scan universe for high-VRP defined-risk setups
                for sym in self.tickers:
                    if not self.is_running:
                        break
                    try:
                        ctx = alpaca_client.get_market_context(sym)
                        
                        # Only enter if VRP satisfies self-improved threshold
                        if ctx.vrp >= self_improving_memory.params.vrp_entry_threshold:
                            intent = reasoning_agent.propose_intent(ctx)
                            decision = validate(intent, ctx.account_state, market_context=ctx)
                            
                            order_id = None
                            if decision.approved and intent.size > 0:
                                order_id = executor.execute_intent(intent, decision)
                                if order_id:
                                    self.trades_executed += 1
                                    # Record in self-improving memory
                                    self_improving_memory.record_entry(
                                        intent_dict=intent.model_dump(),
                                        market_ctx=ctx.model_dump(),
                                        order_id=order_id,
                                        decision_dict=decision.model_dump()
                                    )
                                    
                            snapshot = create_audit_snapshot(
                                ctx=ctx,
                                account=ctx.account_state,
                                intent=intent,
                                decision=decision,
                                order_id=order_id
                            )
                            audit_store.append(snapshot)
                            print(f"[AUTONOMOUS LOOP] {sym}: {decision.decision} (Order: {order_id})")
                    except Exception as e:
                        print(f"[AUTONOMOUS LOOP ERROR] {sym}: {e}")
                    time.sleep(3)
                self.last_run_time = datetime.now(timezone.utc).isoformat()
                time.sleep(max(10, self.interval_seconds - len(self.tickers) * 3))
                
        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.is_running = False
        print("[AUTONOMOUS TRADER] Continuous loop stopped.")

    def get_status(self):
        return {
            "is_running": self.is_running,
            "interval_seconds": self.interval_seconds,
            "tickers": self.tickers,
            "last_run_time": self.last_run_time,
            "trades_executed": self.trades_executed
        }

def hit_profit_target(position) -> bool:
    """
    True once a position has captured >= self_improving_memory.params.profit_target_pct of its
    real cost basis in unrealized profit. Falls back to a flat $50 floor when cost basis is
    ~0 (e.g. a position opened for near-zero net premium), where a percentage is undefined.
    Shared by the autonomous loop and the manual harvest endpoint so both mean the same thing
    by "profit target" instead of one checking a flat $100 and the other any profit at all.
    """
    target_pct = self_improving_memory.params.profit_target_pct
    basis = abs(position.cost_basis)
    if basis < 1.0:
        return position.unrealized_pl >= 50.0
    return (position.unrealized_pl / basis * 100.0) >= target_pct

autonomous_trader = AutonomousTrader()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start background automated reconciliation loop
    reconciliation_engine.start_background_loop()
    yield
    # Shutdown
    reconciliation_engine.stop()
    autonomous_trader.stop()

app = FastAPI(
    title="GlassBox Options - Verifiable AI Trading Agent",
    description="The Safety Layer that makes Autonomous Options Trading Auditable, Attack-Resistant, and Strategy-Honest",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # "*" + credentials is invalid per the CORS spec; this API uses no cookies/auth headers
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SYSTEM & STATUS ENDPOINTS ---

@app.get("/api/status")
def get_system_status():
    account = alpaca_client.get_account_state()
    ks_status = kill_switch.get_status()
    fault_status = fault_injector.get_status()
    risk_limits = load_risk_config()
    auto_status = autonomous_trader.get_status()
    return {
        "status": "HALTED" if ks_status["is_halted"] else "HEALTHY",
        "account": account.model_dump(),
        "kill_switch": ks_status,
        "fault_injector": fault_status,
        "risk_limits": risk_limits,
        "autonomous_trader": auto_status,
        "is_paper_trading": True
    }

@app.post("/api/autonomous/toggle")
def toggle_autonomous_trading():
    if autonomous_trader.is_running:
        autonomous_trader.stop()
    else:
        autonomous_trader.start()
    return {"status": "SUCCESS", "autonomous_trader": autonomous_trader.get_status()}

@app.get("/api/market/{symbol}")
def get_market_context(symbol: str = "SPY"):
    ctx = alpaca_client.get_market_context(symbol.upper())
    return ctx.model_dump()

@app.get("/api/ticker-tape")
def get_ticker_tape():
    """Real last price + day-over-day % change for the scrolling header tape, plus real VIX."""
    symbols = ["SPY", "QQQ", "NVDA", "AAPL", "MSFT", "META", "TSLA", "AMZN", "COIN", "PLTR"]
    quotes = alpaca_client.get_ticker_tape_quotes(symbols)
    vix = alpaca_client._get_real_vix() if alpaca_client.has_real_client else None
    return {"quotes": quotes, "vix": vix}

@app.get("/api/chart/{symbol}")
def get_chart_data(symbol: str = "SPY", timeframe: str = "1M"):
    bars = alpaca_client.get_ohlcv_bars(symbol.upper(), timeframe)
    return {"symbol": symbol.upper(), "timeframe": timeframe, "bars": bars}

@app.get("/api/positions")
def get_live_positions():
    acct = alpaca_client.get_account_state()
    return {"positions": [p.model_dump() for p in acct.positions]}

@app.delete("/api/positions/{symbol}")
def close_live_position(symbol: str):
    success = alpaca_client.close_position(symbol)
    return {"status": "SUCCESS" if success else "FAILED", "symbol": symbol}

@app.get("/api/orders")
def get_live_orders():
    orders = alpaca_client.get_open_orders()
    return {"orders": orders}

class ChatRequest(BaseModel):
    message: str
    symbol: Optional[str] = "SPY"

@app.post("/api/agent/chat")
def chat_with_agent(req: ChatRequest):
    """
    Live AI Copilot chat terminal powered by Gemini.
    Provides instant numeric volatility analysis and trade structuring recommendations.
    """
    ctx = alpaca_client.get_market_context(req.symbol or "SPY")
    
    prompt = f"""You are GlassBox Options Copilot, an elite quantitative options analyst.
Current Market Context for {ctx.underlying}:
- Spot Price: ${ctx.underlying_price}
- IV Rank: {ctx.iv_rank}%
- Realized Vol (30d): {ctx.realized_vol}%
- Volatility Risk Premium (VRP): {ctx.vrp} pts
- VIX Index: {ctx.vix}
- Earnings Blackout (<2d): {ctx.is_earnings_blackout}

User Question/Request: "{req.message}"

Provide a concise, highly quantitative response citing the exact numbers above. Recommend defined-risk structures (e.g. Iron Condor, Put Credit Spread) when IV Rank > 50% and VRP > 0, or explain risk boundaries if high volatility/earnings blackout."""

    reply_source = "gemini"
    try:
        if reasoning_agent.gemini_client:
            res = reasoning_agent.gemini_client.generate_content(prompt)
            answer = res.text.strip()
        else:
            reply_source = "template_fallback"
            answer = f"[Template - Gemini unavailable] IV Rank is {ctx.iv_rank}%, VRP is +{ctx.vrp} pts for {ctx.underlying}. Statistical edge favors selling defined-risk premium via Iron Condor or Credit Spread."
    except Exception as e:
        reply_source = "template_fallback"
        answer = f"[Template - Gemini call failed: {e}] IV Rank {ctx.iv_rank}%, VRP +{ctx.vrp} pts for {ctx.underlying}. Recommended structure: Defined-risk Iron Condor harvesting elevated theta."

    return {
        "reply": answer,
        "reply_source": reply_source,
        "market_context": ctx.model_dump()
    }

# --- SELF-IMPROVING META-COGNITIVE ENGINE ENDPOINTS ---

@app.get("/api/memory")
def get_self_improving_memory_state():
    """Return learning iterations, win rate, PnL, adaptive parameters, and reflection history."""
    return self_improving_memory.get_metrics_summary()

class ReflectRequest(BaseModel):
    trade_id: str
    exit_price: float
    realized_pnl: float

@app.post("/api/memory/reflect")
def trigger_trade_reflection(req: ReflectRequest):
    """
    Manually close a trade and trigger the Gemini Meta-Cognitive Reflection loop.
    Adapts strategy hyperparameters (wing buffer, regime weights).
    """
    trade = self_improving_memory.close_and_reflect(
        trade_id=req.trade_id,
        exit_price=req.exit_price,
        realized_pnl=req.realized_pnl
    )
    return {
        "status": "SUCCESS",
        "trade": trade.model_dump() if trade else None,
        "memory_summary": self_improving_memory.get_metrics_summary()
    }

@app.post("/api/positions/harvest")
def harvest_profitable_positions():
    """
    Close all open positions on Alpaca that have achieved >= 50% max profit target,
    then automatically feed results to the self-improving reflection engine.
    """
    harvested = []
    acct = alpaca_client.get_account_state()
    for p in acct.positions:
        if hit_profit_target(p):
            alpaca_client.close_position(p.symbol)
            t = self_improving_memory.close_and_reflect(
                trade_id=p.symbol,
                exit_price=p.current_price,
                realized_pnl=p.unrealized_pl
            )
            harvested.append({
                "symbol": p.symbol,
                "realized_pnl": p.unrealized_pl,
                "reflection": t.reflection if t else ""
            })
    return {
        "status": "SUCCESS",
        "harvested_count": len(harvested),
        "harvested": harvested,
        "memory_summary": self_improving_memory.get_metrics_summary()
    }

# --- ACT 1: GLASSBOX PIPELINE ENDPOINTS ---

class ProposeRequest(BaseModel):
    symbol: str = "SPY"

@app.post("/api/pipeline/run")
def run_full_glassbox_pipeline(req: ProposeRequest):
    """
    Act 1 Golden Demo Path:
    Market Context -> LLM Intent -> Deterministic Risk Kernel -> Alpaca Execution -> SHA-256 Snapshot Hasher
    """
    ctx = alpaca_client.get_market_context(req.symbol)
    intent = reasoning_agent.propose_intent(ctx)
    decision = validate(intent, ctx.account_state, market_context=ctx)
    
    order_id = None
    if decision.approved:
        order_id = executor.execute_intent(intent, decision)
        
    snapshot = create_audit_snapshot(
        ctx=ctx,
        account=ctx.account_state,
        intent=intent,
        decision=decision,
        order_id=order_id
    )
    audit_store.append(snapshot)
    
    return {
        "market_context": ctx.model_dump(),
        "intent": intent.model_dump(),
        "decision": decision.model_dump(by_alias=True),
        "order_id": order_id,
        "snapshot": snapshot.model_dump()
    }

@app.post("/api/pipeline/guaranteed_rejection")
def run_guaranteed_rejection_scenario():
    """
    Act 1 (15s): Scripted guaranteed limit breach demo.
    Generates an oversized short-vol intent that pushes vega beyond the -250 limit.
    """
    ctx = alpaca_client.get_market_context("SPY")
    spot = ctx.underlying_price
    strike_step = 5.0 if spot > 300 else (2.5 if spot > 80 else 1.0)
    atm = round(spot / strike_step) * strike_step
    demo_expiry = ctx.contracts[0].expiry if ctx.contracts else (datetime.now(timezone.utc) + timedelta(days=19)).strftime("%Y-%m-%d")

    # Size dynamically against the account's REAL current portfolio vega so this "guaranteed"
    # breach actually is one. The account carries real pre-existing positions whose vega varies
    # run to run - a fixed size (e.g. 20) can land well inside the limit on an account that's
    # already net long vega, silently turning the "guaranteed rejection" demo into a real
    # approved-and-filled order. verify_defined_risk_and_max_loss() computes vega impact as
    # -15.0 * size for a short-vol structure like this one; solve for the smallest size that
    # is certain to push projected vega past the limit, plus a safety margin.
    vega_limit = load_risk_config().get("max_portfolio_vega", -250.0)
    current_vega = ctx.account_state.portfolio_vega
    required_size = max(20, int((current_vega - vega_limit) / 15.0) + 10)

    # Intentionally craft a high-size structure that violates vega & max buying power
    bad_intent = Intent(
        underlying="SPY",
        structure="iron_condor",
        legs=[
            OptionLeg(action="sell", type="put", strike=atm - 2 * strike_step, expiry=demo_expiry),
            OptionLeg(action="buy", type="put", strike=atm - 3 * strike_step, expiry=demo_expiry),
            OptionLeg(action="sell", type="call", strike=atm + 2 * strike_step, expiry=demo_expiry),
            OptionLeg(action="buy", type="call", strike=atm + 3 * strike_step, expiry=demo_expiry),
        ],
        size=required_size,  # Oversized enough to breach the vega limit given the account's actual current vega
        rationale="IV rank 88, attempting maximum leverage short-volatility harvest.",
        conviction=0.92,
        regime_tags=["sell_premium", "extreme_leverage"]
    )
    
    decision = validate(bad_intent, ctx.account_state, market_context=ctx)
    snapshot = create_audit_snapshot(ctx, ctx.account_state, bad_intent, decision, order_id=None)
    audit_store.append(snapshot)
    
    return {
        "market_context": ctx.model_dump(),
        "intent": bad_intent.model_dump(),
        "decision": decision.model_dump(by_alias=True),
        "snapshot": snapshot.model_dump()
    }

# --- MARKET REGIME ENGINE & STRATEGY ARENA ---

@app.get("/api/regime/{symbol}")
def get_market_regime(symbol: str = "SPY"):
    """Deterministic regime classification with the real signals that produced it."""
    ctx = alpaca_client.get_market_context(symbol.upper())
    return classify_regime_detailed(ctx).model_dump()

class ArenaRequest(BaseModel):
    symbol: str = "SPY"
    lookback_days: int = 365
    refresh: bool = False

@app.post("/api/arena/score")
def score_strategy_arena(req: ArenaRequest):
    """
    Runs the Strategy Arena tournament: every variant replayed over the same real historical
    closes, ranked by composite score, champion selected from those eligible in the current regime.
    """
    ctx = alpaca_client.get_market_context(req.symbol.upper())
    arena = strategy_arena.run_tournament(
        ctx, lookback_days=req.lookback_days, use_cache=not req.refresh
    )
    return arena.model_dump()

@app.get("/api/arena/registry")
def get_strategy_registry():
    """The strategy population, including each variant's parameters and regime eligibility."""
    return {"variants": [v.model_dump() for v in strategy_arena.get_registry()]}

@app.post("/api/pipeline/arena_run")
def run_arena_driven_pipeline(req: ProposeRequest):
    """
    The full evidence-gated path:
    Regime -> Strategy Arena tournament -> champion as evidence -> LLM/deterministic Intent
    -> Risk Kernel -> execution -> SHA-256 snapshot.

    The arena decides which structures have *earned the right to be considered*; the Risk Kernel
    still independently decides whether the resulting order may actually be placed.
    """
    ctx = alpaca_client.get_market_context(req.symbol.upper())
    arena = strategy_arena.run_tournament(ctx)
    intent = reasoning_agent.propose_intent(ctx, arena=arena)
    decision = validate(intent, ctx.account_state, market_context=ctx)

    order_id = None
    if decision.approved and intent.size > 0:
        order_id = executor.execute_intent(intent, decision)
        if order_id:
            self_improving_memory.record_entry(
                intent_dict=intent.model_dump(),
                market_ctx=ctx.model_dump(),
                order_id=order_id,
                decision_dict=decision.model_dump(),
            )

    snapshot = create_audit_snapshot(
        ctx=ctx, account=ctx.account_state, intent=intent, decision=decision, order_id=order_id
    )
    audit_store.append(snapshot)

    return {
        "market_context": ctx.model_dump(),
        "regime": arena.regime.model_dump(),
        "arena": arena.model_dump(),
        "intent": intent.model_dump(),
        "decision": decision.model_dump(by_alias=True),
        "order_id": order_id,
        "snapshot": snapshot.model_dump(),
    }

# --- ACT 2: TRADETRAP DEFENSE & RECONCILIATION ---

class FaultRequest(BaseModel):
    symbol: str = "AAPL"
    qty: int = 9

@app.post("/api/act2/inject_fault")
def inject_ledger_fault(req: FaultRequest):
    fault_injector.inject_phantom_position(req.symbol, req.qty)
    return {"status": "FAULT_INJECTED", "details": fault_injector.get_status()}

@app.post("/api/act2/clear_fault")
def clear_ledger_fault():
    fault_injector.clear()
    return {"status": "FAULT_CLEARED", "details": fault_injector.get_status()}

@app.post("/api/act2/reconcile_now")
def trigger_immediate_reconciliation():
    event = reconciliation_engine.run_check()
    return {
        "event": event.model_dump(),
        "kill_switch": kill_switch.get_status()
    }

@app.get("/api/act2/reconciliation_log")
def get_reconciliation_log():
    events = reconciliation_engine.get_latest_events(15)
    return [e.model_dump() for e in events]

@app.post("/api/act2/reset_killswitch")
def reset_kill_switch():
    kill_switch.reset()
    return {"status": "RESET", "kill_switch": kill_switch.get_status()}

# --- ACT 3: BLINDFOLD EXPERIMENT ---

@app.post("/api/act3/blindfold")
def run_blindfold_test(req: ProposeRequest):
    ctx = alpaca_client.get_market_context(req.symbol)
    result = blindfold_experiment.run_single_comparison(ctx, asset_alias="ASSET_04")
    return result

@app.post("/api/act3/blindfold_batch")
def run_blindfold_batch():
    """Runs across multiple decision points to compute empirical agreement rate."""
    symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]
    contexts = [alpaca_client.get_market_context(s) for s in symbols]
    batch_res = blindfold_experiment.run_multi_point_experiment(contexts)
    return batch_res

# --- HISTORICAL BACKTEST & VOLATILITY CURVES ---
from perception.backtest_engine import replay_engine

class BacktestRequest(BaseModel):
    symbol: str = "SPY"
    days: int = 180
    strategy: str = "iron_condor"
    trials: int = 12

@app.post("/api/backtest/run")
def run_historical_backtest(req: BacktestRequest):
    res = replay_engine.run_backtest(
        symbol=req.symbol,
        days=req.days,
        strategy_type=req.strategy,
        trials_tested=req.trials
    )
    return res

@app.get("/api/market/payoff/{symbol}")
def get_options_payoff_and_smile(symbol: str = "SPY"):
    """
    Computes real-time PnL Payoff curve and Implied Volatility Smile curve for the asset.
    """
    ctx = alpaca_client.get_market_context(symbol.upper())
    price = ctx.underlying_price
    
    # 1. Smile curve from contracts
    smile_points = []
    seen_strikes = set()
    for c in ctx.contracts:
        if c.strike not in seen_strikes:
            seen_strikes.add(c.strike)
            smile_points.append({"strike": c.strike, "iv": c.implied_volatility})
    smile_points.sort(key=lambda x: x["strike"])

    # 2. Payoff simulation for standard iron condor around ATM
    strike_step = 5.0 if price > 300 else (2.5 if price > 80 else 1.0)
    atm = round(price / strike_step) * strike_step
    
    p_long = atm - 2 * strike_step
    p_short = atm - 1 * strike_step
    c_short = atm + 1 * strike_step
    c_long = atm + 2 * strike_step
    
    credit = round(strike_step * 0.35 * 100, 2)
    max_loss = round((strike_step * 100) - credit, 2)
    
    payoff_curve = []
    prices_range = np.linspace(price * 0.88, price * 1.12, 25)
    
    for p in prices_range:
        p_val = round(float(p), 2)
        if p_val <= p_long:
            pnl = -max_loss
        elif p_val < p_short:
            ratio = (p_val - p_long) / (p_short - p_long)
            pnl = -max_loss + ratio * (credit + max_loss)
        elif p_val <= c_short:
            pnl = credit
        elif p_val < c_long:
            ratio = (c_long - p_val) / (c_long - c_short)
            pnl = -max_loss + ratio * (credit + max_loss)
        else:
            pnl = -max_loss
            
        payoff_curve.append({
            "price": p_val,
            "pnl": round(pnl, 2)
        })

    return {
        "underlying": symbol.upper(),
        "spot_price": price,
        "max_profit": credit,
        "max_loss": max_loss,
        "breakeven_lower": round(p_short - (credit / 100.0), 2),
        "breakeven_upper": round(c_short + (credit / 100.0), 2),
        "payoff_curve": payoff_curve,
        "volatility_smile": smile_points
    }

# --- JUDGE DEMO: THE WHOLE STORY IN ONE CALL ---

class DemoRequest(BaseModel):
    symbol: str = "SPY"
    execute: bool = False   # when False the approved trade is NOT submitted to the broker

@app.post("/api/demo/story")
def run_judge_demo_story(req: DemoRequest):
    """
    Plays the complete GlassBox narrative in one request and returns it as an ordered timeline
    of real steps, so the demo doesn't depend on clicking through eight tabs in the right order.

    Every step below runs the real production code path against real data - nothing here is a
    scripted animation. `execute=false` (the default) runs the full decision path but does not
    submit the approved order, so the story can be replayed safely without placing a new order
    on each run.
    """
    steps: List[Dict[str, Any]] = []

    def step(title: str, verdict: str, detail: str, data: Optional[Dict[str, Any]] = None):
        steps.append({
            "step": len(steps) + 1,
            "title": title,
            "verdict": verdict,          # INFO | PASS | REJECTED | HALTED | LEARNED
            "detail": detail,
            "data": data or {},
            "at": datetime.now(timezone.utc).isoformat(),
        })

    symbol = req.symbol.upper()

    # 1. Perception on real market data.
    ctx = alpaca_client.get_market_context(symbol)
    step("Perception: real market data", "INFO",
         f"{symbol} at ${ctx.underlying_price}. IV rank {ctx.iv_rank}, realized vol "
         f"{ctx.realized_vol}%, VRP {ctx.vrp:+.2f} pts, VIX "
         f"{ctx.vix if ctx.vix is not None else 'UNAVAILABLE'}.",
         {"data_source": ctx.data_source, "underlying_price": ctx.underlying_price,
          "iv_rank": ctx.iv_rank, "vrp": ctx.vrp, "vix": ctx.vix,
          "trend_20d_pct": ctx.trend_20d_pct, "rv_expansion_ratio": ctx.rv_expansion_ratio})

    # 2. Deterministic regime classification.
    regime = classify_regime_detailed(ctx)
    step("Regime Engine: classify the environment", "INFO",
         f"{regime.label} ({regime.regime}), confidence {regime.confidence_pct}%. "
         f"{regime.description}",
         {"regime": regime.regime, "label": regime.label,
          "confidence_pct": regime.confidence_pct, "signals": regime.signals,
          "data_complete": regime.data_complete})

    # 3. Strategy Arena tournament on real history.
    arena = strategy_arena.run_tournament(ctx)
    leaderboard = [
        {"name": s.name, "structure": s.structure, "eligible": s.eligible, "trades": s.trades,
         "win_rate_pct": s.win_rate_pct, "expectancy_pct": s.expectancy_pct, "score": s.score}
        for s in arena.scores
    ]
    step("Strategy Arena: strategies compete for the right to trade", "INFO",
         arena.champion_rationale,
         {"leaderboard": leaderboard, "champion_id": arena.champion_id,
          "champion_name": arena.champion_name, "known_bias": arena.known_bias})

    # 4. Reasoning constrained by that evidence.
    intent = reasoning_agent.propose_intent(ctx, arena=arena)
    step("Reasoning: propose a structured Intent", "INFO",
         f"{intent.structure} x{intent.size}, conviction {intent.conviction}. {intent.rationale}",
         {"intent": intent.model_dump()})

    # 5. The deterministic Risk Kernel gate.
    decision = validate(intent, ctx.account_state, market_context=ctx)
    step("Risk Kernel: independent deterministic gate", "PASS" if decision.approved else "REJECTED",
         (f"APPROVED - all {len(decision.kernel_checks)} checks passed. Max loss "
          f"${decision.max_loss:,.2f}.") if decision.approved else
         f"REJECTED - {'; '.join(decision.reasons)}",
         {"decision": decision.model_dump(by_alias=True)})

    order_id = None
    if decision.approved and intent.size > 0 and req.execute:
        order_id = executor.execute_intent(intent, decision)
        if order_id:
            self_improving_memory.record_entry(
                intent_dict=intent.model_dump(), market_ctx=ctx.model_dump(),
                order_id=order_id, decision_dict=decision.model_dump(),
            )
        step("Execution: real multi-leg order on Alpaca paper", "PASS" if order_id else "REJECTED",
             f"Broker order {order_id}" if order_id else
             "Broker rejected or no real reference price available - failed closed, no order placed.",
             {"order_id": order_id})
    elif decision.approved and intent.size > 0:
        step("Execution: skipped (demo replay mode)", "INFO",
             "The trade was approved and would have been submitted. Execution is suppressed so "
             "this story can be replayed without placing a new order every run.", {})

    snapshot = create_audit_snapshot(ctx, ctx.account_state, intent, decision, order_id=order_id)
    audit_store.append(snapshot)
    step("Audit: tamper-evident SHA-256 snapshot", "PASS",
         f"Snapshot {snapshot.snapshot_id} hashed as {snapshot.hash}.",
         {"snapshot_id": snapshot.snapshot_id, "hash": snapshot.hash})

    # 6. Independent replay verification of the receipt we just wrote.
    verification = replay_verifier.verify_decision(snapshot)
    step("Replay Verifier: recompute the hash independently", "PASS" if verification.integrity else "REJECTED",
         f"Hash integrity {'VERIFIED' if verification.integrity else 'FAILED'}; "
         f"rationale claims {'supported' if verification.condition_supported else 'showed a discrepancy'}.",
         {"verification": verification.model_dump()})

    # 7. Prove the kernel says no: the same account, an intentionally oversized structure.
    rejection = run_guaranteed_rejection_scenario()
    rej_decision = rejection["decision"]
    step("Guaranteed rejection: prove the gate actually bites", "REJECTED",
         f"An intentionally oversized short-vol structure (size {rejection['intent']['size']}) was "
         f"REJECTED: {'; '.join(rej_decision.get('reasons', []))}",
         {"intent": rejection["intent"], "decision": rej_decision})

    # 8. TradeTrap: corrupt the local ledger and let the watchdog catch it.
    fault_injector.inject_phantom_position("AAPL", 9)
    recon = reconciliation_engine.run_check()
    ks = kill_switch.get_status()
    step("TradeTrap: ledger corruption detected, kill switch engaged",
         "HALTED" if ks["is_halted"] else "PASS",
         f"Injected a phantom AAPL position that exists only in local memory. Reconciliation "
         f"compared believed vs. real broker state and took action: {recon.action_taken}. "
         f"Kill switch halted: {ks['is_halted']}.",
         {"reconciliation": recon.model_dump(), "kill_switch": ks})

    # Restore a clean state so the demo is idempotent and the system is left tradable.
    fault_injector.clear()
    kill_switch.reset()
    step("Recovery: fault cleared, kill switch reset", "PASS",
         "Local ledger corruption cleared and the halt lifted - the system is left in a clean, "
         "tradable state so this story can be replayed.",
         {"kill_switch": kill_switch.get_status()})

    # 9. Honest learning state - whatever it actually is.
    memory = self_improving_memory.get_metrics_summary()
    step("Learning: measured track record", "LEARNED",
         f"{memory['closed_trades']} closed trades recorded, win rate {memory['win_rate_pct']}%, "
         f"net realized P&L ${memory['total_realized_pnl']:,.2f}, "
         f"{memory['learning_iterations']} learning iterations. Adaptive parameters: "
         f"VRP entry threshold {memory['parameters']['vrp_entry_threshold']} pts, wing buffer "
         f"{memory['parameters']['wing_buffer_multiplier']}x.",
         {"memory": memory})

    return {
        "symbol": symbol,
        "executed": bool(order_id),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "summary": {
            "regime": regime.label,
            "champion": arena.champion_name,
            "intent_structure": intent.structure,
            "kernel_decision": decision.decision,
            "order_id": order_id,
            "audit_hash": snapshot.hash,
            "hash_verified": verification.integrity,
        },
    }

# --- AUDIT TRAIL & REPLAY VERIFIER ---

@app.get("/api/audit/snapshots")
def get_all_audit_snapshots():
    snaps = audit_store.get_all()
    return [s.model_dump() for s in reversed(snaps)]

@app.post("/api/audit/verify/{id_val}")
def verify_audit_record(id_val: str):
    """
    Replay verifier contract:
    Recomputes SHA-256 hash from raw inputs and validates numeric rationale assertions.
    """
    snap = audit_store.get_by_order_id(id_val) or audit_store.get_by_intent_id(id_val)
    if not snap:
        # Check by snapshot_id
        for s in audit_store.get_all():
            if s.snapshot_id == id_val:
                snap = s
                break
                
    if not snap:
        raise HTTPException(status_code=404, detail="Audit snapshot not found")
        
    result = replay_verifier.verify_decision(snap)
    return result.model_dump()

# --- STATIC FILES (DASHBOARD UI) ---
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="root_static")
