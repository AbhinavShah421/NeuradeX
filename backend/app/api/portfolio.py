"""
Portfolio API Routes — backed by Groww holdings/positions with simulation fallback.

This is a thin FastAPI router: it parses/validates requests and delegates all
business logic (DB/data-provider access, calculations, simulation) to
app.services.portfolio_service. See that module's docstring for the list of
names re-exported below for other modules that import internals from here.
"""
from fastapi import APIRouter

from app.utils.elk_logger import get_logger
from app.services import portfolio_service as service

# Pydantic request model — defined in the service module, imported here for
# use as a route parameter type. Schema is unchanged, only the module moved.
from app.services.portfolio_service import Alert

# Re-exported for external consumers that import these names directly from
# app.api.portfolio — do not remove without updating those call sites:
#   app.api.delivery_paper: _yahoo_quote_map, _build_themes
from app.services.portfolio_service import (  # noqa: F401
    _yahoo_quote_map,
    _build_themes,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get("/")
async def get_portfolio():
    """Portfolio holdings — live from Groww (holdings + LTP), else simulation."""
    return await service.get_portfolio()


@router.get("/optimize")
async def optimize_portfolio(use_llm: bool = True, refresh: bool = False):
    """AI-driven portfolio optimization: live signals + risk + scanner opportunities,
    synthesised by the LLM into a rebalancing plan (deterministic fallback).

    The result is persisted in Redis and keyed to the latest AI scan: it's served
    from cache instantly while the scan is unchanged, and auto-recomputed when a
    newer scan lands (or when refresh=true)."""
    return await service.optimize_portfolio(use_llm, refresh)


@router.get("/invest-plan")
async def invest_plan(amount: float, max_stocks: int = 6):
    """Agentic allocation: split `amount` across the best AI picks, weighted by
    conviction (win probability, capped for diversification), as protective LIMIT
    buy orders sized to real prices."""
    return await service.invest_plan(amount, max_stocks)


@router.post("/add")
async def add_to_portfolio(symbol: str, quantity: int, purchase_price: float):
    """Add stock record (informational — actual orders go through /api/orders)."""
    return await service.add_to_portfolio(symbol, quantity, purchase_price)


@router.get("/performance")
async def get_performance():
    """Portfolio performance metrics — simulated."""
    return await service.get_performance()


@router.get("/alerts")
async def get_alerts():
    """Active price/pattern alerts."""
    return await service.get_alerts()


@router.post("/alerts")
async def create_alert(alert: Alert):
    """Create a new alert."""
    return await service.create_alert(alert)


@router.get("/sector-exposure")
async def sector_exposure():
    """AI sector-exposure scanner + optimizer: the book's current sector weights
    vs an AI-favoured target, with over/under-exposure and rebalance moves."""
    return await service.sector_exposure()


@router.get("/fund-baskets")
async def fund_baskets():
    """AI-scanned mutual-fund-style baskets — themed, weighted stock baskets built
    from the live AI ranked board."""
    return await service.fund_baskets()


@router.get("/fund-baskets/invest")
async def fund_basket_invest(basket: str, amount: float):
    """Allocate `amount` across a chosen basket's holdings by weight, as protective
    LIMIT buy orders sized to real prices."""
    return await service.fund_basket_invest(basket, amount)


@router.get("/themes")
async def themes():
    """smallcase-style AI thematic baskets — narrative themes (EV, green energy,
    defence, digital, …) populated and conviction-weighted from the live scan.
    Lightweight (no backtest); call /themes/{id}/analytics for risk/return."""
    return await service.themes()


@router.get("/themes/{theme_id}/analytics")
async def theme_analytics(theme_id: str):
    """Backtested risk/return (CAGR, volatility, drawdown, Sharpe, vs NIFTY) for a
    theme's current holdings. Cached briefly — it fetches constituent history."""
    return await service.theme_analytics(theme_id)


@router.get("/themes/{theme_id}/rebalance")
async def theme_rebalance(theme_id: str, held: str = ""):
    """Propose a rebalance update for a theme: adds, drops (with reasons), new
    target weights and a drift score. `held` = comma-separated symbols you hold
    (omit to compare against the theme's current full membership)."""
    return await service.theme_rebalance(theme_id, held)


@router.get("/discover")
async def discover():
    """Discovery collections (smallcase-style): all baskets — thematic + quant —
    grouped into collections and tagged by risk, for browsing/filtering."""
    return await service.discover()


@router.get("/health")
async def portfolio_health():
    """AI Portfolio Health Score (0-100) — a single glanceable measure built from a
    multi-factor model: diversification, concentration, sector balance, holding
    quality (AI grades), performance and drawdown — with the issues + top fixes."""
    return await service.portfolio_health()


@router.get("/sip-planner")
async def sip_planner(goal_amount: float = 0, years: float = 10, risk: str = "moderate",
                      current_corpus: float = 0, monthly: float = 0):
    """Goal-based plan: required SIP (or projected corpus from a given SIP), a
    year-by-year projection with optimistic/pessimistic bands, a risk-based asset
    allocation, and AI fund recommendations per sleeve."""
    return await service.sip_planner(goal_amount, years, risk, current_corpus, monthly)


@router.get("/tax-harvest")
async def tax_harvest():
    """Unrealised gains/losses across holdings, tax-loss-harvest candidates and an
    estimated tax saving. (Groww's API doesn't return buy dates, so LTCG/STCG split
    is approximate — figures use the LTCG long-term rate as a guide.)"""
    return await service.tax_harvest()


@router.get("/benchmark")
async def benchmark():
    """Value-weighted portfolio returns (1M/3M/1Y) vs NIFTY 50, with alpha."""
    return await service.benchmark()


@router.get("/risk-lab")
async def risk_lab(market_shock: float = 10.0, sector_shock: float = 20.0):
    """AI Risk Lab: true (correlation-based) diversification, volatility-aware
    smart exits, scenario stress-test, and dividend income — in one pass."""
    return await service.risk_lab(market_shock, sector_shock)


@router.get("/advisor")
async def advisor():
    """An AI advisor feed: synthesises health, sector exposure, benchmark and tax
    into a few plain-English insights + actions (LLM, with a rule-based fallback)."""
    return await service.advisor()
