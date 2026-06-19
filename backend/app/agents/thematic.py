"""Thematic Basket AI agent — smallcase-style investing themes, AI-curated.

smallcase's signature is the *thematic* portfolio: a basket of stocks built
around a real-world narrative (EV, green energy, defence, digital India…),
rebalanced over time. NeuradeX already builds quant baskets (momentum, high-
conviction); this agent adds the missing narrative layer.

For each theme we keep a curated seed universe of the well-known NSE names that
express it. The AI part: we intersect those seeds with the LIVE scan board, keep
only the names the AI currently rates a BUY (grade A/B), conviction-weight them,
assign a risk label, and write a one-line rationale per holding. So a theme is
not a static list — its membership and weights track what the AI likes *now*,
and the rebalancing agent (rebalance()) proposes updates as that changes.
"""
from __future__ import annotations

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

# Curated theme → representative NSE constituents (the investable narrative).
# Membership is filtered against the live AI board, so a thin/over-broad seed is
# fine — only names the scan currently rates BUY (A/B) make the final basket.
THEME_SEEDS: dict[str, dict] = {
    "ev_mobility": {
        "name": "EV & Mobility", "emoji": "🔋",
        "thesis": "The shift to electric vehicles — makers, battery & component suppliers, and charging.",
        "symbols": ["TATAMOTORS", "M&M", "BAJAJ-AUTO", "TVSMOTOR", "HEROMOTOCO", "EICHERMOT",
                     "EXIDEIND", "AMARAJABAT", "BOSCHLTD", "MOTHERSON", "SONACOMS", "UNOMINDA"],
    },
    "green_energy": {
        "name": "Green & Renewable Energy", "emoji": "🌱",
        "thesis": "India's clean-energy build-out — solar, wind, power utilities and transmission.",
        "symbols": ["ADANIGREEN", "TATAPOWER", "NTPC", "POWERGRID", "JSWENERGY", "SUZLON",
                     "INOXWIND", "NHPC", "SJVN", "ADANIENSOL", "TORNTPOWER", "BHEL"],
    },
    "defence": {
        "name": "Defence & Aerospace", "emoji": "🛡️",
        "thesis": "Indigenisation of defence — shipbuilders, electronics, aerospace and ordnance.",
        "symbols": ["HAL", "BEL", "BDL", "MAZDOCK", "COCHINSHIP", "DATAPATTNS",
                     "BEML", "GRSE", "MIDHANI", "PARAS", "ZENTEC", "SOLARINDS"],
    },
    "digital_ai": {
        "name": "Digital India & IT", "emoji": "💻",
        "thesis": "Software exports, digital platforms and the AI/data build-out.",
        "symbols": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT",
                     "COFORGE", "MPHASIS", "KPITTECH", "TATAELXSI", "ZOMATO", "PAYTM", "NYKAA"],
    },
    "banking_finance": {
        "name": "Banking & Financials", "emoji": "🏦",
        "thesis": "Private and public banks, NBFCs and capital-market plays.",
        "symbols": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "BAJFINANCE",
                     "BAJAJFINSV", "INDUSINDBK", "PNB", "BANKBARODA", "CHOLAFIN", "SBICARD",
                     "HDFCLIFE", "ICICIPRULI"],
    },
    "consumption": {
        "name": "Consumption & FMCG", "emoji": "🛒",
        "thesis": "India's domestic consumption — staples, discretionary and retail.",
        "symbols": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TITAN", "DMART",
                     "TATACONSUM", "VBL", "DABUR", "MARICO", "GODREJCP", "TRENT", "JUBLFOOD"],
    },
    "pharma_health": {
        "name": "Pharma & Healthcare", "emoji": "💊",
        "thesis": "Drugmakers, hospitals and diagnostics riding healthcare demand.",
        "symbols": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP", "MAXHEALTH",
                     "LUPIN", "AUROPHARMA", "ALKEM", "TORNTPHARM", "ZYDUSLIFE", "FORTIS"],
    },
    "psu": {
        "name": "PSU & Public Sector", "emoji": "🏛️",
        "thesis": "Government-owned majors re-rating across energy, banks and capital goods.",
        "symbols": ["SBIN", "NTPC", "POWERGRID", "ONGC", "COALINDIA", "BEL", "HAL",
                     "PFC", "RECLTD", "BANKBARODA", "PNB", "GAIL", "IOC", "BHEL"],
    },
    "infra_capex": {
        "name": "Infrastructure & Capex", "emoji": "🏗️",
        "thesis": "The capex cycle — construction, cement, capital goods and logistics.",
        "symbols": ["LT", "ULTRACEMCO", "GRASIM", "SHREECEM", "AMBUJACEM", "ACC",
                     "SIEMENS", "ABB", "CUMMINSIND", "BHARATFORG", "ADANIPORTS", "GMRINFRA"],
    },
    "metals_commodities": {
        "name": "Metals & Commodities", "emoji": "⛏️",
        "thesis": "Steel, base metals and mining geared to the global cycle.",
        "symbols": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "SAIL",
                     "NMDC", "NATIONALUM", "HINDZINC", "APLAPOLLO", "RATNAMANI"],
    },
}


def _risk_label(holdings: list[dict]) -> str:
    """Coarse risk from concentration + average conviction (a quick label; the
    backtested annualized-volatility label is added later by basket analytics)."""
    n = len(holdings)
    avg_wp = sum(h.get("win_probability", 0.5) for h in holdings) / max(1, n)
    if n >= 8 and avg_wp >= 0.55:
        return "Moderate"
    if n <= 4:
        return "High"
    if avg_wp >= 0.6:
        return "Moderate"
    return "Moderate–High"


