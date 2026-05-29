"""
AI Agent API — fetches 1-year daily candles from Groww, computes technical indicators
using the `ta` library, then calls the local Ollama LLM for structured analysis.
"""
import random
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import ta
import ollama as ollama_lib
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# All stocks available for AI analysis (portfolio + standard NSE)
KNOWN_STOCKS = {
    # User's actual portfolio stocks
    "IDBI":        "IDBI Bank",
    "SUZLON":      "Suzlon Energy",
    "SHREEGANES":  "Shree Ganesh BioTech",
    "SBIN":        "State Bank of India",
    "INDUSINDBK":  "IndusInd Bank",
    "TMPV":        "Tata Motors Pref (DVR)",
    "PNB":         "Punjab National Bank",
    "FEDERALBNK":  "Federal Bank",
    "TMCV":        "Tata Motors CV",
    "IREDA":       "Indian Renewable Energy Dev.",
    "ZEEL":        "Zee Entertainment",
    "SYNCOMF":     "Syncom Formulations",
    "IOB":         "Indian Overseas Bank",
    "JKTYRE":      "JK Tyre & Industries",
    "VIKASECO":    "Vikas Ecotech",
    # Standard large-cap NSE stocks
    "RELIANCE":    "Reliance Industries",
    "TCS":         "Tata Consultancy Services",
    "INFY":        "Infosys",
    "HDFCBANK":    "HDFC Bank",
    "ICICIBANK":   "ICICI Bank",
    "HINDUNILVR":  "Hindustan Unilever",
    "BAJFINANCE":  "Bajaj Finance",
    "WIPRO":       "Wipro",
    "KOTAKBANK":   "Kotak Mahindra Bank",
    "TATAMOTORS":  "Tata Motors",
    "ADANIENT":    "Adani Enterprises",
    "MARUTI":      "Maruti Suzuki",
    "SUNPHARMA":   "Sun Pharmaceutical",
    "TITAN":       "Titan Company",
}


# ── Candle helpers ─────────────────────────────────────────────────────────────

def _parse_candles(raw: list) -> list[dict]:
    candles = []
    for c in raw:
        if isinstance(c, list) and len(c) >= 6:
            ts = c[0]
            candles.append({
                "timestamp": datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else str(ts)[:10],
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": int(c[5]),
            })
        elif isinstance(c, dict):
            candles.append({
                "timestamp": str(c.get("timestamp", c.get("time", "")))[:10],
                "open":   float(c.get("open", 0)),
                "high":   float(c.get("high", 0)),
                "low":    float(c.get("low", 0)),
                "close":  float(c.get("close", 0)),
                "volume": int(c.get("volume", 0)),
            })
    return [c for c in candles if c["close"] > 0]


def _simulate_candles(symbol: str, days: int = 365) -> list[dict]:
    base_prices = {
        "SBIN": 820, "IDBI": 72, "SUZLON": 58, "INDUSINDBK": 870,
        "TMPV": 356, "PNB": 102, "FEDERALBNK": 182, "TMCV": 378,
        "IREDA": 178, "ZEEL": 135, "IOB": 54, "JKTYRE": 395,
        "RELIANCE": 2850, "TCS": 3450, "INFY": 1720, "HDFCBANK": 1530,
        "ICICIBANK": 1220, "BAJFINANCE": 6900, "WIPRO": 505, "KOTAKBANK": 1820,
    }
    base = base_prices.get(symbol, 500.0)
    candles = []
    end = datetime.now()
    for i in range(days):
        date = end - timedelta(days=days - i)
        if date.weekday() >= 5:
            continue
        o = round(base * random.uniform(0.991, 1.009), 2)
        c = round(o * random.uniform(0.993, 1.007), 2)
        candles.append({
            "timestamp": date.strftime("%Y-%m-%d"),
            "open":   o,
            "high":   round(max(o, c) * random.uniform(1.001, 1.012), 2),
            "low":    round(min(o, c) * random.uniform(0.988, 0.999), 2),
            "close":  c,
            "volume": random.randint(300_000, 12_000_000),
        })
        base = c
    return candles


# ── Technical indicators ───────────────────────────────────────────────────────

def _safe(series: "pd.Series", idx: int = -1) -> Optional[float]:
    try:
        v = float(series.iloc[idx])
        return None if pd.isna(v) else round(v, 2)
    except Exception:
        return None


