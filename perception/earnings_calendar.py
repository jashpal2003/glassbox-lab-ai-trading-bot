"""
perception/earnings_calendar.py - Earnings blackout gating.

Primary source (best-effort, real data): Finnhub's free-tier earnings calendar endpoint, used
only when FINNHUB_API_KEY is configured (no cost, but requires the user to grab a free key at
finnhub.io - not bundled since none was provided).

Fallback: a small static reference table for a handful of liquid large-cap tickers, honestly
labeled as such. Anything outside both sources returns "unknown_no_data" rather than silently
claiming "no blackout" - callers must not treat unknown as a green light without checking the
source field.
"""

import os
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

# Static reference schedule - honest fallback only, not a live data feed.
KNOWN_EARNINGS: Dict[str, str] = {
    "SPY": "9999-12-31",  # ETFs don't have company earnings
    "QQQ": "9999-12-31",
    "IWM": "9999-12-31",
    "AAPL": "2026-10-29",
    "NVDA": "2026-11-18",
    "TSLA": "2026-10-21",
    "MSFT": "2026-10-27",
    "AMZN": "2026-10-30",
}

_FINNHUB_CACHE: Dict[str, Tuple[Optional[str], datetime]] = {}
_FINNHUB_CACHE_TTL_SECONDS = 3600


def _fetch_finnhub_next_earnings(ticker: str) -> Optional[str]:
    """Returns the next earnings date (YYYY-MM-DD) for ticker via Finnhub, or None on any failure."""
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        return None

    cached = _FINNHUB_CACHE.get(ticker)
    if cached and (datetime.now(timezone.utc) - cached[1]).total_seconds() < _FINNHUB_CACHE_TTL_SECONDS:
        return cached[0]

    try:
        today = datetime.now(timezone.utc).date()
        params = {
            "symbol": ticker,
            "from": today.isoformat(),
            "to": (today + timedelta(days=120)).isoformat(),
            "token": api_key,
        }
        resp = requests.get("https://finnhub.io/api/v1/calendar/earnings", params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        events = sorted(data.get("earningsCalendar", []), key=lambda e: e.get("date", "9999-12-31"))
        next_date = events[0]["date"] if events else None
        _FINNHUB_CACHE[ticker] = (next_date, datetime.now(timezone.utc))
        return next_date
    except Exception as e:
        print(f"[EARNINGS CALENDAR] Finnhub lookup failed for {ticker}: {e}")
        return None


def get_days_until_earnings(underlying: str, current_date: Optional[str] = None) -> Tuple[Optional[int], bool, str]:
    """
    Returns (days_until_earnings, is_in_blackout_window_2days, source).
    source is one of: "static_reference", "provider", "unknown_no_data".
    """
    ticker = underlying.upper()
    now = datetime.fromisoformat(current_date) if current_date else datetime.now(timezone.utc)

    if ticker in ["SPY", "QQQ", "IWM"]:
        return None, False, "static_reference"

    provider_date = _fetch_finnhub_next_earnings(ticker)
    if provider_date:
        try:
            earn_dt = datetime.fromisoformat(provider_date)
            delta_days = (earn_dt.date() - now.date()).days
            return delta_days, (0 <= delta_days <= 2), "provider"
        except Exception:
            pass

    earn_str = KNOWN_EARNINGS.get(ticker)
    if earn_str:
        try:
            earn_dt = datetime.fromisoformat(earn_str)
            delta_days = (earn_dt.date() - now.date()).days
            return delta_days, (0 <= delta_days <= 2), "static_reference"
        except Exception:
            pass

    return None, False, "unknown_no_data"
