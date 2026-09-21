import abc
from typing import Dict, Optional, Tuple
from backend.models.workflow import ExecutionResult


class BaseTool(abc.ABC):
    """
    Abstract base class for all AgentGuard tools.
    Encapsulates command safety validation, environment control, and execution.
    """

    def __init__(self, name: str):
        self.name = name

    @abc.abstractmethod
    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """Validate if command is safe, well-formed, and executable by this tool."""
        pass

    @abc.abstractmethod
    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        workflow_id: str = "unknown",
        step_name: str = "tool_command",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """Executes the tool operation and returns standardized ExecutionResult."""
        pass
