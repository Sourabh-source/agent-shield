import logging
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, status

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.config import settings
from backend.models.workflow import (
    ExecuteStepRequest,
    StepStatus,
    VerifyStepRequest,
    WorkflowCreateRequest,
    WorkflowCreateResponse,
    WorkflowState,
    WorkflowStatus,
)

logger = logging.getLogger("agentguard.api.workflow")
router = APIRouter(prefix="/workflow", tags=["workflow"])


@router.post("/start", response_model=WorkflowCreateResponse, status_code=status.HTTP_201_CREATED)
def start_workflow(
    payload: WorkflowCreateRequest,
    background_tasks: BackgroundTasks,
) -> WorkflowCreateResponse:
    """
    Initializes a new workflow for a GitHub repository and starts the
    orchestrator in the background.
    Supports dry_run and demo_failure_mode options.
    """
    repo_url = payload.repo_url.strip()
    if not repo_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="repo_url cannot be empty",
        )

    if repo_url.startswith("-") or any(c in repo_url for c in [";", "|", "&", "`", "$", "\n", "\r"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or prohibited characters in repository URL",
        )

    orchestrator = WorkflowOrchestrator()
    workflow = orchestrator.create_workflow(
        repo_url=repo_url,
        task=payload.task.strip(),
        dry_run=payload.dry_run,
        demo_failure_mode=payload.demo_failure_mode,
    )

    # Launch execution loop in background
    background_tasks.add_task(orchestrator.run_workflow, workflow.workflow_id)

    return WorkflowCreateResponse(
        workflow_id=workflow.workflow_id,
        status=workflow.overall_status,
    )


@router.post("/{workflow_id}/execute", response_model=WorkflowState)
def execute_workflow_step(
    workflow_id: str,
    payload: Optional[ExecuteStepRequest] = None,
) -> WorkflowState:
    """
    Triggers or resumes execution of the workflow.
    Can be used by Member 1/2 to continue execution or test step execution directly.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    orchestrator = WorkflowOrchestrator()

    # If workflow is not in a terminal state, run execution
    if workflow.overall_status not in [
        WorkflowStatus.COMPLETED,
        WorkflowStatus.VERIFIED_FAILURE,
        WorkflowStatus.CANCELLED,
    ]:
        orchestrator.run_workflow(workflow_id)

    updated = workflow_store.get(workflow_id)
    return updated or workflow


@router.post("/{workflow_id}/cancel", response_model=WorkflowState)
def cancel_workflow(workflow_id: str) -> WorkflowState:
    """
    Requests cancellation of a running workflow.
    Child processes will be safely terminated and workflow status updated to CANCELLED.
    """
    orchestrator = WorkflowOrchestrator()
    workflow = orchestrator.cancel_workflow(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )
    return workflow


@router.post("/{workflow_id}/resume", response_model=WorkflowState)
def resume_workflow(
    workflow_id: str,
    background_tasks: BackgroundTasks,
) -> WorkflowState:
    """
    Resumes a workflow from its SQLite checkpoint, skipping already verified steps.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    orchestrator = WorkflowOrchestrator()
    background_tasks.add_task(orchestrator.run_workflow, workflow_id, True)
    return workflow


@router.post("/{workflow_id}/verify", response_model=WorkflowState)
def external_verify(
    workflow_id: str,
    payload: VerifyStepRequest,
    x_agentguard_verify_token: Optional[str] = Header(None, alias="X-AgentGuard-Verify-Token"),
) -> WorkflowState:
    """
    Integration hook for Member 3's Evidence Engine.
    Allows external verifier to post machine evidence decisions directly into the workflow.
    Routes to the orchestrator state machine to continue, recover/retry, or fail safely.
    Does NOT implement Member 3's verification rules internally.
    """
    if getattr(settings, "VERIFY_TOKEN", None):
        if not x_agentguard_verify_token or x_agentguard_verify_token != settings.VERIFY_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing X-AgentGuard-Verify-Token header",
            )

    orchestrator = WorkflowOrchestrator()
    try:
        return orchestrator.handle_verification_result(
            workflow_id=workflow_id,
            verif_result=payload.verification_result,
            step_id=payload.step_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=msg,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=msg,
            )