def _compute_indicators(candles: list[dict]) -> dict:
    if len(candles) < 30:
        return {}

    df = pd.DataFrame(candles).sort_values("timestamp").reset_index(drop=True)
    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    vol   = df["volume"].astype(float)

    sma20 = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    sma50 = ta.trend.SMAIndicator(close, window=min(50, len(df) - 1)).sma_indicator()

    ema12 = ta.trend.EMAIndicator(close, window=12).ema_indicator()
    ema26 = ta.trend.EMAIndicator(close, window=26).ema_indicator()

    macd_obj  = ta.trend.MACD(close)
    macd      = macd_obj.macd()
    macd_sig  = macd_obj.macd_signal()
    macd_hist = macd_obj.macd_diff()

    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()

    bb     = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_up  = bb.bollinger_hband()
    bb_lo  = bb.bollinger_lband()
    bb_mid = bb.bollinger_mavg()
    bb_pct = bb.bollinger_pband()

    atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

    stoch_k = ta.momentum.StochasticOscillator(high, low, close, window=14).stoch()

    vol_sma20 = ta.trend.SMAIndicator(vol, window=20).sma_indicator()

    price = float(close.iloc[-1])
    s20   = _safe(sma20)
    s50   = _safe(sma50)

    return {
        "current_price":    round(price, 2),
        "high_52w":         round(float(high.max()), 2),
        "low_52w":          round(float(low.min()), 2),
        "sma20":            s20,
        "sma50":            s50,
        "ema12":            _safe(ema12),
        "ema26":            _safe(ema26),
        "macd":             _safe(macd),
        "macd_signal":      _safe(macd_sig),
        "macd_histogram":   _safe(macd_hist),
        "rsi":              _safe(rsi),
        "bb_upper":         _safe(bb_up),
        "bb_lower":         _safe(bb_lo),
        "bb_middle":        _safe(bb_mid),
        "bb_pct_b":         _safe(bb_pct),
        "atr":              _safe(atr),
        "stoch_k":          _safe(stoch_k),
        "vol_current":      int(vol.iloc[-1]),
        "vol_avg20":        int(_safe(vol_sma20) or 0),
        "price_vs_sma20":   round(((price / s20) - 1) * 100, 2) if s20 else None,
        "price_vs_sma50":   round(((price / s50) - 1) * 100, 2) if s50 else None,
        "candle_count":     len(df),
    }


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(symbol: str, name: str, ind: dict, candles: list[dict]) -> str:
    price  = ind.get("current_price", 0)
    h52    = ind.get("high_52w", 0)
    l52    = ind.get("low_52w", 0)
    sma20  = ind.get("sma20")
    sma50  = ind.get("sma50")
    rsi    = ind.get("rsi")
    macd   = ind.get("macd")
    msig   = ind.get("macd_signal")
    mhist  = ind.get("macd_histogram")
    bb_up  = ind.get("bb_upper")
    bb_lo  = ind.get("bb_lower")
    bb_mid = ind.get("bb_middle")
    bb_pct = ind.get("bb_pct_b")
    atr    = ind.get("atr")
    stoch  = ind.get("stoch_k")
    vc     = ind.get("vol_current", 0)
    va     = ind.get("vol_avg20", 0)
    p20    = ind.get("price_vs_sma20")
    p50    = ind.get("price_vs_sma50")

    pos52 = round(((price - l52) / (h52 - l52)) * 100, 1) if h52 != l52 else 50.0

    rsi_label = (
        "OVERBOUGHT — likely reversal risk" if (rsi or 50) > 70
        else "OVERSOLD — potential bounce" if (rsi or 50) < 30
        else "moderately bullish" if (rsi or 50) > 55
        else "moderately bearish" if (rsi or 50) < 45
        else "neutral"
    )
    cross = (
        "GOLDEN CROSS (SMA20 > SMA50, bullish structure)"
        if sma20 and sma50 and sma20 > sma50
        else "DEATH CROSS (SMA20 < SMA50, bearish structure)"
    )
    macd_label = (
        "BULLISH — histogram positive, buyers in control"
        if (mhist or 0) > 0
        else "BEARISH — histogram negative, sellers in control"
    )
    vol_label = "average"
    if va > 0:
        r = vc / va
        vol_label = (
            f"HIGH ({r:.1f}x avg) — strong conviction"
            if r > 1.5
            else f"LOW ({r:.1f}x avg) — weak participation"
            if r < 0.5
            else f"normal ({r:.1f}x avg)"
        )

    recent = candles[-15:]
    tbl = "Date       | Open    | High    | Low     | Close   | Volume\n"
    tbl += "-" * 72 + "\n"
    for c in recent:
        tbl += (
            f"{c['timestamp']} | {c['open']:>7.2f} | {c['high']:>7.2f} | "
            f"{c['low']:>7.2f} | {c['close']:>7.2f} | {c['volume']:>10,}\n"
        )

    return f"""You are an expert stock market analyst specializing in Indian NSE/BSE equities.

Analyze {symbol} ({name}) — NSE-listed Indian stock. All prices are in Indian Rupees (₹).

===== TECHNICAL INDICATORS (1-year daily data, {ind.get('candle_count', '?')} candles) =====

PRICE:
  Current: ₹{price}  |  52W High: ₹{h52}  |  52W Low: ₹{l52}
  Position in 52W range: {pos52:.1f}%

MOVING AVERAGES:
  SMA(20): ₹{sma20}  — price is {f"{p20:+.1f}%" if p20 is not None else "N/A"} vs SMA20
  SMA(50): ₹{sma50}  — price is {f"{p50:+.1f}%" if p50 is not None else "N/A"} vs SMA50
  EMA(12): ₹{ind.get('ema12')}  |  EMA(26): ₹{ind.get('ema26')}
  Cross: {cross}

MOMENTUM:
  RSI(14): {rsi} — {rsi_label}
  MACD: {macd}  |  Signal: {msig}  |  Histogram: {mhist}  → {macd_label}
  Stochastic %K: {stoch}

VOLATILITY:
  Bollinger Upper: ₹{bb_up}  |  Middle: ₹{bb_mid}  |  Lower: ₹{bb_lo}
  %B (band position): {bb_pct}  (0 = at lower, 1 = at upper band)
  ATR(14): ₹{atr}

VOLUME:
  Today: {vc:,}  |  20-Day Avg: {va:,}  →  {vol_label}

===== RECENT PRICE ACTION (last 15 trading days) =====
{tbl}

===== PROVIDE YOUR ANALYSIS =====

Use EXACTLY these section headers and be specific with ₹ price levels:

## 1. OVERALL TREND
State: Strong Bullish / Bullish / Neutral / Bearish / Strong Bearish — and explain with evidence from MAs and price structure.

## 2. TECHNICAL SIGNALS
Interpret RSI, MACD, Bollinger Bands, and Stochastic together. Do they confirm or diverge?

## 3. KEY PRICE LEVELS
- Support 1: ₹X (why)
- Support 2: ₹X (why)
- Resistance 1: ₹X (why)
- Resistance 2: ₹X (why)
- Key pivot: ₹X

## 4. SHORT-TERM OUTLOOK (1 Week)
- Expected range: ₹X – ₹Y
- Most likely path
- What would invalidate this view

## 5. MEDIUM-TERM OUTLOOK (1 Month)
- Expected range: ₹X – ₹Y
- Catalyst needed
- Main risk

## 6. RISK ASSESSMENT
Risk level: HIGH / MEDIUM / LOW
Three specific risks for this stock.

## 7. TRADING RECOMMENDATION
Action: STRONG BUY / BUY / HOLD / SELL / STRONG SELL
- Entry zone: ₹X – ₹Y
- Stop loss: ₹X
- Target 1: ₹X  |  Target 2: ₹Y
- Risk-reward ratio: X:1

Keep price targets grounded in the technical levels you identified above."""


