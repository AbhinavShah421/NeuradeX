"""Declare all RabbitMQ exchanges and queues per REQUIREMENTS.md Section 5."""

import asyncio
import logging

import aio_pika

logger = logging.getLogger(__name__)

EXCHANGES = [
    ("market.data",       "fanout"),
    ("agent.signals",     "direct"),
    ("ensemble.decision", "direct"),
    ("risk.validated",    "direct"),
    ("trade.orders",      "direct"),
    ("trade.outcomes",    "fanout"),
    ("model.retrain",     "direct"),
    ("notifications",     "fanout"),
]

# (queue_name, exchange_name, routing_key)
QUEUE_BINDINGS = [
    # market.data fanout → one queue per agent
    ("market.data.technical",  "market.data",       ""),
    ("market.data.sentiment",  "market.data",       ""),
    ("market.data.macro",      "market.data",       ""),
    ("market.data.pattern",    "market.data",       ""),
    ("market.data.rl",         "market.data",       ""),
    # agent signals
    ("agent.signals",          "agent.signals",     "technical"),
    ("agent.signals",          "agent.signals",     "sentiment"),
    ("agent.signals",          "agent.signals",     "macro"),
    ("agent.signals",          "agent.signals",     "pattern"),
    ("agent.signals",          "agent.signals",     "rl"),
    # ensemble → risk → executor
    ("ensemble.decision",      "ensemble.decision", "decision"),
    ("risk.validated",         "risk.validated",    "validated"),
    ("trade.orders",           "trade.orders",      "order"),
    # trade outcomes fanout → feedback
    ("trade.outcomes.feedback","trade.outcomes",    ""),
    # `trade.outcomes.rl` was declared here and bound to the fanout, but no
    # consumer was ever written for it — rl-agent subscribes to market.data.rl
    # only and has no outcome-handling code at all. A bound queue with zero
    # consumers is worse than no queue: it accepts every published message and
    # silently discards it, while the topology reads as if RL were learning from
    # trade results. It is also redundant — RL *does* learn from outcomes, via
    # the backend Q-table (`ai_engine:rl_qtable`, updated in
    # backend/app/agents/learning.py record_outcome). Removed 2026-08-17.
    # If online RL learning is ever built into the microservice, re-add the
    # binding together with the consumer, not before.
    # retraining + notifications
    ("model.retrain",          "model.retrain",     "retrain"),
    ("notifications.all",      "notifications",     ""),
]


async def setup_topology(rabbitmq_url: str, max_retries: int = 10) -> None:
    for attempt in range(1, max_retries + 1):
        try:
            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                declared_exchanges: dict[str, aio_pika.Exchange] = {}

                for name, kind in EXCHANGES:
                    ex_type = getattr(aio_pika.ExchangeType, kind.upper())
                    ex = await channel.declare_exchange(name, ex_type, durable=True)
                    declared_exchanges[name] = ex
                    logger.info("Exchange declared: %s (%s)", name, kind)

                declared_queues: dict[str, aio_pika.Queue] = {}
                for queue_name, exchange_name, routing_key in QUEUE_BINDINGS:
                    if queue_name not in declared_queues:
                        q = await channel.declare_queue(queue_name, durable=True)
                        declared_queues[queue_name] = q
                    await declared_queues[queue_name].bind(
                        declared_exchanges[exchange_name], routing_key=routing_key
                    )
                    logger.info("Queue %s bound to %s (key=%r)", queue_name, exchange_name, routing_key)

            logger.info("RabbitMQ topology setup complete")
            return
        except Exception as exc:
            logger.warning("RabbitMQ setup attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                await asyncio.sleep(min(2 ** attempt, 30))
            else:
                raise
