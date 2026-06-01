"""Minimal unified LLM client (mirrors the backend's) — Anthropic or Ollama,
auto-selected. Both providers reached over httpx; returns None on failure so the
worker degrades gracefully."""
from __future__ import annotations
import logging
import os

import httpx

logger = logging.getLogger("sentiment-service")

_ANTHROPIC_VERSION = "2023-06-01"

LLM_PROVIDER     = os.getenv("LLM_PROVIDER", "auto")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
OLLAMA_HOST      = os.getenv("LLM_API_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL     = os.getenv("LLM_MODEL", "llama3.2")


def resolve_provider() -> str:
    p = (LLM_PROVIDER or "auto").lower()
    has_key = bool(ANTHROPIC_API_KEY)
    if p == "off":
        return "off"
    if p == "anthropic":
        return "anthropic" if has_key else "ollama"
    if p == "ollama":
        return "ollama"
    return "anthropic" if has_key else "ollama"


def active_model() -> str:
    return ANTHROPIC_MODEL if resolve_provider() == "anthropic" else OLLAMA_MODEL


async def _anthropic(prompt: str, temperature: float, max_tokens: int, timeout: float) -> str | None:
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": _ANTHROPIC_VERSION,
               "content-type": "application/json"}
    body = {"model": ANTHROPIC_MODEL, "max_tokens": max_tokens, "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages", headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip() or None


async def _ollama(prompt: str, temperature: float, timeout: float) -> str | None:
    body = {"model": OLLAMA_MODEL, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "options": {"temperature": temperature}}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{OLLAMA_HOST.rstrip('/')}/api/chat", json=body)
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "").strip() or None


async def llm_chat(prompt: str, *, temperature: float = 0.1, max_tokens: int = 256,
                   timeout: float = 20.0) -> str | None:
    """Anthropic if selected, falling back to Ollama on any failure (no credits,
    rate limit, network) so the sentiment worker keeps producing signals."""
    provider = resolve_provider()
    if provider == "off":
        return None
    if provider == "anthropic":
        try:
            out = await _anthropic(prompt, temperature, max_tokens, timeout)
            if out:
                return out
        except Exception as exc:
            logger.debug("anthropic failed, falling back to ollama: %s", exc)
        try:
            return await _ollama(prompt, temperature, timeout)
        except Exception as exc:
            logger.debug("ollama fallback failed: %s", exc)
            return None
    try:
        return await _ollama(prompt, temperature, timeout)
    except Exception as exc:
        logger.debug("ollama failed: %s", exc)
        return None
