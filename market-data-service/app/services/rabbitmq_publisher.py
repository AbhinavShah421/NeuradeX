"""Publishes market data events to RabbitMQ market.data fanout exchange."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aio_pika

logger = logging.getLogger(__name__)


class RabbitMQPublisher:
    def __init__(self, rabbitmq_url: str):
        self._url = rabbitmq_url
        self._connection: aio_pika.RobustConnection | None = None
        self._channel: aio_pika.Channel | None = None

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        logger.info("RabbitMQ publisher connected")

    async def close(self) -> None:
        if self._connection and not self._connection.is_closed:
            await self._connection.close()

    def _envelope(self, payload: dict[str, Any]) -> bytes:
        return json.dumps(
            {
                "event_id": str(uuid.uuid4()),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "service": "market-data-service",
                "version": "1.0",
                "payload": payload,
            },
            default=str,
        ).encode()

    async def publish_tick(self, tick: dict[str, Any]) -> None:
        if not self._channel:
            logger.warning("Publisher not connected — skipping tick publish")
            return
        try:
            exchange = await self._channel.get_exchange("market.data")
            await exchange.publish(
                aio_pika.Message(
                    body=self._envelope(tick),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key="",
            )
        except Exception as exc:
            logger.error("Failed to publish tick for %s: %s", tick.get("symbol"), exc)

    async def publish_news_ingested(self, count: int) -> None:
        if not self._channel:
            return
        try:
            exchange = await self._channel.get_exchange("notifications")
            await exchange.publish(
                aio_pika.Message(
                    body=self._envelope({"event": "news_ingested", "article_count": count}),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key="",
            )
        except Exception as exc:
            logger.error("Failed to publish news notification: %s", exc)
