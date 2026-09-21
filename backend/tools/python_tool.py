import sys
from typing import Dict, Optional, Tuple

from backend.models.workflow import ExecutionResult
from backend.tools.base_tool import BaseTool
from backend.tools.shell_tool import execute_shell_command, is_safe_command


class PythonTool(BaseTool):
    """
    Tool specialized for executing Python scripts, modules, and test runners (pytest, unittest).
    """

    def __init__(self):
        super().__init__(name="python")
        self.python_executable = sys.executable

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        safe, reason = is_safe_command(command, cwd=cwd)
        if not safe:
            return False, reason
        # Ensure command invokes python or pytest or unittest
        cmd_stripped = command.strip().lower()
        if not any(cmd_stripped.startswith(k) for k in ["python", "pytest", "python3", "py "]):
            return False, f"Command does not appear to be a Python command: {command}"
        return True, None

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        workflow_id: str = "unknown",
        step_name: str = "python_command",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        # Standardize python invocation to current runtime executable if starting with python/python3
        parts = command.strip().split(" ", 1)
        if parts[0] in ["python", "python3"]:
            adjusted_cmd = f'"{self.python_executable}" ' + (parts[1] if len(parts) > 1 else "")
        else:
            adjusted_cmd = command

        return execute_shell_command(
            command=adjusted_cmd,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
            workflow_id=workflow_id,
            step_name=step_name,
            step_id=step_id,
        )
