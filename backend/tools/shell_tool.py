import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from backend.config import settings
from backend.models.workflow import ExecutionResult, current_iso_time
from backend.tools.base_tool import BaseTool


# Defense-in-depth secondary blocked patterns
BLOCKED_PATTERNS = [
    r"\brm\s+-(?:rf|fr)\s+[/~]",                   # rm -rf / or ~
    r"\brm\s+-(?:rf|fr)\s+/\*",                    # rm -rf /*
    r"\brmdir\s+/[sS]\s+/[qQ]\s+[cC]:\\",          # rmdir /s /q c:\
    r"\bdel\s+/[fF]\s+/[sS]\s+/[qQ]\s+[cC]:\\",    # del /f /s /q c:\
    r"Remove-Item\s+.*-Recurse\s+.*[cC]:\\",       # powershell delete C:
    r"\bformat\s+[a-zA-Z]:",                       # format c:
    r"\bmkfs\b",                                   # mkfs
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",   # fork bomb
    r"\bshutdown\b",                               # shutdown
    r"\breboot\b",                                 # reboot
    r">\s*/dev/sd[a-z]",                          # disk overwrite
    r"\bdd\s+if=.*of=/dev/",                       # dd disk write
    r"(?:curl|wget)\s+.*\|\s*(?:bash|sh|cmd|powershell)", # piped remote script execution
]

# Sensitive patterns to redact from logs/outputs
SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{8,})['\"]?", r"\1=***REDACTED***"),
    (r"(AIzaSy[A-Za-z0-9_-]{20,})", r"***REDACTED_GEMINI_KEY***"),
    (r"(ghp_[A-Za-z0-9]{30,})", r"***REDACTED_GITHUB_TOKEN***"),
    (r"(AKIA[0-9A-Z]{16})", r"***REDACTED_AWS_KEY***"),
    (r"(Bearer\s+[A-Za-z0-9_\-\.]{20,})", r"Bearer ***REDACTED***"),
]

# Allowlisted executables permitted for tool execution
ALLOWED_EXECUTABLES = frozenset({
    "git", "pip", "pip3", "python", "python3", "py",
    "node", "npm", "npx", "yarn", "pnpm",
    "pytest", "tsc", "eslint", "prettier",
    "echo", "cat", "exit", "powershell", "taskkill",
})

# Safe environment variables allowlist (never copy unverified host os.environ)
ALLOWED_ENV_KEYS = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "USERPROFILE", "HOMEPATH", "HOMEDRIVE",
    "HOME", "USER", "USERNAME", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "NODE_PATH", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONIOENCODING",
    "PYTHONUTF8", "PIP_DISABLE_PIP_VERSION_CHECK", "PYTHONUNBUFFERED",
    "OS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "NUMBER_OF_PROCESSORS", "COMMONPROGRAMFILES", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "COMMONPROGRAMFILES(X86)", "APPDATA",
    "LOCALAPPDATA", "ALLUSERSPROFILE", "PUBLIC", "SYSTEMDRIVE",
})

SENSITIVE_ENV_SUBSTRINGS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH",
    "GEMINI", "AWS", "GITHUB", "DATABASE", "PRIVATE",
)

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


