# Canonical source for every service's app/elk_logger.py.
# Do not edit the per-service copies directly — edit this file, then run
# `python scripts/sync_shared_python.py` to propagate the change.
import json, logging, os, queue, threading, time, traceback
from datetime import datetime, timezone
from typing import Optional
import requests as _requests
from pythonjsonlogger import jsonlogger

_SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown-service")
_INDEX_PREFIX = "neuradex-logs"
_FLUSH_INTERVAL = 2.0
_BATCH_SIZE = 50
_QUEUE_MAX = 2000

def _es_url() -> str:
    return os.getenv("ELASTICSEARCH_URL", "http://elasticsearch:9200")

class _ElasticsearchHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="es-log-worker", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self._build_doc(record))
        except queue.Full:
            pass

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        super().close()

    def _build_doc(self, record: logging.LogRecord) -> dict:
        doc: dict = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": _SERVICE_NAME,
        }
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_") and key not in doc:
                doc[key] = val
        if record.exc_info:
            doc["exception"] = "".join(traceback.format_exception(*record.exc_info))
        return doc

    def _worker(self) -> None:
        batch: list[dict] = []
        last_flush = time.monotonic()
        while not self._stop.is_set():
            try:
                batch.append(self._queue.get(timeout=_FLUSH_INTERVAL))
                if len(batch) >= _BATCH_SIZE:
                    self._flush(batch); batch = []; last_flush = time.monotonic()
            except queue.Empty:
                pass
            if batch and (time.monotonic() - last_flush) >= _FLUSH_INTERVAL:
                self._flush(batch); batch = []; last_flush = time.monotonic()
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
            lines = []
            for doc in batch:
                lines.append(json.dumps({"index": {"_index": index}}))
                lines.append(json.dumps(doc, default=str))
            _requests.post(f"{_es_url()}/_bulk", data="\n".join(lines)+"\n",
                           headers={"Content-Type": "application/x-ndjson"}, timeout=5)
        except Exception:
            pass

_es_handler: Optional[_ElasticsearchHandler] = None
_configured = False

def setup_logging(level: int = logging.INFO) -> None:
    global _es_handler, _configured
    if _configured:
        return
    _configured = True
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"))
    _es_handler = _ElasticsearchHandler()
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stdout_handler)
    root.addHandler(_es_handler)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "elasticsearch", "aio_pika"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
