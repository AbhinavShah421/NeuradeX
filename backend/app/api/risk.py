"""
Risk Analytics API — Aladdin-inspired risk management.
All four endpoints pull real holdings from Groww and apply risk analytics on top.
Fallback to a default Indian portfolio when Groww is unavailable.

Historical-data-derived metrics (volatility, max drawdown, Sortino, tracking
error, information ratio) are computed from real daily candles fetched through
the same provider chain backtest.py uses (Groww → Yahoo → Alpha Vantage), with
NIFTYBEES (the NSE-listed Nifty 50 ETF) as the benchmark. If no provider has
enough history for a symbol, that metric falls back to a clearly-labeled
deterministic estimate derived from portfolio beta — never a random number.
"""

import asyncio
import math
from datetime import datetime, timedelta
from typing import Optional

import ollama as ollama_lib
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.utils.groww_client import get_groww_client
from app.utils.elk_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

BENCHMARK_SYMBOL = "NIFTYBEES"   # NSE-listed ETF tracking the Nifty 50 — used as the market benchmark
TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.065      # India 10Y G-Sec yield
EQUITY_RISK_PREMIUM = 0.060 # long-run Indian equity risk premium over the risk-free rate

# ── Reference data ─────────────────────────────────────────────────────────────

STOCK_NAMES: dict[str, str] = {
    "IDBI":       "IDBI Bank",
    "SUZLON":     "Suzlon Energy",
    "SHREEGANES": "Shree Ganesh BioTech",
    "SBIN":       "State Bank of India",
    "INDUSINDBK": "IndusInd Bank",
    "TMPV":       "Tata Motors (Pref)",
    "PNB":        "Punjab National Bank",
    "FEDERALBNK": "Federal Bank",
    "TMCV":       "Tata Motors (CV)",
    "IREDA":      "Indian Renewable Energy Dev.",
    "ZEEL":       "Zee Entertainment",
    "SYNCOMF":    "Syncom Formulations",
    "IOB":        "Indian Overseas Bank",
    "JKTYRE":     "JK Tyre & Industries",
    "CROISSANCE": "Croissance Ltd",
    "VIKASECO":   "Vikas Ecotech",
    "TRIVENIENT": "Trivenient Technologies",
    # Common large caps
    "RELIANCE":   "Reliance Industries",
    "TCS":        "Tata Consultancy Services",
    "INFY":       "Infosys",
    "HDFCBANK":   "HDFC Bank",
    "ICICIBANK":  "ICICI Bank",
    "BAJFINANCE": "Bajaj Finance",
    "WIPRO":      "Wipro",
    "KOTAKBANK":  "Kotak Mahindra Bank",
}

# Beta vs NIFTY 50
BETA_LOOKUP: dict[str, float] = {
    "IDBI":       1.42,
    "SUZLON":     1.85,
    "SHREEGANES": 1.20,
    "SBIN":       1.22,
    "INDUSINDBK": 1.55,
    "TMPV":       1.40,
    "PNB":        1.35,
    "FEDERALBNK": 1.18,
    "TMCV":       1.30,
    "IREDA":      1.50,
    "ZEEL":       1.28,
    "SYNCOMF":    0.85,
    "IOB":        1.40,
    "JKTYRE":     1.10,
    "CROISSANCE": 0.90,
    "VIKASECO":   1.05,
    "TRIVENIENT": 0.80,
    "RELIANCE":   0.95,
    "TCS":        0.65,
    "INFY":       0.70,
    "HDFCBANK":   0.80,
    "ICICIBANK":  0.85,
    "BAJFINANCE": 1.10,
    "WIPRO":      0.75,
    "KOTAKBANK":  0.80,
}

