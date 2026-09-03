"""
reasoning/blindfold.py - Act 3: Blindfold Volatility Risk Premium Anonymization Experiment.
Validates whether the model reasons mechanically from volatility and numbers or memorized ticker bias.
"""

from typing import Dict, Any, List, Optional
from shared.schemas import MarketContext, Intent
from reasoning.agent import LLMReasoningAgent

class BlindfoldExperiment:
    def __init__(self, agent: Optional[LLMReasoningAgent] = None):
        self.agent = agent or LLMReasoningAgent()

    def run_single_comparison(self, ctx: MarketContext, asset_alias: str = "ASSET_04") -> Dict[str, Any]:
        """
        Runs reasoning on normal context and anonymized context, comparing outcomes.
        """
        # 1. Normal run
        normal_intent = self.agent.propose_intent(ctx, anonymized=False)
        
        # 2. Anonymized run
        anon_intent = self.agent.propose_intent(ctx, anonymized=True, asset_alias=asset_alias)
        
        # 3. Compare dimensions
        structure_match = (normal_intent.structure == anon_intent.structure)
        conviction_diff = abs(normal_intent.conviction - anon_intent.conviction)
        conviction_match = conviction_diff <= 0.10
        
        tags_overlap = set(normal_intent.regime_tags).intersection(set(anon_intent.regime_tags))
        tags_match = len(tags_overlap) >= max(1, len(normal_intent.regime_tags) // 2)
        
        material_match = structure_match and conviction_match
        
        return {
            "underlying": ctx.underlying,
            "pseudonym": asset_alias,
            "normal_structure": normal_intent.structure,
            "anonymized_structure": anon_intent.structure,
            "normal_conviction": normal_intent.conviction,
            "anonymized_conviction": anon_intent.conviction,
            "structure_match": structure_match,
            "conviction_match": conviction_match,
            "tags_match": tags_match,
            "material_match": material_match,
            "normal_rationale": normal_intent.rationale,
            "anonymized_rationale": anon_intent.rationale
        }

    def run_multi_point_experiment(self, contexts: List[MarketContext]) -> Dict[str, Any]:
        """
        Runs comparison across a batch of market contexts (10+ decision points)
        and computes the empirical agreement rate.
        """
        results = []
        matches = 0
        
        for idx, ctx in enumerate(contexts):
            alias = f"ASSET_{idx+1:02d}"
            res = self.run_single_comparison(ctx, asset_alias=alias)
            results.append(res)
            if res["material_match"]:
                matches += 1
                
        total = len(contexts)
        agreement_rate = round((matches / total) * 100.0, 1) if total > 0 else 0.0
        
        return {
            "total_samples": total,
            "matched_samples": matches,
            "agreement_rate_pct": agreement_rate,
            "details": results,
            "verdict": "VRP_REASONING_HONEST" if agreement_rate >= 80.0 else "NOMINAL_DISCREPANCY"
        }
