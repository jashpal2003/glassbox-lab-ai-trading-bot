"""
reasoning/regime_classifier.py - Market regime classifier with deterministic rules & tags.
"""

from typing import List, Tuple
from shared.schemas import MarketContext

def classify_regime(ctx: MarketContext) -> Tuple[str, List[str]]:
    """
    Classifies the current volatility & trading regime.
    Returns (primary_regime, list_of_regime_tags).
    """
    tags: List[str] = []
    
    if ctx.vix is None or ctx.vix >= 30.0:
        return "stand_down_high_vix", ["high_vix", "stand_down"]
        
    if ctx.is_earnings_blackout:
        return "stand_down_earnings", ["earnings_blackout", "stand_down"]

    if ctx.iv_rank >= 50.0 and ctx.vrp > 0:
        tags.append("sell_premium")
        tags.append("iv_rank_high")
        tags.append("positive_vrp")
        if not ctx.earnings_days or ctx.earnings_days > 14:
            tags.append("no_earnings")
        return "sell_premium", tags

    if ctx.iv_rank < 30.0:
        tags.append("low_volatility")
        tags.append("debit_or_neutral")
        return "neutral", tags

    tags.append("normal_volatility")
    return "neutral", tags
