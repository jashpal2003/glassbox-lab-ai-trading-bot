"""
tests/test_arena.py - Unit tests for the Regime Engine, parameterised backtest structures,
and the Strategy Arena tournament.

These run entirely offline against synthetic price paths, so they test the LOGIC (eligibility
gating, scoring, fail-closed behaviour) rather than any particular market outcome.
"""

import math

import pytest

from shared.schemas import AccountState, MarketContext, OptionContractQuote
from reasoning.regime_engine import (
    classify_regime, classify_regime_detailed, VIX_CEILING, IV_RANK_HIGH,
)
from reasoning.strategy_arena import StrategyArena, compute_composite_score, STRATEGY_REGISTRY
from perception.backtest_engine import build_structure, HistoricalReplayEngine
from perception.vol_metrics import calculate_trend_signals


def make_ctx(**overrides) -> MarketContext:
    """Minimal MarketContext with sane, explicitly-set defaults for regime testing."""
    base = dict(
        underlying="TEST",
        underlying_price=100.0,
        iv_rank=60.0,
        realized_vol=15.0,
        vrp=3.0,
        vix=16.0,
        earnings_days=30,
        is_earnings_blackout=False,
        contracts=[],
        account_state=AccountState(buying_power=100000.0, cash=100000.0, portfolio_value=100000.0),
        trend_20d_pct=0.5,
        price_vs_ema20_pct=0.2,
        rv_short=15.0,
        rv_long=15.0,
        rv_expansion_ratio=1.0,
    )
    base.update(overrides)
    return MarketContext(**base)


# --- Regime engine -------------------------------------------------------------------

def test_earnings_blackout_forces_event_risk():
    regime = classify_regime_detailed(make_ctx(is_earnings_blackout=True))
    assert regime.regime == "EVENT_RISK"
    assert "stand_down" in regime.tags


def test_missing_vix_fails_closed_to_event_risk():
    """A None VIX must never be treated as a safe environment - it is unverifiable, not benign."""
    regime = classify_regime_detailed(make_ctx(vix=None))
    assert regime.regime == "EVENT_RISK"
    assert regime.data_complete is False
    assert "fail_closed" in regime.tags


def test_vix_ceiling_breach_is_event_risk():
    regime = classify_regime_detailed(make_ctx(vix=VIX_CEILING + 0.1))
    assert regime.regime == "EVENT_RISK"


def test_high_iv_no_trend_is_range_regime():
    regime = classify_regime_detailed(make_ctx(iv_rank=75.0, vrp=5.0, trend_20d_pct=0.3,
                                               price_vs_ema20_pct=0.1))
    assert regime.regime == "HIGH_VOL_RANGE"
    assert "sell_premium" in regime.tags


def test_high_iv_with_strong_trend_is_trend_regime():
    regime = classify_regime_detailed(make_ctx(iv_rank=75.0, vrp=5.0, trend_20d_pct=9.0,
                                               price_vs_ema20_pct=4.0))
    assert regime.regime == "HIGH_VOL_TREND"
    assert "trend_up" in regime.tags


def test_low_iv_with_trend_is_debit_regime():
    regime = classify_regime_detailed(make_ctx(iv_rank=12.0, vrp=-1.0, trend_20d_pct=8.0,
                                               price_vs_ema20_pct=3.5))
    assert regime.regime == "LOW_VOL_TREND"


def test_vol_expansion_detected_when_short_rv_outruns_long_rv():
    regime = classify_regime_detailed(make_ctx(rv_short=40.0, rv_long=20.0,
                                               rv_expansion_ratio=2.0, vrp=-3.0))
    assert regime.regime == "VOL_EXPANSION"


def test_confidence_drops_when_trend_data_missing():
    complete = classify_regime_detailed(make_ctx(iv_rank=75.0, vrp=5.0))
    missing = classify_regime_detailed(make_ctx(iv_rank=75.0, vrp=5.0, trend_20d_pct=None,
                                                price_vs_ema20_pct=None,
                                                rv_expansion_ratio=None))
    assert missing.confidence_pct < complete.confidence_pct
    assert missing.data_complete is False


def test_legacy_classify_regime_shim_still_returns_tuple():
    regime, tags = classify_regime(make_ctx())
    assert isinstance(regime, str) and isinstance(tags, list)


# --- Trend signals -------------------------------------------------------------------

def test_trend_signals_none_on_short_history():
    signals = calculate_trend_signals([100.0, 101.0, 102.0])
    assert signals["trend_20d_pct"] is None
    assert signals["rv_long"] is None


def test_trend_signals_detect_a_real_uptrend():
    # Drift plus oscillation - a perfectly smooth curve has literally zero variance, which
    # correctly yields no expansion ratio (see the next test).
    closes = [100.0 * (1.004 ** i) * (1 + 0.01 * math.sin(i / 3.0)) for i in range(80)]
    signals = calculate_trend_signals(closes)
    assert signals["trend_20d_pct"] > 5.0
    assert signals["price_vs_ema20_pct"] > 0
    assert signals["rv_expansion_ratio"] is not None


