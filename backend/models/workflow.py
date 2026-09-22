from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4
from pydantic import BaseModel, Field


def current_iso_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_evidence_digest(stdout: str = "", stderr: str = "", exit_code: int = 0) -> str:
    """Computes a deterministic SHA-256 digest of execution outputs to bind evidence to execution."""
    hasher = hashlib.sha256()
    hasher.update(str(exit_code).encode("utf-8"))
    hasher.update(b":")
    hasher.update((stdout or "").encode("utf-8", errors="replace"))
    hasher.update(b":")
    hasher.update((stderr or "").encode("utf-8", errors="replace"))
    return hasher.hexdigest()


class WorkflowStatus(str, Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    WAITING_FOR_VERIFICATION = "WAITING_FOR_VERIFICATION"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    FAILED = "FAILED"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    INCOMPLETE = "INCOMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNVERIFIABLE = "UNVERIFIABLE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, (str, Enum)):
            other_val = other.value if isinstance(other, Enum) else other
            if self.value in ("COMPLETED", "VERIFIED_SUCCESS") and other_val in ("COMPLETED", "VERIFIED_SUCCESS"):
                return True
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value)


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    SKIPPED = "SKIPPED"
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PARTIALLY_SATISFIED = "PARTIALLY_SATISFIED"


class StepType(str, Enum):
    CLONE_REPOSITORY = "clone_repository"
    ANALYZE_PROJECT = "analyze_project"
    INSTALL_DEPENDENCIES = "install_dependencies"
    BUILD_PROJECT = "build_project"
    RUN_TESTS = "run_tests"
    START_APPLICATION = "start_application"
    HEALTH_CHECK = "health_check"
    FINAL_REPORT = "final_report"
    COMPILE_PROJECT = "compile_project"
    IMPORT_CHECK = "import_check"
    EXECUTE_NOTEBOOK = "execute_notebook"
    VERIFY_OUTPUTS = "verify_outputs"
    SMOKE_TEST = "smoke_test"
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
    STEP_PARTIALLY_SATISFIED = "STEP_PARTIALLY_SATISFIED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class FailureType(str, Enum):
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"
    BUILD_ERROR = "BUILD_ERROR"
    BUILD_FAILURE = "BUILD_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    PORT_ERROR = "PORT_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    TIMEOUT = "TIMEOUT"
    REPOSITORY_ERROR = "REPOSITORY_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    IMPORT_ERROR = "IMPORT_ERROR"
    RUNTIME_FAILURE = "RUNTIME_FAILURE"
    HEALTH_CHECK_FAILURE = "HEALTH_CHECK_FAILURE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class FailureClassification(BaseModel):
    failure_type: FailureType = FailureType.UNKNOWN_ERROR
    reason: str = ""
    confidence: float = 1.0
    details: Optional[Dict[str, Any]] = None
    source_evidence: Optional[str] = None


class RecoveryOutcome(str, Enum):
    """
    Contract for recovery intervention outcomes:
    - SUCCESS: Machine-checkable postcondition evaluated and passed.
    - FAILED: Postcondition evaluated and failed (or recovery command crashed).
    - UNVERIFIABLE: Recovery executed but lacks an automated postcondition (never claimed as SUCCESS).
    - UNRECOVERABLE: Diagnosed failure class cannot be safely auto-remediated; halts retries honestly.
    """
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNVERIFIABLE = "UNVERIFIABLE"
    UNRECOVERABLE = "UNRECOVERABLE"


class RecoveryPlan(BaseModel):
    recovery_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    reason: str
    failure_type: str
    action_type: str
    tool: str
    command: Optional[str] = None
    target_step: str
    max_attempts: int = 2
    postcondition_type: Optional[str] = None
    postcondition_target: Optional[str] = None
    postcondition_cmd: Optional[str] = None
    expected_outcome: RecoveryOutcome = RecoveryOutcome.SUCCESS
    timeout_override: Optional[int] = None
    new_port: Optional[int] = None
    rewritten_command: Optional[str] = None


class RecoveryAttempt(BaseModel):
    recovery_id: str
    step_name: str
    failure_type: str
    action: Optional[str] = None
    status: Union[RecoveryOutcome, str] = RecoveryOutcome.SUCCESS
    exit_code: int = 0
    duration_ms: float = 0.0
    timestamp: str = Field(default_factory=current_iso_time)
    step_resolved: bool = False


# Hand-off models between Orchestrator, Verifier, and Frontend

class EvidenceType(str, Enum):
    EXECUTION_OUTPUT = "EXECUTION_OUTPUT"
    FILESYSTEM_SNAPSHOT = "FILESYSTEM_SNAPSHOT"
    PROCESS_CHECK = "PROCESS_CHECK"
    HTTP_RESPONSE = "HTTP_RESPONSE"


class EvidenceRecord(BaseModel):
    """
    Structured, tamper-evident record binding raw execution evidence to workflow/step execution.
    """
    evidence_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    workflow_id: str
    step_id: str
    execution_id: str
    evidence_type: str = EvidenceType.EXECUTION_OUTPUT.value
    content_digest: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    collected_at: str = Field(default_factory=current_iso_time)


