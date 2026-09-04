"""
reasoning/llm_client.py - Unified multi-provider LLM client with a hard fail-closed contract.

Every LLM call in GlassBox goes through this one module: Intent generation, post-trade reflection,
and the copilot chat. Before this existed each call site rolled its own provider handling, and two
of the three had no OpenRouter fallback at all - so when Gemini ran out of quota, reflections and
chat silently degraded to canned templates even though a perfectly good secondary provider was
configured.

Provider order:
  1. Google Gemini      (GEMINI_API_KEY / GEMINI_MODEL)
  2. OpenRouter         (OPENROUTER_API_KEY / OPENROUTER_MODEL)
  3. None               -> the caller MUST fall back to its own deterministic path

The third case is a first-class outcome, not an error. Nothing in this system is allowed to invent
a trade, a price or a reflection because a model was unreachable; callers degrade to deterministic
rules and label the output honestly.

Every attempt is recorded in `status` so the dashboard can show operators which provider is
actually answering right now, rather than implying "AI powered" when everything is running on
deterministic fallbacks.
"""

import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 25


def _placeholder(value: str) -> bool:
    """True when an env var is absent or still the .env.example placeholder."""
    if not value:
        return True
    v = value.strip().lower()
    return v.startswith("your_") or v in {"none", "changeme", ""}


class LLMResponse:
    """Result of one generation attempt across all configured providers."""

    def __init__(self, text: Optional[str], provider: Optional[str],
                 errors: Optional[List[str]] = None):
        self.text = text
        self.provider = provider          # "gemini" | "openrouter" | None
        self.errors = errors or []

    @property
    def ok(self) -> bool:
        return bool(self.text and self.text.strip())

    def __bool__(self) -> bool:
        return self.ok


