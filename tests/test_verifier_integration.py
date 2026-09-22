"""
Integration Boundary Verification Test: Orchestrator <-> Verifier (and Frontend API access).
Tests according to the AgentGuard PRD specifications.
"""
from typing import List
import pytest
from fastapi.testclient import TestClient

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.verifier_client import MockVerifierClient, VerificationClient
from backend.main import app
from backend.models.workflow import (
    EventType,
    ExecutionResult,
    StepDefinition,
    StepStatus,
    VerificationResult,
    WorkflowState,
    WorkflowStatus,
)


def test_integration_boundary_contract_schemas():
    """
    1 & 2: Confirm ExecutionResult and VerificationResult exact schemas.
    """
    exec_res = ExecutionResult(
        workflow_id="wf_boundary_123",
        step="build_project",
        command="npm run build",
        exit_code=1,
        stdout="",
        stderr="Module not found: Error: Can't resolve 'axios'",
        duration_ms=1250.0,
        workspace="/test/workspace",
    )

    # Validate Orchestrator -> Verifier payload fields
    dumped = exec_res.model_dump()
    assert dumped["workflow_id"] == "wf_boundary_123"
    assert dumped["step"] == "build_project"
    assert dumped["action"] == "build_project"
    assert dumped["command"] == "npm run build"
    assert dumped["exit_code"] == 1
    assert "axios" in dumped["stderr"]
    assert dumped["duration_ms"] == 1250.0
    assert dumped["workspace"] == "/test/workspace"
    assert exec_res.actual["exit_code"] == 1
    assert exec_res.expected["exit_code"] == 0

    # Validate Verifier -> Orchestrator payload fields
    verif_res = VerificationResult(
        verified=False,
        reason="Missing dependency 'axios'",
        recovery_required=True,
        recovery_action="npm install axios",
        retry_allowed=True,
    )
    v_dumped = verif_res.model_dump()
    assert v_dumped["verified"] is False
    assert v_dumped["reason"] == "Missing dependency 'axios'"
    assert v_dumped["recovery_required"] is True
    assert v_dumped["recovery_action"] == "npm install axios"
    assert v_dumped["retry_allowed"] is True


def test_simulation_successful_build():
    """
    7a & 8a: Successful build flow:
    EXECUTE -> ExecutionResult -> VerificationClient -> PASS -> CONTINUE
    """
    class BuildSuccessVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            assert execution_result.step == "build_project"
            assert execution_result.exit_code == 0
            return VerificationResult(
                verified=True,
                reason="Build succeeded, binary artifact confirmed",
            )

    orchestrator = WorkflowOrchestrator(verifier_client=BuildSuccessVerifier())
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/good", task="Build app")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build_project", command="echo Build done"),
    ]
    workflow_store.save(wf)

    result = orchestrator.run_workflow(wf.workflow_id)

    assert result.overall_status == WorkflowStatus.COMPLETED
    assert result.steps[0].status == StepStatus.VERIFIED_SUCCESS
    assert result.retries == 0


def test_simulation_failed_build_no_recovery():
    """
    7b & 8b: Failed build without recovery requested:
    EXECUTE -> ExecutionResult -> VerificationClient -> FAIL -> HALT at VERIFIED_FAILURE
    """
    class BuildFatalFailureVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Fatal syntax error in source code",
                recovery_required=False,
                retry_allowed=False,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=BuildFatalFailureVerifier())
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/bad", task="Build app")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build_project", command="echo Fatal error"),
    ]
    workflow_store.save(wf)

    result = orchestrator.run_workflow(wf.workflow_id)

    # Must NOT produce SUCCESS
    assert result.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert result.steps[0].status == StepStatus.FAILED
    assert result.retries == 0


