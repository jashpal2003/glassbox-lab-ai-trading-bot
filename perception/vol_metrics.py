"""
perception/vol_metrics.py - Mathematical calculations for IV Rank, Realized Volatility, and VRP.
"""

import math
from typing import List, Tuple

def calculate_realized_volatility(close_prices: List[float], annualization_factor: int = 252) -> float:
    """
    Calculate trailing close-to-close annualized realized volatility.
    Returns value in percentage points (e.g., 14.5 for 14.5%).
    """
    if not close_prices or len(close_prices) < 2:
        return 15.0  # Safe default baseline
    
    log_returns = []
    for i in range(1, len(close_prices)):
        p_prev = close_prices[i - 1]
        p_curr = close_prices[i]
        if p_prev > 0 and p_curr > 0:
            log_returns.append(math.log(p_curr / p_prev))
            
    if len(log_returns) < 2:
        return 15.0

    mean_ret = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_ret) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_stdev = math.sqrt(variance)
    annualized_vol = daily_stdev * math.sqrt(annualization_factor) * 100.0
    return round(annualized_vol, 2)


def calculate_iv_rank(current_iv: float, iv_52w_low: float, iv_52w_high: float) -> float:
    """
    Calculate IV Rank as a percentile: (Current IV - 52w Low) / (52w High - 52w Low) * 100.
    Clamped to [0.0, 100.0].
    """
    if iv_52w_high <= iv_52w_low:
        return 50.0
    
    rank = ((current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low)) * 100.0
    return round(max(0.0, min(100.0, rank)), 1)


def calculate_vrp(current_iv: float, realized_vol: float) -> float:
    """
    Calculate Volatility Risk Premium (VRP) = Implied Volatility - Realized Volatility.
    Positive indicates IV is richer than RV (favorable for selling premium).
    """
    return round(current_iv - realized_vol, 2)
