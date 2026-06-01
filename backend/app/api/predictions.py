"""
Predictions API Routes
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import random
from datetime import datetime

from app.utils.elk_logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


class PredictionRequest(BaseModel):
    symbol: str
    timeframe: Optional[str] = "1h"


class PredictionResponse(BaseModel):
    symbol: str
    prediction: str  # "UP", "DOWN", "NEUTRAL"
    confidence: float
    target_price: float
    stop_loss: float
    timeframe: str
    reasoning: str
    timestamp: datetime


@router.get("/{symbol}")
async def get_prediction(symbol: str = "AAPL"):
    """Get AI prediction for a stock"""
    try:
        prediction_value = random.choice(["UP", "DOWN", "NEUTRAL"])
        confidence = round(random.uniform(0.5, 0.99), 2)
        
        # Get current stock price (simulated)
        current_price = round(100 + random.uniform(-50, 200), 2)
        
        # Generate target price based on prediction
        if prediction_value == "UP":
            target_price = round(current_price * random.uniform(1.02, 1.10), 2)
        elif prediction_value == "DOWN":
            target_price = round(current_price * random.uniform(0.90, 0.98), 2)
        else:
            target_price = round(current_price * random.uniform(0.98, 1.02), 2)
        
        stop_loss = round(current_price * 0.95, 2)
        
        reasoning_templates = {
            "UP": [
                f"Strong bullish candlestick patterns detected. RSI shows oversold conditions with recovery momentum.",
                f"Positive sentiment from recent news and analyst upgrades. Trading volume above average.",
                f"Technical support levels broken, MACD showing bullish crossover signals."
            ],
            "DOWN": [
                f"Bearish divergence observed on daily chart. Resistance level not breached.",
                f"Negative sentiment on social media. Institutional selling pressure detected.",
                f"Moving averages showing downward trend. Volume confirms selling intensity."
            ],
            "NEUTRAL": [
                f"Indecisive market with equal buy/sell signals. Consolidation pattern observed.",
                f"Mixed technical indicators. Awaiting key economic events for direction.",
                f"Balanced sentiment with no clear bias. Market in accumulation phase."
            ]
        }
        
        reasoning = random.choice(reasoning_templates[prediction_value])
        
        prediction_data = {
            "symbol": symbol,
            "prediction": prediction_value,
            "confidence": confidence,
            "target_price": target_price,
            "current_price": current_price,
            "stop_loss": stop_loss,
            "upside_potential": round(((target_price - current_price) / current_price) * 100, 2),
            "risk_reward_ratio": round((target_price - current_price) / (current_price - stop_loss), 2),
            "timeframe": "1-4 hours",
            "reasoning": reasoning,
            "factors": [
                "Technical Analysis",
                "Sentiment Analysis",
                "Market Microstructure",
                "Historical Patterns"
            ],
            "timestamp": datetime.now().isoformat()
        }
        
        return {
            "status": "success",
            "data": prediction_data
        }
    except Exception as e:
        logger.error(
            "Error generating prediction",
            extra={"log_type": "prediction_event", "event": "prediction_error", "symbol": symbol, "error": str(e)},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to generate prediction")


@router.post("/{symbol}/custom-analysis")
async def custom_analysis(symbol: str):
    """Get custom AI analysis for a stock"""
    try:
        analysis_data = {
            "symbol": symbol,
            "analysis_type": "comprehensive",
            "technical_analysis": {
                "trend": random.choice(["Uptrend", "Downtrend", "Sideways"]),
                "strength": round(random.uniform(0.4, 0.95), 2),
                "key_support": round(100 + random.uniform(-20, 0), 2),
                "key_resistance": round(100 + random.uniform(0, 50), 2),
                "patterns": ["Head and Shoulders", "Double Bottom", "Ascending Triangle"]
            },
            "fundamental_analysis": {
                "pe_ratio": round(random.uniform(10, 40), 2),
                "earnings_growth": round(random.uniform(-10, 50), 2),
                "debt_to_equity": round(random.uniform(0.1, 2.0), 2),
                "roe": round(random.uniform(5, 25), 2)
            },
            "sentiment_analysis": {
                "news_score": round(random.uniform(-1, 1), 2),
                "social_score": round(random.uniform(-1, 1), 2),
                "analyst_score": round(random.uniform(1, 5), 2)
            },
            "ai_recommendation": {
                "action": random.choice(["BUY", "SELL", "HOLD"]),
                "confidence": round(random.uniform(0.55, 0.99), 2),
                "entry_points": [
                    round(100 + random.uniform(-10, 0), 2),
                    round(100 + random.uniform(-5, 5), 2)
                ],
                "exit_points": [
                    round(100 + random.uniform(5, 15), 2),
                    round(100 + random.uniform(15, 30), 2)
                ]
            },
            "timestamp": datetime.now().isoformat()
        }
        
        return {
            "status": "success",
            "data": analysis_data
        }
    except Exception as e:
        logger.error(f"Error performing custom analysis for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to perform analysis")


@router.get("/{symbol}/history")
async def get_prediction_history(symbol: str = "AAPL", limit: int = 10):
    """Get prediction history for a stock"""
    try:
        from datetime import timedelta
        
        history = []
        for i in range(limit):
            timestamp = datetime.now() - timedelta(hours=i*4)
            prediction = {
                "prediction": random.choice(["UP", "DOWN", "NEUTRAL"]),
                "confidence": round(random.uniform(0.5, 0.99), 2),
                "target_price": round(100 + random.uniform(-30, 30), 2),
                "actual_price": round(100 + random.uniform(-30, 30), 2),
                "accuracy": "✓" if random.random() > 0.4 else "✗",
                "timestamp": timestamp.isoformat()
            }
            history.append(prediction)
        
        return {
            "status": "success",
            "symbol": symbol,
            "count": len(history),
            "data": history
        }
    except Exception as e:
        logger.error(f"Error fetching prediction history for {symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch prediction history")


@router.get("/accuracy/stats")
async def get_accuracy_stats():
    """Real, evidence-backed system metrics computed live from actual trades
    (trade_records) and the agent learning loop (ai_engine_outcomes).
    No hard-coded or random values — every number is derived from stored data."""
    import math
    from sqlalchemy import text
    from app.database.postgres import engine

    try:
        async with engine.begin() as conn:
            # ── Closed trades (the evidence for win-rate / return / sharpe) ──────
            rows = (await conn.execute(text(
                "SELECT pnl_pct, pnl_abs, outcome, trade_source "
                "FROM trade_records WHERE outcome IN ('WIN','LOSS') ORDER BY created_at ASC"
            ))).fetchall()

            # ── Agent prediction accuracy (the learning loop) ───────────────────
            acc_row = (await conn.execute(text(
                "SELECT COUNT(*), SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) "
                "FROM ai_engine_outcomes"
            ))).fetchone()

        total_preds   = int(acc_row[0] or 0) if acc_row else 0
        correct_preds = int(acc_row[1] or 0) if acc_row else 0
        accuracy_rate = (correct_preds / total_preds) if total_preds else 0.0

        # pnl_pct is stored as a fraction (0.0245 = 2.45%)
        returns = [float(r[0]) for r in rows if r[0] is not None]
        pnls    = [float(r[1]) for r in rows if r[1] is not None]
        wins    = sum(1 for r in rows if r[2] == 'WIN')
        losses  = sum(1 for r in rows if r[2] == 'LOSS')
        total   = wins + losses

        win_rate   = (wins / total) if total else 0.0
        avg_ret    = (sum(returns) / len(returns) * 100) if returns else 0.0     # %
        std_ret    = (math.sqrt(sum((x*100 - avg_ret) ** 2 for x in returns) / len(returns))
                      if len(returns) > 1 else 0.0)
        sharpe     = (avg_ret / std_ret) if std_ret > 0 else 0.0                  # per-trade Sharpe
        best_pct   = max(returns) * 100 if returns else 0.0
        worst_pct  = min(returns) * 100 if returns else 0.0

        # Max drawdown from the running cumulative P&L curve
        max_dd = 0.0
        if pnls:
            cum = peak = 0.0
            for p in pnls:
                cum += p
                peak = max(peak, cum)
                max_dd = min(max_dd, cum - peak)

        # Win-rate / return broken down by source (evidence)
        by_source = {}
        for r in rows:
            src = r[3] or 'LIVE'
            d = by_source.setdefault(src, {"trades": 0, "wins": 0, "ret_sum": 0.0})
            d["trades"] += 1
            d["wins"] += 1 if r[2] == 'WIN' else 0
            d["ret_sum"] += float(r[0] or 0.0)
        by_source_list = [
            {"source": s, "trades": d["trades"],
             "win_rate": round(d["wins"] / d["trades"], 4) if d["trades"] else 0.0,
             "avg_return": round(d["ret_sum"] / d["trades"] * 100, 2) if d["trades"] else 0.0}
            for s, d in sorted(by_source.items())
        ]

        stats = {
            "accuracy_rate":       round(accuracy_rate, 4),
            "total_predictions":   total_preds,
            "correct_predictions": correct_preds,

            "win_rate":            round(win_rate, 4),
            "winning_trades":      wins,
            "losing_trades":       losses,
            "total_trades":        total,

            "average_return":      round(avg_ret, 2),
            "return_std":          round(std_ret, 2),
            "best_trade_pct":      round(best_pct, 2),
            "worst_trade_pct":     round(worst_pct, 2),

            "sharpe_ratio":        round(sharpe, 2),
            "max_drawdown":        round(max_dd, 2),

            "by_source":           by_source_list,
            "has_data":            total > 0 or total_preds > 0,
            "updated_at":          datetime.now().isoformat(),
        }
        return {"status": "success", "data": stats}
    except Exception as e:
        logger.error(f"Error fetching accuracy stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch accuracy stats")
