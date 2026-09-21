import sys
from typing import Dict, Optional, Tuple

from backend.models.workflow import ExecutionResult
from backend.tools.base_tool import BaseTool
from backend.tools.shell_tool import execute_shell_command, is_safe_command


class PipTool(BaseTool):
    """
    Tool specialized for package installations, updates, and environment queries.
    """

    def __init__(self):
        super().__init__(name="pip")
        self.python_executable = sys.executable

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        safe, reason = is_safe_command(command, cwd=cwd)
        if not safe:
            return False, reason
        cmd_stripped = command.strip().lower()
        if not (cmd_stripped.startswith("pip") or "python -m pip" in cmd_stripped or "python3 -m pip" in cmd_stripped):
            return False, f"Command is not a pip command: {command}"
        return True, None

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        workflow_id: str = "unknown",
        step_name: str = "pip_command",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        # Standardize pip command through python -m pip
        cmd_stripped = command.strip()
        if cmd_stripped.startswith("pip "):
            cmd = f'"{self.python_executable}" -m pip ' + cmd_stripped[4:]
        elif cmd_stripped.startswith("pip3 "):
            cmd = f'"{self.python_executable}" -m pip ' + cmd_stripped[5:]
        else:
            cmd = command

        return execute_shell_command(
            command=cmd,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
            workflow_id=workflow_id,
            step_name=step_name,
            step_id=step_id,
        )
