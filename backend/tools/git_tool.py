import os
import shutil
import time
from pathlib import Path
from typing import Optional

from backend.models.workflow import ExecutionResult, current_iso_time
from backend.tools.shell_tool import execute_shell_command, redact_secrets


def clone_repository(
    repo_url: str,
    target_dir: str,
    workflow_id: str = "unknown",
    step_id: Optional[str] = None,
    timeout_seconds: int = 120,
) -> ExecutionResult:
    """
    Clones a git repository into the specified target directory.
    Uses --depth 1 for speed and lightweight operation.
    Cleans target directory beforehand if it exists.
    """
    target_path = Path(target_dir).resolve()
    start_time = time.perf_counter()

    # If target already exists and is non-empty, remove it
    if target_path.exists():
        try:
            shutil.rmtree(target_path, ignore_errors=True)
        except Exception as e:
            pass

    target_path.mkdir(parents=True, exist_ok=True)

    # Sanitize repo_url
    safe_url = redact_secrets(repo_url.strip())

    # Security check: Prevent argument injection (flags starting with -) and command chaining
    if safe_url.startswith("-") or any(char in safe_url for char in [";", "|", "&", "`", "$", "\n", "\r"]):
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=126,
            stdout="",
            stderr="Security blocked: Malicious repository URL argument injection detected.",
            workspace=str(target_path),
            metadata={"security_blocked": True},
        )

    # Build clone command
    cmd = f'git clone --depth 1 "{safe_url}" "{str(target_path)}"'

    result = execute_shell_command(
        command=cmd,
        cwd=str(target_path.parent),
        timeout_seconds=timeout_seconds,
        workflow_id=workflow_id,
        step_name="clone_repository",
        step_id=step_id,
    )

    # Attach clone metadata
    if result.metadata is None:
        result.metadata = {}
    result.metadata["repo_url"] = safe_url
    result.metadata["target_dir"] = str(target_path)

    # Verify if clone succeeded by checking target directory contents
    if result.exit_code == 0:
        files = list(target_path.iterdir())
        if not files:
            result.exit_code = 1
            result.stderr = "Git clone returned 0 but target directory is empty."
        else:
            result.metadata["cloned_files_count"] = len(files)

    return result


from backend.tools.base_tool import BaseTool
from typing import Dict, Tuple


class GitTool(BaseTool):
    """Tool specialized for git operations."""

    def __init__(self):
        super().__init__(name="git")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        if not command.strip().lower().startswith("git"):
            return False, f"Command must start with 'git': {command}"
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
