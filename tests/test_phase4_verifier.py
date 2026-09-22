"""
Phase 4 — Verifier Rewrite (P1): Golden Corpus Tests (>= 60 real outputs)

Adversarial and golden corpus testing for DeterministicEvidenceVerifier:
- Dispatches by StepType enum (not substring matching)
- Required-evidence contracts per step type
- Tri-state verification results: VERIFIED, FAILED, UNVERIFIABLE
- 60 real-world outputs across:
  1. Test runners (Pytest, Jest, Mocha, Go, Cargo, Unittest)
  2. Dependency managers (pip, npm, yarn, pnpm)
  3. Build tools (tsc, webpack, next.js, py_compile, gcc, rustc)
  4. Health checks (HTTP status codes, connection errors)
  5. Application start & Git clone
  6. Shadowing and enum dispatch
"""
import pytest
from backend.agent.verifier_client import DeterministicEvidenceVerifier
from backend.models.workflow import ExecutionResult, StepType, VerificationResult


verifier = DeterministicEvidenceVerifier()


def make_exec_result(
    step: str = "test_step",
    step_type: str = StepType.RUN_TESTS.value,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    metadata: dict = None,
) -> ExecutionResult:
    return ExecutionResult(
        workflow_id="wf-golden",
        step=step,
        step_id="step-1",
        execution_id="exec-1234",
        command="test_command",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=50.0,
        workspace="/tmp/test",
        metadata=metadata or {},
        step_type=step_type,
    )


# =========================================================================
# 1. Test Runner Golden Corpus (16 tests)
# =========================================================================

class TestTestRunnerGoldenCorpus:

    def test_pytest_clean_pass(self):
        output = "=== 12 passed in 1.45s ==="
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output))
        assert res.verified is True
        assert res.status == "VERIFIED"

    def test_pytest_assertion_failure(self):
        output = "FAILED tests/test_calc.py::test_add - assert 4 == 5\n=== 1 failed, 11 passed in 1.20s ==="
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=1))
        assert res.verified is False

    def test_pytest_zero_tests_collected_exit_code_zero(self):
        """Zero tests collected must FAIL even if exit code is 0."""
        output = "============================= test session starts =============================\ncollected 0 items\n\n============================ no tests ran in 0.01s ============================"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=0))
        assert res.verified is False, "Zero tests collected must fail verification!"

    def test_pytest_non_assertion_keyerror(self):
        """Pytest failure from KeyError must fail verification."""
        output = "ERROR tests/test_api.py::test_login\nKeyError: 'auth_token'\n=== 1 error in 0.5s ==="
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=1))
        assert res.verified is False

    def test_pytest_runtime_error(self):
        output = "RuntimeError: CUDA out of memory\n=== 1 error, 2 passed in 2.1s ==="
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=1))
        assert res.verified is False

    def test_pytest_suppressed_failure_pipe_true(self):
        """When someone runs `pytest || true`, exit_code is 0 but output has failures."""
        output = "FAILED tests/test_auth.py::test_jwt - assert False\n=== 1 failed, 5 passed in 0.8s ==="
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=0))
        assert res.verified is False, "Must detect test failures even if exit_code == 0!"

    def test_jest_clean_pass(self):
        output = "PASS src/components/Button.test.tsx\nTests: 8 passed, 8 total\nTime: 2.345 s"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output))
        assert res.verified is True
        assert res.status == "VERIFIED"

    def test_jest_test_failure(self):
        output = "FAIL src/components/Header.test.tsx\n  ● Header › renders logo\n    expect(received).toBe(expected)\nTests: 1 failed, 7 passed, 8 total"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=1))
        assert res.verified is False

    def test_jest_zero_tests_found(self):
        output = "No tests found, exiting with code 0"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=0))
        assert res.verified is False

    def test_go_test_clean_pass(self):
        output = "ok  \tgithub.com/example/pkg\t0.124s"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output))
        assert res.verified is True

    def test_go_test_failure(self):
        output = "--- FAIL: TestParseConfig (0.00s)\n    config_test.go:24: expected port 8080, got 0\nFAIL\texit status 1\tFAIL\tgithub.com/example/pkg\t0.034s"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=1))
        assert res.verified is False

    def test_go_test_panic(self):
        output = "panic: runtime error: invalid memory address or nil pointer dereference [recovered]\nFAIL\tgithub.com/example/pkg\t0.002s"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=2))
        assert res.verified is False

    def test_cargo_test_clean_pass(self):
        output = "test result: ok. 15 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output))
        assert res.verified is True

    def test_cargo_test_failure(self):
        output = "failures:\n    tests::test_overflow\ntest result: FAILED. 14 passed; 1 failed; 0 ignored"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=101))
        assert res.verified is False

    def test_unittest_clean_pass(self):
        output = "Ran 24 tests in 0.045s\n\nOK"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output))
        assert res.verified is True

    def test_unittest_failures(self):
        output = "FAILED (failures=2, errors=1)\nRan 10 tests in 0.032s"
        res = verifier.verify(make_exec_result(step="run_tests", step_type=StepType.RUN_TESTS.value, stdout=output, exit_code=1))
        assert res.verified is False