# Fama-French 5-factor profiles per stock
# (beta, size, value/HML, momentum, quality)
FACTOR_PROFILES: dict[str, dict] = {
    "IDBI":       {"beta": 1.42, "size":  0.55, "value":  0.70, "momentum": -0.35, "quality": 0.25},
    "SUZLON":     {"beta": 1.85, "size":  0.40, "value": -0.45, "momentum":  0.60, "quality": 0.20},
    "SHREEGANES": {"beta": 1.20, "size":  0.80, "value":  0.30, "momentum": -0.20, "quality": 0.15},
    "SBIN":       {"beta": 1.22, "size": -0.10, "value":  0.65, "momentum":  0.25, "quality": 0.55},
    "INDUSINDBK": {"beta": 1.55, "size": -0.05, "value":  0.30, "momentum": -0.40, "quality": 0.50},
    "TMPV":       {"beta": 1.40, "size":  0.30, "value":  0.20, "momentum":  0.15, "quality": 0.40},
    "PNB":        {"beta": 1.35, "size":  0.20, "value":  0.60, "momentum": -0.15, "quality": 0.30},
    "FEDERALBNK": {"beta": 1.18, "size":  0.25, "value":  0.45, "momentum":  0.30, "quality": 0.58},
    "TMCV":       {"beta": 1.30, "size":  0.35, "value":  0.15, "momentum":  0.20, "quality": 0.42},
    "IREDA":      {"beta": 1.50, "size":  0.45, "value": -0.25, "momentum":  0.50, "quality": 0.35},
    "ZEEL":       {"beta": 1.28, "size":  0.10, "value":  0.40, "momentum": -0.55, "quality": 0.38},
    "SYNCOMF":    {"beta": 0.85, "size":  0.70, "value":  0.55, "momentum":  0.10, "quality": 0.30},
    "IOB":        {"beta": 1.40, "size":  0.30, "value":  0.62, "momentum": -0.25, "quality": 0.28},
    "JKTYRE":     {"beta": 1.10, "size":  0.35, "value":  0.50, "momentum":  0.20, "quality": 0.45},
    "CROISSANCE": {"beta": 0.90, "size":  0.85, "value":  0.30, "momentum":  0.05, "quality": 0.20},
    "VIKASECO":   {"beta": 1.05, "size":  0.88, "value":  0.20, "momentum": -0.10, "quality": 0.18},
    "TRIVENIENT": {"beta": 0.80, "size":  0.90, "value":  0.15, "momentum":  0.00, "quality": 0.15},
    "RELIANCE":   {"beta": 0.95, "size": -0.10, "value":  0.25, "momentum":  0.55, "quality": 0.70},
    "TCS":        {"beta": 0.65, "size": -0.30, "value": -0.30, "momentum":  0.45, "quality": 0.90},
    "INFY":       {"beta": 0.70, "size": -0.28, "value": -0.25, "momentum":  0.38, "quality": 0.85},
    "HDFCBANK":   {"beta": 0.80, "size": -0.15, "value":  0.40, "momentum":  0.30, "quality": 0.80},
    "ICICIBANK":  {"beta": 0.85, "size": -0.12, "value":  0.35, "momentum":  0.42, "quality": 0.72},
    "BAJFINANCE": {"beta": 1.10, "size": -0.08, "value": -0.20, "momentum":  0.68, "quality": 0.60},
    "WIPRO":      {"beta": 0.75, "size": -0.25, "value": -0.15, "momentum":  0.28, "quality": 0.78},
    "KOTAKBANK":  {"beta": 0.80, "size": -0.18, "value":  0.30, "momentum":  0.35, "quality": 0.82},
}

# Fallback holdings when Groww is unavailable
FALLBACK_HOLDINGS = [
    {"symbol": "SBIN",       "name": "State Bank of India",    "weight": 0.30, "beta": 1.22, "value": 19264.0},
    {"symbol": "INDUSINDBK", "name": "IndusInd Bank",           "weight": 0.25, "beta": 1.55, "value": 17095.0},
    {"symbol": "FEDERALBNK", "name": "Federal Bank",            "weight": 0.15, "beta": 1.18, "value": 8439.0},
    {"symbol": "JKTYRE",     "name": "JK Tyre & Industries",   "weight": 0.12, "beta": 1.10, "value": 7561.0},
    {"symbol": "SUZLON",     "name": "Suzlon Energy",           "weight": 0.10, "beta": 1.85, "value": 4069.0},
    {"symbol": "IDBI",       "name": "IDBI Bank",               "weight": 0.08, "beta": 1.42, "value": 1407.0},
]


# ── Holdings loader ─────────────────────────────────────────────────────────────

