from unittest.mock import MagicMock, patch
import pytest

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.verifier_client import VerificationClient
from backend.models.workflow import (
    EventType,
    ExecutionResult,
    StepDefinition,
    StepStatus,
    StepType,
    VerificationResult,
    WorkflowState,
    WorkflowStatus,
)


class DummyPassingVerifier(VerificationClient):
    """Always passes verification."""
    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        return VerificationResult(
            verified=True,
            reason="Dummy verified pass",
            recovery_required=False,
        )


class FlakyOnceVerifier(VerificationClient):
    """Fails on first call, provides recovery action, passes on second call."""
    def __init__(self):
        self.calls = 0

    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        self.calls += 1
        if self.calls == 1:
            return VerificationResult(
                verified=False,
                reason="Simulated build failure: missing package",
                recovery_required=True,
                recovery_action="echo 'Installing missing package'",
                retry_allowed=True,
            )
        return VerificationResult(
            verified=True,
            reason="Recovered build verified successfully",
            recovery_required=False,
        )


class AlwaysFailingVerifier(VerificationClient):
    """Always fails verification with recovery requested."""
    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        return VerificationResult(
            verified=False,
            reason="Persistent compiler error",
            recovery_required=True,
            recovery_action="echo 'Attempting re-build'",
            retry_allowed=True,
        )


def test_workflow_continues_after_pass():
    verifier = DummyPassingVerifier()
    orchestrator = WorkflowOrchestrator(verifier_client=verifier)

    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/healthy-app",
        task="Check project health",
    )

    # Supply custom minimal 2-step plan for fast test execution
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Step 1", command="echo s1"),
        StepDefinition(id="s2", type="shell_command", name="Step 2", command="echo s2"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    assert finished.overall_status == WorkflowStatus.COMPLETED
    assert all(s.status == StepStatus.VERIFIED_SUCCESS for s in finished.steps)
    assert finished.retries == 0

    event_types = [e.event_type for e in finished.events]
    assert EventType.WORKFLOW_STARTED.value in event_types
    assert EventType.STEP_STARTED.value in event_types
    assert EventType.VERIFICATION_PASSED.value in event_types
    assert EventType.STEP_VERIFIED.value in event_types
    assert EventType.WORKFLOW_COMPLETED.value in event_types


def test_workflow_recovery_and_retry_loop():
    verifier = FlakyOnceVerifier()
    orchestrator = WorkflowOrchestrator(verifier_client=verifier, max_retries=2)

    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/healing-app",
        task="Check project health with self-healing",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Build App", command="echo build"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    # Should have recovered and verified on 2nd attempt
    assert finished.overall_status == WorkflowStatus.COMPLETED
    assert finished.retries == 1
    assert finished.steps[0].retries == 1
    assert finished.steps[0].status == StepStatus.VERIFIED_SUCCESS

    event_types = [e.event_type for e in finished.events]
    assert EventType.VERIFICATION_FAILED.value in event_types
    assert EventType.RECOVERY_STARTED.value in event_types
    assert EventType.RECOVERY_EXECUTED.value in event_types
    assert EventType.STEP_RETRY.value in event_types
    assert EventType.VERIFICATION_PASSED.value in event_types
    assert EventType.WORKFLOW_COMPLETED.value in event_types


def test_maximum_retries_leads_to_verified_failure():
    verifier = AlwaysFailingVerifier()
    orchestrator = WorkflowOrchestrator(verifier_client=verifier, max_retries=2)

    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/broken-app",
        task="Check broken project",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Faulty Step", command="echo fail"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    # Must STOP at max_retries and never claim success
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert finished.steps[0].status == StepStatus.FAILED
    assert finished.steps[0].retries == 2
    assert finished.retries == 2
    assert "VERIFIED FAILURE" in (finished.final_result or "")

    event_types = [e.event_type for e in finished.events]
    assert EventType.WORKFLOW_FAILED.value in event_types
    assert EventType.WORKFLOW_COMPLETED.value not in event_types


def test_workflow_cannot_claim_success_without_verification():
    class RejectingVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Unverified claim rejected",
                recovery_required=False,
                retry_allowed=False,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=RejectingVerifier())
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/app",
        task="Check project",
    )
    # Even if shell command exits with 0, verifier says false!
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Falsely Succeeded Step", command="echo success"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    # Since verifier rejected it, status cannot be COMPLETED or SUCCESS
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert finished.steps[0].status == StepStatus.FAILED
