"""Nightly Pattern-Memory refresh.

Replays real strategy backtests across the whole watchlist and rebuilds the
BACKTEST portion of the memory bank from the latest candle data. Runs once a day
(after market close) so the bank always reflects recent market behaviour without
unbounded growth. LIVE cases (real trade outcomes) are preserved untouched.
"""
from __future__ import annotations
import asyncio
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


def _seconds_until_hour_ist(hour: int) -> float:
    now = datetime.now(IST)
    target = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def scheduled_sweep_loop() -> None:
    """Background task: run the sweep once daily at MEMORY_SWEEP_HOUR_IST."""
    if not settings.MEMORY_SWEEP_ENABLED:
        logger.info("Memory sweep disabled via config")
        return
    while True:
        try:
            wait = _seconds_until_hour_ist(settings.MEMORY_SWEEP_HOUR_IST)
            logger.info("Next pattern-memory sweep in %.0f min", wait / 60)
            await asyncio.sleep(wait)
            await run_memory_sweep(trigger="scheduled")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Scheduled memory sweep error: %s", exc)
            await asyncio.sleep(3600)  # back off an hour on failure
