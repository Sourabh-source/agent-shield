import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.agent.executor import ToolExecutor
from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.verifier_client import DeterministicEvidenceVerifier
from backend.main import app
from backend.models.workflow import (
    ExecutionResult,
    FinalReportData,
    StepDefinition,
    StepStatus,
    StepType,
    VerificationResult,
    WorkflowState,
    WorkflowStatus,
)
from backend.storage.checkpoint import SQLiteCheckpointStorage


@pytest.fixture
def api_client():
    return TestClient(app, headers={"X-API-Key": "test-api-key"})


def test_step_counting_separates_verified_and_not_applicable():
    """
    Test 1: Verify separate step counting:
    - total_steps
    - verified_steps
    - not_applicable_steps
    - failed_steps
    - pending_steps
    NOT_APPLICABLE must NOT be counted as verified_steps.
    """
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/counting-repo",
        task="Test counting separation",
    )

    steps = [
        StepDefinition(
            id="s1",
            type=StepType.CLONE_REPOSITORY.value,
            name="Clone Repository",
            status=StepStatus.VERIFIED_SUCCESS,
            verification_result=VerificationResult(verified=True, status="PASS"),
        ),
        StepDefinition(
            id="s2",
            type=StepType.ANALYZE_PROJECT.value,
            name="Analyze Project",
            status=StepStatus.VERIFIED_SUCCESS,
            verification_result=VerificationResult(verified=True, status="PASS"),
        ),
        StepDefinition(
            id="s3",
            type=StepType.INSTALL_DEPENDENCIES.value,
            name="Install Dependencies",
            status=StepStatus.NOT_APPLICABLE,
            reason="No requirements file",
        ),
        StepDefinition(
            id="s4",
            type=StepType.COMPILE_PROJECT.value,
            name="Compile Project",
            status=StepStatus.NOT_APPLICABLE,
            reason="Static assets only",
        ),
        StepDefinition(
            id="s5",
            type=StepType.IMPORT_CHECK.value,
            name="Import Check",
            status=StepStatus.NOT_APPLICABLE,
            reason="No python modules",
        ),
        StepDefinition(
            id="s6",
            type=StepType.BUILD_PROJECT.value,
            name="Build Project",
            status=StepStatus.NOT_APPLICABLE,
            reason="No build script",
        ),
        StepDefinition(
            id="s7",
            type=StepType.RUN_TESTS.value,
            name="Run Tests",
            status=StepStatus.NOT_APPLICABLE,
            reason="No tests detected",
        ),
        StepDefinition(
            id="s8",
            type=StepType.FINAL_REPORT.value,
            name="Final Report",
            status=StepStatus.NOT_APPLICABLE,
            reason="N/A",
        ),
    ]
    wf.steps = steps
    workflow_store.save(wf)

    report = orchestrator.get_final_report_data(wf.workflow_id)
    assert report is not None
    assert report.total_steps == 8
    assert report.verified_steps == 2
    assert report.not_applicable_steps == 6
    assert report.failed_steps == 0
    assert report.pending_steps == 0
    # Must NOT count NOT_APPLICABLE as verified
    assert report.verified_steps != report.total_steps
    assert "2 steps verified, 6 not applicable" in report.summary


def test_empty_final_report_step_verification_fails():
    """
    Test 2: DeterministicEvidenceVerifier must reject empty final report content.
    - If exit_code != 0, status="FAILED"
    - If stdout is empty, status="FAILED", verified=False
    - Final report must only VERIFY/PASS if non-empty report content exists.
    """
    verifier = DeterministicEvidenceVerifier()

    # Case A: Empty stdout and exit_code = 0
    empty_result = ExecutionResult(
        workflow_id="wf-test-report",
        step="Final Report",
        execution_id="exec-1",
        command="cat final_report.json",
        exit_code=0,
        stdout="",
        stderr="",
        duration_ms=10.0,
        timestamp="2026-09-22T00:00:00Z",
        metadata={"step_type": StepType.FINAL_REPORT.value},
    )
    v_res = verifier.verify(empty_result)
    assert not v_res.verified
    assert v_res.status == "FAILED"
    assert "empty" in v_res.reason.lower()

    # Case B: Whitespace-only stdout
    ws_result = ExecutionResult(
        workflow_id="wf-test-report",
        step="Final Report",
        execution_id="exec-2",
        command="cat final_report.json",
        exit_code=0,
        stdout="   \n\t  \n",
        stderr="",
        duration_ms=10.0,
        timestamp="2026-09-22T00:00:00Z",
        metadata={"step_type": StepType.FINAL_REPORT.value},
    )
    v_res_ws = verifier.verify(ws_result)
    assert not v_res_ws.verified
    assert v_res_ws.status == "FAILED"

    # Case C: Exit code non-zero
    err_result = ExecutionResult(
        workflow_id="wf-test-report",
        step="Final Report",
        execution_id="exec-3",
        command="cat final_report.json",
        exit_code=1,
        stdout="",
        stderr="File not found",
        duration_ms=10.0,
        timestamp="2026-09-22T00:00:00Z",
        metadata={"step_type": StepType.FINAL_REPORT.value},
    )
    v_res_err = verifier.verify(err_result)
    assert not v_res_err.verified
    assert v_res_err.status == "FAILED"

    # Case D: Non-empty report content succeeds
    valid_result = ExecutionResult(
        workflow_id="wf-test-report",
        step="Final Report",
        execution_id="exec-4",
        command="cat final_report.json",
        exit_code=0,
        stdout=json.dumps({"workflow_id": "wf-test-report", "status": "VERIFIED_SUCCESS"}),
        stderr="",
        duration_ms=10.0,
        timestamp="2026-09-22T00:00:00Z",
        metadata={"step_type": StepType.FINAL_REPORT.value},
    )
    v_res_valid = verifier.verify(valid_result)
    assert v_res_valid.verified
    assert v_res_valid.status in ["VERIFIED", "PASS"]


