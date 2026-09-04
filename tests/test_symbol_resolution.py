"""
tests/test_symbol_resolution.py - Symbol search, and the refusal to invent data.

The regression these lock down: a nonexistent ticker used to return HTTP 200 with a
hash-derived price ("NOTAREALTICKER" -> $158.50) because the simulated feed - which exists for
running with NO credentials - was also catching "this symbol does not exist". Inventing a
plausible price for a company that isn't real is the exact failure mode this project exists to
prevent, so it must fail loudly instead.
"""

import pytest

from perception.alpaca_client import AlpacaClient, MarketDataUnavailable, SYMBOL_ALIASES


ASSETS = [
    {"symbol": "MSFT", "name": "Microsoft Corporation Common Stock"},
    {"symbol": "MSFX", "name": "T-Rex 2X Long Microsoft Daily Target ETF"},
    {"symbol": "AAPL", "name": "Apple Inc. Common Stock"},
    {"symbol": "MLP", "name": "Maple Leaf Products"},
    {"symbol": "KO", "name": "Coca-Cola Company"},
    {"symbol": "COKE", "name": "Coca-Cola Consolidated, Inc."},
    {"symbol": "GOOGL", "name": "Alphabet Inc. Class A Common Stock"},
    {"symbol": "JPM", "name": "JPMorgan Chase & Co."},
    {"symbol": "SPY", "name": "State Street SPDR S&P 500 ETF Trust"},
]


@pytest.fixture
def client(monkeypatch):
    c = AlpacaClient()
    monkeypatch.setattr(c, "_get_asset_list", lambda: ASSETS)
    return c


# --- search ranking -------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("msft", "MSFT"),           # exact ticker
    ("MSFT", "MSFT"),           # case-insensitive
    ("microsoft", "MSFT"),      # company name, not the 2x ETF that shares it
    ("apple", "AAPL"),          # must beat "Maple", which merely contains "apple"
    ("coca cola", "KO"),        # space vs. the hyphen in "Coca-Cola"
    ("coca-cola", "KO"),
    ("google", "GOOGL"),        # brand alias -> Alphabet
    ("jp morgan", "JPM"),       # space vs. "JPMorgan"
])
def test_search_returns_expected_primary_symbol(client, query, expected):
    assert client.search_symbols(query)[0]["symbol"] == expected


def test_typo_resolves_via_fuzzy_match(client):
    """'microsft' is what a person actually types."""
    assert client.search_symbols("microsft")[0]["symbol"] == "MSFT"


def test_no_match_returns_empty_not_a_guess(client):
    assert client.search_symbols("ZZQQXNOTREAL") == []
    assert client.resolve_symbol("ZZQQXNOTREAL") is None


def test_blank_query_returns_empty(client):
    assert client.search_symbols("") == []
    assert client.search_symbols("   ") == []


def test_shorter_symbol_wins_within_a_tier(client):
    """The primary listing is almost always shorter than the leveraged/yield products."""
    syms = [r["symbol"] for r in client.search_symbols("microsoft")]
    assert syms.index("MSFT") < syms.index("MSFX")


def test_aliases_all_point_at_plausible_tickers():
    for text, ticker in SYMBOL_ALIASES.items():
        assert ticker.isupper() and 1 <= len(ticker.replace(".", "")) <= 5, (text, ticker)


# --- refusal to fabricate -------------------------------------------------------------

def test_unknown_symbol_raises_instead_of_returning_simulated_data(monkeypatch):
    """
    THE regression test. With credentials configured, an unresolvable symbol must raise -
    never fall through to the simulated feed and answer with an invented price.
    """
    c = AlpacaClient()
    monkeypatch.setattr(c, "has_real_client", True)
    monkeypatch.setattr(c, "_get_asset_list", lambda: ASSETS)
    monkeypatch.setattr(c, "_get_real_market_context",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no bars for NOTAREAL")))

    with pytest.raises(MarketDataUnavailable) as excinfo:
        c.get_market_context("NOTAREAL", force_refresh=True)
    assert excinfo.value.symbol == "NOTAREAL"


def test_unknown_symbol_error_carries_suggestions(monkeypatch):
    c = AlpacaClient()
    monkeypatch.setattr(c, "has_real_client", True)
    monkeypatch.setattr(c, "_get_asset_list", lambda: ASSETS)
    monkeypatch.setattr(c, "_get_real_market_context",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no bars")))

    with pytest.raises(MarketDataUnavailable) as excinfo:
        c.get_market_context("MICROSFT", force_refresh=True)
    assert "MSFT" in [s["symbol"] for s in excinfo.value.suggestions]


def test_chart_bars_also_refuse_to_fabricate(monkeypatch):
    c = AlpacaClient()
    monkeypatch.setattr(c, "has_real_client", True)
    monkeypatch.setattr(c, "_get_asset_list", lambda: ASSETS)
    monkeypatch.setattr(c, "_get_real_ohlcv_bars",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no bars")))
    with pytest.raises(MarketDataUnavailable):
        c.get_ohlcv_bars("NOTAREAL")


def test_simulated_feed_still_serves_when_no_credentials(monkeypatch):
    """The documented no-credentials path must keep working, clearly labelled."""
    c = AlpacaClient()
    monkeypatch.setattr(c, "has_real_client", False)
    ctx = c.get_market_context("SPY", force_refresh=True)
    assert ctx.data_source == "simulated"
    assert ctx.underlying_price > 0


# --- API-level: search endpoint honesty --------------------------------------------------

def test_search_endpoint_reports_searchable_flag(monkeypatch):
    """The UI must know whether search is even possible (no credentials -> no asset master)."""
    from fastapi.testclient import TestClient
    from web.app import app, alpaca_client

    monkeypatch.setattr(alpaca_client, "has_real_client", False)
    client = TestClient(app)
    resp = client.get("/api/symbols/search?q=microsoft")
    assert resp.status_code == 200
    body = resp.json()
    assert body["searchable"] is False
    assert body["results"] == []
