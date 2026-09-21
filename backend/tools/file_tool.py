import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from backend.models.workflow import ExecutionResult, current_iso_time
from backend.tools.base_tool import BaseTool
from backend.tools.shell_tool import check_path_containment


class FileTool(BaseTool):
    """
    Tool specialized for safe file inspection and workspace containment verification.
    """

    def __init__(self):
        super().__init__(name="file")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        parts = command.strip().split(" ", 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        if arg and cwd:
            contained, err = check_path_containment(arg, cwd)
            if not contained:
                return False, err
        elif ".." in command:
            return False, "Path traversal forbidden in file operations"
        return True, None

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 10,
        workflow_id: str = "unknown",
        step_name: str = "file_operation",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        workspace = Path(cwd or ".").resolve()
        parts = command.strip().split(" ", 1)
        op = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        contained, err = check_path_containment(arg, workspace) if arg else (True, None)
        if not contained:
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=command,
                exit_code=1,
                stdout="",
                stderr=f"Path traversal violation: Target '{arg}' outside workspace.",
                duration_ms=1.0,
                workspace=str(workspace),
            )
        target = (workspace / arg).resolve() if not Path(arg).is_absolute() else Path(arg).resolve()

        if op in ["exists", "check"]:
            exists = target.exists()
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=command,
                exit_code=0 if exists else 1,
                stdout=f"File exists: {exists}",
                stderr="" if exists else f"File not found: {target}",
                duration_ms=1.0,
                workspace=str(workspace),
            )
        elif op in ["cat", "read"]:
            if not target.exists():
                return ExecutionResult(
                    workflow_id=workflow_id,
                    step=step_name,
                    step_id=step_id,
                    command=command,
                    exit_code=1,
                    stdout="",
                    stderr=f"File not found: {target}",
                    duration_ms=1.0,
                    workspace=str(workspace),
                )
            content = target.read_text(encoding="utf-8", errors="replace")[:10000]
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=command,
                exit_code=0,
                stdout=content,
                stderr="",
                duration_ms=1.0,
                workspace=str(workspace),
            )
        else:
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=command,
                exit_code=1,
                stdout="",
                stderr=f"Unsupported file operation: {op}",
                duration_ms=1.0,
                workspace=str(workspace),
            )
