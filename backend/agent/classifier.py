import re
from typing import Optional

from backend.models.workflow import ExecutionResult, FailureClassification, FailureType


def classify_failure(exec_result: ExecutionResult) -> FailureClassification:
    """
    Deterministically classifies a failed execution result into a structured category
    with a confidence score and human-readable reason.
    """
    stdout = exec_result.stdout or ""
    stderr = exec_result.stderr or ""
    combined = f"{stdout}\n{stderr}"
    exit_code = exec_result.exit_code

    # 1. Timeout check
    if exit_code == 124 or "timed out" in combined.lower() or (exec_result.metadata and exec_result.metadata.get("timed_out")):
        return FailureClassification(
            failure_type=FailureType.TIMEOUT,
            reason=f"Command exceeded allocated timeout ({exec_result.duration_ms}ms)",
            confidence=0.99,
            details={"duration_ms": exec_result.duration_ms},
        )

    # 2. Dependency errors (Python & Node)
    py_mod = re.search(r"No module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?", combined)
    npm_mod = re.search(r"Cannot find module ['\"]?([a-zA-Z0-9_\-\/@]+)['\"]?", combined)
    webpack_mod = re.search(r"Module not found: Error: Can't resolve '([^']+)'", combined)
    if py_mod:
        mod = py_mod.group(1)
        return FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason=f"Missing Python module: {mod}",
            confidence=0.98,
            details={"module": mod, "ecosystem": "python"},
        )
    elif npm_mod:
        mod = npm_mod.group(1)
        return FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason=f"Missing Node.js module: {mod}",
            confidence=0.98,
            details={"module": mod, "ecosystem": "node"},
        )
    elif webpack_mod:
        mod = webpack_mod.group(1)
        return FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason=f"Unresolved package dependency: {mod}",
            confidence=0.95,
            details={"module": mod, "ecosystem": "node"},
        )
    elif "ModuleNotFoundError" in combined or "ImportError" in combined:
        return FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason="Python import/module dependency error",
            confidence=0.90,
            details={"ecosystem": "python"},
        )

    # 3. Port in use
    if re.search(r"(EADDRINUSE|Address already in use|Errno 10048|Errno 98)", combined, re.IGNORECASE):
        port_match = re.search(r":(\d{2,5})", combined)
        port = port_match.group(1) if port_match else "unknown"
        return FailureClassification(
            failure_type=FailureType.PORT_ERROR,
            reason=f"Network port conflict: port {port} is already in use",
            confidence=0.97,
            details={"port": port},
        )

    # 4. Network errors
    if re.search(r"(connection refused|ConnectError|ETIMEDOUT|ENOTFOUND|Could not resolve host)", combined, re.IGNORECASE):
        return FailureClassification(
            failure_type=FailureType.NETWORK_ERROR,
            reason="Network connectivity failure or remote host unreachable",
            confidence=0.92,
        )

    # 5. Repository errors
    if re.search(r"(fatal: repository .* not found|fatal: destination path .* already exists)", combined, re.IGNORECASE):
        return FailureClassification(
            failure_type=FailureType.REPOSITORY_ERROR,
            reason="Git repository access or workspace path error",
            confidence=0.95,
        )

    # 6. Test failures
    if re.search(r"(FAILED \(failures=|failed, \d+ passed|AssertionError|test failed)", combined, re.IGNORECASE):
        return FailureClassification(
            failure_type=FailureType.TEST_FAILURE,
            reason="Automated test suite assertion failure",
            confidence=0.94,
        )

    # 7. Permission errors
    if re.search(r"(Permission denied|PermissionError|EACCES|Access is denied)", combined, re.IGNORECASE):
        return FailureClassification(
            failure_type=FailureType.PERMISSION_ERROR,
            reason="Filesystem or process permission denied",
            confidence=0.93,
        )

    # 8. Tool / command missing
    if re.search(r"(is not recognized as an internal or external command|command not found)", combined, re.IGNORECASE):
        return FailureClassification(
            failure_type=FailureType.TOOL_ERROR,
            reason="Required executable or CLI tool is not installed or in PATH",
            confidence=0.96,
        )

    # 9. Build errors
    if re.search(r"(SyntaxError|compilation error|build error|tsc: command failed)", combined, re.IGNORECASE):
        return FailureClassification(
            failure_type=FailureType.BUILD_ERROR,
            reason="Source code syntax or compilation failure",
            confidence=0.91,
        )

    # 10. Fallback unknown error
    first_err_line = next((line.strip() for line in stderr.splitlines() if line.strip()), "Execution failed with non-zero exit code")
    return FailureClassification(
        failure_type=FailureType.UNKNOWN_ERROR,
        reason=first_err_line[:150],
        confidence=0.50,
    )