def test_zero_variance_series_reports_no_expansion_ratio_rather_than_dividing_by_zero():
    closes = [100.0 * (1.004 ** i) for i in range(80)]   # constant log returns -> zero vol
    signals = calculate_trend_signals(closes)
    assert signals["rv_long"] == 0.0
    assert signals["rv_expansion_ratio"] is None


# --- Structure construction ----------------------------------------------------------

@pytest.mark.parametrize("structure,expected_legs", [
    ("iron_condor", 4),
    ("iron_butterfly", 4),
    ("credit_spread_put", 2),
    ("credit_spread_call", 2),
    ("debit_spread", 2),
    ("straddle", 2),
    ("strangle", 2),
])
def test_every_structure_builds_with_correct_leg_count(structure, expected_legs):
    s = build_structure(structure, spot=100.0, t_years=21 / 252, iv_frac=0.25,
                        params={"wing_offset_sigma": 1.0, "wing_width_steps": 1.0})
    assert s is not None, f"{structure} failed to build"
    assert len(s.legs) == expected_legs
    assert s.max_loss > 0


def test_credit_structures_open_for_a_credit_and_debits_for_a_debit():
    condor = build_structure("iron_condor", 100.0, 21 / 252, 0.25, {"wing_offset_sigma": 1.0})
    debit = build_structure("debit_spread", 100.0, 21 / 252, 0.25, {"wing_offset_sigma": 1.0})
    assert condor.is_credit and condor.net_debit < 0
    assert (not debit.is_credit) and debit.net_debit > 0


def test_structure_expiry_value_matches_hand_computed_payoff():
    """A 95/105 strangle at a terminal price of 120 is worth exactly its 15 points of call intrinsic."""
    s = build_structure("strangle", 100.0, 21 / 252, 0.25, {"wing_offset_sigma": 1.0})
    call_strike = next(k for sign, t, k in s.legs if t == "call")
    put_strike = next(k for sign, t, k in s.legs if t == "put")
    terminal = call_strike + 15.0
    expected = 15.0 + max(0.0, put_strike - terminal)
    assert s.expiry_value(terminal) == pytest.approx(expected, abs=1e-6)


def test_unknown_structure_returns_none_rather_than_a_substitute():
    assert build_structure("not_a_real_structure", 100.0, 0.1, 0.2, {}) is None


# --- Backtest parameterisation -------------------------------------------------------

def _synthetic_closes(n=400, drift=0.0002, amp=0.02):
    return [100.0 * (1 + drift) ** i * (1 + amp * math.sin(i / 7.0)) for i in range(n)]


def test_profit_target_parameter_actually_changes_outcomes():
    """
    A profit target is only a real parameter if it changes results. Two identical condors that
    differ ONLY in profit target must not produce byte-identical trade logs.
    """
    engine = HistoricalReplayEngine()
    closes = _synthetic_closes()
    tight = engine.run_backtest("TEST", 180, "iron_condor",
                                params={"profit_target_pct": 25.0}, closes=closes)
    loose = engine.run_backtest("TEST", 180, "iron_condor",
                                params={"profit_target_pct": 95.0}, closes=closes)
    assert tight["total_trades"] > 0 and loose["total_trades"] > 0
    tight_days = [t["held_days"] for t in tight["trades_sample"]]
    loose_days = [t["held_days"] for t in loose["trades_sample"]]
    assert tight_days != loose_days, "profit target had no effect on holding period"


def test_backtest_reports_unavailable_rather_than_inventing_data():
    engine = HistoricalReplayEngine()
    res = engine.run_backtest("TEST", 180, "iron_condor", closes=[100.0, 101.0])
    assert res["total_trades"] == 0
    assert res["data_source"] == "unavailable"


# --- Arena tournament ----------------------------------------------------------------

class _StubEngine:
    """Deterministic stand-in for the backtest engine so arena logic is tested in isolation."""

    def __init__(self, per_structure):
        self.per_structure = per_structure

    def get_closes(self, symbol, days):
        return [100.0] * 500

    def run_backtest(self, symbol, days, strategy_type, trials_tested=12, params=None, closes=None):
        return dict(self.per_structure.get(strategy_type, {"total_trades": 0}))


def _result(trades=30, win=70.0, exp=6.0, dd=5.0, sharpe=1.5, dsr=0.9):
    return {"total_trades": trades, "win_rate_pct": win, "expectancy_pct": exp,
            "max_drawdown_pct": dd, "raw_sharpe_ratio": sharpe, "deflated_sharpe_ratio": dsr,
            "total_return_pct": 12.0, "profit_factor": 2.0, "equity_curve": [50000.0, 51000.0]}