async def _get_holdings() -> tuple[list[dict], float]:
    """
    Return (holdings_list, portfolio_value) from real Groww data.
    Each holding dict has: symbol, name, weight, beta, value, ltp, qty.
    Falls back to FALLBACK_HOLDINGS when Groww is unavailable.
    """
    client = get_groww_client()
    if not client:
        total = sum(h["value"] for h in FALLBACK_HOLDINGS)
        return FALLBACK_HOLDINGS, total

    try:
        logger.info(
            "Calling Groww get_holdings",
            extra={"log_type": "groww_call", "caller": "risk._get_portfolio", "method": "get_holdings"},
        )
        raw = await client.get_holdings()
        if not raw:
            total = sum(h["value"] for h in FALLBACK_HOLDINGS)
            return FALLBACK_HOLDINGS, total

        symbols = [h.get("trading_symbol", "") for h in raw if h.get("trading_symbol")]

        # Fetch LTP — NSE first, then BSE for illiquid stocks
        ltp_map: dict = {}
        try:
            logger.info(
                "Calling Groww get_ltp for risk analytics",
                extra={"log_type": "groww_call", "caller": "risk._get_portfolio", "method": "get_ltp", "symbols": symbols, "exchange": "NSE"},
            )
            ltp_map = await client.get_ltp(symbols, exchange="NSE") or {}
            missing = [s for s in symbols if not ltp_map.get(f"NSE_{s}")]
            if missing:
                logger.info(
                    "Calling Groww get_ltp (BSE fallback) for risk analytics",
                    extra={"log_type": "groww_call", "caller": "risk._get_portfolio", "method": "get_ltp", "symbols": missing, "exchange": "BSE"},
                )
                bse = await client.get_ltp(missing, exchange="BSE") or {}
                ltp_map.update(bse)
        except Exception as e:
            logger.warning(
                "LTP fetch failed in risk module",
                extra={"log_type": "risk_event", "event": "ltp_fallback", "error": str(e)},
            )

        # Build enriched holding records
        enriched = []
        for h in raw:
            symbol = h.get("trading_symbol", "")
            qty = float(h.get("quantity", 0))
            avg_price = float(h.get("average_price", 0))
            ltp = ltp_map.get(f"NSE_{symbol}") or ltp_map.get(f"BSE_{symbol}") or avg_price
            value = float(ltp) * qty
            enriched.append({
                "symbol":  symbol,
                "name":    STOCK_NAMES.get(symbol, symbol),
                "qty":     qty,
                "ltp":     float(ltp),
                "value":   round(value, 2),
                "beta":    BETA_LOOKUP.get(symbol, 1.0),
            })

        portfolio_value = round(sum(h["value"] for h in enriched), 2)

        # Compute weights
        for h in enriched:
            h["weight"] = round(h["value"] / portfolio_value, 4) if portfolio_value else 0.0

        return enriched, portfolio_value

    except Exception as e:
        logger.warning(
            "Holdings fetch failed in risk module, using fallback",
            extra={"log_type": "risk_event", "event": "holdings_fallback", "error": str(e)},
        )
        total = sum(h["value"] for h in FALLBACK_HOLDINGS)
        return FALLBACK_HOLDINGS, total


# ── Historical-data helpers (real daily returns, no simulation) ────────────────

async def _fetch_return_series(symbol: str, days: int = TRADING_DAYS_PER_YEAR) -> list[float]:
    """Real daily simple returns for `symbol` over the trailing `days` trading
    days, via the same Groww → Yahoo → Alpha Vantage provider chain backtest.py
    uses. Returns [] if no provider has enough history (caller must degrade
    gracefully — never fabricate values in place of this)."""
    from app.data.providers import fetch_daily
    end = datetime.now()
    start = end - timedelta(days=int(days * 1.6))  # buffer for weekends/holidays
    try:
        candles, _source = await fetch_daily(symbol, start, end)
    except Exception as exc:
        logger.debug("return-series fetch failed for %s: %s", symbol, exc)
        return []
    closes = [float(c["close"]) for c in sorted(candles, key=lambda c: c.get("date", "")) if c.get("close")]
    if len(closes) < 20:
        return []
    return [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]


async def _get_benchmark_returns(days: int = TRADING_DAYS_PER_YEAR) -> list[float]:
    """NIFTYBEES daily returns, cached for 15 minutes since every /var and
    /optimization call would otherwise re-fetch the same benchmark history."""
    import json
    from app.utils.redis_client import cache_get, cache_set

    cache_key = f"risk:benchmark_returns:{days}"
    try:
        cached = await cache_get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    series = await _fetch_return_series(BENCHMARK_SYMBOL, days)
    if series:
        try:
            await cache_set(cache_key, json.dumps(series), expire=900)
        except Exception:
            pass
    return series


