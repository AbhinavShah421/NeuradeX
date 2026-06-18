"""Mutual Funds API — real NAV/returns from AMFI (via mfapi.in), a personal
"My Funds" tracker, a category screener, and AI replacement suggestions.

Groww's trading API does not expose MF holdings, so personal holdings are entered
by the user and stored in Redis; all NAV/return data is real (mfapi.in / AMFI).
"""
from __future__ import annotations
import json
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.utils.elk_logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

_MFAPI = "https://api.mfapi.in/mf"
_LIST_KEY = "mf:list"            # all schemes (code,name) — daily
_NAV_KEY = "mf:nav:{}"           # per-scheme meta+nav — few hours
_SCREEN_KEY = "mf:screen:{}"     # per-category leaderboard — few hours
_HOLDINGS_KEY = "mf:holdings"    # user's saved funds

# Recognised equity categories (matched against scheme names for the screener).
CATEGORIES = ["Large Cap", "Mid Cap", "Small Cap", "Flexi Cap", "Multi Cap",
              "Large & Mid Cap", "Focused", "Value", "ELSS", "Index", "Balanced Advantage"]


async def _cache_get(key: str):
    try:
        from app.utils.redis_client import cache_get
        raw = await cache_get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _cache_set(key: str, val, ttl: int):
    try:
        from app.utils.redis_client import cache_set
        await cache_set(key, json.dumps(val), expire=ttl)
    except Exception:
        pass


async def _all_schemes() -> list[dict]:
    cached = await _cache_get(_LIST_KEY)
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(_MFAPI)
        data = r.json()
    await _cache_set(_LIST_KEY, data, 86400)
    return data


async def _scheme(code: int) -> dict | None:
    cached = await _cache_get(_NAV_KEY.format(code))
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{_MFAPI}/{code}")
            d = r.json()
    except Exception:
        return None
    if not d or not d.get("data"):
        return None
    await _cache_set(_NAV_KEY.format(code), d, 6 * 3600)
    return d


def _parse(ds: str) -> datetime | None:
    try:
        return datetime.strptime(ds, "%d-%m-%Y")
    except Exception:
        return None


def _returns(navs: list[dict]) -> dict:
    """Returns over standard windows from a date-desc NAV series (mfapi format)."""
    pts = [(_parse(n["date"]), float(n["nav"])) for n in navs if n.get("nav") and _parse(n["date"])]
    pts = [p for p in pts if p[1] > 0]
    if len(pts) < 2:
        return {}
    pts.sort(key=lambda x: x[0])               # ascending by date
    latest_d, latest_v = pts[-1]

    def ret_for(days: int) -> float | None:
        target = latest_d.toordinal() - days
        prior = [p for p in pts if p[0].toordinal() <= target]
        if not prior:
            return None
        _, v0 = prior[-1]
        if v0 <= 0:
            return None
        years = days / 365.0
        if years >= 1.0:                       # annualise (CAGR) for ≥ 1y
            return round(((latest_v / v0) ** (1 / years) - 1) * 100, 2)
        return round((latest_v / v0 - 1) * 100, 2)

    return {"1m": ret_for(30), "3m": ret_for(91), "6m": ret_for(182),
            "1y": ret_for(365), "3y": ret_for(365 * 3), "5y": ret_for(365 * 5),
            "nav": round(latest_v, 4), "nav_date": latest_d.strftime("%Y-%m-%d")}


def _category_of(name: str, meta_cat: str = "") -> str:
    blob = f"{name} {meta_cat}"
    for cat in CATEGORIES:
        if cat.lower() in blob.lower():
            return cat
    return (meta_cat or "Other").replace("Equity Scheme - ", "").strip()


async def _fund_summary(code: int) -> dict | None:
    d = await _scheme(code)
    if not d:
        return None
    meta = d.get("meta", {})
    rets = _returns(d.get("data", []))
    if not rets:
        return None
    return {
        "scheme_code": code, "name": meta.get("scheme_name"),
        "fund_house": meta.get("fund_house"),
        "category": _category_of(meta.get("scheme_name", ""), meta.get("scheme_category", "")),
        "scheme_category": meta.get("scheme_category"),
        **rets,
    }


# ── Search + scheme detail ────────────────────────────────────────────────────

@router.get("/search")
async def search(q: str, limit: int = 25):
    """Search schemes by name (prefers Direct-Growth share classes)."""
    ql = q.strip().lower()
    if len(ql) < 2:
        return {"status": "success", "data": []}
    schemes = await _all_schemes()
    hits, seen = [], set()
    for s in schemes:
        if ql in s["schemeName"].lower() and s["schemeCode"] not in seen:
            seen.add(s["schemeCode"]); hits.append(s)
    hits.sort(key=lambda s: (("direct" not in s["schemeName"].lower()),
                             ("growth" not in s["schemeName"].lower()), len(s["schemeName"])))
    return {"status": "success", "data": [
        {"scheme_code": s["schemeCode"], "name": s["schemeName"]} for s in hits[:limit]]}


@router.get("/scheme/{code}")
async def scheme_detail(code: int):
    f = await _fund_summary(code)
    if not f:
        raise HTTPException(404, "scheme not found or no NAV history")
    return {"status": "success", "data": f}


# ── My Funds (personal, Redis-persisted) ──────────────────────────────────────

class AddFund(BaseModel):
    scheme_code: int
    units:    float | None = None
    invested: float | None = None


async def _load_holdings() -> list[dict]:
    return (await _cache_get(_HOLDINGS_KEY)) or []


