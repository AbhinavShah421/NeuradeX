"""RabbitMQ consumer for model.retrain queue."""

import asyncio
import json
import logging

import aio_pika

logger = logging.getLogger(__name__)


async def run_consumer(rabbitmq_url: str, retrain_callback) -> None:
    while True:
        try:
            conn = await aio_pika.connect_robust(rabbitmq_url)
            channel = await conn.channel()
            await channel.set_qos(prefetch_count=1)

            queue = await channel.get_queue("model.retrain")

            async with queue.iterator() as q_iter:
                async for message in q_iter:
                    async with message.process():
                        try:
                            payload = json.loads(message.body)
                            trigger = payload.get("trigger", "manual")
                            symbols = payload.get("symbols", [])
                            logger.info("Retrain triggered by '%s' for %d symbols", trigger, len(symbols))
                            asyncio.create_task(retrain_callback(trigger, symbols))
                        except Exception as exc:
                            logger.error("Error processing retrain message: %s", exc)

        except Exception as exc:
            logger.error("Consumer disconnected: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)
