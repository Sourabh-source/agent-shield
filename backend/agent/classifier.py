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
            source_evidence="timeout exceeded",
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
            source_evidence=py_mod.group(0),
        )
    elif npm_mod:
        mod = npm_mod.group(1)
        return FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason=f"Missing Node.js module: {mod}",
            confidence=0.98,
            details={"module": mod, "ecosystem": "node"},
            source_evidence=npm_mod.group(0),
        )
    elif webpack_mod:
        mod = webpack_mod.group(1)
        return FailureClassification(
            failure_type=FailureType.DEPENDENCY_ERROR,
            reason=f"Unresolved package dependency: {mod}",
            confidence=0.95,
            details={"module": mod, "ecosystem": "node"},
            source_evidence=webpack_mod.group(0),
        )
    elif "ModuleNotFoundError" in combined or "ImportError" in combined:
        f_type = FailureType.IMPORT_ERROR if (exec_result.step and "import" in exec_result.step.lower()) else FailureType.DEPENDENCY_ERROR
        return FailureClassification(
            failure_type=f_type,
            reason="Python import/module dependency error",
            confidence=0.90,
            details={"ecosystem": "python"},
            source_evidence="ModuleNotFoundError/ImportError",
        )

    # 3. Port in use
    port_conflict_match = re.search(r"(EADDRINUSE|Address already in use|Errno 10048|Errno 98)", combined, re.IGNORECASE)
    if port_conflict_match:
        port_match = re.search(r":(\d{2,5})", combined)
        port = port_match.group(1) if port_match else "unknown"
        return FailureClassification(
            failure_type=FailureType.PORT_ERROR,
            reason=f"Network port conflict: port {port} is already in use",
            confidence=0.97,
            details={"port": port},
            source_evidence=port_conflict_match.group(0),
        )

    # 4. Network / Health check errors
    net_match = re.search(r"(connection refused|ConnectError|ETIMEDOUT|ENOTFOUND|Could not resolve host|HTTP [45]\d{2}|status code [45]\d{2})", combined, re.IGNORECASE)
    if net_match:
        f_type = FailureType.HEALTH_CHECK_FAILURE if (exec_result.step and "health" in exec_result.step.lower()) else FailureType.NETWORK_ERROR
        return FailureClassification(
            failure_type=f_type,
            reason="Health check endpoint connection failed or returned error status" if f_type == FailureType.HEALTH_CHECK_FAILURE else "Network connectivity failure or remote host unreachable",
            confidence=0.92,
            source_evidence=net_match.group(0),
        )

    # 5. Repository errors
    repo_match = re.search(r"(fatal: repository .* not found|fatal: destination path .* already exists)", combined, re.IGNORECASE)
    if repo_match:
        return FailureClassification(
            failure_type=FailureType.REPOSITORY_ERROR,
            reason="Git repository access or workspace path error",
            confidence=0.95,
            source_evidence=repo_match.group(0),
        )

    # 6. Test failures
    test_match = re.search(r"(FAILED \(failures=|failed, \d+ passed|AssertionError|test failed)", combined, re.IGNORECASE)
    if test_match:
        return FailureClassification(
            failure_type=FailureType.TEST_FAILURE,
            reason="Automated test suite assertion failure",
            confidence=0.94,
            source_evidence=test_match.group(0),
        )

    # 7. Permission errors
    perm_match = re.search(r"(Permission denied|PermissionError|EACCES|Access is denied)", combined, re.IGNORECASE)
    if perm_match:
        return FailureClassification(
            failure_type=FailureType.PERMISSION_ERROR,
            reason="Filesystem or process permission denied",
            confidence=0.93,
            source_evidence=perm_match.group(0),
        )

    # 8. Tool / command missing
    tool_match = re.search(r"(is not recognized as an internal or external command|command not found)", combined, re.IGNORECASE)
    if tool_match:
        return FailureClassification(
            failure_type=FailureType.TOOL_ERROR,
            reason="Required executable or CLI tool is not installed or in PATH",
            confidence=0.96,
            source_evidence=tool_match.group(0),
        )

    # 9. Resource limit errors
    res_match = re.search(r"(OutOfMemoryError|JavaScript heap out of memory|MemoryError|No space left on device|disk full)", combined, re.IGNORECASE)
    if res_match:
        return FailureClassification(
            failure_type=FailureType.RESOURCE_LIMIT,
            reason=f"Resource exhaustion detected: {res_match.group(1)}",
            confidence=0.96,
            details={"resource_type": "memory" if "memory" in res_match.group(1).lower() else "disk"},
            source_evidence=res_match.group(0),
        )

    # 10. Build & Syntax errors
    if exec_result.step and "compile" in exec_result.step.lower() and any(k in combined for k in ["SyntaxError", "IndentationError", "TabError"]):
        return FailureClassification(
            failure_type=FailureType.SYNTAX_ERROR,
            reason=f"Source code syntax error detected: {next((l for l in combined.splitlines() if 'SyntaxError' in l or 'IndentationError' in l), 'Syntax error')}",
            confidence=0.95,
            source_evidence="SyntaxError",
        )

    build_match = re.search(r"(SyntaxError|compilation error|build error|tsc: command failed)", combined, re.IGNORECASE)
    if build_match:
        return FailureClassification(
            failure_type=FailureType.BUILD_ERROR,
            reason="Source code syntax or compilation failure",
            confidence=0.91,
            source_evidence=build_match.group(0),
        )

    # 11. Runtime failures
    if "traceback (most recent call last):" in combined.lower():
        f_type = FailureType.RUNTIME_FAILURE if (exec_result.step and any(k in exec_result.step.lower() for k in ["start", "run", "notebook", "app"])) else FailureType.BUILD_ERROR
        return FailureClassification(
            failure_type=f_type,
            reason="Unhandled runtime exception traceback detected",
            confidence=0.93,
            source_evidence="Traceback",
        )

    # 11. Fallback unknown error
    first_err_line = next((line.strip() for line in stderr.splitlines() if line.strip()), "Execution failed with non-zero exit code")
    return FailureClassification(
        failure_type=FailureType.UNKNOWN_ERROR,
        reason=first_err_line[:150],
        confidence=0.50,
        source_evidence=first_err_line[:200],
    )
