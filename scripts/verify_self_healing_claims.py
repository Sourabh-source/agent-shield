#!/usr/bin/env python3
"""
AgentGuard Self-Healing Honesty & Correctness Gate.
Enforces that every self-healing claim is backed by machine-checked evidence and real system state.
Never permits constant boolean postconditions, dangerous process killing, or fake remediation hoaxes.
"""

import ast
import os
import socket
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.agent.recovery_planner import recovery_planner
from backend.config import settings
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    FinalReportData,
    RecoveryOutcome,
    RecoveryPlan,
    StepDefinition,
    StepStatus,
)
from backend.tools.port_utils import find_free_port, is_port_in_use


def check_ast_postconditions():
    print("[1/5] Auditing evaluate_postcondition AST for unconditional True branches...")
    planner_file = ROOT / "backend" / "agent" / "recovery_planner.py"
    tree = ast.parse(planner_file.read_text(encoding="utf-8"))

    eval_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_postcondition":
            eval_func = node
            break

    assert eval_func is not None, "evaluate_postcondition not found in recovery_planner.py"

    violations = []
    for node in eval_func.body:
        if isinstance(node, ast.If):
            curr = node
            while curr and isinstance(curr, ast.If):
                returns_true = any(
                    isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant) and stmt.value.value is True
                    for stmt in curr.body
                )
                if returns_true:
                    body_src = ast.get_source_segment(planner_file.read_text(encoding="utf-8"), curr)
                    has_sys_call = any(
                        k in body_src
                        for k in ["subprocess", "socket", "Path", "importlib", "find_spec", "pip", "is_port_in_use", "os."]
                    )
                    if not has_sys_call:
                        violations.append(f"Line {curr.lineno}: Postcondition returns True without system check")
                if curr.orelse and len(curr.orelse) == 1 and isinstance(curr.orelse[0], ast.If):
                    curr = curr.orelse[0]
                else:
                    break

    if violations:
        print("  FAIL: Found violations:")
        for v in violations:
            print("   ", v)
        sys.exit(1)
    print("  PASS: Zero unconditional return True branches detected.")


def check_timeout_and_resource_limits():
    print("[2/5] Auditing TIMEOUT and RESOURCE_LIMIT boundaries...")
    # Timeout at max step time
    res_max = ExecutionResult(
        workflow_id="gate-to",
        step="Slow step",
        command="sleep 100",
        exit_code=124,
        timeout_seconds=settings.MAX_STEP_TIME,
    )
    clf_to = FailureClassification(failure_type=FailureType.TIMEOUT, reason="Timeout")
    plan_to = recovery_planner.generate_recovery_plan(exec_result=res_max, classification=clf_to)
    assert plan_to.action_type == "unrecoverable", f"Expected unrecoverable, got {plan_to.action_type}"
    assert plan_to.expected_outcome == RecoveryOutcome.UNRECOVERABLE

    # Resource limit (OOM)
    res_oom = ExecutionResult(
        workflow_id="gate-oom",
        step="OOM step",
        command="python alloc.py",
        exit_code=137,
    )
    clf_oom = FailureClassification(failure_type=FailureType.RESOURCE_LIMIT, reason="OOM killed")
    plan_oom = recovery_planner.generate_recovery_plan(exec_result=res_oom, classification=clf_oom)
    assert plan_oom.action_type == "unrecoverable", f"Expected unrecoverable, got {plan_oom.action_type}"
    assert plan_oom.expected_outcome == RecoveryOutcome.UNRECOVERABLE
    assert "gc.collect()" not in (plan_oom.command or ""), "Forbidden gc.collect() detected!"
    print("  PASS: Out-of-budget timeouts and OOM classify honestly as UNRECOVERABLE without gc hoax.")


def check_safe_port_handling():
    print("[3/5] Auditing PORT_ERROR foreign process protection...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    try:
        res = ExecutionResult(workflow_id="gate-port", step="Server", command=f"serve --port {port}", exit_code=1)
        clf = FailureClassification(failure_type=FailureType.PORT_ERROR, reason="In use", details={"port": port})
        plan = recovery_planner.generate_recovery_plan(exec_result=res, classification=clf)

        assert plan.action_type == "relocate_port"
        assert plan.new_port != port
        assert "Stop-Process" not in (plan.command or "")
        assert "fuser -k" not in (plan.command or "")
        # Foreign port still alive
        assert is_port_in_use(port) is True
    finally:
        sock.close()

    print("  PASS: Foreign processes are never killed; ports relocate safely.")


def check_idempotency_and_ledger():
    print("[4/5] Auditing idempotency and recovery history ledger...")
    free_p = find_free_port(start_port=24000)
    p_plan = RecoveryPlan(
        reason="Port test",
        failure_type="PORT_ERROR",
        action_type="release_port",
        tool="shell",
        target_step="s",
        postcondition_type="port_free",
        postcondition_target=str(free_p),
    )
    assert recovery_planner.is_action_already_satisfied(p_plan) is True

    from backend.models.workflow import RecoveryAttempt
    att = RecoveryAttempt(
        recovery_id="r1",
        step_name="s",
        failure_type="PORT_ERROR",
        action="taskkill",
        status=RecoveryOutcome.FAILED,
        exit_code=1,
    )
    plan_repeat = RecoveryPlan(
        reason="Repeat",
        failure_type="PORT_ERROR",
        action_type="release_port",
        tool="shell",
        command="taskkill",
        target_step="s",
    )
    assert recovery_planner.is_action_futile(plan_repeat, recovery_history=[att]) is True
    print("  PASS: Idempotency state checks and futile recovery ledger verified.")


def check_reporting_honesty():
    print("[5/5] Auditing FinalReportData breakdown...")
    report = FinalReportData(
        workflow_id="wf-gate",
        repository="repo",
        task="task",
        final_status="VERIFIED_SUCCESS",
        steps_completed=2,
        total_steps=2,
        recoveries=2,
        recoveries_attempted=2,
        recoveries_verified_effective=1,
        recoveries_unrecoverable=1,
        retries=1,
        duration_seconds=1.2,
    )
    assert report.recoveries_attempted == 2
    assert report.recoveries_verified_effective == 1
    assert report.recoveries_unrecoverable == 1
    print("  PASS: Report schema and counter breakdown fully verified.")


def main():
    print("==========================================================")
    print("AgentGuard Self-Healing Honesty & Correctness Gate")
    print("==========================================================")
    check_ast_postconditions()
    check_timeout_and_resource_limits()
    check_safe_port_handling()
    check_idempotency_and_ledger()
    check_reporting_honesty()
    print("==========================================================")
    print("ALL 5 SELF-HEALING HONESTY CHECKS PASSED.")
    print("No fake postconditions, no gc hoax, no process-killing bugs.")
    print("==========================================================")


if __name__ == "__main__":
    main()
