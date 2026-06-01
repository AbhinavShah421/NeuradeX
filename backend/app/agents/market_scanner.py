"""Market scanner — the self-running agent that builds the AI Watchlist.

Periodically analyses the stock universe with the full 7-agent ensemble (on real
daily candles via the data-provider registry), scores each stock, and stores a
ranked watchlist in Redis with the full evidence (per-agent signals, confidence,
agreement, reasoning). Nothing here is hard-coded — every entry is a live model
decision. The autopilot then trades the top of this list.
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
_WATCHLIST_KEY = "ai_engine:watchlist"
_SCAN_INTERVAL = 30 * 60          # rescan every 30 minutes
_TOP_N = 12
_scan_lock = asyncio.Lock()


def _score(action: str, confidence: float, agreement: float) -> float:
    """Higher = a more attractive, higher-conviction opportunity."""
    action_factor = 1.0 if action == "BUY" else (0.35 if action == "SELL" else 0.4)
    return round(confidence * (0.5 + 0.5 * agreement) * action_factor, 4)


async def scan_market(symbols: list[str] | None = None, top_n: int = _TOP_N) -> list[dict]:
    """Run the ensemble across the universe and store a ranked watchlist."""
    if _scan_lock.locked():
        return await get_watchlist()
    async with _scan_lock:
        from app.agents import get_engine, get_learning
        from app.data.providers import fetch_daily
        from app.api.agent import KNOWN_STOCKS

        syms = [s.upper() for s in (symbols or list(KNOWN_STOCKS.keys()))]
        engine, learning = get_engine(), get_learning()
        try:
            weights = await learning.get_weights()
            if weights:
                engine.update_weights(weights)
        except Exception:
            pass

        end = datetime.now()
        start = end - timedelta(days=160)
        items: list[dict] = []

        for sym in syms:
            try:
                candles, source = await fetch_daily(sym, start, end)
                if not candles or len(candles) < 30:
                    continue
                decision = await engine.decide(sym, candles, {"symbol": sym, "capital": 100_000.0, "position": "NONE"})
                items.append({
                    "symbol": sym,
                    "name": KNOWN_STOCKS.get(sym, sym),
                    "price": round(float(candles[-1]["close"]), 2),
                    "action": decision.action,
                    "confidence": round(decision.confidence, 3),
                    "agreement": round(decision.agent_agreement, 3),
                    "risk": round(decision.risk_score, 3),
                    "score": _score(decision.action, decision.confidence, decision.agent_agreement),
                    "reasoning": decision.reasoning,
                    "source": source,
                    "agents": [
                        {"agent": a.agent_name, "action": a.action,
                         "confidence": round(a.confidence, 3), "reasoning": a.reasoning}
                        for a in decision.agents
                    ],
                })
            except Exception as exc:
                logger.debug("scan failed for %s: %s", sym, exc)

        # Rank: BUY first, then by score
        items.sort(key=lambda r: (r["action"] != "BUY", -r["score"]))
        watchlist = items[:top_n]

        payload = {"updated_at": datetime.now(IST).isoformat(), "scanned": len(items),
                   "universe": len(syms), "items": watchlist}
        try:
            from app.utils.redis_client import cache_set
            await cache_set(_WATCHLIST_KEY, json.dumps(payload), expire=86400)
        except Exception as exc:
            logger.warning("watchlist save failed: %s", exc)

        logger.info("Market scan complete",
                    extra={"log_type": "ai_engine", "event": "market_scan",
                           "scanned": len(items), "watchlist": len(watchlist)})
        return watchlist


async def get_watchlist() -> dict:
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_WATCHLIST_KEY)
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.debug("watchlist load failed: %s", exc)
    return {"updated_at": None, "scanned": 0, "universe": 0, "items": []}


async def scanner_loop() -> None:
    """Background loop: keep the watchlist fresh."""
    await asyncio.sleep(20)
    while True:
        try:
            await scan_market()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("scanner loop error: %s", exc)
        await asyncio.sleep(_SCAN_INTERVAL)
