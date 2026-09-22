import contextvars
import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from backend.tools.shell_tool import redact_secrets

correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


class JsonFormatter(logging.Formatter):
    """
    Structured JSON log formatter with automated credential & secret redaction.
    """

    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", None) or correlation_id_ctx.get() or "system"
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
            "correlation_id": cid,
        }
        if record.exc_info:
            log_entry["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(log_entry)


def get_correlation_id() -> str:
    """Returns the current request correlation ID, or a newly generated one."""
    cid = correlation_id_ctx.get()
    if not cid:
        cid = str(uuid4())[:8]
        correlation_id_ctx.set(cid)
    return cid


class CorrelationIdFilter(logging.Filter):
    """Logging filter that injects correlation_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_ctx.get() or "system"
        return True


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Middleware that attaches a unique correlation ID to each HTTP request,
    sets it in the contextvar, logs request start and completion with timing,
    and returns X-Correlation-ID in response headers.
    """

    async def dispatch(self, request: Request, call_next):
        req_cid = request.headers.get("X-Correlation-ID") or str(uuid4())[:8]
        token = correlation_id_ctx.set(req_cid)
        start_time = time.perf_counter()

        logger = logging.getLogger("agentguard.http")
        logger.info(f"[{req_cid}] -> {request.method} {request.url.path}")

        try:
            response: Response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            response.headers["X-Correlation-ID"] = req_cid
            logger.info(
                f"[{req_cid}] <- {request.method} {request.url.path} {response.status_code} ({duration_ms}ms)"
            )
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                f"[{req_cid}] <- {request.method} {request.url.path} ERROR: {str(exc)} ({duration_ms}ms)"
            )
            raise
        finally:
            correlation_id_ctx.reset(token)
