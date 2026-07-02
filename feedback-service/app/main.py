"""feedback-service — subscribes to trade.outcomes, stores records, updates weights."""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, date as date_type

import aio_pika
import asyncpg
from fastapi import FastAPI
from pydantic_settings import BaseSettings
from pydantic import model_validator

from app.weight_updater import compute_weight_updates, determine_outcome

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from app.cors import configure_cors


class Settings(BaseSettings):
    SERVICE_PORT: int = 8012
    SERVICE_NAME: str = "feedback-service"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "stock_user"
    POSTGRES_PASSWORD: str = "stock_password"
    POSTGRES_DB: str = "stock_prediction_db"
    POSTGRES_URL: str = ""
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_URL: str = ""
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    REDIS_URL: str = ""
    RETRAIN_THRESHOLD: int = 500
    WEIGHT_LEARNING_RATE: float = 0.05

    @model_validator(mode="after")
    def build_urls(self) -> "Settings":
        if not self.POSTGRES_URL:
            self.POSTGRES_URL = f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        if not self.REDIS_URL:
            auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.REDIS_URL = f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        if not self.RABBITMQ_URL:
            self.RABBITMQ_URL = f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/"
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
_pool: asyncpg.Pool | None = None
_tasks: list[asyncio.Task] = []
_trade_count_since_retrain = 0


async def _load_current_weights(pool: asyncpg.Pool) -> dict[str, float]:
    rows = await pool.fetch("SELECT agent, weight FROM agent_weights")
    return {r["agent"]: float(r["weight"]) for r in rows}


async def _save_weights(pool: asyncpg.Pool, weights: dict[str, float]) -> None:
    for agent, weight in weights.items():
        await pool.execute(
            "UPDATE agent_weights SET weight=$1, updated_at=NOW() WHERE agent=$2",
            weight, agent,
        )


async def _store_trade_record(pool: asyncpg.Pool, payload: dict) -> None:
    await pool.execute(
        """
        INSERT INTO trade_records
            (trade_id, symbol, exchange, action, entry_price, exit_price,
             pnl_pct, pnl_abs, duration_minutes, ensemble_confidence,
             agent_signals, market_context, outcome, timestamp_open, timestamp_close, trade_source)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
        ON CONFLICT (trade_id) DO UPDATE SET
            exit_price=EXCLUDED.exit_price,
            pnl_pct=EXCLUDED.pnl_pct,
            pnl_abs=EXCLUDED.pnl_abs,
            outcome=EXCLUDED.outcome,
            timestamp_close=EXCLUDED.timestamp_close,
            trade_source=EXCLUDED.trade_source
        """,
        payload.get("trade_id", str(uuid.uuid4())),
        payload.get("symbol", ""),
        payload.get("exchange", "NSE"),
        payload.get("action", ""),
        float(payload.get("entry_price", 0)),
        float(payload.get("exit_price", 0)) if payload.get("exit_price") else None,
        float(payload.get("pnl_pct", 0)) if payload.get("pnl_pct") is not None else None,
        float(payload.get("pnl_abs", 0)) if payload.get("pnl_abs") is not None else None,
        int(payload.get("duration_minutes", 0)) if payload.get("duration_minutes") else None,
        float(payload.get("ensemble_confidence", 0)),
        json.dumps(payload.get("agent_signals", {})),
        json.dumps(payload.get("market_context", {})),
        payload.get("outcome"),
        datetime.fromisoformat(payload["timestamp_open"]) if payload.get("timestamp_open") else datetime.now(tz=timezone.utc),
        datetime.fromisoformat(payload["timestamp_close"]) if payload.get("timestamp_close") else None,
        payload.get("trade_source", "LIVE"),
    )


async def _store_rl_experience(pool: asyncpg.Pool, payload: dict) -> None:
    """Store experience tuple for RL agent replay buffer."""
    state = payload.get("state", {})
    next_state = payload.get("next_state", {})
    if not state:
        return
    action_map = {"BUY": 1, "SELL": 2, "HOLD": 0}
    action = action_map.get(payload.get("action", "HOLD"), 0)
    pnl = float(payload.get("pnl_pct", 0)) if payload.get("pnl_pct") is not None else 0.0
    # Match the Sharpe-blended reward from TradingEnv.step()
    # Without rolling history we fall back to a penalised PnL
    reward = 0.6 * pnl + 0.4 * pnl - 0.001   # simplified: still equal weights, minus cost

    await pool.execute(
        "INSERT INTO rl_experiences (symbol, state, action, reward, next_state, done) VALUES ($1,$2,$3,$4,$5,$6)",
        payload.get("symbol", ""),
        json.dumps(state),
        action,
        reward,
        json.dumps(next_state),
        payload.get("outcome") not in (None, "OPEN"),
    )

    # Prune replay buffer to last 10k experiences
    await pool.execute(
        "DELETE FROM rl_experiences WHERE id NOT IN (SELECT id FROM rl_experiences ORDER BY created_at DESC LIMIT 10000)"
    )


