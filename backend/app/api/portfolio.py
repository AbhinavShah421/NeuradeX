"""
Portfolio API Routes — backed by Groww holdings/positions with simulation fallback.
"""

import asyncio
import json
import os
import random
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# ── Yahoo live-price fallback ─────────────────────────────────────────────────
# Groww's /live-data/ltp needs the paid Live-Data entitlement; when it's not
# available the holdings would otherwise show current = avg (0 P&L). Yahoo gives
# near-real-time NSE prices for free, so we use it to fill any symbol Groww
# couldn't price — the same source the scanner and live sessions already use.
_YH_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_YH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


async def _yahoo_quote(client: httpx.AsyncClient, symbol: str) -> dict | None:
    """Return {'ltp': price, 'prev_close': previous-day close} for an NSE symbol."""
    try:
        r = await client.get(_YH_BASE + f"{symbol}.NS",
                             params={"interval": "1d", "range": "5d"}, headers=_YH_HEADERS)
        if r.status_code != 200:
            return None
        res = (r.json().get("chart", {}).get("result") or [None])[0]
        if not res:
            return None
        meta = res.get("meta") or {}
        ltp = meta.get("regularMarketPrice")
        # previousClose = the actual prior-day close (for the 1D return).
        # chartPreviousClose is the close before the requested range, so it's only
        # a last resort (it would overstate the day change).
        prev = meta.get("previousClose") or meta.get("chartPreviousClose")
        if not (ltp and float(ltp) > 0):
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            closes = [c for c in (q.get("close") or []) if c]
            if not closes:
                return None
            ltp = closes[-1]
            if prev is None and len(closes) >= 2:
                prev = closes[-2]
        return {"ltp": float(ltp), "prev_close": float(prev) if prev else None}
    except Exception:
        return None


async def _yahoo_quote_map(symbols: list[str]) -> dict:
    """{symbol: {'ltp', 'prev_close'}} fetched from Yahoo concurrently."""
    out: dict = {}
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        async def one(s: str) -> None:
            async with sem:
                q = await _yahoo_quote(client, s)
                if q:
                    out[s] = q
        await asyncio.gather(*(one(s) for s in symbols))
    return out

# Fallback data — user's real Groww holdings approximated from last known weights.
# Used when the Groww API session is not yet TOTP-approved.
SIM_HOLDINGS = [
    {"symbol": "TMCV",       "quantity": 100, "average_price": 340.0,  "current_price": 378.0},
    {"symbol": "TMPV",       "quantity": 100, "average_price": 320.0,  "current_price": 356.0},
    {"symbol": "SBIN",       "quantity": 23,  "average_price": 770.0,  "current_price": 830.0},
    {"symbol": "INDUSINDBK", "quantity": 11,  "average_price": 800.0,  "current_price": 870.0},
    {"symbol": "PNB",        "quantity": 72,  "average_price": 90.0,   "current_price": 100.0},
    {"symbol": "FEDERALBNK", "quantity": 46,  "average_price": 170.0,  "current_price": 185.0},
    {"symbol": "IREDA",      "quantity": 36,  "average_price": 160.0,  "current_price": 175.0},
    {"symbol": "JKTYRE",     "quantity": 19,  "average_price": 370.0,  "current_price": 400.0},
    {"symbol": "ZEEL",       "quantity": 33,  "average_price": 125.0,  "current_price": 138.0},
    {"symbol": "IOB",        "quantity": 44,  "average_price": 48.0,   "current_price": 55.0},
    {"symbol": "SUZLON",     "quantity": 47,  "average_price": 50.0,   "current_price": 58.0},
    {"symbol": "IDBI",       "quantity": 20,  "average_price": 65.0,   "current_price": 72.0},
    {"symbol": "SYNCOMF",    "quantity": 69,  "average_price": 8.0,    "current_price": 10.0},
    {"symbol": "SHREEGANES", "quantity": 30,  "average_price": 8.0,    "current_price": 10.0},
    {"symbol": "VIKASECO",   "quantity": 36,  "average_price": 1.5,    "current_price": 2.0},
    {"symbol": "TRIVENIENT", "quantity": 44,  "average_price": 1.5,    "current_price": 2.0},
    {"symbol": "CROISSANCE", "quantity": 7,   "average_price": 1.5,    "current_price": 2.0},
]


class Alert(BaseModel):
    symbol: str
    alert_type: str
    condition: str
    enabled: bool


def _build_portfolio(holdings: list, ltp_map: dict, prev_map: dict | None = None) -> dict:
    """
    Build portfolio dict from Groww holdings + a symbol→LTP map.
    Groww holdings endpoint returns only: trading_symbol, quantity, average_price.
    Current prices come from a separate LTP call (Groww live-data, else Yahoo).
    prev_map: optional {symbol: previous-day close} → used for the 1D return.
    """
    prev_map = prev_map or {}
    stocks = []
    for h in holdings:
        symbol = h.get("trading_symbol", h.get("symbol", ""))
        qty = float(h.get("quantity", 0))
        purchase = float(h.get("average_price", h.get("purchase_price", 0)))

        # LTP lookup — try NSE then BSE key
        ltp = ltp_map.get(f"NSE_{symbol}") or ltp_map.get(f"BSE_{symbol}")
        if ltp is not None:
            current = float(ltp)
        else:
            # Last resort: use avg price (shows 0 gain, clearly wrong rather than misleadingly random)
            current = purchase

        prev_close = prev_map.get(symbol)
        day_change = round((current - float(prev_close)) * qty, 2) if prev_close else 0.0

        value = round(current * qty, 2)
        gain = round((current - purchase) * qty, 2)
        gain_pct = round(((current - purchase) / purchase) * 100, 2) if purchase else 0.0
        stocks.append({
            "symbol": symbol,
            "quantity": int(qty),
            "purchase_price": purchase,
            "current_price": current,
            "value": value,
            "gain": gain,
            "gain_percent": gain_pct,
            "day_change": day_change,
        })

    total_value = round(sum(s["value"] for s in stocks), 2)
    total_gain = round(sum(s["gain"] for s in stocks), 2)
    total_invested = round(sum(s["purchase_price"] * s["quantity"] for s in stocks), 2)
    gain_pct = round((total_gain / total_invested) * 100, 2) if total_invested else 0.0
    day_change = round(sum(s["day_change"] for s in stocks), 2)
    prev_value = total_value - day_change
    day_change_pct = round((day_change / prev_value) * 100, 2) if prev_value else 0.0
    return {
        "total_value": total_value,
        "total_invested": total_invested,
        "total_gain": total_gain,
        "gain_percent": gain_pct,
        "day_change": day_change,
        "day_change_percent": day_change_pct,
        "stocks": stocks,
        "cash_available": round(random.uniform(5000, 50000), 2),
        "updated_at": datetime.now().isoformat(),
    }


