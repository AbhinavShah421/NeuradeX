"""feedback-service — subscribes to trade.outcomes, stores records, updates weights."""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, date as date_type

import aio_pika
import asyncpg
from fastapi import FastAPI
from pydantic_settings import BaseSettings
from pydantic import model_validator

from app.weight_updater import compute_weight_updates, count_agent_hits, determine_outcome

from app.elk_logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

from app.cors import configure_cors

# Below this absolute move a trade is noise rather than a result. Round-trip
# costs alone are ~0.1%, so anything inside this band says nothing about whether
# the entry was any good.
MATERIAL_PNL_PCT = float(os.getenv("MATERIAL_PNL_PCT", "0.0015"))   # 0.15%


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


async def _save_agent_hits(pool: asyncpg.Pool, hits: dict[str, bool]) -> None:
    """Accumulate per-agent accuracy counters. Nothing ever wrote these columns,
    so win_count/total_count read 0 for every agent regardless of the weight
    loop's health — which made a dead loop indistinguishable from an idle one."""
    for agent, correct in hits.items():
        await pool.execute(
            """
            UPDATE agent_weights
            SET total_count = total_count + 1,
                win_count   = win_count + $1,
                updated_at  = NOW()
            WHERE agent = $2
            """,
            1 if correct else 0, agent,
        )


def _pick(payload: dict, *keys, default=None):
    """First key present and non-null. Two producers, two vocabularies."""
    for k in keys:
        v = payload.get(k)
        if v is not None:
            return v
    return default


