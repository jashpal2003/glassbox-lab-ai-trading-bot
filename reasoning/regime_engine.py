"""
reasoning/regime_engine.py - Deterministic Market Regime Engine.

Classifies the current trading environment into one of six named regimes from real, observed
numbers only. There is no LLM in this file: the same MarketContext always produces the same
regime, which is what makes the Strategy Arena's eligibility rules auditable.

Signals used (every one of them real, and all already fetched by the perception layer):
  - IV rank            percentile of current implied vol (disclosed realized-vol-range proxy)
  - VRP                implied minus realized vol, in vol points
  - VIX                real CBOE close; None when the fetch failed
  - trend_20d_pct      % price move over the trailing 20 real sessions
  - price_vs_ema20_pct % distance of spot from its 20-session EMA
  - rv_expansion_ratio 10-session realized vol / 60-session realized vol

When a signal is unavailable (short bar history, VIX fetch failure) the engine says so via
`data_complete=False` and lowers `confidence_pct` instead of substituting a plausible number.
EVENT_RISK is deliberately the fail-closed default: if we cannot verify the volatility
environment, we classify as event risk rather than assume it is safe to sell premium.
"""

from typing import Any, Dict, List, Optional, Tuple

from shared.schemas import MarketContext, MarketRegime, RegimeName

# --- Thresholds. Kept as named module constants so they are visible/quotable in the demo
# rather than buried as magic numbers inside the branching logic. ---
IV_RANK_HIGH = 50.0            # at/above this, implied vol is "rich" for this underlying
IV_RANK_LOW = 30.0             # below this, premium is too cheap to sell
VRP_POSITIVE = 0.0             # VRP above this means implied is above realized
TREND_STRONG_PCT = 3.0         # |20-session move| at/above this counts as directional
EMA_STRETCH_PCT = 1.5          # |spot vs EMA20| at/above this confirms the direction
VOL_EXPANSION_RATIO = 1.35     # short-window RV this many x the long window = vol accelerating
VIX_CEILING = 30.0             # matches kernel/config.yaml vix_kill_switch_level

REGIME_LABELS: Dict[str, Tuple[str, str]] = {
    "HIGH_VOL_RANGE": (
        "High IV / Range-Bound",
        "Implied vol is rich versus realized and price is not trending - the textbook environment "
        "for selling defined-risk premium on both sides.",
    ),
    "HIGH_VOL_TREND": (
        "High IV / Trending",
        "Premium is rich but price is directional, so two-sided structures get run over. Favors "
        "one-sided credit spreads placed against the trend's downside.",
    ),
    "LOW_VOL_TREND": (
        "Low IV / Trending",
        "Premium is cheap and price is moving - paying for directional exposure via debit spreads "
        "is better value than selling thin premium.",
    ),
    "LOW_VOL_RANGE": (
        "Low IV / Range-Bound",
        "Cheap premium and no direction. There is little statistical edge here; the correct action "
        "is usually to size down or stand aside.",
    ),
    "VOL_EXPANSION": (
        "Volatility Expansion",
        "Realized vol is accelerating relative to its own longer baseline, which is how short-vol "
        "positions get hurt. Favors long-vol structures or standing aside.",
    ),
    "EVENT_RISK": (
        "Event Risk / Stand Down",
        "An earnings window, a VIX ceiling breach, or missing volatility data. The system stands "
        "down rather than trading an environment it cannot verify.",
    ),
}


def _collect_signals(ctx: MarketContext) -> Tuple[Dict[str, Any], List[str]]:
    """Gathers the raw numbers the classification is based on, plus a list of missing ones."""
    signals: Dict[str, Any] = {
        "iv_rank": ctx.iv_rank,
        "realized_vol": ctx.realized_vol,
        "vrp": ctx.vrp,
        "vix": ctx.vix,
        "trend_20d_pct": ctx.trend_20d_pct,
        "price_vs_ema20_pct": ctx.price_vs_ema20_pct,
        "rv_short": ctx.rv_short,
        "rv_long": ctx.rv_long,
        "rv_expansion_ratio": ctx.rv_expansion_ratio,
        "earnings_days": ctx.earnings_days,
        "is_earnings_blackout": ctx.is_earnings_blackout,
    }
    missing = [k for k in ("vix", "trend_20d_pct", "rv_expansion_ratio") if signals.get(k) is None]
    return signals, missing


def _is_trending(ctx: MarketContext) -> Optional[bool]:
    """
    True/False when the trend signals are available, None when they aren't.
    Requires BOTH a meaningful 20-session move and confirmation that spot is stretched from its
    own EMA - one alone is too easy to trip on a single outlier session.
    """
    if ctx.trend_20d_pct is None:
        return None
    strong_move = abs(ctx.trend_20d_pct) >= TREND_STRONG_PCT
    if ctx.price_vs_ema20_pct is None:
        return strong_move
    return strong_move and abs(ctx.price_vs_ema20_pct) >= EMA_STRETCH_PCT


