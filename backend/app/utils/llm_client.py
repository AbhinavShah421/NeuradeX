"""Unified LLM client — one async interface over two providers.

Provider resolution (LLM_PROVIDER):
  • "auto"      → Anthropic if ANTHROPIC_API_KEY is set, else Ollama
  • "anthropic" → Anthropic (falls back to Ollama if no key)
  • "ollama"    → Ollama
  • "off"       → disabled (llm_chat returns None)

Both providers are reached over httpx (no extra SDK dependency). Callers should
treat a None return as "LLM unavailable" and fall back to their own heuristics —
the LLM is an enhancement, never a hard dependency on the trading hot path.
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"


def resolve_provider() -> str:
    """Return the provider that will actually be used right now."""
    p = (settings.LLM_PROVIDER or "auto").lower()
    has_key = bool(settings.ANTHROPIC_API_KEY)
    if p == "off":
        return "off"
    if p == "anthropic":
        return "anthropic" if has_key else "ollama"
    if p == "ollama":
        return "ollama"
    # auto
    return "anthropic" if has_key else "ollama"


def active_model() -> str:
    return settings.ANTHROPIC_MODEL if resolve_provider() == "anthropic" else settings.LLM_MODEL


async def _anthropic_chat(prompt: str, system: str | None, temperature: float,
                          max_tokens: int, timeout: float) -> str | None:
    url = f"{settings.ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    body: dict = {
        "model": settings.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        return "".join(parts).strip() or None


async def _ollama_chat(prompt: str, system: str | None, temperature: float,
                       timeout: float) -> str | None:
    url = f"{settings.LLM_API_URL.rstrip('/')}/api/chat"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature},
    }
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=body)
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "").strip() or None


async def llm_chat(prompt: str, system: str | None = None, *,
                   temperature: float = 0.1, max_tokens: int = 512,
                   timeout: float = 12.0) -> str | None:
    """Send a single-turn prompt to the active provider. Returns the text reply,
    or None if the LLM is disabled/unavailable (caller should fall back).

    If Anthropic is selected but a call fails (no credits, rate limit, network),
    it falls back to Ollama automatically — so a flaky/empty Anthropic account
    never takes the system's LLM features down."""
    provider = resolve_provider()
    if provider == "off":
        return None
    if provider == "anthropic":
        try:
            out = await _anthropic_chat(prompt, system, temperature, max_tokens, timeout)
            if out:
                return out
        except Exception as exc:
            logger.debug("anthropic failed, falling back to ollama: %s", exc)
        try:
            return await _ollama_chat(prompt, system, temperature, timeout)
        except Exception as exc:
            logger.debug("ollama fallback failed: %s", exc)
            return None
    try:
        return await _ollama_chat(prompt, system, temperature, timeout)
    except Exception as exc:
        logger.debug("ollama failed: %s", exc)
        return None


import asyncio as _asyncio
import time as _time

_llm_status_cache: dict = {"ts": 0.0, "data": None}
_llm_status_lock = _asyncio.Lock()
_LLM_STATUS_TTL = 300.0  # seconds between actual probes (5 min)


async def llm_status(probe: bool = True) -> dict:
    """Report which provider is active and whether it actually responds.

    Results are cached for 60 s so that frequent polling from the dashboard
    never causes more than one live probe to run at a time — each Anthropic
    probe can take up to 8–30 s and was previously exhausting uvicorn workers.
    """
    now = _time.monotonic()
    if probe and _llm_status_cache["data"] is not None and now - _llm_status_cache["ts"] < _LLM_STATUS_TTL:
        return _llm_status_cache["data"]

    provider = resolve_provider()
    info: dict = {
        "configured_provider": settings.LLM_PROVIDER,
        "active_provider": provider,
        "anthropic_key_present": bool(settings.ANTHROPIC_API_KEY),
        "model": active_model(),
        "ollama_host": settings.LLM_API_URL,
        "available": False,
    }

    if not probe or provider == "off":
        return info

    async with _llm_status_lock:
        # Re-check inside lock — a concurrent waiter may have just refreshed it
        now = _time.monotonic()
        if _llm_status_cache["data"] is not None and now - _llm_status_cache["ts"] < _LLM_STATUS_TTL:
            return _llm_status_cache["data"]

        if provider == "anthropic":
            try:
                a = await _anthropic_chat("Reply with: ok", None, 0.0, 10, 3.0)
                info["anthropic_ok"] = bool(a)
            except Exception as exc:
                info["anthropic_ok"] = False
                info["anthropic_error"] = str(exc)[:140]
                info["effective_provider"] = "ollama (fallback)"
        try:
            out = await llm_chat("Reply with exactly: ok", temperature=0.0,
                                 max_tokens=10, timeout=30.0)
            info["available"] = bool(out)
            info["probe_response"] = (out or "")[:60]
        except Exception as exc:
            info["probe_error"] = str(exc)[:120]

        _llm_status_cache["data"] = info
        _llm_status_cache["ts"] = _time.monotonic()

    return info
