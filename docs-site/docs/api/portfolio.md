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

## AI Sector Exposure (scanner + optimizer)

Maps holdings to their real NSE sector, scores each sector with the live AI scan,
and compares the book's sector weights against an AI-favoured target.

| Method & path | Description |
|---|---|
| `GET /api/portfolio/sector-exposure` | Current sector weights vs AI-favoured target, per-sector over/under-exposure, concentration (effective sectors, top sector), warnings, and concrete rebalance moves (TRIM the most overweight, ADD AI-favoured under-owned sectors with a stock to buy). |

The AI sector score weights sectors by how many high-win-probability BUY setups
they hold in the live ranked board; the target is those scores normalised over the
strongest sectors. Surfaced on the Portfolio **Sector Exposure** tab (donut +
current-vs-target bars + rebalance list).

## AI Fund Baskets (mutual-fund-style, AI-scanned)

Themed, conviction-weighted stock baskets built from the live AI ranked board.

| Method & path | Description |
|---|---|
| `GET /api/portfolio/fund-baskets` | Baskets: **AI Top Picks**, **Sector Leaders** (max diversification), **Momentum Movers**, **Balanced Multi-Sector** (≤2/sector), **High-Conviction** (committed tier). Each returns holdings + weights + stats. |
| `GET /api/portfolio/fund-baskets/invest?basket=&amount=` | Allocate `amount` across a basket by weight as protective LIMIT buys (sized to real prices). |

Shown on the Portfolio **AI Funds** tab; one-click Invest reuses the order confirm modal.

## Sector mapping

`app/utils/sector_map.py` builds a symbol→industry map from NSE index-constituent
CSVs (NIFTY Total Market / 500 / mid / small / micro-cap — they carry an Industry
column the equity master lacks), cached daily in Redis. `sector_of()` resolves
NSE industry → curated stock master → `Other`. Names outside the NSE index lists
are filled via Yahoo `assetProfile` (`POST /api/stocks/directory/backfill-sectors`,
persisted), taking coverage toward 100%. Used by sector-exposure, fund-baskets and
the All Stocks directory.

## AI portfolio intelligence

Higher-level planning/analytics on the live holdings. All are AI/quant-driven and
degrade gracefully (rule-based fallbacks where an LLM is used).

| Method & path | Description |
|---|---|
| `GET /api/portfolio/health` | **Health Score** — a 0-100 score + grade from a weighted multi-factor model (diversification, concentration, sector balance, holding quality via live AI grades, performance, drawdown) with issues + top fixes. |
| `GET /api/portfolio/sip-planner` | **Goal planner** — `goal_amount`, `years`, `risk`, `current_corpus`, `monthly`. Returns required SIP (or projected corpus), a year-by-year projection with optimistic/pessimistic bands, a risk-based equity/debt/gold allocation and per-sleeve fund routing. |
| `GET /api/portfolio/tax-harvest` | **Tax-loss harvesting** — unrealised gains/losses, harvest candidates, potential offset and estimated LTCG tax saved (12.5% over the ₹1.25L exemption), ELSS/80C tips. *Buy dates aren't in Groww's API, so LTCG/STCG isn't split — guidance only.* |
| `GET /api/portfolio/benchmark` | Value-weighted portfolio **1M/3M/1Y returns vs NIFTY 50** (Yahoo) with per-period **alpha**. |
| `GET /api/portfolio/advisor` | **AI Advisor** — LLM-generated plain-English insights synthesising health + sector + benchmark + tax (rule-based fallback). |
| `GET /api/portfolio/risk-lab` | **AI Risk Lab** (one pass over holdings' history + NIFTY): true (correlation) diversification score + hidden-concentration pairs; scenario **stress-test** (per-holding beta → market/sector/rate shocks + fragile names); **ATR smart exits** (stop/target/trailing + scan-downgrade flags); **dividend income forecast** (trailing divs → income + yield). |

The AI here is multi-factor scoring + projection/allocation models + the live AI
scan grades — deterministic finance maths and the existing scanner intelligence
rather than a separately trained model.
