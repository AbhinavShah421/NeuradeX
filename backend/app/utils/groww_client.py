"""
Groww Trading API client with automatic token management.

Auth flow — Groww issues two kinds of API key, and the body differs:

  POST /v1/token/api/access
    Authorization: Bearer {GROWW_API_KEY}
    Body (approval): { key_type: "approval", checksum: SHA256(secret+ts), timestamp }
    Body (totp):     { key_type: "totp", totp: "123456" }
  → returns short-lived access_token (valid until 6 AM next day)

An *approval* key requires a manual session approval in the Groww app every
day; until someone taps it the token endpoint returns 403 and there is no way
around that from here — it is the security control, not a bug. A *totp* key
derives its 6-digit code locally from a base32 seed, so it refreshes
unattended and is the right choice for a headless deployment.

All subsequent calls use: Authorization: Bearer {access_token}

Token is cached in Redis so it survives backend restarts.
On 403 the client enters FAILED state and all API calls fall through to
simulation data. Use force_refresh() or update_credentials() to recover.
"""

import asyncio
import hashlib
import os
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.groww.in/v1"
_REDIS_TOKEN_KEY = "groww:access_token"
_REDIS_EXPIRY_KEY = "groww:token_expiry"

# Groww expires every access token at 06:00 IST. Our containers run on UTC, so
# every expiry calculation below is done in EXPLICIT IST — a naive
# `datetime.now()` here is a 5.5-hour lie.
#
# Why this is spelled out — 2026-08-05: expiry was stored as
# `(datetime.now() + 1 day).replace(hour=6)` with a naive, UTC clock, so a token
# minted on the 4th recorded "expires 06:00" meaning 06:00 UTC = 11:30 IST. The
# real death was 06:00 IST = 00:30 UTC. For the 5.5 hours in between — which is
# exactly the pre-open and first two hours of the session — `_token_is_fresh()`
# said fresh, the keeper skipped its refresh every tick, and groww-feed-service
# crash-looped on "Authentication failed" against a token that had died before
# dawn. Manually refreshing was the only thing that cleared it, every morning.
IST = timezone(timedelta(hours=5, minutes=30))
_TOKEN_EXPIRY_HOUR_IST = int(os.getenv("GROWW_TOKEN_EXPIRY_HOUR_IST", "6"))


def _now_ist() -> datetime:
    return datetime.now(IST)


def _next_expiry_ist(now: Optional[datetime] = None) -> datetime:
    """The next 06:00 IST strictly after `now` — when Groww kills this token.

    Not simply "tomorrow at 6": a token minted at 05:00 IST dies an hour later,
    not 25 hours later, and treating it as day-long would reintroduce exactly
    the believed-fresh-but-dead window this function exists to close.
    """
    now = now or _now_ist()
    expiry = now.replace(hour=_TOKEN_EXPIRY_HOUR_IST, minute=0, second=0, microsecond=0)
    if expiry <= now:
        expiry += timedelta(days=1)
    return expiry


