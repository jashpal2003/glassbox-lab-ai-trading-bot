"""
tests/test_llm_client.py - Unit tests for the unified multi-provider LLM client.

The contract under test is the fail-closed one: providers are tried in order, "configured" is
never reported as "working", and when nobody answers the caller gets an explicit falsy response
so it can use its own deterministic path instead of inventing output.
"""

import pytest

import reasoning.llm_client as llm_mod
from reasoning.llm_client import LLMClient, LLMResponse, _placeholder


@pytest.fixture(autouse=True)
def clear_keys(monkeypatch):
    """Every test starts from a known, key-free environment."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


class _Resp:
    def __init__(self, status_code=200, content="MODEL OUTPUT", payload=None):
        self.status_code = status_code
        self.text = "error body"
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": content}}]
        }

    def json(self):
        return self._payload


# --- placeholder detection -----------------------------------------------------------

@pytest.mark.parametrize("value", ["", None, "your_openrouter_api_key_here", "YOUR_KEY", "none"])
def test_placeholder_values_are_not_treated_as_configured(value):
    assert _placeholder(value or "") is True


def test_real_looking_key_is_treated_as_configured():
    assert _placeholder("sk-or-v1-abc123") is False


# --- status reporting ----------------------------------------------------------------

def test_no_keys_reports_not_configured():
    status = LLMClient().get_status()
    assert status["mode"] == "NOT_CONFIGURED"
    assert status["active_provider"] is None


def test_configured_but_unexercised_reports_ready_not_live(monkeypatch):
    """
    An exhausted API key looks perfectly configured until it returns 429. Reporting LIVE on the
    basis of configuration alone would be a comfortable fiction.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    status = LLMClient().get_status()
    assert status["mode"] == "READY"
    assert status["active_provider"] is None


def test_failed_call_reports_degraded(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp(status_code=401))
    client = LLMClient()
    assert client.generate("hi").ok is False
    status = client.get_status()
    assert status["mode"] == "DEGRADED"
    assert "401" in status["openrouter"]["last_error"]


def test_successful_call_reports_live(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp(content="hello"))
    client = LLMClient()
    result = client.generate("hi")
    assert result.ok and result.provider == "openrouter" and result.text == "hello"
    status = client.get_status()
    assert status["mode"] == "LIVE"
    assert status["active_provider"] == "openrouter"


# --- fallback ordering ---------------------------------------------------------------

def test_openrouter_is_used_when_gemini_fails(monkeypatch):
    """The whole point of the refactor: a dead primary must not disable the secondary."""
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp(content="from openrouter"))

    client = LLMClient()

    class _DeadGemini:
        def generate_content(self, *a, **k):
            raise RuntimeError("429 quota exhausted")

    client._gemini = _DeadGemini()
    client.status["gemini"]["configured"] = True

    result = client.generate("hi")
    assert result.provider == "openrouter"
    assert result.text == "from openrouter"
    assert client.status["gemini"]["failures"] == 1


def test_gemini_is_preferred_when_both_work(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp(content="from openrouter"))

    client = LLMClient()

    class _OkGemini:
        def generate_content(self, *a, **k):
            return type("R", (), {"text": "from gemini"})()

    client._gemini = _OkGemini()
    client.status["gemini"]["configured"] = True

    result = client.generate("hi")
    assert result.provider == "gemini" and result.text == "from gemini"


def test_all_providers_down_returns_falsy_with_reasons(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp(status_code=500))
    result = LLMClient().generate("hi")
    assert not result
    assert result.text is None and result.provider is None
    assert any("openrouter" in e for e in result.errors)


def test_empty_model_response_counts_as_failure(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp(content="   "))
    client = LLMClient()
    assert client.generate("hi").ok is False
    assert client.status["openrouter"]["last_error"] == "empty response"


def test_network_exception_is_caught_not_raised(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    def _boom(*a, **k):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(llm_mod.requests, "post", _boom)
    result = LLMClient().generate("hi")   # must not raise
    assert result.ok is False


def test_json_mode_sets_response_format(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured = {}

    def _capture(url, **kwargs):
        captured.update(kwargs.get("json", {}))
        return _Resp(content="{}")

    monkeypatch.setattr(llm_mod.requests, "post", _capture)
    LLMClient().generate("hi", json_mode=True)
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["temperature"] == 0.2


def test_system_instruction_is_sent_as_system_message(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured = {}

    def _capture(url, **kwargs):
        captured.update(kwargs.get("json", {}))
        return _Resp()

    monkeypatch.setattr(llm_mod.requests, "post", _capture)
    LLMClient(system_instruction="BE PRECISE").generate("hi")
    assert captured["messages"][0] == {"role": "system", "content": "BE PRECISE"}


# --- reflection engine wiring --------------------------------------------------------

def test_reflection_uses_openrouter_and_labels_the_provider(tmp_path, monkeypatch):
    """
    Regression: the reflection engine previously held a Gemini-only handle, so it fell through to
    canned templates whenever Gemini was down even with a healthy OpenRouter configured.
    """
    from reasoning.self_improvement import SelfImprovingMemory

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr(llm_mod.requests, "post",
                        lambda *a, **k: _Resp(content="Vol crushed the short wing.\nWiden wings next time."))

    memory = SelfImprovingMemory(memory_path=str(tmp_path / "mem.json"), llm=LLMClient())
    trade = memory.record_entry({"underlying": "SPY", "structure": "iron_condor"},
                                {"underlying_price": 100.0}, order_id="ord-1")
    closed = memory.close_and_reflect(trade.trade_id, exit_price=101.0, realized_pnl=50.0)

    assert closed.reflection_source == "openrouter"
    assert "template" not in closed.reflection.lower()


def test_reflection_falls_back_to_labelled_template_with_no_provider(tmp_path):
    from reasoning.self_improvement import SelfImprovingMemory

    memory = SelfImprovingMemory(memory_path=str(tmp_path / "mem.json"), llm=LLMClient())
    trade = memory.record_entry({"underlying": "SPY", "structure": "iron_condor"},
                                {"underlying_price": 100.0}, order_id="ord-2")
    closed = memory.close_and_reflect(trade.trade_id, exit_price=99.0, realized_pnl=-40.0)

    assert closed.reflection_source == "template_fallback"
    assert closed.reflection.startswith("[Template]")
