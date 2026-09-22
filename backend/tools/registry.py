import logging
from typing import Dict, List, Optional

from backend.models.workflow import ExecutionResult
from backend.tools.base_tool import BaseTool
from backend.tools.file_tool import FileTool
from backend.tools.git_tool import GitTool
from backend.tools.http_tool import HttpTool
from backend.tools.notebook_tool import NotebookTool
from backend.tools.npm_tool import NpmTool
from backend.tools.pip_tool import PipTool
from backend.tools.python_tool import PythonTool
from backend.tools.shell_tool import ShellTool

logger = logging.getLogger("agentguard.tools.registry")


class ToolRegistry:
    """
    Central registry mapping tool names to specialized BaseTool instances.
    Provides uniform dispatch, command validation, and discovery.
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._register_defaults()

    def _register_defaults(self):
        self.register("git", GitTool())
        self.register("shell", ShellTool())
        self.register("python", PythonTool())
        self.register("npm", NpmTool())
        self.register("pip", PipTool())
        self.register("http", HttpTool())
        self.register("file", FileTool())
        self.register("notebook", NotebookTool())

    def register(self, name: str, tool: BaseTool):
        self._tools[name.lower()] = tool
        logger.debug(f"Registered tool: {name.lower()}")

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name.lower())

    def has(self, name: str) -> bool:
        return name.lower() in self._tools

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def resolve_tool_for_command(self, command: str, default_tool: str = "shell") -> BaseTool:
        """Determines the most appropriate registered tool based on command prefix."""
        cmd = command.strip().lower()
        if cmd.startswith("git ") or cmd == "git":
            return self.get("git") or self._tools["shell"]
        elif cmd.startswith("pip ") or cmd.startswith("pip3 ") or " -m pip " in cmd:
            return self.get("pip") or self._tools["shell"]
        elif any(cmd.startswith(k) for k in ["npm ", "pnpm ", "yarn ", "node ", "npx "]):
            return self.get("npm") or self._tools["shell"]
        elif any(cmd.startswith(k) for k in ["python ", "python3 ", "pytest ", "py "]):
            return self.get("python") or self._tools["shell"]
        elif cmd.endswith(".ipynb") or any(cmd.startswith(k) for k in ["execute_notebook", "run_notebook"]):
            return self.get("notebook") or self._tools["shell"]
        elif cmd.startswith("http://") or cmd.startswith("https://") or cmd.startswith("get http"):
            return self.get("http") or self._tools["shell"]
        return self.get(default_tool) or self._tools["shell"]


# Global tool registry instance
tool_registry = ToolRegistry()
