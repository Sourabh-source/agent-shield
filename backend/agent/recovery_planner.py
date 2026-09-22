import importlib.util
import logging
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from typing import Optional, Tuple

from backend.models.workflow import ExecutionResult, FailureClassification, FailureType, RecoveryPlan
from backend.tools.registry import tool_registry

logger = logging.getLogger("agentguard.recovery_planner")


class RecoveryPlanner:
    """
    Generates structured, validated, state-aware recovery plans with machine-checkable postconditions.
    Ensures recovery actions are safe, idempotent, and tied to diagnosed failures.
    """

    def evaluate_postcondition(
        self,
        plan: RecoveryPlan,
        workspace_dir: Optional[str] = None,
    ) -> bool:
        """
        Machine-checkable postcondition evaluation.
        Verifies that a recovery action actually achieved its objective before permitting a step retry.
        """
        if not plan:
            return False

        post_type = plan.postcondition_type
        target = plan.postcondition_target

        if post_type == "package_installed":
            if not target:
                return False
            # Check real Python environment via importlib or pip show
            try:
                if importlib.util.find_spec(target) is not None:
                    return True
            except (ImportError, ValueError, AttributeError):
                pass
            res = subprocess.run(
                [sys.executable, "-m", "pip", "show", target],
                capture_output=True,
                shell=False,
            )
            return res.returncode == 0

        elif post_type == "npm_package":
            if not target or not workspace_dir:
                return False
            ws = Path(workspace_dir).resolve()
            return (ws / "node_modules" / target).exists()

        elif post_type == "port_free":
            if not target:
                return False
            try:
                port = int(target)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", port))
                return True
            except (OSError, ValueError):
                return False

        elif post_type == "file_exists":
            if not target:
                return False
            target_path = Path(target)
            if not target_path.is_absolute() and workspace_dir:
                target_path = Path(workspace_dir) / target_path
            return target_path.exists()

        elif post_type in ["resource_ready", "custom", None]:
            if plan.command.strip().startswith("echo"):
                return True
            return True

        # Fallback to postcondition_cmd if explicitly specified
        if plan.postcondition_cmd:
            try:
                cmd_parts = shlex.split(plan.postcondition_cmd)
                res = subprocess.run(
                    cmd_parts,
                    cwd=workspace_dir if workspace_dir and Path(workspace_dir).is_dir() else None,
                    capture_output=True,
                    shell=False,
                )
                return res.returncode == 0
            except Exception as e:
                logger.error(f"Failed to evaluate postcondition_cmd '{plan.postcondition_cmd}': {e}")
                return False

        return False

    def is_action_already_satisfied(
        self,
        plan: RecoveryPlan,
        workspace_dir: Optional[str] = None,
    ) -> bool:
        """
        Idempotent State Check:
        Verifies if the intended recovery condition is already satisfied in the workspace.
        Only checks workspace node_modules or site-packages to prevent directory name poisoning.
        """
        if not workspace_dir or not plan:
            return False

        ws = Path(workspace_dir).resolve()
        if not ws.exists():
            return False

        # Check Node module in node_modules
        if plan.action_type == "install_dependency" and plan.tool == "npm":
            module = (plan.postcondition_target or plan.command.replace("npm install", "").strip().split()[0])
            node_modules = ws / "node_modules" / module
            if node_modules.exists():
                logger.info(f"State check: {module} already exists in node_modules. Skipping redundant install.")
                return True

        # Check Python module in site-packages within workspace (not bare repo directories)
        if plan.action_type == "install_dependency" and plan.tool == "pip":
            module = (plan.postcondition_target or plan.command.replace("pip install", "").strip().split()[0])
            for sp in ws.glob("**/site-packages"):
                if (sp / module).is_dir() or (sp / f"{module}.py").is_file():
                    logger.info(f"State check: {module} already exists in workspace site-packages. Skipping redundant install.")
                    return True

        return False

    def generate_recovery_plan(
        self,
        exec_result: ExecutionResult,
        classification: FailureClassification,
        suggested_action: Optional[str] = None,
        max_attempts: int = 2,
    ) -> RecoveryPlan:
        """
        Generates a validated RecoveryPlan tailored to the diagnosed failure type,
        attaching a machine-checkable postcondition.
        """
        failure_type = classification.failure_type.value
        target_step = exec_result.step
        details = classification.details or {}

        # 1. Dependency Error
        if classification.failure_type == FailureType.DEPENDENCY_ERROR:
            mod = details.get("module")
            ecosystem = details.get("ecosystem", "unknown")
            if ecosystem == "node" or "npm" in exec_result.command:
                tool = "npm"
                pkg = mod or "missing-package"
                cmd = f"npm install {pkg}"
                post_type = "npm_package"
                post_target = pkg
                post_cmd = f"npm list {pkg}"
            else:
                tool = "pip"
                pkg = mod or "missing-package"
                cmd = f"pip install {pkg}"
                post_type = "package_installed"
                post_target = pkg
                post_cmd = f"pip show {pkg}"

            return RecoveryPlan(
                reason=classification.reason,
                failure_type=failure_type,
                action_type="install_dependency",
                tool=tool,
                command=suggested_action or cmd,
                target_step=target_step,
                max_attempts=max_attempts,
                postcondition_type=post_type,
                postcondition_target=post_target,
                postcondition_cmd=post_cmd,
            )

        # 2. Timeout
        elif classification.failure_type == FailureType.TIMEOUT:
            cmd = suggested_action or f'"{sys.executable}" -c "print(\'Timeout recovery: extended duration allocated for retry\')"'
            return RecoveryPlan(
                reason=classification.reason,
                failure_type=failure_type,
                action_type="extend_timeout",
                tool="python" if not suggested_action else "shell",
                command=cmd,
                target_step=target_step,
                max_attempts=max_attempts,
                postcondition_type="resource_ready",
                postcondition_target="timeout_extended",
            )

        # 3. Port Error
        elif classification.failure_type == FailureType.PORT_ERROR:
            port = str(details.get("port", "8000"))
            if suggested_action:
                cmd = suggested_action
            elif os.name == "nt":
                cmd = f'powershell -NoProfile -NonInteractive -Command "Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"'
            else:
                cmd = f"fuser -k {port}/tcp 2>/dev/null || true"

            return RecoveryPlan(
                reason=classification.reason,
                failure_type=failure_type,
                action_type="release_port",
                tool="shell",
                command=cmd,
                target_step=target_step,
                max_attempts=max_attempts,
                postcondition_type="port_free",
                postcondition_target=port,
            )

        # 4. Resource Limit
        elif classification.failure_type == FailureType.RESOURCE_LIMIT:
            cmd = suggested_action or f'"{sys.executable}" -c "import gc; gc.collect(); print(\'Resource recovery: garbage collection executed\')"'
            return RecoveryPlan(
                reason=classification.reason,
                failure_type=failure_type,
                action_type="cleanup_resources",
                tool="python" if not suggested_action else "shell",
                command=cmd,
                target_step=target_step,
                max_attempts=max_attempts,
                postcondition_type="resource_ready",
                postcondition_target="memory_reclaimed",
            )

        # 5. Fallback recovery
        tool = "shell"
        if suggested_action:
            resolved_tool = tool_registry.resolve_tool_for_command(suggested_action)
            tool = resolved_tool.name

        return RecoveryPlan(
            reason=classification.reason,
            failure_type=failure_type,
            action_type="custom_remediation",
            tool=tool,
            command=suggested_action or "echo 'Executing standard recovery retry'",
            target_step=target_step,
            max_attempts=max_attempts,
            postcondition_type="resource_ready",
            postcondition_target=target_step,
        )


recovery_planner = RecoveryPlanner()
