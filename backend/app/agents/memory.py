"""Pattern Memory Bank — case-based reasoning over historical market situations.

Every decision the system makes (or replays from history) is fingerprinted and,
once its outcome is known, stored as a *case*: (fingerprint, action, realised
pnl%, regime, source). When a new situation appears we retrieve the k most
similar past cases and use their *actual outcomes* to:

  • bias the decision toward what historically worked, and
  • gate out trades whose nearest neighbours mostly lost.

This is what makes "recognise a pattern it has already gone through" literal:
similar setups produce nearby fingerprints, and their track record drives the call.

The store keeps an in-process cache (numpy matrix) for fast cosine k-NN so a
query doesn't hit Postgres every candle. The cache refreshes on insert and on a
short TTL.
"""
from __future__ import annotations
import json
import time
from typing import Optional

import numpy as np

from app.utils.elk_logger import get_logger
from .base import AgentSignal, BaseAgent
from .fingerprint import build_fingerprint, classify_regime, FINGERPRINT_DIM

logger = get_logger(__name__)

# Retrieval / gating knobs
DEFAULT_K        = 50      # neighbours to retrieve
MIN_SAMPLES      = 8       # below this we treat memory as "no opinion"
SIM_FLOOR        = 0.55    # ignore neighbours less similar than this (cosine)
GATE_WIN_RATE    = 0.50    # an action's neighbours must win ≥ this to be allowed
STRONG_WIN_RATE  = 0.65    # above this we actively boost confidence
_CACHE_TTL       = 45.0    # seconds before the in-memory matrix is reloaded

_DDL = """CREATE TABLE IF NOT EXISTS pattern_memory (
    id            SERIAL PRIMARY KEY,
    symbol        VARCHAR(20),
    fingerprint   TEXT NOT NULL,
    action        VARCHAR(10) NOT NULL,
    entry_price   FLOAT,
    exit_price    FLOAT,
    pnl_pct       FLOAT,
    outcome       VARCHAR(10),
    regime        VARCHAR(20),
    source        VARCHAR(12) DEFAULT 'LIVE',
    created_at    TIMESTAMPTZ DEFAULT NOW()
)"""
_DDL_INDEX = "CREATE INDEX IF NOT EXISTS idx_pattern_memory_symbol ON pattern_memory(symbol)"


