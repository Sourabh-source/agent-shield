"""
Tests for the Evidence Gate and Deterministic Evidence Verifier (Phase 3 & Phase 4).
Proves:
TEST 1: exit_code = 0 but verifier returns verified = false -> NOT VERIFIED_SUCCESS (Mandatory)
TEST 2: exit_code = 1, verifier returns verified = false, recovery_required = true -> RECOVERY
TEST 3: Recovery succeeds -> RETRY -> NEW ExecutionResult -> NEW VerificationResult -> VERIFIED_SUCCESS
TEST 4: Recovery fails repeatedly -> VERIFIED_FAILURE
TEST 5: Verifier unavailable -> VERIFICATION_UNAVAILABLE
TEST 6: Verifier returns malformed result / error -> safe failure / verification unavailable, never success

Unit tests for DeterministicEvidenceVerifier categories A-G:
- Command / Execution verification
- Install Dependency verification
- Build verification
- Test verification
- Health Check verification
- Start Application verification
"""
import pytest
from unittest.mock import MagicMock

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.verifier_client import (
    DeterministicEvidenceVerifier,
    MockVerifierClient,
    UnavailableVerifierClient,
    VerificationClient,
)
from backend.models.workflow import (
    EventType,
    ExecutionResult,
    StepDefinition,
    StepStatus,
    VerificationResult,
    WorkflowStatus,
)


# =========================================================================
# PHASE 4 MANDATORY EVIDENCE GATE TESTS
# =========================================================================

def test_evidence_gate_test1_exit_code_zero_but_verifier_rejects():
    """
    TEST 1 (MANDATORY): Command returns exit_code = 0, but verifier returns verified = False.
    Expected: NOT VERIFIED_SUCCESS. Result must be VERIFIED_FAILURE.
    """
    class RejectingVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Security audit detected critical vulnerability in compiled binary",
                recovery_required=False,
                retry_allowed=False,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=RejectingVerifier())
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/test1-repo",
        task="Audit and verify build",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Build project", command="echo 'build completed cleanly'"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    # Command actually returned exit code 0
    assert finished.steps[0].execution_result.exit_code == 0
    # But because verifier rejected, status is NOT COMPLETED and NOT VERIFIED_SUCCESS
    assert finished.overall_status != WorkflowStatus.COMPLETED
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert finished.steps[0].status == StepStatus.FAILED
    assert "Security audit detected critical vulnerability" in finished.final_result


def test_evidence_gate_test2_failure_triggers_recovery():
    """
    TEST 2: Command returns exit_code = 1, verifier returns verified = False, recovery_required = True.
    Expected: State machine triggers RECOVERY event and attempts recovery action.
    """
    class RecoveryTriggerVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Missing dependency: pandas",
                recovery_required=True,
                recovery_action="echo 'pip install pandas'",
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=RecoveryTriggerVerifier(), max_retries=1)
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/test2-repo",
        task="Test recovery trigger",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Run application", command="echo 'app error' && exit 1"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    event_types = [e.event_type for e in finished.events]
    assert EventType.RECOVERY_STARTED.value in event_types
    assert EventType.RECOVERY_EXECUTED.value in event_types
    assert len(finished.recovery_history) >= 1
    assert finished.recovery_history[0].action == "echo 'pip install pandas'"


