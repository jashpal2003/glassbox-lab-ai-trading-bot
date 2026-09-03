"""
reasoning/strategy_arena.py - The Strategy Arena.

Instead of hard-coding one strategy, the agent maintains a population of strategy variants -
a structure plus a concrete parameter set - and makes them compete for the right to trade.

How a champion is chosen:
  1. The deterministic Regime Engine classifies the current environment from real market numbers.
  2. Only variants whose thesis applies to that regime are ELIGIBLE. A condor does not get to
     compete during volatility expansion; a debit spread does not get to compete when premium
     is rich and price is going nowhere.
  3. Every variant (eligible or not) is replayed over the SAME real historical closes by the
     backtest engine, so the comparison is apples-to-apples and the losers' numbers are visible
     too rather than hidden.
  4. A composite score is computed from expectancy, risk-adjusted return, win rate, drawdown and
     the Deflated Sharpe Ratio. The weights are fixed module constants, published below, so the
     ranking is reproducible and cannot be quietly tuned to flatter a favourite.
  5. The highest-scoring ELIGIBLE variant becomes champion and is passed to the reasoning layer
     as evidence-backed guidance. It is NOT permission to trade - the deterministic Risk Kernel
     still gates every resulting order independently.

There is no LLM in this file. The arena is evidence, not opinion.

Honesty note: a variant with too little real history to evaluate reports `trades=0` and scores
0.0 with an explanatory note. It is never given a placeholder score to make the leaderboard
look fuller.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from shared.schemas import (
    ArenaResult, MarketContext, MarketRegime, RegimeName, StrategyScore, StrategyVariant,
)
from reasoning.regime_engine import classify_regime_detailed
from perception.backtest_engine import replay_engine

# --- Composite score weights. Sum to 1.0. Published here deliberately. ---
W_EXPECTANCY = 0.35     # avg % return on risk per trade - the thing that actually compounds
W_SHARPE = 0.20         # risk-adjusted consistency
W_WIN_RATE = 0.15       # psychological/operational durability
W_DRAWDOWN = 0.15       # capital preservation (inverted: smaller drawdown scores higher)
W_DSR = 0.15            # Deflated Sharpe: probability the edge survives multi-testing bias

MIN_TRADES_FOR_CONFIDENCE = 8   # below this, the score is damped as statistically thin
ARENA_CACHE_TTL_SECONDS = 300


# --- The population. Each variant is a real, tradable defined-risk structure with parameters
# that the learning engine is allowed to mutate within bounds. ---
STRATEGY_REGISTRY: List[StrategyVariant] = [
    StrategyVariant(
        variant_id="ic_1sigma_50tp",
        name="Iron Condor - 1.0sigma wings, 50% target",
        structure="iron_condor",
        params={"wing_offset_sigma": 1.0, "wing_width_steps": 1.0, "profit_target_pct": 50.0,
                "stop_loss_multiplier": 2.0, "risk_per_trade_pct": 3.0},
        eligible_regimes=["HIGH_VOL_RANGE", "LOW_VOL_RANGE"],
        thesis="Sells both wings around a range-bound underlying to harvest the volatility risk "
               "premium; needs price to stay inside the expected move.",
    ),
    StrategyVariant(
        variant_id="ic_1_5sigma_75tp",
        name="Iron Condor - 1.5sigma wings, 75% target",
        structure="iron_condor",
        params={"wing_offset_sigma": 1.5, "wing_width_steps": 1.0, "profit_target_pct": 75.0,
                "stop_loss_multiplier": 2.5, "risk_per_trade_pct": 3.0},
        eligible_regimes=["HIGH_VOL_RANGE", "HIGH_VOL_TREND"],
        thesis="Wider short strikes buy room against a drifting underlying, trading a smaller "
               "credit for a materially higher probability of expiring inside the wings.",
    ),
    StrategyVariant(
        variant_id="pcs_1sigma",
        name="Put Credit Spread - 1.0sigma short strike",
        structure="credit_spread_put",
        params={"wing_offset_sigma": 1.0, "wing_width_steps": 1.0, "profit_target_pct": 50.0,
                "stop_loss_multiplier": 2.0, "risk_per_trade_pct": 3.0},
        eligible_regimes=["HIGH_VOL_RANGE", "HIGH_VOL_TREND", "LOW_VOL_TREND"],
        thesis="One-sided short premium below the market. Profits from time decay and from an "
               "upward or flat drift; only the downside needs defending.",
    ),
    StrategyVariant(
        variant_id="ccs_1sigma",
        name="Call Credit Spread - 1.0sigma short strike",
        structure="credit_spread_call",
        params={"wing_offset_sigma": 1.0, "wing_width_steps": 1.0, "profit_target_pct": 50.0,
                "stop_loss_multiplier": 2.0, "risk_per_trade_pct": 3.0},
        eligible_regimes=["HIGH_VOL_RANGE", "HIGH_VOL_TREND"],
        thesis="One-sided short premium above the market, for when rich premium coincides with "
               "a downward or stalling trend.",
    ),
    StrategyVariant(
        variant_id="bull_call_debit",
        name="Bull Call Debit Spread",
        structure="debit_spread",
        params={"wing_offset_sigma": 1.0, "wing_width_steps": 2.0, "profit_target_pct": 60.0,
                "stop_loss_multiplier": 1.0, "risk_per_trade_pct": 2.0},
        eligible_regimes=["LOW_VOL_TREND"],
        thesis="When premium is cheap, paying a small fixed debit for defined directional "
               "exposure beats selling thin premium for negligible credit.",
    ),
    StrategyVariant(
        variant_id="long_strangle",
        name="Long Strangle - 1.0sigma strikes",
        structure="strangle",
        params={"wing_offset_sigma": 1.0, "profit_target_pct": 80.0,
                "stop_loss_multiplier": 1.0, "risk_per_trade_pct": 1.5},
        eligible_regimes=["VOL_EXPANSION", "LOW_VOL_RANGE"],
        thesis="Long volatility on both sides. The one structure in the population that benefits "
               "when realized vol accelerates past implied.",
    ),
]


def _normalize(value: float, worst: float, best: float) -> float:
    """Linear 0-1 normalisation, clamped. Handles inverted metrics via worst > best."""
    if best == worst:
        return 0.5
    return max(0.0, min(1.0, (value - worst) / (best - worst)))


def compute_composite_score(result: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """
    Turns a backtest result into a single 0-100 score plus the per-component breakdown, so the
    dashboard can show exactly why one variant outranked another.
    """
    trades = int(result.get("total_trades", 0))
    if trades == 0:
        return 0.0, {}

    expectancy = float(result.get("expectancy_pct", 0.0))
    sharpe = float(result.get("raw_sharpe_ratio", 0.0))
    win_rate = float(result.get("win_rate_pct", 0.0))
    drawdown = float(result.get("max_drawdown_pct", 0.0))
    dsr = float(result.get("deflated_sharpe_ratio", 0.0))

    components = {
        "expectancy": _normalize(expectancy, -15.0, 25.0) * W_EXPECTANCY,
        "sharpe": _normalize(sharpe, -1.0, 3.0) * W_SHARPE,
        "win_rate": _normalize(win_rate, 20.0, 90.0) * W_WIN_RATE,
        "drawdown": _normalize(drawdown, 25.0, 0.0) * W_DRAWDOWN,   # inverted on purpose
        "deflated_sharpe": _normalize(dsr, 0.0, 1.0) * W_DSR,
    }
    raw = sum(components.values())

    # Statistically thin samples get damped rather than dropped, so a variant that got lucky in
    # 3 trades cannot outrank one with a real 30-trade record.
    confidence = min(1.0, trades / MIN_TRADES_FOR_CONFIDENCE)
    score = round(raw * 100.0 * (0.6 + 0.4 * confidence), 1)

    breakdown = {k: round(v * 100.0, 1) for k, v in components.items()}
    breakdown["sample_confidence"] = round(confidence * 100.0, 1)
    return score, breakdown


class StrategyArena:
    def __init__(self, registry: Optional[List[StrategyVariant]] = None):
        self.registry: List[StrategyVariant] = list(registry or STRATEGY_REGISTRY)
        self._cache: Dict[str, Tuple[ArenaResult, datetime]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ registry

    def get_registry(self) -> List[StrategyVariant]:
        return list(self.registry)

    def get_variant(self, variant_id: str) -> Optional[StrategyVariant]:
        return next((v for v in self.registry if v.variant_id == variant_id), None)

    def update_variant_params(self, variant_id: str, params: Dict[str, float]) -> Optional[StrategyVariant]:
        """
        Applies a promoted parameter change to a variant. Called only by the learning engine's
        promotion step, never directly by an LLM.
        """
        variant = self.get_variant(variant_id)
        if not variant:
            return None
        variant.params.update(params)
        self.invalidate_cache()
        return variant

    def invalidate_cache(self):
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------------ tournament

    def run_tournament(
        self,
        ctx: MarketContext,
        lookback_days: int = 365,
        use_cache: bool = True,
    ) -> ArenaResult:
        """
        Scores every variant on the same real price history and returns the ranked leaderboard
        with the eligible champion for the CURRENT regime.
        """
        symbol = ctx.underlying.upper()
        regime = classify_regime_detailed(ctx)
        cache_key = f"{symbol}:{regime.regime}:{lookback_days}"

        if use_cache:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached:
                result, cached_at = cached
                if (datetime.now(timezone.utc) - cached_at).total_seconds() < ARENA_CACHE_TTL_SECONDS:
                    return result

        # One shared price-history fetch for the whole tournament.
        closes = replay_engine.get_closes(symbol, lookback_days)
        num_variants = len(self.registry)

        scores: List[StrategyScore] = []
        for variant in self.registry:
            eligible = regime.regime in variant.eligible_regimes
            result = replay_engine.run_backtest(
                symbol=symbol,
                days=lookback_days,
                strategy_type=variant.structure,
                # Every variant is one of the hypotheses being tested this run, which is exactly
                # what the Deflated Sharpe Ratio needs to deflate against.
                trials_tested=num_variants,
                params=variant.params,
                closes=closes,
            )
            score, breakdown = compute_composite_score(result)
            trades = int(result.get("total_trades", 0))
            note = ""
            if trades == 0:
                note = ("No completed cycles on the available real history - not enough bars, or "
                        "this structure could never be opened for a credit at these parameters.")
            elif trades < MIN_TRADES_FOR_CONFIDENCE:
                note = f"Thin sample ({trades} trades) - score damped for low statistical confidence."

            scores.append(StrategyScore(
                variant_id=variant.variant_id,
                name=variant.name,
                structure=variant.structure,
                eligible=eligible,
                trades=trades,
                win_rate_pct=float(result.get("win_rate_pct", 0.0)),
                expectancy_pct=float(result.get("expectancy_pct", 0.0)),
                total_return_pct=float(result.get("total_return_pct", 0.0)),
                max_drawdown_pct=float(result.get("max_drawdown_pct", 0.0)),
                sharpe=float(result.get("raw_sharpe_ratio", 0.0)),
                profit_factor=float(result.get("profit_factor", 0.0)),
                deflated_sharpe=float(result.get("deflated_sharpe_ratio", 0.0)),
                score=score,
                score_breakdown=breakdown,
                equity_curve=list(result.get("equity_curve", []))[:80],
                note=note,
            ))

        # Rank: eligible first, then by score. The ineligible ones stay visible with their real
        # numbers so the selection is inspectable rather than a black box.
        scores.sort(key=lambda s: (s.eligible, s.score), reverse=True)

        champion = next((s for s in scores if s.eligible and s.trades > 0 and s.score > 0), None)
        data_available = any(s.trades > 0 for s in scores)

        if champion:
            rationale = (
                f"{champion.name} is the highest-scoring structure eligible for the "
                f"{regime.label} regime: composite {champion.score}/100 over {champion.trades} "
                f"replayed cycles on real {symbol} history (win rate {champion.win_rate_pct}%, "
                f"expectancy {champion.expectancy_pct:+.2f}% on risk, max drawdown "
                f"{champion.max_drawdown_pct}%, deflated Sharpe {champion.deflated_sharpe})."
            )
        elif not data_available:
            rationale = (
                "No champion: no real historical bars were available to score any variant "
                "(no Alpaca credentials configured, or the market-data fetch failed). The agent "
                "stands down rather than trading an unevaluated strategy."
            )
        else:
            rationale = (
                f"No champion: no strategy in the population is eligible for the {regime.label} "
                f"regime with a positive evidence score. Standing down is the correct action here."
            )

        arena_result = ArenaResult(
            symbol=symbol,
            regime=regime,
            lookback_days=lookback_days,
            scores=scores,
            champion_id=champion.variant_id if champion else None,
            champion_name=champion.name if champion else None,
            champion_rationale=rationale,
            data_source="real_historical_bars" if data_available else "unavailable",
            methodology=(
                f"Every variant replayed over the same real {symbol} daily closes "
                f"({lookback_days}d lookback). Composite score = "
                f"{W_EXPECTANCY:.0%} expectancy + {W_SHARPE:.0%} Sharpe + {W_WIN_RATE:.0%} win rate "
                f"+ {W_DRAWDOWN:.0%} drawdown (inverted) + {W_DSR:.0%} deflated Sharpe, damped for "
                f"samples under {MIN_TRADES_FOR_CONFIDENCE} trades. Eligibility is set by the "
                f"deterministic regime engine, not by the score."
            ),
            known_bias=(
                "entry premium is modeled as implied vol = trailing "
                "realized vol x 1.15, because free granular historical options pricing isn't "
                "available. That assumption hands premium SELLERS a structurally positive "
                "volatility risk premium in every replayed cycle, so short-premium variants "
                "(condors, credit spreads) are systematically flattered relative to long-vol "
                "variants (strangles, debit spreads). Cross-variant ranking within the same family "
                "is meaningful; absolute returns and short-vs-long-vol comparisons are not. Only "
                "the real underlying price path and the exact expiry payoff are assumption-free."
            ),
        )

        with self._lock:
            self._cache[cache_key] = (arena_result, datetime.now(timezone.utc))
        return arena_result

    # ------------------------------------------------------------------ guidance

    def build_guidance(self, arena: ArenaResult) -> str:
        """
        Formats the arena's evidence for injection into the LLM prompt. The model is told what
        the evidence says and which structures are eligible; it still has to justify its own
        choice, and the Risk Kernel still gates the result.
        """
        eligible = [s for s in arena.scores if s.eligible]
        if not eligible:
            return (
                f"[STRATEGY ARENA]\nRegime: {arena.regime.label} ({arena.regime.regime}).\n"
                f"No strategy in the tested population is eligible for this regime. "
                f"Propose size 0 (stand down) unless you can justify otherwise from the numbers."
            )

        lines = [
            "[STRATEGY ARENA - EVIDENCE FROM REAL HISTORICAL REPLAY]",
            f"Regime: {arena.regime.label} ({arena.regime.regime}), "
            f"confidence {arena.regime.confidence_pct}%.",
            f"Eligible structures ranked by measured performance on real {arena.symbol} history:",
        ]
        for s in eligible:
            if s.trades == 0:
                lines.append(f"  - {s.structure} ({s.name}): no evaluable history.")
                continue
            lines.append(
                f"  - {s.structure} ({s.name}): score {s.score}/100, {s.trades} cycles, "
                f"win rate {s.win_rate_pct}%, expectancy {s.expectancy_pct:+.2f}% on risk, "
                f"max drawdown {s.max_drawdown_pct}%."
            )
        if arena.champion_name:
            champ = next(s for s in arena.scores if s.variant_id == arena.champion_id)
            params = self.get_variant(arena.champion_id)
            lines.append(
                f"Evidence-backed champion: {champ.structure} using "
                f"{params.params if params else {}}."
            )
        lines.append(
            "Prefer the champion structure unless the current numbers give you a specific, "
            "quantitative reason to pick a different ELIGIBLE structure - and state that reason "
            "in your rationale."
        )
        return "\n".join(lines)


strategy_arena = StrategyArena()