def validate_and_parse_command(
    command: str,
    cwd: Optional[str] = None,
) -> Tuple[bool, Optional[str], List[List[str]]]:
    """
    Validates that command uses allowlisted executables, does not use shell
    metacharacters (pipes, backticks, redirection, subshells, unauthorized chaining),
    and conforms to workspace path containment.
    Returns (is_safe, error_reason, list_of_argv_commands).
    """
    if not command or not command.strip():
        return False, "Command cannot be empty", []

    # Secondary defense: check regex blocked patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Blocked potentially destructive command matching pattern: {pattern}", []

    # Split chained commands on &&
    sub_cmds = [c.strip() for c in command.split("&&")]
    parsed_commands: List[List[str]] = []

    workspace_root = Path(cwd).resolve() if cwd else None

    for sub in sub_cmds:
        if not sub:
            return False, "Empty chained command segment", []

        try:
            tokens = shlex.split(sub, posix=True)
        except Exception as exc:
            return False, f"Command parsing syntax error: {str(exc)}", []

        if not tokens:
            return False, "Command resolved to empty token list", []

        # Validate executable allowlist
        exe_token = tokens[0]
        exe_stem = Path(exe_token).stem.lower()
        is_allowed_exe = (
            exe_stem in ALLOWED_EXECUTABLES
            or (
                Path(exe_token).is_absolute()
                and Path(exe_token).resolve() == Path(sys.executable).resolve()
            )
        )
        if not is_allowed_exe:
            return False, f"Executable '{exe_token}' is not in the security allowlist", []

        # Validate tokens against dangerous shell operators & metacharacters
        is_python_cmd = exe_stem in ("python", "python3", "py")
        is_git_clone = (exe_stem == "git" and len(tokens) > 1 and tokens[1] == "clone")

        for i, token in enumerate(tokens):
            # Backtick command substitution
            if "`" in token:
                return False, f"Backtick command substitution forbidden: {token}", []

            # Subshell expansion $(...)
            if "$(" in token:
                return False, f"Subshell expansion forbidden: {token}", []

            # Shell operators that must never be unquoted arguments
            if token in (";", "|", "&", ">", "<", ">>", "2>", "2>&1", "<<", "<<<"):
                return False, f"Shell operator forbidden: '{token}'", []

            # Unquoted semicolon attached to token (e.g. 'hello;') outside python -c args
            if ";" in token and not (is_python_cmd and i > 1):
                return False, f"Semicolon command separator forbidden: '{token}'", []

            # Path containment check if cwd is provided
            if workspace_root and i > 0:
                clean_tok = token.strip("\"'")
                # Exclude python -c code or flags
                if is_python_cmd and tokens[1] == "-c" and i >= 2:
                    continue
                # Exclude flags
                if clean_tok.startswith("-"):
                    continue
                # For git clone, intermediate repository URL is external
                if is_git_clone and i < len(tokens) - 1:
                    continue
                # Check tokens containing traversal ..
                if ".." in clean_tok:
                    contained, err = check_path_containment(clean_tok, workspace_root)
                    if not contained:
                        return False, err, []
                # Check absolute paths
                elif not (
                    clean_tok.startswith("http://")
                    or clean_tok.startswith("https://")
                    or clean_tok.startswith("git@")
                ):
                    try:
                        p = Path(clean_tok)
                        if p.is_absolute():
                            contained, err = check_path_containment(p, workspace_root)
                            if not contained:
                                return False, err, []
                    except Exception:
                        pass

        parsed_commands.append(tokens)

    return True, None, parsed_commands


