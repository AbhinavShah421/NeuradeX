"""Nightly Pattern-Memory refresh.

Replays real strategy backtests across the whole watchlist and rebuilds the
BACKTEST portion of the memory bank from the latest candle data. Runs once a day
(after market close) so the bank always reflects recent market behaviour without
unbounded growth. LIVE cases (real trade outcomes) are preserved untouched.
"""
from __future__ import annotations
import asyncio
import os
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

_sweep_lock = asyncio.Lock()
_last_sweep: dict | None = None


def get_last_sweep() -> dict | None:
    return _last_sweep


def is_running() -> bool:
    return _sweep_lock.locked()


async def run_memory_sweep(
    symbols: list[str] | None = None,
    strategies: list[str] | None = None,
    lookback_days: int | None = None,
    trigger: str = "scheduled",
) -> dict:
    """Rebuild the BACKTEST memory from fresh backtests. Returns a summary dict."""
    global _last_sweep
    if _sweep_lock.locked():
        return {"status": "already_running"}

    # Honour the pattern-memory freeze (REPLAY_MEMORY_WRITES=0), same flag the
    # session engine's per-trade _feed_memory respects. This sweep bulk-REPLACES
    # every BACKTEST case in a single transaction, so during a validation batch it
    # silently rewrites the bank the memory agent gates entries on, mid-run —
    # sessions before and after face different evidence, which is the exact
    # confound the freeze exists to prevent. Observed 2026-07-27: the scheduled
    # sweep fired ~3h into a 15h batch and rewrote all 7,039 BACKTEST cases while
    # per-trade writes were correctly blocked. Read at call time, not import time,
    # since the sweep runs on a long timer.
    if os.getenv("REPLAY_MEMORY_WRITES", "1").lower() in ("0", "false", "no"):
        logger.info("Pattern memory sweep skipped — REPLAY_MEMORY_WRITES=0 (bank frozen)",
                    extra={"log_type": "ai_engine", "event": "memory_sweep_skipped",
                           "trigger": trigger})
        return {"status": "skipped_frozen", "trigger": trigger}

    async with _sweep_lock:
        # Imported lazily to avoid a circular import at module load time
        from app.api.backtest import _fetch_candles, _run_engine, STRATEGIES
        from app.api.agent import KNOWN_STOCKS
        from app.agents import get_memory
        from app.agents.fingerprint import build_fingerprint, classify_regime

        syms = [s.upper() for s in (symbols or list(KNOWN_STOCKS.keys()))]
        strats = strategies or list(STRATEGIES.keys())
        days = lookback_days or settings.MEMORY_SWEEP_LOOKBACK_DAYS

        await get_memory().init_db()
        end = datetime.now()
        start = end - timedelta(days=days)

        started = datetime.now(IST)
        all_cases: list[dict] = []
        ok = fail = 0

        for sym in syms:
            try:
                candles, source = await _fetch_candles(sym, start, end)
            except Exception as exc:
                logger.warning("sweep fetch failed for %s: %s", sym, exc)
                fail += 1
                continue
            if not candles or len(candles) < 40:
                continue

            # Precompute date → index once per symbol for no-lookahead fingerprints
            date_idx = {c.get("date"): i for i, c in enumerate(candles)}

            for strat in strats:
                try:
                    # _run_engine is CPU-bound (pandas) → offload off the event loop
                    result = await asyncio.to_thread(
                        _run_engine, candles, strat, {}, 100_000.0, 0.001
                    )
                except Exception as exc:
                    logger.warning("sweep backtest %s/%s failed: %s", sym, strat, exc)
                    fail += 1
                    continue

                for t in result.get("trades", []):
                    idx = date_idx.get(t.get("entry_date"))
                    if idx is None or idx < 15:
                        continue
                    window = candles[: idx + 1]
                    fp = build_fingerprint(window)
                    if fp is None:
                        continue
                    all_cases.append({
                        "symbol": sym, "fingerprint": fp, "action": "BUY",
                        "entry_price": t.get("entry_price", 0.0),
                        "exit_price":  t.get("exit_price", 0.0),
                        "pnl_pct":     float(t.get("pnl_pct", 0.0)),
                        "regime":      classify_regime(window), "source": "BACKTEST",
                    })
                ok += 1

        inserted = await get_memory().replace_source("BACKTEST", all_cases)
        finished = datetime.now(IST)

        _last_sweep = {
            "status": "ok",
            "trigger": trigger,
            "symbols": len(syms),
            "backtests_ok": ok,
            "backtests_failed": fail,
            "cases_inserted": inserted,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_secs": round((finished - started).total_seconds(), 1),
        }
        logger.info("Pattern memory sweep complete",
                    extra={"log_type": "ai_engine", "event": "memory_sweep", **_last_sweep})
        return _last_sweep


async def scheduled_sweep_loop() -> None:
    """Background task: run the sweep once per day, due from MEMORY_SWEEP_HOUR_IST.

    Due-time rather than fire-time: the host is powered down at 02:00 IST, so
    the old sleep-to-the-hour form simply never fired. See app/utils/nightly.py.
    """
    if not settings.MEMORY_SWEEP_ENABLED:
        logger.info("Memory sweep disabled via config")
        return

    from app.utils.nightly import nightly_loop, NotReady

    async def _run() -> object:
        res = await run_memory_sweep(trigger="scheduled")
        if isinstance(res, dict):
            if res.get("status") == "already_running":
                raise NotReady("a sweep is already running")
            # A sweep where EVERY backtest failed rebuilt nothing — the case
            # bank still holds whatever it held this morning. Letting that book
            # the day means the next attempt is tomorrow: on 2026-09-08 the boot
            # catch-up recorded backtests_ok 0 / backtests_failed 248 as a
            # successful sweep because the host's network was not up yet.
            #
            # A partial failure is left alone deliberately — some symbols always
            # fail (delisted tickers, renamed symbols), and a sweep that
            # refreshed most of the bank did its job.
            #
            # `skipped_frozen` is not this: REPLAY_MEMORY_WRITES=0 means the bank
            # is deliberately frozen for a validation batch, so skipping the day
            # is the intended outcome and the slot should be spent.
            if (res.get("status") != "skipped_frozen"
                    and not res.get("backtests_ok")
                    and res.get("backtests_failed")):
                raise NotReady(
                    f"every backtest failed ({res.get('backtests_failed')} symbols) "
                    f"— no candle data reached the sweep")
        return res

    await nightly_loop("memory_sweep", settings.MEMORY_SWEEP_HOUR_IST,
                       _run, label="Pattern-memory sweep")
