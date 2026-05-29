"""
Groww Trading API client with automatic token management.

Auth flow:
  POST /v1/token/api/access
    Authorization: Bearer {GROWW_API_KEY}
    Body: { key_type, checksum: SHA256(secret+ts), timestamp }
  → returns short-lived access_token (valid until 6 AM next day)

All subsequent calls use: Authorization: Bearer {access_token}

Token is cached in Redis so it survives backend restarts.
On 403 (TOTP session not approved), the client enters FAILED state and
all API calls fall through to simulation data. Use force_refresh() or
update_credentials() to recover.
"""

import asyncio
import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.groww.in/v1"
_REDIS_TOKEN_KEY = "groww:access_token"
_REDIS_EXPIRY_KEY = "groww:token_expiry"

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"


def _log_groww_call(
    *,
    http_method: str,
    endpoint: str,
    params: Optional[dict],
    status_code: Optional[int],
    duration_ms: float,
    error: Optional[str],
    extra: Optional[dict] = None,
) -> None:
    """Emit a structured log record for every Groww HTTP call."""
    fields: dict = {
        "log_type": "groww_api_call",
        "http_method": http_method,
        "groww_endpoint": endpoint,
        "groww_params": str(params or {}),
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2),
        "success": error is None,
    }
    if error:
        fields["error"] = error
    if extra:
        fields.update(extra)

    if error:
        logger.error("Groww API call failed: %s %s", http_method, endpoint, extra=fields)
    else:
        logger.info("Groww API call: %s %s", http_method, endpoint, extra=fields)


