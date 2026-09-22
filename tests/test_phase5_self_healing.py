"""
Phase 5 — Real Self-Healing, Not Mocked Retries (P1)
Adversarial test suite demonstrating:
1. is_action_already_satisfied false-positive via workspace folder poisoning (rglob bug)
2. RecoveryPlan missing postcondition definition
3. Recovery action exit_code 0 but postcondition fails -> must NOT retry failed step
4. Real port postcondition verification
5. Production orchestrator free from hardcoded monkey-patching
"""
import os
import socket
import sys
import tempfile
from pathlib import Path
import pytest

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.recovery_planner import RecoveryPlanner, recovery_planner
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    RecoveryPlan,
    StepDefinition,
    StepStatus,
    WorkflowStatus,
)


class TestPhase5SelfHealing:

    def test_directory_poisoning_does_not_satisfy_missing_package(self, tmp_path):
        """
        ADVERSARIAL: An attacker or repo has a folder named 'super_secret_pkg'
        in the workspace. The module is NOT installed in Python.
        The old code did `ws.rglob('super_secret_pkg')` and returned True!
        The hardened code must verify with real package manager, returning False.
        """
        fake_repo = tmp_path / "repo"
        fake_repo.mkdir()
        # Create a directory with the package name inside the repo
        poison_dir = fake_repo / "super_secret_pkg"
        poison_dir.mkdir()

        plan = RecoveryPlan(
            reason="Missing module super_secret_pkg",
            failure_type="DEPENDENCY_ERROR",
            action_type="install_dependency",
            tool="pip",
            command="pip install super_secret_pkg",
            target_step="build",
        )

        # On vulnerable code, is_action_already_satisfied returns True because of rglob!
        is_satisfied = recovery_planner.is_action_already_satisfied(plan, str(fake_repo))
        assert is_satisfied is False, (
            "SECURITY BUG: Workspace directory poisoning caused is_action_already_satisfied "
            "to return True for an uninstalled package!"
        )

    def test_recovery_plan_requires_and_generates_postcondition(self):
        """
        Every generated RecoveryPlan must define a machine-checkable postcondition.
        """
        exec_res = ExecutionResult(
            workflow_id="wf-1",
            step="build",
            command="python build.py",
            exit_code=1,
            stderr="ModuleNotFoundError: No module named 'fastapi'",
        )
        classification = FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason="Missing dependency 'fastapi'",
            details={"module": "fastapi", "ecosystem": "python"},
        )

        plan = recovery_planner.generate_recovery_plan(exec_res, classification)
        assert hasattr(plan, "postcondition_cmd") or hasattr(plan, "postcondition_type"), (
            "RecoveryPlan must have machine-checkable postcondition fields!"
        )
        assert plan.postcondition_type is not None, "Generated plan must define postcondition_type"

    def test_recovery_postcondition_evaluates_real_installed_package(self):
        """
        Postcondition evaluation checks real Python environment (e.g. pytest is installed, fake_xyz is not).
        """
        plan_installed = RecoveryPlan(
            reason="Missing pytest",
            failure_type="DEPENDENCY_ERROR",
            action_type="install_dependency",
            tool="pip",
            command="pip install pytest",
            target_step="test",
            postcondition_type="package_installed",
            postcondition_target="pytest",
        )
        plan_missing = RecoveryPlan(
            reason="Missing non_existent_pkg_12345",
            failure_type="DEPENDENCY_ERROR",
            action_type="install_dependency",
            tool="pip",
            command="pip install non_existent_pkg_12345",
            target_step="test",
            postcondition_type="package_installed",
            postcondition_target="non_existent_pkg_12345",
        )

        assert recovery_planner.evaluate_postcondition(plan_installed) is True
        assert recovery_planner.evaluate_postcondition(plan_missing) is False

    def test_port_postcondition_checks_real_socket_bind(self):
        """
        Port release postcondition actually checks whether socket bind succeeds.
        """
        # Bind a port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        bound_port = s.getsockname()[1]
        s.listen(1)

        try:
            plan = RecoveryPlan(
                reason=f"Port {bound_port} in use",
                failure_type="PORT_ERROR",
                action_type="release_port",
                tool="shell",
                command="echo release",
                target_step="serve",
                postcondition_type="port_free",
                postcondition_target=str(bound_port),
            )
            # Port is currently held by socket `s`
            assert recovery_planner.evaluate_postcondition(plan) is False, "Port should be detected as in use"
        finally:
            s.close()

        # Port is now freed
        assert recovery_planner.evaluate_postcondition(plan) is True, "Port should now be detected as free"

    def test_recovery_action_exit_zero_but_failed_postcondition_aborts_retry(self):
        """
        If a recovery action runs with exit_code=0 (e.g. `echo done`), but its
        postcondition fails (package not installed), the orchestrator must NOT
        burn retries retrying the original step! It must halt with VERIFIED_FAILURE.
        """
        orchestrator = WorkflowOrchestrator(max_retries=2)
        wf = orchestrator.create_workflow(
            repo_url="https://github.com/example/repo",
            task="Test postcondition enforcement",
        )
        # Step that fails with missing module
        wf.steps = [
            StepDefinition(
                id="s1",
                type="shell_command",
                name="build",
                tool="python",
                command=f'"{sys.executable}" -c "import non_existent_pkg_xyz_999"',
            )
        ]
        workflow_store.save(wf)

        finished = orchestrator.run_workflow(wf.workflow_id)

        # The recovery action (pip install non_existent_pkg_xyz_999) fails or postcondition fails.
        # It must NOT retry 2 times fruitlessly when postcondition is not met.
        assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
        # Total retries on step s1 should not be repeatedly burned
        assert wf.steps[0].retries <= 1

    def test_orchestrator_has_no_hardcoded_demo_monkeypatching(self):
        """
        Verify that orchestrator.py source code contains no hardcoded demo failure mode
        command monkey patches (e.g. pandas / syntaxerror injections).
        """
        from backend.agent import orchestrator as orch_module
        orch_source = Path(orch_module.__file__).read_text(encoding="utf-8")
        assert "No module named \\\"pandas\\\"" not in orch_source, (
            "Production orchestrator.py must not contain hardcoded demo monkey patches!"
        )
        assert "SyntaxError: invalid syntax in main.py" not in orch_source, (
            "Production orchestrator.py must not contain hardcoded demo monkey patches!"
        )
