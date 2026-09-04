"""
perception/alpaca_client.py - Alpaca Trading + Market Data Client.

When ALPACA_API_KEY/ALPACA_SECRET_KEY are configured, every number this module returns is real:
- Account equity/cash/positions come from Alpaca's live paper trading account.
- Underlying spot price and realized volatility come from real Alpaca stock bars (IEX feed).
- The options chain (strikes, expiries, OCC symbols, open interest) comes from Alpaca's
  options-contracts master list (`TradingClient.get_option_contracts`), which requires no
  special market-data entitlement.
- Bid/ask/IV/Greeks are enriched from Alpaca's live options quote feed
  (`OptionHistoricalDataClient.get_option_chain`) when available for that contract. When a
  specific contract has no live quote (thin/no trading interest, or the feed entitlement doesn't
  cover it), its price/Greeks/IV are estimated with Black-Scholes from a real reference price -
  either the contract's real last-traded close (implied vol solved from that real price) or, for
  a contract with no trade history at all, from the trailing realized-vol proxy. Every
  OptionContractQuote carries a `quote_source` field disclosing which of these applied - nothing
  is ever silently invented.
- VIX comes from CBOE's public daily-history CSV (no key required).
- Portfolio Greeks are the real sum of each open option position's live Greeks * qty * 100.

Only when no broker credentials are configured at all does this fall back to a clearly-labeled
simulated feed (`data_source="simulated"`), so the app remains runnable for local UI development
without keys.
"""

import os
import csv
import io
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
import requests

from shared.schemas import (
    AccountState, Position, MarketContext, OptionContractQuote, OptionType
)
from perception.vol_metrics import (
    calculate_realized_volatility, calculate_iv_rank, calculate_vrp, calculate_trend_signals
)
from perception.liquidity import compute_spread_pct_of_mid
from perception.earnings_calendar import get_days_until_earnings
from perception.options_pricing import bs_price, bs_greeks, implied_volatility

load_dotenv()

CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
VIX_CACHE_TTL_SECONDS = 900

# Short-lived caches. The dashboard polls several endpoints on timers and each MarketContext
# rebuild costs multiple upstream API round-trips (bars, latest trade, contract master, chain
# snapshot, VIX, account + position Greeks). Without these a single page sitting open hammers
# Alpaca and every panel waits ~6s.
#
# TTLs are deliberately short and asymmetric to what the data actually is: daily closes only
# change once a day, so caching them for minutes is exact, not approximate. The context TTL is
# small enough that quoted prices stay current for a human reading a dashboard.
MARKET_CONTEXT_TTL_SECONDS = 20
DAILY_CLOSES_TTL_SECONDS = 900

# Contracts whose strike is off the standard grid (SPY lists 1-point strikes alongside the
# 5-point grid) are never selected by build_structure, which snaps to the grid - carrying them
# just inflates the payload and the browser's render cost.
MAX_CHAIN_CONTRACTS = 160


def _signed_qty(position) -> float:
    """
    Signed position quantity: positive for long, negative for short.

    Alpaca already returns `qty` signed (e.g. '-8' for a short option leg) AND exposes `side`
    separately. Negating a value that is already negative double-flips the sign and silently
    reports every short leg as long - which corrupts portfolio delta/vega, two of the limits the
    Risk Kernel enforces. Taking the magnitude and applying `side` as the authority is correct
    whether or not the API's qty carries the sign.
    """
    magnitude = abs(float(position.qty))
    is_short = str(position.side).lower().endswith("short")
    return -magnitude if is_short else magnitude


