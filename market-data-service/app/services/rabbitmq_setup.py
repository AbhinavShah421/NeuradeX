"""Declare all RabbitMQ exchanges and queues per REQUIREMENTS.md Section 5."""

import asyncio
import json
import logging
import urllib.parse

import aio_pika
import httpx

logger = logging.getLogger(__name__)

EXCHANGES = [
    ("market.data",       "fanout"),
    ("agent.signals",     "direct"),
    # backend ensemble -> ensemble-engine (MLflow gate) -> risk
    ("ensemble.raw",      "direct"),
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
    ("market.data.sentiment",  "market.data",       ""),
    # agent signals — only `sentiment` remains a microservice; the
    # technical/macro/pattern/rl agents were removed 2026-08-18 because
    # they duplicated in-process backend agents and fed an aggregation
    # the backend now performs itself.
    ("agent.signals",          "agent.signals",     "sentiment"),
    # backend ensemble -> ensemble-engine
    ("ensemble.raw",           "ensemble.raw",      "decision"),
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

# Queue policies. AMQP can only set these as queue arguments at declare time, and
# changing the arguments of a queue that already exists fails with
# PRECONDITION_FAILED — so they go through the management API instead, which is
# idempotent and safe to re-apply on every boot.
#
# agent.signals is the reason this exists. sentiment-agent still publishes to it,
# but its only consumer (ensemble-engine's legacy AgentSignalCollector) was turned
# off on 2026-08-18 when the duplicate agent microservices were removed. The queue
# had grown to ~1,600 messages with nothing draining it and no upper bound, inside
# a broker capped at 256M.
#
# max-length is what actually bounds it. message-ttl was measured on 2026-08-18 to
# expire nothing at all while no consumer is attached — a 60s TTL left 2h-old
# messages sitting at the head — because a classic queue only evaluates head
# expiry on delivery. The TTL is kept anyway: it costs nothing, and if
# AGGREGATE_AGENT_SIGNALS is ever turned back on it stops the collector from
# aggregating hour-stale signals as if they were live.
#
# 1,000 is a debug buffer, not a work queue: enough to inspect what sentiment-agent
# is emitting, small enough to be irrelevant to broker memory.
POLICIES = [
    {
        "name": "agent-signals-bounded",
        "pattern": r"^agent\.signals$",
        "apply-to": "queues",
        "priority": 1,
        "definition": {
            "message-ttl": 3_600_000,   # 1 hour
            "max-length": 1_000,
            "overflow": "drop-head",    # discard oldest, keep the freshest signals
        },
    },
]


async def apply_policies(rabbitmq_url: str) -> None:
    """Apply queue policies via the management API (port 15672).

    Best-effort: a broker without the management plugin, or unreachable HTTP, must
    not stop the service booting — the topology itself is already declared over AMQP.
    """
    parsed = urllib.parse.urlparse(rabbitmq_url)
    host = parsed.hostname or "rabbitmq"
    user = urllib.parse.unquote(parsed.username or "guest")
    password = urllib.parse.unquote(parsed.password or "guest")
    vhost = urllib.parse.quote(parsed.path.lstrip("/") or "/", safe="")

    try:
        async with httpx.AsyncClient(auth=(user, password), timeout=10.0) as client:
            for policy in POLICIES:
                body = {k: v for k, v in policy.items() if k != "name"}
                resp = await client.put(
                    f"http://{host}:15672/api/policies/{vhost}/{policy['name']}",
                    content=json.dumps(body),
                    headers={"content-type": "application/json"},
                )
                if resp.status_code in (201, 204):
                    logger.info("Queue policy applied: %s → %s",
                                policy["name"], policy["definition"])
                else:
                    logger.warning("Queue policy %s rejected: %s %s",
                                   policy["name"], resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Could not apply queue policies (topology is unaffected): %s", exc)


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
            await apply_policies(rabbitmq_url)
            return
        except Exception as exc:
            logger.warning("RabbitMQ setup attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                await asyncio.sleep(min(2 ** attempt, 30))
            else:
                raise