def classify_regime_detailed(ctx: MarketContext) -> MarketRegime:
    """Full regime classification with the supporting numbers attached for the audit trail."""
    signals, missing = _collect_signals(ctx)
    data_complete = not missing
    tags: List[str] = []

    # --- 1. Fail-closed conditions first. These override everything else. ---
    if ctx.is_earnings_blackout:
        tags = ["earnings_blackout", "stand_down"]
        return _build(ctx, "EVENT_RISK", tags, signals, 95.0, data_complete,
                      reason_tag="earnings blackout window")
    if ctx.vix is None:
        tags = ["vix_unavailable", "stand_down", "fail_closed"]
        return _build(ctx, "EVENT_RISK", tags, signals, 40.0, False,
                      reason_tag="live VIX unavailable - cannot verify volatility regime")
    if ctx.vix >= VIX_CEILING:
        tags = ["high_vix", "stand_down"]
        return _build(ctx, "EVENT_RISK", tags, signals, 95.0, data_complete,
                      reason_tag=f"VIX {ctx.vix:.1f} at/above the {VIX_CEILING:.0f} ceiling")

    # --- 2. Volatility expansion: realized vol accelerating against its own baseline. ---
    expanding = (
        ctx.rv_expansion_ratio is not None
        and ctx.rv_expansion_ratio >= VOL_EXPANSION_RATIO
    )
    if expanding and ctx.vrp <= VRP_POSITIVE:
        # Vol is accelerating AND implied is no longer compensating for it - worst case for
        # short premium.
        tags = ["vol_expansion", "negative_vrp", "long_vol_or_stand_aside"]
        return _build(ctx, "VOL_EXPANSION", tags, signals, 80.0, data_complete,
                      reason_tag=f"10d/60d realized vol ratio {ctx.rv_expansion_ratio:.2f} with VRP "
                                 f"{ctx.vrp:+.1f} pts")

    # --- 3. The IV-rank x trend matrix. ---
    trending = _is_trending(ctx)
    iv_high = ctx.iv_rank >= IV_RANK_HIGH
    iv_low = ctx.iv_rank < IV_RANK_LOW
    vrp_positive = ctx.vrp > VRP_POSITIVE

    # Confidence: how far the deciding signals sit from their thresholds, so a borderline
    # 50.4 IV rank does not present as a confident call.
    iv_margin = min(abs(ctx.iv_rank - IV_RANK_HIGH), abs(ctx.iv_rank - IV_RANK_LOW))
    conf = 55.0 + min(30.0, iv_margin)
    if trending is None:
        conf -= 15.0
        tags.append("trend_data_unavailable")
    if not data_complete:
        conf -= 10.0

    if iv_high and vrp_positive:
        tags.extend(["iv_rank_high", "positive_vrp", "sell_premium"])
        if trending:
            tags.append("directional")
            direction = "up" if (ctx.trend_20d_pct or 0) > 0 else "down"
            tags.append(f"trend_{direction}")
            return _build(ctx, "HIGH_VOL_TREND", tags, signals, conf, data_complete,
                          reason_tag=f"IV rank {ctx.iv_rank:.0f} with a {ctx.trend_20d_pct:+.1f}% "
                                     f"20-session move")
        tags.append("range_bound")
        return _build(ctx, "HIGH_VOL_RANGE", tags, signals, min(95.0, conf + 10.0), data_complete,
                      reason_tag=f"IV rank {ctx.iv_rank:.0f}, VRP {ctx.vrp:+.1f} pts, no strong trend")

    if iv_low:
        tags.extend(["iv_rank_low", "cheap_premium"])
        if trending:
            direction = "up" if (ctx.trend_20d_pct or 0) > 0 else "down"
            tags.extend(["directional", f"trend_{direction}", "debit_spread"])
            return _build(ctx, "LOW_VOL_TREND", tags, signals, conf, data_complete,
                          reason_tag=f"IV rank {ctx.iv_rank:.0f} with a {ctx.trend_20d_pct:+.1f}% "
                                     f"20-session move")
        tags.extend(["range_bound", "low_edge"])
        return _build(ctx, "LOW_VOL_RANGE", tags, signals, conf, data_complete,
                      reason_tag=f"IV rank {ctx.iv_rank:.0f} and no strong trend - thin edge")

    # --- 4. Middle ground: IV neither rich nor cheap. Treat direction as the tiebreaker. ---
    tags.append("normal_volatility")
    if trending:
        direction = "up" if (ctx.trend_20d_pct or 0) > 0 else "down"
        tags.extend(["directional", f"trend_{direction}"])
        regime: RegimeName = "HIGH_VOL_TREND" if vrp_positive else "LOW_VOL_TREND"
        return _build(ctx, regime, tags, signals, max(40.0, conf - 10.0), data_complete,
                      reason_tag=f"mid IV rank {ctx.iv_rank:.0f} with a {ctx.trend_20d_pct:+.1f}% "
                                 f"20-session move")

    tags.append("range_bound")
    regime = "HIGH_VOL_RANGE" if vrp_positive else "LOW_VOL_RANGE"
    return _build(ctx, regime, tags, signals, max(40.0, conf - 10.0), data_complete,
                  reason_tag=f"mid IV rank {ctx.iv_rank:.0f}, VRP {ctx.vrp:+.1f} pts, no strong trend")


def _build(
    ctx: MarketContext,
    regime: RegimeName,
    tags: List[str],
    signals: Dict[str, Any],
    confidence: float,
    data_complete: bool,
    reason_tag: str,
) -> MarketRegime:
    label, description = REGIME_LABELS[regime]
    return MarketRegime(
        regime=regime,
        label=label,
        description=f"{description} Classified on: {reason_tag}.",
        tags=tags,
        signals=signals,
        confidence_pct=round(max(0.0, min(100.0, confidence)), 1),
        data_complete=data_complete,
    )


def classify_regime(ctx: MarketContext) -> Tuple[str, List[str]]:
    """
    Backwards-compatible shim for the original classifier's (regime, tags) contract, which the
    LLM agent's deterministic fallback and the existing tests both call.
    """
    detailed = classify_regime_detailed(ctx)
    legacy_map = {
        "HIGH_VOL_RANGE": "sell_premium",
        "HIGH_VOL_TREND": "sell_premium_directional",
        "LOW_VOL_TREND": "debit_directional",
        "LOW_VOL_RANGE": "neutral",
        "VOL_EXPANSION": "long_vol",
        "EVENT_RISK": "stand_down",
    }
    return legacy_map[detailed.regime], detailed.tags