# =========================================================================
# 2. Dependency Installation Golden Corpus (12 tests)
# =========================================================================

class TestDependencyInstallationGoldenCorpus:

    def test_pip_clean_install(self):
        stdout = "Successfully installed fastapi-0.110.0 uvicorn-0.28.0"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stdout=stdout))
        assert res.verified is True

    def test_pip_resolution_impossible(self):
        stderr = "ERROR: Cannot install -r requirements.txt because these package versions have conflicting dependencies.\nResolutionImpossible: for pydantic"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False
        assert res.failure_type == "DEPENDENCY_ERROR"

    def test_pip_wheel_build_failure(self):
        stderr = "error: Failed building wheel for cryptography\nERROR: Could not build wheels for cryptography"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_pip_no_matching_distribution(self):
        stderr = "ERROR: Could not find a version that satisfies the requirement non_existent_pkg_9999 (from versions: none)\nERROR: No matching distribution found for non_existent_pkg_9999"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_pip_externally_managed_environment(self):
        stderr = "error: externally-managed-environment\n× This environment is externally managed\n╰─> To install Python packages system-wide, try..."
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_pip_metadata_generation_failed(self):
        stderr = "error: metadata-generation-failed\n× Encountered error while generating package metadata."
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_npm_clean_install(self):
        stdout = "added 142 packages, and audited 143 packages in 3s\nfound 0 vulnerabilities"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stdout=stdout))
        assert res.verified is True

    def test_npm_eresolve_peer_conflict(self):
        stderr = "npm ERR! code ERESOLVE\nnpm ERR! ERESOLVE unable to resolve dependency tree\nnpm ERR! While resolving: react-dom@18.2.0"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_npm_e404_not_found(self):
        stderr = "npm ERR! code E404\nnpm ERR! 404 Not Found - GET https://registry.npmjs.org/@invalid/pkg - Not found"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_npm_econflict(self):
        stderr = "npm ERR! code ECONFLICT\nnpm ERR! Conflicting dependencies found in package-lock.json"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_npm_integrity_checksum_failure(self):
        stderr = "npm ERR! code EINTEGRITY\nnpm ERR! sha512-abc checksum failed, expected sha512-xyz"
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_yarn_clean_install(self):
        stdout = "success Saved lockfile.\nDone in 4.21s."
        res = verifier.verify(make_exec_result(step="install_deps", step_type=StepType.INSTALL_DEPENDENCIES.value, stdout=stdout))
        assert res.verified is True


# =========================================================================
# 3. Build & Compilation Golden Corpus (12 tests)
# =========================================================================

class TestBuildCompilationGoldenCorpus:

    def test_typescript_clean_build(self):
        stdout = "✨ Done in 1.84s."
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stdout=stdout))
        assert res.verified is True

    def test_typescript_type_error(self):
        stderr = "src/index.ts:14:5 - error TS2322: Type 'string' is not assignable to type 'number'.\nFound 1 error in src/index.ts:14"
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=2))
        assert res.verified is False

    def test_typescript_missing_name_error(self):
        stderr = "src/app.ts:5:1 - error TS2304: Cannot find name 'myUndefinedVariable'."
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=2))
        assert res.verified is False

    def test_nextjs_build_error(self):
        stderr = "Error: Build error occurred\nType error: Page \"pages/api/auth.ts\" has an invalid default export."
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_webpack_compilation_failed(self):
        stdout = "Failed to compile.\nModule not found: Can't resolve './Header' in '/app/src'"
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stdout=stdout, exit_code=1))
        assert res.verified is False

    def test_python_syntax_error(self):
        stderr = "  File \"main.py\", line 12\n    def broken_func(\n                   ^\nSyntaxError: unexpected EOF while parsing"
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_python_indentation_error(self):
        stderr = "  File \"server.py\", line 4\n    print('hello')\nIndentationError: unexpected indent"
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_python_clean_compilation(self):
        stdout = "Listing '.'...\nCompiling './main.py'...\nCompiling './utils.py'..."
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stdout=stdout))
        assert res.verified is True

    def test_gcc_syntax_error(self):
        stderr = "main.c:12:5: error: expected ';' before 'return'"
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=1))
        assert res.verified is False

    def test_rustc_compilation_error(self):
        stderr = "error[E0425]: cannot find value `x` in this scope\n  --> src/main.rs:2:5"
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=101))
        assert res.verified is False

    def test_go_build_clean(self):
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stdout="", stderr="", exit_code=0))
        assert res.verified is True

    def test_go_build_undefined_symbol(self):
        stderr = "./main.go:8:2: undefined: fmt.Printlnn"
        res = verifier.verify(make_exec_result(step="build_project", step_type=StepType.BUILD_PROJECT.value, stderr=stderr, exit_code=1))
        assert res.verified is False


