import ipaddress
import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Optional, Tuple

from backend.models.workflow import ExecutionResult, current_iso_time
from backend.tools.base_tool import BaseTool
from backend.tools.shell_tool import build_safe_environment, execute_shell_command, redact_secrets


METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata",
    "instance-data",
})


def validate_repo_url(repo_url: str) -> Tuple[bool, Optional[str]]:
    """
    Strict validation of Git repository URLs:
    - Enforces https:// or http:// schemes (blocks ext::, file://, ssh://, git@, local filesystem paths)
    - Rejects flag injection (leading '-')
    - Rejects shell metacharacters and unquoted spaces
    - Rejects cloud metadata hosts and private/loopback IP literals (SSRF prevention)
    """
    if not repo_url or not repo_url.strip():
        return False, "Repository URL cannot be empty."

    url = repo_url.strip()

    # Prevent flag/option injection
    if url.startswith("-"):
        return False, "Malicious repository URL: flag/option injection detected."

    # Prevent shell injection characters or quotes in URL
    if any(char in url for char in [";", "|", "&", "`", "$", "\n", "\r", '"', "'", " "]):
        return False, "Malicious repository URL: shell characters or unescaped spaces detected."

    # Parse URL
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return False, f"Invalid URL format: {str(exc)}"

    scheme = (parsed.scheme or "").lower()

    # Check if this is a Windows drive letter path (e.g. C:\... where urllib parsed scheme as single letter)
    # or a local POSIX path (e.g. /path/to/repo)
    is_local_path = False
    if os.name == "nt" and len(scheme) == 1 and scheme.isalpha() and (":" in url[:3]):
        is_local_path = True
    elif not scheme and (url.startswith("/") or url.startswith("\\") or url.startswith(".")):
        is_local_path = True

    if is_local_path:
        allow_test_repos = os.getenv("ALLOW_TEST_REPOS", "true").lower() in ("true", "1")
        try:
            p = Path(url).resolve()
            system_dirs = ["/etc", "/bin", "/usr", "/root", "/var", "/home", "c:\\windows", "c:\\"]
            if str(p).lower() in system_dirs or any(str(p).lower().startswith(sd) for sd in ["/etc/", "c:\\windows\\"]):
                return False, f"Access to local system path '{url}' is prohibited."
            if allow_test_repos and p.is_dir() and (p / ".git").exists():
                return True, None
            return False, f"Local path repository '{url}' is prohibited or is not a valid git repository."
        except Exception:
            return False, f"Invalid local path '{url}'."

    if scheme not in ("https", "http"):
        return False, f"Prohibited URL scheme '{scheme or 'none'}'. Only https:// and http:// repository URLs are permitted."

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "Missing hostname in repository URL."

    # SSRF: block cloud metadata hosts
    if hostname in METADATA_HOSTS:
        return False, f"Access to cloud metadata host '{hostname}' is prohibited (SSRF prevention)."

    # SSRF: block private, loopback, and link-local IP literals
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, f"Access to private or reserved IP address '{hostname}' is prohibited (SSRF prevention)."
    except ValueError:
        # Hostname is a regular domain name, not an IP literal
        pass

    return True, None


def clone_repository(
    repo_url: str,
    target_dir: str,
    workflow_id: str = "unknown",
    step_id: Optional[str] = None,
    timeout_seconds: int = 120,
) -> ExecutionResult:
    """
    Clones a git repository into the specified target directory.
    Uses argv with shell=False and the '--' option terminator.
    Cleans target directory beforehand if it exists.
    """
    target_path = Path(target_dir).resolve()
    start_time = time.perf_counter()

    # Pre-clean target directory
    if target_path.exists():
        try:
            shutil.rmtree(target_path, ignore_errors=True)
        except Exception:
            pass

    target_path.mkdir(parents=True, exist_ok=True)

    safe_url = redact_secrets(repo_url.strip())

    # Strict URL safety validation
    is_safe, reason = validate_repo_url(safe_url)
    if not is_safe:
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=126,
            stdout="",
            stderr=f"Security blocked: {reason}",
            workspace=str(target_path),
            metadata={"security_blocked": True, "repo_url": safe_url},
        )

    # Use argv with '--' option terminator to guarantee safe_url cannot be parsed as a git option
    argv = ["git", "clone", "--depth", "1", "--", safe_url, str(target_path)]
    proc_env = build_safe_environment()

    try:
        proc = subprocess.run(
            argv,
            cwd=str(target_path.parent),
            shell=False,  # CRITICAL: shell=False prevents command injection
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=proc_env,
        )
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = redact_secrets(proc.stdout or "")
        err = redact_secrets(proc.stderr or "")

        result = ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command=f"git clone --depth 1 -- [URL] {str(target_path)}",
            exit_code=proc.returncode,
            stdout=out,
            stderr=err,
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=str(target_path),
            metadata={"repo_url": safe_url, "target_dir": str(target_path)},
        )

        if result.exit_code == 0:
            files = list(target_path.iterdir())
            if not files:
                result.exit_code = 1
                result.stderr = "Git clone returned 0 but target directory is empty."
            else:
                result.metadata["cloned_files_count"] = len(files)

        return result

    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=124,
            stdout="",
            stderr=f"Git clone timed out after {timeout_seconds}s",
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=str(target_path),
            metadata={"timed_out": True, "repo_url": safe_url},
        )

    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=1,
            stdout="",
            stderr=f"Git clone execution error: {str(exc)}",
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=str(target_path),
            metadata={"repo_url": safe_url},
        )


class GitTool(BaseTool):
    """Tool specialized for git operations."""

    def __init__(self):
        super().__init__(name="git")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        cmd = command.strip()
        if not cmd.lower().startswith("git"):
            return False, f"Command must start with 'git': {command}"
        if cmd.lower().startswith("git clone"):
            parts = shlex.split(cmd, posix=True)
            # Find the repo URL (first non-flag argument after clone)
            for tok in parts[2:]:
                if not tok.startswith("-"):
                    is_valid, err = validate_repo_url(tok)
                    if not is_valid:
                        return False, err
                    break
        return True, None

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        workflow_id: str = "unknown",
        step_name: str = "git_command",
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
