import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Request, status

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.models.workflow import FinalReportData, WorkflowEvent, WorkflowState

logger = logging.getLogger("agentguard.api.status")
router = APIRouter(tags=["status"])


def verify_workflow_ownership(workflow: WorkflowState, request: Request, workflow_id: str):
    """Enforces multi-tenancy isolation. Returns 404 on ownership mismatch to prevent enumeration."""
    caller_owner = getattr(request.state, "owner_id", "default-owner")
    wf_owner = getattr(workflow, "owner_id", "default-owner")
    if caller_owner != "admin" and wf_owner and wf_owner != caller_owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )


@router.get("/workflow/{workflow_id}/status", response_model=WorkflowState)
def get_workflow_status(workflow_id: str, request: Request) -> WorkflowState:
    """
    Returns the complete current workflow state.
    Guarded by multi-tenant ownership.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    verify_workflow_ownership(workflow, request, workflow_id)
    return workflow


@router.get("/workflow/{workflow_id}/events", response_model=List[WorkflowEvent])
def get_workflow_events(workflow_id: str, request: Request) -> List[WorkflowEvent]:
    """
    Returns the ordered list of workflow events.
    Guarded by multi-tenant ownership.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    verify_workflow_ownership(workflow, request, workflow_id)
    return workflow.events


@router.get("/workflow/{workflow_id}/report", response_model=FinalReportData)
def get_workflow_final_report(workflow_id: str, request: Request) -> FinalReportData:
    """
    Returns evidence-backed final report data.
    Guarded by multi-tenant ownership.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    verify_workflow_ownership(workflow, request, workflow_id)

    orchestrator = WorkflowOrchestrator()
    report = orchestrator.get_final_report_data(workflow_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )
    return report


@router.get("/workflows", response_model=List[WorkflowState])
def list_workflows(
    request: Request,
    limit: Optional[int] = Query(None, ge=1, le=100, description="Maximum number of workflows to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> List[WorkflowState]:
    """
    Lists workflows for the authenticated tenant with pagination support.
    """
    owner_id = getattr(request.state, "owner_id", "default-owner")
    return workflow_store.list_all(limit=limit, offset=offset, owner_id=owner_id)
