"""
reasoning/self_improvement.py - Meta-Cognitive Reflection & Self-Improving Memory Engine.

Tracks trade lifecycle outcomes (entry Greeks, exit P&L, thesis fidelity),
generates quantitative reflections using Gemini, and adaptively updates strategy
hyperparameters (wing buffers, regime conviction weights, VRP thresholds).
"""

import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

MEMORY_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "perception", "agent_memory.json")

class TradeRecord(BaseModel):
    trade_id: str
    underlying: str
    structure: str
    entry_time: str
    exit_time: Optional[str] = None
    entry_price: float
    exit_price: Optional[float] = None
    entry_vrp: float
    entry_iv_rank: float
    entry_delta: float
    entry_vega: float
    max_profit: float
    max_loss: float
    realized_pnl: float = 0.0
    pnl_pct: float = 0.0
    status: str = "OPEN"  # OPEN, WIN, LOSS, SCRATCH
    thesis: str = ""
    reflection: str = ""
    reflection_source: str = "none"  # "gemini" or "template_fallback" once reflected, else "none"
    lessons_learned: List[str] = Field(default_factory=list)

class AdaptiveStrategyParameters(BaseModel):
    wing_buffer_multiplier: float = 1.0       # Expands/contracts strike distance from ATM
    vrp_entry_threshold: float = 2.0          # Minimum VRP required to sell premium
    iv_rank_threshold: float = 45.0           # Minimum IV Rank required
    profit_target_pct: float = 50.0           # Auto take-profit percentage
    stop_loss_multiplier: float = 1.5         # Auto stop-loss multiple of max credit
    regime_weights: Dict[str, float] = Field(default_factory=lambda: {
        "High IV / Elevated VRP": 1.25,
        "Range-Bound / Normal IV": 1.0,
        "Low IV / Negative VRP": 0.5,
        "Earnings Impending": 0.0
    })