async def _maybe_trigger_retrain(pool: asyncpg.Pool, publisher_channel: aio_pika.Channel) -> None:
    global _trade_count_since_retrain
    _trade_count_since_retrain += 1
    if _trade_count_since_retrain >= settings.RETRAIN_THRESHOLD:
        _trade_count_since_retrain = 0
        try:
            exchange = await publisher_channel.get_exchange("model.retrain")
            msg = json.dumps({
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "service": settings.SERVICE_NAME,
                "payload": {"reason": f"retrain_threshold_{settings.RETRAIN_THRESHOLD}_reached"},
            }).encode()
            await exchange.publish(
                aio_pika.Message(body=msg, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                routing_key="retrain",
            )
            logger.info("Retraining triggered after %d trades", settings.RETRAIN_THRESHOLD)
        except Exception as exc:
            logger.error("Retrain trigger failed: %s", exc)


async def _consumer_loop() -> None:
    while True:
        try:
            connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)
                queue = await channel.get_queue("trade.outcomes.feedback")

                async with queue.iterator() as q_iter:
                    async for message in q_iter:
                        async with message.process():
                            try:
                                if not _pool:
                                    continue
                                body = json.loads(message.body)
                                payload = body.get("payload", body)
                                symbol = payload.get("symbol", "")
                                action = payload.get("action", "")
                                pnl_pct = payload.get("pnl_pct")
                                agent_signals = payload.get("agent_signals", {})

                                # Store trade record
                                await _store_trade_record(_pool, payload)

                                # Store RL experience
                                await _store_rl_experience(_pool, payload)

                                # Update ensemble weights if trade is closed
                                if pnl_pct is not None and action in ("BUY", "SELL"):
                                    outcome = determine_outcome(float(pnl_pct))
                                    current_weights = await _load_current_weights(_pool)
                                    new_weights = compute_weight_updates(
                                        current_weights,
                                        agent_signals,
                                        outcome,
                                        action,
                                        settings.WEIGHT_LEARNING_RATE,
                                    )
                                    await _save_weights(_pool, new_weights)
                                    logger.info(
                                        "Weights updated for %s trade on %s (P&L: %.2f%%) → %s",
                                        outcome, symbol, float(pnl_pct) * 100, new_weights,
                                    )

                                # Trigger retraining if threshold reached
                                await _maybe_trigger_retrain(_pool, channel)

                            except Exception as exc:
                                logger.error("Feedback message error: %s", exc)
        except Exception as exc:
            logger.error("Feedback consumer lost: %s — retry 5s", exc)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    for attempt in range(1, 11):
        try:
            _pool = await asyncpg.create_pool(settings.POSTGRES_URL, min_size=2, max_size=6)
            break
        except Exception:
            await asyncio.sleep(min(2 ** attempt, 30))
    _tasks.append(asyncio.create_task(_consumer_loop(), name="feedback-consumer"))
    logger.info("feedback-service ready — learning_rate=%.3f retrain_at=%d",
                settings.WEIGHT_LEARNING_RATE, settings.RETRAIN_THRESHOLD)
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    if _pool:
        await _pool.close()


app = FastAPI(title="NeuradeX — Feedback Service", lifespan=lifespan)
configure_cors(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.SERVICE_NAME, "trades_since_retrain": _trade_count_since_retrain}


