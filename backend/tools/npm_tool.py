from typing import Dict, Optional, Tuple

from backend.models.workflow import ExecutionResult
from backend.tools.base_tool import BaseTool
from backend.tools.shell_tool import execute_shell_command, is_safe_command


class NpmTool(BaseTool):
    """
    Tool specialized for Node.js package managers (npm, pnpm, yarn, npx, node).
    """

    def __init__(self):
        super().__init__(name="npm")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        safe, reason = is_safe_command(command, cwd=cwd)
        if not safe:
            return False, reason
        cmd_stripped = command.strip().lower()
        if not any(cmd_stripped.startswith(k) for k in ["npm", "pnpm", "yarn", "node", "npx"]):
            return False, f"Command is not an npm/node command: {command}"
        return True, None

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        workflow_id: str = "unknown",
        step_name: str = "npm_command",
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
