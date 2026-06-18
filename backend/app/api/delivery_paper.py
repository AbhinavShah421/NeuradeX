"""Delivery (multi-day) paper-trading portfolios — autopilot.

Parallel to intraday paper trading, but for DELIVERY (swing) holds: positions are
bought from the delivery scan and held across days. A dedicated DeliveryAgent
decides, each daily tick, when to exit each position (target / stop / time-stop /
AI downgrade) and which new delivery picks to open. Supports MULTIPLE portfolios
(e.g. different target/stop/risk configs). Closed trades feed the Delivery line on
the AI Scan Accuracy graph.
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils.elk_logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
_PF_KEY = "delivery_paper:portfolios"
_FLAG = "delivery_paper:enabled"
_LAST_TICK = "delivery_paper:last_tick"

DEFAULT_CONFIG = {"max_positions": 5, "target_pct": 12.0, "stop_pct": 6.0, "max_hold_days": 25}
_GRADE_OK = ("A", "B")


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


async def _rget(key: str):
    from app.utils.redis_client import cache_get
    return await cache_get(key)


async def _rset(key: str, val, ttl: int = 86400 * 365):
    from app.utils.redis_client import cache_set
    await cache_set(key, val if isinstance(val, str) else json.dumps(val), expire=ttl)


async def _load_pfs() -> dict:
    try:
        raw = await _rget(_PF_KEY)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


async def _save_pfs(pfs: dict) -> None:
    await _rset(_PF_KEY, pfs)


def _new_portfolio(name: str, capital: float, cfg: dict) -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name, "capital": capital, "cash": capital,
            "config": {**DEFAULT_CONFIG, **(cfg or {})}, "positions": [], "closed": [],
            "value": capital, "created_at": _today()}


async def _delivery_picks() -> list[dict]:
    """Current delivery-scan picks (grade A/B BUY), priced."""
    try:
        raw = await _rget("ai_engine:watchlist")
        dl = (json.loads(raw) or {}).get("delivery", []) if raw else []
    except Exception:
        dl = []
    out = []
    for it in dl:
        if it.get("grade") in _GRADE_OK and it.get("action") == "BUY" and float(it.get("price") or 0) > 0:
            out.append({"symbol": (it.get("symbol") or "").upper(), "name": it.get("name"),
                        "price": float(it["price"]), "grade": it.get("grade"),
                        "delivery_score": float(it.get("delivery_score") or 0), "sector": it.get("sector")})
    out.sort(key=lambda x: -x["delivery_score"])
    return out


async def _prices(symbols: list[str]) -> dict:
    if not symbols:
        return {}
    from app.api.portfolio import _yahoo_quote_map
    qm = await _yahoo_quote_map([f"{s}.NS" for s in symbols])
    return {s: float(qm[f"{s}.NS"]["ltp"]) for s in symbols if qm.get(f"{s}.NS", {}).get("ltp")}


async def _record_delivery_outcome(date_str: str, symbol: str, pnl_pct: float) -> None:
    """Reflect a closed delivery paper trade on the Delivery accuracy line."""
    try:
        from app.api.ai_engine import _ensure_scan_eval
        from app.database.postgres import engine
        from sqlalchemy import text
        from datetime import date
        await _ensure_scan_eval()
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO scan_evaluations (eval_date, symbol, action, day_return_pct,
                       realized_return_pct, correct, trade_kind)
                VALUES (:d,:s,'BUY',:r,:r,:ok,'delivery')
                ON CONFLICT (eval_date, symbol, trade_kind) DO UPDATE SET
                  realized_return_pct=:r, day_return_pct=:r, correct=:ok
            """), {"d": date.fromisoformat(date_str), "s": symbol,
                   "r": round(pnl_pct, 2), "ok": bool(pnl_pct > 0)})
    except Exception as exc:
        logger.debug("delivery outcome record failed: %s", exc)