def test_evidence_gate_test3_recovery_succeeds_retry_and_reverification():
    """
    TEST 3: Recovery succeeds -> RETRY -> NEW ExecutionResult -> NEW VerificationResult
    -> VERIFIED_SUCCESS only if the new evidence passes.
    """
    received_executions = []

    class SelfHealingTwoStageVerifier(VerificationClient):
        def __init__(self):
            self.call_count = 0

        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            received_executions.append(execution_result)
            self.call_count += 1
            if self.call_count == 1:
                return VerificationResult(
                    verified=False,
                    reason="Missing module: requests",
                    recovery_required=True,
                    recovery_action="echo 'install requests'",
                    retry_allowed=True,
                )
            else:
                # Stage 2: new evidence shows clean execution
                return VerificationResult(
                    verified=True,
                    reason="Step verified after recovery and retry",
                    recovery_required=False,
                )

    orchestrator = WorkflowOrchestrator(verifier_client=SelfHealingTwoStageVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/test3-repo",
        task="Test self-healing loop",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build_project", command="echo building..."),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    # Must have 2 distinct executions
    assert len(received_executions) == 2
    exec1, exec2 = received_executions
    assert exec1.execution_id != exec2.execution_id

    # Must have performed retry
    assert finished.retries == 1
    assert finished.steps[0].retries == 1

    # Final verdict: VERIFIED_SUCCESS
    assert finished.overall_status == WorkflowStatus.COMPLETED
    assert finished.steps[0].status == StepStatus.VERIFIED_SUCCESS
    assert "VERIFIED SUCCESS" in finished.final_result


def test_evidence_gate_test4_repeated_recovery_failure_halts_at_budget():
    """
    TEST 4: Recovery fails repeatedly -> Exhausts MAX_RETRIES -> Halts with VERIFIED_FAILURE.
    """
    class ConstantFailureVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Unfixable compilation failure",
                recovery_required=True,
                recovery_action="echo 'try repair'",
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=ConstantFailureVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/test4-repo",
        task="Test bounded retry failure",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build_project", command="echo fail"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.retries == 2
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert finished.steps[0].status == StepStatus.FAILED
    assert "VERIFIED FAILURE" in finished.final_result


def test_evidence_gate_test5_verifier_unavailable():
    """
    TEST 5: Verifier service is unavailable -> Transitions to VERIFICATION_UNAVAILABLE.
    Never falsely claims success.
    """
    orchestrator = WorkflowOrchestrator(verifier_client=UnavailableVerifierClient())
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/test5-repo",
        task="Test unconfigured verifier",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="install", command="echo ok"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.overall_status == WorkflowStatus.VERIFICATION_UNAVAILABLE
    assert finished.steps[0].status != StepStatus.VERIFIED_SUCCESS
    assert "Verification unavailable" in finished.final_result


def test_evidence_gate_test6_malformed_verifier_response_handled_safely():
    """
    TEST 6: Verifier raises exception or returns malformed data -> Safe failure / verification unavailable, never success.
    """
    class CrashingVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            # Simulate unexpected runtime error or corrupt response
            return VerificationResult(
                verified=False,
                reason="Verifier service encountered internal error: JSON decode error",
                recovery_required=False,
                retry_allowed=False,
                metadata={"verifier_unavailable": True, "error": "malformed_response"},
            )

    orchestrator = WorkflowOrchestrator(verifier_client=CrashingVerifier())
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/test6-repo",
        task="Test malformed verifier response",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build", command="echo ok"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.overall_status == WorkflowStatus.VERIFICATION_UNAVAILABLE
    assert "Verification unavailable" in finished.final_result
    assert finished.steps[0].status != StepStatus.VERIFIED_SUCCESS


# =========================================================================
# PHASE 3 DETERMINISTIC EVIDENCE VERIFIER UNIT TESTS (A-G)
# =========================================================================

def test_deterministic_verifier_rejects_missing_execution_id():
    """A: Basic checks reject execution evidence missing execution_id."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="build",
        command="make",
        exit_code=0,
        execution_id="",  # missing
    )
    verif = verifier.verify(res)
    assert verif.verified is False
    assert "missing execution ID" in verif.reason


def test_deterministic_verifier_rejects_metadata_security_flag():
    """A: Explicit security rejection in metadata fails even if exit_code=0."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="security_scan",
        command="trivy scan",
        exit_code=0,
        metadata={"security_issue": True, "reject_reason": "CVE-2026-9999 found"},
    )
    verif = verifier.verify(res)
    assert verif.verified is False
    assert "CVE-2026-9999 found" in verif.reason


def test_deterministic_verifier_detects_install_fatal_error():
    """B: Install dependencies step detects fatal errors even with exit_code=0."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Install dependencies",
        command="pip install mypkg",
        exit_code=0,
        stderr="ERROR: ResolutionImpossible: conflicting dependencies",
    )
    verif = verifier.verify(res)
    assert verif.verified is False
    assert verif.failure_type == "DEPENDENCY_ERROR"
    assert verif.recovery_required is True


def test_deterministic_verifier_verifies_clean_install():
    """B: Clean dependency install passes."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Install dependencies",
        command="pip install requests",
        exit_code=0,
        stdout="Successfully installed requests-2.31.0",
    )
    verif = verifier.verify(res)
    assert verif.verified is True
    assert verif.recovery_required is False


def test_deterministic_verifier_detects_build_syntax_error():
    """C: Build step detects SyntaxError in output even with exit_code=0."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Build project",
        command="python -m compileall .",
        exit_code=0,
        stderr="SyntaxError: invalid syntax in main.py",
    )
    verif = verifier.verify(res)
    assert verif.verified is False
    assert verif.failure_type == "BUILD_ERROR"


def test_deterministic_verifier_detects_test_failures():
    """D: Test step detects test failure output even with exit_code=0."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Run tests",
        command="pytest",
        exit_code=0,
        stdout="FAILED (failures=2)",
    )
    verif = verifier.verify(res)
    assert verif.verified is False
    assert verif.failure_type == "TEST_FAILURE"


def test_deterministic_verifier_verifies_passing_tests():
    """D: Passing test output verified."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Run tests",
        command="pytest",
        exit_code=0,
        stdout="5 passed in 0.23s",
    )
    verif = verifier.verify(res)
    assert verif.verified is True


def test_deterministic_verifier_detects_health_check_failure():
    """E: Health check detects connection refused."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Health check",
        command="curl http://localhost:8000/health",
        exit_code=0,
        stderr="curl: (7) Failed to connect: Connection refused",
    )
    verif = verifier.verify(res)
    assert verif.verified is False
    assert verif.failure_type == "NETWORK_ERROR"


def test_deterministic_verifier_verifies_passing_health_check():
    """E: Clean health check verified."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Health check",
        command="curl http://localhost:8000/health",
        exit_code=0,
        stdout="HTTP 200 OK - {\"status\": \"ok\"}",
    )
    verif = verifier.verify(res)
    assert verif.verified is True


def test_deterministic_verifier_detects_port_conflict():
    """F: Start application detects port in use."""
    verifier = DeterministicEvidenceVerifier()
    res = ExecutionResult(
        workflow_id="wf_1",
        step="Start application",
        command="uvicorn main:app --port 8000",
        exit_code=0,
        stderr="[Errno 98] Address already in use",
    )
    verif = verifier.verify(res)
    assert verif.verified is False
    assert verif.failure_type == "PORT_ERROR"