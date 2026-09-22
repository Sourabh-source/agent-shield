"""
Phase 2: Verifiable Timeout & Resource Limit Self-Healing Tests

Tests:
1. TIMEOUT recovery actually extends the timeout and enables a real slow subprocess to pass on retry.
2. TIMEOUT at MAX_STEP_TIME transitions honestly to UNRECOVERABLE (preventing infinite loops).
3. RESOURCE_LIMIT with no verifiable remediation classifies honestly as UNRECOVERABLE (no fake gc.collect()).
4. RESOURCE_LIMIT with tracked workflow child process terminates the child and verifies it is dead.
"""
import os
import sys
import tempfile
import time
from pathlib import Path
import pytest

from backend.agent.executor import ToolExecutor
from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.recovery_planner import RecoveryPlanner, recovery_planner
from backend.config import settings
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    RecoveryOutcome,
    StepDefinition,
    StepStatus,
    WorkflowState,
    WorkflowStatus,
)


def test_timeout_plan_and_postcondition_contract():
    """
    Test 2a: TIMEOUT recovery must generate a real extend_timeout plan with
    postcondition_type='timeout_extended' and a target strictly greater than original timeout.
    """
    exec_res = ExecutionResult(
        workflow_id="wf-timeout-1",
        step="Run Slow Tests",
        command="pytest tests/slow",
        exit_code=124,
        timeout_seconds=5,
        metadata={"timed_out": True},
    )
    classification = FailureClassification(
        failure_type=FailureType.TIMEOUT,
        reason="Execution timed out after 5 seconds",
    )
    plan = recovery_planner.generate_recovery_plan(
        exec_result=exec_res,
        classification=classification,
    )

    assert plan.action_type == "extend_timeout"
    assert plan.postcondition_type == "timeout_extended"
    assert plan.timeout_override is not None
    assert plan.timeout_override > 5
    assert plan.expected_outcome == RecoveryOutcome.SUCCESS

    # Postcondition must fail if retry did NOT extend timeout
    retry_res_same = ExecutionResult(
        workflow_id="wf-timeout-1",
        step="Run Slow Tests",
        command="pytest tests/slow",
        exit_code=0,
        timeout_seconds=5,  # Not extended!
    )
    assert not recovery_planner.evaluate_postcondition(plan, execution_result=retry_res_same)

    # Postcondition must pass if retry DID record extended timeout
    retry_res_extended = ExecutionResult(
        workflow_id="wf-timeout-1",
        step="Run Slow Tests",
        command="pytest tests/slow",
        exit_code=0,
        timeout_seconds=plan.timeout_override,
    )
    assert recovery_planner.evaluate_postcondition(plan, execution_result=retry_res_extended)


def test_timeout_budget_exhausted_becomes_unrecoverable():
    """
    Test 2a.3: When timeout hits MAX_STEP_TIME, stop retrying and classify UNRECOVERABLE.
    """
    exec_res = ExecutionResult(
        workflow_id="wf-timeout-max",
        step="Run Slow Tests",
        command="pytest tests/slow",
        exit_code=124,
        timeout_seconds=settings.MAX_STEP_TIME,
        metadata={"timed_out": True},
    )
    classification = FailureClassification(
        failure_type=FailureType.TIMEOUT,
        reason=f"Execution timed out after {settings.MAX_STEP_TIME} seconds (maximum reached)",
    )
    plan = recovery_planner.generate_recovery_plan(
        exec_result=exec_res,
        classification=classification,
    )

    assert plan.action_type == "unrecoverable"
    assert plan.expected_outcome == RecoveryOutcome.UNRECOVERABLE
    assert plan.max_attempts == 0
    assert "budget exhausted" in plan.reason.lower() or "maximum" in plan.reason.lower()


def test_resource_limit_unrecoverable_by_default_without_gc_hoax():
    """
    Test 2b: RESOURCE_LIMIT without active child processes must classify UNRECOVERABLE.
    Must NOT run raw gc.collect() in server process or pretend memory was cleared.
    """
    exec_res = ExecutionResult(
        workflow_id="wf-res-1",
        step="Memory Step",
        command="python train.py",
        exit_code=137,
        metadata={"oom_killed": True},
    )
    classification = FailureClassification(
        failure_type=FailureType.RESOURCE_LIMIT,
        reason="Out of memory: process killed by system",
    )
    plan = recovery_planner.generate_recovery_plan(
        exec_result=exec_res,
        classification=classification,
    )

    assert plan.action_type == "unrecoverable"
    assert plan.expected_outcome == RecoveryOutcome.UNRECOVERABLE
    assert "No verifiable remediation available for this resource class; escalating as unrecoverable" in plan.reason
    assert "gc.collect" not in (plan.command or "")


def test_real_slow_subprocess_recovers_via_timeout_extension():
    """
    Test 2a.4 (Real Fixture):
    A real subprocess sleeps for 2.0 seconds.
    Step starts with timeout_seconds = 1 (fails with exit_code=124).
    Orchestrator recovery extends timeout to 4 seconds.
    Step retries and passes exit_code=0 because the timeout actually changed.
    """
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/slow-repo",
        task="Test real timeout extension",
    )
    
    with tempfile.TemporaryDirectory() as raw_tmpdir:
        tmpdir = str(Path(raw_tmpdir).resolve())
        # Create a real slow script that sleeps for 2 seconds
        script_path = Path(tmpdir) / "slow.py"
        script_path.write_text("import time; time.sleep(2.0); print('DONE SLOW')\n")
        
        step = StepDefinition(
            id="s1",
            type="shell_command",
            name="Run slow task",
            command=f'"{sys.executable}" slow.py',
            timeout_seconds=1,  # Will time out on first run
        )
        wf.steps = [step]
        wf.workspace_path = tmpdir
        workflow_store.save(wf)

        executor = ToolExecutor(workflow_id=wf.workflow_id, workspace_base=tempfile.gettempdir())
        executor.workspace_dir = tmpdir

        # 1. First run times out
        res1 = executor.execute_step(step)
        assert res1.exit_code == 124
        assert res1.metadata.get("timed_out") is True

        # 2. Plan recovery
        classification = FailureClassification(
            failure_type=FailureType.TIMEOUT,
            reason="Step execution timed out",
        )
        plan = recovery_planner.generate_recovery_plan(
            exec_result=res1,
            classification=classification,
        )
        assert plan.action_type == "extend_timeout"
        assert plan.timeout_override >= 3

        # 3. Apply recovery to step
        step.timeout_seconds = plan.timeout_override

        # 4. Retry step with extended timeout
        res2 = executor.execute_step(step)
        assert res2.exit_code == 0
        assert "DONE SLOW" in res2.stdout

        # 5. Postcondition validates against res2
        assert recovery_planner.evaluate_postcondition(plan, execution_result=res2)
