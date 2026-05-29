"""
ELK Stack logger — structured JSON logging to Elasticsearch via a background thread.

All log records are buffered in a thread-safe queue and bulk-indexed to
Elasticsearch by a daemon thread, so logging never blocks the async event loop.
Falls back to stdout-only if Elasticsearch is unreachable.

Index naming convention:
  neuradeX-logs-YYYY.MM.DD  (single index, differentiated by `log_type` field)

Context variable `request_id_var` is set per-request by RequestLoggingMiddleware
and automatically stamped on every log record.
"""

import json
import logging
import os
import queue
import threading
import time
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

import requests as _requests
from pythonjsonlogger import jsonlogger

# Per-request correlation ID, set by the middleware
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_INDEX_PREFIX = "neuradex-logs"
_FLUSH_INTERVAL = 2.0      # seconds between forced flushes
_BATCH_SIZE = 50           # flush when batch reaches this size
_QUEUE_MAX = 5000          # drop logs if queue exceeds this (avoids memory blow-up)


def _es_url() -> str:
    return os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")


class _ElasticsearchHandler(logging.Handler):
    """
    Non-blocking logging handler that bulk-indexes records to Elasticsearch.
    Uses a daemon thread + bounded queue so the event loop is never touched.
    """

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="es-log-worker", daemon=True)
        self._thread.start()

    # ── logging.Handler interface ─────────────────────────────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        try:
            doc = self._build_doc(record)
            self._queue.put_nowait(doc)
        except queue.Full:
            pass  # silently drop — never block the caller

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        super().close()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _build_doc(self, record: logging.LogRecord) -> dict:
        doc: dict = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "neuradeX-backend",
            "request_id": request_id_var.get(""),
        }

        # Attach any extra kwargs passed by the caller
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_") and key not in doc:
                doc[key] = val

        if record.exc_info:
            doc["exception"] = "".join(traceback.format_exception(*record.exc_info))

        return doc

    def _worker(self) -> None:
        """Drain the queue and bulk-index to Elasticsearch."""
        batch: list[dict] = []
        last_flush = time.monotonic()

        while not self._stop.is_set():
            try:
                doc = self._queue.get(timeout=_FLUSH_INTERVAL)
                batch.append(doc)
                if len(batch) >= _BATCH_SIZE:
                    self._flush(batch)
                    batch = []
                    last_flush = time.monotonic()
            except queue.Empty:
                pass

            if batch and (time.monotonic() - last_flush) >= _FLUSH_INTERVAL:
                self._flush(batch)
                batch = []
                last_flush = time.monotonic()

        # Drain remaining records on shutdown
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._flush(batch)

    def _flush(self, batch: list[dict]) -> None:
        try:
            index = f"{_INDEX_PREFIX}-{datetime.now().strftime('%Y.%m.%d')}"
            body_lines = []
            for doc in batch:
                body_lines.append(json.dumps({"index": {"_index": index}}))
                body_lines.append(json.dumps(doc, default=str))
            body = "\n".join(body_lines) + "\n"

            _requests.post(
                f"{_es_url()}/_bulk",
                data=body,
                headers={"Content-Type": "application/x-ndjson"},
                timeout=5,
            )
        except Exception:
            pass  # Elasticsearch down — logs already printed to stdout, just skip ES


# ── Public API ────────────────────────────────────────────────────────────────

_es_handler: Optional[_ElasticsearchHandler] = None
_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """
    Replace the root logger's basic config with JSON-to-stdout + Elasticsearch.
    Call once at application startup (main.py).
    """
    global _es_handler, _configured
    if _configured:
        return
    _configured = True

    # JSON formatter for stdout — structured so Docker logs are also parseable
    stdout_handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    stdout_handler.setFormatter(formatter)

    # Elasticsearch handler
    _es_handler = _ElasticsearchHandler()

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stdout_handler)
    root.addHandler(_es_handler)

    # Suppress noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger. Use instead of logging.getLogger()."""
    return logging.getLogger(name)
