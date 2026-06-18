---
id: mutual-funds
title: Mutual Funds API
sidebar_label: Mutual Funds
---

# Mutual Funds — `/api/mutual-funds`

Real mutual-fund data, a personal tracker, a screener and AI optimisation — all on
**official AMFI NAV data** (via `api.mfapi.in`, ~75k schemes). Groww's trading API
does **not** expose mutual-fund holdings, so personal holdings are entered by the
user and stored in Redis; every NAV/return figure is live and real.

## Data & search

| Method & path | Description |
|---|---|
| `GET /search?q=` | Search schemes by name (prefers Direct-Growth share classes). |
| `GET /scheme/{code}` | Scheme NAV + returns: 1M/3M/6M/1Y/3Y (≥1Y annualised as CAGR), **volatility** (annualised) and **risk-adjusted** (return ÷ vol). |
| `GET /categories` | Screener categories (equity caps + hybrid + debt + sectoral/thematic). |

## My Funds (personal, Redis-persisted)

| Method & path | Description |
|---|---|
| `GET /holdings` | Saved funds enriched with live NAV, current value, P&L and returns. |
| `POST /holdings` | Add a fund `{ scheme_code, units?, invested? }`. |
| `DELETE /holdings/{code}` | Remove a fund. |

## Screener

| Method & path | Description |
|---|---|
| `GET /screener?category=&limit=&sort=return\|risk` | Per-category leaderboard ranked by **1Y return** or **risk-adjusted** return, with Vol + Risk-adj and ⭐ AI top-picks. |

## AI scan + optimisation

| Method & path | Description |
|---|---|
| `GET /scan` | Scans each held fund vs its category peers → verdict **HOLD / REVIEW / REPLACE** with a better risk-adjusted peer suggestion. |
| `GET /optimize?risk=conservative\|moderate\|aggressive` | **Whole-portfolio optimiser**: asset-class mix (Equity/Hybrid/Debt) vs a risk-based target; **category redundancy** detection (funds doing the same job → CONSOLIDATE into the best); per-fund **KEEP/REPLACE/CONSOLIDATE** plan; diversification notes; AI summary (LLM + rule fallback). |

Returns ≥1Y are annualised; risk-adjusted = 1Y return ÷ annualised volatility
(Sharpe-like). Surfaced on the **Mutual Funds** page (My Funds / Optimize /
Screener tabs).
