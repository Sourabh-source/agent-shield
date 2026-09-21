import logging
from typing import List
from fastapi import APIRouter, HTTPException, status

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.models.workflow import FinalReportData, WorkflowEvent, WorkflowState

logger = logging.getLogger("agentguard.api.status")
router = APIRouter(tags=["status"])


@router.get("/workflow/{workflow_id}/status", response_model=WorkflowState)
def get_workflow_status(workflow_id: str) -> WorkflowState:
    """
    Returns the complete current workflow state including:
    - workflow_id
    - repository
    - task
    - current_step
    - overall_status
    - steps
    - events
    - retries
    - verification status
    - final result if available
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )
    return workflow


@router.get("/workflow/{workflow_id}/events", response_model=List[WorkflowEvent])
def get_workflow_events(workflow_id: str) -> List[WorkflowEvent]:
    """
    Returns the ordered list of workflow events, consumed by Member 1's frontend
    for the live execution timeline.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )
    return workflow.events


@router.get("/workflow/{workflow_id}/report", response_model=FinalReportData)
def get_workflow_final_report(workflow_id: str) -> FinalReportData:
    """
    Returns evidence-backed final report data summarizing all steps, verification
    decisions, recovery history, and metrics.
    """
    orchestrator = WorkflowOrchestrator()
    report = orchestrator.get_final_report_data(workflow_id)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )
    return report


@router.get("/workflows", response_model=List[WorkflowState])
def list_workflows() -> List[WorkflowState]:
    """
    Lists all active or past workflows.
    """
    return workflow_store.list_all()