def test_simulation_missing_dependency_and_successful_recovery():
    """
    7c, 7d, 8c, 8d, 8e: Missing dependency -> FAIL -> Recovery executed -> Retry -> PASS
    """
    recovery_executed = []
    received_executions: List[ExecutionResult] = []

    class SelfHealingVerifier(VerificationClient):
        def __init__(self):
            self.attempts = 0

        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            received_executions.append(execution_result)
            self.attempts += 1
            if self.attempts == 1:
                return VerificationResult(
                    verified=False,
                    reason="Missing module: requests",
                    recovery_required=True,
                    recovery_action="echo 'installing requests'",
                    retry_allowed=True,
                )
            else:
                return VerificationResult(
                    verified=True,
                    reason="Build verified after recovery",
                    recovery_required=False,
                )

    orchestrator = WorkflowOrchestrator(verifier_client=SelfHealingVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/recoverable", task="Heal build")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build_project", command="echo building..."),
    ]
    workflow_store.save(wf)

    result = orchestrator.run_workflow(wf.workflow_id)

    # 1. Recovery action was executed
    events = [e.event_type for e in result.events]
    assert EventType.RECOVERY_STARTED.value in events
    assert EventType.RECOVERY_EXECUTED.value in events
    assert EventType.STEP_RETRY.value in events

    # 2. Original step was retried and sent to verifier a second time
    assert len(received_executions) == 2
    assert result.retries == 1
    assert result.steps[0].retries == 1

    # 3. Step verified after retry
    assert result.overall_status == WorkflowStatus.COMPLETED
    assert result.steps[0].status == StepStatus.VERIFIED_SUCCESS


def test_simulation_repeated_recovery_failure_and_maximum_retries():
    """
    7e, 7f, 8f: Repeated recovery failure halts at max retries with VERIFIED_FAILURE.
    """
    class ConstantFailureVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Dependency conflict unresolvable",
                recovery_required=True,
                recovery_action="echo 'retrying recovery'",
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=ConstantFailureVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/stuck", task="Test max retry")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="build_project", command="echo build fail"),
    ]
    workflow_store.save(wf)

    result = orchestrator.run_workflow(wf.workflow_id)

    # Must strictly stop at MAX_RETRIES (2) and result in VERIFIED_FAILURE
    assert result.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert result.steps[0].status == StepStatus.FAILED
    assert result.retries == 2
    assert result.steps[0].retries == 2
    assert "VERIFIED FAILURE" in (result.final_result or "")


def test_orchestrator_trusts_verifier_over_exit_code():
    """
    3 & 8g: Command exits with code 0, but verifier rejects it (e.g. security vulnerability found).
    Orchestrator MUST NOT mark it SUCCESS!
    """
    class RejectingVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            # Command had exit_code 0, but verifier found security defect in machine evidence
            return VerificationResult(
                verified=False,
                reason="Evidence shows high severity security vulnerability in artifact",
                recovery_required=False,
                retry_allowed=False,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=RejectingVerifier())
    wf = orchestrator.create_workflow(repo_url="https://github.com/example/insecure", task="Security check")
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="scan_artifacts", command="echo zero_exit_code"),
    ]
    workflow_store.save(wf)

    result = orchestrator.run_workflow(wf.workflow_id)

    # Even though shell command returned 0, verifier said False -> must be VERIFIED_FAILURE!
    assert result.steps[0].execution_result.exit_code == 0
    assert result.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert result.steps[0].status == StepStatus.FAILED


def test_member1_api_access_without_internal_orchestrator():
    """
    9: Confirm Frontend frontend can retrieve workflow status and timeline events
    strictly through the HTTP REST API.
    """
    client = TestClient(app, headers={"X-API-Key": "test-api-key"})

    # 1. Start workflow via API
    start_resp = client.post(
        "/workflow/start",
        json={"repo_url": "https://github.com/example/ui-test", "task": "Check project UI"},
    )
    assert start_resp.status_code == 201
    wf_id = start_resp.json()["workflow_id"]

    # 2. Get status via API
    status_resp = client.get(f"/workflow/{wf_id}/status")
    assert status_resp.status_code == 200
    state = status_resp.json()
    assert state["workflow_id"] == wf_id
    assert "overall_status" in state
    assert "steps" in state
    assert "retries" in state
    assert "events" in state

    # 3. Get events timeline via API
    events_resp = client.get(f"/workflow/{wf_id}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()
    assert isinstance(events, list)
    assert len(events) >= 1
    assert "event_type" in events[0]
    assert "timestamp" in events[0]
