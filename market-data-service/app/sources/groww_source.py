"""Groww API data source — thin wrapper reusing auth logic from the main backend."""

import asyncio
import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

BASE_URL = "https://api.groww.in/v1"


class GrowwSource:
    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._lock = asyncio.Lock()

    def _checksum(self) -> tuple[str, int]:
        ts = int(time.time())
        digest = hashlib.sha256(f"{self._api_secret}{ts}".encode()).hexdigest()
        return digest, ts

    async def _ensure_token(self) -> Optional[str]:
        async with self._lock:
            if self._token and self._token_expiry and datetime.now() < self._token_expiry:
                return self._token
            try:
                checksum, ts = self._checksum()
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{BASE_URL}/token/api/access",
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "X-API-VERSION": "1.0",
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                        json={"key_type": "approval", "checksum": checksum, "timestamp": ts},
                    )
                    if resp.status_code not in (200, 201):
                        return None
                    data = resp.json()
                    payload = data.get("payload", data)
                    token = payload.get("access_token") or payload.get("token") or payload.get("accessToken")
                    if token:
                        self._token = token
                        self._token_expiry = (datetime.now() + timedelta(days=1)).replace(
                            hour=6, minute=0, second=0, microsecond=0
                        )
            except Exception:
                return None
            return self._token

    async def _get(self, path: str, params: dict | None = None) -> Optional[dict]:
        token = await self._ensure_token()
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{BASE_URL}{path}",
                    headers={"Authorization": f"Bearer {token}", "X-API-VERSION": "1.0", "Accept": "application/json"},
                    params=params,
                )
                if resp.status_code == 401:
                    async with self._lock:
                        self._token = None
                    token = await self._ensure_token()
                    if not token:
                        return None
                    resp = await client.get(
                        f"{BASE_URL}{path}",
                        headers={"Authorization": f"Bearer {token}", "X-API-VERSION": "1.0", "Accept": "application/json"},
                        params=params,
                    )
                if resp.status_code != 200:
                    return None
                return resp.json()
        except Exception:
            return None

    async def get_ltp(self, symbols: list[str], exchange: str = "NSE") -> dict:
        exchange_symbols = ",".join(f"{exchange}_{s}" for s in symbols)
        data = await self._get("/live-data/ltp", {"segment": "CASH", "exchange_symbols": exchange_symbols})
        if not data:
            return {}
        return data.get("payload", {})

    async def get_quote(self, symbol: str, exchange: str = "NSE") -> Optional[dict]:
        data = await self._get(
            "/live-data/quote",
            {"exchange": exchange, "segment": "CASH", "trading_symbol": symbol},
        )
        if not data:
            return None
        return data.get("payload", data)

    async def get_candles(
        self,
        symbol: str,
        interval_minutes: int,
        start: datetime,
        end: datetime,
        exchange: str = "NSE",
    ) -> list[dict]:
        data = await self._get(
            "/historical/candle/range",
            {
                "exchange": exchange,
                "segment": "CASH",
                "trading_symbol": symbol,
                "start_time": int(start.timestamp()),
                "end_time": int(end.timestamp()),
                "interval_in_minutes": interval_minutes,
            },
        )
        if not data:
            return []
        payload = data.get("payload", {})
        candles = payload.get("candles", payload) if isinstance(payload, dict) else payload
        return candles if isinstance(candles, list) else []

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_secret)