class GrowwClient:
    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._lock = asyncio.Lock()
        self._status: str = STATUS_UNKNOWN
        self._failure_reason: str = ""
        self._failure_count: int = 0
        self._last_attempt: Optional[datetime] = None

    # ── Redis token persistence ───────────────────────────────────────────────

    async def _redis_load(self) -> bool:
        try:
            from app.utils.redis_client import cache_get
            token = await cache_get(_REDIS_TOKEN_KEY)
            expiry_str = await cache_get(_REDIS_EXPIRY_KEY)
            if token and expiry_str:
                expiry = datetime.fromisoformat(expiry_str)
                if datetime.now() < expiry:
                    self._access_token = token
                    self._token_expiry = expiry
                    self._status = STATUS_OK
                    logger.info(
                        "Groww token restored from Redis",
                        extra={"log_type": "groww_token", "event": "token_restored", "expires": expiry.isoformat()},
                    )
                    return True
        except Exception as exc:
            logger.debug("Redis token load skipped: %s", exc)
        return False

    async def _redis_save(self) -> None:
        try:
            from app.utils.redis_client import cache_set
            if self._access_token and self._token_expiry:
                ttl = max(60, int((self._token_expiry - datetime.now()).total_seconds()))
                await cache_set(_REDIS_TOKEN_KEY, self._access_token, ttl)
                await cache_set(_REDIS_EXPIRY_KEY, self._token_expiry.isoformat(), ttl)
        except Exception as exc:
            logger.debug("Redis token save skipped: %s", exc)

    async def _redis_clear(self) -> None:
        try:
            from app.utils.redis_client import get_redis
            await get_redis().delete(_REDIS_TOKEN_KEY, _REDIS_EXPIRY_KEY)
        except Exception:
            pass

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _checksum(self) -> tuple[str, int]:
        ts = int(time.time())
        digest = hashlib.sha256(f"{self._api_secret}{ts}".encode()).hexdigest()
        return digest, ts

    async def _refresh_token(self) -> None:
        """Exchange API key for a short-lived access token via Groww token endpoint."""
        self._last_attempt = datetime.now()
        checksum, ts = self._checksum()
        endpoint = "/token/api/access"
        start = time.monotonic()
        status_code: Optional[int] = None
        error: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{BASE_URL}{endpoint}",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "X-API-VERSION": "1.0",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json={"key_type": "approval", "checksum": checksum, "timestamp": ts},
                )
                status_code = resp.status_code

                if resp.status_code == 403:
                    body = resp.text[:300]
                    self._status = STATUS_FAILED
                    self._failure_count += 1
                    self._failure_reason = f"403 — {body}"
                    error = f"403 — {body}"
                    raise ValueError(f"Groww session not approved (403): {body}")

                resp.raise_for_status()
                data = resp.json()
                payload = data.get("payload", data)
                token = (
                    payload.get("access_token")
                    or payload.get("token")
                    or payload.get("accessToken")
                )
                if not token:
                    raise ValueError(f"No access_token in response: {resp.text[:200]}")

                self._access_token = token
                now = datetime.now()
                self._token_expiry = (now + timedelta(days=1)).replace(
                    hour=6, minute=0, second=0, microsecond=0
                )
                self._status = STATUS_OK
                self._failure_count = 0
                self._failure_reason = ""
                await self._redis_save()

                logger.info(
                    "Groww token refreshed",
                    extra={
                        "log_type": "groww_token",
                        "event": "token_refreshed",
                        "expires": self._token_expiry.isoformat(),
                    },
                )

        except ValueError:
            raise
        except Exception as exc:
            self._status = STATUS_FAILED
            self._failure_count += 1
            self._failure_reason = str(exc)
            error = str(exc)
            raise
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            _log_groww_call(
                http_method="POST",
                endpoint=endpoint,
                params=None,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error,
                extra={"event": "token_refresh"},
            )

    async def _token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        async with self._lock:
            if not self._access_token:
                await self._redis_load()

            if not self._access_token or (
                self._token_expiry and datetime.now() >= self._token_expiry
            ):
                await self._refresh_token()

            return self._access_token  # type: ignore[return-value]

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "X-API-VERSION": "1.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ── Status + control ──────────────────────────────────────────────────────

    def get_status(self) -> dict:
        now = datetime.now()
        remaining = None
        if self._token_expiry and self._status == STATUS_OK:
            remaining = max(0, int((self._token_expiry - now).total_seconds()))
        return {
            "status": self._status,
            "token_expiry": self._token_expiry.isoformat() if self._token_expiry else None,
            "time_remaining_seconds": remaining,
            "failure_count": self._failure_count,
            "failure_reason": self._failure_reason,
            "last_attempt": self._last_attempt.isoformat() if self._last_attempt else None,
            "has_token": bool(self._access_token),
        }

    async def force_refresh(self) -> dict:
        async with self._lock:
            self._access_token = None
            self._token_expiry = None
            self._failure_count = 0
            self._failure_reason = ""
            await self._redis_clear()
        try:
            await self._token()
            return {
                "success": True,
                "expires": self._token_expiry.isoformat() if self._token_expiry else None,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def update_credentials(self, api_key: str, api_secret: str) -> dict:
        async with self._lock:
            self._api_key = api_key
            self._api_secret = api_secret
            self._access_token = None
            self._token_expiry = None
            self._failure_count = 0
            self._failure_reason = ""
            await self._redis_clear()
        try:
            await self._token()
            return {
                "success": True,
                "expires": self._token_expiry.isoformat() if self._token_expiry else None,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        token = await self._token()
        start = time.monotonic()
        status_code: Optional[int] = None
        error: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{BASE_URL}{path}", headers=self._headers(token), params=params
                )
                if resp.status_code == 401:
                    async with self._lock:
                        self._access_token = None
                    token = await self._token()
                    resp = await client.get(
                        f"{BASE_URL}{path}", headers=self._headers(token), params=params
                    )
                status_code = resp.status_code
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            _log_groww_call(
                http_method="GET",
                endpoint=path,
                params=params,
                status_code=status_code,
                duration_ms=(time.monotonic() - start) * 1000,
                error=error,
            )

    async def _post(self, path: str, body: dict) -> dict:
        token = await self._token()
        start = time.monotonic()
        status_code: Optional[int] = None
        error: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{BASE_URL}{path}", headers=self._headers(token), json=body
                )
                if resp.status_code == 401:
                    async with self._lock:
                        self._access_token = None
                    token = await self._token()
                    resp = await client.post(
                        f"{BASE_URL}{path}", headers=self._headers(token), json=body
                    )
                status_code = resp.status_code
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            _log_groww_call(
                http_method="POST",
                endpoint=path,
                params=body,
                status_code=status_code,
                duration_ms=(time.monotonic() - start) * 1000,
                error=error,
            )

    # ── Public API methods ────────────────────────────────────────────────────

    async def get_ltp(self, symbols: list[str], exchange: str = "NSE") -> dict:
        exchange_symbols = ",".join(f"{exchange}_{s}" for s in symbols)
        data = await self._get(
            "/live-data/ltp",
            {"segment": "CASH", "exchange_symbols": exchange_symbols},
        )
        return data.get("payload", data)

    async def get_quote(self, symbol: str, exchange: str = "NSE") -> dict:
        data = await self._get(
            "/live-data/quote",
            {"exchange": exchange, "segment": "CASH", "trading_symbol": symbol},
        )
        return data.get("payload", data)

    async def get_historical(
        self,
        symbol: str,
        interval_minutes: int,
        start: datetime,
        end: datetime,
        exchange: str = "NSE",
    ) -> list:
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
        payload = data.get("payload", {})
        return payload.get("candles", payload) if isinstance(payload, dict) else payload

    async def get_holdings(self) -> list:
        data = await self._get("/holdings/user")
        payload = data.get("payload", {})
        return payload.get("holdings", payload) if isinstance(payload, dict) else payload

    async def get_positions(self) -> list:
        data = await self._get("/positions/user")
        payload = data.get("payload", {})
        return payload.get("positions", payload) if isinstance(payload, dict) else payload

    async def place_order(
        self,
        symbol: str,
        quantity: int,
        transaction_type: str,
        order_type: str = "MARKET",
        price: float = 0.0,
        product: str = "CNC",
        exchange: str = "NSE",
    ) -> dict:
        body: dict = {
            "trading_symbol": symbol,
            "quantity": quantity,
            "exchange": exchange,
            "segment": "CASH",
            "product": product,
            "order_type": order_type,
            "transaction_type": transaction_type,
        }
        if order_type == "LIMIT" and price > 0:
            body["price"] = price
        data = await self._post("/order/create", body)
        return data.get("payload", data)


# ── Singleton factory ─────────────────────────────────────────────────────────

_client: Optional[GrowwClient] = None


def get_groww_client() -> Optional[GrowwClient]:
    return _client


def init_groww_client(api_key: str, api_secret: str) -> GrowwClient:
    global _client
    _client = GrowwClient(api_key, api_secret)
    logger.info(
        "Groww API client initialized",
        extra={"log_type": "groww_token", "event": "client_init"},
    )
    return _client
