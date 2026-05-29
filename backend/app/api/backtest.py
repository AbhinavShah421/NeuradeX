"""
Backtesting Module — simulate trading strategies on Groww historical data.

Strategies: SMA Crossover, RSI Mean Reversion, MACD Crossover, Bollinger Band.
Also provides a live-signal endpoint for paper trading.
Day-Autopilot: AI-driven intraday simulation with LLM decision-making.
"""
import asyncio
import json
import math
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd
import ta
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger

FEEDBACK_SERVICE_URL = "http://feedback-service:8012"

logger = get_logger(__name__)
router = APIRouter()

INDIA_RF_RATE = 0.065  # 10Y G-Sec


async def _save_backtest_trades(records: list[dict]) -> None:
    """Fire-and-forget POST to feedback-service to persist backtest trades."""
    if not records:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{FEEDBACK_SERVICE_URL}/trades", json=records)
    except Exception as exc:
        logger.warning("Could not save backtest trades to feedback-service: %s", exc)


def _derive_agent_signals(action: str, indicators: dict, strategy: str = "") -> dict:
    """Map available indicators to the 5 agent signal slots the Orders page expects."""
    rsi = float(indicators.get("rsi", 50) or 50)
    mom5 = float(indicators.get("mom5", 0) or 0)
    above_vwap = bool(indicators.get("above_vwap", True))
    vol_ratio = float(indicators.get("vol_ratio", 1.0) or 1.0)

    # Technical: RSI + momentum
    if rsi < 40 and mom5 >= 0:
        technical = "BUY"
    elif rsi > 65 or mom5 < -0.2:
        technical = "SELL"
    else:
        technical = action

    # Sentiment: momentum proxy
    if mom5 > 0.15:
        sentiment = "BUY"
    elif mom5 < -0.15:
        sentiment = "SELL"
    else:
        sentiment = "HOLD"

    # Pattern: VWAP + volume
    if above_vwap and vol_ratio > 1.2:
        pattern = "BUY"
    elif not above_vwap and vol_ratio > 1.2:
        pattern = "SELL"
    else:
        pattern = "HOLD"

    # Macro: neutral for strategy/intraday backtests
    macro = "HOLD"

    # RL: follows the actual executed action
    rl = action

    return {"technical": technical, "sentiment": sentiment, "macro": macro, "pattern": pattern, "rl": rl}


def _strategy_agent_signals(strategy: str, action: str) -> dict:
    """For simple strategy backtests (no per-candle indicators), technical drives the signal."""
    return {
        "technical": action,
        "sentiment": "HOLD",
        "macro": "HOLD",
        "pattern": "HOLD",
        "rl": action,
    }


def _build_trade_record(
    symbol: str,
    action: str,
    entry_price: float,
    exit_price: float,
    pnl_abs: float,
    pnl_pct_decimal: float,
    timestamp_open: str,
    timestamp_close: str,
    duration_minutes: int,
    agent_signals: dict,
    market_context: dict,
    confidence: float = 0.75,
) -> dict:
    outcome = "WIN" if pnl_abs > 0 else "LOSS"
    return {
        "trade_id": str(uuid.uuid4()),
        "symbol": symbol,
        "exchange": "NSE",
        "action": action,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_abs": round(pnl_abs, 2),
        "pnl_pct": round(pnl_pct_decimal, 6),
        "duration_minutes": duration_minutes,
        "ensemble_confidence": confidence,
        "agent_signals": agent_signals,
        "market_context": market_context,
        "outcome": outcome,
        "timestamp_open": timestamp_open,
        "timestamp_close": timestamp_close,
        "trade_source": "BACKTEST",
    }


# ── Strategy registry ──────────────────────────────────────────────────────────

STRATEGIES = {
    "sma_crossover": {
        "name": "SMA Crossover",
        "description": "Buy when fast SMA crosses above slow SMA (golden cross). Sell on death cross.",
        "params": {
            "sma_fast": {"label": "Fast SMA period", "default": 20, "min": 5,  "max": 50,  "step": 1,   "type": "int"},
            "sma_slow": {"label": "Slow SMA period", "default": 50, "min": 20, "max": 200, "step": 5,   "type": "int"},
        },
    },
    "rsi_mean_reversion": {
        "name": "RSI Mean Reversion",
        "description": "Buy when RSI drops below oversold. Sell when RSI rises above overbought.",
        "params": {
            "rsi_period":  {"label": "RSI period",           "default": 14, "min": 5,  "max": 30, "step": 1,   "type": "int"},
            "oversold":    {"label": "Oversold threshold",   "default": 30, "min": 15, "max": 45, "step": 1,   "type": "int"},
            "overbought":  {"label": "Overbought threshold", "default": 70, "min": 55, "max": 85, "step": 1,   "type": "int"},
        },
    },
    "macd_crossover": {
        "name": "MACD Crossover",
        "description": "Buy when MACD crosses above signal line. Sell on cross below.",
        "params": {
            "fast":   {"label": "Fast EMA period",   "default": 12, "min": 5,  "max": 20, "step": 1, "type": "int"},
            "slow":   {"label": "Slow EMA period",   "default": 26, "min": 15, "max": 50, "step": 1, "type": "int"},
            "signal": {"label": "Signal EMA period", "default": 9,  "min": 3,  "max": 15, "step": 1, "type": "int"},
        },
    },
    "bollinger_band": {
        "name": "Bollinger Band Reversion",
        "description": "Buy when price closes below lower band. Sell when price closes above upper band.",
        "params": {
            "window":  {"label": "Window period",   "default": 20,  "min": 10,  "max": 50,  "step": 5,   "type": "int"},
            "std_dev": {"label": "Std deviations",  "default": 2.0, "min": 1.0, "max": 3.0, "step": 0.5, "type": "float"},
        },
    },
}


# ── Pydantic models ────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    symbol: str
    strategy: str
    start_date: str
    end_date: str
    initial_capital: float = Field(default=100_000.0, ge=10_000, le=100_000_000)
    commission: float = Field(default=0.001, ge=0.0, le=0.05)
    params: dict = {}


# ── Candle helpers ─────────────────────────────────────────────────────────────

def _parse_candles(raw: list) -> list[dict]:
    result = []
    for c in raw:
        if isinstance(c, list) and len(c) >= 6:
            ts = c[0]
            result.append({
                "date":   datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else str(ts)[:10],
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": int(c[5]),
            })
        elif isinstance(c, dict):
            result.append({
                "date":   str(c.get("timestamp", c.get("time", "")))[:10],
                "open":   float(c.get("open", 0)),
                "high":   float(c.get("high", 0)),
                "low":    float(c.get("low", 0)),
                "close":  float(c.get("close", 0)),
                "volume": int(c.get("volume", 0)),
            })
    return [c for c in result if c["close"] > 0]


def _simulate_candles(symbol: str, start: datetime, end: datetime) -> list[dict]:
    BASE = {
        "SBIN": 820, "IDBI": 72, "SUZLON": 58, "INDUSINDBK": 870,
        "TMPV": 356, "PNB": 102, "FEDERALBNK": 182, "TMCV": 378,
        "IREDA": 178, "ZEEL": 135, "IOB": 54, "JKTYRE": 395,
        "RELIANCE": 2850, "TCS": 3450, "INFY": 1720, "HDFCBANK": 1530,
        "ICICIBANK": 1220, "BAJFINANCE": 6900, "WIPRO": 505, "KOTAKBANK": 1820,
    }
    base = BASE.get(symbol, 500.0) * random.uniform(0.60, 0.80)
    result = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            o = round(base * random.uniform(0.991, 1.009), 2)
            c = round(o * random.uniform(0.993, 1.007), 2)
            result.append({
                "date":   cur.strftime("%Y-%m-%d"),
                "open":   o,
                "high":   round(max(o, c) * random.uniform(1.001, 1.012), 2),
                "low":    round(min(o, c) * random.uniform(0.988, 0.999), 2),
                "close":  c,
                "volume": random.randint(300_000, 12_000_000),
            })
            base = c
        cur += timedelta(days=1)
    return result


