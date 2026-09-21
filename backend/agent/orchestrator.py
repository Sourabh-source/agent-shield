import logging
import threading
import time
from typing import Dict, List, Optional
from uuid import uuid4

from backend.agent.classifier import classify_failure
from backend.agent.executor import ToolExecutor
from backend.agent.planner import generate_plan, update_plan_with_analysis
from backend.agent.recovery_planner import recovery_planner
from backend.agent.verifier_client import VerificationClient, get_verifier_client
from backend.config import settings
from backend.models.workflow import (
    EventType,
    ExecutionResult,
    FailureClassification,
    FailureType,
    FinalReportData,
    ProjectAnalysis,
    RecoveryAttempt,
    StepDefinition,
    StepStatus,
    StepType,
    VerificationResult,
    WorkflowEvent,
    WorkflowState,
    WorkflowStatus,
    current_iso_time,
)
from backend.storage.checkpoint import SQLiteCheckpointStorage

logger = logging.getLogger("agentguard.orchestrator")


class WorkflowStore:
    """
    Thread-safe store for workflow states and events with SQLite persistence.
    Workflows survive server restarts and can be resumed from checkpoints.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._memory_cache: Dict[str, WorkflowState] = {}
        self.sqlite = None
        if getattr(settings, "USE_SQLITE_PERSISTENCE", True):
            try:
                self.sqlite = SQLiteCheckpointStorage(db_path=db_path)
                # Pre-populate cache from SQLite
                for wf in self.sqlite.list_workflows():
                    self._memory_cache[wf.workflow_id] = wf
            except Exception as e:
                logger.warning(f"Could not initialize SQLite storage: {e}. Using in-memory store.")

    def save(self, workflow: WorkflowState):
        with self._lock:
            workflow.updated_at = current_iso_time()
            self._memory_cache[workflow.workflow_id] = workflow
            if self.sqlite:
                try:
                    self.sqlite.save_workflow(workflow)
                except Exception as e:
                    logger.error(f"Error saving workflow to SQLite: {e}")

    def get(self, workflow_id: str) -> Optional[WorkflowState]:
        with self._lock:
            if workflow_id in self._memory_cache:
                return self._memory_cache[workflow_id]
            if self.sqlite:
                wf = self.sqlite.get_workflow(workflow_id)
                if wf:
                    self._memory_cache[workflow_id] = wf
                    return wf
            return None

    def list_all(self) -> List[WorkflowState]:
        with self._lock:
            if self._memory_cache:
                return sorted(
                    self._memory_cache.values(),
                    key=lambda w: w.created_at or "",
                    reverse=True,
                )
            if self.sqlite:
                workflows = self.sqlite.list_workflows()
                for wf in workflows:
                    self._memory_cache[wf.workflow_id] = wf
                return workflows
            return []


# Global singleton store
workflow_store = WorkflowStore()


class WorkflowOrchestrator:
    """
    Central controller for Member 2's backend.
    Enforces the core evidence loop:
    EXECUTE -> COLLECT EVIDENCE -> VERIFY -> CONTINUE / RECOVER -> VERIFY AGAIN
    """

    def __init__(
        self,
        verifier_client: Optional[VerificationClient] = None,
        max_retries: Optional[int] = None,
    ):
        self.verifier_client = verifier_client or get_verifier_client()
        self.max_retries = max_retries if max_retries is not None else settings.MAX_RETRIES

    def create_workflow(
        self,
        repo_url: str,
        task: str,
        dry_run: bool = False,
        demo_failure_mode: Optional[str] = None,
    ) -> WorkflowState:
        """Initializes a new workflow record and persists initial PENDING state."""
        workflow_id = str(uuid4())[:8]
        state = WorkflowState(
            workflow_id=workflow_id,
            repository=repo_url,
            task=task,
            overall_status=WorkflowStatus.PENDING,
            max_retries=self.max_retries,
            dry_run=dry_run,
            demo_failure_mode=demo_failure_mode,
        )
        self.emit_event(
            state,
            EventType.WORKFLOW_STARTED,
            message=f"Workflow initialized for repo: {repo_url}" + (" [DRY RUN]" if dry_run else ""),
            metadata={"dry_run": dry_run, "demo_failure_mode": demo_failure_mode},
        )
        workflow_store.save(state)
        return state

    def emit_event(
        self,
        workflow: WorkflowState,
        event_type: EventType,
        step: Optional[str] = None,
        step_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        status: Optional[str] = None,
        message: str = "",
        evidence: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> WorkflowEvent:
        """Appends a standardized event with correlation IDs to the timeline and saves state."""
        event = WorkflowEvent(
            workflow_id=workflow.workflow_id,
            event_type=event_type.value,
            step=step or workflow.current_step,
            step_id=step_id,
            execution_id=execution_id,
            status=status or workflow.overall_status.value,
            message=message,
            timestamp=current_iso_time(),
            evidence=evidence,
            metadata=metadata,
        )
        workflow.events.append(event)
        workflow_store.save(workflow)
        logger.info(f"[{workflow.workflow_id}] Event: {event_type.value} - {message}")
        return event

    def plan_workflow(self, workflow: WorkflowState) -> List[StepDefinition]:
        """Generates validated workflow steps using the AI Planner."""
        workflow.overall_status = WorkflowStatus.PLANNING
        self.emit_event(
            workflow,
            EventType.PLANNING_STARTED,
            message="Planning workflow steps based on repository and task.",
        )

        steps = generate_plan(workflow.repository, workflow.task)
        workflow.steps = steps
        self.emit_event(
            workflow,
            EventType.PLAN_CREATED,
            message=f"Planned {len(steps)} sequential steps.",
            evidence={"step_count": len(steps), "steps": [s.name for s in steps]},
        )
        workflow_store.save(workflow)
        return steps

    def cancel_workflow(self, workflow_id: str) -> Optional[WorkflowState]:
        """Requests cancellation of a running workflow and terminates child processes."""
        workflow = workflow_store.get(workflow_id)
        if not workflow:
            return None
        workflow.overall_status = WorkflowStatus.CANCEL_REQUESTED
        self.emit_event(
            workflow,
            EventType.WORKFLOW_CANCELLED,
            status=WorkflowStatus.CANCEL_REQUESTED.value,
            message="Workflow cancellation requested.",
        )
        workflow_store.save(workflow)
        return workflow

    def run_workflow(self, workflow_id: str, resume: bool = False) -> WorkflowState:
        """
        Executes the entire workflow synchronously.
        Enforces closed-loop evidence verification, bounded self-healing retries,
        execution budgets, and checkpoint resumption.
        """
        workflow = workflow_store.get(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow_start_time = time.perf_counter()

        # Check if workflow was cancelled
        if workflow.overall_status in [WorkflowStatus.CANCEL_REQUESTED, WorkflowStatus.CANCELLED]:
            workflow.overall_status = WorkflowStatus.CANCELLED
            workflow.final_result = "Workflow cancelled by user."
            self.emit_event(
                workflow,
                EventType.WORKFLOW_CANCELLED,
                status=WorkflowStatus.CANCELLED.value,
                message=workflow.final_result,
            )
            workflow_store.save(workflow)
            return workflow

        # 1. Generate plan if not already planned
        if not workflow.steps:
            self.plan_workflow(workflow)

        # 2. Dry-Run Mode Gate
        if workflow.dry_run:
            workflow.overall_status = WorkflowStatus.COMPLETED
            workflow.final_result = "DRY RUN COMPLETE: Workflow planned and validated without command execution."
            self.emit_event(
                workflow,
                EventType.WORKFLOW_COMPLETED,
                status=WorkflowStatus.COMPLETED.value,
                message=workflow.final_result,
            )
            workflow_store.save(workflow)
            return workflow

        executor = ToolExecutor(workflow_id=workflow.workflow_id)
        workflow.workspace_path = executor.workspace_dir
        workflow.overall_status = WorkflowStatus.RUNNING
        workflow_store.save(workflow)

        try:
            for step in workflow.steps:
                # Check for cancellation
                fresh_state = workflow_store.get(workflow_id)
                if fresh_state and fresh_state.overall_status == WorkflowStatus.CANCEL_REQUESTED:
                    workflow.overall_status = WorkflowStatus.CANCELLED
                    workflow.final_result = "Workflow cancelled by user."
                    self.emit_event(
                        workflow,
                        EventType.WORKFLOW_CANCELLED,
                        status=WorkflowStatus.CANCELLED.value,
                        message=workflow.final_result,
                    )
                    workflow_store.save(workflow)
                    return workflow

                # Resumption check: skip steps that are already verified!
                if resume and step.status == StepStatus.VERIFIED_SUCCESS:
                    logger.info(f"Resuming: step '{step.name}' already verified. Skipping.")
                    continue

                # Check execution budget: max workflow time
                elapsed_workflow = time.perf_counter() - workflow_start_time
                if elapsed_workflow > settings.MAX_WORKFLOW_TIME:
                    workflow.overall_status = WorkflowStatus.BUDGET_EXCEEDED
                    workflow.final_result = f"Workflow budget exceeded: exceeded maximum runtime ({settings.MAX_WORKFLOW_TIME}s)."
                    self.emit_event(
                        workflow,
                        EventType.BUDGET_EXCEEDED,
                        step=step.name,
                        status=WorkflowStatus.BUDGET_EXCEEDED.value,
                        message=workflow.final_result,
                    )
                    workflow_store.save(workflow)
                    return workflow

                workflow.current_step = step.name
                workflow.overall_status = WorkflowStatus.RUNNING
                step.status = StepStatus.RUNNING

                self.emit_event(
                    workflow,
                    EventType.STEP_STARTED,
                    step=step.name,
                    step_id=step.id,
                    status=StepStatus.RUNNING.value,
                    message=f"Starting execution of step: {step.name}",
                )

                step_verified = False

                while not step_verified:
                    # Check cancellation during retry loop
                    fresh_state = workflow_store.get(workflow_id)
                    if fresh_state and fresh_state.overall_status == WorkflowStatus.CANCEL_REQUESTED:
                        workflow.overall_status = WorkflowStatus.CANCELLED
                        workflow.final_result = "Workflow cancelled by user."
                        self.emit_event(
                            workflow,
                            EventType.WORKFLOW_CANCELLED,
                            status=WorkflowStatus.CANCELLED.value,
                            message=workflow.final_result,
                        )
                        workflow_store.save(workflow)
                        return workflow

                    # Execute tool action
                    exec_result = executor.execute_step(
                        step=step,
                        repo_url=workflow.repository,
                    )

                    # Demo failure injection mechanism (for reliable presentation)
                    if workflow.demo_failure_mode == "missing_dependency" and "build" in step.name.lower() and step.retries == 0:
                        exec_result.exit_code = 1
                        exec_result.stderr = "ModuleNotFoundError: No module named 'pandas'"
                        exec_result.stdout = ""

                    elif workflow.demo_failure_mode == "persistent_failure" and "build" in step.name.lower():
                        exec_result.exit_code = 1
                        exec_result.stderr = "Persistent syntax error in source file"
                        exec_result.stdout = ""

                    step.execution_result = exec_result

                    self.emit_event(
                        workflow,
                        EventType.COMMAND_EXECUTED,
                        step=step.name,
                        step_id=step.id,
                        execution_id=exec_result.execution_id,
                        message=f"Executed command: '{exec_result.command}' (exit_code={exec_result.exit_code})",
                        evidence={
                            "command": exec_result.command,
                            "exit_code": exec_result.exit_code,
                            "duration_ms": exec_result.duration_ms,
                            "stdout_snippet": exec_result.stdout[:200] if exec_result.stdout else "",
                            "stderr_snippet": exec_result.stderr[:200] if exec_result.stderr else "",
                        },
                    )

                    # Dynamic plan refinement after project analysis
                    if step.type == StepType.ANALYZE_PROJECT.value and exec_result.exit_code == 0:
                        if exec_result.metadata:
                            analysis = ProjectAnalysis.model_validate(exec_result.metadata)
                            workflow.steps = update_plan_with_analysis(workflow.steps, analysis)

                    # Send to verifier
                    workflow.overall_status = WorkflowStatus.VERIFYING
                    step.status = StepStatus.VERIFYING
                    self.emit_event(
                        workflow,
                        EventType.VERIFICATION_STARTED,
                        step=step.name,
                        step_id=step.id,
                        execution_id=exec_result.execution_id,
                        status=StepStatus.VERIFYING.value,
                        message=f"Submitting evidence for verification of step: {step.name}",
                        evidence={"exit_code": exec_result.exit_code},
                    )

                    verif_result: VerificationResult = self.verifier_client.verify(exec_result)
                    step.verification_result = verif_result

                    # Handle verifier service unavailability
                    if verif_result.metadata and verif_result.metadata.get("verifier_unavailable"):
                        workflow.overall_status = WorkflowStatus.VERIFICATION_UNAVAILABLE
                        step.status = StepStatus.FAILED
                        workflow.final_result = f"Verification unavailable: {verif_result.reason}"
                        self.emit_event(
                            workflow,
                            EventType.VERIFICATION_UNAVAILABLE,
                            step=step.name,
                            step_id=step.id,
                            status=WorkflowStatus.VERIFICATION_UNAVAILABLE.value,
                            message=workflow.final_result,
                        )
                        workflow_store.save(workflow)
                        return workflow

                    workflow.verification_status = "PASS" if verif_result.verified else "FAIL"

                    if verif_result.verified:
                        # PASS: mark verified success and continue
                        step.status = StepStatus.VERIFIED_SUCCESS
                        step_verified = True
                        self.emit_event(
                            workflow,
                            EventType.VERIFICATION_PASSED,
                            step=step.name,
                            step_id=step.id,
                            status=StepStatus.VERIFIED_SUCCESS.value,
                            message=f"Verification PASSED for step: {step.name}. Reason: {verif_result.reason or 'OK'}",
                        )
                        self.emit_event(
                            workflow,
                            EventType.STEP_VERIFIED,
                            step=step.name,
                            step_id=step.id,
                            status=StepStatus.VERIFIED_SUCCESS.value,
                            message=f"Step '{step.name}' verified and complete.",
                        )
                        break

                    else:
                        # FAIL: handle classification, recovery and bounded retry
                        self.emit_event(
                            workflow,
                            EventType.VERIFICATION_FAILED,
                            step=step.name,
                            step_id=step.id,
                            status=StepStatus.FAILED.value,
                            message=f"Verification FAILED for step: {step.name}. Reason: {verif_result.reason or 'Unverified'}",
                            evidence={"exit_code": exec_result.exit_code, "stderr": exec_result.stderr[:300]},
                        )

                        # Failure classification
                        classification = classify_failure(exec_result)
                        self.emit_event(
                            workflow,
                            EventType.FAILURE_CLASSIFIED,
                            step=step.name,
                            step_id=step.id,
                            message=f"Failure classified as {classification.failure_type.value}: {classification.reason}",
                            evidence=classification.model_dump(),
                        )

                        # Check recovery budget & retries limit
                        can_recover = (
                            verif_result.recovery_required
                            and verif_result.retry_allowed
                            and step.retries < workflow.max_retries
                            and len(workflow.recovery_history) < settings.MAX_RECOVERY_ACTIONS
                        )

                        if can_recover:
                            workflow.overall_status = WorkflowStatus.RECOVERING
                            step.status = StepStatus.RECOVERING

                            # Structured recovery plan
                            rec_plan = recovery_planner.generate_recovery_plan(
                                exec_result=exec_result,
                                classification=classification,
                                suggested_action=verif_result.recovery_action,
                                max_attempts=workflow.max_retries,
                            )

                            self.emit_event(
                                workflow,
                                EventType.RECOVERY_PLANNED,
                                step=step.name,
                                step_id=step.id,
                                message=f"Structured recovery plan generated: {rec_plan.action_type} using {rec_plan.tool}",
                                evidence=rec_plan.model_dump(),
                            )

                            # Check if recovery is already satisfied (idempotent check)
                            if recovery_planner.is_action_already_satisfied(rec_plan, executor.workspace_dir):
                                logger.info("Recovery condition already satisfied. Proceeding to retry directly.")
                            else:
                                self.emit_event(
                                    workflow,
                                    EventType.RECOVERY_STARTED,
                                    step=step.name,
                                    step_id=step.id,
                                    status=StepStatus.RECOVERING.value,
                                    message=f"Starting recovery for step: {step.name}. Action: {rec_plan.command}",
                                    evidence={"recovery_action": rec_plan.command},
                                )

                                rec_result = executor.execute_recovery_action(
                                    recovery_action=rec_plan.command,
                                    step_id=step.id,
                                )

                                attempt = RecoveryAttempt(
                                    recovery_id=rec_plan.recovery_id,
                                    step_name=step.name,
                                    failure_type=rec_plan.failure_type,
                                    action=rec_plan.command,
                                    status="SUCCESS" if rec_result.exit_code == 0 else "FAILED",
                                    exit_code=rec_result.exit_code,
                                    duration_ms=rec_result.duration_ms,
                                )
                                workflow.recovery_history.append(attempt)

                                self.emit_event(
                                    workflow,
                                    EventType.RECOVERY_EXECUTED,
                                    step=step.name,
                                    step_id=step.id,
                                    status=StepStatus.RECOVERING.value,
                                    message=f"Recovery action executed with exit code {rec_result.exit_code}",
                                    evidence={"exit_code": rec_result.exit_code, "output": rec_result.stdout[:200]},
                                )

                            # Increment retries
                            step.retries += 1
                            workflow.retries += 1

                            self.emit_event(
                                workflow,
                                EventType.STEP_RETRY,
                                step=step.name,
                                step_id=step.id,
                                status=StepStatus.RUNNING.value,
                                message=f"Retrying step '{step.name}' (attempt {step.retries}/{workflow.max_retries})",
                            )
                            continue

                        else:
                            # Retry limit reached or recovery budget exceeded -> VERIFIED FAILURE
                            step.status = StepStatus.FAILED
                            workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
                            workflow.final_result = (
                                f"Workflow halted at step '{step.name}' with VERIFIED FAILURE. "
                                f"Retries exhausted ({step.retries}/{workflow.max_retries}). "
                                f"Reason: {verif_result.reason or 'Execution failed verification'}"
                            )

                            self.emit_event(
                                workflow,
                                EventType.WORKFLOW_FAILED,
                                step=step.name,
                                step_id=step.id,
                                status=WorkflowStatus.VERIFIED_FAILURE.value,
                                message=workflow.final_result,
                                evidence={
                                    "failed_step": step.name,
                                    "exit_code": exec_result.exit_code,
                                    "retries": step.retries,
                                },
                            )
                            workflow_store.save(workflow)
                            return workflow

            # All steps completed and verified
            all_verified = all(s.status == StepStatus.VERIFIED_SUCCESS for s in workflow.steps)
            if all_verified:
                workflow.overall_status = WorkflowStatus.COMPLETED
                workflow.final_result = "VERIFIED SUCCESS: All planned steps passed machine-checked verification."
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_COMPLETED,
                    status=WorkflowStatus.COMPLETED.value,
                    message=workflow.final_result,
                )
            else:
                workflow.overall_status = WorkflowStatus.FAILED
                workflow.final_result = "Workflow finished with unverified steps."
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_FAILED,
                    status=WorkflowStatus.FAILED.value,
                    message=workflow.final_result,
                )

            # Record final execution metrics
            total_duration = round(time.perf_counter() - workflow_start_time, 2)
            workflow.metrics = {
                "total_duration_seconds": total_duration,
                "steps_total": len(workflow.steps),
                "steps_verified": sum(1 for s in workflow.steps if s.status == StepStatus.VERIFIED_SUCCESS),
                "retries_count": workflow.retries,
                "recoveries_count": len(workflow.recovery_history),
            }

            workflow_store.save(workflow)
            return workflow

        finally:
            executor.cleanup()

    def run_workflow_in_background(self, workflow_id: str, resume: bool = False):
        """Launches workflow execution in a background daemon thread."""
        thread = threading.Thread(
            target=self.run_workflow,
            args=(workflow_id,),
            kwargs={"resume": resume},
            daemon=True,
        )
        thread.start()
        return thread

    def handle_verification_result(
        self,
        workflow_id: str,
        verif_result: VerificationResult,
        step_id: Optional[str] = None,
    ) -> WorkflowState:
        """
        Processes an external VerificationResult posted from Member 3 via callback.
        Enforces closed-loop evidence verification, self-healing recovery, retry bounding,
        and state transitions without bypassing the evidence gate.
        """
        workflow = workflow_store.get(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow '{workflow_id}' not found")

        # 1. Match target step
        target_step: Optional[StepDefinition] = None
        if step_id:
            target_step = next((s for s in workflow.steps if s.id == step_id), None)
        if not target_step and workflow.current_step:
            target_step = next((s for s in workflow.steps if s.name == workflow.current_step), None)
        if not target_step and workflow.steps:
            target_step = next(
                (s for s in workflow.steps if s.status in [StepStatus.VERIFYING, StepStatus.RUNNING, StepStatus.PENDING]),
                workflow.steps[-1],
            )

        if not target_step:
            return workflow

        target_step.verification_result = verif_result
        workflow.verification_status = "PASS" if verif_result.verified else "FAIL"

        # CASE E: Verifier unavailable
        if verif_result.metadata and verif_result.metadata.get("verifier_unavailable"):
            workflow.overall_status = WorkflowStatus.VERIFICATION_UNAVAILABLE
            target_step.status = StepStatus.FAILED
            workflow.final_result = f"Verification unavailable: {verif_result.reason}"
            self.emit_event(
                workflow,
                EventType.VERIFICATION_UNAVAILABLE,
                step=target_step.name,
                step_id=target_step.id,
                status=WorkflowStatus.VERIFICATION_UNAVAILABLE.value,
                message=workflow.final_result,
            )
            workflow_store.save(workflow)
            return workflow

        # CASE A: verified = True
        if verif_result.verified:
            target_step.status = StepStatus.VERIFIED_SUCCESS
            self.emit_event(
                workflow,
                EventType.VERIFICATION_PASSED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.VERIFIED_SUCCESS.value,
                message=f"Verification PASSED for step: {target_step.name}. Reason: {verif_result.reason or 'OK'}",
            )
            self.emit_event(
                workflow,
                EventType.STEP_VERIFIED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.VERIFIED_SUCCESS.value,
                message=f"Step '{target_step.name}' verified and complete.",
            )

            # Check if all planned steps are verified
            all_verified = all(s.status == StepStatus.VERIFIED_SUCCESS for s in workflow.steps)
            if all_verified:
                workflow.overall_status = WorkflowStatus.COMPLETED
                workflow.final_result = "VERIFIED SUCCESS: All planned steps passed machine-checked verification."
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_COMPLETED,
                    status=WorkflowStatus.COMPLETED.value,
                    message=workflow.final_result,
                )
                workflow_store.save(workflow)
                return workflow
            else:
                # Continue workflow to the next step
                workflow.overall_status = WorkflowStatus.RUNNING
                workflow_store.save(workflow)
                return self.run_workflow(workflow_id, resume=True)

        # CASE C: verified = False and recovery_required = False
        if not verif_result.recovery_required:
            self.emit_event(
                workflow,
                EventType.VERIFICATION_FAILED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.FAILED.value,
                message=f"Verification FAILED for step: {target_step.name}. Reason: {verif_result.reason or 'Verification rejected'}",
            )
            target_step.status = StepStatus.FAILED
            workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
            workflow.final_result = (
                f"Workflow halted at step '{target_step.name}' with VERIFIED FAILURE. "
                f"Recovery not required or unavailable. Reason: {verif_result.reason or 'Verification rejected'}"
            )
            self.emit_event(
                workflow,
                EventType.WORKFLOW_FAILED,
                step=target_step.name,
                step_id=target_step.id,
                status=WorkflowStatus.VERIFIED_FAILURE.value,
                message=workflow.final_result,
            )
            workflow_store.save(workflow)
            return workflow

        # CASE D: verified = False and retry_allowed = False
        if not verif_result.retry_allowed:
            self.emit_event(
                workflow,
                EventType.VERIFICATION_FAILED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.FAILED.value,
                message=f"Verification FAILED for step: {target_step.name}. Reason: {verif_result.reason or 'Verification failed'}",
            )
            target_step.status = StepStatus.FAILED
            workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
            workflow.final_result = (
                f"Workflow halted at step '{target_step.name}' with VERIFIED FAILURE. "
                f"Retry disallowed by verifier. Reason: {verif_result.reason or 'Verification failed'}"
            )
            self.emit_event(
                workflow,
                EventType.WORKFLOW_FAILED,
                step=target_step.name,
                step_id=target_step.id,
                status=WorkflowStatus.VERIFIED_FAILURE.value,
                message=workflow.final_result,
            )
            workflow_store.save(workflow)
            return workflow

        # CASE B: verified = False, recovery_required = True, retry_allowed = True
        self.emit_event(
            workflow,
            EventType.VERIFICATION_FAILED,
            step=target_step.name,
            step_id=target_step.id,
            status=StepStatus.FAILED.value,
            message=f"Verification FAILED for step: {target_step.name}. Reason: {verif_result.reason or 'Unverified'}",
        )

        can_recover = (
            target_step.retries < workflow.max_retries
            and len(workflow.recovery_history) < settings.MAX_RECOVERY_ACTIONS
        )

        if not can_recover:
            # Retries or recovery budget exhausted
            target_step.status = StepStatus.FAILED
            workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
            workflow.final_result = (
                f"Workflow halted at step '{target_step.name}' with VERIFIED FAILURE. "
                f"Retries exhausted ({target_step.retries}/{workflow.max_retries}). "
                f"Reason: {verif_result.reason or 'Execution failed verification'}"
            )
            self.emit_event(
                workflow,
                EventType.WORKFLOW_FAILED,
                step=target_step.name,
                step_id=target_step.id,
                status=WorkflowStatus.VERIFIED_FAILURE.value,
                message=workflow.final_result,
            )
            workflow_store.save(workflow)
            return workflow

        # Execute recovery and retry
        workflow.overall_status = WorkflowStatus.RECOVERING
        target_step.status = StepStatus.RECOVERING

        # Failure classification
        if target_step.execution_result:
            classification = classify_failure(target_step.execution_result)
        else:
            classification = FailureClassification(
                failure_type=FailureType.UNKNOWN_ERROR,
                reason=verif_result.reason or "Unknown error",
            )

        self.emit_event(
            workflow,
            EventType.FAILURE_CLASSIFIED,
            step=target_step.name,
            step_id=target_step.id,
            message=f"Failure classified as {classification.failure_type.value}: {classification.reason}",
            evidence=classification.model_dump(),
        )

        # Generate structured recovery plan
        rec_plan = recovery_planner.generate_recovery_plan(
            exec_result=target_step.execution_result or ExecutionResult(
                workflow_id=workflow_id,
                step=target_step.name,
                command=target_step.command or "",
                exit_code=1,
                stderr=verif_result.reason or "",
            ),
            classification=classification,
            suggested_action=verif_result.recovery_action,
            max_attempts=workflow.max_retries,
        )

        self.emit_event(
            workflow,
            EventType.RECOVERY_PLANNED,
            step=target_step.name,
            step_id=target_step.id,
            message=f"Structured recovery plan generated: {rec_plan.action_type} using {rec_plan.tool}",
            evidence=rec_plan.model_dump(),
        )

        executor = ToolExecutor(workflow_id=workflow.workflow_id)
        if not recovery_planner.is_action_already_satisfied(rec_plan, executor.workspace_dir):
            self.emit_event(
                workflow,
                EventType.RECOVERY_STARTED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.RECOVERING.value,
                message=f"Starting recovery for step: {target_step.name}. Action: {rec_plan.command}",
                evidence={"recovery_action": rec_plan.command},
            )

            rec_result = executor.execute_recovery_action(
                recovery_action=rec_plan.command,
                step_id=target_step.id,
            )

            attempt = RecoveryAttempt(
                recovery_id=rec_plan.recovery_id,
                step_name=target_step.name,
                failure_type=rec_plan.failure_type,
                action=rec_plan.command,
                status="SUCCESS" if rec_result.exit_code == 0 else "FAILED",
                exit_code=rec_result.exit_code,
                duration_ms=rec_result.duration_ms,
            )
            workflow.recovery_history.append(attempt)

            self.emit_event(
                workflow,
                EventType.RECOVERY_EXECUTED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.RECOVERING.value,
                message=f"Recovery action executed with exit code {rec_result.exit_code}",
                evidence={"exit_code": rec_result.exit_code, "output": rec_result.stdout[:200]},
            )

        # Increment retry counts
        target_step.retries += 1
        workflow.retries += 1

        self.emit_event(
            workflow,
            EventType.STEP_RETRY,
            step=target_step.name,
            step_id=target_step.id,
            status=StepStatus.RUNNING.value,
            message=f"Retrying step '{target_step.name}' (attempt {target_step.retries}/{workflow.max_retries})",
        )

        # Retry the failed step
        target_step.status = StepStatus.RUNNING
        fresh_exec = executor.execute_step(step=target_step, repo_url=workflow.repository)
        target_step.execution_result = fresh_exec

        self.emit_event(
            workflow,
            EventType.COMMAND_EXECUTED,
            step=target_step.name,
            step_id=target_step.id,
            execution_id=fresh_exec.execution_id,
            message=f"Executed command: '{fresh_exec.command}' (exit_code={fresh_exec.exit_code})",
            evidence={
                "command": fresh_exec.command,
                "exit_code": fresh_exec.exit_code,
                "duration_ms": fresh_exec.duration_ms,
            },
        )

        # Require verification again! Never claim success before second verification.
        workflow.overall_status = WorkflowStatus.VERIFYING
        target_step.status = StepStatus.VERIFYING

        self.emit_event(
            workflow,
            EventType.VERIFICATION_STARTED,
            step=target_step.name,
            step_id=target_step.id,
            execution_id=fresh_exec.execution_id,
            status=StepStatus.VERIFYING.value,
            message=f"Submitting evidence for re-verification of step: {target_step.name}",
            evidence={"exit_code": fresh_exec.exit_code},
        )

        fresh_verif = self.verifier_client.verify(fresh_exec)
        target_step.verification_result = fresh_verif
        workflow.verification_status = "PASS" if fresh_verif.verified else "FAIL"

        if fresh_verif.verified:
            target_step.status = StepStatus.VERIFIED_SUCCESS
            self.emit_event(
                workflow,
                EventType.VERIFICATION_PASSED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.VERIFIED_SUCCESS.value,
                message=f"Re-verification PASSED for step: {target_step.name}. Reason: {fresh_verif.reason or 'OK'}",
            )
            self.emit_event(
                workflow,
                EventType.STEP_VERIFIED,
                step=target_step.name,
                step_id=target_step.id,
                status=StepStatus.VERIFIED_SUCCESS.value,
                message=f"Step '{target_step.name}' verified and complete after recovery.",
            )
            all_verified = all(s.status == StepStatus.VERIFIED_SUCCESS for s in workflow.steps)
            if all_verified:
                workflow.overall_status = WorkflowStatus.COMPLETED
                workflow.final_result = "VERIFIED SUCCESS: All planned steps passed machine-checked verification."
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_COMPLETED,
                    status=WorkflowStatus.COMPLETED.value,
                    message=workflow.final_result,
                )
            else:
                workflow.overall_status = WorkflowStatus.RUNNING
                workflow_store.save(workflow)
                return self.run_workflow(workflow_id, resume=True)
        else:
            target_step.status = StepStatus.FAILED
            workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
            workflow.final_result = (
                f"Workflow halted at step '{target_step.name}' with VERIFIED FAILURE. "
                f"Retries exhausted ({target_step.retries}/{workflow.max_retries}). "
                f"Reason: {fresh_verif.reason or 'Re-execution failed verification'}"
            )
            self.emit_event(
                workflow,
                EventType.WORKFLOW_FAILED,
                step=target_step.name,
                step_id=target_step.id,
                status=WorkflowStatus.VERIFIED_FAILURE.value,
                message=workflow.final_result,
            )

        workflow_store.save(workflow)
        return workflow

    def get_final_report_data(self, workflow_id: str) -> Optional[FinalReportData]:
        """Generates evidence-backed structured final report data."""
        workflow = workflow_store.get(workflow_id)
        if not workflow:
            return None

        verif_summary = {}
        for s in workflow.steps:
            if s.verification_result:
                verif_summary[s.name] = "PASS" if s.verification_result.verified else "FAIL"
            else:
                verif_summary[s.name] = s.status.value

        rec_history = [
            {
                "recovery_id": r.recovery_id,
                "step": r.step_name,
                "failure_type": r.failure_type,
                "action": r.action,
                "status": r.status,
                "exit_code": r.exit_code,
            }
            for r in workflow.recovery_history
        ]

        duration = workflow.metrics.get("total_duration_seconds", 0.0)

        return FinalReportData(
            workflow_id=workflow.workflow_id,
            repository=workflow.repository,
            task=workflow.task,
            final_status=workflow.overall_status.value,
            steps_completed=sum(1 for s in workflow.steps if s.status == StepStatus.VERIFIED_SUCCESS),
            total_steps=len(workflow.steps),
            recoveries=len(workflow.recovery_history),
            retries=workflow.retries,
            duration_seconds=duration,
            verification_summary=verif_summary,
            recovery_history=rec_history,
        )
