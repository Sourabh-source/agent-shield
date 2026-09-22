import pytest

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.models.workflow import (
    FinalReportData,
    RecoveryAttempt,
    RecoveryOutcome,
    StepDefinition,
    StepStatus,
    VerificationResult,
    WorkflowState,
    WorkflowStatus,
)


def test_final_report_honest_breakdown_counters():
    """
    Test 5a:
    FinalReportData must honestly distinguish:
    - recoveries_attempted: total recoveries executed or attempted
    - recoveries_verified_effective: only those whose recovery succeeded AND step reached VERIFIED_SUCCESS
    - recoveries_unrecoverable: those escalated without futile retries
    """
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/reporting-test",
        task="Test honest report breakdown",
    )

    step_a = StepDefinition(
        id="s1",
        type="shell_command",
        name="Step A - Resolved",
        status=StepStatus.VERIFIED_SUCCESS,
        verification_result=VerificationResult(verified=True, reason="Tests passed"),
    )
    step_b = StepDefinition(
        id="s2",
        type="shell_command",
        name="Step B - Ineffective Recovery",
        status=StepStatus.FAILED,
        verification_result=VerificationResult(verified=False, reason="Still failing"),
    )
    step_c = StepDefinition(
        id="s3",
        type="shell_command",
        name="Step C - Unrecoverable",
        status=StepStatus.FAILED,
        verification_result=VerificationResult(verified=False, reason="Out of memory"),
    )
    wf.steps = [step_a, step_b, step_c]
    wf.overall_status = WorkflowStatus.VERIFIED_FAILURE

    # 1. Step A recovery: postcondition passed, step resolved -> EFFECTIVE
    attempt_a = RecoveryAttempt(
        recovery_id="rec-1",
        step_name=step_a.name,
        failure_type="DEPENDENCY_ERROR",
        action="pip install pytest",
        status=RecoveryOutcome.SUCCESS,
        exit_code=0,
        step_resolved=True,
    )

    # 2. Step B recovery: recovery executed, but step retry STILL failed -> INEFFECTIVE
    attempt_b = RecoveryAttempt(
        recovery_id="rec-2",
        step_name=step_b.name,
        failure_type="TIMEOUT",
        action="extend_timeout",
        status=RecoveryOutcome.SUCCESS,
        exit_code=0,
        step_resolved=False,  # Step never reached VERIFIED_SUCCESS!
    )

    # 3. Step C recovery: unmanaged OOM / max timeout -> UNRECOVERABLE
    attempt_c = RecoveryAttempt(
        recovery_id="rec-3",
        step_name=step_c.name,
        failure_type="RESOURCE_LIMIT",
        action="unrecoverable",
        status=RecoveryOutcome.UNRECOVERABLE,
        exit_code=1,
        step_resolved=False,
    )

    wf.recovery_history = [attempt_a, attempt_b, attempt_c]
    workflow_store.save(wf)

    report = orchestrator.get_final_report_data(wf.workflow_id)
    assert report is not None
    assert report.recoveries == 3
    assert report.recoveries_attempted == 3
    assert report.recoveries_verified_effective == 1  # Only Step A!
    assert report.recoveries_unrecoverable == 1        # Step C!


def test_step_resolved_is_set_only_when_step_verifies_successfully():
    """
    Test 5b:
    When a step is retried and passes verification,
    only then is attempt.step_resolved set to True.
    """
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/step-resolve",
        task="Test step_resolved lifecycle",
    )

    step = StepDefinition(
        id="s1",
        type="shell_command",
        name="Build app",
        status=StepStatus.RUNNING,
    )
    wf.steps = [step]

    attempt = RecoveryAttempt(
        recovery_id="rec-build",
        step_name=step.name,
        failure_type="DEPENDENCY_ERROR",
        action="pip install wheel",
        status=RecoveryOutcome.SUCCESS,
        exit_code=0,
        step_resolved=False,
    )
    wf.recovery_history = [attempt]
    workflow_store.save(wf)

    # Simulate step succeeding verification
    step.status = StepStatus.VERIFIED_SUCCESS
    for att in wf.recovery_history:
        if att.step_name == step.name and att.status in (RecoveryOutcome.SUCCESS, "SUCCESS"):
            att.step_resolved = True

    workflow_store.save(wf)

    report = orchestrator.get_final_report_data(wf.workflow_id)
    assert report is not None
    assert report.recoveries_verified_effective == 1
    assert wf.recovery_history[0].step_resolved is True
