"""Collects all 5 agent signals for a symbol within a timeout window."""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Optional

import aio_pika

logger = logging.getLogger(__name__)

EXPECTED_AGENTS = {"technical", "sentiment", "macro", "pattern", "rl"}


class AgentSignalCollector:
    """
    Subscribes to agent.signals queue and groups signals by symbol.
    Fires callback when all 5 agents have signaled OR timeout is reached.
    """

    def __init__(self, timeout_seconds: float = 5.0):
        self._timeout = timeout_seconds
        self._pending: dict[str, dict] = defaultdict(dict)       # symbol → {agent: signal}
        self._timestamps: dict[str, float] = {}                   # symbol → first signal ts
        self._callbacks: list = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None

    def on_decision_ready(self, callback) -> None:
        self._callbacks.append(callback)

    async def _fire_callbacks(self, symbol: str, signals: dict) -> None:
        for cb in self._callbacks:
            try:
                await cb(symbol, signals)
            except Exception as exc:
                logger.error("Ensemble callback error for %s: %s", symbol, exc)

    async def _flush_expired(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            async with self._lock:
                expired = [
                    sym for sym, ts in self._timestamps.items()
                    if now - ts >= self._timeout
                ]
                for sym in expired:
                    signals = self._pending.pop(sym, {})
                    self._timestamps.pop(sym, None)
                    if signals:
                        logger.info(
                            "Timeout for %s — got %d/%d agents: %s",
                            sym, len(signals), len(EXPECTED_AGENTS), list(signals.keys()),
                        )
                        asyncio.create_task(self._fire_callbacks(sym, signals))

    async def handle_signal(self, message: aio_pika.IncomingMessage) -> None:
        async with message.process():
            try:
                body = json.loads(message.body)
                payload = body.get("payload", {})
                symbol = payload.get("symbol")
                agent = payload.get("agent")
                if not symbol or not agent:
                    return

                signals = None
                async with self._lock:
                    if symbol not in self._timestamps:
                        self._timestamps[symbol] = time.monotonic()
                    self._pending[symbol][agent] = payload

                    if EXPECTED_AGENTS.issubset(self._pending[symbol].keys()):
                        signals = self._pending.pop(symbol)
                        self._timestamps.pop(symbol, None)

                logger.debug("Got signal: %s from %s → %s", agent, symbol, payload.get("signal"))

                if signals is not None:
                    logger.info("All %d agents received for %s — computing ensemble", len(EXPECTED_AGENTS), symbol)
                    asyncio.create_task(self._fire_callbacks(symbol, signals))

            except Exception as exc:
                logger.error("Signal parse error: %s", exc)

    async def start(self, rabbitmq_url: str) -> None:
        self._flush_task = asyncio.create_task(self._flush_expired(), name="flush-expired")
        while True:
            try:
                connection = await aio_pika.connect_robust(rabbitmq_url)
                async with connection:
                    channel = await connection.channel()
                    await channel.set_qos(prefetch_count=50)
                    queue = await channel.get_queue("agent.signals")
                    await queue.consume(self.handle_signal)
                    await asyncio.Future()
            except Exception as exc:
                logger.error("Signal collector lost connection: %s — retry 5s", exc)
                await asyncio.sleep(5)