def _parse_expiry(raw: str) -> Optional[datetime]:
    """Read a stored expiry back as an IST-aware datetime.

    A naive value is one written by the pre-2026-08-05 code, whose meaning was
    wrong by 5.5 hours. There is no way to salvage it, and trusting it is what
    caused the outage — so treat it as expired and let a fresh token be minted.
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(IST)

# Client-side rate limit — kept safely UNDER Groww's published limits so we never
# get a 429. Groww limits are per *type* and shared (Live Data: 10/s, 300/min;
# Non-Trading: 20/s, 500/min). A single conservative global budget covers all.
_RL_PER_SEC = 5
_RL_PER_MIN = 200
# Don't retry a failing token refresh more often than this (avoids hammering the
# token endpoint when Groww is throttling or credentials are bad).
_TOKEN_RETRY_COOLDOWN = 60.0
# A 429 on the token endpoint means Groww is hard-throttling it — short retries
# only keep its shared penalty window from ever resetting. Back off long.
_TOKEN_RATELIMIT_COOLDOWN = 1800.0   # 30 min after a 429
_MAX_TOKEN_COOLDOWN = 1800.0
# A 403 on an *approval* key means a human has to tap approve in the Groww app,
# so retrying soon is pointless — wait it out. On a *totp* key the same 403 is
# usually transient (clock skew, a code consumed right on the period boundary),
# and blocking for half an hour would strand us for the rest of the session.
_TOTP_FORBIDDEN_COOLDOWN = 120.0

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

KEY_TYPE_APPROVAL = "approval"
KEY_TYPE_TOTP = "totp"


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
    def __init__(self, api_key: str, api_secret: str, key_type: str = KEY_TYPE_APPROVAL):
        self._api_key = api_key
        # Approval secret, or the base32 TOTP seed when key_type is "totp".
        self._api_secret = api_secret
        self._key_type = (key_type or KEY_TYPE_APPROVAL).strip().lower()
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._lock = asyncio.Lock()
        self._status: str = STATUS_UNKNOWN
        self._failure_reason: str = ""
        self._failure_count: int = 0
        self._last_attempt: Optional[datetime] = None
        # Rate limiter state
        self._req_times: deque = deque()
        self._rl_lock = asyncio.Lock()
        self._last_refresh_fail: float = 0.0
        self._cooldown_until: float = 0.0     # monotonic time before which we won't retry
        self._rate_limited: bool = False      # last failure was a 429 (hard throttle)

    # ── Rate limiter ───────────────────────────────────────────────────────────

    async def _acquire(self) -> None:
        """Block until a request slot is free, keeping us under Groww's limits.
        Serialised so bursts are smoothed into a steady, compliant rate."""
        async with self._rl_lock:
            while True:
                now = time.monotonic()
                while self._req_times and now - self._req_times[0] > 60.0:
                    self._req_times.popleft()
                in_min = len(self._req_times)
                in_sec = sum(1 for t in self._req_times if now - t < 1.0)
                if in_sec < _RL_PER_SEC and in_min < _RL_PER_MIN:
                    self._req_times.append(now)
                    return
                wait = 0.05
                if in_sec >= _RL_PER_SEC:
                    oldest_sec = min(t for t in self._req_times if now - t < 1.0)
                    wait = max(wait, 1.0 - (now - oldest_sec))
                if in_min >= _RL_PER_MIN:
                    wait = max(wait, 60.0 - (now - self._req_times[0]))
                await asyncio.sleep(min(wait, 2.0))

    # ── Redis token persistence ───────────────────────────────────────────────

    async def _redis_load(self) -> bool:
        try:
            from app.utils.redis_client import cache_get
            token = await cache_get(_REDIS_TOKEN_KEY)
            expiry_str = await cache_get(_REDIS_EXPIRY_KEY)
            if token and expiry_str:
                expiry = _parse_expiry(expiry_str)
                if expiry and _now_ist() < expiry:
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
                ttl = max(60, int((self._token_expiry - _now_ist()).total_seconds()))
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

    async def _auth_body(self) -> dict:
        """Build the token-request body for whichever key type is configured."""
        if self._key_type == KEY_TYPE_TOTP:
            try:
                import pyotp
            except ImportError as exc:   # pragma: no cover - deployment guard
                # Without pyotp there is no way to mint a code, and the generic
                # handler would bury this as a token failure. Name it, because
                # the fix is a rebuild, not anything to do with the credentials.
                raise RuntimeError(
                    "pyotp is not installed, so a TOTP key cannot be used — "
                    "rebuild the backend image (it is in requirements.txt)"
                ) from exc

            # A code minted in the last moments of its 30s window can expire in
            # flight and come back 403. Wait out the remainder instead of
            # sending one we expect to be rejected.
            into_window = time.time() % 30
            if into_window > 27:
                await asyncio.sleep(30 - into_window + 0.5)
            code = pyotp.TOTP(self._api_secret.strip().replace(" ", "")).now()
            return {"key_type": KEY_TYPE_TOTP, "totp": code}

        checksum, ts = self._checksum()
        return {"key_type": KEY_TYPE_APPROVAL, "checksum": checksum, "timestamp": ts}

    async def _refresh_token(self) -> None:
        """Exchange API key for a short-lived access token via Groww token endpoint."""
        self._last_attempt = _now_ist()
        endpoint = "/token/api/access"
        start = time.monotonic()
        status_code: Optional[int] = None
        error: Optional[str] = None

        try:
            auth_body = await self._auth_body()
            await self._acquire()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{BASE_URL}{endpoint}",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "X-API-VERSION": "1.0",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=auth_body,
                )
                status_code = resp.status_code

                if resp.status_code == 403:
                    body = resp.text[:300]
                    self._status = STATUS_FAILED
                    self._failure_count += 1
                    self._rate_limited = False
                    if self._key_type == KEY_TYPE_TOTP:
                        # Nobody has to do anything for a TOTP key to start
                        # working again, so retry soon — a long backoff here
                        # would strand us for the rest of the session.
                        self._cooldown_until = time.monotonic() + _TOTP_FORBIDDEN_COOLDOWN
                        hint = ("403 on a TOTP key usually means the server clock has drifted "
                                "or the seed is wrong — retrying shortly")
                    else:
                        # An approval key needs a human to tap approve in the
                        # Groww app; retrying before that is pure noise.
                        self._cooldown_until = time.monotonic() + _MAX_TOKEN_COOLDOWN
                        hint = ("approval keys need a daily session approval in the Groww app; "
                                "switch to a TOTP key to refresh unattended")
                    self._failure_reason = f"403 — {body} ({hint})"
                    error = self._failure_reason
                    raise ValueError(f"Groww session not approved (403): {body}")

                # Groww answers a key_type that does not match the key itself
                # with a flat 400 "Invalid type provided". Name it, or it
                # surfaces as an opaque httpx error and reads like an outage.
                if resp.status_code == 400 and "Invalid type provided" in resp.text:
                    other = KEY_TYPE_APPROVAL if self._key_type == KEY_TYPE_TOTP else KEY_TYPE_TOTP
                    self._status = STATUS_FAILED
                    self._failure_count += 1
                    self._rate_limited = False
                    self._cooldown_until = time.monotonic() + _MAX_TOKEN_COOLDOWN
                    self._failure_reason = (
                        f"400 — this API key is not a '{self._key_type}' key. "
                        f"Either set key_type to '{other}', or issue a "
                        f"{self._key_type} key in the Groww dashboard."
                    )
                    error = self._failure_reason
                    raise ValueError(self._failure_reason)

                if not resp.is_success:
                    logger.error(
                        "Groww token endpoint error body: %s", resp.text[:500],
                        extra={"log_type": "groww_token", "event": "token_error_body",
                               "status_code": resp.status_code, "body": resp.text[:500]},
                    )
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
                self._token_expiry = _next_expiry_ist()
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
            self._last_refresh_fail = time.monotonic()
            is_429 = status_code == 429 or "429" in str(exc) or "Too Many Requests" in str(exc)
            if is_429:
                self._rate_limited = True
                self._cooldown_until = time.monotonic() + _TOKEN_RATELIMIT_COOLDOWN
                self._failure_reason = ("Groww token endpoint rate-limited (429) — backing off ~30 min; "
                                        "live data uses the Yahoo fallback meanwhile.")
            else:
                self._rate_limited = False
                cd = min(_TOKEN_RETRY_COOLDOWN * self._failure_count, _MAX_TOKEN_COOLDOWN)
                self._cooldown_until = time.monotonic() + cd
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
                self._token_expiry and _now_ist() >= self._token_expiry
            ):
                # After a failed refresh, wait out the cooldown instead of
                # hammering the token endpoint — repeated hits keep its shared,
                # per-type penalty window from ever resetting (esp. after a 429).
                if self._status == STATUS_FAILED and time.monotonic() < self._cooldown_until:
                    remaining = int(self._cooldown_until - time.monotonic())
                    raise RuntimeError(f"Groww token refresh on cooldown ({remaining}s): {self._failure_reason}")
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
        now = _now_ist()
        remaining = None
        if self._token_expiry and self._status == STATUS_OK:
            remaining = max(0, int((self._token_expiry - now).total_seconds()))
        cooldown_remaining = (max(0, int(self._cooldown_until - time.monotonic()))
                              if self._status == STATUS_FAILED else 0)
        return {
            "status": self._status,
            "key_type": self._key_type,
            # An approval key cannot refresh without someone tapping approve in
            # the Groww app; the UI uses this to say so instead of just failing.
            "unattended": self._key_type == KEY_TYPE_TOTP,
            "token_expiry": self._token_expiry.isoformat() if self._token_expiry else None,
            "time_remaining_seconds": remaining,
            "failure_count": self._failure_count,
            "failure_reason": self._failure_reason,
            "rate_limited": self._rate_limited,
            "cooldown_remaining_seconds": cooldown_remaining,
            "last_attempt": self._last_attempt.isoformat() if self._last_attempt else None,
            "has_token": bool(self._access_token),
        }

    async def force_refresh(self) -> dict:
        async with self._lock:
            # Respect a 429 backoff even on a manual refresh — forcing it only
            # re-triggers Groww's throttle and keeps its penalty window from ever
            # resetting. Tell the user to wait it out (live data uses Yahoo).
            if self._rate_limited and time.monotonic() < self._cooldown_until:
                remaining = int(self._cooldown_until - time.monotonic())
                return {
                    "success": False, "rate_limited": True,
                    "cooldown_remaining_seconds": remaining,
                    "error": (f"Groww is rate-limiting the token endpoint (429). "
                              f"Auto-retry in ~{max(1, remaining // 60)} min; live data "
                              f"uses the Yahoo fallback meanwhile."),
                }
            self._access_token = None
            self._token_expiry = None
            self._failure_count = 0
            self._failure_reason = ""
            self._status = STATUS_UNKNOWN
            self._last_refresh_fail = 0.0
            self._cooldown_until = 0.0
            self._rate_limited = False
            await self._redis_clear()
        try:
            await self._token()
            return {
                "success": True,
                "expires": self._token_expiry.isoformat() if self._token_expiry else None,
            }
        except Exception as exc:
            return {"success": False, "error": self._failure_reason or str(exc)}

    async def update_credentials(
        self, api_key: str, api_secret: str, key_type: Optional[str] = None
    ) -> dict:
        async with self._lock:
            self._api_key = api_key
            self._api_secret = api_secret
            if key_type:
                self._key_type = key_type.strip().lower()
            self._access_token = None
            self._token_expiry = None
            self._failure_count = 0
            self._failure_reason = ""
            # New credentials invalidate every reason we were backing off. Without
            # this, swapping a rejected approval key for a working TOTP key stays
            # blocked until the old key's 30-minute cooldown expires.
            self._status = STATUS_UNKNOWN
            self._cooldown_until = 0.0
            self._rate_limited = False
            self._last_refresh_fail = 0.0
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

    async def _get(self, path: str, params: Optional[dict] = None,
                   refresh_on_401: bool = True) -> dict:
        token = await self._token()
        start = time.monotonic()
        status_code: Optional[int] = None
        error: Optional[str] = None

        try:
            await self._acquire()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{BASE_URL}{path}", headers=self._headers(token), params=params
                )
                # A 401 on entitlement-gated endpoints (live data) is NOT a token
                # expiry — don't wipe the shared token in that case.
                if resp.status_code == 401 and refresh_on_401:
                    async with self._lock:
                        self._access_token = None
                    token = await self._token()
                    await self._acquire()
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
            await self._acquire()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{BASE_URL}{path}", headers=self._headers(token), json=body
                )
                if resp.status_code == 401:
                    async with self._lock:
                        self._access_token = None
                    token = await self._token()
                    await self._acquire()
                    resp = await client.post(
                        f"{BASE_URL}{path}", headers=self._headers(token), json=body
                    )
                status_code = resp.status_code
                if not resp.is_success:
                    # Surface Groww's actual error body (e.g. "trading not enabled",
                    # "unauthorized") instead of httpx's generic status message.
                    raise RuntimeError(f"Groww {resp.status_code}: {resp.text[:400]}")
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
            refresh_on_401=False,
        )
        return data.get("payload", data)

    async def get_quote(self, symbol: str, exchange: str = "NSE") -> dict:
        data = await self._get(
            "/live-data/quote",
            {"exchange": exchange, "segment": "CASH", "trading_symbol": symbol},
            refresh_on_401=False,
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
        validity: str = "DAY",
    ) -> dict:
        body: dict = {
            "trading_symbol": symbol,
            "quantity": quantity,
            "validity": validity,        # required by Groww (GA001 if missing)
            # Client-generated idempotency key — Groww requires a non-empty
            # 8–20 char alphanumeric order_reference_id (GA001 if missing).
            "order_reference_id": uuid.uuid4().hex[:20],
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

    async def get_orders(self, segment: str = "CASH") -> list:
        """The day's order book from Groww."""
        data = await self._get("/order/list", {"segment": segment, "page": 0, "page_size": 50})
        payload = data.get("payload", data)
        if isinstance(payload, dict):
            return payload.get("order_list") or payload.get("orders") or []
        return payload if isinstance(payload, list) else []

    async def cancel_order(self, groww_order_id: str, segment: str = "CASH") -> dict:
        """Cancel a pending order by its Groww order id."""
        data = await self._post("/order/cancel", {"groww_order_id": groww_order_id, "segment": segment})
        return data.get("payload", data)


