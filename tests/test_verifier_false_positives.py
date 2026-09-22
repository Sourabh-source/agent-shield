"""
tests/test_verifier_false_positives.py
Adversarial test suite covering all 11 verifier false-positive scenarios.
Demonstrates vulnerabilities where exit_code=0 or malformed metadata causes unearned passes.
"""
import os
import pytest
from backend.agent.verifier_client import DeterministicEvidenceVerifier
from backend.models.workflow import ExecutionResult, StepType
from backend.models.reason_codes import ReasonCode

verifier = DeterministicEvidenceVerifier()


def make_res(step_type: str, exit_code: int = 0, stdout: str = "", stderr: str = "", command: str = "test", metadata: dict = None) -> ExecutionResult:
    return ExecutionResult(
        workflow_id="wf-fp-test",
        step="test_step",
        step_id="s1",
        execution_id="exec-fp-1",
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=10.0,
        metadata=metadata or {},
        step_type=step_type,
    )


class TestVerifierFalsePositives:
    """11 adversarial test cases targeting verifier false-positive vulnerabilities."""

    # 1. Test runner exit 0 with FAILED line
    def test_fp1_test_runner_exit_0_with_failed_line(self):
        stdout = "FAILED tests/test_auth.py::test_token_leak\n1 failed, 10 passed\n"
        res = verifier.verify(make_res(step_type=StepType.RUN_TESTS.value, exit_code=0, stdout=stdout))
        assert res.verified is False, "Test step with FAILED line must not be verified even if exit_code=0"
        assert res.status == "FAILED"

    # 2. Test runner exit 0 with '\d+ failing'
    def test_fp2_test_runner_exit_0_with_failing_count(self):
        stdout = "  3 passing (42ms)\n  2 failing\n"
        res = verifier.verify(make_res(step_type=StepType.RUN_TESTS.value, exit_code=0, stdout=stdout))
        assert res.verified is False, "Test step with '2 failing' must not be verified even if exit_code=0"
        assert res.status == "FAILED"

    # 3. Test runner exit 0 with TAP 'not ok'
    def test_fp3_test_runner_exit_0_with_tap_not_ok(self):
        stdout = "ok 1 - setup\nnot ok 2 - test_database_connection\n# fail 1\n"
        res = verifier.verify(make_res(step_type=StepType.RUN_TESTS.value, exit_code=0, stdout=stdout))
        assert res.verified is False, "Test step with TAP 'not ok' must not be verified even if exit_code=0"
        assert res.status == "FAILED"

    # 4. Test runner exit 0 with 0 collected tests
    def test_fp4_test_runner_exit_0_zero_collected_tests(self):
        stdout = "collected 0 items\n"
        res = verifier.verify(make_res(step_type=StepType.RUN_TESTS.value, exit_code=0, stdout=stdout))
        assert res.verified is False, "0 collected tests must not be verified"
        assert res.status == "FAILED"

    # 5. Test runner exit 0 with empty output
    def test_fp5_test_runner_empty_output_fails(self):
        res = verifier.verify(make_res(step_type=StepType.RUN_TESTS.value, exit_code=0, stdout="", stderr=""))
        assert res.verified is False, "Empty test output must not be verified"
        assert res.status == "FAILED"

    # 6. Build step with bare echo command
    def test_fp6_build_step_bare_echo_not_applicable(self):
        res = verifier.verify(make_res(step_type=StepType.BUILD_PROJECT.value, exit_code=0, command="echo 'Build success'", stdout="Build success"))
        assert res.verified is False, "Bare echo command must never be VERIFIED"
        assert res.status == "NOT_APPLICABLE"

    # 7. Unhandled Traceback in output with exit 0
    def test_fp7_traceback_detected_across_steps(self):
        stdout = "Processing...\nTraceback (most recent call last):\n  File 'app.py', line 10, in <module>\nRuntimeError: DB connection crashed\n"
        res = verifier.verify(make_res(step_type=StepType.ANALYZE_PROJECT.value, exit_code=0, stdout=stdout))
        assert res.verified is False, "Unhandled traceback must fail verification even if exit_code=0"
        assert res.status == "FAILED"

    # 8. Start step with missing PID in metadata
    def test_fp8_start_step_missing_pid_fails(self):
        res = verifier.verify(make_res(step_type=StepType.START_APPLICATION.value, exit_code=0, stdout="Started", metadata={}))
        assert res.verified is False, "Start step without PID in metadata must fail verification"
        assert res.status == "FAILED"

    # 9. Start step with dead PID
    def test_fp9_start_step_dead_pid_fails(self):
        res = verifier.verify(make_res(step_type=StepType.START_APPLICATION.value, exit_code=0, stdout="Started", metadata={"pid": 99999999}))
        assert res.verified is False, "Start step with dead PID must fail verification"
        assert res.status == "FAILED"

    # 10. Health check step with missing status_code
    def test_fp10_health_check_missing_status_code_fails(self):
        res = verifier.verify(make_res(step_type=StepType.HEALTH_CHECK.value, exit_code=0, stdout="OK", metadata={}))
        assert res.verified is False, "Health check without status_code in metadata must fail verification"
        assert res.status == "FAILED"

    # 11. No fake echo recovery actions returned
    def test_fp11_no_fake_echo_recovery_actions(self):
        # Trigger port error failure
        res_port = verifier.verify(make_res(step_type=StepType.START_APPLICATION.value, exit_code=1, stderr="Address already in use"))
        assert res_port.recovery_action is None or not res_port.recovery_action.startswith("echo "), (
            f"Expected no fake echo recovery action, got: {res_port.recovery_action}"
        )

        # Trigger timeout failure
        res_timeout = verifier.verify(make_res(step_type=StepType.BUILD_PROJECT.value, exit_code=124, stderr="timed out"))
        assert res_timeout.recovery_action is None or not res_timeout.recovery_action.startswith("echo "), (
            f"Expected no fake echo recovery action, got: {res_timeout.recovery_action}"
        )

        # Trigger health check failure
        res_health = verifier.verify(make_res(step_type=StepType.HEALTH_CHECK.value, exit_code=1, stderr="Connection refused"))
        assert res_health.recovery_action is None or not res_health.recovery_action.startswith("echo "), (
            f"Expected no fake echo recovery action, got: {res_health.recovery_action}"
        )
