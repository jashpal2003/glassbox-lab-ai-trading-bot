"""
perception/vol_metrics.py - Mathematical calculations for IV Rank, Realized Volatility, and VRP.
"""

import math
from typing import Dict, List, Optional, Tuple

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


def calculate_ema(close_prices: List[float], span: int) -> Optional[float]:
    """
    Standard exponential moving average of the trailing closes. Returns None when there aren't
    enough real bars to compute it - callers must degrade rather than substitute a guess.
    """
    if not close_prices or len(close_prices) < span:
        return None
    alpha = 2.0 / (span + 1.0)
    ema = close_prices[0]
    for price in close_prices[1:]:
        ema = alpha * price + (1.0 - alpha) * ema
    return round(ema, 4)


def calculate_trend_signals(close_prices: List[float]) -> Dict[str, Optional[float]]:
    """
    Derives trend and volatility-term-structure signals from the SAME real daily closes already
    used for realized vol - no extra API calls, no new data source, nothing invented.

    Returns (all Optional; None means insufficient real history):
      trend_20d_pct        % price change over the trailing 20 sessions
      price_vs_ema20_pct   % distance of the latest close from its 20-session EMA
      rv_short             10-session realized vol, annualized %
      rv_long              60-session realized vol, annualized %
      rv_expansion_ratio   rv_short / rv_long   (>1 means realized vol is accelerating)
    """
    out: Dict[str, Optional[float]] = {
        "trend_20d_pct": None,
        "price_vs_ema20_pct": None,
        "rv_short": None,
        "rv_long": None,
        "rv_expansion_ratio": None,
    }
    if not close_prices:
        return out

    last = close_prices[-1]

    if len(close_prices) >= 21 and close_prices[-21] > 0:
        out["trend_20d_pct"] = round(((last - close_prices[-21]) / close_prices[-21]) * 100.0, 2)

    ema20 = calculate_ema(close_prices[-60:], 20)
    if ema20 and ema20 > 0:
        out["price_vs_ema20_pct"] = round(((last - ema20) / ema20) * 100.0, 2)

    if len(close_prices) >= 11:
        out["rv_short"] = calculate_realized_volatility(close_prices[-11:])
    if len(close_prices) >= 61:
        out["rv_long"] = calculate_realized_volatility(close_prices[-61:])

    rv_s, rv_l = out["rv_short"], out["rv_long"]
    if rv_s is not None and rv_l is not None and rv_l > 0:
        out["rv_expansion_ratio"] = round(rv_s / rv_l, 3)

    return out