# ── Singleton factory ─────────────────────────────────────────────────────────

_client: Optional[GrowwClient] = None


def get_groww_client() -> Optional[GrowwClient]:
    return _client


def init_groww_client(
    api_key: str, api_secret: str, key_type: str = KEY_TYPE_APPROVAL
) -> GrowwClient:
    global _client
    _client = GrowwClient(api_key, api_secret, key_type)
    logger.info(
        "Groww API client initialized (key_type=%s)", _client._key_type,
        extra={"log_type": "groww_token", "event": "client_init",
               "key_type": _client._key_type},
    )
    return _client


# ── Token keeper ──────────────────────────────────────────────────────────────

# Groww tokens die at 06:00 IST, so every session starts needing a new one. Keep one
# in hand across the whole trading window rather than minting on the first
# candle fetch, where a failure costs live ticks and silently drops the day onto
# the Yahoo fallback.
#
# A single pre-open attempt is not enough: if it fails, the feed sits tokenless
# for the entire session. Re-checking on a short interval means any transient
# failure costs one interval, not the day.
_KEEP_FROM_IST = (int(os.getenv("GROWW_KEEP_FROM_HOUR_IST", "8")),
                  int(os.getenv("GROWW_KEEP_FROM_MIN_IST", "30")))
_KEEP_UNTIL_IST = (int(os.getenv("GROWW_KEEP_UNTIL_HOUR_IST", "15")),
                   int(os.getenv("GROWW_KEEP_UNTIL_MIN_IST", "45")))
