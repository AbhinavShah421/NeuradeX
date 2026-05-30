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
