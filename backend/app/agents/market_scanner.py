"""Market scanner — the self-running agent that builds the AI Watchlist.

Periodically analyses the stock universe with the full 7-agent ensemble (on real
daily candles via the data-provider registry), scores each stock, and stores a
ranked watchlist in Redis with the full evidence (per-agent signals, confidence,
agreement, reasoning). Nothing here is hard-coded — every entry is a live model
decision. The autopilot then trades the top of this list.

Each scan now produces three categorised sub-lists:
  intraday  — volatile, high-volume stocks ideal for same-day trading
  delivery  — stable uptrend stocks with an estimated safe holding period
  fno       — F&O-eligible stocks with a specific option recommendation
"""
from __future__ import annotations
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
_WATCHLIST_KEY   = "ai_engine:watchlist"
_SCAN_INTERVAL   = 30 * 60          # rescan every 30 minutes
_TOP_N           = 15               # top N per category
_scan_lock       = asyncio.Lock()

# ── F&O eligible stocks (NSE) ─────────────────────────────────────────────────
FNO_STOCKS: set[str] = {
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "KOTAKBANK",
    "SBIN", "BAJFINANCE", "AXISBANK", "WIPRO", "HINDUNILVR", "ITC",
    "LT", "ONGC", "NTPC", "POWERGRID", "COALINDIA", "JSWSTEEL",
    "TATAMOTORS", "MARUTI", "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB",
    "ADANIENT", "ADANIPORTS", "ULTRACEMCO", "GRASIM", "SHREECEM",
    "TITAN", "ASIANPAINT", "BAJAJFINSV", "BPCL", "EICHERMOT",
    "HEROMOTOCO", "HINDALCO", "INDUSINDBK", "NESTLEIND", "TECHM",
    "TATASTEEL", "BRITANNIA", "SBILIFE", "HDFCLIFE", "ICICIGI",
    "APOLLOHOSP", "UPL", "TATACONSUM", "HCLTECH", "LTIM", "BHARTIARTL",
    "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "FEDERALBNK", "IDFCFIRSTB",
    "BANDHANBNK", "YESBANK", "RBLBANK", "AUBANK",
    "PIDILITIND", "CHOLAFIN", "DMART", "GAIL", "HAVELLS", "HAL",
    "IRCTC", "JINDALSTEL", "LUPIN", "MUTHOOTFIN", "NMDC", "OFSS",
    "POLYCAB", "RECLTD", "SRF", "SIEMENS", "TRENT", "VBL", "VEDL",
    "ZOMATO", "ADANIGREEN", "ADANIPOWER", "AUROPHARMA", "BIOCON",
    "TVSMOTOR", "BALKRISIND", "MOTHERSON", "APOLLOTYRE", "EXIDEIND",
    "DLF", "PRESTIGE", "LODHA", "OBEROIRLTY", "CONCOR", "INDUSTOWER",
    "HINDPETRO", "OIL", "PETRONET", "TATAPOWER", "SAIL", "NATIONALUM",
    "DEEPAKNTR", "BEL", "HAL", "BDL", "RVNL", "PFC",
    "PERSISTENT", "COFORGE", "TATAELXSI", "MPHASIS",
    "PAGEIND", "DIXON", "AMBER", "INDIGO",
    "LAURUS", "GRANULES", "IPCALAB", "ALKEM",
    "SBICARD", "HDFCAMC", "CDSL", "ANGELONE",
    "TORNTPHARM", "PIIND", "SHRIRAMFIN",
}


def _score(action: str, confidence: float, agreement: float) -> float:
    """Higher = a more attractive, higher-conviction opportunity."""
    action_factor = 1.0 if action == "BUY" else (0.35 if action == "SELL" else 0.4)
    return round(confidence * (0.5 + 0.5 * agreement) * action_factor, 4)


def _compute_scan_metrics(candles: list[dict]) -> dict:
    """Compute lightweight metrics from daily candles for categorisation."""
    if len(candles) < 20:
        return {}
    closes  = [float(c["close"]) for c in candles]
    highs   = [float(c["high"])  for c in candles]
    lows    = [float(c["low"])   for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    price = closes[-1]

    # ATR-14 (simple)
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i - 1]),
               abs(lows[i]  - closes[i - 1]))
           for i in range(1, len(candles))]
    atr14    = sum(trs[-14:]) / 14 if len(trs) >= 14 else (sum(trs) / len(trs))
    atr_pct  = round(atr14 / price * 100, 2) if price else 0.0

    # Relative volume (last day vs 20-day avg)
    avg_vol20  = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else (sum(volumes[:-1]) / max(1, len(volumes) - 1))
    rel_volume = round(volumes[-1] / avg_vol20, 2) if avg_vol20 > 0 else 1.0

    # RSI-14
    gains, losses = [], []
    for i in range(1, min(16, len(closes))):
        d = closes[-i] - closes[-i - 1]
        (gains if d > 0 else losses).append(abs(d))
    avg_g = sum(gains) / 14 if gains else 0.001
    avg_l = sum(losses) / 14 if losses else 0.001
    rs    = avg_g / avg_l
    rsi   = round(100 - 100 / (1 + rs), 1)

    # SMA 20 / 50
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes) / len(closes)

    # 10-day momentum %
    momentum_pct = round((closes[-1] - closes[-11]) / closes[-11] * 100, 2) if len(closes) >= 11 else 0.0

    sma_trend = "bullish" if sma20 > sma50 else "bearish"

    # Avg daily volume
    avg_vol = int(avg_vol20)

    return {
        "atrPct":      atr_pct,
        "relVolume":   rel_volume,
        "rsi":         rsi,
        "smaTrend":    sma_trend,
        "sma20":       round(sma20, 2),
        "sma50":       round(sma50, 2),
        "momentumPct": momentum_pct,
        "avgVolume":   avg_vol,
        "liquidityScore": min(1.0, round(avg_vol / 1_000_000, 2)),
    }