class ExecutionResult(BaseModel):
    """
    Standardized execution output sent from Orchestrator to the Verifier's Evidence Engine.
    Captures raw observable evidence from real command execution.
    """
    workflow_id: str = "default"
    step: str = "custom"
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
    evidence_digest: Optional[str] = None
    step_type: Optional[str] = None
    timeout_seconds: Optional[int] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.action:
            self.action = self.step
        if not self.evidence_digest:
            self.evidence_digest = compute_evidence_digest(self.stdout, self.stderr, self.exit_code)

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


def create_evidence_record(
    exec_result: ExecutionResult,
    step_id: str,
    evidence_type: str = EvidenceType.EXECUTION_OUTPUT.value,
) -> EvidenceRecord:
    digest = exec_result.evidence_digest or compute_evidence_digest(
        exec_result.stdout, exec_result.stderr, exec_result.exit_code
    )
    return EvidenceRecord(
        workflow_id=exec_result.workflow_id,
        step_id=step_id,
        execution_id=exec_result.execution_id,
        evidence_type=evidence_type,
        content_digest=digest,
        payload={
            "command": exec_result.command,
            "exit_code": exec_result.exit_code,
            "stdout": exec_result.stdout,
            "stderr": exec_result.stderr,
            "duration_ms": exec_result.duration_ms,
            "workspace": exec_result.workspace,
            "metadata": exec_result.metadata or {},
        },
        collected_at=exec_result.timestamp or current_iso_time(),
    )


class VerificationResult(BaseModel):
    """
    Standardized verification decision received by Orchestrator from Verifier.
    Supports tri-state status: VERIFIED, FAILED, UNVERIFIABLE.
    """
    verified: bool
    status: str = Field(default="VERIFIED")
    reason: Optional[str] = None
    reason_code: Optional[str] = None
    recovery_required: bool = False
    recovery_action: Optional[str] = None
    retry_allowed: bool = True
    recovery_id: Optional[str] = None
    failure_type: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    execution_id: Optional[str] = None
    evidence_digest: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if self.status == "UNVERIFIABLE":
            self.verified = False
        elif not self.verified and self.status == "VERIFIED":
            self.status = "FAILED"
        elif self.verified and self.status == "FAILED":
            self.status = "VERIFIED"


class WorkflowEvent(BaseModel):
    """
    Standardized event schema sent to Frontend.
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


class ProjectManifest(BaseModel):
    """
    Structured deep project detection manifest produced after analyzing cloned repository.
    """
    language: str = "unknown"
    runtime: str = "unknown"
    package_manager: str = "unknown"
    dependency_manager: str = "unknown"
    framework: Optional[str] = None
    is_ml: bool = False
    is_api: bool = False
    is_notebook: bool = False
    is_cli: bool = False
    heavy_dependencies: bool = False
    dependency_files: List[str] = Field(default_factory=list)
    entrypoints: List[str] = Field(default_factory=list)
    notebooks: List[str] = Field(default_factory=list)
    install_command: Optional[str] = None
    build_command: Optional[str] = None
    build_commands: List[str] = Field(default_factory=list)
    test_command: Optional[str] = None
    test_commands: List[str] = Field(default_factory=list)
    start_command: Optional[str] = None
    application_startup_commands: List[str] = Field(default_factory=list)
    health_check_url: Optional[str] = "http://localhost:8000/health"
    http_endpoints: List[str] = Field(default_factory=list)
    likely_health_endpoints: List[str] = Field(default_factory=list)
    compile_command: Optional[str] = None
    import_check_command: Optional[str] = None
    smoke_test_command: Optional[str] = None
    detected_files: List[str] = Field(default_factory=list)
    details: Optional[Dict[str, Any]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.runtime == "unknown" and self.language != "unknown":
            self.runtime = self.language
        if self.dependency_manager == "unknown" and self.package_manager != "unknown":
            self.dependency_manager = self.package_manager
        elif self.package_manager == "unknown" and self.dependency_manager != "unknown":
            self.package_manager = self.dependency_manager


ProjectAnalysis = ProjectManifest


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
    evidence: Optional[EvidenceRecord] = None
    evidence_digest: Optional[str] = None
    timeout_seconds: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowCreateRequest(BaseModel):
    repo_url: str
    task: str = "Check whether this project can be built and run successfully."
    dry_run: bool = False




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
    owner_id: Optional[str] = "default-owner"
    recovery_history: List[RecoveryAttempt] = Field(default_factory=list)
    spawned_pids: List[int] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    final_status: Optional[str] = None
    created_at: str = Field(default_factory=current_iso_time)
    updated_at: str = Field(default_factory=current_iso_time)


class ExecuteStepRequest(BaseModel):
    step_id: Optional[str] = None


class VerifyStepRequest(BaseModel):
    """
    Used when external Verifier posts verification results directly to the workflow API.
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
    evidence_records: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_digests: Dict[str, str] = Field(default_factory=dict)
    recoveries_attempted: int = 0
    recoveries_verified_effective: int = 0
    recoveries_unrecoverable: int = 0
