"""1-second tick store — our own real-data dataset, built from the Groww live feed.

The Groww historical API floors at 1-minute intervals; the live LTP stream gives
us ~1-second resolution. We capture the rawest thing — (ts, price) at 1s — and
resample to OHLC bars of any size on read. This keeps maximum fidelity: any
backtest/replay can request 1s / 5s / 10s / 1-min bars from the same data.

Layout (one Parquet file per symbol per IST trading day — no cross-process write
contention since each writer touches its own symbol/day):

    {CANDLE_STORE_DIR}/ticks/{SYMBOL}/{YYYY-MM-DD}.parquet
        columns: ts (int, epoch seconds, UTC) · price (float)

Volume note: the live LTP stream is price-only, so resampled bars carry
volume=0 until the separate enrichment job (Phase 4) merges real 1-min volume.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

ROOT  = os.getenv("CANDLE_STORE_DIR", "/data/candles")
_TICKS = os.path.join(ROOT, "ticks")
_VOL   = os.path.join(ROOT, "volume")     # per-minute volume sidecar (from 1-min historical)
IST = timezone(timedelta(hours=5, minutes=30))

# Per-file locks so concurrent appends to the same symbol/day serialise.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(path)
        if lk is None:
            lk = _locks[path] = threading.Lock()
        return lk


def _ist_date(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), IST).strftime("%Y-%m-%d")


def _path(symbol: str, date_str: str) -> str:
    return os.path.join(_TICKS, symbol.upper(), f"{date_str}.parquet")


def _vol_path(symbol: str, date_str: str) -> str:
    return os.path.join(_VOL, symbol.upper(), f"{date_str}.parquet")


def _normalise(ticks) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for t in ticks:
        try:
            if isinstance(t, (tuple, list)):
                ts, price = int(t[0]), float(t[1])
            else:
                ts, price = int(t["ts"]), float(t["price"])
            if price > 0:
                out.append((ts, price))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def append_ticks(symbol: str, ticks) -> int:
    """Append (ts, price) samples for `symbol`. Idempotent: dedups on ts (keeps
    the last price seen for a given second). Buckets rows into IST-day files.
    Returns the number of rows written across all touched day-files."""
    rows = _normalise(ticks)
    if not rows:
        return 0
    written = 0
    # Group by IST trading day.
    by_day: dict[str, list[tuple[int, float]]] = {}
    for ts, price in rows:
        by_day.setdefault(_ist_date(ts), []).append((ts, price))

    for date_str, day_rows in by_day.items():
        path = _path(symbol, date_str)
        lock = _lock_for(path)
        with lock:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                new = pd.DataFrame(day_rows, columns=["ts", "price"])
                if os.path.exists(path):
                    old = pd.read_parquet(path)
                    df = pd.concat([old, new], ignore_index=True)
                else:
                    df = new
                df = (df.drop_duplicates(subset="ts", keep="last")
                        .sort_values("ts")
                        .reset_index(drop=True))
                tmp = f"{path}.tmp.{os.getpid()}"
                df.to_parquet(tmp, index=False, engine="pyarrow", compression="zstd")
                os.replace(tmp, path)        # atomic
                written += len(day_rows)
            except Exception as exc:
                logger.warning("candle_store append failed (%s %s): %s", symbol, date_str, exc)
    return written


def read_ticks(symbol: str, date_str: str) -> pd.DataFrame:
    """Raw 1s samples for a symbol/day, sorted by ts. Empty frame if none."""
    path = _path(symbol, date_str)
    if not os.path.exists(path):
        return pd.DataFrame(columns=["ts", "price"])
    try:
        return pd.read_parquet(path).sort_values("ts").reset_index(drop=True)
    except Exception as exc:
        logger.warning("candle_store read failed (%s %s): %s", symbol, date_str, exc)
        return pd.DataFrame(columns=["ts", "price"])


def save_minute_volume(symbol: str, date_str: str, minute_volumes: dict[int, int]) -> int:
    """Store per-minute volume (minute_ts → volume) for a symbol/day, from the
    1-min historical API. Used to enrich the price-only tick store."""
    if not minute_volumes:
        return 0
    path = _vol_path(symbol, date_str)
    with _lock_for(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            df = pd.DataFrame(
                [{"minute": int(m), "volume": int(v)} for m, v in minute_volumes.items()]
            ).drop_duplicates(subset="minute", keep="last").sort_values("minute")
            tmp = f"{path}.tmp.{os.getpid()}"
            df.to_parquet(tmp, index=False, engine="pyarrow", compression="zstd")
            os.replace(tmp, path)
            return len(df)
        except Exception as exc:
            logger.warning("save_minute_volume failed (%s %s): %s", symbol, date_str, exc)
            return 0


def _load_minute_volume(symbol: str, date_str: str) -> dict[int, int]:
    path = _vol_path(symbol, date_str)
    if not os.path.exists(path):
        return {}
    try:
        df = pd.read_parquet(path)
        return {int(r.minute): int(r.volume) for r in df.itertuples()}
    except Exception:
        return {}


def read_bars(symbol: str, date_str: str, bar_seconds: int = 60) -> list[dict]:
    """Resample stored 1s ticks into OHLC bars of `bar_seconds`, in the candle
    dict shape the sessions expect. Volume is merged from the per-minute sidecar
    when available (real volume from the 1-min historical API); bars finer than a
    minute keep volume=0 since sub-minute volume isn't derivable."""
    df = read_ticks(symbol, date_str)
    if df.empty:
        return []
    bar_seconds = max(1, int(bar_seconds))
    minute_vol = _load_minute_volume(symbol, date_str)   # {minute_epoch: volume}
    df = df.copy()
    df["bin"] = (df["ts"] // bar_seconds) * bar_seconds
    agg = df.groupby("bin")["price"].agg(["first", "max", "min", "last"])
    fmt = "%H:%M" if bar_seconds >= 60 else "%H:%M:%S"
    bars: list[dict] = []
    for binstart, r in agg.iterrows():
        binstart = int(binstart)
        # Volume: sum the per-minute volumes overlapping this bar (only meaningful
        # for bars of >= 1 minute; sub-minute bars get 0).
        vol = 0
        if minute_vol and bar_seconds >= 60:
            for m in range(binstart, binstart + bar_seconds, 60):
                vol += minute_vol.get((m // 60) * 60, 0)
        dt = datetime.fromtimestamp(binstart, IST)
        bars.append({
            "ts":        binstart,
            "timestamp": binstart * 1000,
            "time":      dt.strftime(fmt),
            "open":      round(float(r["first"]), 2),
            "high":      round(float(r["max"]),   2),
            "low":       round(float(r["min"]),   2),
            "close":     round(float(r["last"]),  2),
            "volume":    int(vol),
        })
    return bars


def coverage() -> list[dict]:
    """What the dataset holds: one row per symbol/day with tick count + size."""
    out: list[dict] = []
    if not os.path.isdir(_TICKS):
        return out
    for symbol in sorted(os.listdir(_TICKS)):
        sdir = os.path.join(_TICKS, symbol)
        if not os.path.isdir(sdir):
            continue
        for fname in sorted(os.listdir(sdir)):
            if not fname.endswith(".parquet"):
                continue
            fpath = os.path.join(sdir, fname)
            try:
                n = len(pd.read_parquet(fpath, columns=["ts"]))
                out.append({
                    "symbol": symbol,
                    "date":   fname[:-8],
                    "ticks":  int(n),
                    "bytes":  os.path.getsize(fpath),
                })
            except Exception:
                continue
    return out


def coverage_summary() -> dict:
    cov = coverage()
    return {
        "symbols":     len(sorted({c["symbol"] for c in cov})),
        "days":        len(cov),
        "total_ticks": sum(c["ticks"] for c in cov),
        "total_bytes": sum(c["bytes"] for c in cov),
    }
