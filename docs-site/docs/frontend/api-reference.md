---
id: api-reference
title: Frontend API Quick Reference
sidebar_label: API Quick Reference
---

# Frontend API Quick Reference

Complete list of every API call the frontend makes, which page triggers it, and why.

## Auth APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| POST | `/api/auth/login` | Login | Authenticate user, get JWT |
| POST | `/api/auth/signup/send-otp` | Signup (Step 1) | Send OTP to email/phone |
| POST | `/api/auth/signup/verify-otp` | Signup (Step 2) | Verify 6-digit OTP |
| POST | `/api/auth/signup/complete` | Signup (Step 3) | Finish signup with broker |
| GET | `/api/auth/me` | ProtectedRoute, Login, Signup | Load user profile after auth |

## Stock / Market Data APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| GET | `/api/stocks/` | Dashboard | Fetch watchlist stocks |
| GET | `/api/stocks/{symbol}` | Stock Detail | Single stock quote + fundamentals |
| GET | `/api/stocks/{symbol}/sentiment` | Stock Detail | News sentiment analysis |
| GET | `/api/stocks/directory/list` | Dashboard (Directory tab) | Paginated searchable stock list |
| POST | `/api/stocks/directory/prices` | Dashboard (Directory tab) | Batch live prices for current page |

## Prediction APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| GET | `/api/predictions/{symbol}` | Dashboard, Stock Detail, Predictions | Current AI prediction |
| POST | `/api/predictions/{symbol}/custom-analysis` | Predictions | Custom timeframe deep analysis |
| GET | `/api/predictions/{symbol}/history` | Stock Detail | Last 20 predictions with outcomes |
| GET | `/api/predictions/accuracy/stats` | Dashboard | Overall model accuracy metrics |

## Portfolio APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| GET | `/api/portfolio/` | Portfolio | Holdings with P&L |
| GET | `/api/portfolio/performance` | Portfolio | Sharpe, drawdown, beta, alpha |
| GET | `/api/portfolio/alerts` | Portfolio | Active price alerts |

## Orders API

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| POST | `/api/orders/` | Stock Detail | Place live trade order |

## AI Agent (Ollama) APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| GET | `/api/agent/stocks` | AI Agent | Symbols available for LLM analysis |
| GET | `/api/agent/models` | AI Agent | Available Ollama models |
| POST | `/api/agent/analyze/{symbol}` | AI Agent | Run LLM analysis on stock |

## Backtest APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| GET | `/api/backtest/strategies` | Backtest | Available strategy definitions |
| POST | `/api/backtest/run` | Backtest | Run historical strategy simulation |
| GET | `/api/backtest/live-signal/{symbol}` | Backtest | Get real-time strategy signal |
| GET | `/api/backtest/intraday-candles/{symbol}` | Backtest (Autopilot) | 1-min candles for replay |
| POST | `/api/backtest/progressive/start` | Backtest (Autopilot) | Start replay session |
| POST | `/api/backtest/progressive/step` | Backtest (Autopilot) | Advance one candle + AI decision |

## Paper Trading APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| GET | `/api/paper-trading/status` | Paper Trading, AI Engine | Market open/closed status |
| POST | `/api/paper-trading/start` | Paper Trading, AI Engine | Create paper trading session |
| GET | `/api/paper-trading/tick/{symbol}` | Paper Trading | Live quote + AI signal |
| POST | `/api/paper-trading/step` | Paper Trading | Advance agent by one candle |
| POST | `/api/paper-trading/place-order` | Paper Trading | Execute paper trade |

## AI Engine APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| POST | `/api/ai-engine/analyze` | AI Engine | Multi-agent ensemble vote |
| POST | `/api/ai-engine/outcome` | AI Engine | Record trade result for learning |
| GET | `/api/ai-engine/performance` | AI Engine | Agent weights + accuracy |
| GET | `/api/ai-engine/history` | AI Engine | Past ensemble predictions |

## External Service APIs

| Method | Endpoint | Page | Functionality |
|---|---|---|---|
| GET | `http://localhost:8012/stats` | Orders | Aggregate trade statistics (feedback-service) |
| GET | `http://localhost:8012/trades` | Orders | Full trade history (feedback-service) |
| GET | `/api/mlflow/registered-models/search` | Model Registry | List MLflow registered models |
| GET | `/api/mlflow/runs/get` | Model Registry | Fetch run metrics per model |

---

## Page → API Summary

| Page | # API Calls | Key Endpoints |
|---|---|---|
| Login | 2 | `/api/auth/login`, `/api/auth/me` |
| Signup | 4 | `/api/auth/signup/*`, `/api/auth/me` |
| Dashboard | 5 | `/api/stocks/`, `/api/predictions/*`, `/api/stocks/directory/*` |
| Stock Detail | 5 | `/api/stocks/{s}`, `/api/predictions/{s}`, `/api/orders/` |
| Portfolio | 3 | `/api/portfolio/*` |
| Predictions | 2 | `/api/predictions/{s}` (×8), `/api/predictions/{s}/custom-analysis` |
| AI Engine | 5 | `/api/ai-engine/*`, `/api/paper-trading/start` |
| AI Agent | 3 | `/api/agent/*` |
| Backtest | 6 | `/api/backtest/*` |
| Paper Trading | 5 | `/api/paper-trading/*` |
| Orders | 2 | `http://localhost:8012/*` |
| Model Registry | 2 | `/api/mlflow/*` |
