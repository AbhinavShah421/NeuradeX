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
    or None if the LLM is disabled/unavailable (caller should fall back)."""
    provider = resolve_provider()
    try:
        if provider == "anthropic":
            return await _anthropic_chat(prompt, system, temperature, max_tokens, timeout)
        if provider == "ollama":
            return await _ollama_chat(prompt, system, temperature, timeout)
        return None  # "off"
    except Exception as exc:
        logger.debug("llm_chat (%s) failed: %s", provider, exc)
        return None


async def llm_status(probe: bool = True) -> dict:
    """Report which provider is active and whether it actually responds."""
    provider = resolve_provider()
    info = {
        "configured_provider": settings.LLM_PROVIDER,
        "active_provider": provider,
        "anthropic_key_present": bool(settings.ANTHROPIC_API_KEY),
        "model": active_model(),
        "ollama_host": settings.LLM_API_URL,
        "available": False,
    }
    if probe and provider != "off":
        try:
            out = await llm_chat("Reply with exactly: ok", temperature=0.0,
                                 max_tokens=10, timeout=8.0)
            info["available"] = bool(out)
            info["probe_response"] = (out or "")[:60]
        except Exception as exc:
            info["probe_error"] = str(exc)[:120]
    return info