def test_zero_trade_variant_scores_zero_not_a_placeholder():
    score, breakdown = compute_composite_score({"total_trades": 0})
    assert score == 0.0
    assert breakdown == {}


def test_thin_sample_is_damped_below_an_identical_large_sample():
    big, _ = compute_composite_score(_result(trades=40))
    small, _ = compute_composite_score(_result(trades=2))
    assert small < big


def test_better_metrics_score_higher():
    good, _ = compute_composite_score(_result(exp=15.0, sharpe=2.5, win=85.0, dd=2.0))
    bad, _ = compute_composite_score(_result(exp=-5.0, sharpe=-0.5, win=35.0, dd=20.0))
    assert good > bad


def test_champion_must_be_eligible_for_the_current_regime(monkeypatch):
    """
    An ineligible variant may NOT win, even with the best numbers in the field. This is the
    core guarantee of the arena: the regime gate is not overridable by score.
    """
    import reasoning.strategy_arena as arena_mod

    # Iron condors are NOT eligible in LOW_VOL_TREND. Give the condor by far the best numbers
    # in the field and confirm it still cannot win.
    stub = _StubEngine({
        "iron_condor": _result(exp=25.0, sharpe=3.0, win=95.0, dd=1.0),
        "credit_spread_put": _result(exp=5.0, sharpe=0.8, win=60.0, dd=8.0),
        "credit_spread_call": _result(exp=5.0, sharpe=0.8, win=60.0, dd=8.0),
        "debit_spread": _result(exp=3.0, sharpe=0.5, win=45.0, dd=10.0),
        "strangle": _result(exp=1.0),
    })
    monkeypatch.setattr(arena_mod, "replay_engine", stub)

    arena = StrategyArena()
    ctx = make_ctx(iv_rank=12.0, vrp=-1.0, trend_20d_pct=8.0, price_vs_ema20_pct=3.5)
    result = arena.run_tournament(ctx, use_cache=False)

    assert result.regime.regime == "LOW_VOL_TREND"
    champion = next(s for s in result.scores if s.variant_id == result.champion_id)
    assert champion.eligible is True
    assert champion.structure != "iron_condor"
    # The top-scoring condor is still visible in the leaderboard, just barred from winning.
    condor = next(s for s in result.scores if s.structure == "iron_condor")
    assert condor.eligible is False
    assert condor.score > champion.score, "the excluded variant should still out-score the champion"


def test_no_champion_when_nothing_is_eligible(monkeypatch):
    import reasoning.strategy_arena as arena_mod
    monkeypatch.setattr(arena_mod, "replay_engine", _StubEngine({}))

    arena = StrategyArena()
    result = arena.run_tournament(make_ctx(is_earnings_blackout=True), use_cache=False)
    assert result.regime.regime == "EVENT_RISK"
    assert result.champion_id is None
    assert "stand" in result.champion_rationale.lower()


def test_arena_discloses_its_methodological_bias(monkeypatch):
    import reasoning.strategy_arena as arena_mod
    monkeypatch.setattr(arena_mod, "replay_engine", _StubEngine({"iron_condor": _result()}))
    result = StrategyArena().run_tournament(make_ctx(), use_cache=False)
    assert "realized vol x 1.15" in result.known_bias


def test_every_registered_variant_has_a_thesis_and_regimes():
    for v in STRATEGY_REGISTRY:
        assert v.thesis, f"{v.variant_id} has no stated thesis"
        assert v.eligible_regimes, f"{v.variant_id} is eligible nowhere"
        assert v.params, f"{v.variant_id} has no parameters to evolve"


# --- Broker position sign handling ---------------------------------------------------

class _FakePos:
    """Mimics an alpaca-py Position, which returns qty ALREADY signed plus a separate side."""

    def __init__(self, symbol, qty, side):
        self.symbol = symbol
        self.qty = qty
        self.side = side


def test_short_positions_are_signed_negative_not_double_flipped():
    """
    Regression test. Alpaca returns qty already signed ('-8') AND a side enum. Negating an
    already-negative qty reports shorts as LONG, which corrupts portfolio delta/vega - two of
    the limits the Risk Kernel enforces. Verified against real broker payloads.
    """
    from perception.alpaca_client import _signed_qty

    assert _signed_qty(_FakePos("SPY_C", "-8", "PositionSide.SHORT")) == -8.0
    assert _signed_qty(_FakePos("SPY_C", "8", "PositionSide.LONG")) == 8.0
    # Correct even if a future API version stops signing qty for shorts.
    assert _signed_qty(_FakePos("SPY_P", "3", "PositionSide.SHORT")) == -3.0
    assert _signed_qty(_FakePos("SPY_P", "-3", "PositionSide.LONG")) == 3.0