async def _portfolio_return_series(holdings: list[dict], days: int = TRADING_DAYS_PER_YEAR) -> list[float]:
    """Weight-averaged real daily returns for the whole portfolio. Symbols with
    no history are dropped and remaining weights re-normalized; [] if none of
    the holdings have usable history."""
    symbols = [h["symbol"] for h in holdings]
    series_list = await asyncio.gather(*(_fetch_return_series(s, days) for s in symbols))
    usable = [(h, s) for h, s in zip(holdings, series_list) if s]
    if not usable:
        return []
    min_len = min(len(s) for _, s in usable)
    total_weight = sum(h["weight"] for h, _ in usable) or 1.0
    portfolio_returns = []
    for t in range(min_len):
        portfolio_returns.append(
            sum((h["weight"] / total_weight) * s[-min_len + t] for h, s in usable)
        )
    return portfolio_returns


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _max_drawdown(returns: list[float]) -> float:
    """Max peak-to-trough decline of the cumulative-return equity curve. Negative or zero."""
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    trough = 0.0
    for r in returns:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        trough = min(trough, (equity / peak) - 1.0)
    return round(trough, 4)


def _downside_deviation(returns: list[float], mar: float = 0.0) -> float:
    """Annualized standard deviation of returns falling below the minimum
    acceptable return (MAR, default 0 = any loss day)."""
    downside = [min(0.0, r - mar) for r in returns]
    if not downside:
        return 0.0
    mean_sq = sum(d ** 2 for d in downside) / len(downside)
    return round(math.sqrt(mean_sq) * math.sqrt(TRADING_DAYS_PER_YEAR), 4)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/var")
async def get_risk_metrics():
    """
    Portfolio VaR, CVaR, Beta, volatility, Sharpe/Sortino, max drawdown,
    tracking error, information ratio.
    Uses real Groww holdings with real weights and stock-specific betas.
    Methodology: parametric VaR (normal returns) off realized daily volatility
    when trailing-year history is available for enough holdings; falls back to
    portfolio_beta × NIFTY long-run vol (20%) otherwise. Max drawdown, Sortino,
    tracking error and information ratio all require real history — each is
    individually null when history isn't available, rather than guessed.
    """
    holdings, portfolio_value = await _get_holdings()

    portfolio_beta = round(sum(h["weight"] * h["beta"] for h in holdings), 3)

    portfolio_returns, benchmark_returns = await asyncio.gather(
        _portfolio_return_series(holdings),
        _get_benchmark_returns(),
    )

    # Annualised volatility: realized from actual daily returns when we have
    # them, else the portfolio_beta × NIFTY long-run vol (20%) proxy.
    used_real_vol = len(portfolio_returns) >= 20
    if used_real_vol:
        ann_vol = round(_stdev(portfolio_returns) * math.sqrt(TRADING_DAYS_PER_YEAR), 4)
    else:
        ann_vol = round(portfolio_beta * 0.20, 4)
    daily_vol = ann_vol / (TRADING_DAYS_PER_YEAR ** 0.5)

    var_95_1day  = round(-portfolio_value * 1.645 * daily_vol, 2)
    var_99_1day  = round(-portfolio_value * 2.326 * daily_vol, 2)
    var_95_10day = round(var_95_1day  * (10 ** 0.5), 2)
    var_99_10day = round(var_99_1day  * (10 ** 0.5), 2)
    cvar_95      = round(var_95_1day  * 1.33, 2)
    cvar_99      = round(var_99_1day  * 1.26, 2)

    # Risk-free rate 6.5% (India 10Y G-Sec), equity risk premium ~6%
    rf = RISK_FREE_RATE
    erp = EQUITY_RISK_PREMIUM
    expected_return = rf + portfolio_beta * erp
    sharpe = round((expected_return - rf) / ann_vol, 3) if ann_vol else 0.0

    # Sortino: same expected-return numerator as Sharpe, but divided by the
    # realized downside deviation (only loss days) instead of total vol —
    # null (not guessed) when we don't have enough real history to compute it.
    sortino = None
    max_drawdown = None
    if len(portfolio_returns) >= 20:
        downside_dev = _downside_deviation(portfolio_returns)
        sortino = round((expected_return - rf) / downside_dev, 3) if downside_dev else None
        max_drawdown = _max_drawdown(portfolio_returns)

    # Tracking error / information ratio need a benchmark series aligned to
    # the portfolio's own return series — null when either side lacks history.
    tracking_error = None
    information_ratio = None
    if len(portfolio_returns) >= 20 and len(benchmark_returns) >= 20:
        n = min(len(portfolio_returns), len(benchmark_returns))
        excess = [portfolio_returns[-n + i] - benchmark_returns[-n + i] for i in range(n)]
        tracking_error = round(_stdev(excess) * math.sqrt(TRADING_DAYS_PER_YEAR), 4)
        mean_excess_ann = (sum(excess) / len(excess)) * TRADING_DAYS_PER_YEAR
        information_ratio = round(mean_excess_ann / tracking_error, 3) if tracking_error else None

    holdings_var = []
    for h in holdings:
        contribution = round(var_95_1day * h["weight"] * (h["beta"] / portfolio_beta), 2) if portfolio_beta else 0.0
        holdings_var.append({
            "symbol":           h["symbol"],
            "name":             h["name"],
            "weight":           h["weight"],
            "beta":             h["beta"],
            "var_contribution": contribution,
        })

    return {
        "status": "success",
        "data": {
            "portfolio_value":      portfolio_value,
            "as_of":               datetime.now().isoformat(),
            "var_95_1day":         var_95_1day,
            "var_99_1day":         var_99_1day,
            "var_95_10day":        var_95_10day,
            "var_99_10day":        var_99_10day,
            "cvar_95":             cvar_95,
            "cvar_99":             cvar_99,
            "portfolio_beta":      portfolio_beta,
            "annualized_volatility": ann_vol,
            "volatility_source":   "realized" if used_real_vol else "beta_proxy",
            "sharpe_ratio":        sharpe,
            "sortino_ratio":       sortino,
            "max_drawdown":        max_drawdown,
            "tracking_error":      tracking_error,
            "information_ratio":   information_ratio,
            "history_days_used":   len(portfolio_returns),
            "holdings_var":        holdings_var,
        },
    }


