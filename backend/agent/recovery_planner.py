import importlib.util
import logging
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from typing import Optional, Tuple

from backend.config import settings
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    RecoveryOutcome,
    RecoveryPlan,
    StepDefinition,
)
from backend.tools.port_utils import (
    find_free_port,
    find_process_holding_port,
    is_port_in_use,
    rewrite_port_in_command,
)
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
        execution_result: Optional[ExecutionResult] = None,
        step: Optional[StepDefinition] = None,
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
                return not is_port_in_use(port)
            except (OSError, ValueError, TypeError):
                return False

        elif post_type == "file_exists":
            if not target:
                return False
            target_path = Path(target)
            if not target_path.is_absolute() and workspace_dir:
                target_path = Path(workspace_dir) / target_path
            return target_path.exists()

        elif post_type == "timeout_extended":
            target_timeout = None
            if target:
                try:
                    target_timeout = int(target)
                except ValueError:
                    pass
            if target_timeout is None and plan.timeout_override is not None:
                target_timeout = plan.timeout_override
            if target_timeout is None:
                return False

            if execution_result is not None:
                if execution_result.timeout_seconds is not None:
                    return execution_result.timeout_seconds >= target_timeout
                return False

            if step is not None:
                if getattr(step, "timeout_seconds", None) is not None:
                    return step.timeout_seconds >= target_timeout
                return False

            return False

        elif post_type == "port_allocated_and_rewritten":
            if not target:
                return False
            try:
                port = int(target)
            except (ValueError, TypeError):
                return False

            # 1. Verify step command was rewritten to use the new port
            if step is not None and step.command:
                if str(port) not in step.command:
                    return False

            # 2. Verify rewritten_command in plan contains the new port
            if plan.rewritten_command and str(port) not in plan.rewritten_command:
                return False

            # 3. Verify the newly allocated port is currently free to bind
            return not is_port_in_use(port)

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
            current_timeout = exec_result.timeout_seconds or settings.DEFAULT_TIMEOUT_SECONDS
            if current_timeout >= settings.MAX_STEP_TIME:
                return RecoveryPlan(
                    reason=f"Timeout budget exhausted ({current_timeout}s >= MAX_STEP_TIME {settings.MAX_STEP_TIME}s)",
                    failure_type=failure_type,
                    action_type="unrecoverable",
                    tool="none",
                    command=None,
                    target_step=target_step,
                    max_attempts=0,
                    expected_outcome=RecoveryOutcome.UNRECOVERABLE,
                )

            new_timeout = min(max(current_timeout * 2, current_timeout + 5), settings.MAX_STEP_TIME)
            cmd = suggested_action or f'"{sys.executable}" -c "print(\'Timeout recovery: extended duration allocated for retry\')"'
            return RecoveryPlan(
                reason=classification.reason,
                failure_type=failure_type,
                action_type="extend_timeout",
                tool="python" if not suggested_action else "shell",
                command=cmd,
                target_step=target_step,
                max_attempts=max_attempts,
                timeout_override=new_timeout,
                postcondition_type="timeout_extended",
                postcondition_target=str(new_timeout),
                expected_outcome=RecoveryOutcome.SUCCESS,
            )

        # 3. Port Error
        elif classification.failure_type == FailureType.PORT_ERROR:
            try:
                port = int(details.get("port", "8000"))
            except (ValueError, TypeError):
                port = 8000

            # Determine holding PID and workflow-owned PIDs
            holding_pid = details.get("holding_pid") or find_process_holding_port(port)
            owned_pids = set(details.get("spawned_pids") or [])
            if hasattr(exec_result, "metadata") and exec_result.metadata:
                if "spawned_pids" in exec_result.metadata:
                    owned_pids.update(exec_result.metadata["spawned_pids"])
                if "pid" in exec_result.metadata and exec_result.metadata["pid"]:
                    try:
                        owned_pids.add(int(exec_result.metadata["pid"]))
                    except (ValueError, TypeError):
                        pass

            # Case A: Holding process is known and belongs to this workflow -> terminate ONLY this zombie process
            if holding_pid and holding_pid in owned_pids:
                if os.name == "nt":
                    cmd = f"taskkill /F /PID {holding_pid}"
                else:
                    cmd = f"kill -9 {holding_pid}"
                return RecoveryPlan(
                    reason=f"Port {port} is occupied by workflow zombie process (PID {holding_pid}). Safely terminating owned process.",
                    failure_type=failure_type,
                    action_type="release_port",
                    tool="shell",
                    command=cmd,
                    target_step=target_step,
                    max_attempts=max_attempts,
                    postcondition_type="port_free",
                    postcondition_target=str(port),
                    expected_outcome=RecoveryOutcome.SUCCESS,
                )

            # Case B: Foreign process or unknown -> NEVER kill foreign process! Relocate port!
            new_port = find_free_port(start_port=port)
            original_cmd = exec_result.command or ""
            rewritten_cmd = rewrite_port_in_command(original_cmd, port, new_port)

            return RecoveryPlan(
                reason=(
                    f"Port {port} is occupied by an external/foreign process"
                    + (f" (PID {holding_pid})" if holding_pid else "")
                    + f". Relocating step to available port {new_port} without terminating foreign processes."
                ),
                failure_type=failure_type,
                action_type="relocate_port",
                tool="shell",
                command=f'"{sys.executable}" -c "print(\'Port relocated to {new_port}\')"',
                target_step=target_step,
                max_attempts=max_attempts,
                new_port=new_port,
                rewritten_command=rewritten_cmd,
                postcondition_type="port_allocated_and_rewritten",
                postcondition_target=str(new_port),
                expected_outcome=RecoveryOutcome.SUCCESS,
            )

        # 4. Resource Limit
        elif classification.failure_type == FailureType.RESOURCE_LIMIT:
            reason = (
                f"{classification.reason}: No verifiable remediation available for this resource class; escalating as unrecoverable"
                if classification.reason
                else "No verifiable remediation available for this resource class; escalating as unrecoverable"
            )
            return RecoveryPlan(
                reason=reason,
                failure_type=failure_type,
                action_type="unrecoverable",
                tool="none",
                command=None,
                target_step=target_step,
                max_attempts=0,
                expected_outcome=RecoveryOutcome.UNRECOVERABLE,
            )

        # 5. Fallback recovery
        tool = "shell"
        if suggested_action:
            resolved_tool = tool_registry.resolve_tool_for_command(suggested_action)
            tool = resolved_tool.name

        action_type = "custom_remediation" if suggested_action else "unrecoverable"
        expected_outcome = RecoveryOutcome.SUCCESS if suggested_action else RecoveryOutcome.UNRECOVERABLE

        return RecoveryPlan(
            reason=classification.reason,
            failure_type=failure_type,
            action_type=action_type,
            tool=tool,
            command=suggested_action or None,
            target_step=target_step,
            max_attempts=max_attempts if suggested_action else 0,
            postcondition_type="custom" if suggested_action else None,
            postcondition_target=target_step if suggested_action else None,
            expected_outcome=expected_outcome,
        )


recovery_planner = RecoveryPlanner()
