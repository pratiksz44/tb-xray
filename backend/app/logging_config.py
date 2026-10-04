"""Structured (JSON lines) logging for the API, standard library only.

Every log line is one JSON object, e.g.
    {"time": "2026-10-05T09:12:03.512Z", "level": "INFO", "logger": "app.main", "message": "request",
     "request_id": "3f2a...", "method": "POST", "path": "/api/predict", "status": 200, "duration_ms": 1834.2}

Usage:
    setup_logging("INFO")                      # once, at import of app.main
    app.middleware("http")(log_requests)       # one "request" line per HTTP request, with a request id;
                                               # also sets X-Request-ID and Server-Timing response headers
    logger.info("prediction", extra={"tb_probability": 0.91})   # extra fields become JSON keys

Never log image bytes or upload/ZIP file names: they can contain patient information.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime

from fastapi import Request, Response

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra=` and is logged as a field.
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")[:-6] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if (request_id := request_id_var.get()) is not None:
            entry["request_id"] = request_id
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Send all logs (app, uvicorn, libraries) to stdout as JSON lines."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own plain-text handlers; route its logs through the JSON handler instead.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # log_requests below replaces uvicorn's access log (and adds the request id + duration).
    logging.getLogger("uvicorn.access").disabled = True


_request_logger = logging.getLogger("app.request")


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Tag the request with an id (nginx's X-Request-ID, or a new one) and log one line when it finishes."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    fields: dict[str, object] = {"method": request.method, "path": request.url.path}
    try:
        response = await call_next(request)
    except Exception:
        fields["duration_ms"] = _elapsed_ms(start)
        _request_logger.exception("request failed", extra=fields)
        raise
    else:
        fields |= {"status": response.status_code, "duration_ms": _elapsed_ms(start)}
        # Health checks run every few seconds; keep them out of normal logs.
        level = logging.DEBUG if request.url.path == "/api/health" else logging.INFO
        _request_logger.log(level, "request", extra=fields)
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={fields['duration_ms']}"  # shown by the frontend
        return response
    finally:
        request_id_var.reset(token)
