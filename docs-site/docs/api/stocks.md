---
id: stocks
title: Stocks
sidebar_position: 2
---

# Stocks — `/api/stocks`

**File:** [`backend/app/api/stocks.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/stocks.py)

| Method | Path | Line | Data Source |
|---|---|---|---|
| `GET` | `/api/stocks/` | [87](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/stocks.py#L87) | Redis `tick:{symbol}` |
| `GET` | `/api/stocks/{symbol}` | [136](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/stocks.py#L136) | Redis `tick:{symbol}` |
| `GET` | `/api/stocks/{symbol}/candlesticks` | [159](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/stocks.py#L159) | PostgreSQL `ohlcv` table |
| `GET` | `/api/stocks/{symbol}/sentiment` | [227](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/stocks.py#L227) | MongoDB `sentiment_scores` |
| `GET` | `/api/stocks/directory/list` | [248](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/stocks.py#L248) | Static NSE/BSE master list |
| `POST` | `/api/stocks/directory/prices` | [295](https://github.com/AbhinavShah421/NeuradeX/blob/main/backend/app/api/stocks.py#L295) | Redis `tick:{symbol}` (batch) |

## Query Parameters

### `GET /api/stocks/{symbol}/candlesticks`

| Param | Type | Default | Description |
|---|---|---|---|
| `interval` | string | `1h` | Candle interval (`1m`, `5m`, `15m`, `1h`, `1d`) |
| `limit` | int | `100` | Number of candles to return |
