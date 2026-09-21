import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from backend.agent.classifier import classify_failure
from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.planner import generate_plan, validate_plan_gate
from backend.agent.recovery_planner import recovery_planner
from backend.agent.verifier_client import HttpVerifierClient, VerificationClient
from backend.config import settings
from backend.models.workflow import (
    EventType,
    ExecutionResult,
    FailureType,
    RecoveryPlan,
    StepDefinition,
    StepStatus,
    StepType,
    VerificationResult,
    WorkflowState,
    WorkflowStatus,
)
from backend.storage.checkpoint import SQLiteCheckpointStorage
from backend.tools.file_tool import FileTool
from backend.tools.registry import tool_registry
from backend.tools.shell_tool import execute_shell_command, is_safe_command, redact_secrets, truncate_output


# =========================================================================
# 1. Planner & Validation Gate Tests
# =========================================================================

def test_invalid_llm_json_fallback():
    """Case 1: Invalid LLM JSON triggers deterministic fallback cleanly."""
    with patch("backend.agent.planner.plan_workflow_with_gemini", return_value=None):
        steps = generate_plan("https://github.com/example/repo", "test task")
        assert len(steps) == 8
        assert steps[0].type == StepType.CLONE_REPOSITORY.value
        assert steps[0].tool == "git"
        assert steps[0].reason is not None


def test_unknown_planner_tool_rejected():
    """Case 2: Plan validation gate rejects unknown tools."""
    bad_step = StepDefinition(
        id="s1",
        type="build_project",
        name="Build",
        tool="unsupported_quantum_tool_xyz",
        command="build",
    )
    valid, reason = validate_plan_gate([bad_step])
    assert not valid
    assert "Unknown tool" in reason


def test_planner_reasoning_metadata_populated():
    """Case 17: Machine-readable reason metadata is stored on every step."""
    steps = generate_plan("https://github.com/example/repo", "Check project health")
    for s in steps:
        assert s.reason is not None
        assert len(s.reason) > 5


# =========================================================================
# 2. Command Safety, Path Traversal, Secrets & Timeouts
# =========================================================================

def test_unsafe_command_blocked():
    """Case 3: Destructive commands are blocked with exit code 126."""
    res = execute_shell_command("rm -rf /", workflow_id="sec-1")
    assert res.exit_code == 126
    assert "Security Violation" in res.stderr
    assert res.metadata.get("security_blocked") is True