def _rationale(h: dict, theme_name: str) -> str:
    g = h.get("grade") or "?"
    wp = int(round(float(h.get("win_probability") or 0.5) * 100))
    mom = float(h.get("momentum_pct") or 0.0)
    bits = [f"AI grade {g}", f"{wp}% win-prob"]
    if mom > 1:
        bits.append(f"+{mom:.1f}% momentum")
    elif mom < -1:
        bits.append(f"{mom:.1f}% momentum")
    return f"{theme_name} pick — " + ", ".join(bits) + "."


class ThematicBasketAgent:
    def __init__(self, min_holdings: int = 4):
        self.min_holdings = min_holdings

    def build(self, theme_id: str, board_by_symbol: dict[str, dict],
              weight_fn, cap: float = 0.20, max_holdings: int = 10) -> dict | None:
        """Build one theme basket from the live AI board.

        board_by_symbol: {SYMBOL: ranked_item} from the live scan.
        weight_fn: the shared conviction weighting (portfolio._weight).
        """
        seed = THEME_SEEDS.get(theme_id)
        if not seed:
            return None
        # Every theme name present on the board with a live price is a candidate.
        # "buy" = AI currently rates it A/B BUY; "watch" = on the radar but the AI
        # isn't endorsing it right now. We prefer buys, then backfill with the best
        # watch names so the narrative basket always exists (smallcase-style).
        cands: list[dict] = []
        for sym in seed["symbols"]:
            it = board_by_symbol.get(sym)
            if not it:
                continue
            price = float(it.get("price") or 0)
            if price <= 0:
                continue
            is_buy = it.get("grade") in ("A", "B") and it.get("action") == "BUY"
            cands.append({
                "symbol": sym, "name": it.get("name") or sym,
                "sector": it.get("sector", "Other"), "grade": it.get("grade") or "?",
                "win_probability": float(it.get("win_probability") or 0.5),
                "price": price,
                "momentum_pct": float((it.get("metrics") or {}).get("momentum_pct") or 0.0),
                "stance": "buy" if is_buy else "watch",
            })
        if len(cands) < self.min_holdings:
            return None
        buys = [c for c in cands if c["stance"] == "buy"]
        watch = [c for c in cands if c["stance"] == "watch"]
        buys.sort(key=lambda x: ({"A": 0, "B": 1}.get(x["grade"], 2), -x["win_probability"]))
        watch.sort(key=lambda x: -x["win_probability"])
        picks = (buys + watch)[:max_holdings]
        holdings = weight_fn([dict(x) for x in picks], cap=cap)
        for h in holdings:
            h["rationale"] = _rationale(h, seed["name"])
        return {
            "id": theme_id, "name": seed["name"], "emoji": seed["emoji"],
            "thesis": seed["thesis"], "kind": "thematic",
            "risk": _risk_label(holdings),
            "holdings": holdings,
            "stats": {
                "size": len(holdings),
                "sectors": sorted({h["sector"] for h in holdings}),
                "avg_win_probability": round(
                    sum(h["win_probability"] for h in holdings) / max(1, len(holdings)), 3),
                "candidates": len(seed["symbols"]),
                "buys": len(buys),
                "watch": len([h for h in holdings if h.get("stance") == "watch"]),
            },
        }

    def rebalance(self, theme_id: str, held_symbols: list[str],
                  board_by_symbol: dict[str, dict], weight_fn,
                  cap: float = 0.20, max_holdings: int = 10) -> dict:
        """Propose a rebalance update for an existing theme holding set: which
        names dropped out of favour, which new theme names to add, and the fresh
        target weights. The user chooses whether to apply it (smallcase-style)."""
        target = self.build(theme_id, board_by_symbol, weight_fn, cap, max_holdings)
        held = {s.upper() for s in held_symbols}
        if not target:
            return {"id": theme_id, "drift_pct": 100.0 if held else 0.0,
                    "needs_rebalance": bool(held), "target_holdings": [],
                    "add": [], "drop": sorted(held), "drop_reasons": {},
                    "note": "Too few theme names in the scanned universe right now."}
        th = target["holdings"]
        buy_syms = {h["symbol"] for h in th if h.get("stance") == "buy"}
        # Adds = AI-endorsed (A/B BUY) theme names you don't already hold.
        add = sorted(buy_syms - held)
        # Drops = held names the AI no longer endorses.
        drop, drop_reasons = [], {}
        for s in sorted(held):
            it = board_by_symbol.get(s)
            if not it:
                drop.append(s); drop_reasons[s] = "no longer in the scanned universe"
            elif not (it.get("grade") in ("A", "B") and it.get("action") == "BUY"):
                drop.append(s)
                drop_reasons[s] = f"AI no longer endorses it ({it.get('action')}, grade {it.get('grade')})"
        union = held | buy_syms
        drift = round(100.0 * (len(add) + len(drop)) / max(1, len(union)), 1)
        return {
            "id": theme_id, "name": target["name"], "drift_pct": drift,
            "needs_rebalance": bool(add or drop),
            "target_holdings": th, "risk": target["risk"],
            "add": add, "drop": drop, "drop_reasons": drop_reasons,
        }


_agent: ThematicBasketAgent | None = None


def get_thematic_agent() -> ThematicBasketAgent:
    global _agent
    if _agent is None:
        _agent = ThematicBasketAgent()
    return _agent