# =========================================================================
# 4. Health Check Golden Corpus (10 tests)
# =========================================================================

class TestHealthCheckGoldenCorpus:

    def test_health_check_200_ok(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stdout="HTTP 200 OK - {\"status\": \"ok\"}",
            metadata={"status_code": 200},
        ))
        assert res.verified is True
        assert res.status == "VERIFIED"

    def test_health_check_204_no_content(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            metadata={"status_code": 204},
        ))
        assert res.verified is True

    def test_health_check_500_internal_error(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stderr="HTTP 500 Internal Server Error",
            metadata={"status_code": 500},
            exit_code=1,
        ))
        assert res.verified is False

    def test_health_check_502_bad_gateway(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stderr="HTTP 502 Bad Gateway",
            metadata={"status_code": 502},
            exit_code=1,
        ))
        assert res.verified is False

    def test_health_check_503_service_unavailable(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stderr="HTTP 503 Service Unavailable",
            metadata={"status_code": 503},
            exit_code=1,
        ))
        assert res.verified is False

    def test_health_check_404_not_found(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stderr="HTTP 404 Not Found",
            metadata={"status_code": 404},
            exit_code=1,
        ))
        assert res.verified is False

    def test_health_check_401_unauthorized(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            metadata={"status_code": 401},
            exit_code=1,
        ))
        assert res.verified is False

    def test_health_check_connection_refused(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stderr="httpx.ConnectError: [Errno 111] Connection refused",
            exit_code=1,
        ))
        assert res.verified is False

    def test_health_check_dns_resolution_failure(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stderr="httpx.ConnectError: [Errno -2] Name or service not known",
            exit_code=1,
        ))
        assert res.verified is False

    def test_health_check_timeout(self):
        res = verifier.verify(make_exec_result(
            step="health_check",
            step_type=StepType.HEALTH_CHECK.value,
            stderr="httpx.TimeoutException: The read operation timed out",
            exit_code=124,
        ))
        assert res.verified is False


# =========================================================================
# 5. Application Start & Git Clone Golden Corpus (10 tests)
# =========================================================================

