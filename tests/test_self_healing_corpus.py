import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from backend.agent.executor import ToolExecutor
from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.recovery_planner import recovery_planner
from backend.config import settings
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    RecoveryAttempt,
    RecoveryOutcome,
    RecoveryPlan,
    StepDefinition,
    StepStatus,
    VerificationResult,
    WorkflowState,
    WorkflowStatus,
)
from backend.tools.port_utils import find_free_port, is_port_in_use


def test_corpus_real_timeout_extension_recovers_slow_process():
    """Corpus 1: Real slow process times out initially, recovers when duration is extended."""
    with tempfile.TemporaryDirectory() as raw_dir:
        ws = str(Path(raw_dir).resolve())
        script = Path(ws) / "slow_task.py"
        script.write_text("import time; time.sleep(1.8); print('SUCCESS_SLOW')\n")

        executor = ToolExecutor(workflow_id="wf-corpus-timeout")
        executor.workspace_dir = ws

        step = StepDefinition(
            id="s_slow",
            type="shell_command",
            name="Slow Subprocess",
            command=f'"{sys.executable}" slow_task.py',
            timeout_seconds=1,
        )

        # 1. Initial run times out
        res1 = executor.execute_step(step)
        assert res1.exit_code == 124

        # 2. Plan recovery
        clf = FailureClassification(
            failure_type=FailureType.TIMEOUT,
            reason="Step execution timed out",
        )
        plan = recovery_planner.generate_recovery_plan(exec_result=res1, classification=clf)
        assert plan.action_type == "extend_timeout"
        assert plan.timeout_override >= 3

        # 3. Apply recovery & verify postcondition
        step.timeout_seconds = plan.timeout_override
        assert recovery_planner.evaluate_postcondition(plan, step=step)

        # 4. Retry succeeds
        res2 = executor.execute_step(step)
        assert res2.exit_code == 0
        assert "SUCCESS_SLOW" in res2.stdout


def test_corpus_timeout_at_maximum_escalates_unrecoverable():
    """Corpus 2: Subprocess hitting MAX_STEP_TIME halts and escalates as UNRECOVERABLE."""
    res = ExecutionResult(
        workflow_id="wf-corpus-max-to",
        step="Very Long Task",
        command="sleep 1000",
        exit_code=124,
        timeout_seconds=settings.MAX_STEP_TIME,
    )
    clf = FailureClassification(
        failure_type=FailureType.TIMEOUT,
        reason=f"Exceeded MAX_STEP_TIME ({settings.MAX_STEP_TIME}s)",
    )
    plan = recovery_planner.generate_recovery_plan(exec_result=res, classification=clf)
    assert plan.action_type == "unrecoverable"
    assert plan.expected_outcome == RecoveryOutcome.UNRECOVERABLE
    assert plan.max_attempts == 0


def test_corpus_resource_limit_oom_escalates_unrecoverable():
    """Corpus 3: System OOM kill escalates as UNRECOVERABLE without fake gc.collect()."""
    res = ExecutionResult(
        workflow_id="wf-corpus-oom",
        step="OOM Task",
        command="python big_alloc.py",
        exit_code=137,
    )
    clf = FailureClassification(
        failure_type=FailureType.RESOURCE_LIMIT,
        reason="Killed by OS OOM killer (exit code 137)",
    )
    plan = recovery_planner.generate_recovery_plan(exec_result=res, classification=clf)
    assert plan.action_type == "unrecoverable"
    assert plan.expected_outcome == RecoveryOutcome.UNRECOVERABLE
    assert "gc.collect()" not in (plan.command or "")


def test_corpus_port_conflict_foreign_relocates_without_killing():
    """Corpus 4: Foreign process occupying port is left alive, step relocated to free port."""
    foreign_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    foreign_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    foreign_sock.bind(("127.0.0.1", 0))
    foreign_sock.listen(1)
    foreign_port = foreign_sock.getsockname()[1]

    try:
        res = ExecutionResult(
            workflow_id="wf-corpus-port",
            step="API Service",
            command=f"uvicorn app:main --port {foreign_port}",
            exit_code=1,
        )
        clf = FailureClassification(
            failure_type=FailureType.PORT_ERROR,
            reason=f"Port {foreign_port} already in use",
            details={"port": foreign_port, "spawned_pids": []},
        )
        plan = recovery_planner.generate_recovery_plan(exec_result=res, classification=clf)
        assert plan.action_type == "relocate_port"
        assert plan.new_port is not None and plan.new_port != foreign_port
        assert str(plan.new_port) in plan.rewritten_command
        assert "Stop-Process" not in (plan.command or "")

        # Foreign process is still alive and port remains occupied
        assert is_port_in_use(foreign_port) is True

        # Postcondition verifies new port is free and step was updated
        step = StepDefinition(
            id="s_port",
            type="start_application",
            name="API Service",
            command=plan.rewritten_command,
        )
        assert recovery_planner.evaluate_postcondition(plan, step=step) is True

    finally:
        foreign_sock.close()


def test_corpus_idempotency_prevents_duplicate_actions():
    """Corpus 5: Idempotency check prevents repeating already-satisfied actions."""
    free_port = find_free_port(start_port=22000)
    port_plan = RecoveryPlan(
        reason="Port check",
        failure_type="PORT_ERROR",
        action_type="release_port",
        tool="shell",
        target_step="Step 1",
        postcondition_type="port_free",
        postcondition_target=str(free_port),
    )
    assert recovery_planner.is_action_already_satisfied(port_plan) is True

    to_plan = RecoveryPlan(
        reason="Timeout check",
        failure_type="TIMEOUT",
        action_type="extend_timeout",
        tool="python",
        target_step="Step 1",
        timeout_override=60,
        postcondition_type="timeout_extended",
        postcondition_target="60",
    )
    step_to = StepDefinition(id="s", type="shell", name="Step 1", timeout_seconds=60)
    assert recovery_planner.is_action_already_satisfied(to_plan, step=step_to) is True


def test_corpus_futile_recovery_ledger_halts_loops():
    """Corpus 6: Futile recovery action ledger halts repeated attempts on identical actions."""
    plan = RecoveryPlan(
        reason="Install fail",
        failure_type="DEPENDENCY_ERROR",
        action_type="install_dependency",
        tool="pip",
        command="pip install imaginary_bogus_package_xyz",
        target_step="Install Step",
        postcondition_type="package_installed",
        postcondition_target="imaginary_bogus_package_xyz",
    )
    failed_attempt = RecoveryAttempt(
        recovery_id=plan.recovery_id,
        step_name=plan.target_step,
        failure_type=plan.failure_type,
        action=plan.command,
        status=RecoveryOutcome.FAILED,
        exit_code=1,
    )
    assert recovery_planner.is_action_futile(plan, recovery_history=[failed_attempt]) is True
