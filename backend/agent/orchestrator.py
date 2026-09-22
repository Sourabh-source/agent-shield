import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend.agent.classifier import classify_failure
from backend.agent.executor import ToolExecutor
from backend.agent.planner import generate_plan, update_plan_with_analysis
from backend.agent.recovery_planner import recovery_planner
from backend.agent.snapshot import capture_workspace_snapshot, diff_snapshots
from backend.agent.verifier_client import VerificationClient, get_verifier_client
from backend.audit.hash_chain import HashChain
from backend.config import settings
from backend.metrics import (
    workflow_total,
    workflow_duration_seconds,
    active_workflows,
    step_verification_result,
    recovery_attempts_total,
)
from backend.models.workflow import (
    EvidenceRecord,
    EventType,
    ExecutionResult,
    FailureClassification,
    FailureType,
    FinalReportData,
    ProjectAnalysis,
    RecoveryAttempt,
    RecoveryOutcome,
    StepDefinition,
    StepStatus,
    StepType,
    VerificationResult,
    WorkflowEvent,
    WorkflowState,
    WorkflowStatus,
    create_evidence_record,
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

    def list_all(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
        owner_id: Optional[str] = None,
    ) -> List[WorkflowState]:
        with self._lock:
            if self.sqlite:
                return self.sqlite.list_workflows(limit=limit, offset=offset, owner_id=owner_id)
            if self._memory_cache:
                items = sorted(
                    self._memory_cache.values(),
                    key=lambda w: w.created_at or "",
                    reverse=True,
                )
                if owner_id and owner_id != "admin":
                    items = [w for w in items if getattr(w, "owner_id", "default-owner") == owner_id]
            else:
                items = []

            if offset > 0:
                items = items[offset:]
            if limit is not None and limit > 0:
                items = items[:limit]
            return items

    def delete(self, workflow_id: str) -> bool:
        """Removes a workflow from memory cache and persistent SQLite store."""
        with self._lock:
            self._memory_cache.pop(workflow_id, None)
            if self.sqlite:
                return self.sqlite.delete_workflow(workflow_id)
            return True

    def delete_by_owner(self, owner_id: str) -> int:
        """Removes all workflows for an owner from memory and SQLite store."""
        with self._lock:
            deleted_count = 0
            to_delete = [wid for wid, wf in self._memory_cache.items() if getattr(wf, "owner_id", "default-owner") == owner_id]
            for wid in to_delete:
                self._memory_cache.pop(wid, None)
            
            if self.sqlite:
                deleted_count = self.sqlite.delete_by_owner(owner_id)
            else:
                deleted_count = len(to_delete)
            return deleted_count


# Global singleton store
workflow_store = WorkflowStore()


class WorkflowOrchestrator:
    """
    Central controller for Orchestrator's backend.
    Enforces the core evidence loop:
    EXECUTE -> COLLECT EVIDENCE -> VERIFY -> CONTINUE / RECOVER -> VERIFY AGAIN
    """
    _running_workflows: set = set()
    _run_lock = threading.Lock()
    _active_executors: Dict[str, Any] = {}

    def __init__(
        self,
        verifier_client: Optional[VerificationClient] = None,
        max_retries: Optional[int] = None,
    ):
        self.verifier_client = verifier_client or get_verifier_client()
        self.max_retries = max_retries if max_retries is not None else settings.MAX_RETRIES
        self._hash_chain = HashChain()

    def create_workflow(
        self,
        repo_url: str,
        task: str,
        dry_run: bool = False,
        owner_id: Optional[str] = "default-owner",
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
            owner_id=owner_id or "default-owner",
        )
        if getattr(settings, "DEMO_FIXTURES_ENABLED", False):
            from backend.demo.demo_fixtures import is_demo_repository, _build_step_definitions
            if is_demo_repository(repo_url):
                state.steps = _build_step_definitions()
                logger.warning(f"[DEMO] Matched flask-hello-world fixture: pre-populated {len(state.steps)} demo steps")
        self.emit_event(
            state,
            EventType.WORKFLOW_STARTED,
            message=f"Workflow initialized for repo: {repo_url}" + (" [DRY RUN]" if dry_run else ""),
            metadata={"dry_run": dry_run, "owner_id": state.owner_id},
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
        
        # Calculate hash for the new event
        event_data = event.model_dump()
        prev_hash = self._hash_chain._previous_hash
        event_hash = self._hash_chain.append(event_data)
        
        # Add hash metadata
        if event.metadata is None:
            event.metadata = {}
        event.metadata['previous_hash'] = prev_hash
        event.metadata['event_hash'] = event_hash
        
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

        # Terminate any running processes immediately
        with self._run_lock:
            active_exec = self._active_executors.get(workflow_id)
        if active_exec:
            try:
                active_exec.cleanup()
            except Exception as e:
                logger.error(f"Error terminating executor processes on cancellation: {e}")

        return workflow

    def _check_environment_partially_satisfied(self, executor: ToolExecutor) -> bool:
        """
        Inspects whether the runtime environment already satisfies essential project imports
        or bytecode compilation, allowing execution to proceed even after partial dependency install failure.
        """
        ws_dir = Path(executor.workspace_dir)
        if not ws_dir.exists():
            return False

        # 1. Check Python files compilation
        py_files = [
            p for p in ws_dir.rglob("*.py")
            if not any(part.startswith((".", "venv", "__pycache__", "node_modules")) for part in p.parts)
        ]
        if py_files:
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "compileall", "-q", "."],
                    cwd=str(ws_dir),
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode == 0:
                    return True
            except Exception:
                pass

        # 2. Check if notebook exists and is valid JSON
        nb_files = list(ws_dir.rglob("*.ipynb"))
        if nb_files:
            try:
                import json
                for nbf in nb_files:
                    with open(nbf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if "cells" in data:
                            return True
            except Exception:
                pass

        # 3. Check if Node project has node_modules
        if (ws_dir / "package.json").exists() and (ws_dir / "node_modules").exists():
            return True

        return False

    def _handle_partial_dependency_or_halt(
        self,
        workflow: WorkflowState,
        step: StepDefinition,
        executor: ToolExecutor,
        exec_result: ExecutionResult,
        halt_reason: str,
        rec_plan: Optional[Any] = None,
    ) -> bool:
        """
        Checks if dependency installation failure can be treated as partially satisfied
        because the runtime environment can already compile or import project modules.
        Returns True if continuing as PARTIALLY_SATISFIED, False if should halt.
        """
        if step.type == StepType.INSTALL_DEPENDENCIES.value and self._check_environment_partially_satisfied(executor):
            logger.info(
                f"Dependency installation failed ({halt_reason}), but runtime environment is partially satisfied. Continuing to verification."
            )
            step.status = StepStatus.PARTIALLY_SATISFIED
            if step.metadata is None:
                step.metadata = {}
            step.metadata["partial_satisfied"] = True
            if workflow.metadata is None:
                workflow.metadata = {}
            workflow.metadata["partial_dependency_satisfied"] = True
            self.emit_event(
                workflow,
                EventType.STEP_PARTIALLY_SATISFIED,
                step=step.name,
                step_id=step.id,
                execution_id=exec_result.execution_id,
                status=StepStatus.PARTIALLY_SATISFIED.value,
                message="Dependency installation failed or timed out, but runtime environment partially satisfies compilation/imports. Proceeding to verification steps.",
                evidence={
                    "command": exec_result.command,
                    "exit_code": exec_result.exit_code,
                    "partial_satisfied": True,
                    "halt_reason": halt_reason,
                },
            )
            workflow_store.save(workflow)
            return True
        return False

    def run_workflow(self, workflow_id: str, resume: bool = False) -> WorkflowState:
        """
        Executes the entire workflow synchronously.
        Enforces closed-loop evidence verification, bounded self-healing retries,
        execution budgets, and checkpoint resumption.
        """
        with self._run_lock:
            if workflow_id in self._running_workflows:
                logger.warning(f"Workflow '{workflow_id}' is already actively running. Preventing concurrent duplicate execution.")
                existing = workflow_store.get(workflow_id)
                if existing:
                    return existing

            self._running_workflows.add(workflow_id)
            active_workflows.inc()

        workflow = workflow_store.get(workflow_id)
        if not workflow:
            with self._run_lock:
                self._running_workflows.discard(workflow_id)
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow_start_time = time.perf_counter()

        # Demo fixture interception: deterministic workflow for hackathon demos
        if getattr(settings, "DEMO_FIXTURES_ENABLED", False):
            from backend.demo.demo_fixtures import is_demo_repository, run_demo_workflow
            if is_demo_repository(workflow.repository):
                logger.warning("[DEMO] Demo fixtures enabled")
                logger.warning(f"[DEMO] Repository matched:\n{workflow.repository}")
                logger.warning("[DEMO] Starting deterministic flask-hello-world workflow")
                logger.warning("[DEMO] Real executor bypassed")
                logger.warning("[DEMO] Real verifier bypassed")
                try:
                    result = run_demo_workflow(self, workflow)
                    logger.warning("[DEMO] Demo workflow completed successfully")
                    active_workflows.dec()
                    workflow_total.labels(status=result.overall_status.value).inc()
                    workflow_duration_seconds.observe(time.perf_counter() - workflow_start_time)
                    return result
                finally:
                    with self._run_lock:
                        self._running_workflows.discard(workflow_id)
            else:
                logger.info(f"[DEMO] Repository did NOT match demo fixture: {workflow.repository}")
        else:
            logger.debug(f"[DEMO] Demo fixtures disabled, proceeding with real workflow")


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
            active_workflows.dec()
            workflow_total.labels(status=workflow.overall_status.value).inc()
            workflow_duration_seconds.observe(time.perf_counter() - workflow_start_time)
            with self._run_lock:
                self._running_workflows.discard(workflow_id)
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
            active_workflows.dec()
            workflow_total.labels(status=workflow.overall_status.value).inc()
            workflow_duration_seconds.observe(time.perf_counter() - workflow_start_time)
            with self._run_lock:
                self._running_workflows.discard(workflow_id)
            return workflow

        executor = ToolExecutor(workflow_id=workflow.workflow_id)
        with self._run_lock:
            self._active_executors[workflow.workflow_id] = executor
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

                # Resumption check: skip steps that are already verified or partially satisfied!
                if step.status in (StepStatus.VERIFIED_SUCCESS, StepStatus.PARTIALLY_SATISFIED):
                    logger.info(f"Step '{step.name}' is already {step.status.value}. Skipping.")
                    continue

                # Skip steps marked NOT_APPLICABLE for this project
                if step.status == StepStatus.NOT_APPLICABLE:
                    logger.info(f"Skipping step '{step.name}': marked NOT_APPLICABLE.")
                    self.emit_event(
                        workflow,
                        EventType.STEP_VERIFIED,
                        step=step.name,
                        step_id=step.id,
                        status=StepStatus.NOT_APPLICABLE.value,
                        message=f"Step '{step.name}' is NOT_APPLICABLE ({step.reason or 'Not required for this project'}).",
                    )
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

                    effective_step = step

                    # Capture workspace snapshot before execution
                    snap_before = capture_workspace_snapshot(executor.workspace_dir)

                    # Execute tool action
                    exec_result = executor.execute_step(
                        step=effective_step,
                        repo_url=workflow.repository,
                    )

                    # Capture workspace snapshot after execution and compute diff
                    snap_after = capture_workspace_snapshot(executor.workspace_dir)
                    ws_diff = diff_snapshots(snap_before, snap_after)
                    if exec_result.metadata is None:
                        exec_result.metadata = {}
                    exec_result.metadata["workspace_diff"] = ws_diff

                    step.execution_result = exec_result
                    step.evidence_digest = exec_result.evidence_digest
                    step.evidence = create_evidence_record(exec_result, step.id)

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
                    
                    verdict = "unverifiable"
                    if verif_result.metadata and (verif_result.metadata.get("verifier_unavailable") or verif_result.metadata.get("service_unavailable")):
                        verdict = "unverifiable"
                    else:
                        verdict = "pass" if verif_result.verified else "fail"
                    step_verification_result.labels(step_type=step.type, verdict=verdict).inc()

                    # Handle verifier service unavailability
                    if verif_result.metadata and (
                        verif_result.metadata.get("verifier_unavailable")
                        or verif_result.metadata.get("service_unavailable")
                    ):
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
                        for att in workflow.recovery_history:
                            if att.step_name == step.name and att.status in (RecoveryOutcome.SUCCESS, "SUCCESS"):
                                att.step_resolved = True
                        self.emit_event(
                            workflow,
                            EventType.VERIFICATION_PASSED,
                            step=step.name,
                            step_id=step.id,
                            execution_id=exec_result.execution_id,
                            status=StepStatus.VERIFIED_SUCCESS.value,
                            message=f"Verification PASSED for step: {step.name}. Reason: {verif_result.reason or 'OK'}",
                        )
                        self.emit_event(
                            workflow,
                            EventType.STEP_VERIFIED,
                            step=step.name,
                            step_id=step.id,
                            execution_id=exec_result.execution_id,
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
                            execution_id=exec_result.execution_id,
                            status=StepStatus.FAILED.value,
                            message=f"Verification FAILED for step: {step.name}. Reason: {verif_result.reason or 'Unverified'}",
                            evidence={"exit_code": exec_result.exit_code, "stderr": exec_result.stderr[:300]},
                        )

                        # Failure classification
                        classification = classify_failure(exec_result)
                        if classification.details is None:
                            classification.details = {}
                        owned_pids = set(getattr(workflow, "spawned_pids", []) or [])
                        if hasattr(executor, "_spawned_pids"):
                            owned_pids.update(executor._spawned_pids)
                        classification.details["spawned_pids"] = list(owned_pids)

                        self.emit_event(
                            workflow,
                            EventType.FAILURE_CLASSIFIED,
                            step=step.name,
                            step_id=step.id,
                            execution_id=exec_result.execution_id,
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
                                execution_id=exec_result.execution_id,
                                message=f"Structured recovery plan generated: {rec_plan.action_type} using {rec_plan.tool}",
                                evidence=rec_plan.model_dump(),
                            )

                            if rec_plan.expected_outcome == RecoveryOutcome.UNRECOVERABLE or rec_plan.action_type == "unrecoverable":
                                if self._handle_partial_dependency_or_halt(workflow, step, executor, exec_result, rec_plan.reason, rec_plan):
                                    step_verified = True
                                    break
                                step.status = StepStatus.FAILED
                                workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
                                workflow.final_result = (
                                    f"Workflow halted at step '{step.name}' with VERIFIED FAILURE: "
                                    f"Failure is unrecoverable ({rec_plan.reason})."
                                )
                                attempt = RecoveryAttempt(
                                    recovery_id=rec_plan.recovery_id,
                                    step_name=step.name,
                                    failure_type=rec_plan.failure_type,
                                    action="unrecoverable",
                                    status=RecoveryOutcome.UNRECOVERABLE,
                                    exit_code=1,
                                    duration_ms=0.0,
                                    step_resolved=False,
                                    step_verified=False,
                                )
                                workflow.recovery_history.append(attempt)
                                recovery_attempts_total.labels(failure_type=rec_plan.failure_type, outcome="unrecoverable").inc()
                                self.emit_event(
                                    workflow,
                                    EventType.WORKFLOW_FAILED,
                                    step=step.name,
                                    step_id=step.id,
                                    execution_id=exec_result.execution_id,
                                    status=WorkflowStatus.VERIFIED_FAILURE.value,
                                    message=workflow.final_result,
                                    evidence={
                                        "failed_step": step.name,
                                        "unrecoverable_reason": rec_plan.reason,
                                    },
                                )
                                workflow_store.save(workflow)
                                return workflow

                            # Check ledger for repeated futile recovery action
                            if recovery_planner.is_action_futile(rec_plan, workflow.recovery_history):
                                if self._handle_partial_dependency_or_halt(workflow, step, executor, exec_result, f"futile action {rec_plan.command}", rec_plan):
                                    step_verified = True
                                    break
                                logger.warning(
                                    f"Recovery action '{rec_plan.command}' was previously attempted and failed in workflow {workflow.workflow_id}. "
                                    "Escalating as unrecoverable to prevent futile loops."
                                )
                                step.status = StepStatus.FAILED
                                workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
                                workflow.final_result = (
                                    f"Workflow halted at step '{step.name}' with VERIFIED FAILURE: "
                                    f"Recovery action was already attempted and failed ({rec_plan.command or rec_plan.action_type})."
                                )
                                attempt = RecoveryAttempt(
                                    recovery_id=rec_plan.recovery_id,
                                    step_name=step.name,
                                    failure_type=rec_plan.failure_type,
                                    action=rec_plan.command or rec_plan.action_type,
                                    status=RecoveryOutcome.UNRECOVERABLE,
                                    exit_code=1,
                                    duration_ms=0.0,
                                    step_resolved=False,
                                )
                                workflow.recovery_history.append(attempt)
                                recovery_attempts_total.labels(failure_type=rec_plan.failure_type, outcome="unrecoverable").inc()
                                self.emit_event(
                                    workflow,
                                    EventType.WORKFLOW_FAILED,
                                    step=step.name,
                                    step_id=step.id,
                                    execution_id=exec_result.execution_id,
                                    status=WorkflowStatus.VERIFIED_FAILURE.value,
                                    message=workflow.final_result,
                                    evidence={
                                        "failed_step": step.name,
                                        "repeated_futile_action": rec_plan.command or rec_plan.action_type,
                                    },
                                )
                                workflow_store.save(workflow)
                                return workflow

                            # Apply any step-level overrides (such as extended timeout or relocated port)
                            if rec_plan.action_type == "extend_timeout" and rec_plan.timeout_override:
                                step.timeout_seconds = rec_plan.timeout_override
                            if rec_plan.action_type == "relocate_port" and rec_plan.rewritten_command:
                                step.command = rec_plan.rewritten_command

                            # Check if recovery is already satisfied (idempotent check)
                            if recovery_planner.is_action_already_satisfied(rec_plan, executor.workspace_dir, step=step):
                                logger.info("Recovery condition already satisfied. Proceeding to retry directly.")
                            else:
                                self.emit_event(
                                    workflow,
                                    EventType.RECOVERY_STARTED,
                                    step=step.name,
                                    step_id=step.id,
                                    execution_id=exec_result.execution_id,
                                    status=StepStatus.RECOVERING.value,
                                    message=f"Starting recovery for step: {step.name}. Action: {rec_plan.command}",
                                    evidence={"recovery_action": rec_plan.command},
                                )

                                rec_result = executor.execute_recovery_action(
                                    recovery_action=rec_plan.command,
                                    step_id=step.id,
                                )

                                # Postcondition evaluation: verify recovery actually succeeded
                                postcond_met = recovery_planner.evaluate_postcondition(
                                    rec_plan, executor.workspace_dir, step=step, execution_result=rec_result
                                )
                                rec_status = RecoveryOutcome.SUCCESS if (rec_result.exit_code == 0 and postcond_met) else RecoveryOutcome.FAILED
                                recovery_attempts_total.labels(failure_type=rec_plan.failure_type, outcome=rec_status.value.lower()).inc()

                                attempt = RecoveryAttempt(
                                    recovery_id=rec_plan.recovery_id,
                                    step_name=step.name,
                                    failure_type=rec_plan.failure_type,
                                    action=rec_plan.command,
                                    status=rec_status,
                                    exit_code=rec_result.exit_code,
                                    duration_ms=rec_result.duration_ms,
                                )
                                workflow.recovery_history.append(attempt)

                                self.emit_event(
                                    workflow,
                                    EventType.RECOVERY_EXECUTED,
                                    step=step.name,
                                    step_id=step.id,
                                    execution_id=rec_result.execution_id,
                                    status=StepStatus.RECOVERING.value,
                                    message=f"Recovery action executed with exit code {rec_result.exit_code}. Postcondition met: {postcond_met}",
                                    evidence={
                                        "exit_code": rec_result.exit_code,
                                        "postcondition_met": postcond_met,
                                        "output": rec_result.stdout[:200],
                                    },
                                )

                                if not postcond_met:
                                    if self._handle_partial_dependency_or_halt(workflow, step, executor, exec_result, "recovery postcondition failed", rec_plan):
                                        step_verified = True
                                        break
                                    logger.warning(
                                        f"Recovery postcondition failed for step '{step.name}' ({rec_plan.postcondition_target or rec_plan.command}). "
                                        "Aborting futile retries on original step."
                                    )
                                    step.retries += 1
                                    workflow.retries += 1
                                    step.status = StepStatus.FAILED
                                    workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
                                    workflow.final_result = (
                                        f"Workflow halted at step '{step.name}' with VERIFIED FAILURE: "
                                        f"Recovery action executed but postcondition failed ({rec_plan.postcondition_target or rec_plan.command})."
                                    )
                                    self.emit_event(
                                        workflow,
                                        EventType.WORKFLOW_FAILED,
                                        step=step.name,
                                        step_id=step.id,
                                        execution_id=rec_result.execution_id,
                                        status=WorkflowStatus.VERIFIED_FAILURE.value,
                                        message=workflow.final_result,
                                        evidence={
                                            "failed_step": step.name,
                                            "exit_code": rec_result.exit_code,
                                            "postcondition_met": False,
                                        },
                                    )
                                    workflow_store.save(workflow)
                                    return workflow

                            # Increment retries
                            step.retries += 1
                            workflow.retries += 1

                            self.emit_event(
                                workflow,
                                EventType.STEP_RETRY,
                                step=step.name,
                                step_id=step.id,
                                execution_id=exec_result.execution_id,
                                status=StepStatus.RUNNING.value,
                                message=f"Retrying step '{step.name}' (attempt {step.retries}/{workflow.max_retries})",
                            )
                            continue

                        else:
                            # Check if partial dependency satisfaction applies before halting
                            if self._handle_partial_dependency_or_halt(workflow, step, executor, exec_result, verif_result.reason or "retries exhausted"):
                                step_verified = True
                                break

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
                                execution_id=exec_result.execution_id,
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

            # All steps completed: evaluate honest final status
            has_failed = any(s.status == StepStatus.FAILED for s in workflow.steps)
            has_partially_satisfied = (
                any(s.status == StepStatus.PARTIALLY_SATISFIED for s in workflow.steps)
                or bool(workflow.metadata and workflow.metadata.get("partial_dependency_satisfied"))
            )

            EXECUTION_TYPES = {
                StepType.RUN_TESTS.value,
                StepType.START_APPLICATION.value,
                StepType.HEALTH_CHECK.value,
                StepType.BUILD_PROJECT.value,
                StepType.COMPILE_PROJECT.value,
                StepType.IMPORT_CHECK.value,
                StepType.EXECUTE_NOTEBOOK.value,
                StepType.VERIFY_OUTPUTS.value,
                StepType.SMOKE_TEST.value,
                "shell_command",
            }
            executed_verification_steps = [
                s for s in workflow.steps
                if s.status == StepStatus.VERIFIED_SUCCESS and (s.type in EXECUTION_TYPES or (s.type == StepType.CUSTOM.value and s.command))
            ]

            if has_failed:
                workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE
                workflow.final_status = "VERIFIED_FAILURE"
                workflow.final_result = "VERIFIED FAILURE: One or more workflow steps failed verification."
                workflow.verification_status = "FAIL"
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_FAILED,
                    status=WorkflowStatus.VERIFIED_FAILURE.value,
                    message=workflow.final_result,
                )
            elif has_partially_satisfied:
                workflow.overall_status = WorkflowStatus.INCOMPLETE
                workflow.final_status = "INCOMPLETE"
                workflow.final_result = "INCOMPLETE: Dependencies were partially satisfied; project could not be fully verified."
                workflow.verification_status = "INCOMPLETE"
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_COMPLETED,
                    status=WorkflowStatus.INCOMPLETE.value,
                    message=workflow.final_result,
                )
            elif not executed_verification_steps:
                workflow.overall_status = WorkflowStatus.NOT_APPLICABLE
                workflow.final_status = "NOT_APPLICABLE"
                workflow.final_result = "NOT_APPLICABLE: Repository contains no runnable code, entrypoints, or test suites."
                workflow.verification_status = "NOT_APPLICABLE"
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_COMPLETED,
                    status=WorkflowStatus.NOT_APPLICABLE.value,
                    message=workflow.final_result,
                )
            else:
                workflow.overall_status = WorkflowStatus.COMPLETED
                workflow.final_status = "VERIFIED_SUCCESS"
                workflow.final_result = "VERIFIED SUCCESS: All planned steps passed machine-checked verification."
                workflow.verification_status = "PASS"
                self.emit_event(
                    workflow,
                    EventType.WORKFLOW_COMPLETED,
                    status=WorkflowStatus.COMPLETED.value,
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
            active_workflows.dec()
            workflow_total.labels(status=workflow.overall_status.value).inc()
            total_time = time.perf_counter() - workflow_start_time
            workflow_duration_seconds.observe(total_time)
            with self._run_lock:
                self._running_workflows.discard(workflow_id)
                self._active_executors.pop(workflow_id, None)
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
        Processes an external VerificationResult posted from Verifier via callback.
        Enforces closed-loop evidence verification, self-healing recovery, retry bounding,
        and state transitions without bypassing the evidence gate.
        """
        workflow = workflow_store.get(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow '{workflow_id}' not found")

        # Reject if workflow is cancelled or cancel requested
        if workflow.overall_status in [WorkflowStatus.CANCELLED, WorkflowStatus.CANCEL_REQUESTED]:
            raise ValueError(f"Workflow '{workflow_id}' is cancelled; cannot accept verification decisions.")

        # 1. Match target step
        target_step: Optional[StepDefinition] = None
        if step_id:
            target_step = next((s for s in workflow.steps if s.id == step_id), None)
            if not target_step:
                raise ValueError(f"Step with id '{step_id}' not found in workflow '{workflow_id}'")
        elif workflow.current_step:
            target_step = next((s for s in workflow.steps if s.name == workflow.current_step), None)
        if not target_step and workflow.steps:
            target_step = next(
                (s for s in workflow.steps if s.status in [StepStatus.VERIFYING, StepStatus.RUNNING]),
                None,
            )
            if not target_step:
                target_step = next((s for s in workflow.steps if s.status != StepStatus.VERIFIED_SUCCESS), None)

        if not target_step:
            return workflow

        # Reject replay verification on already verified steps
        if target_step.status == StepStatus.VERIFIED_SUCCESS:
            raise ValueError(f"Step '{target_step.name}' is already verified. Replay verification rejected.")

        # Ensure target_step has an execution result; synthesize for backward-compatible test hooks if missing
        if target_step.execution_result is None:
            target_step.execution_result = ExecutionResult(
                workflow_id=workflow_id,
                step=target_step.name,
                step_id=target_step.id,
                command=target_step.command or "",
                exit_code=0 if verif_result.verified else 1,
                stdout="Executed via external verification hook" if verif_result.verified else "",
                stderr="" if verif_result.verified else (verif_result.reason or "Verification failed"),
                workspace=workflow.workspace_path,
            )
            target_step.evidence_digest = target_step.execution_result.evidence_digest
            target_step.evidence = create_evidence_record(target_step.execution_result, target_step.id)

        # Check execution_id binding
        if verif_result.execution_id and target_step.execution_result and target_step.execution_result.execution_id:
            if verif_result.execution_id != target_step.execution_result.execution_id:
                raise ValueError(
                    f"Execution ID mismatch for step '{target_step.name}': "
                    f"expected '{target_step.execution_result.execution_id}', got '{verif_result.execution_id}'"
                )

        # Check evidence_digest binding
        target_digest = (
            target_step.evidence_digest
            or (target_step.execution_result.evidence_digest if target_step.execution_result else None)
        )
        if verif_result.evidence_digest and target_digest:
            if verif_result.evidence_digest != target_digest:
                raise ValueError(
                    f"Evidence digest mismatch for step '{target_step.name}': "
                    f"expected '{target_digest}', got '{verif_result.evidence_digest}'"
                )

        # Security policy: require evidence digest if configured
        if getattr(settings, "REQUIRE_EVIDENCE_DIGEST", False):
            if not verif_result.evidence_digest:
                raise ValueError("Verification rejected: evidence_digest is required by security policy")
            if target_digest and verif_result.evidence_digest != target_digest:
                raise ValueError("Verification rejected: evidence_digest mismatch")

        # Reject if metadata explicitly indicates forgery or invalid evidence
        if verif_result.metadata and (verif_result.metadata.get("forged") or verif_result.metadata.get("evidence_invalid")):
            raise ValueError("Verification rejected: invalid or forged verification evidence detected in metadata")

        target_step.verification_result = verif_result
        workflow.verification_status = "PASS" if verif_result.verified else "FAIL"

        # CASE E: Verifier unavailable
        if verif_result.metadata and (
            verif_result.metadata.get("verifier_unavailable")
            or verif_result.metadata.get("service_unavailable")
        ):
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
            all_verified = all(
                s.status in [StepStatus.VERIFIED_SUCCESS, StepStatus.NOT_APPLICABLE]
                for s in workflow.steps
            )
            if all_verified:
                workflow.overall_status = WorkflowStatus.COMPLETED
                workflow.final_result = "VERIFIED SUCCESS: All planned steps passed machine-checked verification."
                workflow.final_status = "VERIFIED_SUCCESS"
                workflow.verification_status = "PASS"
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
                "step_resolved": getattr(r, "step_resolved", False),
            }
            for r in workflow.recovery_history
        ]

        evidence_records = []
        evidence_digests = {}
        for s in workflow.steps:
            if s.evidence:
                evidence_records.append(s.evidence.model_dump())
            if s.evidence_digest:
                evidence_digests[s.name] = s.evidence_digest
            elif s.execution_result and s.execution_result.evidence_digest:
                evidence_digests[s.name] = s.execution_result.evidence_digest

        duration = workflow.metrics.get("total_duration_seconds", 0.0)

        recoveries_attempted = len(workflow.recovery_history)
        recoveries_verified_effective = sum(
            1 for r in workflow.recovery_history
            if getattr(r, "step_resolved", False) is True
        )
        recoveries_unrecoverable = sum(
            1 for r in workflow.recovery_history
            if (r.status == RecoveryOutcome.UNRECOVERABLE or str(r.status).upper() == "UNRECOVERABLE")
        )

        return FinalReportData(
            workflow_id=workflow.workflow_id,
            repository=workflow.repository,
            task=workflow.task,
            final_status=workflow.overall_status.value,
            steps_completed=sum(
                1 for s in workflow.steps if s.status in [StepStatus.VERIFIED_SUCCESS, StepStatus.NOT_APPLICABLE]
            ),
            total_steps=len(workflow.steps),
            recoveries=len(workflow.recovery_history),
            recoveries_attempted=recoveries_attempted,
            recoveries_verified_effective=recoveries_verified_effective,
            recoveries_unrecoverable=recoveries_unrecoverable,
            retries=workflow.retries,
            duration_seconds=duration,
            verification_summary=verif_summary,
            recovery_history=rec_history,
            evidence_records=evidence_records,
            evidence_digests=evidence_digests,
        )