def test_path_traversal_blocked():
    """Case 4: Path traversal attempting to escape workspace is blocked."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_tool = FileTool()
        # Attempt traversal outside workspace
        res = file_tool.execute(
            command="read ../../etc/passwd",
            cwd=tmpdir,
            workflow_id="sec-2",
        )
        assert res.exit_code != 0
        assert "traversal" in res.stderr.lower()


def test_command_timeout():
    """Case 5: Runaway processes are terminated after timeout (exit code 124)."""
    res = execute_shell_command("python -c \"import time; time.sleep(5)\"", timeout_seconds=1, workflow_id="sec-3")
    assert res.exit_code == 124
    assert res.metadata.get("timed_out") is True


def test_huge_stdout_truncated():
    """Case 6: Large command output is truncated at MAX_OUTPUT_SIZE."""
    huge_text = "A" * (settings.MAX_OUTPUT_SIZE + 500)
    truncated = truncate_output(huge_text, max_size=settings.MAX_OUTPUT_SIZE)
    assert len(truncated) < len(huge_text)
    assert "[TRUNCATED: Output exceeded" in truncated


def test_secret_redaction_comprehensive():
    """Case 25: Sensitive credentials and API tokens are masked."""
    sample = (
        "GEMINI: AIzaSyD3x9FakeKey1234567890123456789\n"
        "GITHUB: ghp_123456789012345678901234567890123456\n"
        "AWS: AKIAIOSFODNN7EXAMPLE\n"
        "BEARER: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123\n"
    )
    redacted = redact_secrets(sample)
    assert "AIzaSy" not in redacted
    assert "ghp_" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "***REDACTED" in redacted


# =========================================================================
# 3. Failure Classification Tests
# =========================================================================

def test_failure_classification_dependency_error():
    """Case 9: Missing module classified as DEPENDENCY_ERROR."""
    res = ExecutionResult(
        workflow_id="w1",
        step="build_project",
        command="npm run build",
        exit_code=1,
        stderr="Module not found: Error: Can't resolve 'axios'",
    )
    classification = classify_failure(res)
    assert classification.failure_type == FailureType.DEPENDENCY_ERROR
    assert "axios" in classification.reason
    assert classification.confidence >= 0.90


def test_failure_classification_port_error():
    """Case 12: EADDRINUSE classified as PORT_ERROR."""
    res = ExecutionResult(
        workflow_id="w1",
        step="start_application",
        command="python app.py",
        exit_code=1,
        stderr="Error: listen EADDRINUSE: address already in use :::8000",
    )
    classification = classify_failure(res)
    assert classification.failure_type == FailureType.PORT_ERROR
    assert "8000" in classification.reason


def test_failure_classification_test_failure():
    """Case 11: Assertion error classified as TEST_FAILURE."""
    res = ExecutionResult(
        workflow_id="w1",
        step="run_tests",
        command="pytest",
        exit_code=1,
        stderr="FAILED tests/test_core.py::test_math - AssertionError: assert 1 == 2",
    )
    classification = classify_failure(res)
    assert classification.failure_type == FailureType.TEST_FAILURE


# =========================================================================
# 4. Structured Recovery & Idempotence
# =========================================================================

def test_structured_recovery_plan_generation():
    """Case 15: Generates concrete, validated RecoveryPlan."""
    res = ExecutionResult(
        workflow_id="w1",
        step="build_project",
        command="npm run build",
        exit_code=1,
        stderr="Cannot find module 'express'",
    )
    classification = classify_failure(res)
    plan = recovery_planner.generate_recovery_plan(res, classification)

    assert isinstance(plan, RecoveryPlan)
    assert plan.action_type == "install_dependency"
    assert plan.tool == "npm"
    assert "npm install express" in plan.command
    assert plan.target_step == "build_project"


def test_idempotent_recovery_skips_existing_dependency():
    """Case 23: If dependency is already present, redundant recovery is skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Simulate express already existing in node_modules
        pkg_dir = Path(tmpdir) / "node_modules" / "express"
        pkg_dir.mkdir(parents=True, exist_ok=True)

        plan = RecoveryPlan(
            reason="Missing express",
            failure_type=FailureType.DEPENDENCY_ERROR.value,
            action_type="install_dependency",
            tool="npm",
            command="npm install express",
            target_step="build_project",
        )
        already_satisfied = recovery_planner.is_action_already_satisfied(plan, workspace_dir=tmpdir)
        assert already_satisfied is True


# =========================================================================
# 5. Verifier Safety & Critical Security Invariants
# =========================================================================

def test_verifier_says_fail_despite_exit_code_zero():
    """
    Case 18 & 24: CRITICAL SECURITY INVARIANT.
    Command returns exit_code=0, but Member 3 Verifier rejects the evidence.
    Orchestrator MUST NOT produce SUCCESS.
    """
    class SecurityRejectingVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Static analysis detected hard-coded credentials in binary artifact",
                recovery_required=False,
                retry_allowed=False,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=SecurityRejectingVerifier())
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/insecure-app",
        task="Check security",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="compile", command="echo build_succeeded_exit_0"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    # Invariant: exit code was 0, but final status is VERIFIED_FAILURE!
    assert finished.steps[0].execution_result.exit_code == 0
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert finished.steps[0].status == StepStatus.FAILED
    assert "VERIFIED FAILURE" in finished.final_result


def test_verifier_service_unavailable_handled_safely():
    """Case 16: If Member 3 service is down, transitions to VERIFICATION_UNAVAILABLE."""
    # Point HttpVerifierClient to unreachable port
    unreachable_client = HttpVerifierClient("http://127.0.0.1:59998/verify")
    orchestrator = WorkflowOrchestrator(verifier_client=unreachable_client)

    wf = orchestrator.create_workflow(repo_url="https://github.com/example/app", task="Verify health")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build", command="echo building"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    # Must NOT become VERIFIED_SUCCESS or crash
    assert finished.overall_status == WorkflowStatus.VERIFICATION_UNAVAILABLE
    assert "Verification unavailable" in finished.final_result
    event_types = [e.event_type for e in finished.events]
    assert EventType.VERIFICATION_UNAVAILABLE.value in event_types


# =========================================================================
# 6. Execution Budgets & Limits
# =========================================================================

def test_retry_budget_exceeded():
    """Case 20: Persistent failures stop at MAX_RETRIES with VERIFIED_FAILURE."""
    class ContinuousFailVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Syntax error never resolved",
                recovery_required=True,
                recovery_action="echo 'retry fix'",
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=ContinuousFailVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/stuck", task="Test limit")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build", command="echo fail"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.retries == 2
    assert finished.steps[0].retries == 2
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE


