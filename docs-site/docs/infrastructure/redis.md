---
id: redis
title: Redis Key Reference
sidebar_position: 2
---

# Redis Key Reference

| Key Pattern | Writer | Reader(s) | TTL | Purpose |
|---|---|---|---|---|
| `tick:{SYMBOL}` | market-data-service | backend/stocks.py, backend/predictions.py | 120 s | Latest price tick |
| `candle:{SYMBOL}:{interval}` | market-data-service | backend/stocks.py | 300 s | Recent OHLCV candles |
| `ensemble:{SYMBOL}` | ensemble-engine [line 134](https://github.com/AbhinavShah421/NeuradeX/blob/main/ensemble-engine/app/main.py#L134) | backend/predictions.py | 300 s | Latest ensemble decision |
| `otp:{email}` | backend/auth.py | backend/auth.py | 300 s | OTP verification code |
| `signup:{email}` | backend/auth.py | backend/auth.py | 600 s | Pending signup state |
| `session:{token}` | backend/auth.py | backend/middleware | varies | JWT blacklist |
| `macro:{key}` | macro-agent | macro-agent | 3600 s | Cached macro indicators |
| `ai_engine:watchlist` | stock-scanner | backend `/watchlist`, autopilot | 24 h | Live ranked AI watchlist |
| `ai_engine:watchlist:premarket:{date}` | stock-scanner | stock-scanner (eval) | 3 d | Morning snapshot graded after close |
| `ai_engine:scan_calibration` | stock-scanner | stock-scanner | 120 d | Learned per-action confidence multipliers |
| `ai_engine:scan_eval:latest` / `:{date}` | stock-scanner | backend `/scan-evaluation` | 30–90 d | Post-market signal-score grade |
| `ai_engine:autopilot_enabled` | backend/autopilot | backend/autopilot | 30 d | Autopilot ON/OFF flag |
| `ai_engine:autopilot:started:{date}` | backend/autopilot | backend/autopilot | 2 d | Symbols autopilot has opened today (one-per-day guard) |
| `session:{id}` (session store) | backend/sessions | backend/sessions, runner loop | — | Server-side live/paper session state |