class PatternMemory:
    def __init__(self) -> None:
        self._mat: Optional[np.ndarray] = None        # (N, FINGERPRINT_DIM) unit vectors
        self._meta: list[dict] = []                   # parallel metadata
        self._loaded_at: float = 0.0
        self._ready = False

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def init_db(self) -> None:
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                await conn.execute(text(_DDL))
                await conn.execute(text(_DDL_INDEX))
            self._ready = True
            logger.info("Pattern memory table ready",
                        extra={"log_type": "ai_engine", "event": "memory_db_init"})
        except Exception as exc:
            logger.warning("Pattern memory init failed: %s", exc)

    # ── writes ────────────────────────────────────────────────────────────────
    async def add_case(
        self,
        symbol: str,
        fingerprint: list[float],
        action: str,
        pnl_pct: float,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        regime: str = "unknown",
        source: str = "LIVE",
    ) -> None:
        if not fingerprint or len(fingerprint) != FINGERPRINT_DIM:
            return
        outcome = "WIN" if pnl_pct > 0 else "LOSS"
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                await conn.execute(text("""
                    INSERT INTO pattern_memory
                      (symbol, fingerprint, action, entry_price, exit_price,
                       pnl_pct, outcome, regime, source)
                    VALUES (:sym,:fp,:act,:ep,:xp,:pp,:oc,:rg,:src)
                """), {
                    "sym": (symbol or "").upper(), "fp": json.dumps(fingerprint),
                    "act": action, "ep": entry_price, "xp": exit_price,
                    "pp": pnl_pct, "oc": outcome, "rg": regime, "src": source,
                })
            self._loaded_at = 0.0  # force cache refresh on next query
        except Exception as exc:
            logger.warning("pattern_memory add_case failed: %s", exc)

    @staticmethod
    def _to_rows(cases: list[dict]) -> list[dict]:
        rows = []
        for c in cases:
            fp = c.get("fingerprint")
            if not fp or len(fp) != FINGERPRINT_DIM:
                continue
            pnl = float(c.get("pnl_pct", 0.0))
            rows.append({
                "sym": (c.get("symbol") or "").upper(),
                "fp": json.dumps(fp), "act": c.get("action", "HOLD"),
                "ep": c.get("entry_price", 0.0), "xp": c.get("exit_price", 0.0),
                "pp": pnl, "oc": "WIN" if pnl > 0 else "LOSS",
                "rg": c.get("regime", "unknown"), "src": c.get("source", "BACKTEST"),
            })
        return rows

    _INSERT_SQL = """INSERT INTO pattern_memory
          (symbol, fingerprint, action, entry_price, exit_price,
           pnl_pct, outcome, regime, source)
        VALUES (:sym,:fp,:act,:ep,:xp,:pp,:oc,:rg,:src)"""

    async def add_cases_bulk(self, cases: list[dict]) -> int:
        """cases: list of dicts matching add_case kwargs. Returns inserted count."""
        rows = self._to_rows(cases)
        if not rows:
            return 0
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                await conn.execute(text(self._INSERT_SQL), rows)
            self._loaded_at = 0.0
            return len(rows)
        except Exception as exc:
            logger.warning("pattern_memory bulk insert failed: %s", exc)
            return 0

    async def replace_source(self, source: str, cases: list[dict]) -> int:
        """Atomically replace all cases of a given source with a fresh set.

        Used by the nightly sweep so re-running it refreshes the bank instead of
        endlessly duplicating the same historical trades. LIVE cases (real
        outcomes) are never touched.
        """
        rows = self._to_rows(cases)
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM pattern_memory WHERE source = :src"),
                                   {"src": source})
                if rows:
                    await conn.execute(text(self._INSERT_SQL), rows)
            self._loaded_at = 0.0
            return len(rows)
        except Exception as exc:
            logger.warning("pattern_memory replace_source failed: %s", exc)
            return 0

    # ── cache ─────────────────────────────────────────────────────────────────
    async def _refresh(self) -> None:
        if self._mat is not None and (time.time() - self._loaded_at) < _CACHE_TTL:
            return
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                rows = (await conn.execute(text("""
                    SELECT symbol, fingerprint, action, pnl_pct, regime, source
                    FROM pattern_memory ORDER BY id DESC LIMIT 50000
                """))).fetchall()
            vecs, meta = [], []
            for r in rows:
                try:
                    fp = json.loads(r[1])
                except Exception:
                    continue
                if len(fp) != FINGERPRINT_DIM:
                    continue
                vecs.append(fp)
                meta.append({"symbol": r[0], "action": r[2], "pnl_pct": float(r[3] or 0.0),
                             "regime": r[4], "source": r[5]})
            if vecs:
                m = np.asarray(vecs, dtype=np.float32)
                norms = np.linalg.norm(m, axis=1, keepdims=True)
                norms[norms == 0] = 1e-9
                self._mat = m / norms
                self._meta = meta
            else:
                self._mat = np.zeros((0, FINGERPRINT_DIM), dtype=np.float32)
                self._meta = []
            self._loaded_at = time.time()
        except Exception as exc:
            logger.warning("pattern_memory refresh failed: %s", exc)
            if self._mat is None:
                self._mat = np.zeros((0, FINGERPRINT_DIM), dtype=np.float32)
                self._meta = []

    # ── retrieval ─────────────────────────────────────────────────────────────
    async def query(
        self, fingerprint: list[float], symbol: Optional[str] = None,
        regime: Optional[str] = None, k: int = DEFAULT_K,
    ) -> dict:
        """Return per-action statistics from the k nearest historical cases.

        Result: {
          sample_count, per_action: {BUY:{n,win_rate,avg_pnl,evidence}, ...},
          best_action, best_evidence, symbol_local (bool)
        }
        """
        await self._refresh()
        empty = {"sample_count": 0, "per_action": {}, "best_action": "HOLD",
                 "best_evidence": 0.0, "symbol_local": False}
        if self._mat is None or self._mat.shape[0] == 0 or not fingerprint:
            return empty
        if len(fingerprint) != FINGERPRINT_DIM:
            return empty

        q = np.asarray(fingerprint, dtype=np.float32)
        nq = np.linalg.norm(q) or 1e-9
        q = q / nq
        sims = self._mat @ q  # cosine similarity (both unit-normalised)

        # Prefer same-symbol cases; fall back to the whole bank if too few
        idx = np.arange(self._mat.shape[0])
        symbol_local = False
        if symbol:
            su = symbol.upper()
            sym_mask = np.array([m["symbol"] == su for m in self._meta])
            if sym_mask.sum() >= MIN_SAMPLES:
                idx = idx[sym_mask]
                symbol_local = True
        if regime and regime != "unknown":
            reg_mask = np.array([self._meta[i]["regime"] == regime for i in idx])
            if reg_mask.sum() >= MIN_SAMPLES:
                idx = idx[reg_mask]

        cand_sims = sims[idx]
        order = np.argsort(-cand_sims)[:k]
        chosen = [(idx[o], float(cand_sims[o])) for o in order if cand_sims[o] >= SIM_FLOOR]
        if len(chosen) < MIN_SAMPLES:
            # relax similarity floor rather than return nothing
            chosen = [(idx[o], float(cand_sims[o])) for o in order][:k]
        if not chosen:
            return empty

        buckets: dict[str, list[tuple[float, float]]] = {"BUY": [], "SELL": [], "HOLD": []}
        for i, sim in chosen:
            m = self._meta[i]
            act = m["action"] if m["action"] in buckets else "HOLD"
            buckets[act].append((sim, m["pnl_pct"]))

        per_action: dict[str, dict] = {}
        for act, items in buckets.items():
            if not items:
                continue
            n = len(items)
            wins = sum(1 for _, p in items if p > 0)
            win_rate = wins / n
            avg_pnl = sum(p for _, p in items) / n
            avg_sim = sum(s for s, _ in items) / n
            # evidence: blend of sample mass, similarity and win-rate edge
            mass = min(1.0, n / DEFAULT_K)
            evidence = avg_sim * (0.4 + 0.6 * mass)
            per_action[act] = {
                "n": n, "win_rate": round(win_rate, 3),
                "avg_pnl": round(avg_pnl, 3), "avg_sim": round(avg_sim, 3),
                "evidence": round(evidence, 3),
            }

        # Best *actionable* (non-HOLD) by expected value = win_rate * avg_pnl mass
        actionable = {a: v for a, v in per_action.items() if a in ("BUY", "SELL")}
        if actionable:
            best_action = max(actionable, key=lambda a: actionable[a]["win_rate"] * actionable[a]["evidence"])
            best_evidence = actionable[best_action]["evidence"]
        else:
            best_action, best_evidence = "HOLD", per_action.get("HOLD", {}).get("evidence", 0.0)

        return {
            "sample_count": len(chosen),
            "per_action": per_action,
            "best_action": best_action,
            "best_evidence": round(best_evidence, 3),
            "symbol_local": symbol_local,
        }

    # ── stats for the UI ──────────────────────────────────────────────────────
    async def stats(self) -> dict:
        try:
            from sqlalchemy import text
            from app.database.postgres import engine
            async with engine.begin() as conn:
                total = (await conn.execute(text("SELECT COUNT(*) FROM pattern_memory"))).scalar() or 0
                by_src = (await conn.execute(text(
                    "SELECT source, COUNT(*), AVG(CASE WHEN pnl_pct>0 THEN 1.0 ELSE 0.0 END) "
                    "FROM pattern_memory GROUP BY source"))).fetchall()
                by_action = (await conn.execute(text(
                    "SELECT action, COUNT(*), AVG(CASE WHEN pnl_pct>0 THEN 1.0 ELSE 0.0 END), AVG(pnl_pct) "
                    "FROM pattern_memory GROUP BY action"))).fetchall()
                by_symbol = (await conn.execute(text(
                    "SELECT symbol, COUNT(*) FROM pattern_memory GROUP BY symbol "
                    "ORDER BY COUNT(*) DESC LIMIT 15"))).fetchall()
            return {
                "total_cases": int(total),
                "by_source":  [{"source": r[0], "count": int(r[1]), "win_rate": round(float(r[2] or 0), 3)} for r in by_src],
                "by_action":  [{"action": r[0], "count": int(r[1]), "win_rate": round(float(r[2] or 0), 3), "avg_pnl": round(float(r[3] or 0), 3)} for r in by_action],
                "top_symbols": [{"symbol": r[0], "count": int(r[1])} for r in by_symbol],
            }
        except Exception as exc:
            logger.warning("pattern_memory stats failed: %s", exc)
            return {"total_cases": 0, "by_source": [], "by_action": [], "top_symbols": []}