_KEEPER_INTERVAL = float(os.getenv("GROWW_KEEPER_INTERVAL_SECS", "300"))
# Re-mint before expiry rather than at it, so a token never lapses mid-candle.
_REFRESH_MARGIN = timedelta(minutes=20)


def _in_keep_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=_KEEP_FROM_IST[0], minute=_KEEP_FROM_IST[1], second=0, microsecond=0)
    end = now.replace(hour=_KEEP_UNTIL_IST[0], minute=_KEEP_UNTIL_IST[1], second=0, microsecond=0)
    return start <= now <= end


def _token_is_fresh(client: "GrowwClient") -> bool:
    """True when the cached token will still be valid a safe margin from now."""
    if not client._access_token or not client._token_expiry:
        return False
    return _now_ist() < client._token_expiry - _REFRESH_MARGIN


async def _shared_token_present() -> bool:
    """Whether a token still exists in Redis, the shared source of truth.

    Expiry arithmetic only catches a token dying on schedule. A token can also
    die early — revoked, invalidated server-side, or rejected for a reason we
    cannot see from here — and then the in-process copy looks perfectly fresh
    while every consumer gets 'Authentication failed'. groww-feed-service, which
    is the first to actually find out, deletes the key; this is how the keeper
    notices and re-mints instead of guarding a corpse until its nominal expiry.

    Errors return True (assume present): a Redis blip must not trigger a token
    re-mint storm.
    """
    try:
        from app.utils.redis_client import cache_get
        return bool(await cache_get(_REDIS_TOKEN_KEY))
    except Exception:
        return True