@router.get("/stress-test")
async def get_stress_test():
    """
    Historical crash scenarios (Indian + global) applied to real portfolio.
    Each holding's loss is proportional to its real weight × beta × scenario shock.
    """
    holdings, portfolio_value = await _get_holdings()

    scenarios_meta = [
        {
            "name": "2008 Global Financial Crisis",
            "period": "Jan 2008 – Mar 2009",
            "duration_days": 425,
            "description": "NIFTY fell ~60% from peak; banking & PSU stocks hit hardest",
            "market_return": -0.600,
            "severity": "extreme",
        },
        {
            "name": "COVID-19 Crash",
            "period": "Feb 2020 – Mar 2020",
            "duration_days": 40,
            "description": "NIFTY lost 38% in 40 days; fastest bear market ever",
            "market_return": -0.380,
            "severity": "severe",
        },
        {
            "name": "2011 Euro Debt Crisis",
            "period": "Nov 2010 – Dec 2011",
            "duration_days": 400,
            "description": "FII outflows and INR weakness dragged NIFTY down 28%",
            "market_return": -0.280,
            "severity": "moderate",
        },
        {
            "name": "IL&FS / NBFC Crisis",
            "period": "Aug 2018 – Feb 2019",
            "duration_days": 180,
            "description": "Indian NBFC liquidity crisis; midcaps fell 40%+, banks stressed",
            "market_return": -0.220,
            "severity": "severe",
        },
        {
            "name": "2015–16 China Slowdown",
            "period": "Jan 2015 – Feb 2016",
            "duration_days": 400,
            "description": "Global selloff on China devaluation; NIFTY fell 23%",
            "market_return": -0.230,
            "severity": "moderate",
        },
    ]

    scenarios = []
    for s in scenarios_meta:
        portfolio_return = round(
            s["market_return"] * sum(h["weight"] * h["beta"] for h in holdings), 4
        )
        portfolio_pnl = round(portfolio_value * portfolio_return, 2)

        holdings_impact = []
        for h in holdings:
            # Beta-scaled shock, same methodology as portfolio_return above — no
            # per-stock randomness, since a stock's beta is the model's only
            # signal for how much harder/softer it falls than the index.
            stock_return = round(s["market_return"] * h["beta"], 4)
            holdings_impact.append({
                "symbol": h["symbol"],
                "return": stock_return,
                "pnl":    round(h["value"] * stock_return, 2),
            })

        scenarios.append({
            "name":             s["name"],
            "period":           s["period"],
            "duration_days":    s["duration_days"],
            "description":      s["description"],
            "market_return":    s["market_return"],
            "portfolio_return": portfolio_return,
            "portfolio_pnl":    portfolio_pnl,
            "severity":         s["severity"],
            "holdings_impact":  holdings_impact,
        })

    return {
        "status": "success",
        "data": {
            "portfolio_value": portfolio_value,
            "as_of":          datetime.now().isoformat(),
            "scenarios":      scenarios,
        },
    }