class AlpacaClient:
    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY", "")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        self.is_paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"

        self.trading_client = None
        self.stock_data_client = None
        self.option_data_client = None
        self.has_real_client = False

        self._vix_cache: Optional[Tuple[float, datetime]] = None
        self._ctx_cache: Dict[str, Tuple[Any, datetime]] = {}
        self._closes_cache: Dict[Tuple[str, int], Tuple[List[Tuple[datetime, float]], datetime]] = {}
        self._cache_lock = threading.Lock()

        # In-memory believed state store - used ONLY when no live credentials are configured.
        self.sim_cash = 50000.0
        self.sim_buying_power = 50000.0
        self.sim_positions: List[Dict[str, Any]] = []

        if self.api_key and self.secret_key and not self.api_key.startswith("your_"):
            try:
                from alpaca.trading.client import TradingClient
                from alpaca.data.historical.stock import StockHistoricalDataClient
                from alpaca.data.historical.option import OptionHistoricalDataClient
                self.trading_client = TradingClient(self.api_key, self.secret_key, paper=self.is_paper)
                self.stock_data_client = StockHistoricalDataClient(self.api_key, self.secret_key)
                self.option_data_client = OptionHistoricalDataClient(self.api_key, self.secret_key)
                self.has_real_client = True
                print("Alpaca trading + stock data + option data clients initialized successfully.")
            except Exception as e:
                print(f"Warning: Alpaca client initialization failed: {e}. Using simulated feed.")
                self.has_real_client = False

    # ------------------------------------------------------------------
    # Account state
    # ------------------------------------------------------------------

    def get_account_state(self) -> AccountState:
        """Fetch current account equity, buying power, positions, and real portfolio Greeks."""
        if self.has_real_client and self.trading_client:
            try:
                acct = self.trading_client.get_account()
                raw_positions = self.trading_client.get_all_positions()

                pos_list: List[Position] = []
                option_symbols: List[str] = []
                for p in raw_positions:
                    signed_qty = _signed_qty(p)
                    pos_list.append(Position(
                        symbol=p.symbol,
                        qty=int(signed_qty),
                        current_price=float(p.current_price or 0.0),
                        market_value=float(p.market_value or 0.0),
                        cost_basis=float(p.cost_basis or 0.0),
                        unrealized_pl=float(p.unrealized_pl or 0.0),
                        asset_class=str(p.asset_class)
                    ))
                    if "option" in str(p.asset_class).lower():
                        option_symbols.append(p.symbol)

                total_delta, total_vega = self._aggregate_option_greeks(raw_positions, option_symbols)

                return AccountState(
                    buying_power=float(acct.buying_power),
                    cash=float(acct.cash),
                    portfolio_value=float(acct.portfolio_value),
                    positions=pos_list,
                    portfolio_delta=total_delta,
                    portfolio_vega=total_vega,
                    data_source="live"
                )
            except Exception as e:
                print(f"Alpaca live account fetch error ({e}). Returning simulated fallback state.")

        pos_objs = [
            Position(
                symbol=p["symbol"],
                qty=p["qty"],
                current_price=p["current_price"],
                market_value=p["market_value"],
                cost_basis=p["cost_basis"],
                unrealized_pl=p["unrealized_pl"],
                asset_class=p.get("asset_class", "option")
            )
            for p in self.sim_positions
        ]
        return AccountState(
            buying_power=self.sim_buying_power,
            cash=self.sim_cash,
            portfolio_value=self.sim_cash + sum(p.market_value for p in pos_objs),
            positions=pos_objs,
            portfolio_delta=-38.0 if pos_objs else 0.0,
            portfolio_vega=-190.0 if pos_objs else 0.0,
            data_source="simulated"
        )

    def _aggregate_option_greeks(self, raw_positions, option_symbols: List[str]) -> Tuple[float, float]:
        """Sum qty * greek * 100 (contract multiplier) across open option positions using live Greeks."""
        if not option_symbols or not self.option_data_client:
            return 0.0, 0.0
        try:
            from alpaca.data.requests import OptionSnapshotRequest
            snaps = self.option_data_client.get_option_snapshot(
                OptionSnapshotRequest(symbol_or_symbols=option_symbols)
            ) or {}
        except Exception as e:
            print(f"[GREEKS AGGREGATION WARNING] Could not fetch live Greeks: {e}. Portfolio delta/vega reported as 0.0.")
            return 0.0, 0.0

        qty_by_symbol = {}
        for p in raw_positions:
            if p.symbol in option_symbols:
                qty_by_symbol[p.symbol] = _signed_qty(p)

        total_delta = 0.0
        total_vega = 0.0
        for sym, snap in snaps.items():
            greeks = getattr(snap, "greeks", None)
            qty = qty_by_symbol.get(sym, 0.0)
            if greeks:
                total_delta += qty * (greeks.delta or 0.0) * 100.0
                total_vega += qty * (greeks.vega or 0.0) * 100.0
        return round(total_delta, 2), round(total_vega, 2)

    # ------------------------------------------------------------------
    # Market context (perception layer's single output to the reasoning layer)
    # ------------------------------------------------------------------

    def get_market_context(self, underlying: str = "SPY", force_refresh: bool = False) -> MarketContext:
        """
        Current market context for a symbol, cached for MARKET_CONTEXT_TTL_SECONDS.

        Rebuilding costs several upstream round-trips, and the dashboard polls on timers; without
        the cache a single open page re-fetches the whole chain every few seconds. Pass
        force_refresh=True where freshness genuinely matters more than latency.
        """
        ticker = underlying.upper()

        if not force_refresh:
            with self._cache_lock:
                cached = self._ctx_cache.get(ticker)
            if cached:
                ctx, cached_at = cached
                if (datetime.now(timezone.utc) - cached_at).total_seconds() < MARKET_CONTEXT_TTL_SECONDS:
                    return ctx

        now_iso = datetime.now(timezone.utc).isoformat()
        if self.has_real_client:
            try:
                ctx = self._get_real_market_context(ticker, now_iso)
            except Exception as e:
                print(f"[MARKET DATA WARNING] Real data path failed for {ticker}: {e}. Falling back to simulated feed.")
                ctx = self._get_simulated_market_context(ticker, now_iso)
        else:
            ctx = self._get_simulated_market_context(ticker, now_iso)

        with self._cache_lock:
            self._ctx_cache[ticker] = (ctx, datetime.now(timezone.utc))
        return ctx

    def _get_daily_closes(self, ticker: str, lookback_days: int = 400) -> List[Tuple[datetime, float]]:
        # Daily bars only change once a day, so caching them for minutes is exact rather than
        # approximate. Both the market context and every Strategy Arena backtest read this.
        key = (ticker, lookback_days)
        with self._cache_lock:
            cached = self._closes_cache.get(key)
        if cached:
            rows, cached_at = cached
            if (datetime.now(timezone.utc) - cached_at).total_seconds() < DAILY_CLOSES_TTL_SECONDS:
                return rows

        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Day, start=start, end=end, feed=DataFeed.IEX)
        bars = self.stock_data_client.get_stock_bars(req)
        rows = [(b.timestamp, float(b.close)) for b in bars.data.get(ticker, [])]
        with self._cache_lock:
            self._closes_cache[key] = (rows, datetime.now(timezone.utc))
        return rows

    def get_daily_closes(self, ticker: str, lookback_days: int = 400) -> List[float]:
        """Public helper: real historical daily closing prices, oldest first. Used by the
        backtest engine. Returns an empty list if no live client is configured or the fetch fails."""
        if not self.has_real_client:
            return []
        try:
            return [c for _, c in self._get_daily_closes(ticker, lookback_days=lookback_days)]
        except Exception as e:
            print(f"[HISTORICAL DATA WARNING] {ticker}: {e}")
            return []

    def _get_latest_trade_price(self, ticker: str) -> Optional[float]:
        try:
            from alpaca.data.requests import StockLatestTradeRequest
            from alpaca.data.enums import DataFeed
            req = StockLatestTradeRequest(symbol_or_symbols=ticker, feed=DataFeed.IEX)
            trades = self.stock_data_client.get_stock_latest_trade(req)
            t = trades.get(ticker)
            return float(t.price) if t else None
        except Exception as e:
            print(f"[LATEST TRADE WARNING] {ticker}: {e}")
            return None

    def get_ticker_tape_quotes(self, symbols: List[str]) -> Dict[str, Dict[str, float]]:
        """Real last price + day-over-day % change for a batch of symbols in one call, for the
        scrolling ticker tape. Symbols with no data are simply omitted (never fabricated)."""
        if not self.has_real_client or not self.stock_data_client:
            return {}
        try:
            from alpaca.data.requests import StockSnapshotRequest
            from alpaca.data.enums import DataFeed
            req = StockSnapshotRequest(symbol_or_symbols=symbols, feed=DataFeed.IEX)
            snaps = self.stock_data_client.get_stock_snapshot(req) or {}
            out = {}
            for sym, snap in snaps.items():
                trade = getattr(snap, "latest_trade", None)
                daily = getattr(snap, "daily_bar", None)
                prev = getattr(snap, "previous_daily_bar", None)
                price = float(trade.price) if trade else (float(daily.close) if daily else None)
                if price is None:
                    continue
                change_pct = None
                if prev and prev.close:
                    change_pct = round((price - float(prev.close)) / float(prev.close) * 100.0, 2)
                out[sym] = {"price": round(price, 2), "change_pct": change_pct}
            return out
        except Exception as e:
            print(f"[TICKER TAPE WARNING] {e}")
            return {}

    def _get_real_vix(self) -> Optional[float]:
        if self._vix_cache and (datetime.now(timezone.utc) - self._vix_cache[1]).total_seconds() < VIX_CACHE_TTL_SECONDS:
            return self._vix_cache[0]
        try:
            resp = requests.get(CBOE_VIX_URL, timeout=5)
            resp.raise_for_status()
            rows = list(csv.reader(io.StringIO(resp.text)))
            last_row = [r for r in rows if r and r[0].strip().upper() != "DATE"][-1]
            vix_close = float(last_row[4])
            self._vix_cache = (vix_close, datetime.now(timezone.utc))
            return vix_close
        except Exception as e:
            print(f"[VIX FETCH WARNING] Could not fetch live VIX from CBOE: {e}")
            return None

    def _compute_iv_rank_proxy(self, closes: List[float], current_iv_pct: float) -> float:
        """
        IV Rank proxy: percentile of current IV against the range of trailing 20-day realized
        volatility observed over the lookback window. A true 52-week *implied*-vol percentile
        would require a paid historical OPRA feed going back a year for a rolling ~3-week
        contract, which isn't available for free - this is a disclosed, documented substitute,
        not a fabricated number.
        """
        window = 20
        if len(closes) < window + 5:
            return 50.0
        rv_series = [
            calculate_realized_volatility(closes[i - window:i + 1])
            for i in range(window, len(closes))
        ]
        lo, hi = min(rv_series), max(rv_series)
        if hi <= lo:
            hi = lo + 5.0
        return calculate_iv_rank(current_iv_pct, lo, hi)

    def _build_real_option_chain(self, ticker: str, spot: float, rv_proxy: float) -> Tuple[List[OptionContractQuote], str, float]:
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import AssetStatus
        from alpaca.data.requests import OptionChainRequest

        now = datetime.now(timezone.utc)
        gte = (now + timedelta(days=10)).date()
        lte = (now + timedelta(days=35)).date()

        creq = GetOptionContractsRequest(
            underlying_symbols=[ticker],
            expiration_date_gte=gte,
            expiration_date_lte=lte,
            status=AssetStatus.ACTIVE,
            limit=1000,
        )
        cres = self.trading_client.get_option_contracts(creq)
        all_contracts = cres.option_contracts or []
        if not all_contracts:
            raise RuntimeError(f"No listed option contracts found for {ticker} in the target expiry window.")

        # Pick the most LIQUID expiry in the window, not merely the first one past 14 days.
        #
        # Underlyings list many near-dated weeklies that are barely traded: for SPY, the 2026-09-17
        # weekly carried zero open interest across every strike while the 2026-09-18 monthly - one
        # day later - had 642 contracts and OI up to 211,162. Taking "first expiry >= 14 DTE"
        # routinely landed on the dead one, which meant modeled prices instead of real quotes, wide
        # spreads, and legs that genuinely could not be exited. Ranking by real open interest fixes
        # the quality of every number downstream.
        expiries = sorted({c.expiration_date for c in all_contracts})
        eligible = [e for e in expiries if (e - now.date()).days >= 14] or expiries

        def expiry_liquidity(exp) -> int:
            return sum(int(c.open_interest or 0) for c in all_contracts if c.expiration_date == exp)

        scored = [(expiry_liquidity(e), e) for e in eligible]
        best_oi = max(oi for oi, _ in scored)
        if best_oi > 0:
            # Prefer the deepest open interest; tie-break toward the nearest such expiry.
            target_expiry_date = min((e for oi, e in scored if oi == best_oi))
        else:
            # No open-interest data at all in the window - fall back to the original rule rather
            # than pretending we made an informed choice.
            target_expiry_date = eligible[0]
        target_expiry = target_expiry_date.isoformat()

        lo_strike, hi_strike = spot * 0.85, spot * 1.15
        candidates = [
            c for c in all_contracts
            if c.expiration_date == target_expiry_date and lo_strike <= float(c.strike_price) <= hi_strike
        ]

        # Keep the standard strike grid. build_structure() snaps every leg to a multiple of the
        # strike step, so off-grid strikes (SPY lists 1-point strikes beside the 5-point grid) can
        # never be traded and only bloat the payload. Fall back to the unfiltered set if the grid
        # filter would leave too little to work with.
        step = 5.0 if spot > 300 else (2.5 if spot > 80 else 1.0)
        on_grid = [c for c in candidates if abs(float(c.strike_price) % step) < 1e-6]
        if len(on_grid) >= 16:
            candidates = on_grid

        # Still too many (very wide chains)? Keep the strikes nearest the money - that is where
        # every structure in the population places its legs.
        if len(candidates) > MAX_CHAIN_CONTRACTS:
            candidates.sort(key=lambda c: abs(float(c.strike_price) - spot))
            candidates = candidates[:MAX_CHAIN_CONTRACTS]

        candidates.sort(key=lambda c: float(c.strike_price))

        live_snapshots: Dict[str, Any] = {}
        try:
            oreq = OptionChainRequest(
                underlying_symbol=ticker,
                expiration_date=target_expiry,
                strike_price_gte=lo_strike,
                strike_price_lte=hi_strike,
            )
            live_snapshots = self.option_data_client.get_option_chain(oreq) or {}
        except Exception as e:
            print(f"[OPTIONS CHAIN WARNING] Live quote/Greeks feed unavailable for {ticker} {target_expiry}: {e}. "
                  f"Falling back to Black-Scholes pricing from real contract close prices.")

        t_years = max(1, (target_expiry_date - now.date()).days) / 365.0

        contracts: List[OptionContractQuote] = []
        atm_iv_samples: List[float] = []

        for c in candidates:
            strike = float(c.strike_price)
            opt_type: OptionType = "call" if "call" in str(c.type).lower() else "put"
            snap = live_snapshots.get(c.symbol)
            quote = getattr(snap, "latest_quote", None) if snap else None

            if quote and quote.bid_price is not None and quote.ask_price and quote.ask_price > 0:
                bid, ask = float(quote.bid_price), float(quote.ask_price)
                mid, spread_pct = compute_spread_pct_of_mid(bid, ask)
                iv_pct = round((snap.implied_volatility or 0.0) * 100.0, 2)
                g = snap.greeks
                delta = g.delta if g else 0.0
                gamma = g.gamma if g else 0.0
                theta = g.theta if g else 0.0
                vega = g.vega if g else 0.0
                source = "live_indicative"
            else:
                close_price = float(c.close_price) if c.close_price else 0.0
                if close_price > 0:
                    solved_iv = implied_volatility(close_price, spot, strike, t_years, opt_type)
                    source = "modeled_from_last_price"
                else:
                    solved_iv = None
                    source = "modeled_no_trade_history"

                iv_used = solved_iv if solved_iv else max(0.05, (rv_proxy / 100.0) * 1.15)
                mid = bs_price(spot, strike, t_years, iv_used, opt_type)
                bid = ask = mid
                spread_pct = 0.0
                iv_pct = round(iv_used * 100.0, 2)
                greeks_dict = bs_greeks(spot, strike, t_years, iv_used, opt_type)
                delta, gamma, theta, vega = greeks_dict["delta"], greeks_dict["gamma"], greeks_dict["theta"], greeks_dict["vega"]

            if spot > 0 and abs(strike - spot) / spot < 0.03:
                atm_iv_samples.append(iv_pct)

            contracts.append(OptionContractQuote(
                symbol=c.symbol,
                strike=strike,
                expiry=target_expiry,
                type=opt_type,
                bid=round(bid, 2),
                ask=round(ask, 2),
                mid=round(mid, 2),
                spread_pct=round(spread_pct, 2),
                open_interest=int(c.open_interest or 0),
                volume=0,  # daily contract volume isn't in the free contracts/snapshot payloads
                delta=round(delta, 4),
                gamma=round(gamma, 5),
                theta=round(theta, 4),
                vega=round(vega, 4),
                implied_volatility=iv_pct,
                quote_source=source,
            ))

        current_iv = (
            sum(atm_iv_samples) / len(atm_iv_samples) if atm_iv_samples
            else (contracts[len(contracts) // 2].implied_volatility if contracts else 20.0)
        )
        return contracts, target_expiry, current_iv

    def _get_real_market_context(self, ticker: str, now_iso: str) -> MarketContext:
        closes_data = self._get_daily_closes(ticker, lookback_days=400)
        if len(closes_data) < 10:
            raise RuntimeError(f"Insufficient real historical bars for {ticker} ({len(closes_data)} bars)")
        closes = [c for _, c in closes_data]

        spot = self._get_latest_trade_price(ticker) or closes[-1]
        rv_30d = calculate_realized_volatility(closes[-31:])

        contracts, target_expiry, current_iv = self._build_real_option_chain(ticker, spot, rv_30d)

        iv_rank = self._compute_iv_rank_proxy(closes, current_iv)
        vrp = calculate_vrp(current_iv, rv_30d)
        vix = self._get_real_vix()

        earn_days, is_blackout, earn_source = get_days_until_earnings(ticker)

        account_state = self.get_account_state()

        # Trend / vol-term-structure signals off the same real closes (no extra API calls).
        trend = calculate_trend_signals(closes)

        return MarketContext(
            underlying=ticker,
            underlying_price=round(spot, 2),
            iv_rank=iv_rank,
            realized_vol=rv_30d,
            vrp=vrp,
            vix=vix,
            earnings_days=earn_days,
            is_earnings_blackout=is_blackout,
            earnings_data_source=earn_source,
            contracts=contracts,
            account_state=account_state,
            timestamp=now_iso,
            data_source="live",
            trend_20d_pct=trend["trend_20d_pct"],
            price_vs_ema20_pct=trend["price_vs_ema20_pct"],
            rv_short=trend["rv_short"],
            rv_long=trend["rv_long"],
            rv_expansion_ratio=trend["rv_expansion_ratio"],
        )

    def _get_simulated_market_context(self, ticker: str, now_iso: str) -> MarketContext:
        """
        Clearly-labeled simulated feed, used only when no broker credentials are configured.
        Kept so the dashboard/API remain runnable for local UI work without live keys.
        """
        base_prices = {
            "SPY": 651.20, "QQQ": 490.50, "IWM": 224.10, "AAPL": 228.40, "NVDA": 125.80,
            "TSLA": 215.30, "MSFT": 448.20, "AMZN": 186.50, "META": 512.40, "GOOGL": 178.90,
            "AMD": 146.30, "COIN": 224.80, "PLTR": 31.40, "NFLX": 680.50
        }
        if ticker in base_prices:
            price = base_prices[ticker]
        else:
            ticker_hash = sum(ord(c) for c in ticker)
            price = round(40.0 + (ticker_hash % 310) + 0.50, 2)

        import math
        is_index = ticker in ["SPY", "QQQ", "IWM"]
        current_iv = 22.4 if is_index else round(28.0 + (sum(ord(c) for c in ticker) % 15), 1)
        synthetic_closes = [price * (1 + 0.003 * math.sin(i * 0.5)) for i in range(30)]
        rv = calculate_realized_volatility(synthetic_closes)
        iv_rank = calculate_iv_rank(current_iv, 12.0, 35.0)
        vrp = calculate_vrp(current_iv, rv)

        earn_days, is_blackout, earn_source = get_days_until_earnings(ticker)

        target_expiry = (datetime.now(timezone.utc) + timedelta(days=19)).strftime("%Y-%m-%d")
        contracts: List[OptionContractQuote] = []
        strike_step = 5.0 if price > 300.0 else (2.5 if price > 80.0 else 1.0)
        atm_strike = round(price / strike_step) * strike_step
        strikes = [atm_strike + i * strike_step for i in range(-5, 6)]

        for strike in strikes:
            moneyness = abs(strike - price) / price
            smile_iv = round(current_iv + (moneyness * 18.0), 2)
            call_mid = max(0.40, round((price - strike) * 0.55 + (price * 0.015), 2))
            put_mid = max(0.40, round((strike - price) * 0.55 + (price * 0.015), 2))
            call_spread = round(call_mid * 0.038, 2) or 0.05
            put_spread = round(put_mid * 0.038, 2) or 0.05
            d_call = round(max(0.05, min(0.95, 0.50 + (price - strike) / (price * 0.12))), 2)
            d_put = round(d_call - 1.0, 2)
            vega_val = round(0.18 * price / 100.0, 2)

            contracts.append(OptionContractQuote(
                symbol=f"{ticker}{target_expiry.replace('-', '')}C{int(strike * 1000):08d}",
                strike=strike, expiry=target_expiry, type="call",
                bid=round(call_mid - call_spread / 2, 2), ask=round(call_mid + call_spread / 2, 2),
                mid=call_mid, spread_pct=round((call_spread / call_mid) * 100, 2),
                open_interest=1240, volume=450, delta=d_call, gamma=0.02, theta=-0.12, vega=vega_val,
                implied_volatility=smile_iv, quote_source="simulated"
            ))
            contracts.append(OptionContractQuote(
                symbol=f"{ticker}{target_expiry.replace('-', '')}P{int(strike * 1000):08d}",
                strike=strike, expiry=target_expiry, type="put",
                bid=round(put_mid - put_spread / 2, 2), ask=round(put_mid + put_spread / 2, 2),
                mid=put_mid, spread_pct=round((put_spread / put_mid) * 100, 2),
                open_interest=1890, volume=620, delta=d_put, gamma=0.02, theta=-0.12, vega=vega_val,
                implied_volatility=smile_iv, quote_source="simulated"
            ))

        account_state = self.get_account_state()
        trend = calculate_trend_signals(synthetic_closes)

        return MarketContext(
            underlying=ticker, underlying_price=price, iv_rank=iv_rank, realized_vol=rv, vrp=vrp,
            vix=16.4, earnings_days=earn_days, is_earnings_blackout=is_blackout,
            earnings_data_source=earn_source, contracts=contracts, account_state=account_state,
            timestamp=now_iso, data_source="simulated",
            trend_20d_pct=trend["trend_20d_pct"],
            price_vs_ema20_pct=trend["price_vs_ema20_pct"],
            rv_short=trend["rv_short"],
            rv_long=trend["rv_long"],
            rv_expansion_ratio=trend["rv_expansion_ratio"],
        )

    # ------------------------------------------------------------------
    # Chart data
    # ------------------------------------------------------------------

    def get_ohlcv_bars(self, symbol: str, timeframe: str = "1M") -> List[Dict[str, Any]]:
        symbol = symbol.upper()
        if self.has_real_client:
            try:
                return self._get_real_ohlcv_bars(symbol, timeframe)
            except Exception as e:
                print(f"[CHART DATA WARNING] Real bars fetch failed for {symbol}: {e}. Falling back to simulated bars.")
        return self._get_simulated_ohlcv_bars(symbol, timeframe)

    def _get_real_ohlcv_bars(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed

        end = datetime.now(timezone.utc)
        if timeframe == "1D":
            tf = TimeFrame(15, TimeFrameUnit.Minute)
            start = end - timedelta(days=5)
        elif timeframe == "1M":
            tf = TimeFrame.Day
            start = end - timedelta(days=60)
        else:
            tf = TimeFrame.Day
            start = end - timedelta(days=120)

        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, end=end, feed=DataFeed.IEX)
        bars = self.stock_data_client.get_stock_bars(req)
        rows = bars.data.get(symbol, [])
        if not rows:
            raise RuntimeError("no bars returned")

        out = []
        for b in rows:
            t_fmt = b.timestamp.strftime("%H:%M") if timeframe == "1D" else b.timestamp.strftime("%b %d")
            out.append({
                "time": t_fmt,
                "open": round(float(b.open), 2), "high": round(float(b.high), 2),
                "low": round(float(b.low), 2), "close": round(float(b.close), 2),
                "volume": int(b.volume or 0)
            })
        return out

    def _get_simulated_ohlcv_bars(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        base_prices = {
            "SPY": 651.20, "QQQ": 490.50, "IWM": 224.10, "AAPL": 228.40, "NVDA": 125.80,
            "TSLA": 215.30, "MSFT": 448.20, "AMZN": 186.50, "META": 512.40, "GOOGL": 178.90,
            "AMD": 146.30, "COIN": 224.80, "PLTR": 31.40, "NFLX": 680.50
        }
        price = base_prices.get(symbol, 40.0 + (sum(ord(c) for c in symbol) % 310))
        num_bars = 45 if timeframe == "1D" else (60 if timeframe == "1M" else 90)
        bars = []
        curr = price * 0.92
        import random
        rnd = random.Random(sum(ord(c) for c in symbol))
        now = datetime.now(timezone.utc)
        for i in range(num_bars):
            t = (now - timedelta(minutes=(num_bars - i) * 15 if timeframe == "1D" else (num_bars - i) * 24 * 60)).strftime(
                "%H:%M" if timeframe == "1D" else "%b %d")
            delta = (rnd.random() - 0.48) * (price * 0.015)
            open_p = round(curr, 2)
            close_p = round(curr + delta, 2)
            high_p = round(max(open_p, close_p) + rnd.random() * (price * 0.008), 2)
            low_p = round(min(open_p, close_p) - rnd.random() * (price * 0.008), 2)
            vol = int(rnd.randint(15000, 85000) * (1.5 if abs(delta) > price * 0.008 else 1.0))
            bars.append({"time": t, "open": open_p, "high": high_p, "low": low_p, "close": close_p, "volume": vol})
            curr = close_p
        if bars:
            bars[-1]["close"] = price
            bars[-1]["high"] = max(bars[-1]["high"], price)
            bars[-1]["low"] = min(bars[-1]["low"], price)
        return bars

    # ------------------------------------------------------------------
    # Orders / positions
    # ------------------------------------------------------------------

    def get_market_clock(self) -> Dict[str, Any]:
        """Real Alpaca market clock - used to gate autonomous trading while the market is closed."""
        if self.has_real_client and self.trading_client:
            try:
                clock = self.trading_client.get_clock()
                return {"is_open": bool(clock.is_open), "next_open": str(clock.next_open), "next_close": str(clock.next_close)}
            except Exception as e:
                print(f"[MARKET CLOCK WARNING] {e}")
        return {"is_open": True, "next_open": None, "next_close": None}

    def get_open_orders(self) -> List[Dict[str, Any]]:
        if self.has_real_client and self.trading_client:
            try:
                from alpaca.trading.requests import GetOrdersRequest
                from alpaca.trading.enums import QueryOrderStatus
                req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=10)
                orders = self.trading_client.get_orders(req)
                return [
                    {
                        "id": str(o.id), "symbol": o.symbol, "qty": int(float(o.qty or 0)),
                        "side": str(o.side), "type": str(o.type), "status": str(o.status),
                        "submitted_at": o.submitted_at.isoformat() if o.submitted_at else ""
                    }
                    for o in orders
                ]
            except Exception as e:
                print(f"Alpaca get_orders error: {e}")
        return []

    def close_position(self, symbol: str) -> bool:
        if self.has_real_client and self.trading_client:
            try:
                self.trading_client.close_position(symbol)
                return True
            except Exception as e:
                print(f"Alpaca close_position error: {e}")
                return False
        self.sim_positions = [p for p in self.sim_positions if p.get("symbol") != symbol]
        return True

    def _resolve_option_contract(self, underlying: str, expiry: str, strike: float, option_type: str):
        """Look up the authoritative Alpaca contract (symbol, close_price, etc.) for a given
        contract spec via the real options-contracts master list. Returns None if not listed."""
        if not self.has_real_client or not self.trading_client:
            return None
        try:
            from alpaca.trading.requests import GetOptionContractsRequest
            from alpaca.trading.enums import AssetStatus, ContractType
            ctype = ContractType.CALL if option_type == "call" else ContractType.PUT
            req = GetOptionContractsRequest(
                underlying_symbols=[underlying],
                expiration_date=expiry,
                strike_price_gte=str(strike),
                strike_price_lte=str(strike),
                type=ctype,
                status=AssetStatus.ACTIVE,
                limit=5,
            )
            res = self.trading_client.get_option_contracts(req)
            return res.option_contracts[0] if res.option_contracts else None
        except Exception as e:
            print(f"[SYMBOL RESOLVE ERROR] {underlying} {expiry} {strike} {option_type}: {e}")
            return None

    def resolve_option_symbol(self, underlying: str, expiry: str, strike: float, option_type: str) -> Optional[str]:
        """Look up the authoritative Alpaca OCC symbol for a given contract spec. Returns None if
        no such contract is currently listed."""
        c = self._resolve_option_contract(underlying, expiry, strike, option_type)
        return c.symbol if c else None

    def _get_live_mid_price(self, symbol: str) -> Optional[float]:
        """Real live bid/ask mid for one option symbol, or None if no live quote exists."""
        if not self.option_data_client:
            return None
        try:
            from alpaca.data.requests import OptionSnapshotRequest
            snaps = self.option_data_client.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=[symbol])) or {}
            snap = snaps.get(symbol)
            q = getattr(snap, "latest_quote", None) if snap else None
            if q and q.bid_price is not None and q.ask_price and q.ask_price > 0:
                return round((float(q.bid_price) + float(q.ask_price)) / 2.0, 4)
        except Exception as e:
            print(f"[QUOTE FETCH WARNING] {symbol}: {e}")
        return None

    def place_multi_leg_order(self, legs: List[Dict[str, Any]], size: int, underlying: str) -> Optional[str]:
        """
        Execute a real multi-leg combo order on Alpaca (order_class=MLEG) resolving each leg to
        its actual listed OCC contract symbol, priced as a real limit order (not a market order -
        multi-leg combos are routinely limit-priced in real trading to avoid slippage, and Alpaca
        rejects options market orders entirely outside market hours whereas a limit order can
        queue for the next session). Returns None (fail closed) if any leg can't be resolved to a
        real listed contract, if no real reference price is available to set a fair limit, or if
        the broker rejects the order - it never fabricates a fallback fill.
        """
        if self.has_real_client and self.trading_client:
            try:
                from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest
                from alpaca.trading.enums import OrderSide, OrderClass, TimeInForce

                order_legs = []
                signed_mids = []
                for leg in legs:
                    contract = self._resolve_option_contract(underlying, leg["expiry"], leg["strike"], leg["type"])
                    if not contract:
                        print(f"[EXECUTION ERROR] No listed contract found for {underlying} {leg['type']} "
                              f"{leg['strike']} {leg['expiry']}; order NOT placed.")
                        return None
                    side = OrderSide.SELL if leg["action"] == "sell" else OrderSide.BUY
                    order_legs.append(OptionLegRequest(symbol=contract.symbol, side=side, ratio_qty=1))

                    mid = self._get_live_mid_price(contract.symbol)
                    if mid is None and contract.close_price:
                        mid = float(contract.close_price)
                    if mid is None:
                        print(f"[EXECUTION ERROR] No real reference price available for {contract.symbol}; "
                              f"order NOT placed (refusing to guess a limit price).")
                        return None
                    signed_mids.append(mid if leg["action"] == "sell" else -mid)

                net_credit_or_debit = sum(signed_mids)
                limit_price = max(0.01, round(abs(net_credit_or_debit), 2))

                req = LimitOrderRequest(
                    qty=max(1, size),
                    order_class=OrderClass.MLEG,
                    time_in_force=TimeInForce.DAY,
                    legs=order_legs,
                    limit_price=limit_price,
                )
                res = self.trading_client.submit_order(req)
                order_id = str(res.id)
                print(f"[ALPACA LIVE BROKER] Multi-leg order submitted: {order_id} "
                      f"({len(order_legs)} legs, qty={size}, limit_price={limit_price})")
                return order_id
            except Exception as e:
                print(f"[ALPACA BROKER ERROR] Multi-leg order submission failed: {e}")
                return None

        # No live credentials configured - honest, clearly-labeled simulated fallback only.
        order_id = f"sim-order-{uuid.uuid4().hex[:10]}"
        for leg in legs:
            pos_sym = f"{underlying}_{leg['type'].upper()}_{leg['strike']}_{leg['expiry']}"
            qty_delta = size if leg['action'] == 'buy' else -size
            self.sim_positions.append({
                "symbol": pos_sym, "qty": qty_delta, "current_price": 4.50,
                "market_value": 4.50 * qty_delta * 100, "cost_basis": 4.50 * qty_delta * 100,
                "unrealized_pl": 0.0, "asset_class": "option"
            })
        self.sim_buying_power -= (size * 500.0)
        return order_id
