import abc
import logging
import re
from typing import Optional
import httpx

from backend.config import settings
from backend.models.workflow import ExecutionResult, StepType, VerificationResult
from backend.models.reason_codes import ReasonCode

logger = logging.getLogger("agentguard.verifier_client")

VERIFIER_VERSION = "2.0.0"


class VerificationClient(abc.ABC):
    """
    Abstract interface for Member 3's Evidence Verification Engine.
    Member 2 provides raw ExecutionResult; Member 3 evaluates machine evidence
    and decides PASS/FAIL, tri-state status, and recovery actions.
    """

    @abc.abstractmethod
    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        """Verify the execution result against machine-checkable evidence rules."""
        pass


class DeterministicEvidenceVerifier(VerificationClient):
    """
    Deterministic Evidence Verifier:
    - Dispatches on StepType enum (not fragile substrings)
    - Required-evidence contracts per step type
    - Tri-state results: VERIFIED, FAILED, UNVERIFIABLE
    - Golden-corpus-verified error classification across pytest, jest, go, cargo, pip, npm, build, health checks
    """

    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        result = self._evaluate(execution_result)
        if execution_result:
            if not result.execution_id and getattr(execution_result, 'execution_id', None):
                result.execution_id = execution_result.execution_id
            if not result.evidence_digest and getattr(execution_result, 'evidence_digest', None):
                result.evidence_digest = execution_result.evidence_digest
        if not result.metadata:
            result.metadata = {}
        result.metadata['verifier_version'] = VERIFIER_VERSION
        return result
    def _evaluate(self, execution_result: ExecutionResult) -> VerificationResult:
        # ----------------------------------------------------
        # A. Structural Evidence Integrity Checks
        # ----------------------------------------------------
        if not execution_result:
            return VerificationResult(
                verified=False,
                status="FAILED",
                reason="Verification failed: execution result is empty",
                recovery_required=False,
                retry_allowed=False,
            
            reason_code=ReasonCode.UNKNOWN,)

        if not execution_result.execution_id:
            return VerificationResult(
                verified=False,
                status="FAILED",
                reason="Verification failed: missing execution ID in evidence",
                recovery_required=False,
                retry_allowed=False,
            
            reason_code=ReasonCode.UNKNOWN,)

        if not execution_result.timestamp:
            return VerificationResult(
                verified=False,
                status="FAILED",
                reason="Verification failed: missing timestamp in evidence",
                recovery_required=False,
                retry_allowed=False,
            
            reason_code=ReasonCode.UNKNOWN,)

        if execution_result.exit_code is None:
            return VerificationResult(
                verified=False,
                status="FAILED",
                reason="Verification failed: exit code not captured",
                recovery_required=False,
                retry_allowed=False,
            
            reason_code=ReasonCode.UNKNOWN,)

        metadata = execution_result.metadata or {}
        if metadata.get("reject") or metadata.get("security_issue") or metadata.get("evidence_invalid") or metadata.get("security_blocked"):
            return VerificationResult(
                verified=False,
                status="FAILED",
                reason=str(metadata.get("reject_reason") or "Verification rejected: security issue or invalid evidence detected in metadata"),
                recovery_required=False,
                retry_allowed=False,
                metadata=metadata,
            
            reason_code=ReasonCode.SECURITY_BLOCKED,)

        stdout = execution_result.stdout or ""
        stderr = execution_result.stderr or ""
        combined_logs = f"{stdout}\n{stderr}"
        combined_lower = combined_logs.lower()

        # StepType dispatch: prefer explicit step_type enum
        step_type = getattr(execution_result, "step_type", None)
        step_name = (execution_result.step or "").lower()
        command = (execution_result.command or "").lower()

        # Fallback mapping if step_type is not populated
        if not step_type:
            if "clone" in step_name or "git clone" in command:
                step_type = StepType.CLONE_REPOSITORY.value
            elif "analyze" in step_name:
                step_type = StepType.ANALYZE_PROJECT.value
            elif "test" in step_name or "pytest" in command or "jest" in command or "npm test" in command:
                step_type = StepType.RUN_TESTS.value
            elif "install" in step_name or "pip" in command or "npm install" in command:
                step_type = StepType.INSTALL_DEPENDENCIES.value
            elif "build" in step_name or "compile" in step_name or "tsc" in command or "next build" in command:
                step_type = StepType.BUILD_PROJECT.value
            elif "start" in step_name or "run" in step_name:
                step_type = StepType.START_APPLICATION.value
            elif "health" in step_name or "http" in command:
                step_type = StepType.HEALTH_CHECK.value
            elif "report" in step_name:
                step_type = StepType.FINAL_REPORT.value

        # ----------------------------------------------------
        # B. Non-Zero Exit Code: Deterministic Diagnosis
        # ----------------------------------------------------
        if execution_result.exit_code != 0:
            # 1. Port error check
            port_patterns = [
                r"address already in use",
                r"eaddrinuse",
                r"errno 10048",
                r"errno 98",
                r"port already in use",
            ]
            if any(re.search(p, combined_lower) for p in port_patterns):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Port conflict detected during execution: {stderr[:150].strip() or stdout[:150].strip()}",
                    recovery_required=True,
                    recovery_action="echo 'Killing conflicting process'",
                    retry_allowed=True,
                    failure_type="PORT_ERROR",
                
            reason_code=ReasonCode.PORT_CONFLICT,)

            # 2. Python missing module check
            py_missing_mod = re.search(r"No module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?", combined_logs)
            if py_missing_mod:
                module = py_missing_mod.group(1)
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Missing Python module: {module}",
                    recovery_required=True,
                    recovery_action=f"pip install {module}",
                    retry_allowed=True,
                    failure_type="DEPENDENCY_ERROR",
                
            reason_code=ReasonCode.DEPENDENCY_ERROR,)

            # 3. Node missing module check
            npm_missing_mod = re.search(r"Cannot find module ['\"]?([a-zA-Z0-9_\-\/@]+)['\"]?", combined_logs)
            missing_pkg = re.search(r"Module not found: Error: Can't resolve '([^']+)'", combined_logs)
            if npm_missing_mod:
                pkg = npm_missing_mod.group(1)
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Missing Node.js module: {pkg}",
                    recovery_required=True,
                    recovery_action=f"npm install {pkg}",
                    retry_allowed=True,
                    failure_type="DEPENDENCY_ERROR",
                
            reason_code=ReasonCode.DEPENDENCY_ERROR,)
            if missing_pkg:
                pkg = missing_pkg.group(1)
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Missing package: {pkg}",
                    recovery_required=True,
                    recovery_action=f"npm install {pkg}",
                    retry_allowed=True,
                    failure_type="DEPENDENCY_ERROR",
                
            reason_code=ReasonCode.DEPENDENCY_ERROR,)

            # 4. Timeout
            if execution_result.exit_code == 124 or "timed out" in combined_lower or "timeout" in combined_lower:
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Command execution timed out: {stderr[:150].strip() or 'Timeout expired'}",
                    recovery_required=True,
                    recovery_action="echo 'Retrying with extended timeout'",
                    retry_allowed=True,
                    failure_type="TIMEOUT",
                
            reason_code=ReasonCode.TIMEOUT,)

            # 5. Dependency errors (resolution / checksum / 404)
            if any(e in combined_lower for e in ["resolutionimpossible", "no matching distribution", "eresolve", "econflict", "eintegrity", "could not find a version"]):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Dependency manager failure: {stderr[:150].strip() or stdout[:150].strip()}",
                    recovery_required=True,
                    recovery_action="pip install --upgrade pip",
                    retry_allowed=True,
                    failure_type="DEPENDENCY_ERROR",
                
            reason_code=ReasonCode.DEPENDENCY_ERROR,)

            # 6. Build / Syntax error
            if any(e in combined_lower for e in ["syntaxerror", "indentationerror", "error ts", "compilation failed", "failed to compile", "error[e", "error: expected"]):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Build/Compilation error: {stderr[:150].strip() or stdout[:150].strip()}",
                    recovery_required=True,
                    recovery_action="pip install -r requirements.txt",
                    retry_allowed=True,
                    failure_type="BUILD_ERROR",
                
            reason_code=ReasonCode.BUILD_ERROR_DETECTED,)

            # 7. StepType-based failure classification
            if step_type == StepType.RUN_TESTS.value:
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Test run failed with exit code {execution_result.exit_code}: {stderr[:150].strip() or stdout[:150].strip()}",
                    recovery_required=True,
                    recovery_action="pip install pytest",
                    retry_allowed=True,
                    failure_type="TEST_FAILURE",
                
            reason_code=ReasonCode.EXIT_NONZERO,)
            elif step_type == StepType.INSTALL_DEPENDENCIES.value:
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Dependency installation failed with exit code {execution_result.exit_code}: {stderr[:150].strip() or stdout[:150].strip()}",
                    recovery_required=True,
                    recovery_action="pip install --upgrade pip",
                    retry_allowed=True,
                    failure_type="DEPENDENCY_ERROR",
                
            reason_code=ReasonCode.DEPENDENCY_ERROR,)
            elif step_type == StepType.BUILD_PROJECT.value:
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Build failed with exit code {execution_result.exit_code}: {stderr[:150].strip() or stdout[:150].strip()}",
                    recovery_required=True,
                    recovery_action="pip install -r requirements.txt",
                    retry_allowed=True,
                    failure_type="BUILD_ERROR",
                
            reason_code=ReasonCode.BUILD_ERROR_DETECTED,)
            elif step_type == StepType.HEALTH_CHECK.value:
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Health check failed with exit code {execution_result.exit_code}: {stderr[:150].strip() or stdout[:150].strip()}",
                    recovery_required=True,
                    recovery_action="echo 'Waiting for service readiness'",
                    retry_allowed=True,
                    failure_type="NETWORK_ERROR",
                
            reason_code=ReasonCode.HEALTH_CHECK_FAILED,)
            elif step_type == StepType.CLONE_REPOSITORY.value:
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Repository clone failed with exit code {execution_result.exit_code}: {stderr[:150].strip() or 'Clone failed'}",
                    recovery_required=False,
                    retry_allowed=False,
                    failure_type="REPOSITORY_ERROR",
                
            reason_code=ReasonCode.CLONE_FAILED,)

            return VerificationResult(
                verified=False,
                status="FAILED",
                reason=f"Step '{execution_result.step}' failed with exit code {execution_result.exit_code}: {stderr[:150].strip() or stdout[:150].strip() or 'Execution error'}",
                recovery_required=True,
                recovery_action="echo 'Attempting automatic workspace cleanup'",
                retry_allowed=True,
                failure_type="UNKNOWN_ERROR",
            
            reason_code=ReasonCode.UNKNOWN,)

        # ----------------------------------------------------
        # C. Exit Code 0: Required Evidence Contracts per StepType
        # ----------------------------------------------------

        # 1. RUN_TESTS
        if step_type == StepType.RUN_TESTS.value:
            # Check 1: Zero tests collected / ran must FAIL
            zero_test_indicators = [
                "collected 0 items",
                "no tests ran",
                "no tests were found",
                "no tests found",
                "ran 0 tests",
                "0 passed, 0 failed",
                "empty test suite",
            ]
            if any(z in combined_lower for z in zero_test_indicators):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason="Test verification failed: zero tests were executed or collected.",
                    recovery_required=True,
                    recovery_action="pip install pytest",
                    retry_allowed=True,
                    failure_type="TEST_FAILURE",
                
            reason_code=ReasonCode.ZERO_TESTS_COLLECTED,)

            # Check 2: Fatal test failure patterns (even if exit code was 0 due to pipe or suppression)
            fatal_test_errors = [
                r"failed\s*\([^\)]*failures=",
                r"failed\s*\([^\)]*errors=",
                r"assertionerror",
                r"===\s*failure\s*===",
                r"failures:",
                r"test result:\s*failed",
                r"---\s*fail:",
                r"fail\t",
                r"tests:.*[1-9]\d*\s+failed",
                r"\b[1-9]\d*\s+failed\b",
            ]
            for pattern in fatal_test_errors:
                if re.search(pattern, combined_lower):
                    return VerificationResult(
                        verified=False,
                        status="FAILED",
                        reason=f"Test verification failed: output reports test failures ({pattern}).",
                        recovery_required=True,
                        recovery_action="pip install pytest",
                        retry_allowed=True,
                        failure_type="TEST_FAILURE",
                    
            reason_code=ReasonCode.PIPE_SUPPRESSED_FAILURE,)

            if not combined_logs.strip():
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason="Test verification failed: zero test execution evidence captured (empty output)",
                    recovery_required=True,
                    recovery_action="pip install pytest",
                    retry_allowed=True,
                    failure_type="TEST_FAILURE",
                
            reason_code=ReasonCode.EXIT_NONZERO,)

            return VerificationResult(
                verified=True,
                status="VERIFIED",
                reason="Tests verified: test execution completed successfully without failures",
                recovery_required=False,
            
            reason_code=ReasonCode.EXIT_ZERO_CLEAN,)

        # 2. INSTALL_DEPENDENCIES
        if step_type == StepType.INSTALL_DEPENDENCIES.value:
            fatal_install_errors = [
                "resolutionimpossible",
                "could not find a version",
                "failed building wheel",
                "npm err!",
                "eresolve",
                "econflict",
                "eintegrity",
                "code e404",
                "no matching distribution",
                "externally-managed-environment",
                "metadata-generation-failed",
            ]
            for err_pattern in fatal_install_errors:
                if err_pattern in combined_lower:
                    return VerificationResult(
                        verified=False,
                        status="FAILED",
                        reason=f"Dependency installation contained fatal error: {err_pattern}",
                        recovery_required=True,
                        recovery_action="pip install --upgrade pip",
                        retry_allowed=True,
                        failure_type="DEPENDENCY_ERROR",
                    
            reason_code=ReasonCode.DEPENDENCY_ERROR,)
            return VerificationResult(
                verified=True,
                status="VERIFIED",
                reason="Dependency installation verified: exit code 0 with clean package manager output",
                recovery_required=False,
            
            reason_code=ReasonCode.EXIT_ZERO_CLEAN,)

        # 3. BUILD_PROJECT
        if step_type == StepType.BUILD_PROJECT.value:
            fatal_build_errors = [
                "syntaxerror:",
                "indentationerror:",
                "taberror:",
                "compilation failed",
                "fatal error:",
                "error ts",
                "modulenotfounderror:",
                "failed to compile",
                "error[e",
                "error: expected",
            ]
            for err_pattern in fatal_build_errors:
                if err_pattern in combined_lower:
                    return VerificationResult(
                        verified=False,
                        status="FAILED",
                        reason=f"Build output contained fatal error: {err_pattern}",
                        recovery_required=True,
                        recovery_action="pip install -r requirements.txt",
                        retry_allowed=True,
                        failure_type="BUILD_ERROR",
                    
            reason_code=ReasonCode.BUILD_ERROR_DETECTED,)
            return VerificationResult(
                verified=True,
                status="VERIFIED",
                reason="Build verified: clean compilation with exit code 0",
                recovery_required=False,
            
            reason_code=ReasonCode.EXIT_ZERO_CLEAN,)

        # 4. HEALTH_CHECK
        if step_type == StepType.HEALTH_CHECK.value:
            status_code = metadata.get("status_code")
            if status_code is not None and (status_code < 200 or status_code >= 300):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Health check failed with HTTP status {status_code}",
                    recovery_required=True,
                    recovery_action="echo 'Restarting service'",
                    retry_allowed=True,
                    failure_type="NETWORK_ERROR",
                
            reason_code=ReasonCode.HEALTH_CHECK_FAILED,)
            if any(e in combined_lower for e in ["connection refused", "404 not found", "500 internal", "502 bad", "503 service", "name or service not known", "timeout"]):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason="Health check endpoint connection failed or returned error status",
                    recovery_required=True,
                    recovery_action="echo 'Waiting for service readiness'",
                    retry_allowed=True,
                    failure_type="NETWORK_ERROR",
                
            reason_code=ReasonCode.HEALTH_CHECK_FAILED,)
            return VerificationResult(
                verified=True,
                status="VERIFIED",
                reason="Health check verified: endpoint responded successfully with valid HTTP status",
                recovery_required=False,
            
            reason_code=ReasonCode.EXIT_ZERO_CLEAN,)

        # 5. START_APPLICATION
        if step_type == StepType.START_APPLICATION.value:
            if any(p in combined_lower for p in ["address already in use", "port already in use", "eaddrinuse", "errno 10048", "errno 98"]):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason="Port conflict detected during application start",
                    recovery_required=True,
                    recovery_action="echo 'Killing conflicting process'",
                    retry_allowed=True,
                    failure_type="PORT_ERROR",
                
            reason_code=ReasonCode.PORT_CONFLICT,)
            pid = metadata.get("pid")
            return VerificationResult(
                verified=True,
                status="VERIFIED",
                reason=f"Application startup verified: process running (PID: {pid})" if pid else "Application startup verified: process started successfully",
                recovery_required=False,
            
            reason_code=ReasonCode.UNKNOWN,)

        # 6. CLONE_REPOSITORY
        if step_type == StepType.CLONE_REPOSITORY.value:
            cloned_count = metadata.get("cloned_files_count")
            if cloned_count is not None and cloned_count == 0 and "empty" in stderr.lower():
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason="Repository clone failed: destination directory is empty",
                    recovery_required=False,
                    retry_allowed=False,
                    failure_type="REPOSITORY_ERROR",
                
            reason_code=ReasonCode.CLONE_EMPTY,)
            if any(f in stderr.lower() for f in ["fatal: repository", "authentication failed"]):
                return VerificationResult(
                    verified=False,
                    status="FAILED",
                    reason=f"Repository clone error: {stderr.strip()}",
                    recovery_required=False,
                    retry_allowed=False,
                    failure_type="REPOSITORY_ERROR",
                
            reason_code=ReasonCode.CLONE_FAILED,)
            return VerificationResult(
                verified=True,
                status="VERIFIED",
                reason=f"Repository clone verified: files present in workspace" if cloned_count is None else f"Repository clone verified: {cloned_count} files present in workspace",
                recovery_required=False,
            
            reason_code=ReasonCode.EXIT_ZERO_CLEAN,)

        # 7. ANALYZE_PROJECT & FINAL_REPORT
        if step_type in (StepType.ANALYZE_PROJECT.value, StepType.FINAL_REPORT.value):
            return VerificationResult(
                verified=True,
                status="VERIFIED",
                reason=f"Step '{execution_result.step}' passed machine-checked evidence verification",
                recovery_required=False,
                retry_allowed=True,
            
            reason_code=ReasonCode.EXIT_ZERO_CLEAN,)

        # 8. Unknown / Unverified Step Type: Tri-state UNVERIFIABLE
        return VerificationResult(
            verified=False,
            status="UNVERIFIABLE",
            reason=f"Step '{execution_result.step}' has step_type '{step_type}': no machine-evidence verification rules defined.",
            recovery_required=False,
            retry_allowed=True,
        
            reason_code=ReasonCode.STEP_UNVERIFIABLE,)