async def _store_trade_record(pool: asyncpg.Pool, payload: dict) -> bool:
    """Store one trade, from either producer. False if the record was refused.

    Two things write here and they do not use the same field names. The Python
    side (POST /trades, backtests and paper sessions) speaks the column names
    directly. The Java trade-executor publishes a `TradeOutcome` over
    trade.outcomes whose fields are named for what they mean at execution time:
    `fill_price`, `agent_votes`, `executed_at`, `pnl`.

    Reading only the column names silently dropped every executor field that
    was not `trade_id`, `symbol` or `action` — measured 2026-09-08, all 90
    executor trades since 2026-08-18 were stored with entry_price 0, confidence
    0, no votes and no context, while the executor's own log for the same
    trades read "BUY NIACL @ 204.49 (paper=true, confidence=0.61)". Nothing
    errored; `payload.get("entry_price", 0)` simply returned the default every
    time. The blank rows filling the Orders page are those records.

    So accept both dialects, preferring the column name where a producer sends
    it. `status` is deliberately NOT mapped onto `outcome`: the executor's
    "FILLED" describes the entry, not how the trade turned out.
    """
    # session_id may be passed as a top-level key or nested inside market_context
    ctx = payload.get("market_context") or {}
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except Exception:
            ctx = {}
    session_id = payload.get("session_id") or ctx.get("session_id")

    # The executor sends no market_context at all, which left these rows with
    # nothing to show in the Orders row expander. It does send the execution
    # detail that context is for, so keep it rather than storing "{}".
    if not ctx:
        execution = {k: payload[k] for k in
                     ("fill_qty", "stop_loss", "take_profit", "portfolio_value",
                      "status", "exit_reason")
                     if payload.get(k) is not None}
        if execution:
            ctx = {"source": "trade-executor", **execution}

    entry_price = _pick(payload, "entry_price", "fill_price", default=0)
    pnl_abs = _pick(payload, "pnl_abs", "pnl")
    agent_signals = _pick(payload, "agent_signals", "agent_votes", default={})
    opened_at = _pick(payload, "timestamp_open", "executed_at")
    confidence = _pick(payload, "ensemble_confidence", "confidence", default=0)

    # `paper_trade` decides the source when the producer does not name one. The
    # executor sends paper_trade=true and no trade_source, so its paper fills
    # were being stored under the "LIVE" column default and shown as LIVE on
    # the Orders page.
    paper_trade = bool(_pick(payload, "paper_trade", default=False))
    trade_source = payload.get("trade_source") or ("PAPER" if paper_trade else "LIVE")

    # A record with no entry price and no outcome describes no executed trade.
    # There is nothing to compute a P&L from, nothing to group into a session,
    # nothing to learn from — and on the Orders page it renders as a blank row
    # that buries the real ones. 90 of these accumulated between 2026-08-18 and
    # 2026-09-08 from the field-name mismatch above, and were deleted once it
    # was fixed. Refuse them at the door so they cannot come back.
    #
    # WARNING, not debug: this only fires when a producer sends something
    # unusable, and the whole reason those 90 went unnoticed for three weeks is
    # that storing them was silent. A regression here should be loud.
    if not float(entry_price or 0) and not payload.get("outcome"):
        logger.warning(
            "Refusing trade record with no executed trade — %s %s from %s "
            "(no entry price, no outcome). Payload keys: %s",
            payload.get("action", "?"), payload.get("symbol", "?"), trade_source,
            sorted(payload.keys()),
        )
        return False

    await pool.execute(
        """
        INSERT INTO trade_records
            (trade_id, symbol, exchange, action, entry_price, exit_price,
             pnl_pct, pnl_abs, duration_minutes, ensemble_confidence,
             agent_signals, market_context, outcome, timestamp_open, timestamp_close,
             trade_source, session_id, paper_trade)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
        ON CONFLICT (trade_id) DO UPDATE SET
            exit_price=EXCLUDED.exit_price,
            pnl_pct=EXCLUDED.pnl_pct,
            pnl_abs=EXCLUDED.pnl_abs,
            outcome=EXCLUDED.outcome,
            timestamp_close=EXCLUDED.timestamp_close,
            trade_source=EXCLUDED.trade_source,
            session_id=EXCLUDED.session_id,
            paper_trade=EXCLUDED.paper_trade,
            -- The closing leg carries these and the entry cannot. Leaving them out
            -- stored every executor close with a NULL duration and a context still
            -- reading status=FILLED, so nothing recorded WHY a position was exited.
            duration_minutes=COALESCE(EXCLUDED.duration_minutes, trade_records.duration_minutes),
            market_context=COALESCE(EXCLUDED.market_context, trade_records.market_context)
        """,
        payload.get("trade_id", str(uuid.uuid4())),
        payload.get("symbol", ""),
        payload.get("exchange", "NSE"),
        payload.get("action", ""),
        float(entry_price),
        float(payload.get("exit_price", 0)) if payload.get("exit_price") else None,
        float(payload.get("pnl_pct", 0)) if payload.get("pnl_pct") is not None else None,
        float(pnl_abs) if pnl_abs is not None else None,
        int(payload.get("duration_minutes", 0)) if payload.get("duration_minutes") else None,
        float(confidence),
        json.dumps(agent_signals),
        json.dumps(ctx),
        payload.get("outcome"),
        datetime.fromisoformat(opened_at) if opened_at else datetime.now(tz=timezone.utc),
        datetime.fromisoformat(payload["timestamp_close"]) if payload.get("timestamp_close") else None,
        trade_source,
        session_id,
        paper_trade,
    )
    return True


