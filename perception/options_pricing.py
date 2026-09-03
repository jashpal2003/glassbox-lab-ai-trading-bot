"""
perception/options_pricing.py - Black-Scholes option pricing, Greeks, and implied-volatility
solver (European, no dividend adjustment).

Used only to fill gaps when Alpaca's live options quote feed doesn't cover a contract (thin
liquidity, indicative-feed-only entitlement, etc). Every call site feeds this real observed
inputs - a real spot price, a real contract strike/expiry, and either a real last-traded price
(to back-solve implied vol) or a real trailing realized-volatility estimate as a documented proxy.
This module never invents a market price from nothing; it prices/estimates from real inputs using
a standard, disclosed model.
"""

import math
from typing import Optional, Dict

RISK_FREE_RATE = 0.045  # approximate short-term T-bill rate, used only as a BS model input


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(spot: float, strike: float, t_years: float, vol: float, option_type: str, r: float = RISK_FREE_RATE) -> float:
    """Black-Scholes European option price."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if t_years <= 0 or vol <= 0:
        intrinsic = max(0.0, (spot - strike)) if option_type == "call" else max(0.0, (strike - spot))
        return round(intrinsic, 4)

    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    if option_type == "call":
        price = spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    else:
        price = strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return round(max(0.0, price), 4)


def bs_greeks(spot: float, strike: float, t_years: float, vol: float, option_type: str, r: float = RISK_FREE_RATE) -> Dict[str, float]:
    """Returns delta, gamma, theta (per day), vega (per 1 vol point)."""
    if spot <= 0 or strike <= 0 or t_years <= 0 or vol <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    pdf_d1 = _norm_pdf(d1)

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf_d1 * vol) / (2 * math.sqrt(t_years)) - r * strike * math.exp(-r * t_years) * _norm_cdf(d2)) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf_d1 * vol) / (2 * math.sqrt(t_years)) + r * strike * math.exp(-r * t_years) * _norm_cdf(-d2)) / 365.0

    gamma = pdf_d1 / (spot * vol * math.sqrt(t_years))
    vega = spot * pdf_d1 * math.sqrt(t_years) / 100.0  # sensitivity per 1 vol point (1%)

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 5),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }


def implied_volatility(
    target_price: float,
    spot: float,
    strike: float,
    t_years: float,
    option_type: str,
    r: float = RISK_FREE_RATE,
) -> Optional[float]:
    """
    Bisection solve for the implied volatility that reproduces target_price under Black-Scholes.
    Returns None if the price is degenerate/outside solvable bounds - callers must fall back to a
    realized-vol proxy in that case rather than guessing.
    """
    if target_price <= 0 or t_years <= 0 or spot <= 0 or strike <= 0:
        return None

    lo, hi = 0.01, 4.0
    price_lo = bs_price(spot, strike, t_years, lo, option_type, r)
    price_hi = bs_price(spot, strike, t_years, hi, option_type, r)
    if not (price_lo <= target_price <= price_hi):
        return None

    for _ in range(60):
        mid = (lo + hi) / 2.0
        price = bs_price(spot, strike, t_years, mid, option_type, r)
        if abs(price - target_price) < 0.001:
            return round(mid, 4)
        if price > target_price:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2.0, 4)