@router.get("/")
async def get_portfolio():
    """Portfolio holdings — live from Groww (holdings + LTP), else simulation."""
    client = get_groww_client()
    if client:
        try:
            logger.info(
                "Calling Groww get_holdings",
                extra={"log_type": "groww_call", "caller": "portfolio.get_portfolio", "method": "get_holdings"},
            )
            raw = await client.get_holdings()
            if raw:
                # Groww holdings has no price data — fetch LTP separately for all symbols
                symbols = [
                    h.get("trading_symbol", h.get("symbol", ""))
                    for h in raw
                    if h.get("trading_symbol") or h.get("symbol")
                ]
                ltp_map: dict = {}
                if symbols:
                    try:
                        # Fetch NSE prices first
                        logger.info(
                            "Calling Groww get_ltp for holdings",
                            extra={"log_type": "groww_call", "caller": "portfolio.get_portfolio", "method": "get_ltp", "symbols": symbols, "exchange": "NSE"},
                        )
                        ltp_data = await client.get_ltp(symbols, exchange="NSE")
                        ltp_map = ltp_data if isinstance(ltp_data, dict) else {}

                        # For symbols with no NSE price, try BSE
                        missing = [s for s in symbols if not ltp_map.get(f"NSE_{s}")]
                        if missing:
                            try:
                                logger.info(
                                    "Calling Groww get_ltp (BSE fallback) for holdings",
                                    extra={"log_type": "groww_call", "caller": "portfolio.get_portfolio", "method": "get_ltp", "symbols": missing, "exchange": "BSE"},
                                )
                                bse_data = await client.get_ltp(missing, exchange="BSE")
                                if isinstance(bse_data, dict):
                                    ltp_map.update(bse_data)
                            except Exception:
                                pass
                    except Exception as ltp_err:
                        logger.warning(
                            "LTP fetch for holdings failed",
                            extra={"log_type": "portfolio_event", "event": "ltp_fallback", "error": str(ltp_err)},
                        )

                    # Groww live-data is entitlement-gated. Fetch Yahoo quotes for
                    # all holdings to (a) fill any price Groww couldn't return so
                    # current values are real instead of collapsing to the average,
                    # and (b) get each previous close for the 1D return.
                    prev_map: dict = {}
                    try:
                        yq = await _yahoo_quote_map(symbols)
                        filled = 0
                        for s in symbols:
                            q = yq.get(s)
                            if not q:
                                continue
                            if q.get("prev_close"):
                                prev_map[s] = q["prev_close"]
                            if not (ltp_map.get(f"NSE_{s}") or ltp_map.get(f"BSE_{s}")) and q.get("ltp"):
                                ltp_map[f"NSE_{s}"] = q["ltp"]
                                filled += 1
                        logger.info(
                            "Yahoo quotes: priced %d holdings, %d prev-closes",
                            filled, len(prev_map),
                            extra={"log_type": "portfolio_event", "event": "yahoo_quote_fallback",
                                   "filled": filled, "prev_closes": len(prev_map)},
                        )
                    except Exception as yh_err:
                        logger.warning("Yahoo quote fallback failed: %s", yh_err,
                                       extra={"log_type": "portfolio_event", "event": "yahoo_quote_error"})

                return {"status": "success", "data": _build_portfolio(raw, ltp_map, prev_map)}
        except Exception as e:
            logger.warning(
                "Groww holdings fetch failed, using simulation",
                extra={"log_type": "portfolio_event", "event": "holdings_fallback", "error": str(e)},
            )

    # Simulation fallback — use base prices as "current"
    sim_ltp = {f"NSE_{h['symbol']}": h["current_price"] for h in SIM_HOLDINGS}
    return {"status": "success", "data": _build_portfolio(
        [{"trading_symbol": h["symbol"], "quantity": h["quantity"], "average_price": h["average_price"]} for h in SIM_HOLDINGS],
        sim_ltp,
    )}


# ── AI Portfolio Optimizer ────────────────────────────────────────────────────
# Flow: real holdings (live prices/P&L) → per-holding AI signal from live daily
# indicators → portfolio risk (concentration/sector/losers) → higher-conviction
# opportunities from the AI scanner watchlist → LLM (Claude) synthesises a
# structured rebalancing plan. A deterministic rule engine produces the same
# shape as a fallback, so the feature works even with the LLM disabled.

def _sma(vals: list[float], n: int) -> float:
    if not vals:
        return 0.0
    return sum(vals[-n:]) / min(n, len(vals))


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return round(100 - 100 / (1 + rs), 1)


async def _daily_series(client: httpx.AsyncClient, symbol: str) -> dict | None:
    try:
        r = await client.get(_YH_BASE + f"{symbol}.NS",
                             params={"interval": "1d", "range": "6mo"}, headers=_YH_HEADERS)
        if r.status_code != 200:
            return None
        res = (r.json().get("chart", {}).get("result") or [None])[0]
        if not res:
            return None
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        closes = [c for c in (q.get("close") or []) if c]
        highs = [h for h in (q.get("high") or []) if h]
        lows = [l for l in (q.get("low") or []) if l]
        return {"closes": closes, "highs": highs, "lows": lows} if len(closes) >= 20 else None
    except Exception:
        return None


def _holding_signal(series: dict) -> dict:
    closes, highs, lows = series["closes"], series["highs"], series["lows"]
    price = closes[-1]
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    rsi = _rsi(closes)
    mom = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0.0
    atr = (sum(highs[i] - lows[i] for i in range(len(closes) - 14, len(closes))) / 14
           if len(closes) >= 14 and len(highs) >= 14 and len(lows) >= 14 else 0.0)
    atr_pct = round(atr / price * 100, 2) if price else 0.0
    up = price > sma20 and sma20 >= sma50
    down = price < sma20 and sma20 < sma50
    if up and mom > 0 and rsi < 72:
        signal = "bullish"
    elif down or mom < -3 or rsi > 82:
        signal = "bearish"
    else:
        signal = "neutral"
    health = 50 + (15 if up else (-15 if down else 0)) + max(-15, min(15, mom * 1.5)) \
        + (10 if 45 <= rsi <= 65 else (-8 if (rsi > 75 or rsi < 30) else 0))
    return {
        "signal": signal,
        "health": int(max(0, min(100, round(health)))),
        "rsi": rsi,
        "momentum_pct": round(mom, 2),
        "atr_pct": atr_pct,
        "sma_trend": "up" if sma20 >= sma50 else "down",
    }


def _portfolio_risk(stocks: list[dict]) -> dict:
    tv = sum(s["value"] for s in stocks) or 1.0
    weights = [(s["symbol"], s["value"] / tv) for s in stocks]
    top_sym, top_w = max(weights, key=lambda x: x[1])
    hhi = round(sum(w * w for _, w in weights), 4)              # Herfindahl concentration (1 = single stock)
    sect: dict = {}
    for s in stocks:
        sect[s.get("sector", "Other")] = sect.get(s.get("sector", "Other"), 0.0) + s["value"] / tv * 100
    top_sector, top_sector_pct = max(sect.items(), key=lambda x: x[1]) if sect else ("—", 0.0)
    losers = sum(1 for s in stocks if s.get("gain_percent", 0) < 0)
    return {
        "holdings": len(stocks),
        "top_symbol": top_sym,
        "top_weight_pct": round(top_w * 100, 1),
        "hhi": hhi,
        "effective_holdings": round(1 / hhi, 1) if hhi else len(stocks),
        "top_sector": top_sector,
        "top_sector_pct": round(top_sector_pct, 1),
        "losers": losers,
        "sector_breakdown": {k: round(v, 1) for k, v in sorted(sect.items(), key=lambda x: -x[1])},
    }


async def _ai_opportunities(held: set) -> list[dict]:
    """Top-graded BUY names from the live AI scanner watchlist that aren't held."""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:watchlist")
        items = json.loads(raw).get("items", []) if raw else []
    except Exception:
        items = []
    out = []
    for it in items:
        sym = (it.get("symbol") or "").upper()
        if not sym or sym in held:
            continue
        if it.get("grade") in ("A", "B") and it.get("action") == "BUY":
            out.append({
                "symbol": sym, "name": it.get("name", sym),
                "grade": it.get("grade"), "action": it.get("action"),
                "win_probability": it.get("win_probability"),
                "price": it.get("price"),
                "reasoning": (it.get("reasoning") or "")[:160],
            })
    return out[:10]


def _action_reason(action: str, sig: str, pnl: float, weight_pct: float) -> str:
    if action == "EXIT":
        return f"AI signal {sig}, down {pnl:.1f}% — cut a losing position the model no longer favours."
    if action == "TRIM":
        if weight_pct > 18:
            return f"Oversized at {weight_pct:.0f}% of the book — trim to cap single-name risk."
        return f"AI signal {sig} — reduce exposure while the setup is weak."
    if action == "ADD":
        return f"AI signal {sig}, up {pnl:.1f}% and underweight — let a working position run."
    return f"AI signal {sig} — hold; no change warranted."


