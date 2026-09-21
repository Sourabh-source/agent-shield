import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import status_router, workflow_router
from backend.config import settings
from backend.observability import ObservabilityMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agentguard.main")

app = FastAPI(
    title="AgentGuard Backend (Member 2)",
    description=(
        "Evidence-Gated Self-Healing Agent Workflow Engine. "
        "Orchestrates AI planning, tool execution, evidence collection, and recovery."
    ),
    version="1.0.0",
)

# Observability middleware: tracing, correlation IDs, and timing
app.add_middleware(ObservabilityMiddleware)

# CORS middleware for Member 1 frontend integration
allow_creds = "*" not in settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(workflow_router)
app.include_router(status_router)


@app.get("/")
def root():
    return {
        "service": "AgentGuard Backend",
        "module": "Member 2 - Agent Planner & Execution Engine",
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

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