def _estimate_delivery_weeks(metrics: dict, action: str, confidence: float) -> int:
    """Estimate safe holding weeks for delivery trade."""
    if action == "SELL" or action == "HOLD":
        return 0

    base = 2
    if metrics.get("smaTrend") == "bullish":
        base += 2
    if confidence >= 0.78:
        base += 2
    elif confidence >= 0.68:
        base += 1
    rsi = metrics.get("rsi", 50)
    if 40 <= rsi <= 62:
        base += 1
    if metrics.get("momentumPct", 0) > 3:
        base += 1
    atr_pct = metrics.get("atrPct", 2.0)
    if atr_pct > 3.5:
        base -= 1
    if atr_pct > 5.0:
        base -= 1
    return max(1, min(12, base))


def _fno_recommendation(symbol: str, price: float, action: str,
                         confidence: float, metrics: dict) -> dict | None:
    """Generate an F&O option recommendation for the current expiry cycle."""
    if action not in ("BUY", "SELL"):
        return None

    today         = date.today()
    # Next Thursday (weekly expiry)
    days_ahead    = (3 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    weekly_expiry = today + timedelta(days=days_ahead)

    # Monthly expiry: last Thursday of current month
    if today.month == 12:
        first_next = date(today.year + 1, 1, 1)
    else:
        first_next = date(today.year, today.month + 1, 1)
    month_last_day = first_next - timedelta(days=1)
    thu_offset     = (month_last_day.weekday() - 3) % 7
    monthly_expiry = month_last_day - timedelta(days=thu_offset)

    opt_type = "CE" if action == "BUY" else "PE"

    # Strike interval by price band
    if price > 5000:   interval = 100
    elif price > 2000: interval = 50
    elif price > 1000: interval = 25
    elif price > 500:  interval = 10
    else:              interval = 5

    atm_strike = round(price / interval) * interval
    if opt_type == "CE":
        strike = atm_strike + interval      # 1 step OTM call
    else:
        strike = atm_strike - interval      # 1 step OTM put

    # Safe days: how long to hold the option
    if confidence >= 0.80:
        safe_days = max(days_ahead, 5)
    elif confidence >= 0.70:
        safe_days = min(days_ahead, 4)
    else:
        safe_days = min(days_ahead, 2)

    # Prefer monthly if weekly has < 3 days left
    expiry     = monthly_expiry if days_ahead < 3 else weekly_expiry
    expiry_lbl = expiry.strftime("%d %b")

    atr_pct = metrics.get("atrPct", 2.0)
    momentum = metrics.get("momentumPct", 0)

    return {
        "optionType":   opt_type,
        "strike":       strike,
        "atmStrike":    atm_strike,
        "expiry":       expiry_lbl,
        "expiryDate":   expiry.isoformat(),
        "safeDays":     safe_days,
        "rationale": (
            f"{'Bullish' if opt_type == 'CE' else 'Bearish'} setup — "
            f"buy {symbol} {strike} {opt_type} expiry {expiry_lbl}. "
            f"ATR {atr_pct:.1f}%, momentum {momentum:+.1f}%. "
            f"Suggested hold: {safe_days} {'day' if safe_days == 1 else 'days'}."
        ),
    }


def _intraday_score(item: dict, metrics: dict) -> float:
    """Extra score boost for intraday suitability."""
    if not metrics:
        return 0.0
    atr   = metrics.get("atrPct", 0)
    rvol  = metrics.get("relVolume", 1.0)
    liq   = metrics.get("liquidityScore", 0)
    boost = 0.0
    if atr > 2.0:   boost += 0.15
    if atr > 3.0:   boost += 0.10
    if rvol > 1.3:  boost += 0.10
    if liq > 0.5:   boost += 0.10
    return round(boost, 3)


async def scan_market(symbols: list[str] | None = None, top_n: int = _TOP_N) -> list[dict]:
    """Run the ensemble across the universe and store a ranked watchlist."""
    if _scan_lock.locked():
        return (await get_watchlist()).get("items", [])
    async with _scan_lock:
        from app.agents import get_engine, get_learning
        from app.data.providers import fetch_daily
        from app.api.agent import KNOWN_STOCKS

        syms   = [s.upper() for s in (symbols or list(KNOWN_STOCKS.keys()))]
        engine = get_engine()
        learning = get_learning()
        try:
            weights = await learning.get_weights()
            if weights:
                engine.update_weights(weights)
        except Exception:
            logger.debug("Failed to refresh ensemble weights before market scan; using existing weights", exc_info=True)

        end   = datetime.now()
        start = end - timedelta(days=160)
        items: list[dict] = []

        for sym in syms:
            try:
                candles, source = await fetch_daily(sym, start, end)
                if not candles or len(candles) < 30:
                    continue
                decision = await engine.decide(
                    sym, candles,
                    {"symbol": sym, "capital": 100_000.0, "position": "NONE"},
                )
                metrics = _compute_scan_metrics(candles)
                item: dict = {
                    "symbol":     sym,
                    "name":       KNOWN_STOCKS.get(sym, sym),
                    "price":      round(float(candles[-1]["close"]), 2),
                    "action":     decision.action,
                    "confidence": round(decision.confidence, 3),
                    "agreement":  round(decision.agent_agreement, 3),
                    "risk":       round(decision.risk_score, 3),
                    "score":      _score(decision.action, decision.confidence, decision.agent_agreement),
                    "reasoning":  decision.reasoning,
                    "source":     source,
                    "metrics":    metrics,
                    "agents": [
                        {"agent": a.agent_name, "action": a.action,
                         "confidence": round(a.confidence, 3), "reasoning": a.reasoning}
                        for a in decision.agents
                    ],
                }

                # ── Delivery enrichment ───────────────────────────────────────
                item["deliveryWeeks"] = _estimate_delivery_weeks(
                    metrics, decision.action, decision.confidence)

                # ── FNO enrichment ────────────────────────────────────────────
                if sym in FNO_STOCKS:
                    rec = _fno_recommendation(
                        sym, item["price"], decision.action,
                        decision.confidence, metrics)
                    if rec:
                        item["fnoRecommendation"] = rec

                items.append(item)
            except Exception as exc:
                logger.debug("scan failed for %s: %s", sym, exc)

        # ── Build categorised sub-lists ───────────────────────────────────────

        # Intraday: high ATR + volume + BUY preferred
        intraday = sorted(
            [i for i in items if i["action"] == "BUY"],
            key=lambda i: -(i["score"] + _intraday_score(i, i.get("metrics", {}))),
        )[:top_n]

        # Delivery: bullish trend, moderate volatility, BUY only
        delivery = sorted(
            [i for i in items
             if i["action"] == "BUY"
             and i.get("metrics", {}).get("smaTrend") == "bullish"
             and i.get("metrics", {}).get("atrPct", 99) < 4.5
             and i.get("deliveryWeeks", 0) >= 2],
            key=lambda i: (-i.get("deliveryWeeks", 0), -i["score"]),
        )[:top_n]

        # FNO: F&O eligible + directional signal (BUY or SELL)
        fno = sorted(
            [i for i in items
             if i["symbol"] in FNO_STOCKS
             and i["action"] in ("BUY", "SELL")
             and "fnoRecommendation" in i],
            key=lambda i: (i["action"] != "BUY", -i["score"]),
        )[:top_n]

        # Combined top list (all categories, deduplicated, BUY first)
        items.sort(key=lambda r: (r["action"] != "BUY", -r["score"]))
        watchlist = items[:top_n]

        payload = {
            "updatedAt":  datetime.now(IST).isoformat(),
            "updated_at": datetime.now(IST).isoformat(),   # legacy key
            "scanned":    len(items),
            "universe":   len(syms),
            "items":      watchlist,
            "intraday":   intraday,
            "delivery":   delivery,
            "fno":        fno,
        }
        try:
            from app.utils.redis_client import cache_set
            await cache_set(_WATCHLIST_KEY, json.dumps(payload, default=str), expire=86400)
        except Exception as exc:
            logger.warning("watchlist save failed: %s", exc)

        logger.info("Market scan complete",
                    extra={"log_type": "ai_engine", "event": "market_scan",
                           "scanned": len(items), "watchlist": len(watchlist),
                           "intraday": len(intraday), "delivery": len(delivery),
                           "fno": len(fno)})
        return watchlist


async def get_watchlist() -> dict:
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(_WATCHLIST_KEY)
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.debug("watchlist load failed: %s", exc)
    return {"updatedAt": None, "updated_at": None,
            "scanned": 0, "universe": 0,
            "items": [], "intraday": [], "delivery": [], "fno": []}


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
