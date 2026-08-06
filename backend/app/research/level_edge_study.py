"""Do positional / multi-day setups carry intraday edge? — a read-only study.

The 2026-07-30 audit measured the intraday gross edge at ~zero (net -0.124%/trade
against a 0.125% round trip) and closed exit tuning, the memory bank and ATR
filtering by measurement. Its one open avenue was a NEW ENTRY SIGNAL, and the
system has no positional logic at all: no support/resistance, pivots, opening
range, swing levels, or prior-day context beyond gap and change.

This study measures a battery of such setups against the counterfactual labels
already on `session_decisions`, so a setup is judged on the P&L the live exit
rules would actually have produced. It changes no live behaviour.

Stages (each runnable alone; every stage caches so the next re-runs cheaply):

    extract   decisions + own-day 1-min bars + daily history  -> parquet
    features  the 12 setups' signals per decision bar          -> parquet
    report    day-level paired tests vs the cost hurdle        -> markdown

Run:
    python -m app.research.level_edge_study --stage extract
    python -m app.research.level_edge_study --stage features
    python -m app.research.level_edge_study --stage report
    python -m app.research.level_edge_study --stage all
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))

OUT_DIR = os.environ.get("RESEARCH_OUT", "/tmp/level_edge")
DECISIONS_PQ = os.path.join(OUT_DIR, "decisions.parquet")
BARS_PQ = os.path.join(OUT_DIR, "bars.parquet")
DAILY_PQ = os.path.join(OUT_DIR, "daily.parquet")
FEATURES_PQ = os.path.join(OUT_DIR, "features.parquet")
REPORT_MD = os.path.join(OUT_DIR, "report.md")

# Daily history is fetched once per symbol over this pad so multi-day features
# (run-up, distance-from-high) have lookback before the first decision day.
DAILY_PAD_DAYS = 120
YAHOO_CONCURRENCY = 6


def _log(msg: str) -> None:
    print(f"[level_edge] {msg}", flush=True)


# ── Stage 1: extract ─────────────────────────────────────────────────────────

_DECISION_SQL = """
    SELECT d.id, upper(d.symbol) AS symbol, d.candle_time, d.price, d.action,
           d.executed, d.confidence, d.cf_pnl_pct, d.indicators,
           COALESCE(sm.date,
                    (d.created_at AT TIME ZONE 'Asia/Kolkata')::date::text) AS day
    FROM session_decisions d
    LEFT JOIN session_metadata sm ON sm.session_id = d.session_id
    WHERE d.cf_pnl_pct IS NOT NULL
"""


async def _load_decisions() -> pd.DataFrame:
    from sqlalchemy import text
    from app.database import postgres

    # The engine is created at app startup; a standalone script must init it.
    if postgres.engine is None:
        await postgres.init_postgres()
    engine = postgres.engine

    async with engine.begin() as conn:
        rows = (await conn.execute(text(_DECISION_SQL))).fetchall()
    df = pd.DataFrame(rows, columns=[
        "id", "symbol", "candle_time", "price", "action", "executed",
        "confidence", "cf_pnl_pct", "indicators", "day",
    ])
    # The 6 live indicators ride along so the study can control for what the
    # gate already saw (sma5, sma20, rsi, atr, vwap, momentum_pct).
    inds = pd.json_normalize(df["indicators"].apply(lambda v: v or {})).add_prefix("live_")
    df = pd.concat([df.drop(columns=["indicators"]), inds], axis=1)
    return df


def _load_bars_for(pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """Own-day 1-minute bars from the tick store for each (symbol, day)."""
    from app.data.candle_store import read_bars

    frames = []
    for i, (sym, day) in enumerate(pairs, 1):
        bars = read_bars(sym, day, 60)
        if not bars:
            continue
        b = pd.DataFrame(bars)
        b["symbol"] = sym
        b["day"] = day
        b["bar_idx"] = range(len(b))
        frames.append(b)
        if i % 100 == 0:
            _log(f"  bars {i}/{len(pairs)} symbol-days")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


async def _load_daily(symbols: list[str], first_day: str, last_day: str) -> pd.DataFrame:
    """Daily OHLC per symbol. Prior-day levels and multi-day context come from
    here, not the tick store: the tick store covers only ~41% of prior days
    (it grows from live capture only), which would silently halve the sample."""
    from app.data.providers.yahoo_provider import YahooProvider

    provider = YahooProvider()
    start = datetime.fromisoformat(first_day).replace(tzinfo=IST) - timedelta(days=DAILY_PAD_DAYS)
    end = datetime.fromisoformat(last_day).replace(tzinfo=IST) + timedelta(days=2)

    sem = asyncio.Semaphore(YAHOO_CONCURRENCY)
    frames: list[pd.DataFrame] = []
    done = 0

    async def one(sym: str) -> None:
        nonlocal done
        async with sem:
            try:
                rows = await provider.daily(sym, start, end)
            except Exception as exc:
                _log(f"  daily {sym} failed: {exc}")
                rows = []
        done += 1
        if done % 50 == 0:
            _log(f"  daily {done}/{len(symbols)} symbols")
        if rows:
            d = pd.DataFrame(rows)
            d["symbol"] = sym
            frames.append(d)

    await asyncio.gather(*(one(s) for s in symbols))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])


async def stage_extract() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    _log("loading counterfactual-labelled decisions…")
    dec = await _load_decisions()
    _log(f"  {len(dec):,} labelled rows, {dec.symbol.nunique()} symbols, {dec.day.nunique()} days")

    pairs = sorted(set(zip(dec.symbol, dec.day)))
    _log(f"reading own-day bars for {len(pairs)} symbol-days…")
    bars = _load_bars_for(pairs)
    _log(f"  {len(bars):,} bars over {bars.groupby(['symbol', 'day']).ngroups} symbol-days")

    syms = sorted(dec.symbol.unique())
    _log(f"fetching daily history for {len(syms)} symbols…")
    daily = await _load_daily(syms, dec.day.min(), dec.day.max())
    _log(f"  {len(daily):,} daily candles over {daily.symbol.nunique() if len(daily) else 0} symbols")

    dec.to_parquet(DECISIONS_PQ, index=False)
    bars.to_parquet(BARS_PQ, index=False)
    daily.to_parquet(DAILY_PQ, index=False)
    _log(f"wrote {DECISIONS_PQ}, {BARS_PQ}, {DAILY_PQ}")

    # Sanity: the extract must reproduce the audit's known baseline.
    _log(f"BASELINE CHECK mean cf_pnl_pct = {dec.cf_pnl_pct.mean():+.4f}% "
         f"(audit: -0.124%), win-rate = {100 * (dec.cf_pnl_pct > 0).mean():.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", required=True,
                    choices=["extract", "features", "report", "all"])
    args = ap.parse_args()

    if args.stage in ("extract", "all"):
        asyncio.run(stage_extract())
    if args.stage in ("features", "all"):
        from app.research.level_features import stage_features
        stage_features()
    if args.stage in ("report", "all"):
        from app.research.level_report import stage_report
        stage_report()


if __name__ == "__main__":
    main()
