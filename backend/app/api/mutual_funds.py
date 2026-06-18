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

# Categories matched against scheme names for the screener (equity + hybrid + debt
# + sectoral/thematic).
CATEGORIES = ["Large Cap", "Mid Cap", "Small Cap", "Flexi Cap", "Multi Cap",
              "Large & Mid Cap", "Focused", "Value", "ELSS", "Index",
              "Balanced Advantage", "Aggressive Hybrid", "Multi Asset", "Equity Savings",
              "Liquid", "Corporate Bond", "Gilt", "Short Duration",
              "Pharma", "Healthcare", "Technology", "Infrastructure", "Banking", "Consumption", "Energy"]


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

    r1y = ret_for(365)

    # Annualised volatility from the last ~1y of NAV moves + a risk-adjusted score
    # (1y return ÷ volatility — Sharpe-like, higher = better return per unit risk).
    vol = risk_adjusted = None
    cutoff = latest_d.toordinal() - 365
    recent = [v for d, v in pts if d.toordinal() >= cutoff]
    if len(recent) >= 20:
        steps = [recent[i] / recent[i - 1] - 1 for i in range(1, len(recent)) if recent[i - 1] > 0]
        if len(steps) >= 15:
            import statistics
            sd = statistics.pstdev(steps)
            vol = round(sd * (252 ** 0.5) * 100, 2)              # annualised %
            if vol and r1y is not None:
                risk_adjusted = round(r1y / vol, 2)

    return {"1m": ret_for(30), "3m": ret_for(91), "6m": ret_for(182),
            "1y": r1y, "3y": ret_for(365 * 3), "5y": ret_for(365 * 5),
            "vol": vol, "risk_adjusted": risk_adjusted,
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

def _sort_key(sort: str):
    if sort == "risk":
        return lambda x: -(x.get("risk_adjusted") if x.get("risk_adjusted") is not None else -999)
    return lambda x: -(x.get("1y") if x.get("1y") is not None else -999)


async def _screen_category(category: str, limit: int = 30, sort: str = "return") -> list[dict]:
    cached = await _cache_get(_SCREEN_KEY.format(f"{category.lower()}:{sort}"))
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
    funds.sort(key=_sort_key(sort))
    for i, f in enumerate(funds, 1):
        f["rank"] = i
    funds = funds[:limit]
    await _cache_set(_SCREEN_KEY.format(f"{category.lower()}:{sort}"), funds, 6 * 3600)
    return funds


@router.get("/categories")
async def categories():
    return {"status": "success", "data": CATEGORIES}


@router.get("/screener")
async def screener(category: str = "Large Cap", limit: int = 20, sort: str = "return"):
    """Category leaderboard, ranked by 1-year return or risk-adjusted (return ÷
    volatility), with AI top-picks flagged."""
    sort = "risk" if sort == "risk" else "return"
    funds = await _screen_category(category, limit=limit, sort=sort)
    if funds:
        key = "risk_adjusted" if sort == "risk" else "1y"
        vals = sorted([f[key] for f in funds if f.get(key) is not None])
        med = vals[len(vals) // 2] if vals else 0
        for f in funds:
            f["ai_pick"] = bool(f.get("rank", 99) <= 3 and (f.get(key) or -999) >= med)
    return {"status": "success", "data": {"category": category, "sort": sort, "funds": funds,
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
        # Rank peers by risk-adjusted return (return per unit volatility).
        peers = await _screen_category(f["category"], limit=20, sort="risk")
        peer_ra = sorted([p["risk_adjusted"] for p in peers if p.get("risk_adjusted") is not None])
        median = peer_ra[len(peer_ra) // 2] if peer_ra else None
        best = next((p for p in peers if p["scheme_code"] != f["scheme_code"]
                     and p.get("risk_adjusted") is not None), None)
        fra = f.get("risk_adjusted")
        lagging = (median is not None and fra is not None and fra < median)
        better = bool(best and fra is not None and (best.get("risk_adjusted") or 0) - fra >= 0.15)
        verdict = "REPLACE" if (lagging and better) else "REVIEW" if lagging else "HOLD"
        suggestion = None
        if verdict == "REPLACE" and best:
            suggestion = {
                "scheme_code": best["scheme_code"], "name": best["name"], "fund_house": best["fund_house"],
                "1y": best.get("1y"), "3y": best.get("3y"), "vol": best.get("vol"),
                "risk_adjusted": best.get("risk_adjusted"),
                "edge_1y": round((best.get("1y") or 0) - (f.get("1y") or 0), 2),
                "reason": f"Better risk-adjusted {f['category']} peer: {best.get('1y')}% 1y at "
                          f"{best.get('vol')}% vol (risk-adj {best.get('risk_adjusted')}) vs your "
                          f"{f.get('1y')}% at {f.get('vol')}% vol (risk-adj {fra}).",
            }
        results.append({
            "fund": f, "verdict": verdict, "lagging": lagging,
            "risk_adjusted": fra, "category_median_ra": median, "suggestion": suggestion,
        })
    order = {"REPLACE": 0, "REVIEW": 1, "HOLD": 2}
    results.sort(key=lambda r: order.get(r["verdict"], 3))
    return {"status": "success", "data": {
        "results": results, "count": len(results),
        "replace": sum(1 for r in results if r["verdict"] == "REPLACE"),
        "updated_at": datetime.now().isoformat(),
        "note": "MF holdings are entered manually — Groww's API doesn't expose mutual funds.",
    }}


# ── AI MF Portfolio Optimizer ──────────────────────────────────────────────────

_EQUITY_CATS = {"Large Cap", "Mid Cap", "Small Cap", "Flexi Cap", "Multi Cap", "Large & Mid Cap",
                "Focused", "Value", "ELSS", "Index", "Pharma", "Healthcare", "Technology",
                "Infrastructure", "Banking", "Consumption", "Energy"}
_HYBRID_CATS = {"Aggressive Hybrid", "Balanced Advantage", "Multi Asset", "Equity Savings"}
_DEBT_CATS = {"Liquid", "Corporate Bond", "Gilt", "Short Duration"}

# Target asset mix by risk profile.
_MF_TARGET = {
    "conservative": {"Equity": 35, "Hybrid": 15, "Debt": 50},
    "moderate":     {"Equity": 60, "Hybrid": 15, "Debt": 25},
    "aggressive":   {"Equity": 80, "Hybrid": 10, "Debt": 10},
}


def _asset_class(cat: str) -> str:
    if cat in _DEBT_CATS:
        return "Debt"
    if cat in _HYBRID_CATS:
        return "Hybrid"
    return "Equity"


@router.get("/optimize")
async def optimize_mf(risk: str = "moderate"):
    """AI MF portfolio optimizer: checks asset-allocation fit vs a risk-based
    target, finds category redundancy (multiple funds doing the same job) and
    laggards, and produces a KEEP / REPLACE / CONSOLIDATE plan per fund."""
    risk = risk if risk in _MF_TARGET else "moderate"
    held = await _load_holdings()
    funds: list[dict] = []
    for h in held:
        f = await _fund_summary(h["scheme_code"])
        if not f:
            continue
        units = h.get("units")
        f["units"] = units
        f["value"] = round(units * f["nav"], 2) if units else None
        f["asset_class"] = _asset_class(f["category"])
        funds.append(f)
    if not funds:
        return {"status": "success", "data": {"note": "Add your funds first (My Funds), then optimize."}}

    total_val = sum(f["value"] for f in funds if f["value"]) or 0.0
    for f in funds:
        f["weight"] = (f["value"] / total_val * 100) if (f["value"] and total_val) else (100 / len(funds))

    # Current asset-class allocation vs risk target
    alloc: dict[str, float] = {"Equity": 0.0, "Hybrid": 0.0, "Debt": 0.0}
    for f in funds:
        alloc[f["asset_class"]] += f["weight"]
    target = _MF_TARGET[risk]
    alloc_rows = [{
        "asset": a, "current_pct": round(alloc[a], 1), "target_pct": target[a],
        "delta": round(alloc[a] - target[a], 1),
        "status": "overweight" if alloc[a] - target[a] >= 10 else "underweight" if alloc[a] - target[a] <= -10 else "balanced",
    } for a in ("Equity", "Hybrid", "Debt")]

    # Peers per distinct category (risk-adjusted leaderboard), cached
    cats = {f["category"] for f in funds}
    peers_by_cat = {c: await _screen_category(c, limit=20, sort="risk") for c in cats}

    # Group holdings by fine category to detect redundancy
    by_cat: dict[str, list] = {}
    for f in funds:
        by_cat.setdefault(f["category"], []).append(f)

    actions = []
    for cat, group in by_cat.items():
        group.sort(key=lambda x: -(x.get("risk_adjusted") if x.get("risk_adjusted") is not None else -999))
        best_held = group[0]
        peers = peers_by_cat.get(cat, [])
        held_codes = {f["scheme_code"] for f in funds}
        ras = sorted([p["risk_adjusted"] for p in peers if p.get("risk_adjusted") is not None])
        med = ras[len(ras) // 2] if ras else None
        best_peer = next((p for p in peers if p["scheme_code"] not in held_codes and p.get("risk_adjusted") is not None), None)
        for i, f in enumerate(group):
            fra = f.get("risk_adjusted")
            lagging = med is not None and fra is not None and fra < med
            if len(group) > 1 and i > 0:
                actions.append({
                    "verdict": "CONSOLIDATE", "fund": f,
                    "reason": f"Redundant {cat} fund — you already hold {len(group)} here. "
                              f"Merge into your best {cat} fund ({best_held['name']}, risk-adj {best_held.get('risk_adjusted')}).",
                    "into": {"scheme_code": best_held["scheme_code"], "name": best_held["name"]},
                })
            elif lagging and best_peer and (best_peer.get("risk_adjusted") or 0) - (fra or 0) >= 0.15:
                actions.append({
                    "verdict": "REPLACE", "fund": f,
                    "reason": f"Lagging {cat} on risk-adjusted return ({fra} vs cat median {med}).",
                    "suggestion": {"scheme_code": best_peer["scheme_code"], "name": best_peer["name"],
                                   "fund_house": best_peer["fund_house"], "1y": best_peer.get("1y"),
                                   "risk_adjusted": best_peer.get("risk_adjusted"), "vol": best_peer.get("vol")},
                })
            else:
                actions.append({"verdict": "KEEP", "fund": f, "reason": f"Solid {cat} holding — risk-adj {fra}."})

    rank = {"REPLACE": 0, "CONSOLIDATE": 1, "KEEP": 2}
    actions.sort(key=lambda a: rank.get(a["verdict"], 3))
    n_replace = sum(1 for a in actions if a["verdict"] == "REPLACE")
    n_consol = sum(1 for a in actions if a["verdict"] == "CONSOLIDATE")

    # Diversification notes
    notes = []
    if len(funds) > 8:
        notes.append(f"You hold {len(funds)} funds — beyond ~6-8 adds overlap, not diversification. Consolidate the weakest.")
    if len([a for a in alloc_rows if a["status"] == "underweight"]):
        for a in alloc_rows:
            if a["status"] == "underweight":
                notes.append(f"{a['asset']} is {abs(a['delta']):.0f}% below your {risk} target — add a {a['asset'].lower()} fund.")
    overweight_eq = next((a for a in alloc_rows if a["asset"] == "Equity" and a["status"] == "overweight"), None)
    if overweight_eq:
        notes.append(f"Equity is {overweight_eq['delta']:+.0f}% vs target — trim toward debt/hybrid to de-risk.")

    avg_ra = round(sum(f.get("risk_adjusted") or 0 for f in funds) / len(funds), 2)
    summary = (f"{len(funds)} funds across {len(cats)} categories. "
               f"{n_replace} to replace, {n_consol} to consolidate. "
               f"Asset mix Equity {alloc['Equity']:.0f}% / Hybrid {alloc['Hybrid']:.0f}% / Debt {alloc['Debt']:.0f}% "
               f"vs {risk} target {target['Equity']}/{target['Hybrid']}/{target['Debt']}. Avg risk-adjusted {avg_ra}.")

    # LLM enrichment (optional; falls back to the rule summary)
    ai_summary = summary
    try:
        from app.utils.llm_client import llm_chat
        out = await llm_chat(
            f"You are a concise Indian mutual-fund advisor. In 2-3 short sentences, give the single most "
            f"important optimization for this MF portfolio:\n{summary}\nNotes: {' '.join(notes)}",
            temperature=0.3, max_tokens=180)
        if out and out.strip():
            ai_summary = out.strip()
    except Exception:
        pass

    return {"status": "success", "data": {
        "risk": risk, "fund_count": len(funds), "categories": len(cats),
        "allocation": alloc_rows, "actions": actions, "notes": notes,
        "replace": n_replace, "consolidate": n_consol, "keep": len(funds) - n_replace - n_consol,
        "avg_risk_adjusted": avg_ra, "summary": summary, "ai_summary": ai_summary,
        "updated_at": datetime.now().isoformat(),
    }}
