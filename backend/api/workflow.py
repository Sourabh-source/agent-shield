import hashlib
import hmac
import logging
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

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

from typing import Dict, Optional

logger = logging.getLogger("agentguard.api.workflow")
router = APIRouter(tags=["workflow"])

_idempotency_cache: Dict[str, str] = {}  # key -> workflow_id


def verify_workflow_ownership(workflow: WorkflowState, request: Request, workflow_id: str):
    """Enforces multi-tenancy isolation. Returns 404 on ownership mismatch to prevent enumeration."""
    caller_owner = getattr(request.state, "owner_id", "default-owner")
    wf_owner = getattr(workflow, "owner_id", "default-owner")
    if caller_owner != "admin" and wf_owner and wf_owner != caller_owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )


@router.post("/start", response_model=WorkflowCreateResponse, status_code=status.HTTP_201_CREATED)
def start_workflow(
    payload: WorkflowCreateRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> WorkflowCreateResponse:
    """
    Initializes a new workflow for a Git repository and starts orchestrator in the background.
    Binds the workflow to the authenticated tenant owner.
    """
    if idempotency_key and idempotency_key in _idempotency_cache:
        existing_id = _idempotency_cache[idempotency_key]
        existing_wf = workflow_store.get(existing_id)
        if existing_wf:
            return WorkflowCreateResponse(
                workflow_id=existing_wf.workflow_id,
                status=existing_wf.overall_status,
            )

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

    owner_id = getattr(request.state, "owner_id", "default-owner")

    orchestrator = WorkflowOrchestrator()
    workflow = orchestrator.create_workflow(
        repo_url=repo_url,
        task=payload.task.strip(),
        dry_run=payload.dry_run,
        owner_id=owner_id,
    )

    if idempotency_key:
        _idempotency_cache[idempotency_key] = workflow.workflow_id

    # Launch execution loop in background
    background_tasks.add_task(orchestrator.run_workflow, workflow.workflow_id)

    return WorkflowCreateResponse(
        workflow_id=workflow.workflow_id,
        status=workflow.overall_status,
    )


@router.post("/{workflow_id}/execute", response_model=WorkflowState)
def execute_workflow(
    workflow_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: Optional[ExecuteStepRequest] = None,
    sync: bool = False,
) -> WorkflowState:
    """
    Triggers execution of the workflow in background.
    Guarded by tenant ownership and strict FSM transition validation.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    verify_workflow_ownership(workflow, request, workflow_id)

    # Strict FSM validation: terminal states cannot be re-executed
    if workflow.overall_status in [
        WorkflowStatus.COMPLETED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.BUDGET_EXCEEDED,
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot execute workflow in terminal state '{workflow.overall_status.value}'",
        )

    orchestrator = WorkflowOrchestrator()

    if sync:
        orchestrator.run_workflow(workflow_id)
        updated = workflow_store.get(workflow_id)
        return updated or workflow
    else:
        background_tasks.add_task(orchestrator.run_workflow, workflow_id)
        return workflow


@router.post("/{workflow_id}/cancel", response_model=WorkflowState)
def cancel_workflow(workflow_id: str, request: Request) -> WorkflowState:
    """
    Requests cancellation of a running workflow.
    Child processes will be safely terminated and workflow status updated to CANCELLED.
    Guarded by tenant ownership.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    verify_workflow_ownership(workflow, request, workflow_id)

    orchestrator = WorkflowOrchestrator()
    cancelled = orchestrator.cancel_workflow(workflow_id)
    return cancelled or workflow


@router.post("/{workflow_id}/resume", response_model=WorkflowState)
def resume_workflow(
    workflow_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
) -> WorkflowState:
    """
    Resumes a workflow from its SQLite checkpoint, skipping already verified steps.
    Guarded by tenant ownership and strict FSM transition validation.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    verify_workflow_ownership(workflow, request, workflow_id)

    if workflow.overall_status == WorkflowStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resume a workflow that is already COMPLETED",
        )
    if workflow.overall_status == WorkflowStatus.CANCELLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resume a workflow that has been CANCELLED",
        )

    orchestrator = WorkflowOrchestrator()
    background_tasks.add_task(orchestrator.run_workflow, workflow_id, True)
    return workflow


@router.post("/{workflow_id}/verify", response_model=WorkflowState)
def external_verify(
    workflow_id: str,
    payload: VerifyStepRequest,
    request: Request,
    x_agentguard_verify_signature: Optional[str] = Header(None, alias="X-AgentGuard-Verify-Signature"),
    x_agentguard_verify_token: Optional[str] = Header(None, alias="X-AgentGuard-Verify-Token"),
) -> WorkflowState:
    """
    Integration hook for Evidence Engine verification.
    Enforces HMAC-SHA256 signature verification or verification token auth.
    Guarded by tenant ownership.
    """
    workflow = workflow_store.get(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' not found",
        )

    verify_workflow_ownership(workflow, request, workflow_id)

    # HMAC-SHA256 signature verification
    if x_agentguard_verify_signature:
        secret = getattr(settings, "VERIFY_HMAC_SECRET", "agentguard-hmac-secret-key-prod")
        step_part = payload.step_id or ""
        msg = f"{workflow_id}:{step_part}".encode("utf-8")
        expected_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(x_agentguard_verify_signature, expected_sig):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid X-AgentGuard-Verify-Signature HMAC signature",
            )
    elif getattr(settings, "VERIFY_TOKEN", None):
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
