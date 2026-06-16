---
id: portfolio
title: Portfolio
sidebar_position: 4
---

# Portfolio — `/api/portfolio`

**File:** [`backend/app/api/portfolio.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/portfolio.py)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/portfolio/` | Holdings from Groww (qty + avg). Current price comes from Groww live-data, falling back to **Yahoo** when that entitlement is missing — so current value/P&L and the **1D return** are real instead of collapsing to the average |
| `GET` | `/api/portfolio/optimize?refresh=` | **AI rebalancing plan**: per-holding live signal, concentration/sector risk, scanner opportunities → LLM synthesis (rule-based fallback). Persisted and **keyed to the latest scan** (served from cache until a newer scan lands). Each at-risk holding gets an AI **alternative** + executable trade sizes; `refresh=true` forces recompute |
| `GET` | `/api/portfolio/invest-plan?amount=&max_stocks=` | **AI Invest**: divide `amount` across the best A/B-grade AI picks, conviction-weighted (win probability, capped 35%/stock) as protective LIMIT buys |
| `POST` | `/api/portfolio/add` | Manually add a holding (informational) |
| `GET` | `/api/portfolio/performance` | P&L and performance metrics |
| `GET` / `POST` | `/api/portfolio/alerts` | List / create price-pattern alerts |

**Order sizing:** the optimize/invest plans emit **protective LIMIT** orders (a price collar around the last price, tick-aligned), use each holding's exchange, and cap per-order value — placed via [`/api/orders`](orders.md).

**External call:** Groww broker API via [`backend/app/utils/groww_client.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/utils/groww_client.py) for live holdings; [Yahoo](../ai-engine/data-providers.md) for the live-price/previous-close fallback.  
**Alerts storage:** MongoDB `alerts` collection.

See the [Portfolio & Predictions frontend](../frontend/portfolio-predictions.md) for the AI Optimize / AI Invest / Swap UI.
