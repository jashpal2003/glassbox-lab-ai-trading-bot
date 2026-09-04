"""
reasoning/agent.py - Multi-provider LLM Reasoning Agent for Intent Generation.
Supports Google Gemini, OpenRouter, and a deterministic fallback engine.
Enforces strict JSON schema validation and fail-closed architecture.
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
import requests

from shared.schemas import MarketContext, Intent, OptionLeg
from reasoning.regime_engine import classify_regime, classify_regime_detailed
from reasoning.llm_client import LLMClient


def _snap_legs_to_real_contracts(intent: Intent, ctx: MarketContext) -> Intent:
    """
    Snap every leg's strike/expiry to the nearest actually-listed contract of the same type in
    ctx.contracts (the real chain fetched from Alpaca). The LLM (and the deterministic fallback)
    reason about strikes independently of the exact real strike grid; without this, the kernel
    could approve an Intent that the executor then can't map to a tradable contract. If no real
    contracts are available for a leg's type (e.g. simulated feed), the leg is left as proposed.
    """
    if not ctx.contracts:
        return intent

    by_type: Dict[str, list] = {"call": [], "put": []}
    for c in ctx.contracts:
        by_type.setdefault(c.type, []).append(c)
    for lst in by_type.values():
        lst.sort(key=lambda c: c.strike)

    new_legs = []
    for leg in intent.legs:
        candidates = by_type.get(leg.type, [])
        if not candidates:
            new_legs.append(leg)
            continue
        nearest = min(candidates, key=lambda c: abs(c.strike - leg.strike))
        new_legs.append(OptionLeg(action=leg.action, type=leg.type, strike=nearest.strike, expiry=nearest.expiry))
    intent.legs = new_legs
    return intent

load_dotenv()

SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "prompts", "system_prompt.md")

def get_system_prompt() -> str:
    if os.path.exists(SYSTEM_PROMPT_PATH):
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "You are an options-structure recommender. Emit only valid Intent JSON."

class LLMReasoningAgent:
    def __init__(self):
        self.system_prompt = get_system_prompt()
        # One shared multi-provider client (Gemini -> OpenRouter -> deterministic). The reflection
        # engine reuses this exact client so a working secondary provider benefits every LLM
        # feature, not just Intent generation.
        self.llm = LLMClient(system_instruction=self.system_prompt)

        from reasoning.self_improvement import self_improving_memory
        self_improving_memory.llm = self.llm

    def get_provider_status(self) -> Dict[str, Any]:
        """Which LLM provider is actually answering right now - surfaced in the dashboard."""
        return self.llm.get_status()

    def propose_intent(
        self,
        ctx: MarketContext,
        anonymized: bool = False,
        asset_alias: str = "ASSET_01",
        arena: Optional[Any] = None,
    ) -> Intent:
        """
        Generate structured Intent from MarketContext.
        Enforces schema validation and fail-closed pattern with self-improving memory injection.

        `arena` is an optional ArenaResult. When supplied, the Strategy Arena's measured evidence
        (which structures are eligible in this regime, and how each performed on real historical
        replay) is injected into the prompt, and the deterministic fallback builds the arena's
        evidence-backed champion instead of a hardcoded guess. The arena constrains and informs
        the proposal - it never authorises it. The Risk Kernel still gates the result.
        """
        from reasoning.self_improvement import self_improving_memory
        display_ticker = asset_alias if anonymized else ctx.underlying
        learned_context = self_improving_memory.get_learned_prompt_context() if not anonymized else ""

        # Blindfold runs stay arena-free: the whole point of that experiment is to isolate what
        # the model does with the raw numbers, so feeding it ranked guidance would contaminate it.
        arena_context = ""
        if arena is not None and not anonymized:
            from reasoning.strategy_arena import strategy_arena
            arena_context = strategy_arena.build_guidance(arena)

        # Prepare context payload for prompt
        context_dict = {
            "underlying": display_ticker,
            "underlying_price": ctx.underlying_price,
            "iv_rank": ctx.iv_rank,
            "realized_vol": ctx.realized_vol,
            "vrp": ctx.vrp,
            "vix": ctx.vix,
            "earnings_days": ctx.earnings_days,
            "is_earnings_blackout": ctx.is_earnings_blackout,
            "trend_20d_pct": ctx.trend_20d_pct,
            "price_vs_ema20_pct": ctx.price_vs_ema20_pct,
            "realized_vol_10d": ctx.rv_short,
            "realized_vol_60d": ctx.rv_long,
            "vol_expansion_ratio": ctx.rv_expansion_ratio,
            "sample_contracts": [
                {
                    "symbol": c.symbol if not anonymized else c.symbol.replace(ctx.underlying, asset_alias),
                    "strike": c.strike,
                    "expiry": c.expiry,
                    "type": c.type,
                    "bid": c.bid,
                    "ask": c.ask,
                    "mid": c.mid,
                    "delta": c.delta,
                    "vega": c.vega
                }
                for c in ctx.contracts[:8]
            ]
        }

        user_content = (
            f"Market Context:\n{json.dumps(context_dict, indent=2)}\n\n"
            f"{arena_context}\n\n{learned_context}\n\n"
            f"Emit a single JSON Intent according to the schema."
        )

        # Attempt 1: Call Primary / Secondary LLM
        raw_response = self._call_llm(user_content)

        if raw_response:
            intent = self._parse_and_validate(raw_response, ctx.underlying if not anonymized else asset_alias)
            if intent:
                return _snap_legs_to_real_contracts(intent, ctx)

            # Attempt 2: Retry with validation feedback
            retry_prompt = f"Previous response was invalid JSON or violated schema. Error occurred. Strictly output ONLY valid JSON following the schema for:\n{json.dumps(context_dict)}"
            retry_response = self._call_llm(retry_prompt)
            if retry_response:
                intent_retry = self._parse_and_validate(retry_response, ctx.underlying if not anonymized else asset_alias)
                if intent_retry:
                    return _snap_legs_to_real_contracts(intent_retry, ctx)

        # Fail Closed / Deterministic Rule Fallback
        return _snap_legs_to_real_contracts(
            self._deterministic_intent_generation(ctx, display_ticker, arena=arena), ctx
        )

    def _call_llm(self, user_content: str) -> Optional[str]:
        """
        Generates via the shared multi-provider client (Gemini -> OpenRouter). Returns None when
        no provider could answer, which the caller treats as "use the deterministic path", never
        as "make something up".

        JSON mode + low temperature: this system's premise is deterministic, auditable
        risk-structuring, not creative variance - the same numeric inputs should reach the same
        structure and conviction run to run.
        """
        self.last_provider = None
        response = self.llm.generate(user_content, json_mode=True)
        if response.ok:
            self.last_provider = response.provider
            return response.text
        return None

    def _parse_and_validate(self, text: str, underlying: str) -> Optional[Intent]:
        """Parses JSON text and strictly validates against Intent Pydantic model."""
        try:
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
            
            # Ensure underlying matches
            data["underlying"] = underlying
            if "intent_id" not in data or not data["intent_id"]:
                data["intent_id"] = str(uuid.uuid4())
                
            return Intent(**data)
        except Exception as e:
            print(f"Schema validation error: {e}")
            return None

    def _stand_down_intent(self, ctx: MarketContext, display_ticker: str, target_expiry: str,
                            tags: list, reason: str) -> Intent:
        """A size-0 Intent: the system's explicit, auditable way of declining to trade."""
        price = ctx.underlying_price
        put_short = round((price * 0.98) / 5.0) * 5.0
        return Intent(
            intent_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            underlying=display_ticker,
            structure="credit_spread_put",
            legs=[
                OptionLeg(action="sell", type="put", strike=put_short, expiry=target_expiry),
                OptionLeg(action="buy", type="put", strike=put_short - 5.0, expiry=target_expiry),
            ],
            size=0,  # Zero size = Stand down
            rationale=f"Stand down: {reason}.",
            conviction=0.0,
            regime_tags=tags,
            snapshot_ref=""
        )

    def _deterministic_intent_generation(
        self, ctx: MarketContext, display_ticker: str, arena: Optional[Any] = None
    ) -> Intent:
        """
        Deterministic, rule-based fallback Intent generator - used whenever no LLM is reachable
        or every LLM response failed schema validation.

        When an ArenaResult is supplied, this builds the arena's evidence-backed champion using
        the SAME strike-selection math the backtest engine measured that champion with, so the
        structure actually traded is the structure whose track record was evaluated. Without an
        arena it falls back to the original IV-rank/VRP rules.
        """
        regime_detail = classify_regime_detailed(ctx)
        tags = regime_detail.tags
        target_expiry = ctx.contracts[0].expiry if ctx.contracts else (datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        price = ctx.underlying_price

        # Fail-closed environments. The regime engine already treats a missing VIX, a VIX ceiling
        # breach and an earnings blackout as EVENT_RISK, so one check covers all three.
        if regime_detail.regime == "EVENT_RISK":
            return self._stand_down_intent(
                ctx, display_ticker, target_expiry, tags,
                regime_detail.description.split("Classified on: ")[-1].rstrip(".")
            )

        # --- Arena-driven path: build the champion structure at real strikes. ---
        if arena is not None and getattr(arena, "champion_id", None):
            from reasoning.strategy_arena import strategy_arena
            from perception.backtest_engine import build_structure

            variant = strategy_arena.get_variant(arena.champion_id)
            champ_score = next((s for s in arena.scores if s.variant_id == arena.champion_id), None)
            if variant:
                # Days to expiry from the real contract expiry we are actually going to trade.
                try:
                    exp_date = datetime.strptime(target_expiry, "%Y-%m-%d").date()
                    dte_days = max(1, (exp_date - datetime.now(timezone.utc).date()).days)
                except Exception:
                    dte_days = 21
                t_years = dte_days / 365.0
                # Use the real observed ATM implied vol where the chain provided one, else the
                # real realized vol as the documented proxy.
                atm_iv = None
                if ctx.contracts:
                    atm = min(ctx.contracts, key=lambda c: abs(c.strike - price))
                    atm_iv = atm.implied_volatility / 100.0 if atm.implied_volatility else None
                iv_frac = atm_iv or max(0.05, ctx.realized_vol / 100.0)

                structure = build_structure(variant.structure, price, t_years, iv_frac, variant.params)
                if structure is not None:
                    legs = [
                        OptionLeg(
                            action="buy" if sign > 0 else "sell",
                            type=opt_type,
                            strike=strike,
                            expiry=target_expiry,
                        )
                        for sign, opt_type, strike in structure.legs
                    ]
                    evidence = (
                        f"score {champ_score.score}/100 over {champ_score.trades} replayed cycles, "
                        f"win rate {champ_score.win_rate_pct}%, expectancy "
                        f"{champ_score.expectancy_pct:+.2f}% on risk"
                        if champ_score else "arena-selected"
                    )
                    # Conviction is tied to real measured evidence and regime clarity, not vibes.
                    conviction = round(
                        min(0.95, 0.35 + (champ_score.score / 200.0 if champ_score else 0.0)
                            + (regime_detail.confidence_pct / 400.0)), 2
                    )
                    return Intent(
                        intent_id=str(uuid.uuid4()),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        underlying=display_ticker,
                        structure=variant.structure,
                        legs=legs,
                        size=1,
                        rationale=(
                            f"Regime {regime_detail.regime} (IV rank {ctx.iv_rank}, VRP {ctx.vrp:+.1f} pts, "
                            f"20d trend {ctx.trend_20d_pct if ctx.trend_20d_pct is not None else 'n/a'}%). "
                            f"Strategy Arena champion '{variant.name}': {evidence}."
                        ),
                        conviction=conviction,
                        regime_tags=tags,
                        snapshot_ref=""
                    )

        # --- No arena (or champion unbuildable): original IV-rank/VRP rules. ---
        put_short = round((price * 0.98) / 5.0) * 5.0
        put_long = put_short - 5.0
        call_short = round((price * 1.02) / 5.0) * 5.0
        call_long = call_short + 5.0

        if ctx.iv_rank >= 50.0 and ctx.vrp > 0:
            return Intent(
                intent_id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                underlying=display_ticker,
                structure="iron_condor",
                legs=[
                    OptionLeg(action="sell", type="put", strike=put_short, expiry=target_expiry),
                    OptionLeg(action="buy", type="put", strike=put_long, expiry=target_expiry),
                    OptionLeg(action="sell", type="call", strike=call_short, expiry=target_expiry),
                    OptionLeg(action="buy", type="call", strike=call_long, expiry=target_expiry)
                ],
                size=2,
                rationale=f"IV rank {ctx.iv_rank}, VRP +{ctx.vrp}pts, no earnings in window, regime=sell_premium",
                conviction=0.71,
                regime_tags=tags,
                snapshot_ref=""
            )

        # Moderate IV rank -> Defined-Risk Put Credit Spread
        return Intent(
            intent_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            underlying=display_ticker,
            structure="credit_spread_put",
            legs=[
                OptionLeg(action="sell", type="put", strike=put_short, expiry=target_expiry),
                OptionLeg(action="buy", type="put", strike=put_long, expiry=target_expiry)
            ],
            size=1,
            rationale=f"IV rank {ctx.iv_rank} is moderate; structuring defined-risk put credit spread.",
            conviction=0.65,
            regime_tags=tags,
            snapshot_ref=""
        )
