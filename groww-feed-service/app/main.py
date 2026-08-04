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
# Staleness watchdog. 2026-07-16 11:31:08 IST: the NATS socket died mid-session
# ("nats: unexpected EOF") and the SDK's internal reconnect failed every 4s for
# the REST OF THE DAY (its socket JWT had gone stale; only a new GrowwFeed()
# mints a fresh one). get_ltp() kept returning the last cached prices and this
# loop kept re-publishing them with a FRESH timestamp — so every downstream
# freshness check passed while all 275 symbols sat frozen for 4 hours (one
# paper exit executed on a phantom price; the whole afternoon's sessions saw
# flat candles). Detect it the only way that works: during market hours a
# 275-symbol subscription MUST show some price change — if nothing moves for
# STALE_EXIT_SECS, the socket is dead regardless of what get_ltp() claims, so
# exit and let Docker restart us (a fresh process mints a fresh socket token).
STALE_EXIT_SECS = float(os.getenv("FEED_STALE_EXIT_SECS", "180"))
# RSS ceiling. MAX_INIT_FAILS only bounds *consecutive* failures; it does nothing
# about the slow case, where each successful re-init also leaves a NatsClient and
# its reconnect thread behind. Measured 2026-08-04: 20 inits in one process life
# took RSS from ~25MB to ~99MB — about 5MB each, none of it reclaimable — so the
# 256MB container cap is roughly two days away at that rate. That is the same
# path that ended in "maximum recursion depth exceeded" on 14-16 July.
#
# Docker kills the container at the cap without warning, mid-session. Exiting
# ourselves a little short of it means the restart happens on our terms: cheap
# (a fresh process re-subscribes in seconds) and, unlike an OOM kill, logged.
# Measured against cgroup anon (see _rss_mb). A fresh process with 340 symbols
# subscribed sits at ~156MB of the 256MB container limit, so the usable leak
# headroom is only ~100MB. 215MB restarts us with ~40MB to spare while still
# allowing roughly 40 leaked inits — a couple of days at the observed rate.
# A restart here costs seconds and re-subscribes; an OOM kill costs the session.
MEM_EXIT_MB = float(os.getenv("FEED_MEM_EXIT_MB", "215"))
MEM_CHECK_SECS = 30.0

_r = redis.from_url(REDIS_URL, decode_responses=True)

_api = None
_feed = None
_token = None
_tok2sym: dict[str, str] = {}     # exchange_token -> SYMBOL
_subscribed: set[str] = set()
_logged_shape = False
_last_px: dict[str, float] = {}   # token -> last published price (watchdog)
_last_change = time.time()        # last time ANY published price changed
_init_count = 0                   # GrowwFeed constructions this process — one leak each


def _rss_mb() -> float:
    """Memory as the CONTAINER accounts it, in MB. 0.0 if unreadable, which
    leaves the watchdog inert rather than restart-looping on a bad reading.

    Specifically cgroup `anon` — the non-reclaimable part, which is what an OOM
    kill actually turns on. Two metrics were tried and rejected: VmRSS counts
    shared library mappings and spikes past 180MB merely from subscribing 340
    symbols, and memory.current folds in ~20MB of page cache the kernel would
    evict long before killing anything. Both read high enough on a healthy
    process to make a sane ceiling fire immediately.
    """
    try:                                    # cgroup v2: anon only
        with open("/sys/fs/cgroup/memory.stat") as fh:
            for line in fh:
                if line.startswith("anon "):
                    return int(line.split()[1]) / (1024.0 * 1024.0)
    except Exception:
        pass
    try:                                    # cgroup v1 fallback
        with open("/sys/fs/cgroup/memory/memory.stat") as fh:
            for line in fh:
                if line.startswith("rss "):
                    return int(line.split()[1]) / (1024.0 * 1024.0)
    except Exception:
        pass
    return 0.0


def _get_token() -> str | None:
    try:
        return _r.get(TOKEN_KEY)
    except Exception as exc:
        log.warning("redis token read failed: %s", exc)
        return None


def _init(token: str) -> bool:
    global _api, _feed, _token, _subscribed, _tok2sym, _init_count
    try:
        from growwapi import GrowwAPI, GrowwFeed
        _api = GrowwAPI(token)
        # Each construction strands a NatsClient in the SDK's class-level cache
        # with no way to release it, so count them: this is the leak's rate.
        _init_count += 1
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
    global _logged_shape, _last_change
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
        if _last_px.get(tok) != px:
            _last_px[tok] = px
            _last_change = now
        try:
            _r.set(LTP_PREFIX + sym, f"{px}:{now}", ex=60)
            written += 1
        except Exception:
            pass
    return written


def _market_hours_ist() -> bool:
    """NSE trading window with a small margin (09:20–15:25 IST, Mon–Fri) —
    the only period the staleness watchdog may fire (outside it, frozen
    prices are legitimate)."""
    t = time.gmtime(time.time() + 5.5 * 3600)
    if t.tm_wday >= 5:
        return False
    m = t.tm_hour * 60 + t.tm_min
    return (9 * 60 + 20) <= m <= (15 * 60 + 25)


def main() -> None:
    log.info("groww-feed-service starting; redis=%s", REDIS_URL)
    last_resync = 0.0
    last_mem_check = 0.0
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

            # Leaked-client watchdog (see MEM_EXIT_MB comment). Checked on a
            # timer rather than every poll — reading /proc each second to watch
            # a leak that moves in 5MB steps is pure overhead.
            if now - last_mem_check >= MEM_CHECK_SECS:
                last_mem_check = now
                rss = _rss_mb()
                if rss >= MEM_EXIT_MB:
                    log.warning("RSS %.0fMB >= %.0fMB ceiling after %d feed inits — leaked "
                                "NatsClients cannot be freed in-process, exiting for a clean "
                                "restart before Docker OOM-kills us", rss, MEM_EXIT_MB, _init_count)
                    sys.exit(3)

            # Staleness watchdog (see STALE_EXIT_SECS comment).
            if (_subscribed and _market_hours_ist()
                    and now - _last_change > STALE_EXIT_SECS):
                log.warning("no price change across %d symbols for %.0fs during market "
                            "hours — socket presumed dead, exiting for a clean restart",
                            len(_subscribed), now - _last_change)
                sys.exit(2)
        except Exception as exc:
            log.warning("loop error: %s", exc)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
