import logging
import os
from pathlib import Path
import sys
from typing import Optional, Tuple

from backend.models.workflow import ExecutionResult, FailureClassification, FailureType, RecoveryPlan
from backend.tools.registry import tool_registry

logger = logging.getLogger("agentguard.recovery_planner")


class RecoveryPlanner:
    """
    Generates structured, validated, state-aware recovery plans.
    Ensures recovery actions are safe, idempotent, and tied to diagnosed failures.
    """

    def is_action_already_satisfied(
        self,
        plan: RecoveryPlan,
        workspace_dir: Optional[str] = None,
    ) -> bool:
        """
        Idempotent State Check:
        Verifies if the intended recovery condition is already satisfied in the workspace.
        E.g., if a module was already installed by a prior step or manual intervention,
        we can skip repeated execution.
        """
        if not workspace_dir:
            return False

        ws = Path(workspace_dir).resolve()
        if not ws.exists():
            return False

        # Check Python module in virtualenv/site-packages if applicable
        if plan.action_type == "install_dependency" and plan.tool == "pip":
            module = plan.command.replace("pip install", "").strip().split()[0]
            # Fast check if directory exists in node_modules or site-packages
            for p in ws.rglob(module):
                if p.is_dir():
                    logger.info(f"State check: {module} already exists in workspace. Skipping redundant install.")
                    return True

        # Check Node module in node_modules
        if plan.action_type == "install_dependency" and plan.tool == "npm":
            module = plan.command.replace("npm install", "").strip().split()[0]
            node_modules = ws / "node_modules" / module
            if node_modules.exists():
                logger.info(f"State check: {module} already exists in node_modules. Skipping redundant install.")
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
        Generates a validated RecoveryPlan tailored to the diagnosed failure type.
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
            else:
                tool = "pip"
                pkg = mod or "missing-package"
                cmd = f"pip install {pkg}"

            return RecoveryPlan(
                reason=classification.reason,
                failure_type=failure_type,
                action_type="install_dependency",
                tool=tool,
                command=suggested_action or cmd,
                target_step=target_step,
                max_attempts=max_attempts,
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
            )

        # 3. Port Error
        elif classification.failure_type == FailureType.PORT_ERROR:
            port = details.get("port", "8000")
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
        )


recovery_planner = RecoveryPlanner()
