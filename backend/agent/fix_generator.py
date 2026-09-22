"""
Engine for synthesizing actionable Recommended Fixes from workflow execution,
verification, recovery history, and classified failure evidence.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.models.workflow import (
    FailureType,
    RecommendedFix,
    RecommendedFixesSummary,
    RecoveryAttempt,
    RecoveryOutcome,
    StepDefinition,
    StepStatus,
    WorkflowEvent,
    WorkflowState,
    WorkflowStatus,
)


class _IssueCandidate:
    """Internal accumulator for grouping and deduplicating workflow issues."""

    def __init__(
        self,
        step_name: str,
        failure_type: str,
        reason: str = "",
        details: Optional[Dict[str, Any]] = None,
        source_evidence: str = "",
        command: str = "",
        exit_code: int = 0,
    ) -> None:
        self.step_name = step_name
        self.failure_type = failure_type
        self.reason = reason
        self.details: Dict[str, Any] = details or {}
        self.source_evidence = source_evidence
        self.command = command
        self.exit_code = exit_code
        self.occurrences: int = 1
        self.retries: int = 0
        self.recovery_attempted: Optional[str] = None
        self.recovery_result: Optional[str] = None
        self.recovered: bool = False
        self.unverified: bool = False
        self.final_step_status: Optional[StepStatus] = None


def _normalize_failure_type(ft: Optional[str]) -> str:
    if not ft:
        return "UNKNOWN_ERROR"
    return str(ft).strip().upper()


def _extract_module_from_text(text: str) -> Optional[str]:
    m = re.search(r"No module named [\'\"]([^\'\"]+)[\'\"]", text)
    if m:
        return m.group(1)
    m = re.search(r"Cannot find module [\'\"]([^\'\"]+)[\'\"]", text)
    if m:
        return m.group(1)
    m = re.search(r"ModuleNotFoundError:\s+No module named [\'\"]?([a-zA-Z0-9_\-]+)[\'\"]?", text)
    if m:
        return m.group(1)
    return None


def _extract_port_from_text(text: str) -> Optional[str]:
    m = re.search(r":(\d{2,5})", text)
    if m:
        return m.group(1)
    m = re.search(r"port\s+(\d{2,5})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _synthesize_content(cand: _IssueCandidate) -> Tuple[str, str, str, str]:
    """
    Synthesizes (title, what_happened, diagnosis, recommended_fix) for an issue candidate.
    """
    ft = cand.failure_type
    step = cand.step_name
    details = cand.details or {}
    evidence = (cand.source_evidence or cand.reason or "").strip()
    cmd = (cand.command or details.get("command") or "").lower()

    is_pytest = (
        "pytest" in cmd
        or "pytest" in evidence.lower()
        or details.get("missing_executable") == "pytest"
        or details.get("module") == "pytest"
    )

    # 1. COMMAND_NOT_FOUND / EXECUTABLE_NOT_FOUND / WinError 2
    if ft in ("COMMAND_NOT_FOUND", "EXECUTABLE_NOT_FOUND") or "[winerror 2]" in evidence.lower():
        if is_pytest:
            title = f"{step} — pytest executable not found"
            what_happened = "Windows could not find the pytest executable (WinError 2)."
            diagnosis = "pytest is installed/attempted to be installed, but the executable is not available through PATH."
            recommended_fix = "Use `python -m pytest` with the same Python environment used for dependency installation."
            return title, what_happened, diagnosis, recommended_fix
        else:
            missing = details.get("missing_executable") or "command"
            title = f"{step} — {missing} executable not found"
            what_happened = f"Windows could not find the {missing} executable (system cannot find the file specified)."
            diagnosis = f"The '{missing}' executable is not available through system PATH or was not found in the active environment."
            recommended_fix = f"Ensure '{missing}' is installed in the active environment or invoke it via the environment interpreter (e.g. `python -m {missing}`)."
            return title, what_happened, diagnosis, recommended_fix

    # 2. DEPENDENCY_ERROR / IMPORT_ERROR
    if ft in ("DEPENDENCY_ERROR", "IMPORT_ERROR") or "modulenotfounderror" in evidence.lower():
        mod = details.get("module") or _extract_module_from_text(evidence) or "dependency"
        eco = details.get("ecosystem") or "python"
        title = f"{step} — missing dependency: {mod}" if mod != "dependency" else f"{step} — missing dependency"
        what_happened = f"Dependency was missing." if mod == "dependency" else f"Dependency '{mod}' was missing."
        diagnosis = f"The required package '{mod}' is not installed in the active {eco} environment."
        if eco == "node":
            recommended_fix = f"Install the missing dependency using `npm install {mod}` and add it to package.json."
        else:
            recommended_fix = f"Install the missing dependency using `pip install {mod}` and add it to requirements.txt."
        return title, what_happened, diagnosis, recommended_fix

    # 3. PORT_ERROR / Port Conflict
    if ft == "PORT_ERROR" or any(k in evidence.lower() for k in ("eaddrinuse", "already in use", "10048")):
        port = details.get("port") or _extract_port_from_text(evidence) or "target"
        title = f"{step} — port {port} conflict" if port != "target" else f"{step} — port conflict"
        what_happened = f"Network port {port} is already in use by another active process."
        diagnosis = f"Port {port} is occupied, preventing the application service from binding."
        recommended_fix = f"Free port {port} or configure the application to listen on an available port (e.g. set PORT=8001)."
        return title, what_happened, diagnosis, recommended_fix

    # 4. PROCESS_EXITED
    if ft == "PROCESS_EXITED" or "process with pid" in evidence.lower():
        title = f"{step} — process terminated unexpectedly"
        what_happened = "Application started in background but exited prematurely (process is not running)."
        diagnosis = "The application process exited immediately after launch instead of continuing to run as a persistent service."
        recommended_fix = "Inspect application startup logs for runtime crashes or unhandled exceptions, and ensure the server listen loop is not exiting."
        return title, what_happened, diagnosis, recommended_fix

    # 5. TEST_FAILURE
    if ft == "TEST_FAILURE" or "assertionerror" in evidence.lower() or "failures=" in evidence.lower():
        title = f"{step} — test assertion failure"
        what_happened = "One or more automated test assertions failed during test execution."
        diagnosis = "Automated test suite assertion failure: application behavior does not match expected test assertions."
        recommended_fix = "Review test failure stack traces and fix failing code logic or update assertions."
        return title, what_happened, diagnosis, recommended_fix

    # 6. HEALTH_CHECK_FAILURE / SERVICE_NOT_LISTENING
    if ft in ("HEALTH_CHECK_FAILURE", "SERVICE_NOT_LISTENING") or "connection refused" in evidence.lower():
        title = f"{step} — health check connection failed"
        what_happened = "Health check endpoint connection failed or returned an error status code."
        diagnosis = "The service process is either not listening on the expected host/port, or the health route failed."
        recommended_fix = "Verify server host binding (use 0.0.0.0 or 127.0.0.1), check endpoint routing, and ensure initialization finishes before probes."
        return title, what_happened, diagnosis, recommended_fix

    # 7. BUILD_ERROR / SYNTAX_ERROR
    if ft in ("BUILD_ERROR", "SYNTAX_ERROR") or "syntaxerror" in evidence.lower():
        title = f"{step} — syntax or compilation error"
        what_happened = "Source code compilation or syntax check encountered an error."
        diagnosis = "Source files contain invalid syntax or failing compilation targets."
        recommended_fix = "Review syntax error tracebacks and correct invalid syntax or compiler configuration."
        return title, what_happened, diagnosis, recommended_fix

    # 8. RESOURCE_LIMIT
    if ft == "RESOURCE_LIMIT" or "memory" in evidence.lower() or "heap" in evidence.lower():
        title = f"{step} — resource limit exceeded"
        what_happened = "Process exhausted system memory or disk resources."
        diagnosis = "Process exceeded resource allocation limits during execution."
        recommended_fix = "Increase available process memory or optimize resource allocation for the step."
        return title, what_happened, diagnosis, recommended_fix

    # 9. PERMISSION_ERROR
    if ft == "PERMISSION_ERROR" or "permission denied" in evidence.lower() or "access is denied" in evidence.lower():
        title = f"{step} — permission denied"
        what_happened = "Filesystem or process permission was denied during execution."
        diagnosis = "The process lacks sufficient privileges to access the requested path or resource."
        recommended_fix = "Adjust file permissions or run with appropriate directory access rights."
        return title, what_happened, diagnosis, recommended_fix

    # 10. Fallback generic
    title = f"{step} — verification failure"
    what_happened = cand.reason or "Step execution failed with non-zero exit code."
    diagnosis = cand.reason or "Verification conditions were not satisfied."
    recommended_fix = "Check execution logs and stderr output to address the reported failure."
    return title, what_happened, diagnosis, recommended_fix


def generate_recommended_fixes(workflow: WorkflowState) -> Tuple[List[RecommendedFix], RecommendedFixesSummary]:
    """
    Builds an evidence-backed Fix List from workflow execution, verification,
    recovery history, retries, and events. Deduplicates repeated failures from retries.
    """
    candidates: Dict[Tuple[str, str], _IssueCandidate] = {}

    step_map: Dict[str, StepDefinition] = {s.name: s for s in workflow.steps}

    # 1. Inspect Workflow Events for failure classifications and verification failures
    for evt in workflow.events:
        step_name = evt.step or ""
        if not step_name and evt.step_id:
            for s in workflow.steps:
                if s.id == evt.step_id:
                    step_name = s.name
                    break
        if not step_name:
            continue

        if evt.event_type == "FAILURE_CLASSIFIED":
            ev_data = evt.evidence or {}
            ft = _normalize_failure_type(ev_data.get("failure_type"))
            reason = ev_data.get("reason", evt.message)
            details = ev_data.get("details") or {}
            source_ev = str(ev_data.get("source_evidence") or "")
            key = (step_name, ft)

            if key in candidates:
                cand = candidates[key]
                cand.occurrences += 1
                if not cand.details and details:
                    cand.details = details
                if not cand.source_evidence and source_ev:
                    cand.source_evidence = source_ev
            else:
                candidates[key] = _IssueCandidate(
                    step_name=step_name,
                    failure_type=ft,
                    reason=reason,
                    details=details,
                    source_evidence=source_ev,
                )

        elif evt.event_type == "VERIFICATION_FAILED":
            ev_data = evt.evidence or {}
            stderr = str(ev_data.get("stderr") or "")
            exit_code = int(ev_data.get("exit_code") or 1)
            for (sn, ft), cand in list(candidates.items()):
                if sn == step_name:
                    if not cand.source_evidence and stderr:
                        cand.source_evidence = stderr
                    cand.exit_code = exit_code

    # 2. Correlate with recovery history
    for attempt in workflow.recovery_history:
        step_name = attempt.step_name
        ft = _normalize_failure_type(attempt.failure_type)
        key = (step_name, ft)

        if key not in candidates:
            matched = False
            for (sn, f_t), cand in candidates.items():
                if sn == step_name:
                    key = (sn, f_t)
                    matched = True
                    break
            if not matched:
                candidates[key] = _IssueCandidate(
                    step_name=step_name,
                    failure_type=ft,
                    reason=f"Recovery triggered for {ft}",
                )

        cand = candidates[key]
        cand.recovery_attempted = attempt.action
        if getattr(attempt, "step_resolved", False) is True:
            cand.recovered = True
            cand.recovery_result = "Recovery succeeded."
        else:
            status_str = str(attempt.status)
            cand.recovery_result = f"Recovery attempted ({status_str}) but step did not resolve."

    # 3. Check steps for failures or unverified states not in events/recoveries
    for step in workflow.steps:
        step_name = step.name
        is_failed = step.status == StepStatus.FAILED
        is_unverified = step.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.VERIFYING)
        has_verif_fail = (
            step.verification_result is not None
            and not step.verification_result.verified
            and step.status != StepStatus.NOT_APPLICABLE
        )

        step_cands = [c for (sn, _), c in candidates.items() if sn == step_name]

        if is_failed or has_verif_fail:
            if not step_cands:
                ft = "UNKNOWN_ERROR"
                reason = "Step execution failed"
                source_ev = ""
                exit_c = 1
                cmd_str = step.command or ""

                if step.verification_result:
                    ft = _normalize_failure_type(step.verification_result.failure_type)
                    reason = step.verification_result.reason or reason
                if step.execution_result:
                    source_ev = step.execution_result.stderr or step.execution_result.stdout
                    exit_c = step.execution_result.exit_code
                    cmd_str = step.execution_result.command or cmd_str

                key = (step_name, ft)
                cand = _IssueCandidate(
                    step_name=step_name,
                    failure_type=ft,
                    reason=reason,
                    source_evidence=source_ev,
                    command=cmd_str,
                    exit_code=exit_c,
                )
                candidates[key] = cand
                step_cands = [cand]

        for cand in step_cands:
            cand.final_step_status = step.status
            if step.command and not cand.command:
                cand.command = step.command
            cand.retries = max(cand.retries, step.retries, cand.occurrences - 1)

            if step.status == StepStatus.VERIFIED_SUCCESS:
                if cand.recovered or cand.retries > 0 or cand.recovery_attempted:
                    cand.recovered = True
                    cand.recovery_result = cand.recovery_result or "Recovery succeeded. Step subsequently verified."
            elif step.status == StepStatus.FAILED:
                cand.recovered = False
            elif is_unverified:
                cand.unverified = True

    # 4. Filter and build RecommendedFix objects
    fix_list: List[RecommendedFix] = []

    for _, cand in candidates.items():
        if cand.final_step_status == StepStatus.NOT_APPLICABLE:
            continue
        if cand.final_step_status == StepStatus.VERIFIED_SUCCESS and not cand.recovered and cand.retries == 0:
            continue

        title, what_happened, diagnosis, recommended_fix = _synthesize_content(cand)

        if cand.recovered and cand.final_step_status == StepStatus.VERIFIED_SUCCESS:
            status = "RECOVERED"
            severity = "MEDIUM"
        elif cand.unverified:
            status = "UNVERIFIED"
            severity = "MEDIUM"
        else:
            status = "ACTION REQUIRED"
            severity = "HIGH"

        fix_item = RecommendedFix(
            id="",
            title=title,
            step=cand.step_name,
            what_happened=what_happened,
            diagnosis=diagnosis,
            recommended_fix=recommended_fix,
            status=status,
            severity=severity,
            recovery_attempted=cand.recovery_attempted,
            recovery_result=cand.recovery_result,
            retries=cand.retries,
            failure_type=cand.failure_type,
            details=cand.details or None,
        )
        fix_list.append(fix_item)

    # Sort fixes: ACTION REQUIRED first, UNVERIFIED second, RECOVERED third
    status_priority = {"ACTION REQUIRED": 0, "UNVERIFIED": 1, "RECOVERED": 2}
    fix_list.sort(key=lambda f: status_priority.get(f.status, 3))

    for idx, f in enumerate(fix_list, start=1):
        f.id = f"FIX {idx:02d}"

    issues_found = len(fix_list)
    recovered_automatically = sum(1 for f in fix_list if f.status == "RECOVERED")
    action_required = sum(1 for f in fix_list if f.status == "ACTION REQUIRED")
    total_retries = max(sum(f.retries for f in fix_list), workflow.retries)

    summary = RecommendedFixesSummary(
        issues_found=issues_found,
        recovered_automatically=recovered_automatically,
        action_required=action_required,
        retries=total_retries,
    )

    return fix_list, summary
