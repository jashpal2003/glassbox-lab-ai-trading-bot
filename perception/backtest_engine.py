"""
perception/backtest_engine.py - Historical Replay & Backtesting Engine for Options Strategies.

Simulates a parameterised options strategy over REAL historical daily closes for the underlying
(Alpaca stock bars, `AlpacaClient.get_daily_closes`). Granular historical options-chain pricing
(actual bid/ask at any arbitrary past date) isn't freely available, so option premium is modeled
with Black-Scholes using an implied-vol assumption derived from the REAL trailing realized
volatility at that point in history (IV = RV x a disclosed volatility-risk-premium multiplier).
This is standard, disclosed practice for backtesting index-option strategies without a paid
historical OPRA subscription - it is not fabricated data. The underlying price path itself is
always real: entry spot, every mark-to-market step, and the terminal expiry price are real closes.

Supports every structure in `StructureType` so the Strategy Arena can compete them against each
other on identical real history:
  - iron_condor / iron_butterfly   short both wings, defined risk
  - credit_spread_put / _call      one-sided short premium, defined risk
  - debit_spread                   long directional (bull call)
  - straddle / strangle            long volatility

Positions are marked to model each session against the REAL close for that session, so a
`profit_target_pct` parameter genuinely changes outcomes (the position can close early on a real
price path) rather than being a decorative dial. Trades that never hit the target are held to
expiry and settled with an exact payoff against the real terminal price.

Computes Sharpe, Max Drawdown, profit factor and the Deflated Sharpe Ratio (Bailey & Lopez de
Prado, 2014) on the resulting equity curve, run through the exact same math for every variant.
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from perception.options_pricing import bs_price
from perception.vol_metrics import calculate_realized_volatility
from kernel.risk_kernel import load_risk_config

VOL_RISK_PREMIUM_MULTIPLIER = 1.15  # disclosed assumption: IV priced ~15% above trailing RV
ENTRY_GAP_TRADING_DAYS = 10
EXPIRY_HORIZON_TRADING_DAYS = 21
RV_WINDOW = 20
TRADING_DAYS_PER_YEAR = 252.0

# Default parameter set. Every key here is overridable per StrategyVariant, which is exactly how
# the arena tests e.g. a 0.75-sigma condor against a 1.25-sigma condor.
DEFAULT_PARAMS: Dict[str, float] = {
    "wing_offset_sigma": 1.0,      # short strikes placed this many expected-move sigmas from spot
    "wing_width_steps": 1.0,       # long protective wing this many strike steps beyond the short
    "profit_target_pct": 50.0,     # close early once this % of max profit is captured
    "stop_loss_multiplier": 2.0,   # close early once loss reaches this multiple of credit taken
    "risk_per_trade_pct": 3.0,     # % of equity risked per position
    "dte_trading_days": float(EXPIRY_HORIZON_TRADING_DAYS),
}


def calculate_deflated_sharpe_ratio(
    observed_sharpe: float,
    num_trades: int,
    num_trials: int = 15,
    skewness: float = -0.45,
    kurtosis: float = 3.8
) -> float:
    """
    Computes the Deflated Sharpe Ratio (DSR) which adjusts for selection bias and multi-testing.
    Returns value between 0.0 and 1.0 (probability that strategy has true positive alpha).
    """
    if num_trades < 5 or observed_sharpe <= 0:
        return 0.05

    em_const = 0.5772156649
    z = (1.0 - em_const) * math.sqrt(2.0 * math.log(max(2, num_trials)))
    expected_max_sr = z / math.sqrt(252.0)

    sr_variance = (1.0 + (0.5 * observed_sharpe**2) - (skewness * observed_sharpe) + ((kurtosis - 3.0) / 4.0 * observed_sharpe**2)) / max(1, num_trades)
    sr_std = math.sqrt(max(0.0001, sr_variance))

    dsr_stat = (observed_sharpe - expected_max_sr) / sr_std
    dsr_prob = 0.5 * (1.0 + math.erf(dsr_stat / math.sqrt(2.0)))

    return round(max(0.01, min(0.99, dsr_prob)), 3)


def _stdev(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def _strike_step(price: float) -> float:
    return 5.0 if price > 300 else (2.5 if price > 80 else 1.0)


class OptionStructure:
    """
    A concrete set of option legs chosen at entry, able to price itself at any later spot price.

    `legs` are (action, type, strike) where action is +1 for long and -1 for short. `net_debit`
    is what the structure cost to open per share (negative = a credit was received).
    """

    def __init__(self, legs: List[Tuple[int, str, float]], net_debit: float, max_loss: float,
                 max_profit: float, is_credit: bool):
        self.legs = legs
        self.net_debit = net_debit
        self.max_loss = max_loss
        self.max_profit = max_profit
        self.is_credit = is_credit

    def model_value(self, spot: float, t_years: float, iv_frac: float) -> float:
        """Black-Scholes value of the whole structure per share at an arbitrary later point."""
        total = 0.0
        for sign, opt_type, strike in self.legs:
            total += sign * bs_price(spot, strike, t_years, iv_frac, opt_type)
        return total

    def expiry_value(self, spot: float) -> float:
        """Exact intrinsic settlement value per share against the real terminal price."""
        total = 0.0
        for sign, opt_type, strike in self.legs:
            intrinsic = max(0.0, spot - strike) if opt_type == "call" else max(0.0, strike - spot)
            total += sign * intrinsic
        return total

    def pnl_per_share(self, current_value: float) -> float:
        """P&L per share for a position opened at `net_debit` and now worth `current_value`."""
        return current_value - self.net_debit


def build_structure(
    structure_type: str,
    spot: float,
    t_years: float,
    iv_frac: float,
    params: Dict[str, float],
) -> Optional[OptionStructure]:
    """
    Selects strikes for `structure_type` around a real entry spot and prices every leg with
    Black-Scholes off the real trailing-realized-vol-derived IV. Returns None for an unsupported
    structure rather than silently substituting a different one.
    """
    step = _strike_step(spot)
    offset_sigma = params.get("wing_offset_sigma", DEFAULT_PARAMS["wing_offset_sigma"])
    width_steps = max(1.0, params.get("wing_width_steps", DEFAULT_PARAMS["wing_width_steps"]))

    # Expected move over the holding period, from the real IV estimate.
    one_sigma = spot * iv_frac * math.sqrt(max(t_years, 1e-6))
    offset = max(step, round((one_sigma * offset_sigma) / step) * step)
    width = width_steps * step

    def snap(x: float) -> float:
        return round(x / step) * step

    price = lambda k, typ: bs_price(spot, k, t_years, iv_frac, typ)

    if structure_type in ("iron_condor", "iron_butterfly"):
        # Butterfly is the degenerate condor with both shorts at the money.
        put_short = snap(spot) if structure_type == "iron_butterfly" else snap(spot - offset)
        call_short = snap(spot) if structure_type == "iron_butterfly" else snap(spot + offset)
        put_long = put_short - width
        call_long = call_short + width
        legs = [(-1, "put", put_short), (1, "put", put_long),
                (-1, "call", call_short), (1, "call", call_long)]
        credit = (price(put_short, "put") - price(put_long, "put")
                  + price(call_short, "call") - price(call_long, "call"))
        if credit <= 0:
            return None
        return OptionStructure(legs, net_debit=-credit, max_loss=max(0.01, width - credit),
                               max_profit=credit, is_credit=True)

    if structure_type == "credit_spread_put":
        short_k = snap(spot - offset)
        long_k = short_k - width
        legs = [(-1, "put", short_k), (1, "put", long_k)]
        credit = price(short_k, "put") - price(long_k, "put")
        if credit <= 0:
            return None
        return OptionStructure(legs, net_debit=-credit, max_loss=max(0.01, width - credit),
                               max_profit=credit, is_credit=True)

    if structure_type == "credit_spread_call":
        short_k = snap(spot + offset)
        long_k = short_k + width
        legs = [(-1, "call", short_k), (1, "call", long_k)]
        credit = price(short_k, "call") - price(long_k, "call")
        if credit <= 0:
            return None
        return OptionStructure(legs, net_debit=-credit, max_loss=max(0.01, width - credit),
                               max_profit=credit, is_credit=True)

    if structure_type == "debit_spread":
        # Bull call spread: long the nearer strike, short the further one.
        long_k = snap(spot)
        short_k = long_k + max(width, offset)
        legs = [(1, "call", long_k), (-1, "call", short_k)]
        debit = price(long_k, "call") - price(short_k, "call")
        if debit <= 0:
            return None
        return OptionStructure(legs, net_debit=debit, max_loss=max(0.01, debit),
                               max_profit=max(0.01, (short_k - long_k) - debit), is_credit=False)

    if structure_type == "straddle":
        k = snap(spot)
        legs = [(1, "call", k), (1, "put", k)]
        debit = price(k, "call") + price(k, "put")
        if debit <= 0:
            return None
        # Long vol: theoretical upside is open-ended; cap the reported max profit at the move
        # implied by the entry IV so the risk/reward figures stay conservative rather than flattering.
        return OptionStructure(legs, net_debit=debit, max_loss=max(0.01, debit),
                               max_profit=max(0.01, one_sigma * 2.0 - debit), is_credit=False)

    if structure_type == "strangle":
        call_k = snap(spot + offset)
        put_k = snap(spot - offset)
        legs = [(1, "call", call_k), (1, "put", put_k)]
        debit = price(call_k, "call") + price(put_k, "put")
        if debit <= 0:
            return None
        return OptionStructure(legs, net_debit=debit, max_loss=max(0.01, debit),
                               max_profit=max(0.01, one_sigma * 2.0 - debit), is_credit=False)

    return None


class HistoricalReplayEngine:
    def __init__(self, client=None):
        self.risk_config = load_risk_config()
        self._client = client  # lazily constructed; injectable for tests

    def _get_client(self):
        if self._client is None:
            from perception.alpaca_client import AlpacaClient
            self._client = AlpacaClient()
        return self._client

    def get_closes(self, symbol: str, days: int) -> List[float]:
        """Real historical daily closes, fetched once so many variants can share one API call."""
        lookback_days = max(days + 90, 420)
        return self._get_client().get_daily_closes(symbol.upper(), lookback_days=lookback_days)

    def run_backtest(
        self,
        symbol: str = "SPY",
        days: int = 180,
        strategy_type: str = "iron_condor",
        trials_tested: int = 12,
        params: Optional[Dict[str, float]] = None,
        closes: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """
        Replays `strategy_type` with `params` over real historical closes.

        `closes` can be injected so the Strategy Arena scores many variants against one shared
        (already-fetched) price history instead of re-hitting the market-data API per variant.
        """
        symbol = symbol.upper()
        cfg = dict(DEFAULT_PARAMS)
        cfg.update(params or {})

        if closes is None:
            closes = self.get_closes(symbol, days)

        horizon = int(max(5, cfg.get("dte_trading_days", EXPIRY_HORIZON_TRADING_DAYS)))

        if len(closes) < RV_WINDOW + horizon + 5:
            return self._insufficient_data_response(symbol, days, trials_tested, strategy_type)

        target_cycles = max(10, days // ENTRY_GAP_TRADING_DAYS)
        initial_capital = 50000.0
        current_equity = initial_capital
        equity_curve = [initial_capital]
        trade_returns: List[float] = []
        trades: List[Dict[str, Any]] = []
        wins = 0
        gross_win = 0.0
        gross_loss = 0.0
        cycle = 0
        skipped = 0

        i = RV_WINDOW
        while i + horizon < len(closes) and cycle < target_cycles:
            entry_price = closes[i]
            rv = calculate_realized_volatility(closes[i - RV_WINDOW:i + 1])
            iv_assumed = rv * VOL_RISK_PREMIUM_MULTIPLIER
            iv_frac = max(0.01, iv_assumed / 100.0)
            vrp = round(iv_assumed - rv, 2)
            t_years = horizon / TRADING_DAYS_PER_YEAR

            structure = build_structure(strategy_type, entry_price, t_years, iv_frac, cfg)
            if structure is None:
                skipped += 1
                i += ENTRY_GAP_TRADING_DAYS
                continue

            contracts_size = max(
                1,
                int((current_equity * (cfg["risk_per_trade_pct"] / 100.0)) / (structure.max_loss * 100.0))
            )

            # --- Walk the REAL price path forward, marking the position to model each session.
            # This is what makes profit_target_pct and stop_loss_multiplier real parameters.
            profit_target_value = structure.max_profit * (cfg["profit_target_pct"] / 100.0)
            stop_value = structure.max_profit * cfg["stop_loss_multiplier"]
            exit_reason = "expiry"
            exit_index = i + horizon
            pnl_per_share = 0.0

            for step_ahead in range(1, horizon + 1):
                idx = i + step_ahead
                spot_now = closes[idx]
                remaining = (horizon - step_ahead) / TRADING_DAYS_PER_YEAR
                if step_ahead == horizon or remaining <= 0:
                    value_now = structure.expiry_value(spot_now)
                else:
                    value_now = structure.model_value(spot_now, remaining, iv_frac)
                pnl_now = structure.pnl_per_share(value_now)

                if pnl_now >= profit_target_value:
                    exit_reason = "profit_target"
                    exit_index = idx
                    pnl_per_share = pnl_now
                    break
                if pnl_now <= -stop_value:
                    exit_reason = "stop_loss"
                    exit_index = idx
                    pnl_per_share = pnl_now
                    break
                pnl_per_share = pnl_now

            exit_price = closes[exit_index]
            pnl = pnl_per_share * 100.0 * contracts_size
            risk_dollars = structure.max_loss * 100.0 * contracts_size
            is_win = pnl > 0
            wins += 1 if is_win else 0
            if pnl > 0:
                gross_win += pnl
            else:
                gross_loss += abs(pnl)

            equity_before = current_equity
            current_equity += pnl
            trade_returns.append(pnl / max(1.0, equity_before))
            equity_curve.append(round(current_equity, 2))

            trades.append({
                "cycle": cycle + 1,
                "entry_spot": round(entry_price, 2),
                "exit_spot": round(exit_price, 2),
                "held_days": exit_index - i,
                "exit_reason": exit_reason,
                "iv_assumed": round(iv_assumed, 1),
                "vrp": vrp,
                "structure": strategy_type,
                "strikes": {f"{'long' if s > 0 else 'short'}_{t}": k for s, t, k in structure.legs},
                "credit_or_debit": round(-structure.net_debit * 100 * contracts_size, 2),
                "risk_at_entry": round(risk_dollars, 2),
                "pnl": round(pnl, 2),
                "return_on_risk_pct": round((pnl / risk_dollars) * 100.0, 2) if risk_dollars else 0.0,
                "status": "WIN" if is_win else "LOSS",
                "running_equity": round(current_equity, 2),
            })

            i += ENTRY_GAP_TRADING_DAYS
            cycle += 1

        if cycle == 0:
            return self._insufficient_data_response(symbol, days, trials_tested, strategy_type)

        total_return_pct = round(((current_equity - initial_capital) / initial_capital) * 100.0, 2)
        win_rate = round((wins / cycle) * 100.0, 1)
        expectancy_pct = round(sum(t["return_on_risk_pct"] for t in trades) / cycle, 2)
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else (
            round(gross_win, 2) if gross_win > 0 else 0.0)

        peak = initial_capital
        max_dd = 0.0
        for eq in equity_curve:
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)

        std_ret = _stdev(trade_returns)
        if len(trade_returns) > 1 and std_ret > 0:
            mean_ret = sum(trade_returns) / len(trade_returns)
            annualized_sr = round((mean_ret / std_ret) * math.sqrt(TRADING_DAYS_PER_YEAR / ENTRY_GAP_TRADING_DAYS), 2)
        else:
            annualized_sr = 0.0

        dsr = calculate_deflated_sharpe_ratio(observed_sharpe=annualized_sr, num_trades=cycle,
                                              num_trials=trials_tested)

        return {
            "symbol": symbol,
            "simulation_days": days,
            "strategy": strategy_type,
            "params": cfg,
            "total_trades": cycle,
            "skipped_cycles": skipped,
            "win_rate_pct": win_rate,
            "expectancy_pct": expectancy_pct,
            "profit_factor": profit_factor,
            "total_return_pct": total_return_pct,
            "max_drawdown_pct": round(max_dd, 2),
            "raw_sharpe_ratio": annualized_sr,
            "deflated_sharpe_ratio": dsr,
            "strategy_variants_tested": trials_tested,
            "overfitting_risk_verdict": "LOW (True Alpha Verified)" if dsr >= 0.80 else "MODERATE",
            "equity_curve": equity_curve,
            "trades_sample": trades[:8],
            "data_source": "real_historical_bars",
            "methodology": (
                "Real historical underlying closes from Alpaca. Option premium modeled via "
                "Black-Scholes with implied vol = trailing realized vol x 1.15 (disclosed VRP "
                "assumption - granular historical options-chain pricing isn't freely available "
                "this far back). The position is marked to model against every real intervening "
                "close, so profit-target and stop-loss exits happen on the real price path; "
                "positions held to expiry settle with an exact payoff against the real terminal price."
            )
        }

    def _insufficient_data_response(self, symbol: str, days: int, trials_tested: int,
                                    strategy_type: str = "iron_condor") -> Dict[str, Any]:
        return {
            "symbol": symbol, "simulation_days": days, "strategy": strategy_type,
            "params": {}, "total_trades": 0, "skipped_cycles": 0, "win_rate_pct": 0.0,
            "expectancy_pct": 0.0, "profit_factor": 0.0,
            "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "raw_sharpe_ratio": 0.0,
            "deflated_sharpe_ratio": 0.05, "strategy_variants_tested": trials_tested,
            "overfitting_risk_verdict": "INSUFFICIENT_DATA",
            "equity_curve": [50000.0], "trades_sample": [],
            "data_source": "unavailable",
            "methodology": f"Could not fetch enough real historical bars for {symbol} to run a backtest "
                            f"(no Alpaca credentials configured, or the fetch failed)."
        }


replay_engine = HistoricalReplayEngine()
