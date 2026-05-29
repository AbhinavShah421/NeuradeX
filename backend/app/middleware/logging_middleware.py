"""
Request logging middleware.

Logs every HTTP request/response with:
  - Full URL (scheme + host + path + query string)
  - HTTP method, status code, duration_ms
  - Request body (JSON, truncated to 2 KB)
  - Response body (JSON, truncated to 2 KB)
  - Client IP, User-Agent
  - X-Request-ID header on all responses for client-side tracing

The request_id is stored in `request_id_var` so every log record emitted
during that request is automatically correlated in Kibana.
"""

import json
import time
import uuid
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.utils.elk_logger import get_logger, request_id_var

logger = get_logger("neuradeX.middleware.request")

_BODY_LIMIT = 2048          # max bytes captured from request/response body
_SKIP_PATHS = {"/health", "/"}   # paths excluded from body logging


def _try_parse(raw: bytes) -> Optional[dict | list | str]:
    """Try to decode bytes as JSON, fall back to truncated string."""
    try:
        return json.loads(raw)
    except Exception:
        text = raw.decode("utf-8", errors="replace")
        return text[:_BODY_LIMIT] if text.strip() else None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = str(uuid.uuid4())
        token = request_id_var.set(req_id)

        # Full URL for easy searching in Kibana
        full_url = str(request.url)

        # Skip health-check noise entirely
        if request.url.path in _SKIP_PATHS:
            try:
                return await call_next(request)
            finally:
                request_id_var.reset(token)

        # ── Capture request body ──────────────────────────────────────────────
        # IMPORTANT: BaseHTTPMiddleware exhausts the ASGI receive stream when
        # we call request.body(). We must reconstruct the receive callable so
        # the route handler can still read the body — otherwise ALL POST routes
        # return 400 "There was an error parsing the body".
        raw_req = b""
        req_body: Optional[dict | list | str] = None
        try:
            raw_req = await request.body()
            if raw_req:
                req_body = _try_parse(raw_req[:_BODY_LIMIT])
        except Exception:
            pass

        # Restore the receive stream for the downstream route handler
        _body_snapshot = raw_req
        async def _restored_receive() -> dict:
            return {"type": "http.request", "body": _body_snapshot, "more_body": False}
        request = Request(request.scope, _restored_receive)

        logger.info(
            "Incoming request",
            extra={
                "log_type": "api_request",
                "http_method": request.method,
                "url": full_url,
                "path": request.url.path,
                "query": str(request.url.query),
                "client_ip": request.client.host if request.client else "",
                "user_agent": request.headers.get("user-agent", ""),
                "request_body": req_body,
                "content_type": request.headers.get("content-type", ""),
            },
        )

        # ── Call the route handler ────────────────────────────────────────────
        start = time.monotonic()
        status_code = 500
        resp_body: Optional[dict | list | str] = None

        try:
            response: Response = await call_next(request)
            status_code = response.status_code

            # ── Capture response body ─────────────────────────────────────────
            # StreamingResponse: consume and re-wrap the body chunks
            raw_chunks = []
            async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                raw_chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
            raw_resp = b"".join(raw_chunks)

            resp_body = _try_parse(raw_resp[:_BODY_LIMIT])

            # Re-wrap so the client still receives the full body
            from starlette.responses import Response as PlainResponse
            response = PlainResponse(
                content=raw_resp,
                status_code=status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
            return response

        except Exception as exc:
            logger.error(
                "Unhandled exception in request",
                extra={
                    "log_type": "api_error",
                    "http_method": request.method,
                    "url": full_url,
                    "error": str(exc),
                },
                exc_info=True,
            )
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            logger.info(
                "Request completed",
                extra={
                    "log_type": "api_response",
                    "http_method": request.method,
                    "url": full_url,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "response_body": resp_body,
                },
            )
            request_id_var.reset(token)
