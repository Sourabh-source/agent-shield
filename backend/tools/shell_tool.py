import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.config import settings
from backend.models.workflow import ExecutionResult, current_iso_time
from backend.tools.base_tool import BaseTool


# Block dangerous commands to prevent host system damage
BLOCKED_PATTERNS = [
    r"\brm\s+-(?:rf|fr)\s+[/~]",                   # rm -rf / or ~
    r"\brmdir\s+/[sS]\s+/[qQ]\s+[cC]:\\",          # rmdir /s /q c:\
    r"\bdel\s+/[fF]\s+/[sS]\s+/[qQ]\s+[cC]:\\",    # del /f /s /q c:\
    r"\bformat\s+[a-zA-Z]:",                       # format c:
    r"\bmkfs\b",                                   # mkfs
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",   # fork bomb
    r"\bshutdown\b",                               # shutdown
    r"\breboot\b",                                 # reboot
    r">\s*/dev/sd[a-z]",                          # disk overwrite
    r"\bdd\s+if=.*of=/dev/",                       # dd disk write
]

# Sensitive patterns to redact from logs/outputs
SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?", r"\1=***REDACTED***"),
    (r"(AIzaSy[A-Za-z0-9_-]{20,})", r"***REDACTED_GEMINI_KEY***"),
    (r"(ghp_[A-Za-z0-9]{30,})", r"***REDACTED_GITHUB_TOKEN***"),
    (r"(AKIA[0-9A-Z]{16})", r"***REDACTED_AWS_KEY***"),
    (r"(Bearer\s+[A-Za-z0-9_\-\.]{20,})", r"Bearer ***REDACTED***"),
]

MAX_OUTPUT_SIZE = getattr(settings, "MAX_OUTPUT_SIZE", 1_000_000)


def redact_secrets(text: str) -> str:
    """Mask credentials and sensitive keys from output and command strings."""
    if not text:
        return ""
    sanitized = text
    for pattern, replacement in SECRET_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized


def truncate_output(text: str, max_size: int = MAX_OUTPUT_SIZE) -> str:
    """Limits output size to avoid memory exhaustion and oversized payloads."""
    if not text or len(text) <= max_size:
        return text
    truncated = text[:max_size]
    return f"{truncated}\n[TRUNCATED: Output exceeded {max_size} bytes limit]"


def check_path_containment(candidate: "str | Path", workspace: "str | Path") -> Tuple[bool, Optional[str]]:
    """
    Robust path containment check using resolved Path objects.
    Verifies that candidate path resolves strictly inside workspace.
    Uses candidate.resolve().relative_to(workspace.resolve()).
    Handles Windows and Linux paths, mixed separators, and traversal.
    """
    try:
        ws_resolved = Path(workspace).resolve()
        cand_str = str(candidate).strip("\"'")
        cand_path = Path(cand_str)
        if not cand_path.is_absolute():
            cand_resolved = (ws_resolved / cand_path).resolve()
        else:
            cand_resolved = cand_path.resolve()

        # candidate must be relative to workspace (or be the workspace itself)
        cand_resolved.relative_to(ws_resolved)
        return True, None
    except (ValueError, Exception) as exc:
        return False, f"Path traversal violation: '{candidate}' escapes workspace '{workspace}'"


def is_safe_command(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Validate that the command does not match dangerous host-destructive patterns
    or attempt illegal path traversal escaping the workspace.
    """
    if not command or not command.strip():
        return False, "Command cannot be empty"

    # Check for blocked command patterns across chained sub-commands
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Blocked potentially destructive command matching pattern: {pattern}"

    # Path traversal safety check: disallow traversal outside workspace
    if cwd:
        try:
            workspace_root = Path(cwd).resolve()
            is_git_clone = command.strip().lower().startswith("git clone")
            parts = command.replace(";", " ").replace("&&", " ").replace("||", " ").replace("|", " ").split()
            for i, part in enumerate(parts):
                clean_part = part.strip("\"'")
                # 1. Any token with '..' (e.g. ../sibling, ../../outside, src/..\..\other)
                if ".." in clean_part:
                    contained, err = check_path_containment(clean_part, workspace_root)
                    if not contained:
                        return False, err

                # For git clone, source repo can be an external repository path or URL;
                # only destination (the final path argument) must be contained in workspace.
                if is_git_clone and i < len(parts) - 1:
                    continue

                # 2. Any argument token (i > 0) representing an absolute path outside workspace
                if i > 0 and not clean_part.startswith("-") and not (
                    clean_part.startswith("http://")
                    or clean_part.startswith("https://")
                    or clean_part.startswith("git@")
                ):
                    try:
                        p = Path(clean_part)
                        if p.is_absolute():
                            contained, err = check_path_containment(p, workspace_root)
                            if not contained:
                                return False, err
                    except Exception:
                        pass
        except Exception:
            pass

    return True, None


def execute_shell_command(
    command: str,
    cwd: Optional[str] = None,
    timeout_seconds: int = 60,
    env: Optional[Dict[str, str]] = None,
    workflow_id: str = "unknown",
    step_name: str = "shell_command",
    step_id: Optional[str] = None,
) -> ExecutionResult:
    """
    Safely executes a shell command within the specified workspace.
    Captures stdout, stderr, exit_code, duration_ms, and returns structured ExecutionResult.
    """
    safe, reason = is_safe_command(command, cwd=cwd)
    if not safe:
        return ExecutionResult(
            workflow_id=workflow_id,
            step=step_name,
            step_id=step_id,
            command=redact_secrets(command),
            exit_code=126,
            stdout="",
            stderr=f"Security Violation: {reason}",
            duration_ms=0.0,
            timestamp=current_iso_time(),
            workspace=cwd,
            metadata={"security_blocked": True},
        )

    # Ensure working directory exists if provided
    resolved_cwd = None
    if cwd:
        path = Path(cwd).resolve()
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        resolved_cwd = str(path)

    # Merge environment variables
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)

    start_time = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=resolved_cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=proc_env,
        )
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = truncate_output(redact_secrets(proc.stdout))
        err = truncate_output(redact_secrets(proc.stderr))

        return ExecutionResult(
            workflow_id=workflow_id,
            step=step_name,
            step_id=step_id,
            command=redact_secrets(command),
            exit_code=proc.returncode,
            stdout=out,
            stderr=err,
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=resolved_cwd,
        )

    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout.decode() if exc.stdout else "")
        err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr.decode() if exc.stderr else "")
        return ExecutionResult(
            workflow_id=workflow_id,
            step=step_name,
            step_id=step_id,
            command=redact_secrets(command),
            exit_code=124,  # Standard timeout exit code
            stdout=truncate_output(redact_secrets(out)),
            stderr=truncate_output(redact_secrets(f"Command timed out after {timeout_seconds}s: {err}")),
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=resolved_cwd,
            metadata={"timed_out": True},
        )

    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExecutionResult(
            workflow_id=workflow_id,
            step=step_name,
            step_id=step_id,
            command=redact_secrets(command),
            exit_code=1,
            stdout="",
            stderr=f"Execution error: {str(exc)}",
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=resolved_cwd,
        )


class ShellTool(BaseTool):
    """Tool wrapper for generic shell commands."""

    def __init__(self):
        super().__init__(name="shell")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        return is_safe_command(command, cwd=cwd)

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        workflow_id: str = "unknown",
        step_name: str = "shell_command",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        return execute_shell_command(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
            workflow_id=workflow_id,
            step_name=step_name,
            step_id=step_id,
        )