# ── Ollama caller ──────────────────────────────────────────────────────────────

async def _call_ollama(prompt: str, model: str) -> str:
    try:
        client = ollama_lib.AsyncClient(host=settings.LLM_API_URL)
        response = await client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 2000},
        )
        # ollama lib ≥0.3 returns a ChatResponse object; older builds return a plain dict
        if hasattr(response, "message"):
            msg = response.message
            return msg.content if hasattr(msg, "content") else str(msg)
        if isinstance(response, dict):
            msg = response.get("message", {})
            return msg.get("content", "") if isinstance(msg, dict) else str(msg)
        return str(response)
    except Exception as e:
        logger.error(
            "Ollama LLM unavailable in agent",
            extra={"log_type": "agent_event", "event": "llm_error", "error": str(e)},
        )
        raise HTTPException(
            status_code=503,
            detail=(
                f"Ollama LLM unavailable — {e}. "
                f"Run: ollama pull {model}"
            ),
        )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/stocks")
async def get_agent_stocks():
    """All stocks available for AI analysis, with portfolio membership flagged."""
    groww = get_groww_client()
    portfolio_syms: set[str] = set()

    if groww:
        try:
            logger.info(
                "Calling Groww get_holdings",
                extra={"log_type": "groww_call", "caller": "agent.get_agent_stocks", "method": "get_holdings"},
            )
            holdings = await groww.get_holdings()
            for h in holdings:
                sym = h.get("trading_symbol", h.get("symbol", ""))
                if sym:
                    portfolio_syms.add(sym)
        except Exception:
            pass

    stocks = [
        {"symbol": sym, "name": name, "in_portfolio": sym in portfolio_syms}
        for sym, name in KNOWN_STOCKS.items()
    ]
    stocks.sort(key=lambda s: (0 if s["in_portfolio"] else 1, s["symbol"]))
    return {"status": "success", "data": stocks}


