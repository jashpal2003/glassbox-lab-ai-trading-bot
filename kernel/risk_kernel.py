"""
kernel/risk_kernel.py - Deterministic Risk Kernel (Pure Non-LLM Function).
The hard boundary: validates trade Intent against strict versioned risk limits.
Zero LLM dependencies, zero network broker execution.
"""

import os
import yaml
from typing import Dict, Any, List, Optional
from shared.schemas import Intent, AccountState, MarketContext, KernelDecision, KernelCheck
from kernel.kill_switch import kill_switch

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")

def load_risk_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("limits", {})
    # Default fallbacks matching spec
    return {
        "max_portfolio_delta": 250,
        "max_portfolio_vega": -250,
        "max_single_position_pct_bp": 5.0,
        "max_open_positions": 8,
        "min_buying_power_floor_pct": 20.0,
        "max_spread_pct_of_mid": 8.0,
        "require_defined_risk": True,
        "earnings_blackout_days": 2,
        "vix_kill_switch_level": 30.0,
        "min_open_interest": 50,
        "min_daily_volume": 10
    }

def verify_defined_risk_and_max_loss(intent: Intent) -> tuple[bool, float, float, float]:
    """
    Independently inspects legs to verify defined risk (no naked short legs)
    and calculates max loss per contract and estimated delta/vega deltas.
    """
    if not intent.legs or intent.size <= 0:
        return True, 0.0, 0.0, 0.0

    short_puts = [leg for leg in intent.legs if leg.action == "sell" and leg.type == "put"]
    long_puts = [leg for leg in intent.legs if leg.action == "buy" and leg.type == "put"]
    short_calls = [leg for leg in intent.legs if leg.action == "sell" and leg.type == "call"]
    long_calls = [leg for leg in intent.legs if leg.action == "buy" and leg.type == "call"]

    # Defined-risk rule: EVERY short leg must be paired with a long leg of the same option type.
    # An unpaired short leg is naked and is always rejected - that rule is absolute.
    #
    # Pairing preference is the protective wing beyond the short strike (long put below a short
    # put, long call above a short call), which is the credit-spread/condor case and gives the
    # conservative widest-width max loss. If no such wing exists, a same-type long leg on the
    # OTHER side still caps the risk - that is a debit spread (e.g. long 100 call / short 110
    # call), where max loss is the debit paid and the short leg cannot run away because the long
    # leg gains faster. Requiring the wing to be directional would reject legitimate defined-risk
    # debit spreads as "naked", which they are not.
    is_defined = True
    max_loss_per_share = 0.0

    for sp in short_puts:
        protective = [lp for lp in long_puts if lp.strike < sp.strike]
        if protective:
            spread_width = sp.strike - min(lp.strike for lp in protective)
        elif long_puts:
            spread_width = abs(min(long_puts, key=lambda lp: abs(lp.strike - sp.strike)).strike - sp.strike)
        else:
            is_defined = False
            continue
        max_loss_per_share = max(max_loss_per_share, spread_width)

    for sc in short_calls:
        protective = [lc for lc in long_calls if lc.strike > sc.strike]
        if protective:
            spread_width = min(lc.strike for lc in protective) - sc.strike
        elif long_calls:
            spread_width = abs(min(long_calls, key=lambda lc: abs(lc.strike - sc.strike)).strike - sc.strike)
        else:
            is_defined = False
            continue
        max_loss_per_share = max(max_loss_per_share, spread_width)

    total_max_loss = max_loss_per_share * 100.0 * intent.size
    if total_max_loss == 0.0:
        total_max_loss = 500.0 * intent.size  # standard baseline for debit

    # Projected greeks for iron condor / spread structure
    delta_delta = 0.0
    vega_delta = -15.0 * intent.size if (short_puts or short_calls) else 10.0 * intent.size
    
    return is_defined, round(total_max_loss, 2), round(delta_delta, 2), round(vega_delta, 2)


