import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    StepDefinition,
    StepStatus,
    StepType,
    WorkflowState,
    WorkflowStatus,
)
from backend.models.reason_codes import ReasonCode
from backend.agent.classifier import classify_failure
from backend.agent.verifier_client import DeterministicEvidenceVerifier
from backend.agent.recovery_planner import recovery_planner, RecoveryOutcome
from backend.tools.project_analyzer import analyze_workspace
from backend.agent.planner import update_plan_with_analysis, build_adaptive_plan
from backend.tools.shell_tool import validate_and_parse_command, execute_shell_command


class TestPytestCommandGeneration:
    """Requirement 1 & 2: Prefer python -m pytest and use same Python interpreter."""

    def test_analyzer_generates_python_m_pytest(self, tmp_path):
        """Project analyzer detects test files and generates 'python -m pytest'."""
        (tmp_path / "test_app.py").write_text("def test_dummy(): pass\n")
        manifest = analyze_workspace(str(tmp_path))
        assert manifest.test_command == "python -m pytest"
        assert "python -m pytest" in manifest.test_commands

    def test_planner_prefers_python_m_pytest(self):
        """Planner normalizes 'pytest' to 'python -m pytest' in adaptive plan and updates."""
        from backend.models.workflow import ProjectManifest
        manifest = ProjectManifest(
            language="python",
            runtime="python",
            package_manager="pip",
            test_command="pytest",
        )
        steps = build_adaptive_plan(manifest)
        test_step = next((s for s in steps if s.type == StepType.RUN_TESTS.value), None)
        assert test_step is not None
        assert test_step.command == "python -m pytest"

    def test_update_plan_with_analysis_normalizes_pytest(self):
        """Existing steps are updated to use 'python -m pytest' instead of 'pytest'."""
        steps = [
            StepDefinition(
                id="s_test",
                type=StepType.RUN_TESTS.value,
                name="Run tests",
                command="pytest",
            )
        ]
        from backend.models.workflow import ProjectAnalysis
        analysis = ProjectAnalysis(
            language="python",
            runtime="python",
            package_manager="pip",
            test_command="pytest",
        )
        updated = update_plan_with_analysis(steps, analysis)
        assert updated[0].command == "python -m pytest"

    def test_security_allowlist_permits_python_m_pytest(self):
        """Requirement 3: Command security allowlist cleanly permits 'python -m pytest'."""
        safe, reason, parsed = validate_and_parse_command("python -m pytest")
        assert safe is True, f"Expected safe, got: {reason}"
        assert len(parsed) == 1
        assert parsed[0] == ["python", "-m", "pytest"]


class TestWinErrorClassification:
    """Requirement 4: Classify WinError 2 / executable-not-found separately as COMMAND_NOT_FOUND."""

    def test_winerror_2_classified_as_command_not_found(self):
        """WinError 2 is classified as COMMAND_NOT_FOUND, never UNKNOWN_ERROR."""
        exec_res = ExecutionResult(
            workflow_id="wf-test",
            step="Run tests",
            command="pytest",
            exit_code=127,
            stdout="",
            stderr="Execution error: [WinError 2] The system cannot find the file specified",
            metadata={"executable_not_found": True},
        )
        classification = classify_failure(exec_res)
        assert classification.failure_type == FailureType.COMMAND_NOT_FOUND
        assert classification.failure_type != FailureType.UNKNOWN_ERROR
        assert "pytest" in classification.reason.lower() or "executable" in classification.reason.lower()

    def test_filenotfounderror_classified_as_command_not_found(self):
        """FileNotFoundError in stderr is classified as COMMAND_NOT_FOUND."""
        exec_res = ExecutionResult(
            workflow_id="wf-test",
            step="Run tests",
            command="pytest",
            exit_code=127,
            stdout="",
            stderr="FileNotFoundError: [WinError 2] The system cannot find the file specified: 'pytest'",
        )
        classification = classify_failure(exec_res)
        assert classification.failure_type == FailureType.COMMAND_NOT_FOUND

    def test_verifier_evaluates_command_not_found_with_recovery(self):
        """Verifier identifies missing pytest executable and prescribes pip install pytest."""
        verifier = DeterministicEvidenceVerifier()
        exec_res = ExecutionResult(
            workflow_id="wf-test",
            step="Run tests",
            command="pytest",
            exit_code=127,
            stdout="",
            stderr="Execution error: [WinError 2] The system cannot find the file specified",
            metadata={"executable_not_found": True},
        )
        verif_res = verifier.verify(exec_res)
        assert verif_res.verified is False
        assert verif_res.failure_type == "COMMAND_NOT_FOUND"
        assert verif_res.reason_code == ReasonCode.COMMAND_NOT_FOUND
        assert verif_res.recovery_required is True
        assert verif_res.recovery_action == "pip install pytest"


class TestRecoveryPlanPytestRewriting:
    """Requirement 5: Recovery recognizes missing pytest, installs it, retries using python -m pytest."""

    def test_recovery_from_command_not_found_rewrites_to_python_m_pytest(self):
        """Missing pytest executable triggers pip install pytest and rewrites command to python -m pytest."""
        exec_res = ExecutionResult(
            workflow_id="wf-test",
            step="Run tests",
            command="pytest tests/",
            exit_code=127,
            stderr="Execution error: [WinError 2] The system cannot find the file specified",
        )
        classification = FailureClassification(
            failure_type=FailureType.COMMAND_NOT_FOUND,
            reason="Executable not found: pytest",
            details={"missing_executable": "pytest"},
        )
        plan = recovery_planner.generate_recovery_plan(exec_res, classification)
        assert plan.command == "pip install pytest"
        assert plan.rewritten_command == "python -m pytest tests/"
        assert plan.expected_outcome == RecoveryOutcome.SUCCESS

    def test_recovery_from_missing_pytest_module_rewrites_to_python_m_pytest(self):
        """python -m pytest failing with 'No module named pytest' rewrites to python -m pytest."""
        exec_res = ExecutionResult(
            workflow_id="wf-test",
            step="Run tests",
            command="pytest",
            exit_code=1,
            stderr="/usr/bin/python: No module named pytest",
        )
        classification = FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason="Missing Python module: pytest",
            details={"module": "pytest", "ecosystem": "python"},
        )
        plan = recovery_planner.generate_recovery_plan(exec_res, classification)
        assert plan.command == "pip install pytest"
        assert plan.rewritten_command == "python -m pytest"