def test_workflow_time_budget_exceeded():
    """Case 21: Exceeding max workflow duration halts with BUDGET_EXCEEDED."""
    verifier = MagicMock()
    verifier.verify.return_value = VerificationResult(verified=True, reason="OK")
    orchestrator = WorkflowOrchestrator(verifier_client=verifier)

    wf = orchestrator.create_workflow(repo_url="https://github.com/example/timeout", task="Test budget")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="step1", command="echo 1"),
        StepDefinition(id="s2", type="shell_command", name="step2", command="echo 2"),
    ]
    workflow_store.save(wf)

    # Temporarily mock MAX_WORKFLOW_TIME to 0 seconds so budget expires
    with patch.object(settings, "MAX_WORKFLOW_TIME", 0.001):
        time.sleep(0.01)
        finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.overall_status == WorkflowStatus.BUDGET_EXCEEDED
    assert "budget exceeded" in finished.final_result.lower()


# =========================================================================
# 7. Checkpointing, Resumption & Cancellation
# =========================================================================

def test_checkpointing_and_workflow_resumption():
    """Case 19: Checkpoint persistence allows resuming unverified steps without re-running verified ones."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test_checkpoint.db")
        storage = SQLiteCheckpointStorage(db_path=db_file)

        class StepTrackerVerifier(VerificationClient):
            def verify(self, execution_result: ExecutionResult) -> VerificationResult:
                return VerificationResult(verified=True, reason="OK")

        orchestrator = WorkflowOrchestrator(verifier_client=StepTrackerVerifier())

        wf = orchestrator.create_workflow(repo_url="https://github.com/example/resume", task="Test resume")
        wf.steps = [
            StepDefinition(id="s1", type="shell_command", name="Step 1", command="echo step1", status=StepStatus.VERIFIED_SUCCESS),
            StepDefinition(id="s2", type="shell_command", name="Step 2", command="echo step2", status=StepStatus.PENDING),
        ]
        storage.save_workflow(wf)

        # Re-load from storage (simulating server reboot)
        loaded_wf = storage.get_workflow(wf.workflow_id)
        assert loaded_wf is not None
        assert loaded_wf.steps[0].status == StepStatus.VERIFIED_SUCCESS

        # Resume execution
        with patch.object(workflow_store, "sqlite", storage):
            workflow_store.save(loaded_wf)
            finished = orchestrator.run_workflow(loaded_wf.workflow_id, resume=True)

        assert finished.overall_status == WorkflowStatus.COMPLETED
        assert finished.steps[0].status == StepStatus.VERIFIED_SUCCESS
        assert finished.steps[1].status == StepStatus.VERIFIED_SUCCESS


def test_workflow_cancellation():
    """Case 27: Workflow cancellation stops execution and marks CANCELLED."""
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/cancel", task="Test cancel")
    wf.overall_status = WorkflowStatus.CANCEL_REQUESTED
    workflow_store.save(wf)

    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="long_build", command="echo cancelled"),
    ]

    finished = orchestrator.run_workflow(wf.workflow_id)
    assert finished.overall_status == WorkflowStatus.CANCELLED
    assert "cancelled" in finished.final_result.lower()


# =========================================================================
# 8. Dry-Run Mode & Final Report
# =========================================================================

def test_dry_run_mode_produces_plan_without_execution():
    """Case 26: Dry-run mode produces validated plan with reasons, zero execution."""
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/dry-app",
        task="Check project health in dry-run mode",
        dry_run=True,
    )
    finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.overall_status == WorkflowStatus.COMPLETED
    assert "DRY RUN COMPLETE" in finished.final_result
    assert len(finished.steps) == 8
    # Zero command executions occurred
    for s in finished.steps:
        assert s.execution_result is None


def test_final_report_generation():
    """Case 28: Evidence-backed final report contains full verification summary & metrics."""
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/report-app", task="Report test")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Build", status=StepStatus.VERIFIED_SUCCESS),
    ]
    wf.steps[0].verification_result = VerificationResult(verified=True, reason="Clean build")
    wf.metrics = {"total_duration_seconds": 15.2}
    workflow_store.save(wf)

    report = orchestrator.get_final_report_data(wf.workflow_id)
    assert report is not None
    assert report.workflow_id == wf.workflow_id
    assert report.verification_summary["Build"] == "PASS"
    assert report.duration_seconds == 15.2


# =========================================================================
# 9. Concurrency & Workflow Isolation
# =========================================================================

def test_concurrent_workflow_isolation():
    """Case 24: Concurrent workflows maintain isolated workspaces, events, and results."""
    orchestrator = WorkflowOrchestrator(verifier_client=MagicMock(verify=lambda e: VerificationResult(verified=True)))

    wf_A = orchestrator.create_workflow(repo_url="https://github.com/example/app-A", task="Task A")
    wf_B = orchestrator.create_workflow(repo_url="https://github.com/example/app-B", task="Task B")

    assert wf_A.workflow_id != wf_B.workflow_id

    # Workspaces must be completely isolated
    base = Path(settings.WORKSPACE_BASE_DIR).resolve()
    ws_A = base / wf_A.workflow_id
    ws_B = base / wf_B.workflow_id
    assert ws_A != ws_B

    # Steps and events must not crosstalk
    wf_A.steps = [StepDefinition(id="s1", type="shell_command", name="A_step", command="echo A")]
    wf_B.steps = [StepDefinition(id="s1", type="shell_command", name="B_step", command="echo B")]
    workflow_store.save(wf_A)
    workflow_store.save(wf_B)

    fin_A = orchestrator.run_workflow(wf_A.workflow_id)
    fin_B = orchestrator.run_workflow(wf_B.workflow_id)

    assert fin_A.steps[0].name == "A_step"
    assert fin_B.steps[0].name == "B_step"
    assert len(fin_A.events) > 0
    assert all(e.workflow_id == fin_A.workflow_id for e in fin_A.events)
    assert all(e.workflow_id == fin_B.workflow_id for e in fin_B.events)


# =========================================================================
# 10. Additional Adversarial & Boundary Cases
# =========================================================================

def test_nonexistent_repository_clone_failure():
    """Case 7 & 8: Git clone of non-existent repository fails cleanly with structured evidence."""
    from backend.tools.git_tool import clone_repository
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "nonexistent"
        res = clone_repository("https://github.com/nonexistent-user-1234567/fake-repo-xyz", str(target), workflow_id="w-git")
        assert res.exit_code != 0
        assert res.step == "clone_repository"
        assert res.duration_ms >= 0


def test_build_error_classification():
    """Case 10: Syntax and compiler errors classified as BUILD_ERROR."""
    res = ExecutionResult(
        workflow_id="w-build",
        step="build_project",
        command="python -m py_compile main.py",
        exit_code=1,
        stderr="SyntaxError: invalid syntax (main.py, line 14)",
    )
    classification = classify_failure(res)
    assert classification.failure_type == FailureType.BUILD_ERROR
    assert "syntax" in classification.reason.lower()


def test_health_check_failure_returns_exit_code_1():
    """Case 13: Health check to inactive port captures connection error with exit code 1."""
    from backend.tools.http_tool import perform_health_check
    res = perform_health_check("http://127.0.0.1:59997/health", timeout_seconds=1, workflow_id="w-hc")
    assert res.exit_code == 1
    assert res.step == "health_check"
    err_str = (res.metadata.get("error", "") + " " + res.stderr).lower()
    assert any(k in err_str for k in ["connection_refused", "connect", "timed out", "timeout"])


def test_recovery_budget_exceeded():
    """Case 22: Reaching MAX_RECOVERY_ACTIONS budget halts the workflow."""
    class FlakyForeverVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Dependency error persistent",
                recovery_required=True,
                recovery_action="echo 'retry'",
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=FlakyForeverVerifier(), max_retries=10)
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/budget", task="Test budget")
    wf.steps = [StepDefinition(id="s1", type="shell_command", name="build", command="echo fail")]
    workflow_store.save(wf)

    # Force recovery budget to 2
    with patch.object(settings, "MAX_RECOVERY_ACTIONS", 2):
        finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert len(finished.recovery_history) <= 2


def test_tool_registry_lookup_and_dispatch():
    """Verify ToolRegistry resolves python, pip, npm, git, http correctly."""
    assert tool_registry.has("python")
    assert tool_registry.has("pip")
    assert tool_registry.has("npm")
    assert tool_registry.has("git")
    assert tool_registry.has("http")
    assert tool_registry.has("file")

    py_tool = tool_registry.resolve_tool_for_command("pytest -v")
    assert py_tool.name == "python"

    npm_tool = tool_registry.resolve_tool_for_command("npm install react")
    assert npm_tool.name == "npm"

    pip_tool = tool_registry.resolve_tool_for_command("pip install fastapi")
    assert pip_tool.name == "pip"