def _baseline_plan(stocks: list[dict], signals: dict, risk: dict, opps: list[dict]) -> dict:
    tv = sum(s["value"] for s in stocks) or 1.0
    rows = []
    for s in stocks:
        w = s["value"] / tv
        wp = w * 100
        sig = signals.get(s["symbol"], {}).get("signal", "neutral")
        pnl = s.get("gain_percent", 0.0)
        if sig == "bearish" and pnl < -8:
            act, f = "EXIT", 0.0
        elif wp > 18:
            act, f = "TRIM", 0.6
        elif sig == "bearish":
            act, f = "TRIM", 0.7
        elif sig == "bullish" and wp < 8 and pnl >= 0:
            act, f = "ADD", 1.4
        else:
            act, f = "HOLD", 1.0
        rows.append((s, w, f, act, sig, pnl, wp))

    raw_targets = {s["symbol"]: w * f for (s, w, f, *_rest) in rows}
    tot = sum(raw_targets.values()) or 1.0
    actions = []
    for (s, w, f, act, sig, pnl, wp) in rows:
        actions.append({
            "symbol": s["symbol"],
            "action": act,
            "current_weight_pct": round(wp, 1),
            "target_weight_pct": round(raw_targets[s["symbol"]] / tot * 100, 1),
            "reason": _action_reason(act, sig, pnl, wp),
        })
    add_c = [{
        "symbol": o["symbol"],
        "suggested_weight_pct": 5.0,
        "reason": f"AI watchlist grade {o.get('grade')} BUY"
                  + (f" · {int((o.get('win_probability') or 0) * 100)}% win prob" if o.get("win_probability") else ""),
    } for o in opps[:3]]

    warnings = []
    if risk["top_weight_pct"] > 20:
        warnings.append(f"Concentration: {risk['top_symbol']} is {risk['top_weight_pct']:.0f}% of the portfolio.")
    if risk["top_sector_pct"] > 40:
        warnings.append(f"Sector skew: {risk['top_sector']} is {risk['top_sector_pct']:.0f}% of the book.")
    if risk["losers"] >= max(1, int(risk["holdings"] * 0.6)):
        warnings.append(f"{risk['losers']} of {risk['holdings']} holdings are underwater.")

    n_exit = sum(1 for a in actions if a["action"] == "EXIT")
    n_trim = sum(1 for a in actions if a["action"] == "TRIM")
    n_add = sum(1 for a in actions if a["action"] == "ADD")
    return {
        "summary": f"{n_exit} exit, {n_trim} trim and {n_add} add suggestions across {len(stocks)} holdings, "
                   f"with {len(add_c)} new high-conviction candidates from the AI scanner.",
        "objective": "Cut weak/oversized positions, rotate into higher-conviction AI picks, and reduce concentration.",
        "actions": actions,
        "add_candidates": add_c,
        "risk_warnings": warnings,
        "expected_effect": "Lower single-name and sector concentration; tilt the book toward names the AI rates BUY.",
    }


def _parse_llm_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = text.strip()
    if "```" in t:                                   # strip code fences
        t = t.split("```")[1] if t.count("```") >= 2 else t
        t = t.replace("json", "", 1).strip() if t.lower().startswith("json") else t
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1 or b <= a:
        return None
    try:
        obj = json.loads(t[a:b + 1])
        return obj if isinstance(obj, dict) and obj.get("actions") else None
    except Exception:
        return None


def _optimize_prompt(stocks: list[dict], signals: dict, risk: dict, opps: list[dict],
                     total_value: float, gain_pct: float) -> str:
    rows = "SYMBOL | SECTOR | WEIGHT% | P&L% | DAY% | AI SIGNAL | HEALTH | RSI | TREND | MOM%\n"
    tv = sum(s["value"] for s in stocks) or 1.0
    for s in stocks:
        sg = signals.get(s["symbol"], {})
        rows += (f"{s['symbol']} | {s.get('sector','Other')} | {s['value']/tv*100:.1f} | "
                 f"{s.get('gain_percent',0):+.1f} | {round((s.get('day_change',0)/ (s['value'] or 1))*100,2):+.2f} | "
                 f"{sg.get('signal','?')} | {sg.get('health','?')} | {sg.get('rsi','?')} | "
                 f"{sg.get('sma_trend','?')} | {sg.get('momentum_pct','?')}\n")
    opp_rows = "\n".join(
        f"- {o['symbol']} ({o.get('grade')} BUY, "
        f"{int((o.get('win_probability') or 0)*100)}% win prob): {o.get('reasoning','')}"
        for o in opps) or "- (none surfaced right now)"
    return f"""You are an expert Indian-equity (NSE) portfolio manager. Optimise this REAL portfolio.

PORTFOLIO: value ₹{total_value:,.0f}, total return {gain_pct:+.1f}%, {risk['holdings']} holdings.
CONCENTRATION: largest = {risk['top_symbol']} at {risk['top_weight_pct']:.0f}%; effective holdings ≈ {risk['effective_holdings']}; top sector = {risk['top_sector']} at {risk['top_sector_pct']:.0f}%; {risk['losers']} underwater.

HOLDINGS (with live AI signals):
{rows}
HIGHER-CONVICTION OPPORTUNITIES (live AI scanner, not currently held):
{opp_rows}

Decide an action for EVERY holding (EXIT / TRIM / HOLD / ADD) and propose target weights that sum to ~100%. Favour trimming oversized or AI-bearish names, holding/adding AI-bullish ones, and rotating freed capital into the opportunities. Long-only.

Respond with ONLY valid JSON (no prose, no code fence) in this exact schema:
{{"summary": str, "objective": str,
 "actions": [{{"symbol": str, "action": "EXIT|TRIM|HOLD|ADD", "current_weight_pct": number, "target_weight_pct": number, "reason": str}}],
 "add_candidates": [{{"symbol": str, "suggested_weight_pct": number, "reason": str}}],
 "risk_warnings": [str],
 "expected_effect": str}}"""


# ── Order-execution guardrails ────────────────────────────────────────────────
_NSE_TICK         = 0.05                                              # standard NSE equity tick
_ORDER_COLLAR_PCT = float(os.getenv("ORDER_COLLAR_PCT", "0.7")) / 100.0   # LIMIT band vs last price
_MAX_ORDER_VALUE  = float(os.getenv("MAX_ORDER_VALUE", "300000"))    # per-order ₹ cap (fat-finger guard)


def _round_tick(p: float) -> float:
    return round(round(p / _NSE_TICK) * _NSE_TICK, 2)


def _limit_price(side: str, price: float) -> float:
    """A protective LIMIT price: pay a touch above (BUY) / accept a touch below
    (SELL) the last price, never a blind MARKET fill. Band is at least 2 ticks so
    it works on low-priced/illiquid names too."""
    band = max(price * _ORDER_COLLAR_PCT, 2 * _NSE_TICK)
    raw = price + band if side == "BUY" else price - band
    return _round_tick(max(_NSE_TICK, raw))