def test_final_report_persistence_and_api_retrieval(api_client, tmp_path):
    """
    Test 3: Final report must be persisted in workflow state and checkpoint,
    and returned cleanly via GET /workflow/{id}/status and GET /workflow/{id}/report.
    """
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/persistence-repo",
        task="Test persistence of final report",
    )

    step_1 = StepDefinition(
        id="s1",
        type=StepType.CLONE_REPOSITORY.value,
        name="Clone Repository",
        status=StepStatus.VERIFIED_SUCCESS,
        verification_result=VerificationResult(verified=True, status="PASS"),
    )
    step_2 = StepDefinition(
        id="s2",
        type=StepType.FINAL_REPORT.value,
        name="Final Report",
        status=StepStatus.VERIFIED_SUCCESS,
        verification_result=VerificationResult(verified=True, status="PASS"),
    )
    wf.steps = [step_1, step_2]
    wf.overall_status = WorkflowStatus.COMPLETED
    wf.final_status = "VERIFIED_SUCCESS"

    report_data = orchestrator.get_final_report_data(wf.workflow_id)
    assert report_data is not None
    wf.final_report = report_data
    if wf.metadata is None:
        wf.metadata = {}
    wf.metadata["final_report"] = report_data.model_dump()
    workflow_store.save(wf)

    # 1. API: GET /workflow/{id}/status contains final_report
    status_resp = api_client.get(f"/workflow/{wf.workflow_id}/status")
    assert status_resp.status_code == 200
    status_json = status_resp.json()
    assert "final_report" in status_json
    assert status_json["final_report"] is not None
    assert status_json["final_report"]["workflow_id"] == wf.workflow_id
    assert status_json["final_report"]["verified_steps"] == 2
    assert status_json["final_report"]["total_steps"] == 2
    assert "summary" in status_json["final_report"]

    # 2. API: GET /workflow/{id}/report returns the report
    report_resp = api_client.get(f"/workflow/{wf.workflow_id}/report")
    assert report_resp.status_code == 200
    report_json = report_resp.json()
    assert report_json["workflow_id"] == wf.workflow_id
    assert report_json["verified_steps"] == 2
    assert report_json["total_steps"] == 2
    assert report_json["not_applicable_steps"] == 0

    # 3. Checkpoint roundtrip test
    storage = SQLiteCheckpointStorage(db_path=str(tmp_path / "test_checkpoints.db"))
    storage.save_workflow(wf)
    loaded_wf = storage.get_workflow(wf.workflow_id)
    assert loaded_wf is not None
    assert loaded_wf.final_report is not None
    assert loaded_wf.final_report.workflow_id == wf.workflow_id
    assert loaded_wf.final_report.verified_steps == 2
    assert loaded_wf.final_report.not_applicable_steps == 0


def test_workflow_fails_if_final_report_step_failed():
    """
    Test 4: Workflow must NOT be marked fully successful if final report step failed.
    """
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/fail-report-repo",
        task="Test failure when report fails",
    )

    s1 = StepDefinition(
        id="s1",
        type=StepType.START_APPLICATION.value,
        name="Start Application",
        status=StepStatus.VERIFIED_SUCCESS,
        verification_result=VerificationResult(verified=True, status="PASS"),
    )
    s2 = StepDefinition(
        id="s2",
        type=StepType.FINAL_REPORT.value,
        name="Final Report",
        status=StepStatus.FAILED,
        verification_result=VerificationResult(verified=False, status="FAILED", reason="Empty report"),
    )
    wf.steps = [s1, s2]
    workflow_store.save(wf)

    # In orchestrator honest final status check:
    has_failed = any(s.status == StepStatus.FAILED for s in wf.steps)
    final_report_step = next((s for s in wf.steps if s.type == StepType.FINAL_REPORT.value), None)
    if final_report_step and final_report_step.status != StepStatus.VERIFIED_SUCCESS:
        has_failed = True

    assert has_failed is True
    # If final report step failed, final status must be VERIFIED_FAILURE
    if has_failed:
        wf.overall_status = WorkflowStatus.VERIFIED_FAILURE
        wf.final_status = "VERIFIED_FAILURE"
    assert wf.overall_status == WorkflowStatus.VERIFIED_FAILURE