class SelfImprovingMemory:
    def __init__(self, memory_path: str = MEMORY_FILE_PATH, gemini_client: Optional[Any] = None):
        self.memory_path = memory_path
        self.gemini_client = gemini_client
        self.trades: List[TradeRecord] = []
        self.params = AdaptiveStrategyParameters()
        self.total_realized_pnl: float = 0.0
        self.win_count: int = 0
        self.loss_count: int = 0
        self.learning_iterations: int = 0
        self._load()

    def _load(self):
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.trades = [TradeRecord(**t) for t in data.get("trades", [])]
                    self.params = AdaptiveStrategyParameters(**data.get("params", {}))
                    self.total_realized_pnl = float(data.get("total_realized_pnl", 0.0))
                    self.win_count = int(data.get("win_count", 0))
                    self.loss_count = int(data.get("loss_count", 0))
                    self.learning_iterations = int(data.get("learning_iterations", 0))
            except Exception as e:
                print(f"[MEMORY LOAD WARNING] Error loading memory ({e}). Starting from an empty track record.")
                self.trades = []
                self.params = AdaptiveStrategyParameters()
                self.total_realized_pnl = 0.0
                self.win_count = 0
                self.loss_count = 0
                self.learning_iterations = 0
        else:
            # Genuinely empty track record - no trades have happened yet. Do not invent a
            # fabricated "seed" history; the dashboard should honestly show zero trades until
            # the system actually trades.
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)
        data = {
            "trades": [t.model_dump() for t in self.trades],
            "params": self.params.model_dump(),
            "total_realized_pnl": round(self.total_realized_pnl, 2),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "learning_iterations": self.learning_iterations,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        with open(self.memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def record_entry(
        self,
        intent_dict: Dict[str, Any],
        market_ctx: Dict[str, Any],
        order_id: str,
        decision_dict: Optional[Dict[str, Any]] = None,
    ) -> TradeRecord:
        """Log a newly opened trade into memory.

        `decision_dict` (the KernelDecision this Intent was executed under) supplies the real,
        kernel-computed max_loss and delta/vega impact of this specific trade. `Intent` itself has
        no delta/vega/max_profit/max_loss fields, so without this the entry would silently record
        fabricated placeholder numbers (0.0 Greeks, a fixed 750.0 max_loss) regardless of the
        actual structure/size traded.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        decision_dict = decision_dict or {}
        max_loss = float(decision_dict.get("max_loss", 0.0))
        pre_trade_delta = float(market_ctx.get("account_state", {}).get("portfolio_delta", 0.0))
        pre_trade_vega = float(market_ctx.get("account_state", {}).get("portfolio_vega", 0.0))
        entry_delta = float(decision_dict.get("projected_delta", pre_trade_delta)) - pre_trade_delta
        entry_vega = float(decision_dict.get("projected_vega", pre_trade_vega)) - pre_trade_vega
        trade = TradeRecord(
            trade_id=f"TR-{order_id[:12]}",
            underlying=intent_dict.get("underlying", "SPY"),
            structure=intent_dict.get("structure", "iron_condor"),
            entry_time=now_iso,
            entry_price=float(market_ctx.get("underlying_price", 0.0)),
            entry_vrp=float(market_ctx.get("vrp", 0.0)),
            entry_iv_rank=float(market_ctx.get("iv_rank", 0.0)),
            entry_delta=round(entry_delta, 2),
            entry_vega=round(entry_vega, 2),
            max_profit=0.0,  # not computed anywhere yet - honestly unknown rather than a fabricated guess
            max_loss=max_loss,
            status="OPEN",
            thesis=intent_dict.get("rationale", "Grounded defined-risk premium harvest.")
        )
        self.trades.append(trade)
        self._save()
        return trade

    def close_and_reflect(self, trade_id: str, exit_price: float, realized_pnl: float) -> Optional[TradeRecord]:
        """
        Close a trade, run LLM reflection, adapt strategy parameters, and update epistemic memory.
        """
        trade = None
        for t in self.trades:
            if t.trade_id == trade_id or trade_id in t.trade_id:
                trade = t
                break

        if not trade:
            # No matching recorded entry exists for this trade_id. Log an honest, minimally
            # populated closure rather than inventing plausible-looking entry Greeks/VRP that were
            # never actually observed - unknown fields stay 0.0/"unknown", not fabricated numbers.
            inferred_underlying = trade_id.split("_")[0][:6] if "_" in trade_id else trade_id[:6]
            trade = TradeRecord(
                trade_id=f"TR-UNTRACKED-{datetime.now().strftime('%H%M%S')}",
                underlying=inferred_underlying,
                structure="unknown",
                entry_time=datetime.now(timezone.utc).isoformat(),
                entry_price=exit_price,
                exit_price=exit_price,
                entry_vrp=0.0,
                entry_iv_rank=0.0,
                entry_delta=0.0,
                entry_vega=0.0,
                max_profit=0.0,
                max_loss=max(0.01, abs(realized_pnl)),
                realized_pnl=realized_pnl,
                status="WIN" if realized_pnl > 0 else "LOSS",
                thesis="Untracked entry: this position was closed without a matching recorded "
                       "entry event, so entry-time Greeks/VRP are unknown rather than estimated."
            )
            self.trades.append(trade)

        now_iso = datetime.now(timezone.utc).isoformat()
        trade.exit_time = now_iso
        trade.exit_price = exit_price
        trade.realized_pnl = realized_pnl
        pnl_denominator = trade.max_profit if realized_pnl > 0 else trade.max_loss
        # 0.0 means the max profit/loss basis is genuinely unknown (untracked entry) - reporting
        # a percentage against an arbitrary denominator would be misleading, so leave it at 0.0
        # rather than inventing a number like "11500%".
        trade.pnl_pct = round((realized_pnl / pnl_denominator) * 100, 2) if pnl_denominator else 0.0
        trade.status = "WIN" if realized_pnl > 0 else ("LOSS" if realized_pnl < 0 else "SCRATCH")

        # Update totals
        self.total_realized_pnl += realized_pnl
        if trade.status == "WIN":
            self.win_count += 1
        elif trade.status == "LOSS":
            self.loss_count += 1
        self.learning_iterations += 1

        # Run Self-Improvement Meta-Reflection
        reflection, lessons, reflection_source = self._generate_reflection(trade)
        trade.reflection = reflection
        trade.lessons_learned = lessons
        trade.reflection_source = reflection_source

        # Adapt quantitative parameters
        self._adapt_parameters(trade)

        self._save()
        return trade

    def _generate_reflection(self, trade: TradeRecord) -> tuple[str, List[str], str]:
        """Generate Gemini reflection on trade outcome. Returns (reflection, lessons, source) where
        source is "gemini" for a real generated reflection or "template_fallback" for the
        deterministic canned text used when Gemini is unavailable or errors - callers must not
        present a template_fallback reflection as genuine model insight."""
        prompt = f"""You are the Meta-Cognitive Self-Improvement Engine of GlassBox Options.
Analyze this closed options trade:
- Underlying: {trade.underlying} ({trade.structure})
- Entry Price: ${trade.entry_price} -> Exit Price: ${trade.exit_price}
- Entry VRP: {trade.entry_vrp} pts | IV Rank: {trade.entry_iv_rank}%
- Realized PnL: ${trade.realized_pnl} ({trade.pnl_pct}%) -> Status: {trade.status}
- Original Thesis: "{trade.thesis}"

Provide:
1. A 2-sentence quantitative reflection on what drove this outcome.
2. Two concise bullet points summarizing actionable lessons for future trade structuring."""

        try:
            if self.gemini_client:
                res = self.gemini_client.generate_content(prompt)
                text = res.text.strip()
                lines = [line.strip("- *").strip() for line in text.split("\n") if line.strip()]
                reflection = text[:250]
                lessons = [l for l in lines if len(l) > 15 and not l.startswith("You")][:2]
                if not lessons:
                    lessons = [f"Adjust strike buffer on {trade.underlying} based on realized vol velocity."]
                return reflection, lessons, "gemini"
        except Exception as e:
            print(f"[REFLECTION ENGINE ERROR] {e}")

        # Deterministic reflection fallback - templated text, not a generated insight. Tagged
        # "template_fallback" so callers/UI never present this as genuine LLM reasoning.
        if trade.status == "WIN":
            reflection = f"[Template] Thesis verified on {trade.underlying}. Theta decay harvested successfully with entry VRP of +{trade.entry_vrp} pts."
            lessons = [
                f"Elevated VRP on {trade.underlying} consistently provided statistical margin of safety.",
                "Enforcing 50% profit target prevented late-cycle delta slippage."
            ]
        else:
            reflection = f"[Template] Underlying {trade.underlying} moved faster than implied volatility expectation, stressing wings."
            lessons = [
                f"Widen wing buffer multiplier on {trade.underlying} when VRP is below 4.0 pts.",
                "Tighten stop-loss threshold during elevated macroeconomic headline windows."
            ]
        return reflection, lessons, "template_fallback"

    def _adapt_parameters(self, last_trade: TradeRecord):
        """Adapt strategy parameters based on empirical feedback."""
        win_rate = (self.win_count / (self.win_count + self.loss_count)) if (self.win_count + self.loss_count) > 0 else 0.67

        # If losing trade: widen wings and raise entry selectivity
        if last_trade.status == "LOSS":
            self.params.wing_buffer_multiplier = round(min(1.50, self.params.wing_buffer_multiplier + 0.05), 2)
            self.params.vrp_entry_threshold = round(min(4.0, self.params.vrp_entry_threshold + 0.25), 2)
            # Reduce regime weight
            for regime in self.params.regime_weights:
                if "High IV" not in regime:
                    self.params.regime_weights[regime] = round(max(0.3, self.params.regime_weights[regime] - 0.05), 2)
        else:
            # On winning streak: stabilize wing multiplier and reward high-VRP regimes
            if win_rate > 0.70:
                self.params.wing_buffer_multiplier = round(max(0.90, self.params.wing_buffer_multiplier - 0.02), 2)
                self.params.vrp_entry_threshold = round(max(1.5, self.params.vrp_entry_threshold - 0.1), 2)
                self.params.regime_weights["High IV / Elevated VRP"] = round(min(1.5, self.params.regime_weights.get("High IV / Elevated VRP", 1.2) + 0.05), 2)

    def get_learned_prompt_context(self) -> str:
        """Inject learned heuristics and recent reflection wisdom directly into LLM prompts."""
        win_rate = round((self.win_count / (self.win_count + self.loss_count or 1)) * 100, 1)
        recent_lessons = []
        for t in reversed(self.trades[-4:]):
            recent_lessons.extend(t.lessons_learned)
        recent_lessons_str = "\n- ".join(recent_lessons[:4]) if recent_lessons else "Maintain strict delta-neutral defined risk."

        return f"""[SELF-IMPROVING MEMORY CONTEXT]
- Historical Win Rate: {win_rate}% over {self.learning_iterations} trade cycles (Net Realized PnL: ${self.total_realized_pnl:,.2f})
- Current Learned Wing Buffer Multiplier: {self.params.wing_buffer_multiplier}x
- Minimum VRP Entry Threshold: {self.params.vrp_entry_threshold} pts
- Recent Meta-Cognitive Lessons Learned:
- {recent_lessons_str}
Prioritize these learned empirical constraints when sizing and structuring legs."""

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Return structured summary for the frontend learning visualizer."""
        total_trades = self.win_count + self.loss_count
        win_rate = round((self.win_count / total_trades * 100), 1) if total_trades > 0 else 0.0
        return {
            "total_trades": len(self.trades),
            "closed_trades": total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate_pct": win_rate,
            "total_realized_pnl": round(self.total_realized_pnl, 2),
            "learning_iterations": self.learning_iterations,
            "parameters": self.params.model_dump(),
            "recent_trades": [t.model_dump() for t in reversed(self.trades[-6:])]
        }

# Global Singleton
self_improving_memory = SelfImprovingMemory()