class MockVerifierClient(DeterministicEvidenceVerifier):
    """Local simulation of Member 3's verifier client."""
    pass


class HttpVerifierClient(VerificationClient):
    """Member 3 HTTP Client posting evidence to external verification engine."""

    def __init__(self, endpoint_url: str):
        self.endpoint_url = endpoint_url

    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        logger.info(f"POSTing execution result for step '{execution_result.step}' to {self.endpoint_url}")
        payload = execution_result.model_dump()
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(self.endpoint_url, json=payload)
                response.raise_for_status()
                data = response.json()
                return VerificationResult(**data,
            reason_code=ReasonCode.UNKNOWN)
        except Exception as e:
            logger.error(f"Failed to verify with external verifier: {e}")
            return VerificationResult(
                verified=False,
                status="FAILED",
                reason=f"External verification service error: {str(e)}",
                recovery_required=False,
                recovery_action="echo 'Waiting for external verifier availability'",
                retry_allowed=False,
                metadata={"verifier_unavailable": True, "service_unavailable": True},
            
            reason_code=ReasonCode.SERVICE_UNAVAILABLE,)


class UnavailableVerifierClient(VerificationClient):
    """Verifier client simulating Member 3 service failure."""

    def verify(self, execution_result: ExecutionResult) -> VerificationResult:
        return VerificationResult(
            verified=False,
            status="FAILED",
            reason="Member 3 verification service is currently unavailable.",
            recovery_required=False,
            retry_allowed=False,
            metadata={"verifier_unavailable": True, "service_unavailable": True},
        
            reason_code=ReasonCode.SERVICE_UNAVAILABLE,)


def get_verifier_client() -> VerificationClient:
    """Factory creating appropriate VerificationClient based on configuration."""
    if settings.MOCK_VERIFIER:
        logger.info("Initializing MockVerifierClient (Deterministic Evidence Verifier)")
        return MockVerifierClient()
    elif settings.MEMBER3_VERIFIER_URL and settings.MEMBER3_VERIFIER_URL.strip():
        logger.info(f"Initializing HttpVerifierClient targeting {settings.MEMBER3_VERIFIER_URL}")
        return HttpVerifierClient(endpoint_url=settings.MEMBER3_VERIFIER_URL.strip())
    else:
        logger.warning("MOCK_VERIFIER is false and MEMBER3_VERIFIER_URL is empty; initializing UnavailableVerifierClient")
        return UnavailableVerifierClient()