@app.get("/stats")
async def get_stats():
    if not _pool:
        return {"error": "not ready"}
    try:
        rows = await _pool.fetch(
            "SELECT outcome, COUNT(*) as count, AVG(pnl_pct) as avg_pnl FROM trade_records WHERE outcome IS NOT NULL GROUP BY outcome"
        )
        return {
            "trade_stats": [dict(r) for r in rows],
            "trades_since_retrain": _trade_count_since_retrain,
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/weights")
async def get_weights():
    if not _pool:
        return {"error": "not ready"}
    weights = await _load_current_weights(_pool)
    return {"weights": weights}


@app.post("/trades")
async def post_trades(payload: list[dict]):
    """Direct insert for backtest/paper trades — bypasses RabbitMQ."""
    if not _pool:
        return {"error": "not ready"}
    saved = 0
    for record in payload:
        try:
            await _store_trade_record(_pool, record)
            saved += 1
        except Exception as exc:
            logger.error("POST /trades insert error: %s", exc)
    return {"saved": saved, "total": len(payload)}


@app.get("/trades")
async def get_trades(limit: int = 500, offset: int = 0, source: str = None):
    if not _pool:
        return []
    try:
        if source and source.upper() != "ALL":
            rows = await _pool.fetch(
                """
                SELECT trade_id, symbol, exchange, action, entry_price, exit_price,
                       pnl_pct, pnl_abs, duration_minutes, ensemble_confidence,
                       agent_signals, market_context, outcome, timestamp_open, timestamp_close,
                       trade_source, created_at
                FROM trade_records
                WHERE COALESCE(trade_source, 'LIVE') = $3
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit, offset, source.upper(),
            )
        else:
            rows = await _pool.fetch(
                """
                SELECT trade_id, symbol, exchange, action, entry_price, exit_price,
                       pnl_pct, pnl_abs, duration_minutes, ensemble_confidence,
                       agent_signals, market_context, outcome, timestamp_open, timestamp_close,
                       trade_source, created_at
                FROM trade_records
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit, offset,
            )
        result = []
        for r in rows:
            d = dict(r)
            d["agent_signals"]   = json.loads(d["agent_signals"])   if d["agent_signals"]   else {}
            d["market_context"]  = json.loads(d["market_context"])  if d["market_context"]  else {}
            # Normalise timestamps to ISO strings
            for k in ("timestamp_open", "timestamp_close", "created_at"):
                if d.get(k):
                    d[k] = d[k].isoformat()
            result.append(d)
        return result
    except Exception as exc:
        logger.error("GET /trades error: %s", exc)
        return []


@app.get("/trades/exists")
async def trade_exists(symbol: str, date: str):
    """Return {exists: bool} — true if symbol has any historical trades on the given date (YYYY-MM-DD)."""
    if not _pool:
        return {"exists": False}
    try:
        trade_date = datetime.strptime(date, "%Y-%m-%d").date()
        row = await _pool.fetchrow(
            """
            SELECT 1 FROM trade_records
            WHERE symbol = $1
              AND DATE(timestamp_open AT TIME ZONE 'Asia/Kolkata') = $2
              AND trade_source IN ('BACKTEST', 'REPLAY')
            LIMIT 1
            """,
            symbol.upper(), trade_date,
        )
        return {"exists": row is not None}
    except Exception as exc:
        logger.error("GET /trades/exists error: %s", exc)
        return {"exists": False}


@app.get("/agent-accuracy")
async def get_agent_accuracy(min_trades: int = 20):
    """
    Per-agent precision, recall, F1 and confusion matrix derived from closed trades.
    For each agent, reads agent_signals JSONB to extract the signal the agent voted,
    then compares against the actual trade outcome (WIN/LOSS) to compute metrics.
    """
    if not _pool:
        return {"error": "not ready"}
    try:
        rows = await _pool.fetch(
            """
            SELECT agent_signals, action, outcome
            FROM trade_records
            WHERE outcome IN ('WIN', 'LOSS')
              AND agent_signals IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 2000
            """
        )

        AGENTS = ["technical", "pattern", "sentiment", "rl", "macro"]
        # For each agent track: TP, FP, TN, FN
        # A correct prediction = agent signal aligned with action AND outcome=WIN,
        # OR agent signal opposed trade direction AND outcome=LOSS.
        stats: dict[str, dict] = {a: {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "total": 0} for a in AGENTS}

        for row in rows:
            raw = row.get("agent_signals") or "{}"
            signals = json.loads(raw) if isinstance(raw, str) else raw
            actual_action = row.get("action", "HOLD")
            outcome = row.get("outcome", "")

            for agent in AGENTS:
                vote = signals.get(agent, {})
                if not isinstance(vote, dict):
                    continue
                agent_signal = vote.get("signal", "HOLD")
                if agent_signal == "HOLD":
                    continue

                s = stats[agent]
                s["total"] += 1
                agent_agreed = (agent_signal == actual_action)
                if agent_agreed and outcome == "WIN":
                    s["tp"] += 1
                elif agent_agreed and outcome == "LOSS":
                    s["fp"] += 1
                elif not agent_agreed and outcome == "LOSS":
                    s["tn"] += 1
                else:  # not agreed, outcome WIN
                    s["fn"] += 1

        result = {}
        for agent, s in stats.items():
            total = s["total"]
            if total < min_trades:
                result[agent] = {"status": "insufficient_data", "total": total}
                continue
            tp, fp, tn, fn = s["tp"], s["fp"], s["tn"], s["fn"]
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            accuracy = (tp + tn) / total if total > 0 else 0.0
            result[agent] = {
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "accuracy": round(accuracy, 4),
                "total_trades": total,
                "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
            }

        return {"agent_accuracy": result, "evaluated_from": len(rows), "min_trades_threshold": min_trades}
    except Exception as exc:
        logger.error("GET /agent-accuracy error: %s", exc)
        return {"error": str(exc)}


@app.get("/portfolio-metrics")
async def get_portfolio_metrics():
    """
    Portfolio-level metrics: Sharpe, Sortino, Calmar, max drawdown, win rate.
    Computed from all closed trade_records.
    """
    if not _pool:
        return {"error": "not ready"}
    try:
        rows = await _pool.fetch(
            """
            SELECT pnl_pct, outcome, timestamp_open, timestamp_close
            FROM trade_records
            WHERE outcome IN ('WIN', 'LOSS', 'BREAK_EVEN')
              AND pnl_pct IS NOT NULL
            ORDER BY timestamp_open ASC
            """
        )
        if not rows:
            return {"error": "no closed trades"}

        pnls = [float(r["pnl_pct"]) for r in rows]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0.001)
        losses = sum(1 for p in pnls if p < -0.001)
        win_rate = wins / n if n else 0.0
        mean_pnl = sum(pnls) / n
        std_pnl = (sum((p - mean_pnl) ** 2 for p in pnls) / max(n - 1, 1)) ** 0.5

        # Sharpe (annualised assuming ~252 trading days, 1 trade per day approximation)
        sharpe = (mean_pnl / std_pnl * (252 ** 0.5)) if std_pnl > 0 else 0.0

        # Sortino (only downside deviation)
        neg_pnls = [p for p in pnls if p < 0]
        down_std = (sum(p ** 2 for p in neg_pnls) / max(len(neg_pnls), 1)) ** 0.5
        sortino = (mean_pnl / down_std * (252 ** 0.5)) if down_std > 0 else 0.0

        # Max drawdown (cumulative)
        cumulative = 1.0
        peak = 1.0
        max_dd = 0.0
        for p in pnls:
            cumulative *= (1 + p)
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak
            if dd > max_dd:
                max_dd = dd

        total_return = cumulative - 1.0
        calmar = (total_return / max_dd) if max_dd > 0 else 0.0

        return {
            "total_trades": n,
            "win_rate": round(win_rate, 4),
            "mean_pnl_pct": round(mean_pnl * 100, 4),
            "std_pnl_pct": round(std_pnl * 100, 4),
            "total_return_pct": round(total_return * 100, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "calmar_ratio": round(calmar, 4),
        }
    except Exception as exc:
        logger.error("GET /portfolio-metrics error: %s", exc)
        return {"error": str(exc)}


@app.get("/trades/{trade_id}")
async def get_trade(trade_id: str):
    if not _pool:
        return {"error": "not ready"}
    try:
        row = await _pool.fetchrow(
            """
            SELECT trade_id, symbol, exchange, action, entry_price, exit_price,
                   pnl_pct, pnl_abs, duration_minutes, ensemble_confidence,
                   agent_signals, market_context, outcome, timestamp_open, timestamp_close,
                   trade_source, created_at
            FROM trade_records WHERE trade_id=$1
            """,
            trade_id,
        )
        if not row:
            return {"error": "not found"}
        d = dict(row)
        agent_signals  = json.loads(d["agent_signals"])  if d["agent_signals"]  else {}
        market_context = json.loads(d["market_context"]) if d["market_context"] else {}
        d["agent_signals"]  = agent_signals
        d["market_context"] = market_context
        for k in ("timestamp_open", "timestamp_close", "created_at"):
            if d.get(k):
                d[k] = d[k].isoformat()

        # Reconstruct execution steps for the frontend
        atr = float(market_context.get("atr", 0))
        price = float(d["entry_price"] or 0)
        action = d["action"] or "BUY"
        stop_loss   = (price - atr * 2) if action == "BUY" else (price + atr * 2)
        take_profit = (price + atr * 3) if action == "BUY" else (price - atr * 3)

        d["execution_steps"] = [
            {
                "step": 1, "name": "Market Signal",
                "data": {"symbol": d["symbol"], "price": price, "regime": market_context.get("regime"), "vix": market_context.get("vix")},
            },
            {
                "step": 2, "name": "Agent Decisions",
                "data": agent_signals,
            },
            {
                "step": 3, "name": "Ensemble Vote",
                "data": {"action": action, "confidence": d["ensemble_confidence"]},
            },
            {
                "step": 4, "name": "Risk Gate",
                "data": {"atr": atr, "stop_loss": stop_loss, "take_profit": take_profit},
            },
            {
                "step": 5, "name": "Order Fill",
                "data": {"fill_price": price, "status": "FILLED"},
            },
            {
                "step": 6, "name": "Trade Outcome",
                "data": {"exit_price": d.get("exit_price"), "pnl_pct": d.get("pnl_pct"), "outcome": d.get("outcome")},
            },
        ]
        return d
    except Exception as exc:
        logger.error("GET /trades/%s error: %s", trade_id, exc)
        return {"error": str(exc)}
