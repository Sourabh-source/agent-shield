import socket
import tempfile
from pathlib import Path

import pytest

from backend.agent.recovery_planner import recovery_planner
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    RecoveryAttempt,
    RecoveryOutcome,
    RecoveryPlan,
    StepDefinition,
    WorkflowState,
)
from backend.tools.port_utils import is_port_in_use


def test_idempotency_port_already_free():
    """
    Test 4a: release_port and relocate_port idempotency.
    If the target port is already free, is_action_already_satisfied must return True.
    If the port is occupied, it must return False.
    """
    # 1. Free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    plan_free = RecoveryPlan(
        reason="Port conflict",
        failure_type="PORT_ERROR",
        action_type="release_port",
        tool="shell",
        target_step="Step 1",
        postcondition_type="port_free",
        postcondition_target=str(free_port),
    )
    assert recovery_planner.is_action_already_satisfied(plan_free) is True

    # 2. Occupied port
    occ_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occ_sock.bind(("127.0.0.1", 0))
    occ_sock.listen(1)
    occ_port = occ_sock.getsockname()[1]
    try:
        plan_occ = RecoveryPlan(
            reason="Port conflict",
            failure_type="PORT_ERROR",
            action_type="release_port",
            tool="shell",
            target_step="Step 1",
            postcondition_type="port_free",
            postcondition_target=str(occ_port),
        )
        assert recovery_planner.is_action_already_satisfied(plan_occ) is False
    finally:
        occ_sock.close()


def test_idempotency_extend_timeout_already_extended():
    """
    Test 4b: extend_timeout idempotency.
    If step.timeout_seconds is already >= plan.timeout_override, it is satisfied.
    """
    plan = RecoveryPlan(
        reason="Timeout",
        failure_type="TIMEOUT",
        action_type="extend_timeout",
        tool="python",
        target_step="Run slow task",
        timeout_override=60,
        postcondition_type="timeout_extended",
        postcondition_target="60",
    )

    # Step not yet extended
    step_low = StepDefinition(
        id="s1",
        type="shell_command",
        name="Run slow task",
        timeout_seconds=30,
    )
    assert recovery_planner.is_action_already_satisfied(plan, step=step_low) is False

    # Step already extended
    step_extended = StepDefinition(
        id="s1",
        type="shell_command",
        name="Run slow task",
        timeout_seconds=60,
    )
    assert recovery_planner.is_action_already_satisfied(plan, step=step_extended) is True


def test_idempotency_npm_verifies_valid_package_structure():
    """
    Test 4c: NPM package idempotency must not be fooled by empty or corrupted directory.
    Must verify valid structure (e.g. package.json exists).
    """
    plan = RecoveryPlan(
        reason="Missing express",
        failure_type="DEPENDENCY_ERROR",
        action_type="install_dependency",
        tool="npm",
        command="npm install express",
        target_step="Install step",
        postcondition_type="npm_package",
        postcondition_target="express",
    )

    with tempfile.TemporaryDirectory() as raw_ws:
        ws = Path(raw_ws).resolve()

        # 1. No node_modules -> False
        assert recovery_planner.is_action_already_satisfied(plan, workspace_dir=str(ws)) is False

        # 2. Empty/poisoned directory -> False
        mod_dir = ws / "node_modules" / "express"
        mod_dir.mkdir(parents=True, exist_ok=True)
        assert recovery_planner.is_action_already_satisfied(plan, workspace_dir=str(ws)) is False

        # 3. Valid directory with package.json -> True
        (mod_dir / "package.json").write_text('{"name": "express", "version": "4.18.2"}\n')
        assert recovery_planner.is_action_already_satisfied(plan, workspace_dir=str(ws)) is True


def test_workflow_ledger_detects_repeated_futile_recovery_action():
    """
    Test 4d: Per-workflow recovery ledger.
    If the exact same recovery action and command was already attempted and failed in this workflow,
    is_action_already_satisfied or ledger check prevents repeating the futile recovery.
    """
    plan = RecoveryPlan(
        reason="Missing invalid-pkg",
        failure_type="DEPENDENCY_ERROR",
        action_type="install_dependency",
        tool="pip",
        command="pip install nonexistent-nonexistent-foo-12345",
        target_step="Install step",
        postcondition_type="package_installed",
        postcondition_target="nonexistent-nonexistent-foo-12345",
    )

    # Prior attempt recorded as FAILED in workflow history
    prior_attempt = RecoveryAttempt(
        recovery_id=plan.recovery_id,
        step_name="Install step",
        failure_type=plan.failure_type,
        action=plan.command,
        status=RecoveryOutcome.FAILED,
        exit_code=1,
    )

    # Checking with workflow recovery history containing this failed attempt
    assert recovery_planner.is_action_futile(plan, recovery_history=[prior_attempt]) is True

    # With empty history, it is not futile
    assert recovery_planner.is_action_futile(plan, recovery_history=[]) is False
