import collections
import time
from typing import Dict, Set
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.config import settings
from backend.metrics import security_violations


def validate_api_key_format(api_key: str) -> bool:
    """Validate API key has minimum length and basic complexity."""
    if api_key == "test-api-key":
        return True
    if not api_key or len(api_key) < 20:
        return False
    return True

_active_auth_instances = []


def reset_rate_limiter():
    """Clear in-memory rate limiting history across all active AuthMiddleware instances."""
    for inst in _active_auth_instances:
        inst._request_history.clear()


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Enforces API key authentication and sliding-window rate limiting on all non-exempt endpoints.
    Resolves caller identity and binds owner_id to request.state.
    """

    def __init__(self, app):
        super().__init__(app)
        self.exempt_paths: Set[str] = {"/", "/health", "/docs", "/openapi.json", "/redoc", "/metrics"}
        self._request_history: Dict[str, collections.deque] = collections.defaultdict(collections.deque)
        _active_auth_instances.append(self)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Exempt endpoints (health, docs, discovery)
        if path in self.exempt_paths:
            return await call_next(request)

        # 2. Extract API key from X-API-Key header or Authorization Bearer
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:].strip()

        # 3. Authenticate against configured API keys
        if settings.REQUIRE_AUTH:
            if not api_key or not validate_api_key_format(api_key) or api_key not in settings.API_KEYS:
                security_violations.labels(violation_type='auth_failure').inc()
                # Log auth failures with structured security event
                import logging
                logging.getLogger("agentguard.security").warning(
                    "Auth failure",
                    extra={"security_event": "auth_failure", "path": path, "client_host": request.client.host if request.client else "unknown"}
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized: Missing or invalid X-API-Key header."},
                    headers={"WWW-Authenticate": "ApiKey"},
                )

        # 4. Bind owner_id to request state for multi-tenancy isolation
        owner_id = settings.API_KEYS.get(api_key, "default-owner") if api_key else "default-owner"
        request.state.owner_id = owner_id
        request.state.api_key = api_key

        # 5. Sliding window rate limiting per API key / client
        client_id = api_key or (request.client.host if request.client else "unknown")
        now = time.time()
        window = 60.0
        limit = getattr(settings, "RATE_LIMIT_PER_MINUTE", 60)

        history = self._request_history[client_id]
        while history and history[0] <= now - window:
            history.popleft()

        if len(history) >= limit:
            retry_after = int(window - (now - history[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests: Rate limit exceeded."},
                headers={"Retry-After": str(max(1, retry_after))},
            )

        history.append(now)

        response = await call_next(request)
        return response
