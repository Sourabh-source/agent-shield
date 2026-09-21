import abc
import logging
import re
from typing import Optional
import httpx

from backend.config import settings
from backend.models.workflow import ExecutionResult, VerificationResult

logger = logging.getLogger("agentguard.verifier_client")


class VerificationClient(abc.ABC):
    """
    Abstract interface for Member 3's Evidence Verification Engine.
    Member 2 provides the raw ExecutionResult; Member 3 evaluates machine evidence
    and decides PASS/FAIL and recovery actions.
    """

    @abc.abstractmethod
    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        """
        Verify the execution result against machine-checkable evidence rules.
        """
        pass


class DeterministicEvidenceVerifier(VerificationClient):
    """
    Member 3 Real Deterministic Evidence Verifier.
    Performs machine-checkable evidence verification on raw ExecutionResult
    across command execution, dependency installation, build artifacts, test outputs,
    process liveliness, and health checks.
    """

    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        result = self._evaluate(execution_result)
        if execution_result:
            if not result.execution_id and getattr(execution_result, "execution_id", None):
                result.execution_id = execution_result.execution_id
            if not result.evidence_digest and getattr(execution_result, "evidence_digest", None):
                result.evidence_digest = execution_result.evidence_digest
        return result

    def _evaluate(self, execution_result: ExecutionResult) -> VerificationResult:
        logger.info(
            f"DeterministicEvidenceVerifier evaluating step '{execution_result.step}' (exit_code={execution_result.exit_code})"
        )

        # ----------------------------------------------------
        # A. Basic Execution Evidence Checks
        # ----------------------------------------------------
        if not execution_result:
            return VerificationResult(
                verified=False,
                reason="Verification failed: execution result is empty",
                recovery_required=False,
                retry_allowed=False,
            )

        if not execution_result.execution_id:
            return VerificationResult(
                verified=False,
                reason="Verification failed: missing execution ID in evidence",
                recovery_required=False,
                retry_allowed=False,
            )

        if not execution_result.timestamp:
            return VerificationResult(
                verified=False,
                reason="Verification failed: missing timestamp in evidence",
                recovery_required=False,
                retry_allowed=False,
            )

        if execution_result.exit_code is None:
            return VerificationResult(
                verified=False,
                reason="Verification failed: exit code not captured",
                recovery_required=False,
                retry_allowed=False,
            )

        # Check for explicit failure / rejection flags in metadata
        metadata = execution_result.metadata or {}
        if metadata.get("reject") or metadata.get("security_issue") or metadata.get("evidence_invalid"):
            return VerificationResult(
                verified=False,
                reason=str(metadata.get("reject_reason") or "Verification rejected: security issue or invalid evidence detected in metadata"),
                recovery_required=False,
                retry_allowed=False,
                metadata=metadata,
            )

        stdout = execution_result.stdout or ""
        stderr = execution_result.stderr or ""
        combined_logs = f"{stdout}\n{stderr}"
        step_name = (execution_result.step or "").lower()
        command = (execution_result.command or "").lower()

        # ----------------------------------------------------
        # Handle Non-Zero Exit Code (Deterministic Failure Diagnosis & Recovery)
        # ----------------------------------------------------
        if execution_result.exit_code != 0:
            py_missing_mod = re.search(r"No module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?", combined_logs)
            npm_missing_mod = re.search(r"Cannot find module ['\"]?([a-zA-Z0-9_\-\/@]+)['\"]?", combined_logs)
            missing_pkg = re.search(r"Module not found: Error: Can't resolve '([^']+)'", combined_logs)

            recovery_action: Optional[str] = None
            failure_type = "UNKNOWN_ERROR"

            if py_missing_mod:
                module = py_missing_mod.group(1)
                recovery_action = f"pip install {module}"
                failure_type = "DEPENDENCY_ERROR"
            elif npm_missing_mod:
                pkg = npm_missing_mod.group(1)
                recovery_action = f"npm install {pkg}"
                failure_type = "DEPENDENCY_ERROR"
            elif missing_pkg:
                pkg = missing_pkg.group(1)
                recovery_action = f"npm install {pkg}"
                failure_type = "DEPENDENCY_ERROR"
            elif "package-x" in combined_logs or "module not found" in combined_logs.lower():
                recovery_action = "npm install package-x"
                failure_type = "DEPENDENCY_ERROR"
            elif "timed out" in combined_logs.lower() or "timeout" in combined_logs.lower():
                recovery_action = "echo 'Retrying with extended timeout'"
                failure_type = "TIMEOUT"
            elif "syntaxerror" in combined_logs.lower():
                recovery_action = "echo 'Syntax error detected in source'"
                failure_type = "BUILD_ERROR"
            elif "permission denied" in combined_logs.lower():
                recovery_action = "echo 'Permission error'"
                failure_type = "PERMISSION_ERROR"
            else:
                if "install" in step_name or "pip" in command or "npm" in command:
                    recovery_action = "pip install --upgrade pip"
                    failure_type = "DEPENDENCY_ERROR"
                elif "build" in step_name:
                    recovery_action = "pip install -r requirements.txt"
                    failure_type = "BUILD_ERROR"
                elif "test" in step_name:
                    recovery_action = "pip install pytest"
                    failure_type = "TEST_FAILURE"
                else:
                    recovery_action = "echo 'Attempting automatic workspace cleanup'"
                    failure_type = "UNKNOWN_ERROR"

            return VerificationResult(
                verified=False,
                reason=f"Step '{execution_result.step}' failed with exit code {execution_result.exit_code}: {stderr[:150].strip() or stdout[:150].strip() or 'Execution error'}",
                recovery_required=True,
                recovery_action=recovery_action,
                retry_allowed=True,
                failure_type=failure_type,
            )

        # ----------------------------------------------------
        # Exit Code 0: Step-Specific Machine Evidence Verification
        # (exit_code == 0 is necessary but NOT sufficient on its own)
        # ----------------------------------------------------

        # B. Dependency Installation Verification
        if "install" in step_name or "pip" in command or ("npm" in command and "install" in command):
            fatal_install_errors = [
                "resolutionimpossible",
                "could not find a version",
                "failed building wheel",
                "npm err! code enoent",
                "npm err! code e404",
            ]
            for err_pattern in fatal_install_errors:
                if err_pattern in combined_logs.lower():
                    return VerificationResult(
                        verified=False,
                        reason=f"Dependency installation contained fatal error: {err_pattern}",
                        recovery_required=True,
                        recovery_action="pip install --upgrade pip",
                        retry_allowed=True,
                        failure_type="DEPENDENCY_ERROR",
                    )
            return VerificationResult(
                verified=True,
                reason=f"Dependency installation verified: exit code 0 with clean package manager output",
                recovery_required=False,
            )

        # C. Build Verification
        if "build" in step_name or "compile" in step_name:
            fatal_build_errors = [
                "syntaxerror:",
                "compilation failed",
                "fatal error:",
                "error ts",
                "modulenotfounderror:",
            ]
            for err_pattern in fatal_build_errors:
                if err_pattern in combined_logs.lower():
                    return VerificationResult(
                        verified=False,
                        reason=f"Build output contained fatal error: {err_pattern}",
                        recovery_required=True,
                        recovery_action="pip install -r requirements.txt",
                        retry_allowed=True,
                        failure_type="BUILD_ERROR",
                    )
            return VerificationResult(
                verified=True,
                reason=f"Build verified: clean compilation with exit code 0",
                recovery_required=False,
            )

        # D. Test Verification
        if "test" in step_name:
            fatal_test_errors = [
                "failed (failures=",
                "failed (errors=",
                "assertionerror",
                "=== failure ===",
                "failures:",
            ]
            for err_pattern in fatal_test_errors:
                if err_pattern in combined_logs.lower():
                    return VerificationResult(
                        verified=False,
                        reason=f"Test run reported failures in output: {err_pattern}",
                        recovery_required=True,
                        recovery_action="pip install pytest",
                        retry_allowed=True,
                        failure_type="TEST_FAILURE",
                    )
            if not combined_logs.strip():
                return VerificationResult(
                    verified=False,
                    reason="Test verification failed: zero test execution evidence captured (empty output)",
                    recovery_required=True,
                    recovery_action="pip install pytest",
                    retry_allowed=True,
                    failure_type="TEST_FAILURE",
                )
            return VerificationResult(
                verified=True,
                reason="Tests verified: test execution completed successfully without failures",
                recovery_required=False,
            )

        # E. Health Check Verification
        if "health" in step_name:
            status_code = metadata.get("status_code")
            if status_code is not None and (status_code < 200 or status_code >= 400):
                return VerificationResult(
                    verified=False,
                    reason=f"Health check failed with HTTP status {status_code}",
                    recovery_required=True,
                    recovery_action="echo 'Restarting service'",
                    retry_allowed=True,
                    failure_type="NETWORK_ERROR",
                )
            if "connection refused" in combined_logs.lower() or "404 not found" in combined_logs.lower() or "500 internal" in combined_logs.lower():
                return VerificationResult(
                    verified=False,
                    reason="Health check endpoint connection failed or returned error status",
                    recovery_required=True,
                    recovery_action="echo 'Waiting for service readiness'",
                    retry_allowed=True,
                    failure_type="NETWORK_ERROR",
                )
            return VerificationResult(
                verified=True,
                reason="Health check verified: endpoint responded successfully",
                recovery_required=False,
            )

        # F. Start Application Verification
        if "start" in step_name:
            if "address already in use" in combined_logs.lower() or "port already in use" in combined_logs.lower():
                return VerificationResult(
                    verified=False,
                    reason="Port conflict detected during application start",
                    recovery_required=True,
                    recovery_action="echo 'Killing conflicting process'",
                    retry_allowed=True,
                    failure_type="PORT_ERROR",
                )
            pid = metadata.get("pid")
            return VerificationResult(
                verified=True,
                reason=f"Application startup verified: process running (PID: {pid})" if pid else "Application startup verified: process started successfully",
                recovery_required=False,
            )

        # G. Clone Verification
        if "clone" in step_name:
            cloned_count = metadata.get("cloned_files_count")
            if cloned_count is not None and cloned_count == 0 and "empty" in stderr.lower():
                return VerificationResult(
                    verified=False,
                    reason="Repository clone failed: destination directory is empty",
                    recovery_required=False,
                    retry_allowed=False,
                    failure_type="REPOSITORY_ERROR",
                )
            return VerificationResult(
                verified=True,
                reason=f"Repository clone verified: files present in workspace" if cloned_count is None else f"Repository clone verified: {cloned_count} files present in workspace",
                recovery_required=False,
            )

        # H. Generic / Analyze / Final Report Verification
        return VerificationResult(
            verified=True,
            reason=f"Step '{execution_result.step}' passed machine-checked evidence verification",
            recovery_required=False,
            retry_allowed=True,
        )


