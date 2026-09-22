"""
Phase 1: Self-Healing Contract Enforcement Tests

Tests:
1. AST Static Scan: evaluate_postcondition MUST NOT contain any unconditional 'return True' branches.
2. RecoveryOutcome enum exists with exact 4 states: SUCCESS, FAILED, UNVERIFIABLE, UNRECOVERABLE.
3. RecoveryPlan requires outcome self-declaration and distinguishes fixable from unrecoverable failures.
4. RecoveryAttempt captures RecoveryOutcome and step_resolved status.
"""
import ast
from pathlib import Path
import pytest

from backend.agent.recovery_planner import RecoveryPlanner, recovery_planner
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    RecoveryAttempt,
    RecoveryOutcome,
    RecoveryPlan,
    StepDefinition,
    StepStatus,
)


def test_ast_scan_no_unconditional_return_true_in_postconditions():
    """
    Operating Rule 2: NO POSTCONDITION MAY RETURN A CONSTANT.
    Static-scan evaluate_postcondition: no post_type branch may unconditionally return True.
    """
    planner_file = Path(__file__).resolve().parent.parent / "backend" / "agent" / "recovery_planner.py"
    content = planner_file.read_text(encoding="utf-8")
    tree = ast.parse(content)

    eval_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate_postcondition":
            eval_func = node
            break

    assert eval_func is not None, "evaluate_postcondition not found in recovery_planner.py"

    violations = []
    # Check top-level if/elif branches in evaluate_postcondition
    for node in eval_func.body:
        if isinstance(node, ast.If):
            curr = node
            while curr and isinstance(curr, ast.If):
                # If the branch contains a return True
                returns_true = any(
                    isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant) and stmt.value.value is True
                    for stmt in curr.body
                )
                if returns_true:
                    body_src = ast.get_source_segment(content, curr)
                    has_sys_call = any(
                        k in body_src for k in ["subprocess", "socket", "Path", "importlib", "find_spec", "pip", "psutil", "os."]
                    )
                    if not has_sys_call:
                        violations.append(f"Line {curr.lineno}: Postcondition branch returns True without system state check")
                if curr.orelse and len(curr.orelse) == 1 and isinstance(curr.orelse[0], ast.If):
                    curr = curr.orelse[0]
                else:
                    break

    assert not violations, "Found postcondition branches that unconditionally return True:\n" + "\n".join(violations)


def test_recovery_outcome_enum_contract():
    """Ensure RecoveryOutcome defines all 4 required contract states and is a string enum."""
    assert hasattr(RecoveryOutcome, "SUCCESS")
    assert hasattr(RecoveryOutcome, "FAILED")
    assert hasattr(RecoveryOutcome, "UNVERIFIABLE")
    assert hasattr(RecoveryOutcome, "UNRECOVERABLE")

    assert RecoveryOutcome.SUCCESS.value == "SUCCESS"
    assert RecoveryOutcome.FAILED.value == "FAILED"
    assert RecoveryOutcome.UNVERIFIABLE.value == "UNVERIFIABLE"
    assert RecoveryOutcome.UNRECOVERABLE.value == "UNRECOVERABLE"


def test_recovery_plan_self_classification():
    """
    A RecoveryPlan without an automated fix must self-classify as UNRECOVERABLE.
    """
    classification = FailureClassification(
        failure_type=FailureType.SYNTAX_ERROR,
        reason="SyntaxError: invalid syntax in repo code",
    )
    exec_res = ExecutionResult(
        workflow_id="wf-test",
        step="Compile",
        command="python main.py",
        exit_code=1,
    )
    plan = recovery_planner.generate_recovery_plan(
        exec_result=exec_res,
        classification=classification,
        suggested_action=None,
    )

    assert plan.expected_outcome == RecoveryOutcome.UNRECOVERABLE
    assert plan.max_attempts == 0
    assert plan.command is None or plan.action_type == "unrecoverable"


def test_recovery_attempt_tracks_outcome_and_resolution():
    """
    RecoveryAttempt must record RecoveryOutcome and whether the retried step subsequently succeeded.
    """
    attempt = RecoveryAttempt(
        recovery_id="rec-1",
        step_name="Build",
        failure_type="DEPENDENCY_ERROR",
        action="pip install requests",
        status=RecoveryOutcome.SUCCESS,
        exit_code=0,
        step_resolved=True,
    )
    assert attempt.status == RecoveryOutcome.SUCCESS
    assert attempt.step_resolved is True