async def _fetch_candles(symbol: str, start: datetime, end: datetime) -> tuple[list[dict], str]:
    groww = get_groww_client()
    if groww:
        try:
            logger.info(
                "Calling Groww get_historical for backtest",
                extra={"log_type": "groww_call", "caller": "backtest._fetch_candles", "method": "get_historical", "symbol": symbol, "interval_minutes": 1440},
            )
            raw = await groww.get_historical(symbol, 1440, start, end)
            if raw and len(raw) > 10:
                candles = _parse_candles(raw)
                if candles:
                    return candles, "groww"
        except Exception as exc:
            logger.warning(
                "Groww candles fetch failed, using simulation",
                extra={"log_type": "backtest_event", "event": "candles_fallback", "symbol": symbol, "error": str(exc)},
            )
    return _simulate_candles(symbol, start, end), "simulated"


# ── Signal generators ──────────────────────────────────────────────────────────

def _generate_signals(df: pd.DataFrame, strategy: str, params: dict) -> pd.Series:
    """Returns a Series: 1 = buy, -1 = sell, 0 = hold."""
    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    sig   = pd.Series(0, index=df.index)

    if strategy == "sma_crossover":
        f = int(params.get("sma_fast", 20))
        s = int(params.get("sma_slow", 50))
        sma_f = ta.trend.SMAIndicator(close, window=f).sma_indicator()
        sma_s = ta.trend.SMAIndicator(close, window=s).sma_indicator()
        for i in range(1, len(df)):
            if pd.isna(sma_f.iloc[i]) or pd.isna(sma_s.iloc[i]):
                continue
            if sma_f.iloc[i-1] < sma_s.iloc[i-1] and sma_f.iloc[i] >= sma_s.iloc[i]:
                sig.iloc[i] = 1
            elif sma_f.iloc[i-1] > sma_s.iloc[i-1] and sma_f.iloc[i] <= sma_s.iloc[i]:
                sig.iloc[i] = -1

    elif strategy == "rsi_mean_reversion":
        period = int(params.get("rsi_period", 14))
        os     = float(params.get("oversold",  30))
        ob     = float(params.get("overbought", 70))
        rsi = ta.momentum.RSIIndicator(close, window=period).rsi()
        for i in range(1, len(df)):
            if pd.isna(rsi.iloc[i]):
                continue
            if rsi.iloc[i-1] >= os and rsi.iloc[i] < os:
                sig.iloc[i] = 1
            elif rsi.iloc[i-1] <= ob and rsi.iloc[i] > ob:
                sig.iloc[i] = -1

    elif strategy == "macd_crossover":
        fst = int(params.get("fast",   12))
        slw = int(params.get("slow",   26))
        sgn = int(params.get("signal",  9))
        obj = ta.trend.MACD(close, window_fast=fst, window_slow=slw, window_sign=sgn)
        macd  = obj.macd()
        msig  = obj.macd_signal()
        for i in range(1, len(df)):
            if pd.isna(macd.iloc[i]) or pd.isna(msig.iloc[i]):
                continue
            if macd.iloc[i-1] < msig.iloc[i-1] and macd.iloc[i] >= msig.iloc[i]:
                sig.iloc[i] = 1
            elif macd.iloc[i-1] > msig.iloc[i-1] and macd.iloc[i] <= msig.iloc[i]:
                sig.iloc[i] = -1

    elif strategy == "bollinger_band":
        w   = int(params.get("window",  20))
        sd  = float(params.get("std_dev", 2.0))
        bb  = ta.volatility.BollingerBands(close, window=w, window_dev=sd)
        bb_lo = bb.bollinger_lband()
        bb_hi = bb.bollinger_hband()
        for i in range(1, len(df)):
            if pd.isna(bb_lo.iloc[i]):
                continue
            if close.iloc[i] < bb_lo.iloc[i]:
                sig.iloc[i] = 1
            elif close.iloc[i] > bb_hi.iloc[i]:
                sig.iloc[i] = -1

    return sig


# ── Core engine ────────────────────────────────────────────────────────────────