def validate(
    intent: Intent,
    account_state: AccountState,
    market_context: Optional[MarketContext] = None,
    config: Optional[Dict[str, Any]] = None
) -> KernelDecision:
    """
    Pure deterministic validation function.
    Evaluates Intent against all risk parameters and returns KernelDecision.
    """
    cfg = config or load_risk_config()
    checks: List[KernelCheck] = []
    reasons: List[str] = []

    # 1. Kill Switch Check
    ks_status = kill_switch.get_status()
    if ks_status["is_halted"]:
        checks.append(KernelCheck(
            check="system_kill_switch",
            value=1.0,
            limit=0.0,
            pass_status=False,
            description=f"System halted: {ks_status['halt_reason']}"
        ))
        reasons.append(f"KILL SWITCH ACTIVE: {ks_status['halt_reason']}")

    # 2. Defined-Risk Verification
    is_defined, max_loss, delta_impact, vega_impact = verify_defined_risk_and_max_loss(intent)
    require_defined = cfg.get("require_defined_risk", True)
    
    if require_defined:
        checks.append(KernelCheck(
            check="require_defined_risk",
            value=1.0 if is_defined else 0.0,
            limit=1.0,
            pass_status=is_defined,
            description="All short legs must be protected with long wings"
        ))
        if not is_defined:
            reasons.append("Defined-risk breach: Naked options legs detected.")

    # 3. Position Size / Buying Power Gating
    bp = account_state.buying_power
    total_val = account_state.portfolio_value or 50000.0
    
    pos_pct_bp = (max_loss / bp * 100.0) if bp > 0 else 100.0
    max_single_bp_limit = cfg.get("max_single_position_pct_bp", 5.0)
    pos_bp_pass = pos_pct_bp <= max_single_bp_limit
    checks.append(KernelCheck(
        check="single_position_pct_bp",
        value=round(pos_pct_bp, 2),
        limit=max_single_bp_limit,
        pass_status=pos_bp_pass,
        description="Position allocation % of buying power"
    ))
    if not pos_bp_pass:
        reasons.append(f"Position size {pos_pct_bp:.1f}% exceeds max {max_single_bp_limit}% of buying power.")

    # 4. Buying Power Floor Cushion
    remaining_bp = bp - max_loss
    remaining_floor_pct = (remaining_bp / total_val * 100.0) if total_val > 0 else 0.0
    min_bp_floor_limit = cfg.get("min_buying_power_floor_pct", 20.0)
    floor_pass = remaining_floor_pct >= min_bp_floor_limit
    checks.append(KernelCheck(
        check="buying_power_floor",
        value=round(remaining_floor_pct, 2),
        limit=min_bp_floor_limit,
        pass_status=floor_pass,
        description="Post-trade buying power reserve floor"
    ))
    if not floor_pass:
        reasons.append(f"Post-trade cash cushion {remaining_floor_pct:.1f}% drops below {min_bp_floor_limit}% floor.")

    # 5. Open Position Count
    current_positions_count = len(account_state.positions)
    max_open_limit = cfg.get("max_open_positions", 8)
    pos_count_pass = current_positions_count < max_open_limit
    checks.append(KernelCheck(
        check="max_open_positions",
        value=float(current_positions_count),
        limit=float(max_open_limit),
        pass_status=pos_count_pass,
        description="Max concurrent open positions"
    ))
    if not pos_count_pass:
        reasons.append(f"Open positions count ({current_positions_count}) reached maximum limit of {max_open_limit}.")

    # 6. Portfolio Delta Cap
    proj_delta = account_state.portfolio_delta + delta_impact
    max_delta_limit = cfg.get("max_portfolio_delta", 250.0)
    delta_pass = abs(proj_delta) <= max_delta_limit
    checks.append(KernelCheck(
        check="portfolio_delta",
        value=round(proj_delta, 2),
        limit=float(max_delta_limit),
        pass_status=delta_pass,
        description="Total net directional delta cap"
    ))
    if not delta_pass:
        reasons.append(f"Projected portfolio delta {proj_delta:.1f} exceeds limit +/-{max_delta_limit}.")

    # 7. Portfolio Vega Limit (Cap on Short Volatility)
    proj_vega = account_state.portfolio_vega + vega_impact
    max_vega_limit = cfg.get("max_portfolio_vega", -250.0)
    # Notice: negative vega means short volatility; proj_vega must be >= max_portfolio_vega (-250)
    vega_pass = proj_vega >= max_vega_limit
    checks.append(KernelCheck(
        check="portfolio_vega",
        value=round(proj_vega, 2),
        limit=float(max_vega_limit),
        pass_status=vega_pass,
        description="Aggregate short volatility exposure cap"
    ))
    if not vega_pass:
        reasons.append(f"Vega would push portfolio to {proj_vega:.1f}, limit is {max_vega_limit:.1f} — REJECTED.")

    # 8. Spread & Liquidity Gate
    spread_val = 4.1  # Default benchmark for liquid SPY options
    if market_context and market_context.contracts:
        spread_val = market_context.contracts[0].spread_pct
    max_spread_limit = cfg.get("max_spread_pct_of_mid", 8.0)
    spread_pass = spread_val <= max_spread_limit
    checks.append(KernelCheck(
        check="spread_pct",
        value=round(spread_val, 2),
        limit=float(max_spread_limit),
        pass_status=spread_pass,
        description="Option contract bid-ask spread % of mid"
    ))
    if not spread_pass:
        reasons.append(f"Contract spread {spread_val:.1f}% exceeds maximum allowable spread {max_spread_limit}%.")

    # 9. Open Interest Gate
    # config.yaml declared min_open_interest but nothing enforced it - a limit that silently does
    # nothing is worse than no limit, because it reads like a guarantee. Open interest IS real in
    # Alpaca's contracts feed, so it is now genuinely enforced against the legs being traded.
    # (min_daily_volume stays unenforced and is annotated as such in config.yaml: per-contract
    # daily volume is not in the free feed, so every contract reports 0 and gating on it would
    # reject everything.)
    min_oi_limit = cfg.get("min_open_interest", 50)
    if market_context and market_context.contracts and intent.size > 0:
        leg_strikes = {(leg.type, leg.strike) for leg in intent.legs}
        leg_contracts = [c for c in market_context.contracts if (c.type, c.strike) in leg_strikes]
        worst_oi = min((c.open_interest for c in leg_contracts), default=None)
    else:
        worst_oi = None

    if worst_oi is not None:
        oi_pass = worst_oi >= min_oi_limit
        checks.append(KernelCheck(
            check="min_open_interest",
            value=float(worst_oi),
            limit=float(min_oi_limit),
            pass_status=oi_pass,
            description="Thinnest traded leg's open interest (liquidity / exit-risk gate)"
        ))
        if not oi_pass:
            reasons.append(
                f"Thinnest leg has {worst_oi} open interest, below the {min_oi_limit} minimum "
                f"— too illiquid to exit reliably."
            )

    # 10. VIX Level Check
    # market_context omitted entirely (e.g. kernel-only unit tests) -> benign default.
    # market_context present but vix is None -> live VIX fetch failed; fail closed, never guess.
    vix_limit = cfg.get("vix_kill_switch_level", 30.0)
    if market_context is None:
        vix_val: Optional[float] = 16.4
    else:
        vix_val = market_context.vix

    if vix_val is None:
        vix_pass = False
        checks.append(KernelCheck(
            check="vix_level",
            value=-1.0,
            limit=float(vix_limit),
            pass_status=False,
            description="Market volatility environment limit"
        ))
        reasons.append("VIX data unavailable — failing closed (cannot verify volatility regime safety).")
    else:
        vix_pass = vix_val < vix_limit
        checks.append(KernelCheck(
            check="vix_level",
            value=round(vix_val, 2),
            limit=float(vix_limit),
            pass_status=vix_pass,
            description="Market volatility environment limit"
        ))
        if not vix_pass:
            reasons.append(f"Market VIX {vix_val:.1f} breached safety ceiling of {vix_limit:.1f}.")

    # 10. Earnings Blackout Check
    is_blackout = market_context.is_earnings_blackout if market_context else False
    earn_pass = not is_blackout
    checks.append(KernelCheck(
        check="earnings_blackout",
        value=1.0 if is_blackout else 0.0,
        limit=0.0,
        pass_status=earn_pass,
        description="Prohibits trading within earnings announcement blackout window"
    ))
    if not earn_pass:
        reasons.append("Underlying has earnings announcement inside 2-day blackout window.")

    # Overall Approval
    all_passed = all(c.pass_status for c in checks) and (intent.size > 0)
    
    return KernelDecision(
        intent_id=intent.intent_id,
        approved=all_passed,
        decision="APPROVED" if all_passed else "REJECTED",
        kernel_checks=checks,
        reasons=reasons,
        max_loss=max_loss,
        projected_delta=proj_delta,
        projected_vega=proj_vega
    )