async def _apply_trade_outcome(pool: asyncpg.Pool, payload: dict) -> bool:
    """Advance the learning loop for one CLOSED trade. Returns True if weights moved.

    Shared by both ingest paths. It has to be shared: the RabbitMQ consumer had
    this logic inline, but essentially every trade this system produces arrives
    through POST /trades instead ("bypasses RabbitMQ"), which only stored the
    row. That is why trade_records held 14,656 trades while agent_weights sat at
    its 2026-05-30 seed values with every counter on zero — the loop was not
    failing, it was never being invoked at all.
    """
    action = payload.get("action", "")
    pnl_pct = payload.get("pnl_pct")
    if pnl_pct is None or action not in ("BUY", "SELL"):
        return False                      # still open, or not a directional leg

    # "Has a pnl_pct" is not the same as "has closed". The executor's TradeOutcome
    # types pnl_pct as a Java primitive double, so an ENTRY serialises it as 0.0
    # rather than omitting it — which reads here as a break-even close. That was
    # inert only for as long as the votes failed to map: with `agent_votes` now
    # reaching compute_weight_updates, every open entry would nudge the weights
    # towards break-even on a trade whose result is not known yet.
    #
    # So require positive evidence of a close. Python producers send exit_price,
    # timestamp_close and an explicit outcome on the closing leg; the executor
    # sends none of the three at entry.
    if not any(payload.get(k) for k in ("exit_price", "timestamp_close", "outcome")):
        return False                      # entry leg — nothing to learn from yet

    symbol = payload.get("symbol", "")
    # Same two dialects as _store_trade_record. The executor's votes arrive as
    # `agent_votes`, so reading only `agent_signals` handed compute_weight_updates
    # an empty dict — every executor trade was a no-op for the weight loop.
    agent_signals = _pick(payload, "agent_signals", "agent_votes", default={})
    if isinstance(agent_signals, str):
        try:
            agent_signals = json.loads(agent_signals)
        except Exception:
            agent_signals = {}

    outcome = determine_outcome(float(pnl_pct))
    try:
        current_weights = await _load_current_weights(pool)
        new_weights = compute_weight_updates(
            current_weights, agent_signals, outcome, action,
            settings.WEIGHT_LEARNING_RATE,
        )
        await _save_weights(pool, new_weights)
        await _save_agent_hits(pool, count_agent_hits(agent_signals, outcome, action))
        logger.info(
            "Weights updated for %s trade on %s (P&L: %.2f%%) → %s",
            outcome, symbol, float(pnl_pct) * 100, new_weights,
        )
        return True
    except Exception:
        logger.exception(
            "WEIGHT UPDATE FAILED for %s on %s — trade stored, "
            "learning loop did NOT advance", action, symbol,
        )
        return False


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

                                # Store trade record
                                await _store_trade_record(_pool, payload)

                                # Advance the learning loop if the trade closed.
                                # Shared with POST /trades so both ingest paths
                                # behave identically.
                                await _apply_trade_outcome(_pool, payload)

                                # Trigger retraining if threshold reached
                                await _maybe_trigger_retrain(_pool, channel)

                            except Exception:
                                logger.exception("Feedback message error — message dropped")
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
        # Headline win-rate counts any positive close as a win, including trades
        # that resolved inside the noise band — 2026-08-04's ETERNAL closed
        # +Rs1.70 on a Rs50,000 position and scored the same as a real winner.
        # Across live paper trades since 2026-07-01 that was 12 of 52 trades
        # (23%), 7 of them logged as wins, so the headline flatters itself.
        # P&L figures were always right; only the rate was distorted.
        material = await _pool.fetch(
            """
            SELECT COUNT(*) FILTER (WHERE abs(pnl_pct) >= $1)                          AS material,
                   COUNT(*) FILTER (WHERE abs(pnl_pct) <  $1)                          AS scratch,
                   COUNT(*) FILTER (WHERE abs(pnl_pct) >= $1 AND outcome = 'WIN')      AS material_wins,
                   COUNT(*) FILTER (WHERE abs(pnl_pct) <  $1 AND outcome = 'WIN')      AS scratch_wins
            FROM trade_records
            WHERE outcome IN ('WIN','LOSS') AND pnl_pct IS NOT NULL AND trade_source = 'PAPER'
            """,
            MATERIAL_PNL_PCT,
        )
        m = dict(material[0]) if material else {}
        n_mat = int(m.get("material") or 0)
        return {
            "trade_stats": [dict(r) for r in rows],
            "material": {
                "threshold_pct": MATERIAL_PNL_PCT * 100,
                "material_trades": n_mat,
                "scratch_trades": int(m.get("scratch") or 0),
                "scratch_wins_removed": int(m.get("scratch_wins") or 0),
                "material_win_pct": (round(100.0 * int(m.get("material_wins") or 0) / n_mat, 1)
                                     if n_mat else None),
            },
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


# Trade sources whose outcomes are allowed to move live agent weights.
# Operator-run replay/backtest sweeps must NOT: their outcomes depend on which
# symbols and dates happened to be tested, and pooling them is precisely the
# contamination that left ai_engine_agent_weights fitted to a frozen June replay
# corpus (see backend/app/agents/learning.py _EXCLUDE_SIM_OUTCOMES). They are
# still stored — only the weight update is skipped.
_LEARNABLE_SOURCES = {"PAPER", "LIVE"}


@app.post("/trades")
async def post_trades(payload: list[dict]):
    """Direct insert for backtest/paper trades — bypasses RabbitMQ.

    Closed PAPER/LIVE trades also advance the weight loop here. This endpoint
    is how essentially every trade in the system actually arrives, so when it
    only stored rows, agent_weights never moved at all.
    """
    if not _pool:
        return {"error": "not ready"}
    saved = 0
    refused = 0
    learned = 0
    for record in payload:
        try:
            if not await _store_trade_record(_pool, record):
                refused += 1        # no executed trade in it — see the guard
                continue
            saved += 1
        except Exception as exc:
            logger.error("POST /trades insert error: %s", exc)
            continue
        source = str(record.get("trade_source") or "LIVE").upper()
        if source in _LEARNABLE_SOURCES:
            if await _apply_trade_outcome(_pool, record):
                learned += 1
    return {"saved": saved, "refused": refused,
            "total": len(payload), "learned": learned}


# Belt and braces behind the write guard in _store_trade_record. A trade with no
# entry price and no outcome represents no executed trade: no P&L, no win/loss,
# nothing for the Orders row to expand into. 90 such rows accumulated from the
# field-name mismatch and were deleted 2026-09-08 once it was fixed, and the
# guard now refuses new ones — so this should never match anything. It stays
# because a read filter costs nothing and the failure it covers was invisible
# for three weeks.
#
# Deliberately narrow. A genuine open position has an entry price and shows up
# normally, so this hides only the unreadable rows, nothing live.
_READABLE = "NOT (COALESCE(entry_price, 0) = 0 AND outcome IS NULL)"

_TRADE_COLUMNS = """
    trade_id, symbol, exchange, action, entry_price, exit_price,
    pnl_pct, pnl_abs, duration_minutes, ensemble_confidence,
    agent_signals, market_context, outcome, timestamp_open, timestamp_close,
    trade_source, created_at
"""


@app.get("/trades")
async def get_trades(limit: int = 500, offset: int = 0, source: str = None,
                     include_empty: bool = False):
    """Recent trades, newest first.

    `include_empty=true` keeps the unreadable rows in, for anyone auditing what
    a producer actually wrote rather than reading the trade list.
    """
    if not _pool:
        return []
    keep = "TRUE" if include_empty else _READABLE
    try:
        if source and source.upper() != "ALL":
            rows = await _pool.fetch(
                f"""
                SELECT {_TRADE_COLUMNS}
                FROM trade_records
                WHERE COALESCE(trade_source, 'LIVE') = $3 AND {keep}
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit, offset, source.upper(),
            )
        else:
            rows = await _pool.fetch(
                f"""
                SELECT {_TRADE_COLUMNS}
                FROM trade_records
                WHERE {keep}
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


@app.get("/trades/open")
async def get_open_trades(days: int = 1):
    """Trades the trade-executor opened and never closed.

    The executor holds its positions in memory, so a restart used to lose them:
    the rows stayed open in the database with nothing left alive that knew to
    close them. This is how it gets them back at boot.

    Only rows written by the executor are returned — a Python session runner
    manages its own exits and must not have them taken over from here. Also only
    the recent ones: `days=1` is today, and an older open row is a stranded
    record, not a position anyone still holds.
    """
    if not _pool:
        return []
    try:
        rows = await _pool.fetch(
            f"""
            SELECT {_TRADE_COLUMNS}, paper_trade
            FROM trade_records
            WHERE exit_price IS NULL
              AND outcome IS NULL
              AND COALESCE(entry_price, 0) > 0
              AND market_context->>'source' = 'trade-executor'
              AND timestamp_open >= NOW() - ($1::int * INTERVAL '1 day')
            ORDER BY timestamp_open ASC
            """,
            max(1, min(days, 30)),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["agent_signals"]  = json.loads(d["agent_signals"])  if d["agent_signals"]  else {}
            d["market_context"] = json.loads(d["market_context"]) if d["market_context"] else {}
            for k in ("timestamp_open", "timestamp_close", "created_at"):
                if d.get(k):
                    d[k] = d[k].isoformat()
            out.append(d)
        return out
    except Exception as exc:
        logger.error("GET /trades/open error: %s", exc)
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