# ── The Memory Agent ──────────────────────────────────────────────────────────

class MemoryAgent(BaseAgent):
    """An agent that votes purely on what similar historical situations did."""
    name = "memory"

    def __init__(self, memory: PatternMemory) -> None:
        self._mem = memory

    async def analyze(self, symbol: str, candles: list[dict], context: dict) -> AgentSignal:
        fp = build_fingerprint(candles)
        if fp is None:
            return AgentSignal(agent_name=self.name, action="HOLD", confidence=0.30,
                               reasoning="Memory: insufficient candles to fingerprint")
        regime = classify_regime(candles)
        res = await self._mem.query(fp, symbol=symbol, regime=regime)
        pa = res["per_action"]

        # Expose every action's track record so the ensemble gate can use it
        indicators = {
            "sample_count": res["sample_count"],
            "regime": regime,
            "symbol_local": res["symbol_local"],
            "best_action": res["best_action"],
            "best_evidence": res["best_evidence"],
        }
        for act in ("BUY", "SELL", "HOLD"):
            if act in pa:
                indicators[f"wr_{act}"] = pa[act]["win_rate"]
                indicators[f"n_{act}"]  = pa[act]["n"]
                indicators[f"pnl_{act}"] = pa[act]["avg_pnl"]

        if res["sample_count"] < MIN_SAMPLES:
            return AgentSignal(
                agent_name=self.name, action="HOLD", confidence=0.32,
                reasoning=f"Memory: only {res['sample_count']} similar cases — no strong precedent",
                indicators=indicators,
            )

        best = res["best_action"]
        bstat = pa.get(best, {})
        wr = bstat.get("win_rate", 0.0)
        if best in ("BUY", "SELL") and wr >= GATE_WIN_RATE:
            conf = 0.35 + min(0.55, (wr - 0.5) * 1.2 + res["best_evidence"] * 0.4)
            action = best
            reason = (f"Memory: {bstat['n']} similar {regime} setups → {best} "
                      f"won {wr:.0%} (avg {bstat['avg_pnl']:+.2f}%)")
        else:
            action = "HOLD"
            conf = 0.40
            reason = (f"Memory: similar setups don't favour a trade "
                      f"(best {best} {wr:.0%} over {bstat.get('n', 0)} cases)")

        return AgentSignal(agent_name=self.name, action=action,
                           confidence=round(max(0.30, min(0.92, conf)), 3),
                           reasoning=reason, indicators=indicators)
