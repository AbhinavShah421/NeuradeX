"""Angel One (SmartAPI) live market data — real-time NSE LTP for paper trading.

Flow:
  1. loginByPassword (client code + MPIN + TOTP) → jwt + feed token.
  2. Fetch the scrip master once → build {SYMBOL: token} for NSE equity.
  3. Poll getMarketData (LTP mode) for the watchlist tokens every few seconds →
     keep an in-memory {symbol: (ltp, ts)} map.

Paper sessions read that map (no per-session HTTP) and overlay it on the candle
base, cutting the live-candle gap to the poll interval (~3s). Everything is
gated on credentials and degrades to the Yahoo feed when Angel isn't configured
or is unavailable — it is never a hard dependency.

Required env: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET.
Get them at https://smartapi.angelbroking.com (create an app for the API key;
enable TOTP and use its base32 secret).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import struct
import time
from datetime import datetime, timezone, timedelta

import httpx

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

_BASE = "https://apiconnect.angelone.in"
_SCRIP_MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# Live LTP map shared with paper trading: symbol(upper) -> (price, epoch_ts)
_LTP: dict[str, tuple[float, float]] = {}


def _totp(secret: str, digits: int = 6, period: int = 30) -> str:
    """Standard RFC-6238 TOTP (SHA1) from a base32 secret — no extra dependency."""
    key = base64.b32decode(secret.strip().upper() + "=" * (-len(secret.strip()) % 8))
    counter = int(time.time()) // period
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


class AngelClient:
    def __init__(self, api_key: str, client_code: str, pin: str, totp_secret: str):
        self._api_key = api_key
        self._client_code = client_code
        self._pin = pin
        self._totp_secret = totp_secret
        self._jwt: str | None = None
        self._feed_token: str | None = None
        self._token_map: dict[str, str] = {}     # SYMBOL -> token
        self._map_day: str | None = None
        self._status = "unknown"
        self._reason = ""
        self._lock = asyncio.Lock()

    # ── auth ──────────────────────────────────────────────────────────────────

    def _headers(self, auth: bool = False) -> dict:
        h = {
            "Content-Type": "application/json", "Accept": "application/json",
            "X-UserType": "USER", "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1", "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00", "X-PrivateKey": self._api_key,
        }
        if auth and self._jwt:
            h["Authorization"] = f"Bearer {self._jwt}"
        return h

    async def _login(self) -> bool:
        url = f"{_BASE}/rest/auth/angelbroking/user/v1/loginByPassword"
        body = {"clientcode": self._client_code, "password": self._pin, "totp": _totp(self._totp_secret)}
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(url, headers=self._headers(), json=body)
                data = r.json()
            if not data.get("status") or not data.get("data"):
                self._status = "failed"
                self._reason = str(data.get("message") or data.get("errorcode") or data)[:160]
                logger.warning("Angel login failed: %s", self._reason)
                return False
            self._jwt = data["data"].get("jwtToken")
            self._feed_token = data["data"].get("feedToken")
            self._status = "ok"
            self._reason = ""
            logger.info("Angel One logged in", extra={"log_type": "angel", "event": "login_ok"})
            return True
        except Exception as exc:
            self._status = "failed"
            self._reason = str(exc)[:160]
            logger.warning("Angel login error: %s", exc)
            return False

    # ── scrip master (symbol -> token) ────────────────────────────────────────

    async def _ensure_token_map(self, symbols: list[str]) -> None:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if self._token_map and self._map_day == today:
            return
        try:
            async with httpx.AsyncClient(timeout=60.0) as c:
                r = await c.get(_SCRIP_MASTER, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                rows = r.json()
            want = {s.upper() for s in symbols}
            m: dict[str, str] = {}
            for row in rows:
                if row.get("exch_seg") != "NSE":
                    continue
                sym = (row.get("symbol") or "").upper()
                # NSE equity trading symbols look like "RELIANCE-EQ" / "M&M-EQ";
                # map the base (more reliable than the free-text name field).
                if sym.endswith("-EQ"):
                    base = sym[:-3]
                    if base in want:
                        m[base] = str(row.get("token"))
            if m:
                self._token_map = m
                self._map_day = today
                logger.info("Angel scrip map built: %d/%d symbols", len(m), len(want))
        except Exception as exc:
            logger.warning("Angel scrip master fetch failed: %s", exc)

    # ── LTP quote ─────────────────────────────────────────────────────────────

    async def refresh_ltps(self, symbols: list[str]) -> int:
        """Fetch LTP for the given symbols and update the shared _LTP map."""
        async with self._lock:
            if self._status != "ok" or not self._jwt:
                if not await self._login():
                    return 0
            await self._ensure_token_map(symbols)
            tokens = [self._token_map[s.upper()] for s in symbols if s.upper() in self._token_map]
            if not tokens:
                return 0
            tok2sym = {v: k for k, v in self._token_map.items()}
            url = f"{_BASE}/rest/secure/angelbroking/market/v1/quote/"
            body = {"mode": "LTP", "exchangeTokens": {"NSE": tokens}}
            try:
                async with httpx.AsyncClient(timeout=12.0) as c:
                    r = await c.post(url, headers=self._headers(auth=True), json=body)
                    data = r.json()
                if not data.get("status"):
                    # jwt likely expired → force a re-login next call
                    self._status = "failed"
                    self._reason = str(data.get("message") or data)[:160]
                    return 0
                fetched = (data.get("data") or {}).get("fetched") or []
                now = time.time()
                n = 0
                for item in fetched:
                    sym = tok2sym.get(str(item.get("symbolToken")))
                    ltp = item.get("ltp")
                    if sym and ltp:
                        _LTP[sym] = (float(ltp), now)
                        n += 1
                return n
            except Exception as exc:
                logger.debug("Angel quote error: %s", exc)
                return 0

    def get_status(self) -> dict:
        return {"status": self._status, "reason": self._reason,
                "symbols_mapped": len(self._token_map), "live_symbols": len(_LTP),
                "has_jwt": bool(self._jwt)}


_client: AngelClient | None = None


def init_angel_client() -> AngelClient | None:
    """Create the singleton if all credentials are present."""
    global _client
    key = os.getenv("ANGEL_API_KEY", "")
    cc  = os.getenv("ANGEL_CLIENT_CODE", "")
    pin = os.getenv("ANGEL_PIN", "")
    sec = os.getenv("ANGEL_TOTP_SECRET", "")
    if not (key and cc and pin and sec):
        return None
    _client = AngelClient(key, cc, pin, sec)
    logger.info("Angel One client configured")
    return _client


def get_angel_client() -> AngelClient | None:
    return _client


def angel_get_ltp(symbol: str, max_age: float = 20.0) -> float:
    """Latest Angel LTP for a symbol if fresh enough, else 0.0 (read-only, no HTTP)."""
    v = _LTP.get(symbol.upper())
    if v and (time.time() - v[1]) <= max_age:
        return v[0]
    return 0.0


async def angel_poll_loop() -> None:
    """Keep the LTP map warm for the AI watchlist during market hours."""
    from app.utils.redis_client import cache_get
    interval = int(os.getenv("ANGEL_POLL_SECS", "3"))
    await asyncio.sleep(12)
    while True:
        try:
            client = get_angel_client()
            if client:
                from app.api.paper_trading import _market_status_label
                if _market_status_label() == "open":
                    raw = await cache_get("ai_engine:watchlist")
                    syms = []
                    if raw:
                        syms = [it.get("symbol") for it in json.loads(raw).get("items", []) if it.get("symbol")]
                    if syms:
                        await client.refresh_ltps(syms)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("angel poll loop error: %s", exc)
        await asyncio.sleep(interval)
