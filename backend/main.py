import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import status_router, workflow_router, metrics_router
from backend.api.account import account_router
from backend.config import settings
from backend.middleware.auth import AuthMiddleware
from backend.observability import JsonFormatter, ObservabilityMiddleware

# Configure structured JSON logging with secret redaction
_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.basicConfig(
    level=logging.INFO,
    handlers=[_handler],
)
logger = logging.getLogger("agentguard.main")

app = FastAPI(
    title="AgentGuard Backend",
    description=(
        "Evidence-Gated Self-Healing Agent Workflow Engine. "
        "Orchestrates AI planning, tool execution, evidence collection, and recovery."
    ),
    version="1.0.0",
)

# 1. Observability middleware: tracing, correlation IDs, and timing
app.add_middleware(ObservabilityMiddleware)

# 2. AuthN / AuthZ & Rate Limiting Middleware
app.add_middleware(AuthMiddleware)

# 3. CORS middleware with safe defaults
allow_creds = "*" not in settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=allow_creds,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Legacy routes (backward compat)
app.include_router(workflow_router, prefix="/workflow")
# Versioned routes
app.include_router(workflow_router, prefix="/v1/workflow")
app.include_router(status_router)
app.include_router(metrics_router)
app.include_router(account_router)


@app.get("/")
def root():
    return {
        "service": "AgentGuard Backend",
        "module": "AgentGuard - Orchestrator & Execution Engine",
        "status": "healthy",
        "docs_url": "/docs",
        "endpoints": {
            "start_workflow": "POST /workflow/start",
            "get_status": "GET /workflow/{workflow_id}/status",
            "execute_workflow": "POST /workflow/{workflow_id}/execute",
            "cancel_workflow": "POST /workflow/{workflow_id}/cancel",
            "resume_workflow": "POST /workflow/{workflow_id}/resume",
            "workflow_report": "GET /workflow/{workflow_id}/report",
            "verify_step": "POST /workflow/{workflow_id}/verify",
            "workflow_events": "GET /workflow/{workflow_id}/events",
            "list_workflows": "GET /workflows",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
