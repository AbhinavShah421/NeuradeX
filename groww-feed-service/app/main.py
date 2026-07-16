"""Groww live-data feed microservice.

Owns the heavy `growwapi` SDK (whose pinned pydantic/pandas/protobuf conflict with
the main backend), connects to Groww's streaming feed, and publishes the latest
tick per symbol to Redis so the backend's paper trading can read real-time prices.

Contract with the backend (app/utils/groww_feed.py):
  • reads  Redis set  `groww:feed:symbols`        — symbols to stream
  • reads  Redis key  `groww:access_token`        — token the backend's GrowwClient obtained
  • writes Redis key  `groww:ltp:{SYMBOL}` = "<price>:<epoch_ts>" (TTL 60s)

Everything is best-effort: on any error it backs off and retries; it never needs
to be up for trading to work (the backend falls back to REST/Yahoo).
"""
import os
import sys
import time
import logging

import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("groww-feed")

REDIS_URL = os.getenv("REDIS_URL") or f"redis://{os.getenv('REDIS_HOST','redis')}:{os.getenv('REDIS_PORT','6379')}/0"
SYMBOLS_SET   = "groww:feed:symbols"
TOKEN_KEY     = "groww:access_token"
LTP_PREFIX    = "groww:ltp:"
EXCHANGE      = "NSE"
SEGMENT       = "CASH"
POLL_SECS     = 1.0
RESYNC_SECS   = 20.0    # how often to pick up new symbols / token changes
# growwapi.GrowwFeed._key() mints a brand-new random socket token on every call,
# so its CLASS-LEVEL `_nats_clients` cache (keyed on that token) never gets a
# repeat key and never evicts old entries — and NatsClient has no close()/
# disconnect(), each one also leaking a background reconnect thread. Every
# failed _init() here constructs a fresh GrowwFeed(), so a stuck token (or any
# other persistent failure) piles these up forever: observed 2026-07-14 to
# 2026-07-16, ~10s retry cadence, ~150MB RSS and eventually "maximum recursion
# depth exceeded" on construction (never recovered — 10k+ leaked clients/
# threads in one process). There is no public hook to tear these down, so the
# only real fix is to not let them accumulate in-process: bail out and let
# Docker (`restart: unless-stopped`) hand us a clean interpreter.
MAX_INIT_FAILS = 3

_r = redis.from_url(REDIS_URL, decode_responses=True)

_api = None
_feed = None
_token = None
_tok2sym: dict[str, str] = {}     # exchange_token -> SYMBOL
_subscribed: set[str] = set()
_logged_shape = False


def _get_token() -> str | None:
    try:
        return _r.get(TOKEN_KEY)
    except Exception as exc:
        log.warning("redis token read failed: %s", exc)
        return None


def _init(token: str) -> bool:
    global _api, _feed, _token, _subscribed, _tok2sym
    try:
        from growwapi import GrowwAPI, GrowwFeed
        _api = GrowwAPI(token)
        _feed = GrowwFeed(_api)
        _token = token
        _subscribed = set()
        _tok2sym = {}
        log.info("groww feed initialised")
        return True
    except Exception as exc:
        log.warning("groww feed init failed: %s", exc)
        _api = _feed = None
        return False


def _wanted_symbols() -> list[str]:
    try:
        return sorted(s.upper() for s in (_r.smembers(SYMBOLS_SET) or []))
    except Exception:
        return []


def _subscribe(new_syms: list[str]) -> None:
    instruments = []
    for sym in new_syms:
        try:
            inst = _api.get_instrument_by_exchange_and_trading_symbol(EXCHANGE, sym)
            tok = str(inst.get("exchange_token") or inst.get("token") or "")
            if not tok:
                continue
            instruments.append({"exchange": EXCHANGE, "segment": SEGMENT, "exchange_token": tok})
            _tok2sym[tok] = sym
        except Exception:
            continue
    if not instruments:
        return
    try:
        _feed.subscribe_ltp(instruments)
        _subscribed.update(new_syms)
        log.info("subscribed %d symbols (total %d)", len(instruments), len(_subscribed))
    except Exception as exc:
        log.warning("subscribe failed: %s", exc)


def _extract_prices(node, out: dict) -> None:
    """Recursively pull {exchange_token: price} from the feed's nested
    {exchange: {segment: {token: value}}} structure. value may be a number or a
    dict carrying ltp/last_price; None means no tick received yet."""
    if isinstance(node, dict):
        for k, v in node.items():
            tok = str(k)
            if tok in _tok2sym:
                px = 0.0
                if isinstance(v, dict):
                    px = float(v.get("ltp") or v.get("last_price") or v.get("ltp_in_paise", 0) or 0)
                    if v.get("ltp_in_paise") and not (v.get("ltp") or v.get("last_price")):
                        px = px / 100.0
                elif isinstance(v, (int, float)):
                    px = float(v)
                if px > 0:
                    out[tok] = px
            elif isinstance(v, dict):
                _extract_prices(v, out)


def _publish_ticks() -> int:
    global _logged_shape
    if _feed is None:
        return 0
    try:
        data = _feed.get_ltp() or {}
    except Exception:
        return 0
    prices: dict = {}
    _extract_prices(data, prices)
    if not _logged_shape and data:
        _logged_shape = True
        log.info("first feed payload: %s | extracted=%s", str(data)[:250], prices)
    now = time.time()
    written = 0
    for tok, px in prices.items():
        sym = _tok2sym.get(tok)
        if not sym:
            continue
        try:
            _r.set(LTP_PREFIX + sym, f"{px}:{now}", ex=60)
            written += 1
        except Exception:
            pass
    return written


def main() -> None:
    log.info("groww-feed-service starting; redis=%s", REDIS_URL)
    last_resync = 0.0
    init_fails = 0
    while True:
        try:
            token = _get_token()
            if not token:
                log.info("no groww token in redis yet — waiting")
                time.sleep(5)
                continue
            if token != _token or _feed is None:
                if not _init(token):
                    init_fails += 1
                    if init_fails >= MAX_INIT_FAILS:
                        log.warning("init failed %d times in a row — exiting so Docker restarts "
                                    "us with a clean process (see MAX_INIT_FAILS comment)", init_fails)
                        sys.exit(1)
                    time.sleep(10)
                    continue
                init_fails = 0

            now = time.time()
            if now - last_resync >= RESYNC_SECS:
                last_resync = now
                wanted = _wanted_symbols()
                new = [s for s in wanted if s not in _subscribed]
                if new:
                    _subscribe(new)

            _publish_ticks()
        except Exception as exc:
            log.warning("loop error: %s", exc)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