@router.get("/holdings")
async def get_mf_holdings():
    """User's saved funds enriched with live NAV, current value and returns."""
    held = await _load_holdings()
    out, total_cur, total_inv = [], 0.0, 0.0
    for h in held:
        f = await _fund_summary(h["scheme_code"])
        if not f:
            continue
        units = h.get("units")
        invested = h.get("invested")
        cur = round(units * f["nav"], 2) if units else None
        if cur:
            total_cur += cur
        if invested:
            total_inv += invested
        out.append({**f, "units": units, "invested": invested, "current_value": cur,
                    "gain": round(cur - invested, 2) if (cur and invested) else None,
                    "gain_pct": round((cur - invested) / invested * 100, 2) if (cur and invested) else None})
    return {"status": "success", "data": {
        "funds": out, "count": len(out),
        "total_current": round(total_cur, 2) or None,
        "total_invested": round(total_inv, 2) or None,
        "total_gain": round(total_cur - total_inv, 2) if total_inv else None,
    }}


@router.post("/holdings")
async def add_mf_holding(req: AddFund):
    f = await _fund_summary(req.scheme_code)
    if not f:
        raise HTTPException(404, "scheme not found or no NAV history")
    held = await _load_holdings()
    held = [h for h in held if h["scheme_code"] != req.scheme_code]
    held.append({"scheme_code": req.scheme_code, "units": req.units, "invested": req.invested})
    await _cache_set(_HOLDINGS_KEY, held, 86400 * 365)
    return {"status": "success", "data": {"added": f["name"], "count": len(held)}}


@router.delete("/holdings/{code}")
async def remove_mf_holding(code: int):
    held = [h for h in await _load_holdings() if h["scheme_code"] != code]
    await _cache_set(_HOLDINGS_KEY, held, 86400 * 365)
    return {"status": "success", "data": {"count": len(held)}}


# ── Category screener ──────────────────────────────────────────────────────────

async def _screen_category(category: str, limit: int = 30) -> list[dict]:
    cached = await _cache_get(_SCREEN_KEY.format(category.lower()))
    if cached:
        return cached
    schemes = await _all_schemes()
    cl = category.lower()
    cand = [s for s in schemes
            if cl in s["schemeName"].lower()
            and "direct" in s["schemeName"].lower() and "growth" in s["schemeName"].lower()
            and "idcw" not in s["schemeName"].lower()]
    # de-dup by scheme code, bound the fetch
    seen, uniq = set(), []
    for s in cand:
        if s["schemeCode"] not in seen:
            seen.add(s["schemeCode"]); uniq.append(s)
    funds, names = [], set()
    for s in uniq[:80]:
        f = await _fund_summary(s["schemeCode"])
        if f and f.get("1y") is not None and f["name"] not in names:
            names.add(f["name"]); funds.append(f)
        if len(funds) >= limit + 10:
            break
    funds.sort(key=lambda x: -(x.get("1y") or -999))
    for i, f in enumerate(funds, 1):
        f["rank"] = i
    funds = funds[:limit]
    await _cache_set(_SCREEN_KEY.format(category.lower()), funds, 6 * 3600)
    return funds


@router.get("/categories")
async def categories():
    return {"status": "success", "data": CATEGORIES}


@router.get("/screener")
async def screener(category: str = "Large Cap", limit: int = 20):
    """Category leaderboard ranked by 1-year return, with AI top-picks flagged."""
    funds = await _screen_category(category, limit=limit)
    if funds:
        med = sorted([f["1y"] for f in funds if f.get("1y") is not None])[len(funds) // 2]
        for f in funds:
            f["ai_pick"] = bool(f.get("rank", 99) <= 3 and (f.get("1y") or 0) >= med)
    return {"status": "success", "data": {"category": category, "funds": funds,
            "count": len(funds), "updated_at": datetime.now().isoformat()}}


# ── Scan holdings + AI replacement suggestions ─────────────────────────────────

@router.get("/scan")
async def scan_holdings():
    """Scan each held fund vs its category peers; flag laggards and suggest a
    better-performing peer (AI replacement)."""
    held = await _load_holdings()
    results = []
    for h in held:
        f = await _fund_summary(h["scheme_code"])
        if not f:
            continue
        peers = await _screen_category(f["category"], limit=20)
        peer_1y = sorted([p["1y"] for p in peers if p.get("1y") is not None])
        median = peer_1y[len(peer_1y) // 2] if peer_1y else None
        best = next((p for p in peers if p["scheme_code"] != f["scheme_code"]), None)
        lagging = (median is not None and f.get("1y") is not None and f["1y"] < median)
        better = bool(best and f.get("1y") is not None and (best.get("1y") or 0) - f["1y"] >= 2.0)
        verdict = "REPLACE" if (lagging and better) else "REVIEW" if lagging else "HOLD"
        suggestion = None
        if verdict == "REPLACE" and best:
            suggestion = {
                "scheme_code": best["scheme_code"], "name": best["name"], "fund_house": best["fund_house"],
                "1y": best.get("1y"), "3y": best.get("3y"),
                "edge_1y": round((best.get("1y") or 0) - (f.get("1y") or 0), 2),
                "reason": f"Top {f['category']} peer beats your fund by "
                          f"{round((best.get('1y') or 0) - (f.get('1y') or 0), 1)}% over 1y "
                          f"({best.get('1y')}% vs {f.get('1y')}%).",
            }
        results.append({
            "fund": f, "category_median_1y": median, "verdict": verdict,
            "lagging": lagging, "suggestion": suggestion,
        })
    order = {"REPLACE": 0, "REVIEW": 1, "HOLD": 2}
    results.sort(key=lambda r: order.get(r["verdict"], 3))
    return {"status": "success", "data": {
        "results": results, "count": len(results),
        "replace": sum(1 for r in results if r["verdict"] == "REPLACE"),
        "updated_at": datetime.now().isoformat(),
        "note": "MF holdings are entered manually — Groww's API doesn't expose mutual funds.",
    }}
