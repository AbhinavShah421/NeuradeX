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

# Standard LogRecord attributes. These must never be shipped: they are logging
# internals, not caller context.
#
# The previous guard tested `key not in logging.LogRecord.__dict__`, which is the
# CLASS dict (methods such as getMessage), not the per-record instance attribute
# names set in LogRecord.__init__. So every one of these leaked into each
# document — ~20 junk fields per log line. Worse, `args` holds the %-format
# arguments and so has no stable type; Elasticsearch dynamically mapped it as
# `long` from an early numeric value, after which every log line with string
# args was REJECTED at index time and silently vanished from Kibana.
_RESERVED = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


class _ElasticsearchHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._stop = threading.Event()
        self._revive_lock = threading.Lock()
        self._closed_for_good = False
        self._thread = threading.Thread(target=self._worker, name="es-log-worker", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self._build_doc(record))
        except queue.Full:
            pass
        # Self-heal: libraries that reconfigure logging (dictConfig and
        # friends) may close() this handler while it stays attached to the
        # root logger — the worker then exits cleanly and every later record
        # is silently dropped on a full queue. Ensemble-engine shipped ZERO
        # documents for months this way. If the worker is gone but we are
        # still receiving records, we are evidently not shutting down: revive.
        if not self._thread.is_alive() and not self._closed_for_good:
            with self._revive_lock:
                if not self._thread.is_alive():
                    self._stop.clear()
                    self._thread = threading.Thread(
                        target=self._worker, name="es-log-worker", daemon=True)
                    self._thread.start()
                    print(f"elk_logger[{_SERVICE_NAME}]: es-log-worker was dead — revived",
                          file=__import__("sys").stderr)

    def close(self) -> None:
        # Interpreter shutdown (logging.shutdown via atexit) must win over the
        # revive logic; a mid-run close() from a logging reconfig must not.
        self._closed_for_good = __import__("sys").is_finalizing()
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
            if key not in _RESERVED and not key.startswith("_") and key not in doc:
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

    _last_err_report = 0.0
    _ERR_REPORT_EVERY = 300.0  # rate-limit failure reports to stderr

    def _flush(self, batch: list[dict]) -> None:
        # Failures must be *visible*: a silent `except: pass` here cost weeks
        # of missing logs. stdout still has every record, so report the
        # shipping failure to stderr (rate-limited) and drop the batch.
        try:
            index = f"{_INDEX_PREFIX}-{datetime.now().strftime('%Y.%m.%d')}"
            lines = []
            for doc in batch:
                lines.append(json.dumps({"index": {"_index": index}}))
                lines.append(json.dumps(doc, default=str))
            resp = _requests.post(f"{_es_url()}/_bulk", data="\n".join(lines)+"\n",
                                  headers={"Content-Type": "application/x-ndjson"}, timeout=5)
            err = None
            if resp.status_code >= 300:
                err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                body = resp.json()
                if body.get("errors"):
                    first = next((it["index"].get("error") for it in body.get("items", [])
                                  if it.get("index", {}).get("error")), None)
                    err = f"bulk item errors, first: {first}"
            if err:
                self._report_err(err)
        except Exception as exc:
            self._report_err(f"{type(exc).__name__}: {exc}")

    def _report_err(self, msg: str) -> None:
        import sys
        now = time.monotonic()
        if now - self._last_err_report >= self._ERR_REPORT_EVERY:
            _ElasticsearchHandler._last_err_report = now
            print(f"elk_logger[{_SERVICE_NAME}]: ES shipping failed — {msg}", file=sys.stderr)

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
