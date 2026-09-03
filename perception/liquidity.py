"""
perception/liquidity.py - Contract-level liquidity filters and gating metrics.
"""

from typing import Tuple, Dict, Any

def compute_spread_pct_of_mid(bid: float, ask: float) -> Tuple[float, float]:
    """
    Computes mid price and (ask - bid) / mid * 100%.
    """
    if bid < 0 or ask < 0 or ask < bid:
        return 0.0, 100.0
    mid = (bid + ask) / 2.0
    if mid <= 0.001:
        return mid, 100.0
    spread_pct = ((ask - bid) / mid) * 100.0
    return round(mid, 2), round(spread_pct, 2)


def check_contract_liquidity(
    bid: float,
    ask: float,
    open_interest: int,
    volume: int,
    max_spread_pct: float = 8.0,
    min_open_interest: int = 50,
    min_daily_volume: int = 10
) -> Dict[str, Any]:
    """
    Gating function per contract. Returns passing status and metrics.
    """
    mid, spread_pct = compute_spread_pct_of_mid(bid, ask)
    
    spread_pass = spread_pct <= max_spread_pct
    oi_pass = open_interest >= min_open_interest
    vol_pass = volume >= min_daily_volume
    
    passed = spread_pass and oi_pass and vol_pass
    return {
        "passed": passed,
        "mid": mid,
        "spread_pct": spread_pct,
        "spread_pass": spread_pass,
        "oi_pass": oi_pass,
        "vol_pass": vol_pass,
        "open_interest": open_interest,
        "volume": volume
    }