class TestStartAndCloneGoldenCorpus:

    def test_app_start_clean_running_pid(self):
        import os
        res = verifier.verify(make_exec_result(
            step="start_application",
            step_type=StepType.START_APPLICATION.value,
            stdout=f"Application started in background (PID {os.getpid()})",
            metadata={"pid": os.getpid()},
        ))
        assert res.verified is True

    def test_app_start_port_already_in_use(self):
        res = verifier.verify(make_exec_result(
            step="start_application",
            step_type=StepType.START_APPLICATION.value,
            stderr="ERROR: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 8000): address already in use",
            exit_code=1,
        ))
        assert res.verified is False
        assert res.failure_type == "PORT_ERROR"

    def test_app_start_eaddrinuse_node(self):
        res = verifier.verify(make_exec_result(
            step="start_application",
            step_type=StepType.START_APPLICATION.value,
            stderr="Error: listen EADDRINUSE: address already in use :::3000",
            exit_code=1,
        ))
        assert res.verified is False
        assert res.failure_type == "PORT_ERROR"

    def test_app_start_segfault(self):
        res = verifier.verify(make_exec_result(
            step="start_application",
            step_type=StepType.START_APPLICATION.value,
            stderr="Segmentation fault (core dumped)",
            exit_code=139,
        ))
        assert res.verified is False

    def test_clone_clean_files_present(self):
        res = verifier.verify(make_exec_result(
            step="clone_repository",
            step_type=StepType.CLONE_REPOSITORY.value,
            stdout="Cloning into 'repo'...",
            metadata={"cloned_files_count": 28},
        ))
        assert res.verified is True

    def test_clone_empty_directory_fails(self):
        res = verifier.verify(make_exec_result(
            step="clone_repository",
            step_type=StepType.CLONE_REPOSITORY.value,
            stderr="Git clone returned 0 but target directory is empty.",
            metadata={"cloned_files_count": 0},
            exit_code=1,
        ))
        assert res.verified is False

    def test_clone_fatal_repo_not_found(self):
        res = verifier.verify(make_exec_result(
            step="clone_repository",
            step_type=StepType.CLONE_REPOSITORY.value,
            stderr="fatal: repository 'https://github.com/nonexistent/fake-repo' not found",
            exit_code=128,
        ))
        assert res.verified is False

    def test_clone_fatal_authentication_failed(self):
        res = verifier.verify(make_exec_result(
            step="clone_repository",
            step_type=StepType.CLONE_REPOSITORY.value,
            stderr="fatal: Authentication failed for 'https://github.com/secret/repo'",
            exit_code=128,
        ))
        assert res.verified is False

    def test_clone_destination_already_exists(self):
        res = verifier.verify(make_exec_result(
            step="clone_repository",
            step_type=StepType.CLONE_REPOSITORY.value,
            stderr="fatal: destination path 'repo' already exists and is not an empty directory.",
            exit_code=128,
        ))
        assert res.verified is False

    def test_clone_remote_rejected(self):
        res = verifier.verify(make_exec_result(
            step="clone_repository",
            step_type=StepType.CLONE_REPOSITORY.value,
            stderr="fatal: remote error: upload-pack not allowed",
            exit_code=128,
        ))
        assert res.verified is False


# =========================================================================
# 6. StepType Enum Dispatch, Tri-State, & Shadowing (6 tests)
# =========================================================================

class TestStepTypeEnumDispatchAndTriState:

    def test_step_dispatch_by_step_type_not_name_shadowing(self):
        """
        Step named 'install_test_deps_and_test' has StepType.RUN_TESTS.
        In the old code, 'install' in step_name matched dependency verification first!
        Now with StepType enum dispatch, it must be evaluated as a test step.
        """
        output = "FAILED tests/test_core.py::test_fn\n=== 1 failed in 0.2s ==="
        res = verifier.verify(make_exec_result(
            step="install_test_deps_and_test",
            step_type=StepType.RUN_TESTS.value,
            stdout=output,
            exit_code=1,
        ))
        # Must be diagnosed as TEST_FAILURE, NOT DEPENDENCY_ERROR
        assert res.failure_type == "TEST_FAILURE", (
            f"Step dispatch shadowed! Got {res.failure_type}, expected TEST_FAILURE"
        )

    def test_unknown_custom_step_returns_unverifiable(self):
        """A step with no applicable verification rules returns UNVERIFIABLE tri-state."""
        res = verifier.verify(make_exec_result(
            step="deploy_to_kubernetes_cluster",
            step_type="custom_deploy_step",
            stdout="Cluster deployment submitted",
            exit_code=0,
        ))
        assert res.status == "UNVERIFIABLE", (
            f"Expected UNVERIFIABLE tri-state for unverified custom step, got {res.status}"
        )
        assert res.verified is False

    def test_tri_state_verified_field_matches_boolean(self):
        """When status == 'VERIFIED', verified is True."""
        res = verifier.verify(make_exec_result(
            step="test_step",
            step_type=StepType.RUN_TESTS.value,
            stdout="=== 5 passed in 0.1s ===",
        ))
        assert res.status == "VERIFIED"
        assert res.verified is True

    def test_tri_state_failed_field_matches_boolean(self):
        """When status == 'FAILED', verified is False."""
        res = verifier.verify(make_exec_result(
            step="test_step",
            step_type=StepType.RUN_TESTS.value,
            stdout="=== 1 failed in 0.1s ===",
            exit_code=1,
        ))
        assert res.status == "FAILED"
        assert res.verified is False

    def test_analyze_project_step_passes(self):
        res = verifier.verify(make_exec_result(
            step="analyze_project",
            step_type=StepType.ANALYZE_PROJECT.value,
            stdout="Project detected: Python",
        ))
        assert res.verified is True
        assert res.status == "VERIFIED"

    def test_final_report_step_passes(self):
        res = verifier.verify(make_exec_result(
            step="generate_final_report",
            step_type=StepType.FINAL_REPORT.value,
            stdout="Report compiled successfully",
        ))
        assert res.verified is True
        assert res.status == "VERIFIED"
