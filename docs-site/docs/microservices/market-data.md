---
id: market-data
title: Market Data Service
sidebar_position: 1
---

# Market Data Service — Port 8001

**Entry point:** [`market-data-service/app/main.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/main.py)

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `GET` | `/tick/{symbol}` | Read latest tick from Redis |

## Background Jobs

| Loop | Interval | What it does |
|---|---|---|
| Price tick | `TICK_INTERVAL_SECONDS` (default 60s) | Polls Groww / Yahoo Finance → writes Redis → publishes to `market.data` |
| News ingestion | `NEWS_INTERVAL_SECONDS` (default 300s) | Polls NewsAPI → stores in MongoDB `news` → publishes `news_ingested` notification |

## RabbitMQ — Publishes

**File:** [`market-data-service/app/services/rabbitmq_publisher.py`](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_publisher.py)

| Exchange | Type | Payload | Line |
|---|---|---|---|
| `market.data` | fanout | `{symbol, price, timestamp, open, high, low, close, volume, exchange}` | [41](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_publisher.py#L41) |
| `notifications` | fanout | `{event: "news_ingested", article_count}` | [58](https://github.com/AbhinavShah421/NeuradeX/blob/main/market-data-service/app/services/rabbitmq_publisher.py#L58) |

## Redis — Writes

| Key | Value | TTL |
|---|---|---|
| `tick:{SYMBOL}` | `{ltp, volume, timestamp, open, high, low, close}` | 120 s |
| `candle:{SYMBOL}:{interval}` | JSON array of recent candles | 300 s |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WATCHLIST` | 15 NSE stocks | Comma-separated symbols to track |
| `TICK_INTERVAL_SECONDS` | `60` | Price poll frequency |
| `NEWS_INTERVAL_SECONDS` | `300` | News poll frequency |