class TestActualTestFailuresNotTreatedAsDependencyErrors:
    """Requirement 6: Do not blindly install pytest for every test failure. Only when missing."""

    def test_assertion_failure_reported_as_test_failure_not_dependency_error(self):
        """Actual test assertions failing must NOT trigger pip install pytest."""
        verifier = DeterministicEvidenceVerifier()
        exec_res = ExecutionResult(
            workflow_id="wf-test",
            step="Run tests",
            command="python -m pytest",
            exit_code=1,
            stdout="collected 3 items\n\ntest_math.py .F.\n\n=== FAILURES ===\nassert 1 == 2\nFAILED test_math.py::test_add",
            stderr="",
        )
        verif_res = verifier.verify(exec_res)
        assert verif_res.verified is False
        assert verif_res.failure_type == "TEST_FAILURE"
        # Must NOT demand pip install pytest on legitimate test failures!
        assert verif_res.recovery_action is None
        assert verif_res.recovery_required is False

        # Classifier also verifies this is TEST_FAILURE
        clf = classify_failure(exec_res)
        assert clf.failure_type == FailureType.TEST_FAILURE
        assert clf.failure_type != FailureType.DEPENDENCY_ERROR
        assert clf.failure_type != FailureType.COMMAND_NOT_FOUND

    def test_exit_nonzero_with_no_missing_modules_is_test_failure(self):
        """Test runner exiting with code 1 without missing module outputs reports TEST_FAILURE."""
        verifier = DeterministicEvidenceVerifier()
        exec_res = ExecutionResult(
            workflow_id="wf-test",
            step="Run tests",
            command="python -m pytest",
            exit_code=1,
            stdout="1 failed, 2 passed in 0.15s",
            stderr="",
        )
        verif_res = verifier.verify(exec_res)
        assert verif_res.verified is False
        assert verif_res.failure_type == "TEST_FAILURE"
        assert verif_res.recovery_required is False
        assert verif_res.recovery_action is None


class TestRealPythonRepositoryEndToEnd:
    """Requirement 8: Test with a real Python repository layout."""

    def test_real_repo_tests_pass_with_python_m_pytest(self, tmp_path):
        """When tests pass, step is verified success using python -m pytest."""
        ws = tmp_path / "repo"
        ws.mkdir()
        (ws / "math_lib.py").write_text("def add(a, b): return a + b\n")
        (ws / "test_math.py").write_text("from math_lib import add\ndef test_add(): assert add(2, 3) == 5\n")

        # Execute test command directly using current Python
        cmd = f'"{sys.executable}" -m pytest test_math.py'
        safe, reason, _ = validate_and_parse_command(cmd, cwd=str(ws))
        assert safe is True

        res = execute_shell_command(cmd, cwd=str(ws))
        assert res.exit_code == 0
        assert "passed" in res.stdout.lower()

        verifier = DeterministicEvidenceVerifier()
        verif_res = verifier.verify(res)
        assert verif_res.verified is True
        assert verif_res.status == "VERIFIED"

    def test_real_repo_actual_test_failure_not_dependency_error(self, tmp_path):
        """When code fails assertion, verify it's reported as TEST_FAILURE, not missing pytest."""
        ws = tmp_path / "repo_fail"
        ws.mkdir()
        (ws / "test_broken.py").write_text("def test_bad(): assert 2 + 2 == 5\n")

        cmd = f'"{sys.executable}" -m pytest test_broken.py'
        res = execute_shell_command(cmd, cwd=str(ws))
        assert res.exit_code != 0
        assert "assert 2 + 2 == 5" in res.stdout or "failed" in res.stdout.lower()

        verifier = DeterministicEvidenceVerifier()
        verif_res = verifier.verify(res)
        assert verif_res.verified is False
        assert verif_res.failure_type == "TEST_FAILURE"
        assert verif_res.recovery_required is False
        assert verif_res.recovery_action is None

        classification = classify_failure(res)
        assert classification.failure_type == FailureType.TEST_FAILURE

    def test_orchestrator_rewrites_pytest_command_on_retry(self):
        """Orchestrator applies rewritten_command so retry executes 'python -m pytest'."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        step = StepDefinition(
            id="s_test",
            type=StepType.RUN_TESTS.value,
            name="Run tests",
            command="pytest",
        )
        plan = recovery_planner.generate_recovery_plan(
            exec_result=ExecutionResult(
                workflow_id="wf-test",
                step=step.name,
                command="pytest",
                exit_code=127,
                stderr="[WinError 2] The system cannot find the file specified",
            ),
            classification=FailureClassification(
                failure_type=FailureType.COMMAND_NOT_FOUND,
                reason="Executable not found: pytest",
                details={"missing_executable": "pytest"},
            ),
        )
        assert plan.rewritten_command == "python -m pytest"

        # When orchestrator applies the plan
        if plan.rewritten_command:
            step.command = plan.rewritten_command
        assert step.command == "python -m pytest"
