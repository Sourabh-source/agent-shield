"""
Unit and integration tests for Fix List generation in FinalReportData and API persistence.
"""

import pytest
from backend.agent.fix_generator import generate_recommended_fixes
from backend.agent.orchestrator import WorkflowOrchestrator
from backend.agent.verifier_client import MockVerifierClient
from backend.models.workflow import (
    ExecutionResult,
    FailureType,
    FinalReportData,
    RecommendedFix,
    RecommendedFixesSummary,
    RecoveryAttempt,
    RecoveryOutcome,
    StepDefinition,
    StepStatus,
    StepType,
    VerificationResult,
    WorkflowEvent,
    WorkflowState,
    WorkflowStatus,
)


class TestFixListGeneration:
    def test_clean_workflow_has_zero_issues(self):
        """Clean workflow with verified steps produces an empty fix list and zero counts."""
        workflow = WorkflowState(
            workflow_id="wf-clean",
            repository="https://github.com/example/clean-repo",
            task="Verify repository",
            overall_status=WorkflowStatus.COMPLETED,
            steps=[
                StepDefinition(
                    id="step-1",
                    type=StepType.CLONE_REPOSITORY.value,
                    name="Clone repository",
                    status=StepStatus.VERIFIED_SUCCESS,
                    verification_result=VerificationResult(verified=True, status="VERIFIED"),
                ),
                StepDefinition(
                    id="step-2",
                    type=StepType.INSTALL_DEPENDENCIES.value,
                    name="Install dependencies",
                    status=StepStatus.VERIFIED_SUCCESS,
                    verification_result=VerificationResult(verified=True, status="VERIFIED"),
                ),
                StepDefinition(
                    id="step-3",
                    type=StepType.RUN_TESTS.value,
                    name="Run tests",
                    status=StepStatus.VERIFIED_SUCCESS,
                    verification_result=VerificationResult(verified=True, status="VERIFIED"),
                ),
            ],
            retries=0,
        )

        fixes, summary = generate_recommended_fixes(workflow)

        assert fixes == []
        assert summary.issues_found == 0
        assert summary.recovered_automatically == 0
        assert summary.action_required == 0
        assert summary.retries == 0

    def test_recovered_issue_marked_recovered(self):
        """When an issue was recovered and step ultimately passed, status is RECOVERED."""
        workflow = WorkflowState(
            workflow_id="wf-recovered",
            repository="https://github.com/example/repo",
            task="Verify repository",
            overall_status=WorkflowStatus.COMPLETED,
            steps=[
                StepDefinition(
                    id="step-install",
                    type=StepType.INSTALL_DEPENDENCIES.value,
                    name="Install dependencies",
                    status=StepStatus.VERIFIED_SUCCESS,
                    retries=1,
                    verification_result=VerificationResult(verified=True, status="VERIFIED"),
                )
            ],
            recovery_history=[
                RecoveryAttempt(
                    recovery_id="rec-1",
                    step_name="Install dependencies",
                    failure_type="DEPENDENCY_ERROR",
                    action="pip install flask",
                    status=RecoveryOutcome.SUCCESS,
                    step_resolved=True,
                )
            ],
            events=[
                WorkflowEvent(
                    workflow_id="wf-recovered",
                    event_type="FAILURE_CLASSIFIED",
                    step="Install dependencies",
                    message="Failure classified as DEPENDENCY_ERROR: Missing Python module: flask",
                    evidence={
                        "failure_type": "DEPENDENCY_ERROR",
                        "reason": "Missing Python module: flask",
                        "details": {"module": "flask", "ecosystem": "python"},
                    },
                )
            ],
            retries=1,
        )

        fixes, summary = generate_recommended_fixes(workflow)

        assert len(fixes) == 1
        fix = fixes[0]
        assert fix.id == "FIX 01"
        assert fix.step == "Install dependencies"
        assert fix.status == "RECOVERED"
        assert "flask" in fix.title or "flask" in fix.what_happened
        assert "pip install flask" in (fix.recommended_fix or "") or "pip install flask" in (fix.recovery_attempted or "")
        assert fix.recovery_attempted == "pip install flask"
        assert "Recovery succeeded" in (fix.recovery_result or "")
        assert summary.issues_found == 1
        assert summary.recovered_automatically == 1
        assert summary.action_required == 0

    def test_unrecovered_pytest_winerror2_issue(self):
        """Pytest WinError 2 without successful recovery yields ACTION REQUIRED with python -m pytest fix."""
        workflow = WorkflowState(
            workflow_id="wf-pytest-fail",
            repository="https://github.com/example/repo",
            task="Verify repository",
            overall_status=WorkflowStatus.VERIFIED_FAILURE,
            steps=[
                StepDefinition(
                    id="step-tests",
                    type=StepType.RUN_TESTS.value,
                    name="Run tests",
                    status=StepStatus.FAILED,
                    command="pytest",
                    retries=2,
                    verification_result=VerificationResult(
                        verified=False,
                        status="FAILED",
                        failure_type="COMMAND_NOT_FOUND",
                        reason="[WinError 2] The system cannot find the file specified: 'pytest'",
                    ),
                    execution_result=ExecutionResult(
                        workflow_id="wf-pytest-fail",
                        step="Run tests",
                        command="pytest",
                        exit_code=127,
                        stderr="FileNotFoundError: [WinError 2] The system cannot find the file specified: 'pytest'",
                    ),
                )
            ],
            recovery_history=[
                RecoveryAttempt(
                    recovery_id="rec-pytest",
                    step_name="Run tests",
                    failure_type="COMMAND_NOT_FOUND",
                    action="pip install pytest",
                    status=RecoveryOutcome.FAILED,
                    step_resolved=False,
                )
            ],
            events=[
                WorkflowEvent(
                    workflow_id="wf-pytest-fail",
                    event_type="FAILURE_CLASSIFIED",
                    step="Run tests",
                    message="Failure classified as COMMAND_NOT_FOUND: Executable or command not found: pytest",
                    evidence={
                        "failure_type": "COMMAND_NOT_FOUND",
                        "reason": "Executable or command not found: pytest",
                        "details": {"missing_executable": "pytest", "ecosystem": "python"},
                        "source_evidence": "[WinError 2] The system cannot find the file specified",
                    },
                ),
                # Second retry event
                WorkflowEvent(
                    workflow_id="wf-pytest-fail",
                    event_type="FAILURE_CLASSIFIED",
                    step="Run tests",
                    message="Failure classified as COMMAND_NOT_FOUND: Executable or command not found: pytest",
                    evidence={
                        "failure_type": "COMMAND_NOT_FOUND",
                        "reason": "Executable or command not found: pytest",
                        "details": {"missing_executable": "pytest", "ecosystem": "python"},
                        "source_evidence": "[WinError 2] The system cannot find the file specified",
                    },
                ),
            ],
            retries=2,
        )

        fixes, summary = generate_recommended_fixes(workflow)

        # Must deduplicate repeated failures into ONE issue
        assert len(fixes) == 1
        fix = fixes[0]
        assert fix.id == "FIX 01"
        assert fix.step == "Run tests"
        assert fix.status == "ACTION REQUIRED"
        assert fix.retries == 2
        assert "pytest executable not found" in fix.title
        assert "WinError 2" in fix.what_happened
        assert "python -m pytest" in fix.recommended_fix
        assert summary.issues_found == 1
        assert summary.action_required == 1
        assert summary.recovered_automatically == 0
        assert summary.retries == 2

    def test_deduplicate_repeated_failures_from_retries(self):
        """Repeated identical failures during bounded retries are deduplicated to a single fix with Retries count."""
        workflow = WorkflowState(
            workflow_id="wf-dedup",
            repository="https://github.com/example/repo",
            task="Verify repository",
            overall_status=WorkflowStatus.VERIFIED_FAILURE,
            steps=[
                StepDefinition(
                    id="step-port",
                    type=StepType.START_APPLICATION.value,
                    name="Start application",
                    status=StepStatus.FAILED,
                    retries=2,
                    verification_result=VerificationResult(
                        verified=False,
                        status="FAILED",
                        failure_type="PORT_ERROR",
                        reason="Network port conflict: port 5000 is already in use",
                    ),
                )
            ],
            recovery_history=[
                RecoveryAttempt(
                    recovery_id="rec-1",
                    step_name="Start application",
                    failure_type="PORT_ERROR",
                    action="relocate_port to 5001",
                    status=RecoveryOutcome.FAILED,
                    step_resolved=False,
                ),
                RecoveryAttempt(
                    recovery_id="rec-2",
                    step_name="Start application",
                    failure_type="PORT_ERROR",
                    action="relocate_port to 5002",
                    status=RecoveryOutcome.FAILED,
                    step_resolved=False,
                ),
            ],
            events=[
                WorkflowEvent(
                    workflow_id="wf-dedup",
                    event_type="FAILURE_CLASSIFIED",
                    step="Start application",
                    message="Port conflict 5000",
                    evidence={"failure_type": "PORT_ERROR", "details": {"port": "5000"}},
                ),
                WorkflowEvent(
                    workflow_id="wf-dedup",
                    event_type="FAILURE_CLASSIFIED",
                    step="Start application",
                    message="Port conflict 5000",
                    evidence={"failure_type": "PORT_ERROR", "details": {"port": "5000"}},
                ),
            ],
            retries=2,
        )

        fixes, summary = generate_recommended_fixes(workflow)

        assert len(fixes) == 1
        assert fixes[0].retries == 2
        assert fixes[0].status == "ACTION REQUIRED"
        assert "port 5000 conflict" in fixes[0].title
        assert summary.issues_found == 1
        assert summary.action_required == 1
        assert summary.retries == 2

    def test_unverified_step_marked_unverified(self):
        """Incomplete or unverified steps receive UNVERIFIED status."""
        workflow = WorkflowState(
            workflow_id="wf-unverif",
            repository="https://github.com/example/repo",
            task="Verify repository",
            overall_status=WorkflowStatus.INCOMPLETE,
            steps=[
                StepDefinition(
                    id="step-smoke",
                    type=StepType.SMOKE_TEST.value,
                    name="Smoke test",
                    status=StepStatus.PENDING,
                    verification_result=VerificationResult(
                        verified=False,
                        status="UNVERIFIABLE",
                        reason="Verifier timed out",
                    ),
                )
            ],
            retries=0,
        )

        fixes, summary = generate_recommended_fixes(workflow)
        assert len(fixes) == 1
        assert fixes[0].status == "UNVERIFIED"

    def test_orchestrator_get_final_report_data_includes_fixes(self):
        """WorkflowOrchestrator.get_final_report_data attaches recommended_fixes and fixes_summary."""
        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/test/repo",
            task="Verify repository",
        )
        orchestrator.plan_workflow(workflow)
        # Mark the last step as failed
        target_step = workflow.steps[-1]
        target_step.status = StepStatus.FAILED
        target_step.verification_result = VerificationResult(
            verified=False,
            status="FAILED",
            failure_type="TEST_FAILURE",
            reason="AssertionError in test_main.py",
        )
        workflow.overall_status = WorkflowStatus.VERIFIED_FAILURE

        report = orchestrator.get_final_report_data(workflow.workflow_id)

        assert report is not None
        assert hasattr(report, "recommended_fixes")
        assert len(report.recommended_fixes) >= 1
        assert report.recommended_fixes[0].step == target_step.name
        assert report.recommended_fixes[0].status == "ACTION REQUIRED"
        assert report.fixes_summary is not None
        assert report.fixes_summary.issues_found >= 1
        assert report.fixes_summary.action_required >= 1

        # Persistence: model_dump -> model_validate roundtrip
        dumped = report.model_dump()
        restored = FinalReportData.model_validate(dumped)
        assert len(restored.recommended_fixes) == len(report.recommended_fixes)
        assert restored.recommended_fixes[0].id == "FIX 01"
        assert restored.fixes_summary.issues_found == report.fixes_summary.issues_found