class LLMClient:
    def __init__(self, system_instruction: Optional[str] = None):
        self.system_instruction = system_instruction

        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
        # Any OpenRouter model slug works here; set OPENROUTER_MODEL in .env to switch.
        self.openrouter_model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

        self._gemini = None
        self._lock = threading.Lock()

        # Per-provider health, surfaced through /api/status for the dashboard.
        self.status: Dict[str, Dict[str, Any]] = {
            "gemini": {"configured": False, "last_ok": None, "last_error": None, "calls": 0, "failures": 0},
            "openrouter": {"configured": False, "last_ok": None, "last_error": None, "calls": 0, "failures": 0},
        }

        if not _placeholder(self.gemini_key):
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.gemini_key)
                self._gemini = genai.GenerativeModel(
                    model_name=self.gemini_model,
                    system_instruction=system_instruction,
                )
                self.status["gemini"]["configured"] = True
            except Exception as e:
                self.status["gemini"]["last_error"] = f"init failed: {e}"
                print(f"[LLM] Gemini init warning: {e}")

        if not _placeholder(self.openrouter_key):
            self.status["openrouter"]["configured"] = True

    # ------------------------------------------------------------------ helpers

    def _record(self, provider: str, ok: bool, error: Optional[str] = None):
        with self._lock:
            s = self.status[provider]
            s["calls"] += 1
            if ok:
                s["last_ok"] = datetime.now(timezone.utc).isoformat()
                s["last_error"] = None
            else:
                s["failures"] += 1
                s["last_error"] = (error or "unknown error")[:300]

    @property
    def any_provider_configured(self) -> bool:
        return self.status["gemini"]["configured"] or self.status["openrouter"]["configured"]

    def get_status(self) -> Dict[str, Any]:
        """
        Provider health for the dashboard.

        A provider is only reported LIVE once a call has actually succeeded. "Configured" is not
        the same as "working" - an exhausted API key looks perfectly configured right up until it
        returns 429, and claiming LIVE on that basis would be exactly the kind of comfortable
        fiction this system exists to avoid.

        mode:
          LIVE            a provider answered successfully on its most recent attempt
          READY           configured, but no call has been made yet (unverified)
          DEGRADED        configured, but the most recent attempt failed
          NOT_CONFIGURED  no API key set - everything runs on deterministic fallbacks
        """
        with self._lock:
            gem, orr = dict(self.status["gemini"]), dict(self.status["openrouter"])

        def healthy(s):
            return s["configured"] and s["last_ok"] and not s["last_error"]

        def unverified(s):
            return s["configured"] and not s["calls"]

        if healthy(gem):
            active, mode = "gemini", "LIVE"
        elif healthy(orr):
            active, mode = "openrouter", "LIVE"
        elif unverified(gem) or unverified(orr):
            active, mode = None, "READY"
        elif gem["configured"] or orr["configured"]:
            active, mode = None, "DEGRADED"
        else:
            active, mode = None, "NOT_CONFIGURED"

        detail = {
            "LIVE": f"{active} is answering",
            "READY": "configured but not yet exercised this session",
            "DEGRADED": "every configured provider failed its last call - running on deterministic rules",
            "NOT_CONFIGURED": "no GEMINI_API_KEY or OPENROUTER_API_KEY set - running on deterministic rules",
        }[mode]

        return {
            "mode": mode,
            "active_provider": active,
            "detail": detail,
            "gemini": {**gem, "model": self.gemini_model},
            "openrouter": {**orr, "model": self.openrouter_model},
            "fallback": "deterministic rules (no fabricated output)",
        }

    # ------------------------------------------------------------------ providers

    def _try_gemini(self, prompt: str, json_mode: bool) -> Optional[str]:
        if not self._gemini:
            return None
        try:
            config: Dict[str, Any] = {"temperature": 0.2}
            if json_mode:
                config["response_mime_type"] = "application/json"
            response = self._gemini.generate_content(prompt, generation_config=config)
            text = getattr(response, "text", None)
            if text and text.strip():
                self._record("gemini", True)
                return text
            self._record("gemini", False, "empty response")
        except Exception as e:
            self._record("gemini", False, str(e))
            print(f"[LLM] Gemini call failed: {e}. Trying OpenRouter...")
        return None

    def _try_openrouter(self, prompt: str, json_mode: bool, timeout: int) -> Optional[str]:
        if not self.status["openrouter"]["configured"]:
            return None
        try:
            messages = []
            if self.system_instruction:
                messages.append({"role": "system", "content": self.system_instruction})
            messages.append({"role": "user", "content": prompt})

            payload: Dict[str, Any] = {
                "model": self.openrouter_model,
                "messages": messages,
                "temperature": 0.2,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            resp = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.openrouter_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://glassbox-options.ai",
                    "X-Title": "GlassBox Options",
                },
                json=payload,
                timeout=timeout,
            )
            if resp.status_code != 200:
                self._record("openrouter", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
                print(f"[LLM] OpenRouter HTTP {resp.status_code}: {resp.text[:200]}")
                return None

            data = resp.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if text and text.strip():
                self._record("openrouter", True)
                return text
            self._record("openrouter", False, "empty response")
        except Exception as e:
            self._record("openrouter", False, str(e))
            print(f"[LLM] OpenRouter call failed: {e}")
        return None

    # ------------------------------------------------------------------ public API

    def generate(
        self,
        prompt: str,
        json_mode: bool = False,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> LLMResponse:
        """
        Try each configured provider in order. Returns an LLMResponse whose `provider` names who
        actually answered, or a falsy response when nobody could - callers must then use their own
        deterministic path and label the result as such.
        """
        errors: List[str] = []

        text = self._try_gemini(prompt, json_mode)
        if text:
            return LLMResponse(text, "gemini")
        if self.status["gemini"]["configured"]:
            errors.append(f"gemini: {self.status['gemini']['last_error']}")

        text = self._try_openrouter(prompt, json_mode, timeout)
        if text:
            return LLMResponse(text, "openrouter")
        if self.status["openrouter"]["configured"]:
            errors.append(f"openrouter: {self.status['openrouter']['last_error']}")

        if not self.any_provider_configured:
            errors.append("no LLM provider configured (set GEMINI_API_KEY or OPENROUTER_API_KEY)")
        return LLMResponse(None, None, errors)


# Shared singleton for callers that don't need their own system instruction.
llm_client = LLMClient()