async def _load_creds_from_db() -> Optional["GrowwClient"]:
    """Re-read the broker credentials from Postgres and build the client.

    Startup loads them exactly once and swallows a failure with a warning. That
    is one attempt against a database that, on a host reboot, may not be
    accepting connections yet — compose's depends_on ordering is only honoured
    by `compose up`, not by the daemon restarting `restart: unless-stopped`
    containers in parallel. Losing that one attempt used to cost the whole
    session: no client, so the keeper idles, so no token, so no live feed, all
    day, with nothing to recover it.
    """
    try:
        from sqlalchemy import text
        from app.database.postgres import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            row = await db.execute(
                text("SELECT broker_api_key, broker_api_secret, COALESCE(broker_key_type, 'approval') "
                     "FROM users WHERE broker_api_key IS NOT NULL AND broker_api_key != '' LIMIT 1")
            )
            creds = row.fetchone()
        if creds and creds[0] and creds[1]:
            return init_groww_client(creds[0], creds[1], creds[2])
    except Exception as exc:
        logger.debug("Groww credential reload failed: %s", exc)
    return None


async def token_keeper_loop() -> None:
    """Hold a valid Groww token for the whole trading window, unattended.

    Ticks on a short interval so a failed mint costs one interval rather than
    the session. The token is shared through Redis, so whichever process keeps
    it serves every other one — this only needs to run in a single role.
    """
    logger.info(
        "Groww token keeper started — %02d:%02d–%02d:%02d IST, every %.0fs",
        *_KEEP_FROM_IST, *_KEEP_UNTIL_IST, _KEEPER_INTERVAL,
        extra={"log_type": "groww_token", "event": "keeper_started"},
    )
    warned_no_creds = False
    warned_approval = False

    while True:
        try:
            now = datetime.now(IST)
            if not _in_keep_window(now):
                await asyncio.sleep(_KEEPER_INTERVAL)
                continue

            client = get_groww_client()
            if client is None:
                # Startup's single credential read may simply have lost a race
                # with Postgres. Try again before writing the session off.
                client = await _load_creds_from_db()
            if client is None:
                if not warned_no_creds:
                    logger.warning(
                        "Groww token keeper idle — no credentials configured",
                        extra={"log_type": "groww_token", "event": "keeper_no_creds"},
                    )
                    warned_no_creds = True
                await asyncio.sleep(_KEEPER_INTERVAL)
                continue
            warned_no_creds = False

            # Another process may have minted it already; Redis is the shared
            # source of truth, so adopt that before spending a token request.
            if not _token_is_fresh(client):
                await client._redis_load()

            if _token_is_fresh(client):
                # ...but "fresh" is only an expiry calculation. If the shared
                # token is gone, someone found it rejected before its nominal
                # expiry, and the in-process copy is worth exactly nothing.
                if await _shared_token_present():
                    await asyncio.sleep(_KEEPER_INTERVAL)
                    continue
                logger.warning(
                    "Groww token disappeared from Redis before its expiry (%s) — "
                    "treating the in-process copy as dead and re-minting",
                    client._token_expiry,
                    extra={"log_type": "groww_token", "event": "keeper_token_revoked"},
                )
                async with client._lock:
                    client._access_token = None
                    client._token_expiry = None

            try:
                await client._token()
                logger.info(
                    "Groww token refreshed by keeper (key_type=%s, expires %s)",
                    client._key_type, client._token_expiry,
                    extra={"log_type": "groww_token", "event": "keeper_refreshed",
                           "key_type": client._key_type},
                )
                warned_approval = False
            except Exception as exc:
                # An approval key genuinely cannot recover on its own. Say it
                # once per outage instead of every interval.
                if client._key_type == KEY_TYPE_APPROVAL:
                    if not warned_approval:
                        logger.error(
                            "Groww token keeper blocked — an approval key needs a manual "
                            "session approval in the Groww app. Switch to a TOTP key to "
                            "run unattended: %s", exc,
                            extra={"log_type": "groww_token", "event": "keeper_needs_approval"},
                        )
                        warned_approval = True
                else:
                    logger.warning(
                        "Groww token keeper refresh failed, retrying in %.0fs: %s",
                        _KEEPER_INTERVAL, exc,
                        extra={"log_type": "groww_token", "event": "keeper_retry"},
                    )

            await asyncio.sleep(_KEEPER_INTERVAL)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Groww token keeper loop error: %s", exc,
                         extra={"log_type": "groww_token", "event": "keeper_loop_error"})
            await asyncio.sleep(_KEEPER_INTERVAL)