# ── The Delivery AI Agent ──────────────────────────────────────────────────────

class DeliveryAgent:
    """Decides, per daily tick, when to exit a delivery position and which new
    delivery picks to enter — the AI managing the swing portfolio over time."""

    @staticmethod
    def decide_exit(pos: dict, price: float, held_days: int, cfg: dict,
                    still_picked: bool, grade_now: str | None):
        tp, sp, mh = cfg["target_pct"], cfg["stop_pct"], cfg["max_hold_days"]
        if price >= pos["target"]:
            return True, f"Target hit (+{tp:.0f}%) — book the gain."
        if price <= pos["stop"]:
            return True, f"Stop hit (-{sp:.0f}%) — cut the loss."
        if held_days >= mh:
            return True, f"Time stop — held {held_days}d without the move playing out."
        if not still_picked or (grade_now not in _GRADE_OK):
            return True, "AI downgrade — no longer a high-grade delivery setup (thesis weakened)."
        return False, "Holding — delivery thesis intact."

    @staticmethod
    def select_entries(picks: list[dict], held: set, slots: int) -> list[dict]:
        return [p for p in picks if p["symbol"] not in held][:max(0, slots)]


_AGENT = DeliveryAgent()


# ── Tick: run the agent across all portfolios ──────────────────────────────────