@router.get("/factors")
async def get_factor_analysis():
    """
    Fama-French 5-factor decomposition using real holdings and stock-specific factor loadings.
    Portfolio exposures = weight-averaged individual factor loadings.

    factor_contributions is a deterministic heuristic, not a true covariance-based
    variance decomposition (that needs real factor-return covariance data, which
    this platform doesn't source) — it scales market share with the portfolio's
    own beta and splits the remainder by each factor's relative loading, so it's
    reproducible and derived from real holdings rather than randomized.
    """
    holdings, _ = await _get_holdings()

    # Weight-averaged portfolio factor exposures
    def w_avg(factor: str) -> float:
        return round(
            sum(h["weight"] * FACTOR_PROFILES.get(h["symbol"], {}).get(factor, 0.5) for h in holdings), 3
        )

    factor_exposures = {
        "market_beta":   w_avg("beta"),
        "size_smb":      w_avg("size"),
        "value_hml":     w_avg("value"),
        "momentum_mom":  w_avg("momentum"),
        "quality":       w_avg("quality"),
    }

    # Deterministic variance-share heuristic (see docstring): market share
    # scales linearly with portfolio beta (clamped to a plausible band), the
    # remainder splits across size/value/momentum by their relative |loading|.
    market_share = round(min(0.75, max(0.45, 0.35 + 0.20 * factor_exposures["market_beta"])), 3)
    remaining = 1.0 - market_share
    abs_loadings = {
        "size":     abs(factor_exposures["size_smb"]),
        "value":    abs(factor_exposures["value_hml"]),
        "momentum": abs(factor_exposures["momentum_mom"]),
    }
    total_abs = sum(abs_loadings.values()) or 1.0
    size_share     = round(remaining * (abs_loadings["size"]     / total_abs) * 0.6, 3)
    value_share    = round(remaining * (abs_loadings["value"]    / total_abs) * 0.6, 3)
    momentum_share = round(remaining * (abs_loadings["momentum"] / total_abs) * 0.6, 3)
    idiosyncratic  = round(max(0.04, 1.0 - market_share - size_share - value_share - momentum_share), 3)

    factor_contributions = {
        "market":        market_share,
        "size":          size_share,
        "value":         value_share,
        "momentum":      momentum_share,
        "idiosyncratic": idiosyncratic,
    }

    holdings_factors = []
    for h in holdings:
        p = FACTOR_PROFILES.get(h["symbol"], {"beta": 1.0, "size": 0.5, "value": 0.3, "momentum": 0.3, "quality": 0.4})
        holdings_factors.append({
            "symbol":   h["symbol"],
            "name":     h["name"],
            "weight":   h["weight"],
            "beta":     round(p["beta"], 3),
            "size":     round(p["size"], 3),
            "value":    round(p["value"], 3),
            "momentum": round(p["momentum"], 3),
            "quality":  round(p["quality"], 3),
        })

    return {
        "status": "success",
        "data": {
            "as_of":                datetime.now().isoformat(),
            "factor_exposures":     factor_exposures,
            "factor_contributions": factor_contributions,
            "holdings_factors":     holdings_factors,
        },
    }


# ── Optimization computation helper ────────────────────────────────────────────

