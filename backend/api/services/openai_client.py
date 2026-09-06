"""
Singleton OpenAI client with cost tracking and rate limiting.

Features:
- One client instance per process (no per-request recreation)
- Per-model hourly call counters with configurable caps
- GPT-4o → gpt-4o-mini automatic downgrade when cap hit
- Offline fallback stub (returns None → caller uses local/Ollama path)

Usage:
    from api.services.openai_client import openai_client
    reply = openai_client.generate(messages, model="gpt-4o-mini")
    if reply is None:
        # API unavailable or quota hit — use offline fallback
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Literal, Optional

logger = logging.getLogger(__name__)

ModelType = Literal["gpt-4o-mini", "gpt-4o"]

# ── Rate limits (per rolling hour window) ────────────────────────────────────
# Override in .env via XYRON_MAX_GPT4O_PER_HOUR / XYRON_MAX_MINI_PER_HOUR
_DEFAULT_MAX_GPT4O_PER_HOUR = 20
_DEFAULT_MAX_MINI_PER_HOUR  = 500

# ── Local Ollama model for offline fallback ──────────────────────────────────
# mistral:7b (4.4GB) OOMs on low-VRAM GPUs like the T1200 (4GB) once Whisper/
# Kokoro/XTTS are also resident — benchmarked 2026-08-19: qwen2.5:1.5b answers
# in ~0.8-1s warm at 1.4GB VRAM with reliable structured-JSON output, vs.
# mistral:7b's OOM risk and qwen3:4b's ~20s+ "thinking mode" overhead (its
# `think:false` request param is not honored by the installed build). Override
# via OLLAMA_LOCAL_MODEL if a different local model is verified to fit.
import os as _os
LOCAL_OLLAMA_MODEL = _os.getenv("OLLAMA_LOCAL_MODEL", "qwen2.5:1.5b")


# ── Cost tracker ──────────────────────────────────────────────────────────────

class _HourlyTracker:
    """Thread-safe rolling-hour call counter per model."""

    def __init__(self):
        self._lock         = threading.Lock()
        self._window_start = time.monotonic()
        self._counts: dict[str, int] = {}

    def _maybe_reset(self) -> None:
        if time.monotonic() - self._window_start >= 3600:
            self._window_start = time.monotonic()
            self._counts.clear()
            logger.info("[CostTracker] Hourly window reset")

    def increment(self, model: str) -> int:
        with self._lock:
            self._maybe_reset()
            self._counts[model] = self._counts.get(model, 0) + 1
            return self._counts[model]

    def get(self, model: str) -> int:
        with self._lock:
            self._maybe_reset()
            return self._counts.get(model, 0)

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._maybe_reset()
            return dict(self._counts)

    def seconds_until_reset(self) -> int:
        with self._lock:
            elapsed = time.monotonic() - self._window_start
            return max(0, int(3600 - elapsed))


# ── Singleton client ───────────────────────────────────────────────────────────

class OpenAIClient:
    """
    Process-wide singleton OpenAI client.
    Never recreated per request — safe for async + threaded FastAPI.
    """

    _instance: Optional["OpenAIClient"] = None
    _create_lock = threading.Lock()

    # Maps OpenAI model names → best available local Ollama model
    _OLLAMA_MODEL_MAP: dict[str, str] = {
        "gpt-4o":      LOCAL_OLLAMA_MODEL,
        "gpt-4o-mini": LOCAL_OLLAMA_MODEL,
    }
    _OLLAMA_BASE_URL = "http://localhost:11434/v1"

    def __new__(cls) -> "OpenAIClient":
        with cls._create_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._ready            = False
                inst._client           = None
                inst._ollama_client    = None  # OpenAI-compat client pointing at Ollama
                inst._quota_exhausted  = False  # set True on first insufficient_quota → skip retries
                inst._tracker          = _HourlyTracker()
                inst._max_4o           = _DEFAULT_MAX_GPT4O_PER_HOUR
                inst._max_mini         = _DEFAULT_MAX_MINI_PER_HOUR
                inst._init_lock        = threading.Lock()
                cls._instance = inst
        return cls._instance

    # ── Lazy init (reads config only once) ───────────────────────────────────

    def _ensure_init(self) -> None:
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            try:
                from api.config import settings
                key = getattr(settings, "openai_api_key", "") or ""

                # Load custom caps from env if present
                import os
                self._max_4o   = int(os.getenv("XYRON_MAX_GPT4O_PER_HOUR",  str(_DEFAULT_MAX_GPT4O_PER_HOUR)))
                self._max_mini = int(os.getenv("XYRON_MAX_MINI_PER_HOUR",   str(_DEFAULT_MAX_MINI_PER_HOUR)))

                if key and key.startswith("sk-"):
                    from openai import OpenAI
                    # max_retries=0 — the SDK's default retry-with-backoff
                    # treats 429 insufficient_quota the same as a transient
                    # rate limit and retries anyway, which can never succeed
                    # until billing is fixed and only adds seconds of wasted
                    # backoff before falling through to the Ollama fallback
                    # below (same fix already applied in response_pipeline.py
                    # for the same observed retry-storm behavior).
                    self._client = OpenAI(api_key=key, max_retries=0)
                    logger.info("[OpenAIClient] Initialized — 4o cap=%d/hr  mini cap=%d/hr",
                                self._max_4o, self._max_mini)
                else:
                    logger.warning("[OpenAIClient] No valid API key — offline mode active")

                # Always try to set up a local Ollama client as fallback
                self._ollama_client = self._init_ollama()

            except Exception as exc:
                logger.warning("[OpenAIClient] Init error: %s", exc)
            finally:
                self._ready = True

    def _init_ollama(self):
        """Return an OpenAI-compatible client pointing at local Ollama, or None."""
        import os
        base_url = os.getenv("OLLAMA_API_URL", self._OLLAMA_BASE_URL)
        try:
            import urllib.request as _ur
            _ur.urlopen(f"{base_url}/models", timeout=2)  # connectivity check
            from openai import OpenAI
            client = OpenAI(base_url=base_url, api_key="ollama", max_retries=0)
            logger.info("[OpenAIClient] Ollama fallback ready at %s", base_url)
            return client
        except Exception as exc:
            logger.debug("[OpenAIClient] Ollama not available: %s", exc)
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        self._ensure_init()
        return self._client is not None

    def generate(
        self,
        messages: list[dict],
        model: ModelType = "gpt-4o-mini",
        max_tokens: int   = 120,
        temperature: float = 0.7,
    ) -> Optional[str]:
        """
        Call OpenAI and return the text reply.

        Automatic downgrade:
          - If GPT-4o hourly cap hit → runs gpt-4o-mini instead
          - If both caps hit → returns None (caller uses offline path)
          - If API errors → returns None

        Returns None when:
          - No API key configured
          - Rate limit exceeded
          - Network / API error
        """
        self._ensure_init()

        if not self._client or self._quota_exhausted:
            # No OpenAI key, or quota already confirmed exhausted → go straight to Ollama
            return self._ollama_fallback(messages, model, max_tokens, temperature)

        # ── Rate limit check ──────────────────────────────────────────────────
        if model == "gpt-4o":
            used_4o = self._tracker.get("gpt-4o")
            if used_4o >= self._max_4o:
                resets_in = self._tracker.seconds_until_reset()
                logger.warning(
                    "[CostControl] GPT-4o cap hit (%d/%d) — downgrading to gpt-4o-mini (resets in %ds)",
                    used_4o, self._max_4o, resets_in,
                )
                model = "gpt-4o-mini"

        if model == "gpt-4o-mini":
            used_mini = self._tracker.get("gpt-4o-mini")
            if used_mini >= self._max_mini:
                logger.warning("[CostControl] gpt-4o-mini cap hit (%d/%d) — returning None (offline path)",
                               used_mini, self._max_mini)
                return None

        # ── API call ──────────────────────────────────────────────────────────
        call_num = self._tracker.increment(model)
        logger.debug("[OpenAIClient] %s call #%d this hour", model, call_num)

        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return (resp.choices[0].message.content or "").strip()

        except Exception as exc:
            err_str = str(exc)
            if "insufficient_quota" in err_str or "429" in err_str:
                self._quota_exhausted = True
                logger.warning("[OpenAIClient] quota exhausted — switching permanently to Ollama")
            else:
                logger.warning("[OpenAIClient] %s request failed: %s", model, exc)
            return self._ollama_fallback(messages, model, max_tokens, temperature)

    def _ollama_fallback(
        self,
        messages: list[dict],
        openai_model: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        """Try local Ollama when OpenAI fails. Returns None if Ollama also unavailable."""
        if not self._ollama_client:
            return None
        local_model = self._OLLAMA_MODEL_MAP.get(openai_model, "mistral:7b")
        try:
            resp = self._ollama_client.chat.completions.create(
                model=local_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = (resp.choices[0].message.content or "").strip()
            logger.info("[OpenAIClient] Ollama:%s answered (%d chars)", local_model, len(text))
            return text or None
        except Exception as exc:
            logger.warning("[OpenAIClient] Ollama:%s also failed: %s", local_model, exc)
            return None

    def is_available(self) -> bool:
        return self.available

    def stats(self) -> dict:
        """Return current hour call counts — safe to expose in a debug endpoint."""
        self._ensure_init()
        return {
            "calls_this_hour": self._tracker.stats(),
            "caps": {
                "gpt-4o":      self._max_4o,
                "gpt-4o-mini": self._max_mini,
            },
            "resets_in_seconds": self._tracker.seconds_until_reset(),
            "available": self.available,
        }


# ── Module-level singleton (import this everywhere) ───────────────────────────
openai_client = OpenAIClient()


# ── Offline Ollama fallback ────────────────────────────────────────────────────

def offline_generate(
    prompt: str,
    *,
    system: Optional[str] = None,
    complex: bool = False,
) -> Optional[str]:
    """Local Ollama fallback — llama3.2:3b for quick tasks, LOCAL_OLLAMA_MODEL
    (qwen2.5:1.5b by default) for complex ones. mistral:7b (4.4GB) was the prior
    default for `complex` but OOMs on low-VRAM GPUs like the T1200 (4GB) once
    Whisper/Kokoro/XTTS are also resident — see LOCAL_OLLAMA_MODEL above.

    Respects OLLAMA_MODEL and OLLAMA_API_URL env vars.
    Returns None silently if Ollama is not running or the package is not installed.
    """
    import os as _os
    _default_model = LOCAL_OLLAMA_MODEL if complex else "llama3.2:3b"
    model = _os.getenv("OLLAMA_MODEL", _default_model)
    try:
        import ollama as _ollama
        _base_url = _os.getenv("OLLAMA_API_URL", "http://localhost:11434")
        client = _ollama.Client(host=_base_url)
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat(model=model, messages=messages)
        text = resp["message"]["content"].strip()
        logger.debug("[Ollama:%s] response length=%d", model, len(text))
        return text or None
    except Exception as exc:
        logger.warning("[Ollama:%s] failed: %s", model, exc)
        return None