async def tick(reason: str = "manual") -> dict:
    pfs = await _load_pfs()
    if not pfs:                                   # auto-create a default portfolio
        p = _new_portfolio("Delivery Core", 200000.0, {})
        pfs[p["id"]] = p
    picks = await _delivery_picks()
    pick_map = {p["symbol"]: p for p in picks}
    held_syms = {pos["symbol"] for pf in pfs.values() for pos in pf["positions"]}
    prices = await _prices(sorted(held_syms | set(pick_map)))
    today = _today()
    summary = {"opened": 0, "closed": 0, "portfolios": len(pfs)}

    for pf in pfs.values():
        cfg = {**DEFAULT_CONFIG, **pf.get("config", {})}
        # 1) exits
        survivors = []
        for pos in pf["positions"]:
            sym = pos["symbol"]
            price = prices.get(sym, pos.get("current", pos["entry_price"]))
            held_days = (datetime.fromisoformat(today) - datetime.fromisoformat(pos["entry_date"])).days
            exit_now, why = _AGENT.decide_exit(pos, price, held_days, cfg,
                                               sym in pick_map, pick_map.get(sym, {}).get("grade"))
            if exit_now:
                pnl_pct = (price - pos["entry_price"]) / pos["entry_price"] * 100
                pnl = round((price - pos["entry_price"]) * pos["qty"], 2)
                pf["cash"] = round(pf["cash"] + price * pos["qty"], 2)
                pf["closed"].append({**pos, "exit_date": today, "exit_price": round(price, 2),
                                     "pnl_pct": round(pnl_pct, 2), "pnl": pnl,
                                     "days_held": held_days, "reason": why})
                await _record_delivery_outcome(today, sym, pnl_pct)
                summary["closed"] += 1
            else:
                pos["current"] = round(price, 2)
                pos["pnl_pct"] = round((price - pos["entry_price"]) / pos["entry_price"] * 100, 2)
                pos["status_reason"] = why
                survivors.append(pos)
        pf["positions"] = survivors

        # 2) entries
        slots = cfg["max_positions"] - len(pf["positions"])
        if slots > 0 and pf["cash"] > 1000:
            entries = _AGENT.select_entries(picks, {p["symbol"] for p in pf["positions"]}, slots)
            alloc = pf["cash"] / max(1, slots)
            for e in entries:
                price = prices.get(e["symbol"], e["price"])
                qty = int(alloc // price)
                if qty < 1:
                    continue
                pf["cash"] = round(pf["cash"] - qty * price, 2)
                pf["positions"].append({
                    "symbol": e["symbol"], "name": e.get("name"), "sector": e.get("sector"),
                    "entry_date": today, "entry_price": round(price, 2), "qty": qty,
                    "target": round(price * (1 + cfg["target_pct"] / 100), 2),
                    "stop": round(price * (1 - cfg["stop_pct"] / 100), 2),
                    "grade": e.get("grade"), "current": round(price, 2), "pnl_pct": 0.0,
                    "status_reason": "Opened from delivery scan.",
                })
                summary["opened"] += 1

        pf["value"] = round(pf["cash"] + sum(p["qty"] * p.get("current", p["entry_price"]) for p in pf["positions"]), 2)
        inv = pf["capital"]
        pf["return_pct"] = round((pf["value"] - inv) / inv * 100, 2) if inv else 0.0

    await _save_pfs(pfs)
    await _rset(_LAST_TICK, today)
    logger.info("delivery paper tick (%s): +%d opened, %d closed across %d portfolios",
                reason, summary["opened"], summary["closed"], len(pfs))
    return summary


# ── Endpoints ───────────────────────────────────────────────────────────────────

class CreatePortfolio(BaseModel):
    name: str = "Delivery Portfolio"
    capital: float = 200000.0
    max_positions: int | None = None
    target_pct: float | None = None
    stop_pct: float | None = None
    max_hold_days: int | None = None


class EnableRequest(BaseModel):
    enabled: bool


@router.get("/portfolios")
async def list_portfolios():
    pfs = await _load_pfs()
    enabled = (await _rget(_FLAG)) == "1"
    return {"status": "success", "data": {
        "enabled": enabled, "last_tick": await _rget(_LAST_TICK),
        "portfolios": list(pfs.values()),
        "totals": {"value": round(sum(p.get("value", 0) for p in pfs.values()), 2),
                   "invested": round(sum(p.get("capital", 0) for p in pfs.values()), 2)},
    }}


@router.post("/portfolios")
async def create_portfolio(req: CreatePortfolio):
    pfs = await _load_pfs()
    cfg = {k: v for k, v in {"max_positions": req.max_positions, "target_pct": req.target_pct,
                             "stop_pct": req.stop_pct, "max_hold_days": req.max_hold_days}.items() if v is not None}
    p = _new_portfolio(req.name, max(10000.0, req.capital), cfg)
    pfs[p["id"]] = p
    await _save_pfs(pfs)
    return {"status": "success", "data": p}


@router.delete("/portfolios/{pid}")
async def delete_portfolio(pid: str):
    pfs = await _load_pfs()
    pfs.pop(pid, None)
    await _save_pfs(pfs)
    return {"status": "success", "data": {"count": len(pfs)}}


@router.post("/enable")
async def enable(req: EnableRequest):
    await _rset(_FLAG, "1" if req.enabled else "0")
    if req.enabled:                               # kick a tick immediately
        try:
            await tick(reason="enable")
        except Exception as exc:
            logger.warning("delivery enable tick failed: %s", exc)
    return {"status": "success", "data": {"enabled": req.enabled}}


@router.post("/tick")
async def tick_now():
    return {"status": "success", "data": await tick(reason="manual")}


@router.get("/status")
async def status():
    pfs = await _load_pfs()
    return {"status": "success", "data": {
        "enabled": (await _rget(_FLAG)) == "1", "last_tick": await _rget(_LAST_TICK),
        "portfolios": len(pfs), "open_positions": sum(len(p["positions"]) for p in pfs.values())}}


# ── Autopilot loop (once per day when enabled) ──────────────────────────────────

async def delivery_autopilot_loop():
    import asyncio
    await asyncio.sleep(40)
    while True:
        try:
            if (await _rget(_FLAG)) == "1" and (await _rget(_LAST_TICK)) != _today():
                await tick(reason="autopilot")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("delivery autopilot loop error: %s", exc)
        await asyncio.sleep(1800)                 # check every 30 min; ticks once/day
