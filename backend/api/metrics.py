from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest

router = APIRouter(prefix="/metrics", tags=["metrics"])

@router.get("", response_class=PlainTextResponse)
def get_metrics():
    """Returns Prometheus metrics."""
    return generate_latest()