def _run_engine(candles: list[dict], strategy: str, params: dict,
                initial_capital: float, commission: float) -> dict:
    df = pd.DataFrame(candles).sort_values("date").reset_index(drop=True)
    signals = _generate_signals(df, strategy, params)

    cash   = initial_capital
    shares = 0
    entry_price = 0.0
    entry_date  = ""
    trades: list[dict] = []
    equity_curve: list[dict] = []

    # Buy-and-hold benchmark: buy at day-0 close
    bh_price  = float(df["close"].iloc[0])
    bh_shares = math.floor(initial_capital / bh_price)
    bh_cash   = initial_capital - bh_shares * bh_price

    for i, row in df.iterrows():
        price = float(row["close"])
        s     = int(signals.iloc[i])

        if s == 1 and shares == 0:
            qty  = math.floor(cash / (price * (1 + commission)))
            if qty > 0:
                cash   -= qty * price * (1 + commission)
                shares  = qty
                entry_price = price
                entry_date  = row["date"]

        elif s == -1 and shares > 0:
            revenue = shares * price * (1 - commission)
            cash += revenue
            cost  = shares * entry_price * (1 + commission)
            pnl   = revenue - cost
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            exit_dt  = datetime.strptime(row["date"], "%Y-%m-%d")
            trades.append({
                "entry_date":   entry_date,
                "exit_date":    row["date"],
                "entry_price":  round(entry_price, 2),
                "exit_price":   round(price, 2),
                "shares":       shares,
                "pnl":          round(pnl, 2),
                "pnl_pct":      round((pnl / cost) * 100, 2),
                "holding_days": (exit_dt - entry_dt).days,
                "type":         "WIN" if pnl > 0 else "LOSS",
            })
            shares = 0

        pv = cash + shares * price
        bv = bh_cash + bh_shares * price
        equity_curve.append({
            "date":      row["date"],
            "portfolio": round(pv, 2),
            "benchmark": round(bv, 2),
        })

    final_pv = cash + shares * float(df["close"].iloc[-1])
    final_bv = bh_cash + bh_shares * float(df["close"].iloc[-1])

    # ── Metrics ────────────────────────────────────────────────────────────────
    total_ret   = ((final_pv - initial_capital) / initial_capital) * 100
    bh_ret      = ((final_bv - initial_capital) / initial_capital) * 100
    n_days      = (datetime.strptime(df["date"].iloc[-1], "%Y-%m-%d") -
                   datetime.strptime(df["date"].iloc[0],  "%Y-%m-%d")).days
    years       = max(n_days / 365.25, 0.1)
    cagr        = ((final_pv / initial_capital) ** (1 / years) - 1) * 100

    vals = [e["portfolio"] for e in equity_curve]
    dr   = [(vals[i] - vals[i-1]) / vals[i-1] for i in range(1, len(vals)) if vals[i-1] > 0]
    if dr:
        mu    = sum(dr) / len(dr)
        sigma = (sum((r - mu)**2 for r in dr) / len(dr)) ** 0.5
        rf_d  = INDIA_RF_RATE / 252
        sharpe = ((mu - rf_d) / sigma * math.sqrt(252)) if sigma > 0 else 0.0
    else:
        sharpe = 0.0

    peak   = initial_capital
    max_dd = 0.0
    for e in equity_curve:
        v = e["portfolio"]
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        max_dd = max(max_dd, dd)

    wins   = [t for t in trades if t["type"] == "WIN"]
    losses = [t for t in trades if t["type"] == "LOSS"]
    gp     = sum(t["pnl"] for t in wins)
    gl     = abs(sum(t["pnl"] for t in losses))
    pf     = (gp / gl) if gl > 0 else (1.0 if not wins else 99.0)
    wr     = (len(wins) / len(trades) * 100) if trades else 0.0
    avg_hd = (sum(t["holding_days"] for t in trades) / len(trades)) if trades else 0.0

    # Downsample equity curve to ≤ 250 points
    step = max(1, len(equity_curve) // 250)
    ec_sampled = equity_curve[::step]
    if equity_curve and ec_sampled[-1] != equity_curve[-1]:
        ec_sampled.append(equity_curve[-1])

    return {
        "metrics": {
            "initial_capital":       round(initial_capital, 2),
            "final_value":           round(final_pv, 2),
            "total_return_pct":      round(total_ret, 2),
            "buy_hold_return_pct":   round(bh_ret, 2),
            "cagr":                  round(cagr, 2),
            "sharpe_ratio":          round(sharpe, 3),
            "max_drawdown_pct":      round(max_dd, 2),
            "win_rate":              round(wr, 1),
            "total_trades":          len(trades),
            "winning_trades":        len(wins),
            "losing_trades":         len(losses),
            "profit_factor":         round(pf, 2),
            "avg_holding_days":      round(avg_hd, 1),
            "gross_profit":          round(gp, 2),
            "gross_loss":            round(gl, 2),
        },
        "trades":       trades,
        "equity_curve": ec_sampled,
        "open_position": shares > 0,
        "candle_count":  len(df),
    }


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/strategies")
async def get_strategies():
    return {"status": "success", "data": STRATEGIES}


@router.post("/run")
async def run_backtest(req: BacktestRequest):
    symbol = req.symbol.upper()
    if req.strategy not in STRATEGIES:
        raise HTTPException(400, f"Unknown strategy '{req.strategy}'. Valid: {list(STRATEGIES)}")

    try:
        start = datetime.strptime(req.start_date, "%Y-%m-%d")
        end   = datetime.strptime(req.end_date,   "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")

    if start >= end:
        raise HTTPException(400, "start_date must be before end_date")

    candles, source = await _fetch_candles(symbol, start, end)

    if len(candles) < 30:
        raise HTTPException(400, "Need at least 30 days of data. Widen your date range.")

    result = _run_engine(candles, req.strategy, req.params, req.initial_capital, req.commission)
    strat  = STRATEGIES[req.strategy]

    logger.info(
        "Backtest completed",
        extra={
            "log_type": "backtest_event",
            "event": "backtest_complete",
            "symbol": symbol,
            "strategy": req.strategy,
            "data_source": source,
            "candle_count": result["candle_count"],
            "total_trades": result["metrics"]["total_trades"],
            "total_return_pct": result["metrics"]["total_return_pct"],
            "win_rate": result["metrics"]["win_rate"],
            "sharpe_ratio": result["metrics"]["sharpe_ratio"],
            "max_drawdown_pct": result["metrics"]["max_drawdown_pct"],
            "start_date": req.start_date,
            "end_date": req.end_date,
        },
    )

    # Persist completed trades to feedback-service so they appear in Orders page
    strat_name = STRATEGIES[req.strategy]["name"]
    records = []
    for t in result["trades"]:
        try:
            entry_dt = datetime.strptime(t["entry_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            exit_dt  = datetime.strptime(t["exit_date"],  "%Y-%m-%d").replace(tzinfo=timezone.utc)
            records.append(_build_trade_record(
                symbol=symbol,
                action="BUY",
                entry_price=t["entry_price"],
                exit_price=t["exit_price"],
                pnl_abs=t["pnl"],
                pnl_pct_decimal=t["pnl_pct"] / 100.0,
                timestamp_open=entry_dt.isoformat(),
                timestamp_close=exit_dt.isoformat(),
                duration_minutes=t["holding_days"] * 375,
                agent_signals=_strategy_agent_signals(req.strategy, "BUY"),
                market_context={"atr": t["entry_price"] * 0.01, "strategy": req.strategy, "data_source": source, "holding_days": t["holding_days"]},
                confidence=0.75,
            ))
        except Exception:
            pass
    asyncio.create_task(_save_backtest_trades(records))

    return {
        "status": "success",
        "data": {
            "symbol":          symbol,
            "strategy":        req.strategy,
            "strategy_name":   strat["name"],
            "start_date":      req.start_date,
            "end_date":        req.end_date,
            "data_source":     source,
            "initial_capital": req.initial_capital,
            "commission_pct":  round(req.commission * 100, 3),
            "params":          req.params,
            **result,
            "generated_at":    datetime.now().isoformat(),
        },
    }


@router.get("/live-signal/{symbol}")
async def get_live_signal(
    symbol: str,
    strategy: str  = Query("sma_crossover"),
    sma_fast: int  = Query(20), sma_slow: int = Query(50),
    rsi_period: int = Query(14), oversold: int = Query(30), overbought: int = Query(70),
    fast: int  = Query(12), slow: int = Query(26), signal: int = Query(9),
    window: int = Query(20), std_dev: float = Query(2.0),
):
    """Current live trading signal + indicator values for paper trading."""
    symbol = symbol.upper()
    params = {
        "sma_fast": sma_fast, "sma_slow": sma_slow,
        "rsi_period": rsi_period, "oversold": oversold, "overbought": overbought,
        "fast": fast, "slow": slow, "signal": signal,
        "window": window, "std_dev": std_dev,
    }

    end   = datetime.now()
    start = end - timedelta(days=300)
    candles, _ = await _fetch_candles(symbol, start, end)

    if not candles:
        raise HTTPException(500, "Could not fetch candle data")

    df  = pd.DataFrame(candles).sort_values("date").reset_index(drop=True)
    sigs = _generate_signals(df, strategy, params)
    close = df["close"].astype(float)

    cur_sig   = int(sigs.iloc[-1])
    sig_label = {1: "BUY", -1: "SELL", 0: "HOLD"}.get(cur_sig, "HOLD")

    # Build recent signal history (last 10 days)
    recent = []
    for i in range(max(0, len(sigs) - 10), len(sigs)):
        sv = int(sigs.iloc[i])
        recent.append({
            "date":   df["date"].iloc[i],
            "signal": {1: "BUY", -1: "SELL", 0: "HOLD"}.get(sv, "HOLD"),
            "close":  round(float(close.iloc[i]), 2),
        })

    # Current indicator snapshot
    def _safe(v): return round(float(v), 4) if not pd.isna(v) else None

    indicators: dict = {}
    if strategy == "sma_crossover":
        sf = ta.trend.SMAIndicator(close, window=sma_fast).sma_indicator()
        ss = ta.trend.SMAIndicator(close, window=sma_slow).sma_indicator()
        fv, sv2 = _safe(sf.iloc[-1]), _safe(ss.iloc[-1])
        indicators = {
            "sma_fast": fv, "sma_slow": sv2,
            "spread_pct": round((fv / sv2 - 1) * 100, 2) if fv and sv2 else None,
        }
    elif strategy == "rsi_mean_reversion":
        rsi = ta.momentum.RSIIndicator(close, window=rsi_period).rsi()
        indicators = {"rsi": _safe(rsi.iloc[-1]), "oversold": oversold, "overbought": overbought}
    elif strategy == "macd_crossover":
        obj = ta.trend.MACD(close, window_fast=fast, window_slow=slow, window_sign=signal)
        indicators = {
            "macd":      _safe(obj.macd().iloc[-1]),
            "signal":    _safe(obj.macd_signal().iloc[-1]),
            "histogram": _safe(obj.macd_diff().iloc[-1]),
        }
    elif strategy == "bollinger_band":
        bb = ta.volatility.BollingerBands(close, window=window, window_dev=std_dev)
        indicators = {
            "upper":  _safe(bb.bollinger_hband().iloc[-1]),
            "middle": _safe(bb.bollinger_mavg().iloc[-1]),
            "lower":  _safe(bb.bollinger_lband().iloc[-1]),
            "pct_b":  _safe(bb.bollinger_pband().iloc[-1]),
        }

    logger.info(
        "Live signal generated",
        extra={
            "log_type": "backtest_event",
            "event": "live_signal",
            "symbol": symbol,
            "strategy": strategy,
            "signal": sig_label,
            "last_price": round(float(close.iloc[-1]), 2),
        },
    )

    return {
        "status": "success",
        "data": {
            "symbol":         symbol,
            "strategy":       strategy,
            "signal":         sig_label,
            "last_price":     round(float(close.iloc[-1]), 2),
            "indicators":     indicators,
            "recent_signals": recent,
            "candle_count":   len(candles),
            "generated_at":   datetime.now().isoformat(),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  AI DAY-TRADING AUTOPILOT
# ═══════════════════════════════════════════════════════════════════════════════

IST = timezone(timedelta(hours=5, minutes=30))

_INTRADAY_BASE = {
    "SBIN": 820, "IDBI": 72, "SUZLON": 58, "INDUSINDBK": 870,
    "TMPV": 356, "PNB": 102, "FEDERALBNK": 182, "TMCV": 378,
    "IREDA": 178, "ZEEL": 135, "IOB": 54, "JKTYRE": 395,
    "RELIANCE": 2850, "TCS": 3450, "INFY": 1720, "HDFCBANK": 1530,
    "ICICIBANK": 1220, "BAJFINANCE": 6900, "WIPRO": 505, "KOTAKBANK": 1820,
    "TRIVENIENT": 2, "VIKASECO": 2, "CROISSANCE": 2, "SYNCOMF": 10, "SHREEGANES": 10,
}


def _simulate_intraday_5min(symbol: str, date_str: str) -> list[dict]:
    """Generate 75 realistic 5-min candles (09:15–15:25 IST) for a trading day."""
    seed = hash(f"{symbol}{date_str}") % (2 ** 31)
    rng = random.Random(seed)

    base = _INTRADAY_BASE.get(symbol, 500.0) * rng.uniform(0.88, 1.12)
    gap_pct = rng.uniform(-0.010, 0.015)
    trend_dir = rng.choice([-1, 1, 1])
    trend_str = rng.uniform(0.0015, 0.0045)
    vol_mult = rng.uniform(0.7, 1.6)

    date = datetime.strptime(date_str, "%Y-%m-%d")
    market_open_ist = datetime(date.year, date.month, date.day, 9, 15, tzinfo=IST)

    price = round(base * (1 + gap_pct), 2)
    candles = []

    for i in range(75):
        candle_time = market_open_ist + timedelta(minutes=5 * i)
        ts = int(candle_time.timestamp())

        # Volume envelope: spike at open, dip at midday, spike at close
        if i < 6:       vol = int(500_000 * vol_mult * rng.uniform(2.5, 4.0))
        elif i < 18:    vol = int(500_000 * vol_mult * rng.uniform(1.2, 2.0))
        elif i < 48:    vol = int(500_000 * vol_mult * rng.uniform(0.5, 1.0))
        elif i < 60:    vol = int(500_000 * vol_mult * rng.uniform(0.9, 1.4))
        else:           vol = int(500_000 * vol_mult * rng.uniform(1.8, 3.2))

        # Price walk
        trend = trend_dir * trend_str * (1 - abs(i - 38) / 75)
        noise = rng.gauss(0, 0.0028)
        if i == 6:   noise += rng.uniform(-0.006, 0.006)   # post-open reversal
        if i == 42:  noise += trend_dir * 0.005             # afternoon momentum burst
        if i >= 66:  noise += rng.uniform(-0.004, 0.004)   # closing volatility

        close = round(price * (1 + trend + noise), 2)
        open_ = round(price * rng.uniform(0.9992, 1.0008), 2)
        high  = round(max(open_, close) * rng.uniform(1.0005, 1.006), 2)
        low   = round(min(open_, close) * rng.uniform(0.994, 0.9995), 2)

        candles.append({
            "time":      candle_time.strftime("%H:%M"),
            "timestamp": ts,
            "open":  open_,
            "high":  high,
            "low":   low,
            "close": close,
            "volume": vol,
        })
        price = close

    return candles


def _intraday_indicators(candles: list[dict], idx: int) -> dict:
    """Compute VWAP, SMAs, RSI, momentum up to candle idx."""
    window = candles[:idx + 1]
    closes = [c["close"] for c in window]
    vols   = [c["volume"] for c in window]
    n = len(closes)

    # VWAP
    tp_sum = sum(
        ((c["high"] + c["low"] + c["close"]) / 3) * c["volume"]
        for c in window
    )
    vwap = tp_sum / max(sum(vols), 1)

    sma5  = sum(closes[-5:])  / min(5,  n)
    sma20 = sum(closes[-20:]) / min(20, n)

    # RSI
    if n >= 15:
        gains  = [max(0, closes[i] - closes[i-1]) for i in range(n-14, n)]
        losses = [max(0, closes[i-1] - closes[i]) for i in range(n-14, n)]
        ag, al = sum(gains)/14, sum(losses)/14
        rsi = 100 - 100 / (1 + ag / max(al, 1e-9))
    else:
        rsi = 50.0

    mom5 = (closes[-1] / closes[-5] - 1) * 100 if n >= 5 else 0.0
    avg_vol = sum(vols) / n
    vol_ratio = vols[-1] / max(avg_vol, 1)

    return {
        "vwap":      round(vwap, 2),
        "sma5":      round(sma5, 2),
        "sma20":     round(sma20, 2),
        "rsi":       round(rsi, 1),
        "mom5":      round(mom5, 3),
        "vol_ratio": round(vol_ratio, 2),
        "above_vwap": closes[-1] > vwap,
    }


def _tech_signal(ind: dict, position: str, candle: dict, entry_price: float) -> int:
    """1 = buy, -1 = sell, 0 = hold based on technicals."""
    rsi, mom5 = ind["rsi"], ind["mom5"]
    price = candle["close"]
    vwap, sma5, sma20 = ind["vwap"], ind["sma5"], ind["sma20"]
    try:
        h, m = int(candle["time"].split(":")[0]), int(candle["time"].split(":")[1])
    except (KeyError, ValueError, IndexError):
        h, m = 0, 0  # safe fallback — never force-close

    # Force square-off ≥ 14:45
    if position == "LONG" and (h > 14 or (h == 14 and m >= 45)):
        return -1

    if position == "NONE":
        if rsi < 35 and price >= vwap and mom5 > 0:      return 1
        if sma5 > sma20 and mom5 > 0.18 and rsi < 62:   return 1
        if mom5 > 0.35 and price > vwap and rsi < 66:   return 1
    elif position == "LONG":
        gain_pct = (price - entry_price) / entry_price * 100
        if rsi > 72:                          return -1
        if sma5 < sma20 and mom5 < -0.12:    return -1
        if mom5 < -0.28:                      return -1
        if gain_pct >= 2.5:                  return -1   # take profit
        if gain_pct <= -1.5:                 return -1   # stop loss

    return 0


async def _llm_decide(
    symbol: str, date: str, candle: dict, ind: dict,
    position: str, entry_price: float, unrealised: float,
    cash: float, signal_hint: int, recent: list[dict], model: str,
) -> dict:
    """Call LLM for a trading decision; fall back to rule-based if LLM fails."""
    max_qty = max(1, int(cash * 0.95 / candle["close"])) if position == "NONE" else 0
    hint_txt = {1: "BUY SIGNAL", -1: "SELL SIGNAL", 0: "HOLD"}.get(signal_hint, "HOLD")
    pos_txt = f"LONG at ₹{entry_price:.2f} (P&L: ₹{unrealised:+.2f})" if position == "LONG" else "NONE (cash)"
    recent_str = "\n".join(
        f"  {c['time']}: O:{c['open']} H:{c['high']} L:{c['low']} C:{c['close']} V:{c['volume']:,}"
        for c in recent[-6:]
    )

    prompt = f"""You are an AI day trader on the NSE, India.
Stock: {symbol} | Date: {date} | Time: {candle['time']} IST

Current 5-min candle: O:{candle['open']} H:{candle['high']} L:{candle['low']} C:{candle['close']}
Recent candles:
{recent_str}

Indicators:
- VWAP: ₹{ind['vwap']} | Price {'above' if ind['above_vwap'] else 'below'} VWAP
- SMA5: ₹{ind['sma5']} | SMA20: ₹{ind['sma20']}
- RSI(14): {ind['rsi']} | 5-candle momentum: {ind['mom5']:+.2f}%
- Volume ratio vs avg: {ind['vol_ratio']}x

Technical system: {hint_txt}
Current position: {pos_txt}
Available capital: ₹{cash:.0f} | Max qty: {max_qty}

Rules: Day trading only (square off by 14:45 IST). One position at a time.

Respond in JSON only:
{{"action":"BUY"|"SELL"|"HOLD","confidence":50-95,"quantity":{max_qty if position=="NONE" else 0},"reason":"1-2 sentences"}}"""

    try:
        import ollama as ollama_lib
        llm_url = getattr(settings, "LLM_API_URL", "http://host.docker.internal:11434")
        client = ollama_lib.AsyncClient(host=llm_url)
        resp = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.25},
            ),
            timeout=15.0,
        )
        raw = resp["message"]["content"].strip()
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            action = parsed.get("action", "HOLD").upper()
            if action not in ("BUY", "SELL", "HOLD"):
                action = "HOLD"
            return {
                "action":     action,
                "confidence": int(parsed.get("confidence", 65)),
                "quantity":   int(parsed.get("quantity", max_qty if action == "BUY" else 0)),
                "reason":     str(parsed.get("reason", "Technical signal.")),
            }
    except Exception as exc:
        logger.warning(
            "LLM day-trade decision failed, using rule-based fallback",
            extra={"log_type": "backtest_event", "event": "llm_fallback", "error": str(exc)},
        )

    # Rule-based fallback
    action = {1: "BUY", -1: "SELL", 0: "HOLD"}.get(signal_hint, "HOLD")
    reasons = {
        "BUY":  f"RSI {ind['rsi']:.0f} oversold; price {'above' if ind['above_vwap'] else 'near'} VWAP with positive momentum.",
        "SELL": f"RSI {ind['rsi']:.0f}; momentum {ind['mom5']:+.2f}% — taking profit/cutting loss.",
        "HOLD": "No clear entry signal; waiting for confirmation.",
    }
    return {"action": action, "confidence": 62, "quantity": max_qty if action == "BUY" else 0, "reason": reasons[action]}


class DayAutopilotRequest(BaseModel):
    symbol:     str
    date:       str    # YYYY-MM-DD
    start_time: str = "09:15"   # IST HH:MM — AI watches from this time onwards
    capital:    float = Field(default=50_000.0, ge=5_000, le=10_000_000)
    model:      Optional[str] = None


@router.post("/day-autopilot")
async def day_autopilot(req: DayAutopilotRequest):
    """AI-powered intraday day trading simulation for a single date."""
    symbol = req.symbol.upper()
    try:
        datetime.strptime(req.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    # Fetch real intraday data or simulate
    candles = None
    data_source = "simulated"
    groww = get_groww_client()
    if groww:
        try:
            trade_date = datetime.strptime(req.date, "%Y-%m-%d")
            logger.info(
                "Calling Groww get_historical for day autopilot",
                extra={"log_type": "groww_call", "caller": "backtest.day_autopilot", "method": "get_historical", "symbol": symbol, "date": req.date, "interval_minutes": 5},
            )
            raw = await groww.get_historical(symbol, 5, trade_date, trade_date.replace(hour=23, minute=59))
            if raw and len(raw) > 30:
                parsed = []
                for c in raw:
                    if isinstance(c, list) and len(c) >= 6:
                        ts = int(c[0])
                        dt_ist = datetime.fromtimestamp(ts, tz=IST)
                        parsed.append({
                            "time":      dt_ist.strftime("%H:%M"),
                            "timestamp": ts,
                            "open":  float(c[1]), "high": float(c[2]),
                            "low":   float(c[3]), "close": float(c[4]),
                            "volume": int(c[5]),
                        })
                if parsed:
                    candles = parsed
                    data_source = "groww"
        except Exception as exc:
            logger.warning(
                "Groww intraday fetch failed, using simulation",
                extra={"log_type": "backtest_event", "event": "intraday_fallback", "error": str(exc)},
            )

    if not candles:
        candles = _simulate_intraday_5min(symbol, req.date)

    # ── Start time → candle index ─────────────────────────────────────────────
    try:
        sh, sm = map(int, req.start_time.split(":"))
    except Exception:
        sh, sm = 9, 15
    start_idx = max(0, ((sh * 60 + sm) - (9 * 60 + 15)) // 5)
    start_idx = min(start_idx, len(candles) - 20)  # leave room for decisions

    # ── Simulation ─────────────────────────────────────────────────────────────
    model   = req.model or getattr(settings, "LLM_MODEL", "llama3.2")
    capital = req.capital
    cash    = capital
    position   = "NONE"
    entry_price = 0.0
    qty         = 0
    trades: list[dict] = []

    # Decision points: every 3 candles (15 min), starting from start_idx
    decision_pts = list(range(max(start_idx, 3), len(candles) - 1, 3))

    for idx in decision_pts:
        candle = candles[idx]
        ind    = _intraday_indicators(candles, idx)
        signal = _tech_signal(ind, position, candle, entry_price)

        # Skip non-events
        if signal == 0 and position == "NONE":   continue
        if signal == 1 and position == "LONG":   continue
        if signal == -1 and position == "NONE":  continue

        unrealised = (candle["close"] - entry_price) * qty if position == "LONG" else 0.0
        dec = await _llm_decide(
            symbol, req.date, candle, ind,
            position, entry_price, unrealised,
            cash, signal, candles[max(0, idx - 5):idx + 1], model,
        )

        action = dec["action"]

        if action == "BUY" and position == "NONE":
            qty  = max(1, int(cash * 0.95 / candle["close"]))
            cost = qty * candle["close"]
            if cost <= cash:
                cash -= cost
                position    = "LONG"
                entry_price = candle["close"]
                trades.append({
                    "time":       candle["time"],
                    "timestamp":  candle["timestamp"],
                    "action":     "BUY",
                    "price":      candle["close"],
                    "quantity":   qty,
                    "confidence": dec["confidence"],
                    "reason":     dec["reason"],
                    "pnl":        None,
                    "pnl_pct":    None,
                    "candle_index": idx,
                    "indicators": ind,
                })

        elif action == "SELL" and position == "LONG":
            revenue = qty * candle["close"]
            pnl     = revenue - qty * entry_price
            cash   += revenue
            trades.append({
                "time":       candle["time"],
                "timestamp":  candle["timestamp"],
                "action":     "SELL",
                "price":      candle["close"],
                "quantity":   qty,
                "confidence": dec["confidence"],
                "reason":     dec["reason"],
                "pnl":        round(pnl, 2),
                "pnl_pct":    round(pnl / (qty * entry_price) * 100, 2),
                "candle_index": idx,
                "indicators": ind,
            })
            position    = "NONE"
            qty         = 0
            entry_price = 0.0

    # Force close at end of day
    if position == "LONG":
        last    = candles[-1]
        revenue = qty * last["close"]
        pnl     = revenue - qty * entry_price
        cash   += revenue
        trades.append({
            "time":       last["time"],
            "timestamp":  last["timestamp"],
            "action":     "SELL",
            "price":      last["close"],
            "quantity":   qty,
            "confidence": 99,
            "reason":     "Market close — all positions squared off automatically.",
            "pnl":        round(pnl, 2),
            "pnl_pct":    round(pnl / (qty * entry_price) * 100, 2),
            "candle_index": len(candles) - 1,
            "indicators": {},
        })

    final_capital = cash
    total_pnl     = round(final_capital - capital, 2)
    sell_trades   = [t for t in trades if t["action"] == "SELL"]
    wins          = [t for t in sell_trades if (t.get("pnl") or 0) > 0]
    losses        = [t for t in sell_trades if (t.get("pnl") or 0) <= 0]

    logger.info(
        "Day autopilot simulation completed",
        extra={
            "log_type": "backtest_event",
            "event": "autopilot_complete",
            "symbol": symbol,
            "date": req.date,
            "data_source": data_source,
            "model_used": model,
            "capital": capital,
            "total_pnl": total_pnl,
            "total_pnl_pct": round(total_pnl / capital * 100, 2),
            "total_trades": len(sell_trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
        },
    )

    # Persist completed sell trades (round trips) to Orders page
    _ap_records = []
    buy_map: dict[str, dict] = {}
    for t in trades:
        if t["action"] == "BUY":
            buy_map["current"] = t
        elif t["action"] == "SELL" and t.get("pnl") is not None:
            buy_t = buy_map.pop("current", None)
            entry_price = buy_t["price"] if buy_t else t["price"]
            entry_time_str = buy_t["time"] if buy_t else t["time"]
            entry_dt = datetime.strptime(f"{req.date} {entry_time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            exit_dt  = datetime.strptime(f"{req.date} {t['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            dur = int((exit_dt - entry_dt).total_seconds() / 60)
            ind = t.get("indicators", {})
            _ap_records.append(_build_trade_record(
                symbol=symbol,
                action="BUY",
                entry_price=entry_price,
                exit_price=t["price"],
                pnl_abs=t["pnl"],
                pnl_pct_decimal=t["pnl_pct"] / 100.0,
                timestamp_open=entry_dt.isoformat(),
                timestamp_close=exit_dt.isoformat(),
                duration_minutes=dur,
                agent_signals=_derive_agent_signals("BUY", ind),
                market_context={"atr": ind.get("vwap", entry_price) * 0.008, "regime": "intraday", "data_source": data_source, "vwap": ind.get("vwap"), "rsi": ind.get("rsi")},
                confidence=t.get("confidence", 70) / 100.0,
            ))
    asyncio.create_task(_save_backtest_trades(_ap_records))

    return {
        "status": "success",
        "data": {
            "symbol":        symbol,
            "date":          req.date,
            "capital":       capital,
            "candles":       candles,

            "trades":        trades,
            "metrics": {
                "total_pnl":       total_pnl,
                "total_pnl_pct":   round(total_pnl / capital * 100, 2),
                "final_capital":   round(final_capital, 2),
                "total_trades":    len(sell_trades),
                "winning_trades":  len(wins),
                "losing_trades":   len(losses),
                "gross_profit":    round(sum(t["pnl"] for t in wins), 2),
                "gross_loss":      round(abs(sum(t["pnl"] for t in losses)), 2),
            },
            "start_candle_index": start_idx,
            "start_time":        req.start_time,
            "data_source":       data_source,
            "model_used":        model,
            "generated_at":      datetime.now().isoformat(),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  PROGRESSIVE AUTOPILOT — candles + per-step agent decisions (no lookahead)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/intraday-candles/{symbol}")
async def get_intraday_candles(symbol: str, date: str = Query(...)):
    """Return all 5-min candles for one trading day (Groww or simulated).
    No LLM involved — the frontend drives the progressive replay."""
    symbol = symbol.upper()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    candles, data_source = None, "simulated"
    groww = get_groww_client()
    if groww:
        try:
            trade_date = datetime.strptime(date, "%Y-%m-%d")
            logger.info(
                "Calling Groww get_historical for intraday candles",
                extra={"log_type": "groww_call", "caller": "backtest.get_intraday_candles", "method": "get_historical", "symbol": symbol, "date": date, "interval_minutes": 5},
            )
            raw = await groww.get_historical(
                symbol, 5,
                trade_date,
                trade_date.replace(hour=23, minute=59),
            )
            if raw and len(raw) > 30:
                parsed = []
                for c in raw:
                    if isinstance(c, list) and len(c) >= 6:
                        ts = int(c[0])
                        dt_ist = datetime.fromtimestamp(ts, tz=IST)
                        parsed.append({
                            "time":      dt_ist.strftime("%H:%M"),
                            "timestamp": ts,
                            "open":  float(c[1]), "high": float(c[2]),
                            "low":   float(c[3]), "close": float(c[4]),
                            "volume": int(c[5]),
                        })
                if parsed:
                    candles = parsed
                    data_source = "groww"
        except Exception as exc:
            logger.warning(
                "Groww intraday fetch failed, using simulation",
                extra={"log_type": "backtest_event", "event": "intraday_fallback", "error": str(exc)},
            )

    if not candles:
        candles = _simulate_intraday_5min(symbol, date)

    return {
        "status": "success",
        "data": {
            "symbol":        symbol,
            "date":          date,
            "candles":       candles,
            "data_source":   data_source,
            "total_candles": len(candles),
        },
    }


class AgentStepRequest(BaseModel):
    symbol:      str
    date:        str
    candles:     list[dict]   # ALL candles seen so far — no future data
    position:    str = "NONE" # "NONE" or "LONG"
    entry_price: float = 0.0
    entry_time:  str = ""
    entry_qty:   int = 0
    capital:     float = 50_000.0
    model:       Optional[str] = None


@router.post("/agent-step")
async def agent_step(req: AgentStepRequest):
    """Single LLM decision step using only the candles seen so far.
    The agent has zero lookahead — it cannot see future price data."""
    if not req.candles:
        raise HTTPException(400, "candles list is empty")

    symbol  = req.symbol.upper()
    candles = req.candles
    idx     = len(candles) - 1
    candle  = candles[idx]

    ind    = _intraday_indicators(candles, idx)
    signal = _tech_signal(ind, req.position, candle, req.entry_price)

    # Fast-path: skip LLM for unambiguous no-ops
    if (
        (signal == 0  and req.position == "NONE") or   # no signal, nothing to do
        (signal == 1  and req.position == "LONG") or   # buy signal but already holding
        (signal == -1 and req.position == "NONE")      # sell signal but no position
    ):
        return {
            "status": "success",
            "data": {
                "action": "HOLD", "confidence": 50, "quantity": 0,
                "reason": "No actionable signal at this candle.",
                "indicators": ind, "candle_index": idx,
                "candle_time": candle.get("time", ""), "price": candle["close"],
                "signal_hint": "HOLD",
            },
        }

    model      = req.model or getattr(settings, "LLM_MODEL", "llama3.2")
    unrealised = (
        (candle["close"] - req.entry_price) * req.entry_qty
        if req.position == "LONG" else 0.0
    )
    recent = candles[max(0, idx - 5): idx + 1]

    dec = await _llm_decide(
        symbol, req.date, candle, ind,
        req.position, req.entry_price, unrealised,
        req.capital, signal, recent, model,
    )

    return {
        "status": "success",
        "data": {
            "action":      dec["action"],
            "confidence":  dec["confidence"],
            "quantity":    dec["quantity"],
            "reason":      dec["reason"],
            "indicators":  ind,
            "candle_index": idx,
            "candle_time":  candle.get("time", ""),
            "price":        candle["close"],
            "signal_hint":  {1: "BUY", -1: "SELL", 0: "HOLD"}.get(signal, "HOLD"),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  PROGRESSIVE BACKTEST — stateless, fresh Groww fetch every step
#  Client holds session state (cash, position, trades) and passes it each call.
#  Server fetches candles from market open up to current_time on every request.
# ═══════════════════════════════════════════════════════════════════════════════

_MARKET_OPEN_MINUTES = 9 * 60 + 15   # 09:15
_SQUAREOFF_MINUTES   = 15 * 60 + 25  # 15:25 — last candle, force close


def _time_to_minutes(hhmm: str) -> int:
    try:
        h, m = map(int, hhmm.split(":"))
        return h * 60 + m
    except Exception:
        return _MARKET_OPEN_MINUTES


def _minutes_to_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


async def _fetch_candles_up_to(symbol: str, date_str: str, up_to_time: str) -> tuple[list[dict], str]:
    """Fetch 5-min candles from market open up to up_to_time (HH:MM inclusive)."""
    date = datetime.strptime(date_str, "%Y-%m-%d")
    up_to_minutes = _time_to_minutes(up_to_time)

    groww = get_groww_client()
    if groww:
        try:
            logger.info(
                "Calling Groww get_historical for progressive step",
                extra={
                    "log_type": "groww_call",
                    "caller": "backtest.progressive",
                    "method": "get_historical",
                    "symbol": symbol,
                    "date": date_str,
                    "up_to_time": up_to_time,
                    "interval_minutes": 5,
                },
            )
            raw = await groww.get_historical(symbol, 5, date, date.replace(hour=23, minute=59))
            if raw and len(raw) >= 1:
                parsed = []
                for c in raw:
                    if isinstance(c, list) and len(c) >= 6:
                        ts = int(c[0])
                        dt_ist = datetime.fromtimestamp(ts, tz=IST)
                        candle_minutes = dt_ist.hour * 60 + dt_ist.minute
                        if candle_minutes <= up_to_minutes:
                            parsed.append({
                                "time":      dt_ist.strftime("%H:%M"),
                                "timestamp": ts,
                                "open":  float(c[1]), "high": float(c[2]),
                                "low":   float(c[3]), "close": float(c[4]),
                                "volume": int(c[5]),
                            })
                if parsed:
                    return parsed, "groww"
        except Exception as exc:
            logger.warning(
                "Groww historical fetch failed for progressive step, using simulation",
                extra={
                    "log_type": "backtest_event",
                    "event": "progressive_fallback",
                    "symbol": symbol,
                    "error": str(exc),
                },
            )

    all_candles = _simulate_intraday_5min(symbol, date_str)
    sliced = [c for c in all_candles if _time_to_minutes(c["time"]) <= up_to_minutes]
    return (sliced if sliced else all_candles[:1]), "simulated"


def _prev_trading_day(d: datetime) -> datetime:
    """Return the most recent weekday (Mon–Fri) strictly before d."""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:   # Saturday=5, Sunday=6
        prev -= timedelta(days=1)
    return prev


async def _fetch_full_day_candles(symbol: str, date_str: str) -> list[dict]:
    """Fetch the complete trading-day candles for a past date (background display only).
    Returns empty list silently on any failure — callers must tolerate missing data.
    """
    date = datetime.strptime(date_str, "%Y-%m-%d")
    groww = get_groww_client()
    if not groww:
        return []
    try:
        raw = await groww.get_historical(symbol, 5, date, date.replace(hour=23, minute=59))
        if not raw:
            return []
        parsed = []
        for c in raw:
            if isinstance(c, list) and len(c) >= 6:
                ts = int(c[0])
                dt_ist = datetime.fromtimestamp(ts, tz=IST)
                m = dt_ist.hour * 60 + dt_ist.minute
                if _MARKET_OPEN_MINUTES <= m <= _SQUAREOFF_MINUTES:
                    parsed.append({
                        "time":      dt_ist.strftime("%H:%M"),
                        "timestamp": ts,
                        "open":  float(c[1]), "high": float(c[2]),
                        "low":   float(c[3]), "close": float(c[4]),
                        "volume": int(c[5]),
                    })
        return parsed
    except Exception:
        return []


def _compute_metrics(cash: float, capital: float, trades: list[dict]) -> dict:
    sell_trades = [t for t in trades if t["action"] == "SELL"]
    wins   = [t for t in sell_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in sell_trades if (t.get("pnl") or 0) <= 0]
    total_pnl = round(cash - capital, 2)
    return {
        "total_pnl":      total_pnl,
        "total_pnl_pct":  round(total_pnl / capital * 100, 2) if capital else 0,
        "final_capital":  round(cash, 2),
        "total_trades":   len(sell_trades),
        "winning_trades": len(wins),
        "losing_trades":  len(losses),
        "gross_profit":   round(sum(t["pnl"] for t in wins), 2),
        "gross_loss":     round(abs(sum(t["pnl"] for t in losses)), 2),
    }


class ProgressiveStartRequest(BaseModel):
    symbol:     str
    date:       str
    start_time: str = "09:15"
    capital:    float = Field(default=50_000.0, ge=5_000, le=10_000_000)
    model:      Optional[str] = None


class ProgressiveStepRequest(BaseModel):
    symbol:       str
    date:         str
    current_time: str            # HH:MM — the last candle time the client already has
    capital:      float
    cash:         float
    position:     str = "NONE"
    quantity:     int = 0
    entry_price:  float = 0.0
    entry_time:   Optional[str] = None
    trades:       list[dict] = []
    model:        Optional[str] = None


@router.post("/progressive/start")
async def progressive_start(req: ProgressiveStartRequest):
    """Start a progressive backtest session.

    Fetches all 5-min candles from market open to start_time, runs the AI agent
    on the last candle, optionally executes the first trade, and returns the
    initial session state the client must persist and pass back each step.
    """
    symbol = req.symbol.upper()
    try:
        req_date = datetime.strptime(req.date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    today_ist = datetime.now(IST).date()
    if req_date >= today_ist:
        raise HTTPException(
            400,
            f"Groww historical API only provides data for completed trading days. "
            f"Please select a past date (last trading day: {today_ist - timedelta(days=1 if today_ist.weekday() > 0 else 3)})."
        )
    if req_date.weekday() >= 5:
        raise HTTPException(400, "Selected date is a weekend. Please pick a weekday (Mon–Fri).")

    start_minutes = max(_MARKET_OPEN_MINUTES, min(_time_to_minutes(req.start_time), _SQUAREOFF_MINUTES))
    start_time_str = _minutes_to_time(start_minutes)

    candles, data_source = await _fetch_candles_up_to(symbol, req.date, start_time_str)
    if not candles:
        raise HTTPException(500, "Could not fetch candle data")

    # Fetch previous trading day's full candles for background chart display only.
    # Failures are silent — an empty list is fine.
    prev_date_str = _prev_trading_day(datetime.strptime(req.date, "%Y-%m-%d")).strftime("%Y-%m-%d")
    prev_day_candles = await _fetch_full_day_candles(symbol, prev_date_str)

    model = req.model or getattr(settings, "LLM_MODEL", "llama3.2")
    idx    = len(candles) - 1
    candle = candles[idx]
    ind    = _intraday_indicators(candles, idx)
    signal = _tech_signal(ind, "NONE", candle, 0.0)

    dec = await _llm_decide(
        symbol, req.date, candle, ind,
        "NONE", 0.0, 0.0,
        req.capital, signal,
        candles[max(0, idx - 5):idx + 1], model,
    )

    cash        = req.capital
    position    = "NONE"
    quantity    = 0
    entry_price = 0.0
    entry_time  = None
    trades: list[dict] = []
    trade_executed = None

    if dec["action"] == "BUY":
        qty  = dec.get("quantity") or max(1, int(cash * 0.95 / candle["close"]))
        cost = qty * candle["close"]
        if cost <= cash:
            cash       -= cost
            position    = "LONG"
            quantity    = qty
            entry_price = candle["close"]
            entry_time  = candle["time"]
            trade_executed = {"action": "BUY", "price": candle["close"], "quantity": qty, "pnl": None, "time": candle["time"]}
            trades.append({
                "time": candle["time"], "timestamp": candle.get("timestamp", 0),
                "action": "BUY", "price": candle["close"], "quantity": qty,
                "confidence": dec["confidence"], "reason": dec["reason"],
                "pnl": None, "pnl_pct": None, "candle_index": idx, "indicators": ind,
            })

    next_minutes    = start_minutes + 5
    is_market_closed = next_minutes >= _SQUAREOFF_MINUTES
    next_time       = _minutes_to_time(next_minutes) if not is_market_closed else "market_closed"

    logger.info(
        "Progressive backtest started",
        extra={
            "log_type": "backtest_event", "event": "progressive_start",
            "symbol": symbol, "date": req.date, "start_time": start_time_str,
            "capital": req.capital, "candles_fetched": len(candles),
            "data_source": data_source, "agent_action": dec["action"],
        },
    )

    return {
        "status": "success",
        "data": {
            "symbol":         symbol,
            "date":           req.date,
            "current_time":   start_time_str,
            "candles":        candles,
            "latest_candle":  candle,
            "indicators":     ind,
            "agent_decision": dec,
            "trade_executed": trade_executed,
            "position": {
                "status":      position,
                "entry_price": entry_price,
                "quantity":    quantity,
                "entry_time":  entry_time,
                "current_pnl": 0.0,
            },
            "cash":            round(cash, 2),
            "capital":         req.capital,
            "trades":          trades,
            "metrics":         _compute_metrics(cash, req.capital, trades),
            "is_market_closed": is_market_closed,
            "next_time":       next_time,
            "data_source":      data_source,
            "model_used":       model,
            "prev_day_candles": prev_day_candles,
            "prev_day_date":    prev_date_str,
        },
    }


@router.post("/progressive/step")
async def progressive_step(req: ProgressiveStepRequest):
    """Advance the progressive backtest by one 5-min candle.

    The client passes its full session state (cash, position, trades, etc.).
    The server fetches fresh candles from Groww from market open up to the
    next candle time, runs the AI agent, executes any trade, and returns the
    updated state the client should persist for the following step.
    """
    symbol = req.symbol.upper()
    try:
        datetime.strptime(req.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    current_minutes = _time_to_minutes(req.current_time)
    next_minutes    = current_minutes + 5
    next_time_str   = _minutes_to_time(next_minutes)
    is_market_closed = next_minutes >= _SQUAREOFF_MINUTES

    candles, data_source = await _fetch_candles_up_to(symbol, req.date, next_time_str)
    if not candles:
        raise HTTPException(500, "Could not fetch candle data")

    model       = req.model or getattr(settings, "LLM_MODEL", "llama3.2")
    cash        = req.cash
    position    = req.position
    quantity    = req.quantity
    entry_price = req.entry_price
    entry_time  = req.entry_time
    trades      = list(req.trades)

    idx    = len(candles) - 1
    candle = candles[idx]
    ind    = _intraday_indicators(candles, idx)

    force_squareoff = is_market_closed and position == "LONG"
    signal = -1 if force_squareoff else _tech_signal(ind, position, candle, entry_price)

    unrealised = (candle["close"] - entry_price) * quantity if position == "LONG" else 0.0
    dec = await _llm_decide(
        symbol, req.date, candle, ind,
        position, entry_price, unrealised,
        cash, signal,
        candles[max(0, idx - 5):idx + 1], model,
    )

    if force_squareoff:
        dec["action"]     = "SELL"
        dec["reason"]     = "Market close — all positions squared off automatically."
        dec["confidence"] = 99

    trade_executed = None

    if dec["action"] == "BUY" and position == "NONE":
        qty  = dec.get("quantity") or max(1, int(cash * 0.95 / candle["close"]))
        cost = qty * candle["close"]
        if cost <= cash:
            cash       -= cost
            position    = "LONG"
            quantity    = qty
            entry_price = candle["close"]
            entry_time  = candle["time"]
            trade_executed = {"action": "BUY", "price": candle["close"], "quantity": qty, "pnl": None, "time": candle["time"]}
            trades.append({
                "time": candle["time"], "timestamp": candle.get("timestamp", 0),
                "action": "BUY", "price": candle["close"], "quantity": qty,
                "confidence": dec["confidence"], "reason": dec["reason"],
                "pnl": None, "pnl_pct": None, "candle_index": idx, "indicators": ind,
            })

    elif dec["action"] == "SELL" and position == "LONG":
        revenue = quantity * candle["close"]
        pnl     = revenue - quantity * entry_price
        cash   += revenue
        pnl_pct = round(pnl / (quantity * entry_price) * 100, 2) if entry_price > 0 else 0.0
        trade_executed = {"action": "SELL", "price": candle["close"], "quantity": quantity, "pnl": round(pnl, 2), "time": candle["time"]}
        trades.append({
            "time": candle["time"], "timestamp": candle.get("timestamp", 0),
            "action": "SELL", "price": candle["close"], "quantity": quantity,
            "confidence": dec["confidence"], "reason": dec["reason"],
            "pnl": round(pnl, 2), "pnl_pct": pnl_pct,
            "candle_index": idx, "indicators": ind,
        })
        # Save this completed round-trip to the Orders page
        try:
            _open_time = entry_time or next_time_str
            entry_dt = datetime.strptime(f"{req.date} {_open_time}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            exit_dt  = datetime.strptime(f"{req.date} {candle['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            dur = int((exit_dt - entry_dt).total_seconds() / 60)
            asyncio.create_task(_save_backtest_trades([_build_trade_record(
                symbol=symbol,
                action="BUY",
                entry_price=entry_price,
                exit_price=candle["close"],
                pnl_abs=round(pnl, 2),
                pnl_pct_decimal=pnl_pct / 100.0,
                timestamp_open=entry_dt.isoformat(),
                timestamp_close=exit_dt.isoformat(),
                duration_minutes=dur,
                agent_signals=_derive_agent_signals("BUY", ind),
                market_context={"atr": ind.get("vwap", entry_price) * 0.008, "regime": "intraday", "vwap": ind.get("vwap"), "rsi": ind.get("rsi")},
                confidence=dec["confidence"] / 100.0,
            )]))
        except Exception:
            pass
        position    = "NONE"
        quantity    = 0
        entry_price = 0.0
        entry_time  = None

    current_pnl = (candle["close"] - entry_price) * quantity if position == "LONG" else 0.0

    after_next_minutes = next_minutes + 5
    next_next_time = (
        _minutes_to_time(after_next_minutes)
        if after_next_minutes < _SQUAREOFF_MINUTES
        else "market_closed"
    )

    logger.info(
        "Progressive step processed",
        extra={
            "log_type": "backtest_event", "event": "progressive_step",
            "symbol": symbol, "date": req.date, "time": next_time_str,
            "agent_action": dec["action"], "position": position,
            "cash": round(cash, 2), "is_market_closed": is_market_closed,
        },
    )

    return {
        "status": "success",
        "data": {
            "symbol":         symbol,
            "date":           req.date,
            "current_time":   next_time_str,
            "candles":        candles,
            "latest_candle":  candle,
            "indicators":     ind,
            "agent_decision": dec,
            "trade_executed": trade_executed,
            "position": {
                "status":      position,
                "entry_price": entry_price,
                "quantity":    quantity,
                "entry_time":  entry_time,
                "current_pnl": round(current_pnl, 2),
            },
            "cash":            round(cash, 2),
            "capital":         req.capital,
            "trades":          trades,
            "metrics":         _compute_metrics(cash, req.capital, trades),
            "is_market_closed": is_market_closed,
            "next_time":       next_next_time,
            "data_source":     data_source,
            "model_used":      model,
        },
    }