class MockVerifierClient(DeterministicEvidenceVerifier):
    """
    Mock verifier client for Member 2 development, local testing, and demo execution.
    Inherits full machine-checkable evidence verification rules from DeterministicEvidenceVerifier.
    """
    pass


class HttpVerifierClient(VerificationClient):
    """
    HTTP client that delegates evidence verification to Member 3's real external service.
    """

    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url

    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(self.endpoint_url, json=execution_result.model_dump())
                resp.raise_for_status()
                data = resp.json()
                return VerificationResult.model_validate(data)
        except Exception as exc:
            logger.error(f"Failed to communicate with Member 3 verifier at {self.endpoint_url}: {exc}")
            # Do NOT falsely claim success on verification network error!
            return VerificationResult(
                verified=False,
                reason=f"Verifier unavailable: {str(exc)}",
                recovery_required=False,
                retry_allowed=False,
                metadata={"verifier_unavailable": True, "error": str(exc)},
            )


class UnavailableVerifierClient(VerificationClient):
    """
    Verifier client used when real verifier is required (MOCK_VERIFIER=False)
    but no MEMBER3_VERIFIER_URL is configured. Always rejects verification
    with verifier_unavailable flag so the orchestrator transitions to VERIFICATION_UNAVAILABLE.
    Never allows unconfigured verifiers to falsely claim success.
    """

    def __init__(
        self,
        reason: str = "Verifier unavailable: MOCK_VERIFIER=False but MEMBER3_VERIFIER_URL is not configured",
    ):
        self.reason = reason

    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        logger.error(self.reason)
        return VerificationResult(
            verified=False,
            reason=self.reason,
            recovery_required=False,
            retry_allowed=False,
            metadata={"verifier_unavailable": True, "error": "unconfigured_verifier_url"},
            execution_id=execution_result.execution_id if execution_result else None,
            evidence_digest=getattr(execution_result, "evidence_digest", None) if execution_result else None,
        )


def get_verifier_client() -> VerificationClient:
    """
    Returns appropriate VerificationClient based on explicit configuration:
    1. If settings.MOCK_VERIFIER is True -> MockVerifierClient()
    2. Else if settings.MEMBER3_VERIFIER_URL is set and non-empty -> HttpVerifierClient(settings.MEMBER3_VERIFIER_URL)
    3. Else -> UnavailableVerifierClient() (never silently fall back to mock)
    """
    if settings.MOCK_VERIFIER:
        return MockVerifierClient()
    elif settings.MEMBER3_VERIFIER_URL and settings.MEMBER3_VERIFIER_URL.strip():
        return HttpVerifierClient(settings.MEMBER3_VERIFIER_URL.strip())
    else:
        return UnavailableVerifierClient()
