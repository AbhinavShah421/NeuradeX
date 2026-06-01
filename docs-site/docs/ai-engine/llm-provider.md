---
id: llm-provider
title: LLM Provider
sidebar_label: LLM Provider
---

# LLM Provider (Anthropic / Ollama)

One async interface — [`backend/app/utils/llm_client.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/utils/llm_client.py) —
sits over **two** LLM backends and picks one automatically. Both are reached
over plain `httpx` (no provider SDK), and any failure returns `None` so callers
fall back to their own heuristics. **The LLM is always an enhancement, never a
hard dependency on the trading hot path.**

```python
from app.utils.llm_client import llm_chat
reply = await llm_chat(prompt, temperature=0.1, max_tokens=200)  # str | None
```

## Provider selection

Controlled by `LLM_PROVIDER`:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | **Anthropic if `ANTHROPIC_API_KEY` is set, else Ollama** |
| `anthropic` | Claude Messages API (falls back to Ollama if no key) |
| `ollama` | Local Ollama at `LLM_API_URL` |
| `off` | LLM disabled — `llm_chat()` returns `None` |

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `auto` | Provider selection (above) |
| `ANTHROPIC_API_KEY` | — | Set to use Claude |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude model |
| `LLM_MODEL` | `llama3.2` | Ollama model |
| `LLM_API_URL` | `http://host.docker.internal:11434` | Ollama host (reaches the host machine from Docker) |

To switch to Claude, add `ANTHROPIC_API_KEY=sk-ant-...` to `backend/.env` (and
the project root `.env`, which the LLM-using services also read) and recreate
the `backend` + `sentiment-service` containers.

## Status endpoint

```
GET /api/ai-engine/llm-status
```

Reports the active provider, model, whether an Anthropic key is present, and a
live probe (it sends a tiny prompt and checks for a reply):

```json
{ "configured_provider": "auto", "active_provider": "ollama",
  "anthropic_key_present": false, "model": "llama3.2",
  "available": true, "probe_response": "ok" }
```

## Who uses it

| Consumer | Use |
|---|---|
| [sentiment-service](../microservices/sentiment-service.md) | Judges news headlines into a sentiment signal (its own copy of this client, same env) |
| backend `sentiment` agent | Reads the cached news signal (does **not** call the LLM on the hot path) |

> The same provider config (`LLM_PROVIDER`, `ANTHROPIC_API_KEY`, …) is passed to
> every LLM-using service via Docker Compose, so changing it in one place
> switches the whole system.