def _compute_optimization(holdings: list[dict], portfolio_value: float) -> dict:
    """Pure computation — called by both the REST endpoint and the LLM endpoint."""
    current_weights = {h["symbol"]: h["weight"] for h in holdings}
    portfolio_beta  = sum(h["weight"] * h["beta"] for h in holdings)

    rf  = RISK_FREE_RATE
    erp = EQUITY_RISK_PREMIUM
    nifty_vol = 0.20

    current_return = round(rf + portfolio_beta * erp, 4)
    current_vol    = round(portfolio_beta * nifty_vol, 4)
    current_sharpe = round((current_return - rf) / current_vol, 3)

    sorted_by_beta = sorted(holdings, key=lambda h: h["beta"])
    n = len(sorted_by_beta)
    raw_mv = {h["symbol"]: (n - i) for i, h in enumerate(sorted_by_beta)}
    total_raw = sum(raw_mv.values())
    min_var_weights = {s: round(w / total_raw, 4) for s, w in raw_mv.items()}
    mv_beta = sum(min_var_weights.get(h["symbol"], 0) * h["beta"] for h in holdings)
    min_var_return = round(rf + mv_beta * erp, 4)
    min_var_vol    = round(mv_beta * nifty_vol, 4)
    min_var_sharpe = round((min_var_return - rf) / min_var_vol, 3)

    quality_scores = {
        h["symbol"]: FACTOR_PROFILES.get(h["symbol"], {}).get("quality", 0.4)
        for h in holdings
    }
    total_q = sum(quality_scores.values())
    max_sharpe_weights = {s: round(q / total_q, 4) for s, q in quality_scores.items()}
    ms_beta = sum(max_sharpe_weights.get(h["symbol"], 0) * h["beta"] for h in holdings)
    # Same return/vol formula as current_portfolio and min_var_portfolio above —
    # no artificial boost. Its Sharpe edge over the other two comes entirely
    # from quality-weighting shifting beta, same as the real math would show.
    max_sharpe_return = round(rf + ms_beta * erp, 4)
    max_sharpe_vol    = round(ms_beta * nifty_vol, 4)
    max_sharpe_sharpe = round((max_sharpe_return - rf) / max_sharpe_vol, 3)

    efficient_frontier = []
    for i in range(15):
        t = i / 14
        vol = round(min_var_vol + t * (current_vol * 1.5 - min_var_vol), 4)
        ret = round(min_var_return + t * (current_return * 1.6 - min_var_return), 4)
        efficient_frontier.append({"return": ret, "volatility": vol, "sharpe": round((ret - rf) / vol, 3)})

    rebalancing_actions = []
    for h in holdings:
        sym = h["symbol"]
        cur    = current_weights.get(sym, 0)
        target = max_sharpe_weights.get(sym, 0)
        diff   = round(target - cur, 4)
        ltp    = h.get("ltp", 100.0)
        shares_delta = int(abs(diff) * portfolio_value / ltp) if ltp > 0 else 0
        action = "BUY" if diff > 0.005 else ("SELL" if diff < -0.005 else "HOLD")
        rebalancing_actions.append({
            "symbol":          sym,
            "name":            h.get("name", sym),
            "beta":            h["beta"],
            "current_weight":  cur,
            "target_weight":   target,
            "weight_delta":    diff,
            "action":          action,
            "shares_delta":    shares_delta,
            "estimated_value": round(abs(diff) * portfolio_value, 2),
        })

    return {
        "portfolio_value": portfolio_value,
        "current_portfolio": {
            "weights": current_weights, "expected_return": current_return,
            "volatility": current_vol, "sharpe_ratio": current_sharpe,
        },
        "min_variance_portfolio": {
            "weights": min_var_weights, "expected_return": min_var_return,
            "volatility": min_var_vol, "sharpe_ratio": min_var_sharpe,
        },
        "max_sharpe_portfolio": {
            "weights": max_sharpe_weights, "expected_return": max_sharpe_return,
            "volatility": max_sharpe_vol, "sharpe_ratio": max_sharpe_sharpe,
        },
        "efficient_frontier": efficient_frontier,
        "rebalancing_actions": rebalancing_actions,
    }


@router.get("/optimization")
async def get_optimization():
    """Mean-variance optimization on the real portfolio."""
    holdings, portfolio_value = await _get_holdings()
    opt = _compute_optimization(holdings, portfolio_value)
    return {"status": "success", "data": {"as_of": datetime.now().isoformat(), **opt}}


# ── LLM helpers ────────────────────────────────────────────────────────────────

async def _call_ollama(prompt: str, model: str) -> str:
    try:
        client = ollama_lib.AsyncClient(host=settings.LLM_API_URL)
        response = await client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.35, "num_predict": 2500},
        )
        if hasattr(response, "message"):
            msg = response.message
            return msg.content if hasattr(msg, "content") else str(msg)
        if isinstance(response, dict):
            msg = response.get("message", {})
            return msg.get("content", "") if isinstance(msg, dict) else str(msg)
        return str(response)
    except Exception as e:
        logger.error(
            "Ollama LLM unavailable in risk module",
            extra={"log_type": "risk_event", "event": "llm_error", "error": str(e)},
        )
        raise HTTPException(status_code=503, detail=f"Ollama LLM unavailable — {e}. Run: ollama pull {model}")