@router.get("/models")
async def get_ollama_models():
    """List Ollama models installed on the host machine."""
    try:
        client = ollama_lib.AsyncClient(host=settings.LLM_API_URL)
        resp = await client.list()
        # resp is a ListResponse object in newer ollama lib, dict in older
        if hasattr(resp, "models"):
            models = [m.model if hasattr(m, "model") else m.get("model", "") for m in (resp.models or [])]
        else:
            models = [m.get("model", "") for m in (resp.get("models") or [])]
        return {"status": "success", "data": models, "current": settings.LLM_MODEL}
    except Exception as e:
        return {
            "status": "error",
            "data": [],
            "error": str(e),
            "current": settings.LLM_MODEL,
            "hint": f"Make sure Ollama is running. LLM_API_URL={settings.LLM_API_URL}",
        }


@router.post("/analyze/{symbol}")
async def analyze_stock(
    symbol: str,
    model: Optional[str] = Query(None, description="Override Ollama model"),
):
    """
    Full AI analysis pipeline:
      1. Fetch 1 year of daily candles from Groww (simulation fallback)
      2. Compute RSI, MACD, SMA, Bollinger Bands, ATR, Stochastic via `ta`
      3. Build a structured prompt and call the local Ollama LLM
      4. Return indicators + raw candles + AI analysis text
    """
    symbol = symbol.upper()
    name   = KNOWN_STOCKS.get(symbol, symbol)
    llm_model = model or settings.LLM_MODEL

    # Step 1: fetch candles
    candles: list[dict] = []
    data_source = "simulated"

    groww = get_groww_client()
    if groww:
        try:
            end   = datetime.now()
            start = end - timedelta(days=365)
            logger.info(
                "Calling Groww get_historical for AI analysis",
                extra={"log_type": "groww_call", "caller": "agent.analyze_stock", "method": "get_historical", "symbol": symbol, "interval_minutes": 1440, "days": 365},
            )
            raw   = await groww.get_historical(symbol, 1440, start, end)
            if raw and len(raw) > 30:
                candles = _parse_candles(raw)
                data_source = "groww"
                logger.info(
                    "Fetched daily candles from Groww",
                    extra={"log_type": "agent_event", "event": "candles_fetched", "symbol": symbol, "count": len(candles)},
                )
        except Exception as exc:
            logger.warning(
                "Groww candles failed, using simulation",
                extra={"log_type": "agent_event", "event": "candles_fallback", "symbol": symbol, "error": str(exc)},
            )

    if not candles:
        candles = _simulate_candles(symbol, days=365)

    if len(candles) < 20:
        raise HTTPException(status_code=400, detail=f"Insufficient candle data for {symbol}")

    # Step 2: compute indicators
    indicators = _compute_indicators(candles)

    # Step 3: call LLM
    prompt   = _build_prompt(symbol, name, indicators, candles)
    analysis = await _call_ollama(prompt, llm_model)

    return {
        "status": "success",
        "data": {
            "symbol":        symbol,
            "name":          name,
            "data_source":   data_source,
            "candle_count":  len(candles),
            "indicators":    indicators,
            "recent_candles": candles[-20:],
            "analysis":      analysis,
            "model_used":    llm_model,
            "generated_at":  datetime.now().isoformat(),
        },
    }
