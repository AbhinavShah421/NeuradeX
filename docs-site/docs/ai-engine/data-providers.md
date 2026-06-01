---
id: data-providers
title: Market-Data Providers
sidebar_label: Data Providers
---

# Market-Data Providers

The platform is **not tied to a single data source**. A pluggable provider
registry tries each configured source in priority order and returns the first
**real** result — so when one is rate-limited or down, the system automatically
uses another instead of falling back to simulated data.

> **Where it lives:** `backend/app/data/providers/` (registry + provider
> implementations); config UI at `frontend/src/pages/Settings.tsx`.

---

## Providers

| Provider | Key | Strengths |
|---|---|---|
| **Groww** | broker API key | Best historical (daily + past intraday). No in-progress day; live quotes need a paid Live-Data subscription |
| **Yahoo Finance** (`.NS`) | none | Free. Serves **today's** intraday (~1–2 min delay) + years of daily history |
| **Alpha Vantage** (`.BSE`) | free API key | Rate-limited fallback for daily/intraday |

Add a provider by subclassing `DataProvider` and registering it in
`app/data/providers/__init__.py` (`_ALL`).

---

## Resolution

```
fetch_intraday(symbol, date) / fetch_daily(symbol, start, end)
    └─ for provider in enabled order:        # default: groww → yahoo → alphavantage
          candles = await provider.fetch(...)
          if candles: return candles, provider.name
       └─ else: simulated (only when no real provider has data)
```

The **real-data-only guard** (AI Live Trading) treats any non-`simulated` source
as real, so replay/paper now work via Yahoo when Groww is unavailable.

---

## Runtime configuration

Provider **order**, **enabled** flags and **API keys** are stored in Redis and
edited from the **Settings** page (profile menu → Settings). Changes take effect
immediately for all data fetches.

| Method & path | Description |
|---|---|
| `GET /api/backtest/providers` | List providers + live availability |
| `GET /api/settings/providers` | Providers + current config (order / enabled / key present) — auth |
| `PUT /api/settings/providers` | Update order / disabled / keys — auth |

---

## Groww rate limiting

Groww limits are **per-type and shared** (Live Data 10/s · 300/min; Non-Trading
20/s · 500/min) and exhausting one API throttles the whole type — including the
token endpoint. The Groww client (`app/utils/groww_client.py`) therefore stays
**under the limits by design**:

- a global **token-bucket limiter** (5 req/s, 200 req/min) on every request,
  including token refresh;
- **exponential backoff** on failed token refreshes so a penalised token
  endpoint is left to reset;
- a live-data `401` (entitlement) **does not** wipe/refresh the shared token.

If Groww is rate-limited, set **Yahoo** as the top provider in **Settings** and
the app keeps serving real data with no Groww dependency.