def is_safe_command(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Backward-compatible validation wrapper."""
    safe, reason, _ = validate_and_parse_command(command, cwd=cwd)
    return safe, reason


def build_safe_environment(env_override: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Constructs an isolated environment containing only allowlisted keys from os.environ
    and caller-provided overrides with sensitive keys stripped.
    """
    proc_env = {
        k: v for k, v in os.environ.items()
        if k.upper() in ALLOWED_ENV_KEYS
    }

    if env_override:
        for k, v in env_override.items():
            k_upper = k.upper()
            if any(sens in k_upper for sens in SENSITIVE_ENV_SUBSTRINGS):
                continue
            proc_env[k] = v

    return proc_env


def execute_shell_command(
    command: Union[str, List[str]],
    cwd: Optional[str] = None,
    timeout_seconds: int = 60,
    env: Optional[Dict[str, str]] = None,
    workflow_id: str = "unknown",
    step_name: str = "shell_command",
    step_id: Optional[str] = None,
) -> ExecutionResult:
    """
    Safely executes an allowlisted command using argv arrays with shell=False.
    Supports command sequences joined by &&.
    Captures stdout, stderr, exit_code, duration_ms, and returns structured ExecutionResult.
    """
    cmd_str = command if isinstance(command, str) else " ".join(command)

    safe, reason, parsed_commands = validate_and_parse_command(cmd_str, cwd=cwd)
    if not safe:
        return ExecutionResult(
            workflow_id=workflow_id,
            step=step_name,
            step_id=step_id,
            command=redact_secrets(cmd_str),
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

    proc_env = build_safe_environment(env)
    start_time = time.perf_counter()

    accumulated_stdout: List[str] = []
    accumulated_stderr: List[str] = []
    last_exit_code = 0

    for tokens in parsed_commands:
        exe_lower = tokens[0].lower()

        # Handle built-in commands directly without shell
        if exe_lower == "echo":
            out_str = " ".join(tokens[1:]) + "\n"
            accumulated_stdout.append(out_str)
            last_exit_code = 0
            continue

        if exe_lower == "exit":
            code = 0
            if len(tokens) > 1 and tokens[1].lstrip("-").isdigit():
                code = int(tokens[1])
            last_exit_code = code
            if last_exit_code != 0:
                break
            continue

        if exe_lower == "cat" and not shutil.which("cat"):
            # Minimal emulation for environments without cat (e.g. Windows)
            if len(tokens) > 1:
                target_file = Path(resolved_cwd or ".") / tokens[1] if not Path(tokens[1]).is_absolute() else Path(tokens[1])
                if target_file.exists() and target_file.is_file():
                    try:
                        content = target_file.read_text(encoding="utf-8", errors="replace")
                        accumulated_stdout.append(content)
                        last_exit_code = 0
                    except Exception as e:
                        accumulated_stderr.append(f"cat: {e}\n")
                        last_exit_code = 1
                else:
                    accumulated_stderr.append(f"cat: {tokens[1]}: No such file or directory\n")
                    last_exit_code = 1
            else:
                last_exit_code = 0
            if last_exit_code != 0:
                break
            continue

        # If python/pip executable is specified as a simple name, normalize to current python
        run_tokens = list(tokens)
        if run_tokens[0].lower() in ("python", "python3") and not Path(run_tokens[0]).is_absolute():
            run_tokens[0] = sys.executable
        elif run_tokens[0].lower() in ("pip", "pip3") and not Path(run_tokens[0]).is_absolute():
            run_tokens = [sys.executable, "-m", "pip"] + run_tokens[1:]

        try:
            proc = subprocess.run(
                run_tokens,
                cwd=resolved_cwd,
                shell=False,  # CRITICAL: shell=False prevents shell injection
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=proc_env,
            )
            accumulated_stdout.append(proc.stdout or "")
            accumulated_stderr.append(proc.stderr or "")
            last_exit_code = proc.returncode

            if last_exit_code != 0:
                break

        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            out = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout.decode() if exc.stdout else "")
            err = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr.decode() if exc.stderr else "")
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=redact_secrets(cmd_str),
                exit_code=124,
                stdout=truncate_output(redact_secrets("".join(accumulated_stdout) + (out or ""))),
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
                command=redact_secrets(cmd_str),
                exit_code=1,
                stdout=truncate_output(redact_secrets("".join(accumulated_stdout))),
                stderr=f"Execution error: {str(exc)}",
                duration_ms=duration_ms,
                timestamp=current_iso_time(),
                workspace=resolved_cwd,
            )

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    final_stdout = truncate_output(redact_secrets("".join(accumulated_stdout)))
    final_stderr = truncate_output(redact_secrets("".join(accumulated_stderr)))

    return ExecutionResult(
        workflow_id=workflow_id,
        step=step_name,
        step_id=step_id,
        command=redact_secrets(cmd_str),
        exit_code=last_exit_code,
        stdout=final_stdout,
        stderr=final_stderr,
        duration_ms=duration_ms,
        timestamp=current_iso_time(),
        workspace=resolved_cwd,
    )


class ShellTool(BaseTool):
    """Tool wrapper for generic shell commands."""

    def __init__(self):
        super().__init__(name="shell")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        safe, reason = is_safe_command(command, cwd=cwd)
        return safe, reason

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