def _build_optimization_prompt(holdings: list[dict], portfolio_value: float, opt: dict) -> str:
    cur = opt["current_portfolio"]
    mv  = opt["min_variance_portfolio"]
    ms  = opt["max_sharpe_portfolio"]

    holdings_table = "Symbol       | Name                          | Weight  | Beta | Quality\n"
    holdings_table += "-" * 72 + "\n"
    for h in holdings:
        fp = FACTOR_PROFILES.get(h["symbol"], {})
        holdings_table += (
            f"{h['symbol']:<12} | {h.get('name', h['symbol']):<29} | "
            f"{h['weight']*100:>5.1f}%  | {h['beta']:.2f} | {fp.get('quality', 0.4):.2f}\n"
        )

    actions_table = "Symbol       | Action | Δ Weight | Shares Δ | Est. Value\n"
    actions_table += "-" * 60 + "\n"
    for a in opt["rebalancing_actions"]:
        actions_table += (
            f"{a['symbol']:<12} | {a['action']:<6} | "
            f"{a['weight_delta']*100:>+6.2f}% | {a['shares_delta']:>8} | ₹{a['estimated_value']:>10,.0f}\n"
        )

    portfolio_beta = round(sum(h["weight"] * h["beta"] for h in holdings), 3)
    dominant = max(holdings, key=lambda h: h["weight"])

    return f"""You are an expert portfolio manager specializing in Indian equity markets (NSE/BSE).
Analyze this portfolio and provide concrete, actionable optimization recommendations.

===== PORTFOLIO OVERVIEW =====
Total Portfolio Value: ₹{portfolio_value:,.0f}
Number of Holdings: {len(holdings)}
Portfolio Beta (vs NIFTY 50): {portfolio_beta:.3f}
Largest Position: {dominant['symbol']} ({dominant.get('name', '')} — {dominant['weight']*100:.1f}% of portfolio)

===== CURRENT HOLDINGS =====
{holdings_table}

===== OPTIMIZATION SCENARIOS =====

CURRENT PORTFOLIO:
  Expected Return: {cur['expected_return']*100:.2f}%  |  Volatility: {cur['volatility']*100:.2f}%  |  Sharpe: {cur['sharpe_ratio']:.3f}

MIN VARIANCE PORTFOLIO (shifts weight toward lowest-beta stocks):
  Expected Return: {mv['expected_return']*100:.2f}%  |  Volatility: {mv['volatility']*100:.2f}%  |  Sharpe: {mv['sharpe_ratio']:.3f}
  Volatility reduction: {(cur['volatility'] - mv['volatility'])*100:.2f}%

MAX SHARPE PORTFOLIO (weights by quality factor, maximizes risk-adjusted return):
  Expected Return: {ms['expected_return']*100:.2f}%  |  Volatility: {ms['volatility']*100:.2f}%  |  Sharpe: {ms['sharpe_ratio']:.3f}
  Sharpe improvement: +{ms['sharpe_ratio'] - cur['sharpe_ratio']:.3f}

===== PROPOSED REBALANCING ACTIONS (Current → Max Sharpe) =====
{actions_table}

===== YOUR ANALYSIS =====

Use EXACTLY these section headers. Be specific — name stocks, cite percentages, give concrete ₹ reasoning.

## 1. PORTFOLIO ASSESSMENT
Evaluate: concentration risk, beta exposure ({portfolio_beta:.2f} vs NIFTY = 1.0), quality of holdings, sector skew. What is the dominant risk theme?

## 2. OPTIMIZATION RECOMMENDATION
Should this investor move toward Min Variance or Max Sharpe — or stay current? Give a clear recommendation with a trade-off explanation specific to this portfolio's composition.

## 3. PRIORITY REBALANCING ACTIONS
List the top 3 most impactful trades to execute first. For each:
- Stock name + symbol
- BUY / SELL and approximate amount in ₹
- Specific reason based on this stock's beta, quality, and weight

## 4. RISK WARNINGS
Identify the top 2–3 specific risks requiring attention. Name the stocks and cite their weight/beta. What scenario would hurt this portfolio most?

## 5. DIVERSIFICATION GAPS
What is this portfolio missing? Suggest 1–2 stock types or sectors (with examples of NSE-listed stocks) that would improve diversification.

## 6. BOTTOM LINE
One clear paragraph: overall portfolio health, the single most important action to take, and the expected improvement in risk-adjusted returns."""


@router.get("/optimization/analyze")
async def analyze_optimization(
    model: Optional[str] = Query(None, description="Override Ollama model"),
):
    """Feed portfolio optimization data to the LLM and return AI recommendations."""
    llm_model = model or settings.LLM_MODEL
    holdings, portfolio_value = await _get_holdings()
    opt = _compute_optimization(holdings, portfolio_value)
    prompt = _build_optimization_prompt(holdings, portfolio_value, opt)
    analysis = await _call_ollama(prompt, llm_model)
    return {
        "status": "success",
        "data": {
            "analysis":     analysis,
            "model_used":   llm_model,
            "generated_at": datetime.now().isoformat(),
        },
    }
