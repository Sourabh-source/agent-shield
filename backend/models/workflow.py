from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


def current_iso_time() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStatus(str, Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    WAITING_FOR_VERIFICATION = "WAITING_FOR_VERIFICATION"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    SKIPPED = "SKIPPED"
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"


class StepType(str, Enum):
    CLONE_REPOSITORY = "clone_repository"
    ANALYZE_PROJECT = "analyze_project"
    INSTALL_DEPENDENCIES = "install_dependencies"
    BUILD_PROJECT = "build_project"
    RUN_TESTS = "run_tests"
    START_APPLICATION = "start_application"
    HEALTH_CHECK = "health_check"
    FINAL_REPORT = "final_report"
    CUSTOM = "custom"


class EventType(str, Enum):
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    PLANNING_STARTED = "PLANNING_STARTED"
    PLAN_CREATED = "PLAN_CREATED"
    STEP_STARTED = "STEP_STARTED"
    COMMAND_EXECUTED = "COMMAND_EXECUTED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
    FAILURE_CLASSIFIED = "FAILURE_CLASSIFIED"
    RECOVERY_PLANNED = "RECOVERY_PLANNED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_EXECUTED = "RECOVERY_EXECUTED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    STEP_RETRY = "STEP_RETRY"
    STEP_VERIFIED = "STEP_VERIFIED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class FailureType(str, Enum):
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    BUILD_ERROR = "BUILD_ERROR"
    TEST_FAILURE = "TEST_FAILURE"
    PORT_ERROR = "PORT_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    TIMEOUT = "TIMEOUT"
    REPOSITORY_ERROR = "REPOSITORY_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class FailureClassification(BaseModel):
    failure_type: FailureType = FailureType.UNKNOWN_ERROR
    reason: str = ""
    confidence: float = 1.0
    details: Optional[Dict[str, Any]] = None


class RecoveryPlan(BaseModel):
    recovery_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    reason: str
    failure_type: str
    action_type: str
    tool: str
    command: str
    target_step: str
    max_attempts: int = 2


class RecoveryAttempt(BaseModel):
    recovery_id: str
    step_name: str
    failure_type: str
    action: str
    status: str = "COMPLETED"
    exit_code: int = 0
    duration_ms: float = 0.0
    timestamp: str = Field(default_factory=current_iso_time)


# Hand-off models between Member 2, Member 3, and Member 1

class ExecutionResult(BaseModel):
    """
    Standardized execution output sent from Member 2 to Member 3's Evidence Engine.
    Captures raw observable evidence from real command execution.
    """
    workflow_id: str
    step: str
    action: Optional[str] = None
    step_id: Optional[str] = None
    execution_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    command: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timestamp: str = Field(default_factory=current_iso_time)
    workspace: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.action:
            self.action = self.step

    @property
    def actual(self) -> Dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    @property
    def expected(self) -> Dict[str, Any]:
        return {"exit_code": 0}


class VerificationResult(BaseModel):
    """
    Standardized verification decision received by Member 2 from Member 3.
    """
    verified: bool
    reason: Optional[str] = None
    recovery_required: bool = False
    recovery_action: Optional[str] = None
    retry_allowed: bool = True
    recovery_id: Optional[str] = None
    failure_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkflowEvent(BaseModel):
    """
    Standardized event schema sent to Member 1's Frontend.
    """
    workflow_id: str
    event_type: str
    event_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    step: Optional[str] = None
    step_id: Optional[str] = None
    execution_id: Optional[str] = None
    status: str = "RUNNING"
    message: str = ""
    timestamp: str = Field(default_factory=current_iso_time)
    evidence: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class ProjectAnalysis(BaseModel):
    """
    Structured project detection output produced after analyzing cloned repository.
    """
    language: str = "unknown"
    package_manager: str = "unknown"
    install_command: Optional[str] = None
    build_command: Optional[str] = None
    test_command: Optional[str] = None
    start_command: Optional[str] = None
    health_check_url: Optional[str] = "http://localhost:8000/health"
    detected_files: List[str] = Field(default_factory=list)
    details: Optional[Dict[str, Any]] = None


class StepDefinition(BaseModel):
    id: str
    type: str
    name: str
    tool: Optional[str] = None
    reason: Optional[str] = None
    command: Optional[str] = None
    description: Optional[str] = None
    status: StepStatus = StepStatus.PENDING
    retries: int = 0
    execution_result: Optional[ExecutionResult] = None
    verification_result: Optional[VerificationResult] = None


class WorkflowCreateRequest(BaseModel):
    repo_url: str
    task: str = "Check whether this project can be built and run successfully."
    dry_run: bool = False
    demo_failure_mode: Optional[str] = None


class WorkflowCreateResponse(BaseModel):
    workflow_id: str
    status: WorkflowStatus


class WorkflowState(BaseModel):
    workflow_id: str
    repository: str
    task: str
    current_step: Optional[str] = None
    overall_status: WorkflowStatus = WorkflowStatus.PENDING
    steps: List[StepDefinition] = Field(default_factory=list)
    events: List[WorkflowEvent] = Field(default_factory=list)
    retries: int = 0
    max_retries: int = 2
    workspace_path: Optional[str] = None
    verification_status: Optional[str] = None
    final_result: Optional[str] = None
    dry_run: bool = False
    demo_failure_mode: Optional[str] = None
    recovery_history: List[RecoveryAttempt] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=current_iso_time)
    updated_at: str = Field(default_factory=current_iso_time)


class ExecuteStepRequest(BaseModel):
    step_id: Optional[str] = None


class VerifyStepRequest(BaseModel):
    """
    Used when external Member 3 posts verification results directly to the workflow API.
    """
    step_id: Optional[str] = None
    verification_result: VerificationResult


class FinalReportData(BaseModel):
    workflow_id: str
    repository: str
    task: str
    final_status: str
    steps_completed: int
    total_steps: int
    recoveries: int
    retries: int
    duration_seconds: float
    verification_summary: Dict[str, str] = Field(default_factory=dict)
    recovery_history: List[Dict[str, Any]] = Field(default_factory=list)