def _build_trade(side: str, qty: int, price: float, exchange: str) -> dict | None:
    if qty <= 0 or price <= 0:
        return None
    if side == "BUY":                                   # cap order value to avoid fat-finger buys
        qty = min(qty, int(_MAX_ORDER_VALUE // price))
    if qty <= 0:
        return None
    lp = _limit_price(side, price)
    return {
        "transaction_type": side, "quantity": int(qty),
        "order_type": "LIMIT", "limit_price": lp,
        "exchange": exchange, "product": "CNC",
        "est_value": round(qty * lp, 2),
    }


def _exchange_for(symbol: str, master: dict) -> str:
    ex = (master.get(symbol, {}) or {}).get("exchange", "NSE")
    return "BSE" if ex == "BSE" else "NSE"             # BOTH/NSE → NSE


def _enrich_actions(plan: dict, stocks: list[dict], signals: dict, opps: list[dict], total_value: float) -> None:
    """Attach executable trade quantities to every action and an AI 'best
    alternative' (same-sector preferred) to every at-risk / non-performing one,
    so the UI can show swaps and place real orders."""
    hmap = {s["symbol"]: s for s in stocks}
    try:
        from app.data.stocks_master import STOCKS_BY_SYMBOL
    except Exception:
        STOCKS_BY_SYMBOL = {}
    pool = [{**o, "sector": (STOCKS_BY_SYMBOL.get(o["symbol"], {}) or {}).get("sector", "Other")} for o in opps]

    actions = plan.get("actions") or []
    # 1) Protective LIMIT trades for every actionable row (per-holding exchange,
    #    price collar, value cap — all from _build_trade).
    for a in actions:
        h = hmap.get(a.get("symbol"))
        price = float(h["current_price"]) if h else 0.0
        qty = int(h["quantity"]) if h else 0
        cw = a.get("current_weight_pct") or 0
        tw = a.get("target_weight_pct") or 0
        ex = _exchange_for(a.get("symbol", ""), STOCKS_BY_SYMBOL)
        a["current_price"] = round(price, 2)
        a["quantity"] = qty
        a["pnl_pct"] = round(float(h["gain_percent"]), 2) if h and h.get("gain_percent") is not None else None
        trade = None
        act = a.get("action")
        if act == "EXIT" and qty > 0:
            trade = _build_trade("SELL", qty, price, ex)            # full exit (no value cap on sells)
        elif act == "TRIM" and qty > 0 and price > 0 and cw > tw:
            sq = max(1, min(qty, round((cw - tw) / 100 * total_value / price)))
            trade = _build_trade("SELL", sq, price, ex)
        elif act == "ADD" and price > 0 and tw > cw:
            bq = max(1, round((tw - cw) / 100 * total_value / price))
            trade = _build_trade("BUY", bq, price, ex)
        a["trade"] = trade

    # 2) Best alternative for at-risk / non-performing holdings (biggest first)
    at_risk = [a for a in actions
               if a.get("action") in ("EXIT", "TRIM")
               or signals.get(a.get("symbol"), {}).get("signal") == "bearish"]
    at_risk.sort(key=lambda a: -(a.get("current_weight_pct") or 0))
    used: set = set()
    for a in at_risk:
        h = hmap.get(a.get("symbol"))
        sec = (h.get("sector") if h else "Other") or "Other"
        cw = a.get("current_weight_pct") or 0
        tw = a.get("target_weight_pct") or 0
        freed = (cw if a.get("action") == "EXIT" else max(0.0, cw - tw)) / 100 * total_value
        cand = next((o for o in pool if o["symbol"] not in used and o["sector"] == sec and sec != "Other"), None) \
            or next((o for o in pool if o["symbol"] not in used), None)
        if not cand:
            continue
        used.add(cand["symbol"])
        ap = float(cand.get("price") or 0)
        same = cand["sector"] == sec and sec != "Other"
        raw_qty = max(1, round(freed / ap)) if ap > 0 and freed > 0 else 0
        alt_ex = _exchange_for(cand["symbol"], STOCKS_BY_SYMBOL)
        order = _build_trade("BUY", raw_qty, ap, alt_ex) if raw_qty > 0 else None
        a["alternative"] = {
            "symbol": cand["symbol"], "name": cand.get("name"), "grade": cand.get("grade"),
            "win_probability": cand.get("win_probability"), "sector": cand["sector"],
            "same_sector": same,
            "scanner_reasoning": cand.get("reasoning"),          # the AI scanner's evidence for the pick
            "price": round(ap, 2),
            "buy_qty": (order["quantity"] if order else 0),     # value-capped qty
            "order": order,
            "reason": (f"Same sector ({sec}) · " if same else "")
                      + f"AI {cand.get('grade')} BUY"
                      + (f" · {int((cand.get('win_probability') or 0) * 100)}% win prob" if cand.get("win_probability") else ""),
        }


_OPT_CACHE_KEY = "portfolio:optimization:v4"   # bump to invalidate stale-shape caches


async def _latest_scan_version() -> str | None:
    """The scanner watchlist's updated_at — the optimization is keyed to it so it
    auto-refreshes whenever a newer AI scan lands."""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:watchlist")
        return json.loads(raw).get("updated_at") if raw else None
    except Exception:
        return None


@router.get("/optimize")
async def optimize_portfolio(use_llm: bool = True, refresh: bool = False):
    """AI-driven portfolio optimization: live signals + risk + scanner opportunities,
    synthesised by the LLM into a rebalancing plan (deterministic fallback).

    The result is persisted in Redis and keyed to the latest AI scan: it's served
    from cache instantly while the scan is unchanged, and auto-recomputed when a
    newer scan lands (or when refresh=true)."""
    from app.utils.redis_client import cache_get, cache_set
    scan_at = await _latest_scan_version()

    # Serve the persisted plan unless a newer scan has landed or a refresh is forced.
    if not refresh:
        try:
            raw = await cache_get(_OPT_CACHE_KEY)
            if raw:
                cached = json.loads(raw)
                if cached.get("scan_at") == scan_at and cached.get("plan", {}).get("actions"):
                    cached["cached"] = True
                    return {"status": "success", "data": cached}
        except Exception:
            pass

    port = (await get_portfolio())["data"]
    stocks = port.get("stocks", [])
    if not stocks:
        return {"status": "success", "data": {"as_of": datetime.now().isoformat(),
                "plan": {"summary": "No holdings to optimise.", "actions": [], "add_candidates": [],
                         "risk_warnings": [], "objective": "", "expected_effect": ""},
                "risk": {}, "signals": {}, "opportunities": [], "source": "none", "scan_at": scan_at, "cached": False}}

    # Sector enrichment
    try:
        from app.data.stocks_master import STOCKS_BY_SYMBOL
        for s in stocks:
            s["sector"] = (STOCKS_BY_SYMBOL.get(s["symbol"], {}) or {}).get("sector", "Other")
    except Exception:
        for s in stocks:
            s.setdefault("sector", "Other")

    # Per-holding AI signals from live daily indicators (concurrent)
    signals: dict = {}
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        async def sig(s):
            async with sem:
                series = await _daily_series(client, s["symbol"])
                if series:
                    signals[s["symbol"]] = _holding_signal(series)
        await asyncio.gather(*(sig(s) for s in stocks))

    risk = _portfolio_risk(stocks)
    held = {s["symbol"] for s in stocks}
    opps = await _ai_opportunities(held)
    baseline = _baseline_plan(stocks, signals, risk, opps)

    plan, source = baseline, "rule-based"
    if use_llm:
        try:
            from app.utils.llm_client import llm_chat
            text = await llm_chat(
                _optimize_prompt(stocks, signals, risk, opps, port.get("total_value", 0), port.get("gain_percent", 0)),
                system="You are a precise portfolio optimiser. Output only valid JSON.",
                temperature=0.2, max_tokens=1600, timeout=45.0,
            )
            parsed = _parse_llm_json(text)
            if parsed:
                # Trust the platform for real current weights; the LLM only owns
                # the action + target weight + reasoning. This guarantees the table
                # always shows real NOW values and a numeric TARGET.
                tv = sum(s["value"] for s in stocks) or 1.0
                wmap = {s["symbol"]: round(s["value"] / tv * 100, 1) for s in stocks}
                norm = []
                for a in (parsed.get("actions") or []):
                    sym = (a.get("symbol") or "").upper()
                    cw = wmap.get(sym, a.get("current_weight_pct") or 0)
                    try:
                        tw = round(float(a.get("target_weight_pct")), 1)
                    except (TypeError, ValueError):
                        tw = cw
                    act = (a.get("action") or "HOLD").upper()
                    if act not in ("EXIT", "TRIM", "HOLD", "ADD"):
                        act = "HOLD"
                    norm.append({"symbol": sym, "action": act, "current_weight_pct": cw,
                                 "target_weight_pct": tw, "reason": a.get("reason") or ""})
                parsed["actions"] = norm or baseline["actions"]
                parsed.setdefault("add_candidates", baseline["add_candidates"])
                parsed.setdefault("risk_warnings", baseline["risk_warnings"])
                parsed.setdefault("objective", baseline["objective"])
                parsed.setdefault("expected_effect", baseline["expected_effect"])
                plan, source = parsed, "ai"
        except Exception as exc:
            logger.warning("LLM optimize failed, using rule-based plan: %s", exc,
                           extra={"log_type": "portfolio_event", "event": "optimize_llm_error"})

    # Attach executable trade sizes + AI alternatives for at-risk holdings.
    try:
        _enrich_actions(plan, stocks, signals, opps, port.get("total_value", 0) or 0)
    except Exception as exc:
        logger.warning("action enrichment failed: %s", exc,
                       extra={"log_type": "portfolio_event", "event": "optimize_enrich_error"})

    data = {
        "as_of": datetime.now().isoformat(),
        "scan_at": scan_at,
        "cached": False,
        "source": source,
        "portfolio": {"total_value": port.get("total_value"), "total_invested": port.get("total_invested"),
                      "gain_percent": port.get("gain_percent"), "day_change": port.get("day_change")},
        "risk": risk,
        "signals": signals,
        "opportunities": opps,
        "plan": plan,
    }
    # Persist so the plan survives reloads/restarts and is shown instantly next time.
    try:
        await cache_set(_OPT_CACHE_KEY, json.dumps(data), expire=86400 * 7)
    except Exception:
        pass
    return {"status": "success", "data": data}


# ── AI "Invest this amount" — divide capital across the best AI-watchlist picks ─

async def _invest_candidates(max_stocks: int) -> list[dict]:
    """Best A/B-grade BUY names from the live AI scanner (delivery + intraday,
    de-duplicated, ranked by grade then win-probability)."""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:watchlist")
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    pool: dict = {}
    for it in (data.get("delivery") or []) + (data.get("items") or []):   # delivery first (hold-friendly)
        sym = (it.get("symbol") or "").upper()
        if not sym or sym in pool:
            continue
        if it.get("grade") in ("A", "B") and it.get("action") == "BUY" and float(it.get("price") or 0) > 0:
            pool[sym] = it
    cands = list(pool.values())
    gr = {"A": 0, "B": 1, "C": 2, "D": 3}
    cands.sort(key=lambda x: (gr.get(x.get("grade", "D"), 3), -(x.get("win_probability") or 0)))
    return cands[:max_stocks]


@router.get("/invest-plan")
async def invest_plan(amount: float, max_stocks: int = 6):
    """Agentic allocation: split `amount` across the best AI picks, weighted by
    conviction (win probability, capped for diversification), as protective LIMIT
    buy orders sized to real prices."""
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    cands = await _invest_candidates(max_stocks)
    if not cands:
        return {"status": "success", "data": {"amount": amount, "deployed": 0, "leftover": amount,
                "count": 0, "picks": [], "note": "No A/B-grade BUY picks available yet — try after the next AI scan."}}

    try:
        from app.data.stocks_master import STOCKS_BY_SYMBOL
    except Exception:
        STOCKS_BY_SYMBOL = {}

    # Conviction weights (win probability), capped at 35% each for diversification.
    raw_w = {c["symbol"]: max(0.05, float(c.get("win_probability") or 0.5)) for c in cands}
    tot = sum(raw_w.values()) or 1.0
    weights = {k: min(0.35, v / tot) for k, v in raw_w.items()}
    s = sum(weights.values()) or 1.0
    weights = {k: v / s for k, v in weights.items()}

    picks = []
    deployed = 0.0
    for c in cands:
        sym = c["symbol"]
        price = float(c["price"])
        ex = _exchange_for(sym, STOCKS_BY_SYMBOL)
        limit = _limit_price("BUY", price)
        alloc = amount * weights[sym]
        qty = int(alloc // limit)
        if qty < 1:
            continue                                    # allocation can't afford one share
        cost = round(qty * limit, 2)
        deployed += cost
        picks.append({
            "symbol": sym, "name": c.get("name", sym), "grade": c.get("grade"),
            "win_probability": c.get("win_probability"),
            "sector": (STOCKS_BY_SYMBOL.get(sym, {}) or {}).get("sector", "Other"),
            "price": round(price, 2), "limit_price": limit,
            "quantity": qty, "est_cost": cost, "reasoning": c.get("reasoning"),
            "order": {"transaction_type": "BUY", "quantity": qty, "order_type": "LIMIT",
                      "limit_price": limit, "exchange": ex, "product": "CNC", "est_value": cost},
        })

    # Greedy top-up: spend leftover on the highest-conviction affordable pick.
    leftover = round(amount - deployed, 2)
    guard = 0
    while picks and guard < 2000:
        guard += 1
        nxt = next((p for p in picks if p["limit_price"] <= leftover), None)
        if not nxt:
            break
        nxt["quantity"] += 1
        nxt["est_cost"] = round(nxt["quantity"] * nxt["limit_price"], 2)
        nxt["order"]["quantity"] = nxt["quantity"]
        nxt["order"]["est_value"] = nxt["est_cost"]
        deployed = round(deployed + nxt["limit_price"], 2)
        leftover = round(leftover - nxt["limit_price"], 2)

    for p in picks:
        p["weight_pct"] = round(p["est_cost"] / amount * 100, 1) if amount else 0.0

    return {"status": "success", "data": {
        "amount": round(amount, 2), "deployed": round(deployed, 2), "leftover": round(amount - deployed, 2),
        "count": len(picks), "picks": picks,
        "as_of": datetime.now().isoformat(),
    }}


@router.post("/add")
async def add_to_portfolio(symbol: str, quantity: int, purchase_price: float):
    """Add stock record (informational — actual orders go through /api/orders)."""
    return {
        "status": "success",
        "data": {
            "symbol": symbol.upper(),
            "quantity": quantity,
            "purchase_price": purchase_price,
            "total_cost": round(quantity * purchase_price, 2),
            "status": "recorded",
            "timestamp": datetime.now().isoformat(),
        },
    }


@router.get("/performance")
async def get_performance():
    """Portfolio performance metrics — simulated."""
    return {
        "status": "success",
        "data": {
            "daily_return": round(random.uniform(-5, 5), 2),
            "weekly_return": round(random.uniform(-10, 15), 2),
            "monthly_return": round(random.uniform(-20, 30), 2),
            "yearly_return": round(random.uniform(-30, 50), 2),
            "sharpe_ratio": round(random.uniform(0.5, 2.5), 2),
            "max_drawdown": round(random.uniform(-30, -5), 2),
            "win_rate": round(random.uniform(0.4, 0.8), 2),
            "average_trade_return": round(random.uniform(0.5, 3.0), 2),
            "updated_at": datetime.now().isoformat(),
        },
    }


@router.get("/alerts")
async def get_alerts():
    """Active price/pattern alerts."""
    return {
        "status": "success",
        "count": 3,
        "data": [
            {"id": 1, "symbol": "SBIN",       "alert_type": "price",     "condition": "Price > ₹850",               "enabled": True,  "created_at": datetime.now().isoformat()},
            {"id": 2, "symbol": "INDUSINDBK","alert_type": "pattern",   "condition": "Bullish engulfing detected", "enabled": True,  "created_at": datetime.now().isoformat()},
            {"id": 3, "symbol": "IREDA",     "alert_type": "sentiment", "condition": "Sentiment > 0.7",           "enabled": False, "created_at": datetime.now().isoformat()},
        ],
    }


@router.post("/alerts")
async def create_alert(alert: Alert):
    """Create a new alert."""
    return {
        "status": "success",
        "data": {
            "id": random.randint(1000, 9999),
            "symbol": alert.symbol,
            "alert_type": alert.alert_type,
            "condition": alert.condition,
            "enabled": alert.enabled,
            "created_at": datetime.now().isoformat(),
        },
    }


# ── AI Sector Exposure Scanner + Optimizer ────────────────────────────────────
# Maps holdings to sectors, scores each sector with the live AI scan, and
# compares the book's sector weights against an AI-favoured target.

def _sector_of(symbol: str) -> str:
    from app.utils.sector_map import sector_of
    return sector_of(symbol)


async def _ranked_items() -> list[dict]:
    """The live AI ranked board (full universe scan), each item sector-tagged."""
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get("ai_engine:ranked")
        data = json.loads(raw) if raw else {}
        items = data.get("items") or []
    except Exception:
        items = []
    for it in items:
        it["sector"] = _sector_of(it.get("symbol", ""))
    return items


async def _sector_ai_scores() -> dict:
    """Aggregate the AI scan by sector → a 0..1 AI score per sector plus its best names."""
    items = await _ranked_items()
    agg: dict[str, dict] = {}
    for it in items:
        sec = it.get("sector", "Other")
        wp = float(it.get("win_probability") or 0.0)
        mom = float((it.get("metrics") or {}).get("momentum_pct") or 0.0)
        a = agg.setdefault(sec, {"n": 0, "buys": 0, "wp_sum": 0.0, "mom_sum": 0.0, "names": []})
        a["n"] += 1
        if it.get("action") == "BUY":
            a["buys"] += 1
        a["wp_sum"] += wp
        a["mom_sum"] += mom
        a["names"].append({
            "symbol": it.get("symbol"), "name": it.get("name"), "grade": it.get("grade"),
            "action": it.get("action"), "win_probability": wp, "price": it.get("price"),
            "momentum_pct": round(mom, 2),
        })
    out: dict[str, dict] = {}
    for sec, a in agg.items():
        n = max(1, a["n"])
        buy_ratio = a["buys"] / n
        avg_wp = a["wp_sum"] / n
        score = round(avg_wp * (0.5 + 0.5 * buy_ratio), 4)
        top = sorted([x for x in a["names"] if x["action"] == "BUY"],
                     key=lambda x: -x["win_probability"])[:5]
        out[sec] = {"score": score, "count": a["n"], "buys": a["buys"],
                    "avg_win_probability": round(avg_wp, 4),
                    "avg_momentum_pct": round(a["mom_sum"] / n, 2), "top": top}
    return out


@router.get("/sector-exposure")
async def sector_exposure():
    """AI sector-exposure scanner + optimizer: the book's current sector weights
    vs an AI-favoured target, with over/under-exposure and rebalance moves."""
    from app.utils.sector_map import ensure_loaded
    await ensure_loaded()
    pf = (await get_portfolio()).get("data", {})
    stocks = pf.get("stocks", [])
    tv = float(pf.get("total_value") or 0.0) or 1.0
    for s in stocks:
        s["sector"] = _sector_of(s.get("symbol", ""))

    cur_val: dict[str, float] = {}
    holders: dict[str, list] = {}
    for s in stocks:
        sec = s["sector"]
        cur_val[sec] = cur_val.get(sec, 0.0) + float(s.get("value") or 0.0)
        holders.setdefault(sec, []).append(s)
    current_pct = {sec: v / tv * 100 for sec, v in cur_val.items()}

    ai = await _sector_ai_scores()
    scored = {sec: a["score"] for sec, a in ai.items() if a["buys"] > 0 and sec != "Other"}
    top_secs = sorted(scored, key=lambda s: -scored[s])[:8]
    tot = sum(scored[s] for s in top_secs) or 1.0
    target_pct = {sec: round(scored[sec] / tot * 100, 1) for sec in top_secs}

    rows = []
    for sec in sorted(set(current_pct) | set(target_pct),
                      key=lambda s: -(current_pct.get(s, 0) + target_pct.get(s, 0))):
        cur = round(current_pct.get(sec, 0.0), 1)
        tgt = round(target_pct.get(sec, 0.0), 1)
        delta = round(cur - tgt, 1)
        status = "overweight" if delta >= 8 else "underweight" if delta <= -8 else "balanced"
        a = ai.get(sec, {})
        rows.append({
            "sector": sec, "current_pct": cur, "target_pct": tgt, "delta": delta, "status": status,
            "ai_score": a.get("score"), "ai_avg_win_probability": a.get("avg_win_probability"),
            "holdings": [s["symbol"] for s in holders.get(sec, [])],
            "holding_count": len(holders.get(sec, [])),
            "ai_top": (a.get("top") or [])[:3],
        })

    hhi = sum((p / 100) ** 2 for p in current_pct.values()) or 0.0
    top_sector, top_pct = max(current_pct.items(), key=lambda x: x[1]) if current_pct else ("—", 0.0)

    suggestions = []
    for r in rows:
        if r["status"] == "overweight" and r["holding_count"] > 0:
            weakest = min(holders[r["sector"]], key=lambda s: s.get("gain_percent", 0))
            suggestions.append({
                "action": "TRIM", "sector": r["sector"], "from_pct": r["current_pct"], "to_pct": r["target_pct"],
                "reason": f"{r['sector']} is {r['delta']:+.0f}% vs the AI target — reduce concentration.",
                "stock": weakest["symbol"],
            })
        elif r["status"] == "underweight" and r["ai_top"]:
            t = r["ai_top"][0]
            suggestions.append({
                "action": "ADD", "sector": r["sector"], "from_pct": r["current_pct"], "to_pct": r["target_pct"],
                "reason": f"AI favours {r['sector']} (score {r['ai_score']}) but the book is {abs(r['delta']):.0f}% light.",
                "stock": t.get("symbol"), "stock_name": t.get("name"),
                "win_probability": t.get("win_probability"), "price": t.get("price"),
            })
    suggestions.sort(key=lambda x: 0 if x["action"] == "ADD" else 1)

    warnings = []
    if top_pct > 40:
        warnings.append(f"{top_sector} is {top_pct:.0f}% of the book — heavy single-sector concentration.")
    if len(current_pct) <= 2 and stocks:
        warnings.append("Spread across very few sectors — diversification is low.")

    return {"status": "success", "data": {
        "total_value": round(tv, 2),
        "sectors": rows,
        "current": {k: round(v, 1) for k, v in sorted(current_pct.items(), key=lambda x: -x[1])},
        "target": target_pct,
        "effective_sectors": round(1 / hhi, 1) if hhi else 0,
        "top_sector": top_sector, "top_sector_pct": round(top_pct, 1),
        "ai_favoured": [{"sector": s, "score": ai[s]["score"]} for s in top_secs],
        "suggestions": suggestions[:8],
        "warnings": warnings,
        "updated_at": datetime.now().isoformat(),
    }}


# ── AI Mutual-Fund-style Baskets (built by scanning the AI ranked board) ───────

def _weight(holdings: list[dict], key="win_probability", cap=0.25) -> list[dict]:
    raw = {h["symbol"]: max(0.05, float(h.get(key) or 0.5)) for h in holdings}
    tot = sum(raw.values()) or 1.0
    w = {k: min(cap, v / tot) for k, v in raw.items()}
    s = sum(w.values()) or 1.0
    for h in holdings:
        h["weight_pct"] = round(w[h["symbol"]] / s * 100, 1)
    return holdings


def _basket_stats(holdings: list[dict]) -> dict:
    secs = sorted({h["sector"] for h in holdings})
    avg_wp = round(sum(h["win_probability"] for h in holdings) / max(1, len(holdings)), 3)
    return {"size": len(holdings), "sectors": len(secs), "sector_list": secs, "avg_win_probability": avg_wp}


async def _basket_pool() -> list[dict]:
    items = await _ranked_items()
    pool, seen = [], set()
    for it in items:
        sym = (it.get("symbol") or "").upper()
        if (sym and sym not in seen and it.get("grade") in ("A", "B")
                and it.get("action") == "BUY" and float(it.get("price") or 0) > 0):
            seen.add(sym)
            pool.append({
                "symbol": sym, "name": it.get("name"), "sector": it.get("sector", "Other"),
                "grade": it.get("grade"), "win_probability": float(it.get("win_probability") or 0.5),
                "price": float(it.get("price")),
                "momentum_pct": float((it.get("metrics") or {}).get("momentum_pct") or 0.0),
            })
    return pool


async def _build_baskets() -> list[dict]:
    pool = await _basket_pool()
    if not pool:
        return []
    gr = {"A": 0, "B": 1}
    baskets: list[dict] = []

    top = sorted(pool, key=lambda x: (gr.get(x["grade"], 2), -x["win_probability"]))[:8]
    baskets.append({"id": "top", "name": "AI Top Picks", "risk": "Moderate",
                    "theme": "Highest-conviction A/B BUYs across the market",
                    "description": "The strongest names the AI scan rates right now, conviction-weighted.",
                    "holdings": _weight([dict(x) for x in top]), "stats": _basket_stats(top)})

    by_sec: dict[str, dict] = {}
    for x in sorted(pool, key=lambda x: -x["win_probability"]):
        if x["sector"] != "Other" and x["sector"] not in by_sec:
            by_sec[x["sector"]] = x
    leaders = sorted(by_sec.values(), key=lambda x: -x["win_probability"])[:8]
    if leaders:
        baskets.append({"id": "sector_leaders", "name": "Sector Leaders", "risk": "Low–Moderate",
                        "theme": "One leader from each strong sector",
                        "description": "Maximum sector diversification — the top AI pick in each leading sector.",
                        "holdings": _weight([dict(x) for x in leaders], cap=0.20), "stats": _basket_stats(leaders)})

    mom = sorted([x for x in pool if x["momentum_pct"] > 0], key=lambda x: -x["momentum_pct"])[:8]
    if mom:
        baskets.append({"id": "momentum", "name": "Momentum Movers", "risk": "High",
                        "theme": "Strongest price momentum among AI BUYs",
                        "description": "Higher-octane: the fastest-rising AI BUY names, momentum-weighted.",
                        "holdings": _weight([dict(x) for x in mom], key="momentum_pct", cap=0.25),
                        "stats": _basket_stats(mom)})

    bal: list[dict] = []
    per_sec: dict[str, int] = {}
    for x in sorted(pool, key=lambda x: (gr.get(x["grade"], 2), -x["win_probability"])):
        if x["sector"] == "Other":
            continue
        if per_sec.get(x["sector"], 0) < 2 and len(bal) < 10:
            bal.append(x); per_sec[x["sector"]] = per_sec.get(x["sector"], 0) + 1
    if bal:
        baskets.append({"id": "balanced", "name": "Balanced Multi-Sector", "risk": "Low",
                        "theme": "Diversified across many sectors (≤2 per sector)",
                        "description": "A fund-like core: spread across sectors to smooth single-name and sector risk.",
                        "holdings": _weight([dict(x) for x in bal], cap=0.15), "stats": _basket_stats(bal)})

    try:
        from app.utils.redis_client import cache_get
        wl = json.loads(await cache_get("ai_engine:watchlist") or "{}")
        comm = wl.get("committed") or []
    except Exception:
        comm = []
    hc = []
    for it in comm:
        if float(it.get("price") or 0) > 0:
            hc.append({"symbol": it.get("symbol"), "name": it.get("name"),
                       "sector": _sector_of(it.get("symbol", "")), "grade": it.get("grade"),
                       "win_probability": float(it.get("win_probability") or 0.5), "price": float(it.get("price")),
                       "momentum_pct": float((it.get("metrics") or {}).get("momentum_pct") or 0.0)})
    if hc:
        baskets.append({"id": "high_conviction", "name": "High-Conviction", "risk": "Concentrated",
                        "theme": "Only the committed, multi-signal-confirmed picks",
                        "description": "The few names that clear every gate (the tier the autopilot paper-trades).",
                        "holdings": _weight([dict(x) for x in hc], cap=0.40), "stats": _basket_stats(hc)})
    return baskets


@router.get("/fund-baskets")
async def fund_baskets():
    """AI-scanned mutual-fund-style baskets — themed, weighted stock baskets built
    from the live AI ranked board."""
    from app.utils.sector_map import ensure_loaded
    await ensure_loaded()
    baskets = await _build_baskets()
    return {"status": "success", "data": {"baskets": baskets, "count": len(baskets),
            "updated_at": datetime.now().isoformat(),
            "note": "Baskets are AI-generated from the live scan and refresh each scan."}}


@router.get("/fund-baskets/invest")
async def fund_basket_invest(basket: str, amount: float):
    """Allocate `amount` across a chosen basket's holdings by weight, as protective
    LIMIT buy orders sized to real prices."""
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    from app.utils.sector_map import ensure_loaded
    await ensure_loaded()
    baskets = await _build_baskets()
    b = next((x for x in baskets if x["id"] == basket), None)
    if not b:
        raise HTTPException(status_code=404, detail=f"basket '{basket}' not found")
    try:
        from app.data.stocks_master import STOCKS_BY_SYMBOL
    except Exception:
        STOCKS_BY_SYMBOL = {}

    picks, deployed = [], 0.0
    for h in b["holdings"]:
        alloc = amount * (h["weight_pct"] / 100)
        price = float(h["price"])
        qty = int(alloc // price)
        ex = _exchange_for(h["symbol"], STOCKS_BY_SYMBOL)
        if qty < 1:
            picks.append({"symbol": h["symbol"], "name": h.get("name"), "sector": h["sector"],
                          "weight_pct": h["weight_pct"], "qty": 0, "skipped": "allocation below 1 share"})
            continue
        limit = _limit_price("BUY", price)
        trade = _build_trade("BUY", qty, limit, ex)
        if not trade:
            continue
        spend = round(qty * limit, 2)
        deployed += spend
        picks.append({"symbol": h["symbol"], "name": h.get("name"), "sector": h["sector"],
                      "weight_pct": h["weight_pct"], "qty": qty, "limit_price": limit,
                      "est_cost": spend, "win_probability": h["win_probability"], "trade": trade})
    return {"status": "success", "data": {
        "basket": b["id"], "basket_name": b["name"], "amount": amount,
        "deployed": round(deployed, 2), "leftover": round(amount - deployed, 2),
        "count": sum(1 for p in picks if p.get("qty")), "picks": picks,
    }}


# ── AI Portfolio Health Score (X-ray) ─────────────────────────────────────────

_GRADE_Q = {"A": 90, "B": 75, "C": 55, "D": 35}


async def _ranked_by_symbol() -> dict:
    items = await _ranked_items()
    return {(it.get("symbol") or "").upper(): it for it in items}


def _grade_from_score(s: float) -> str:
    return ("A" if s >= 85 else "B" if s >= 70 else "C" if s >= 55 else "D" if s >= 40 else "F")


@router.get("/health")
async def portfolio_health():
    """AI Portfolio Health Score (0-100) — a single glanceable measure built from a
    multi-factor model: diversification, concentration, sector balance, holding
    quality (AI grades), performance and drawdown — with the issues + top fixes."""
    from app.utils.sector_map import ensure_loaded
    await ensure_loaded()
    pf = (await get_portfolio()).get("data", {})
    stocks = pf.get("stocks", [])
    tv = float(pf.get("total_value") or 0.0)
    if not stocks or tv <= 0:
        return {"status": "success", "data": {"score": None, "grade": "—",
                "note": "No holdings to analyse yet."}}
    ranked = await _ranked_by_symbol()

    weights = [s["value"] / tv for s in stocks]
    hhi = sum(w * w for w in weights) or 1e-9
    eff_holdings = 1 / hhi
    top_w = max(weights) * 100

    sect_val: dict[str, float] = {}
    for s in stocks:
        sec = _sector_of(s["symbol"])
        sect_val[sec] = sect_val.get(sec, 0.0) + s["value"]
    top_sector, top_sec_val = max(sect_val.items(), key=lambda x: x[1])
    top_sec_pct = top_sec_val / tv * 100
    eff_sectors = 1 / (sum((v / tv) ** 2 for v in sect_val.values()) or 1e-9)

    # Holding quality from AI grades (rated names); unrated = neutral 50.
    q_sum = q_wt = 0.0
    for s, w in zip(stocks, weights):
        it = ranked.get(s["symbol"].upper())
        q = _GRADE_Q.get((it or {}).get("grade"), 50)
        q_sum += q * w; q_wt += w
    quality = q_sum / q_wt if q_wt else 50

    gain_pct = float(pf.get("gain_percent") or 0.0)
    losers = sum(1 for s in stocks if s.get("gain_percent", 0) < 0)
    loser_frac = losers / len(stocks)

    # Sub-scores (0-100)
    f_div = max(0, min(100, eff_holdings / 12 * 100))
    f_conc = 100 if top_w <= 10 else max(0, 100 - (top_w - 10) * 2.5)
    f_sect = 100 if top_sec_pct <= 25 else max(0, 100 - (top_sec_pct - 25) * 2.2)
    f_qual = quality
    f_perf = max(0, min(100, 55 + gain_pct * 2.2))
    f_risk = max(0, 100 - loser_frac * 100)

    W = {"diversification": 0.20, "concentration": 0.15, "sector": 0.15,
         "quality": 0.25, "performance": 0.15, "drawdown": 0.10}
    subs = {"diversification": f_div, "concentration": f_conc, "sector": f_sect,
            "quality": f_qual, "performance": f_perf, "drawdown": f_risk}
    score = round(sum(subs[k] * W[k] for k in W), 1)

    issues, actions = [], []
    if top_w > 20:
        issues.append(f"{stocks[weights.index(max(weights))]['symbol']} is {top_w:.0f}% of the book (single-name risk).")
        actions.append("Trim the largest position toward ≤15%.")
    if top_sec_pct > 40:
        issues.append(f"{top_sector} is {top_sec_pct:.0f}% of the book (sector concentration).")
        actions.append(f"Diversify out of {top_sector} — see Sector Exposure.")
    if eff_holdings < 6:
        issues.append(f"Only ~{eff_holdings:.0f} effective holdings — thinly diversified.")
        actions.append("Add a few uncorrelated names or a Balanced Multi-Sector basket.")
    if quality < 55:
        issues.append("Average holding quality is low on the AI scan (many C/D-grade names).")
        actions.append("Replace weak names with A/B-grade picks (AI Optimize).")
    if loser_frac >= 0.6:
        issues.append(f"{losers}/{len(stocks)} holdings are underwater.")
    if not issues:
        issues.append("No major structural issues — well-balanced book.")

    return {"status": "success", "data": {
        "score": score, "grade": _grade_from_score(score),
        "factors": [{"key": k, "label": k.title(), "score": round(subs[k], 1),
                     "weight": int(W[k] * 100)} for k in W],
        "metrics": {"effective_holdings": round(eff_holdings, 1), "top_weight_pct": round(top_w, 1),
                    "top_sector": top_sector, "top_sector_pct": round(top_sec_pct, 1),
                    "effective_sectors": round(eff_sectors, 1), "avg_quality": round(quality, 1),
                    "gain_pct": gain_pct, "losers": losers, "holdings": len(stocks)},
        "issues": issues, "actions": actions[:4],
        "updated_at": datetime.now().isoformat(),
    }}


# ── Goal-based SIP Planner ─────────────────────────────────────────────────────

_RISK = {
    "conservative": {"return": 0.09, "band": 0.03, "equity": 30, "debt": 60, "gold": 10},
    "moderate":     {"return": 0.12, "band": 0.035, "equity": 60, "debt": 30, "gold": 10},
    "aggressive":   {"return": 0.145, "band": 0.04, "equity": 85, "debt": 10, "gold": 5},
}


def _fv_sip(monthly: float, annual: float, years: float) -> float:
    r = annual / 12
    n = int(years * 12)
    if r == 0:
        return monthly * n
    return monthly * (((1 + r) ** n - 1) / r) * (1 + r)


@router.get("/sip-planner")
async def sip_planner(goal_amount: float = 0, years: float = 10, risk: str = "moderate",
                      current_corpus: float = 0, monthly: float = 0):
    """Goal-based plan: required SIP (or projected corpus from a given SIP), a
    year-by-year projection with optimistic/pessimistic bands, a risk-based asset
    allocation, and AI fund recommendations per sleeve."""
    risk = risk if risk in _RISK else "moderate"
    cfg = _RISK[risk]
    ann = cfg["return"]
    years = max(1.0, min(40.0, years))

    fv_corpus = current_corpus * (1 + ann) ** years
    required_sip = None
    if not monthly and goal_amount > 0:
        need = max(0.0, goal_amount - fv_corpus)
        r = ann / 12; n = int(years * 12)
        factor = (((1 + r) ** n - 1) / r) * (1 + r) if r else n
        required_sip = round(need / factor, 0) if factor else None
        monthly = required_sip or 0

    def project(a: float):
        return round(_fv_sip(monthly, a, years) + current_corpus * (1 + a) ** years, 0)

    projected = project(ann)
    series = []
    for y in range(1, int(years) + 1):
        series.append({"year": y,
                       "expected": round(_fv_sip(monthly, ann, y) + current_corpus * (1 + ann) ** y, 0),
                       "optimistic": round(_fv_sip(monthly, ann + cfg["band"], y) + current_corpus * (1 + ann + cfg["band"]) ** y, 0),
                       "pessimistic": round(_fv_sip(monthly, max(0.02, ann - cfg["band"]), y) + current_corpus * (1 + max(0.02, ann - cfg["band"])) ** y, 0)})

    invested = round(monthly * int(years * 12) + current_corpus, 0)
    sleeves = [
        {"sleeve": "Equity", "pct": cfg["equity"],
         "how": "AI Fund Baskets (Portfolio → AI Funds) or top Flexi/Large-Cap funds (Funds → Screener)."},
        {"sleeve": "Debt", "pct": cfg["debt"],
         "how": "Corporate Bond / Short Duration funds (Funds → Screener)."},
        {"sleeve": "Gold", "pct": cfg["gold"], "how": "A Gold ETF / fund of funds."},
    ]
    return {"status": "success", "data": {
        "risk": risk, "assumed_return_pct": round(ann * 100, 1), "years": years,
        "monthly_sip": round(monthly, 0), "required_sip": required_sip,
        "current_corpus": current_corpus, "goal_amount": goal_amount or None,
        "projected_corpus": projected, "invested": invested,
        "wealth_gained": round(projected - invested, 0),
        "optimistic": project(ann + cfg["band"]), "pessimistic": project(max(0.02, ann - cfg["band"])),
        "on_track": bool(goal_amount and projected >= goal_amount),
        "allocation": {"equity": cfg["equity"], "debt": cfg["debt"], "gold": cfg["gold"]},
        "sleeves": sleeves, "projection": series,
        "note": "Projections use assumed long-run returns by risk profile — not guaranteed.",
    }}


# ── Tax / Capital-Gains optimizer (tax-loss harvesting) ────────────────────────

@router.get("/tax-harvest")
async def tax_harvest():
    """Unrealised gains/losses across holdings, tax-loss-harvest candidates and an
    estimated tax saving. (Groww's API doesn't return buy dates, so LTCG/STCG split
    is approximate — figures use the LTCG long-term rate as a guide.)"""
    pf = (await get_portfolio()).get("data", {})
    stocks = pf.get("stocks", [])
    if not stocks:
        return {"status": "success", "data": {"note": "No holdings to analyse."}}

    LTCG_RATE, LTCG_EXEMPT = 0.125, 125000
    gains, losses = [], []
    total_unreal = 0.0
    for s in stocks:
        g = float(s.get("gain") or 0.0)
        total_unreal += g
        row = {"symbol": s["symbol"], "qty": s["quantity"], "gain": round(g, 2),
               "gain_pct": s.get("gain_percent"), "value": s["value"]}
        (gains if g >= 0 else losses).append(row)

    losses.sort(key=lambda x: x["gain"])              # biggest losses first
    gains.sort(key=lambda x: -x["gain"])
    harvestable = round(sum(-l["gain"] for l in losses), 2)
    realised_gain_pool = round(sum(g["gain"] for g in gains), 2)
    offset = min(harvestable, realised_gain_pool)
    taxable_after_exempt = max(0.0, realised_gain_pool - LTCG_EXEMPT)
    est_tax_saved = round(min(offset, taxable_after_exempt) * LTCG_RATE, 0)

    return {"status": "success", "data": {
        "total_unrealised": round(total_unreal, 2),
        "unrealised_gains": realised_gain_pool, "harvestable_losses": harvestable,
        "harvest_candidates": losses[:10], "top_gainers": gains[:10],
        "potential_offset": round(offset, 2), "est_tax_saved": est_tax_saved,
        "ltcg_exemption": LTCG_EXEMPT, "ltcg_rate_pct": LTCG_RATE * 100,
        "tips": [
            f"Harvest up to ₹{harvestable:,.0f} of losses to offset realised gains and cut LTCG tax.",
            f"₹{LTCG_EXEMPT:,.0f} of long-term equity gains are tax-free each year — book up to that first.",
            "An ELSS fund (Funds → Screener → ELSS) gives 80C deduction with the shortest (3y) lock-in.",
        ],
        "caveat": "Buy dates aren't available from Groww's API, so LTCG vs STCG can't be split exactly — treat figures as guidance, not filing advice.",
        "updated_at": datetime.now().isoformat(),
    }}
