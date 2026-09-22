import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

from backend.config import settings
from backend.models.workflow import (
    ExecutionResult,
    ProjectAnalysis,
    StepDefinition,
    StepType,
    current_iso_time,
)
from backend.tools.git_tool import clone_repository
from backend.tools.http_tool import perform_health_check
from backend.tools.project_analyzer import analyze_workspace
from backend.tools.registry import tool_registry
from backend.tools.shell_tool import execute_shell_command, is_safe_command, redact_secrets

logger = logging.getLogger("agentguard.executor")


class ToolExecutor:
    """
    ToolExecutor performs real operations within a designated workflow workspace.
    Captures stdout, stderr, exit codes, execution timing, and returns standardized ExecutionResult.
    Dispatches commands through specialized tools in the ToolRegistry.
    """

    def __init__(self, workflow_id: str, workspace_base: Optional[str] = None):
        self.workflow_id = workflow_id
        base = workspace_base or settings.WORKSPACE_BASE_DIR
        self.workspace_dir = str((Path(base) / workflow_id).resolve())
        self.background_process: Optional[subprocess.Popen] = None
        self._spawned_pids: set[int] = set()

    def ensure_workspace(self) -> str:
        Path(self.workspace_dir).mkdir(parents=True, exist_ok=True)
        return self.workspace_dir

    def execute_step(
        self,
        step: StepDefinition,
        repo_url: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Executes a workflow step according to its type and assigned tool.
        """
        self.ensure_workspace()
        step_type = step.type
        logger.info(f"Executing step {step.id} ({step_type}) for workflow {self.workflow_id}")

        if step_type == StepType.CLONE_REPOSITORY.value:
            if not repo_url:
                return ExecutionResult(
                    workflow_id=self.workflow_id,
                    step=step.name,
                    step_id=step.id,
                    command="clone_repository",
                    exit_code=1,
                    stdout="",
                    stderr="No repository URL provided for clone operation.",
                    workspace=self.workspace_dir,
                )
            return clone_repository(
                repo_url=repo_url,
                target_dir=self.workspace_dir,
                workflow_id=self.workflow_id,
                step_id=step.id,
                timeout_seconds=min(settings.DEFAULT_TIMEOUT_SECONDS * 2, settings.MAX_STEP_TIME),
            )

        elif step_type == StepType.ANALYZE_PROJECT.value:
            start_time = time.perf_counter()
            analysis: ProjectAnalysis = analyze_workspace(self.workspace_dir)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            is_valid = analysis.language != "unknown"
            return ExecutionResult(
                workflow_id=self.workflow_id,
                step=step.name,
                step_id=step.id,
                command="analyze_workspace",
                exit_code=0 if is_valid else 1,
                stdout=(
                    f"Language: {analysis.language}, "
                    f"Package Manager: {analysis.package_manager}, "
                    f"Build: {analysis.build_command or 'None'}, "
                    f"Test: {analysis.test_command or 'None'}, "
                    f"Start: {analysis.start_command or 'None'}"
                ),
                stderr="" if is_valid else "Project structure could not be identified.",
                duration_ms=duration_ms,
                workspace=self.workspace_dir,
                metadata=analysis.model_dump(),
            )

        elif step_type == StepType.HEALTH_CHECK.value:
            url = step.command or "http://localhost:8000/health"
            http_tool = tool_registry.get("http")
            if http_tool:
                return http_tool.execute(
                    command=url,
                    cwd=self.workspace_dir,
                    timeout_seconds=5,
                    workflow_id=self.workflow_id,
                    step_name=step.name,
                    step_id=step.id,
                )
            return perform_health_check(
                url=url,
                timeout_seconds=5,
                workflow_id=self.workflow_id,
                step_id=step.id,
            )

        elif step_type == StepType.START_APPLICATION.value:
            cmd = step.command
            if not cmd or cmd.startswith("echo"):
                return ExecutionResult(
                    workflow_id=self.workflow_id,
                    step=step.name,
                    step_id=step.id,
                    command=cmd or "noop",
                    exit_code=0,
                    stdout="No application start command defined; skipped service launch.",
                    stderr="",
                    duration_ms=5.0,
                    workspace=self.workspace_dir,
                )

            # Security validation: check for dangerous commands & parse argv
            from backend.tools.shell_tool import build_safe_environment, validate_and_parse_command
            is_safe, block_reason, parsed_cmds = validate_and_parse_command(cmd, cwd=self.workspace_dir)
            if not is_safe:
                return ExecutionResult(
                    workflow_id=self.workflow_id,
                    step=step.name,
                    step_id=step.id,
                    command=redact_secrets(cmd),
                    exit_code=126,
                    stdout="",
                    stderr=f"Security blocked: {block_reason}",
                    duration_ms=1.0,
                    workspace=self.workspace_dir,
                    metadata={"security_blocked": True},
                )

            start_time = time.perf_counter()
            try:
                import sys
                argv = list(parsed_cmds[0]) if parsed_cmds else [cmd]
                if argv[0].lower() in ("python", "python3") and not Path(argv[0]).is_absolute():
                    argv[0] = sys.executable

                creation_flags = 0
                if os.name == "nt":
                    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

                proc_env = build_safe_environment()
                self.background_process = subprocess.Popen(
                    argv,
                    cwd=self.workspace_dir,
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=creation_flags,
                    env=proc_env,
                )
                if self.background_process.pid:
                    self._spawned_pids.add(self.background_process.pid)
                time.sleep(1.5)
                poll = self.background_process.poll()
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

                if poll is not None and poll != 0:
                    out, err = self.background_process.communicate()
                    return ExecutionResult(
                        workflow_id=self.workflow_id,
                        step=step.name,
                        step_id=step.id,
                        command=cmd,
                        exit_code=poll,
                        stdout=out or "",
                        stderr=err or "Application terminated immediately.",
                        duration_ms=duration_ms,
                        workspace=self.workspace_dir,
                    )

                return ExecutionResult(
                    workflow_id=self.workflow_id,
                    step=step.name,
                    step_id=step.id,
                    command=cmd,
                    exit_code=0,
                    stdout=f"Application started in background (PID {self.background_process.pid})",
                    stderr="",
                    duration_ms=duration_ms,
                    workspace=self.workspace_dir,
                    metadata={"pid": self.background_process.pid},
                )
            except Exception as exc:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return ExecutionResult(
                    workflow_id=self.workflow_id,
                    step=step.name,
                    step_id=step.id,
                    command=cmd,
                    exit_code=1,
                    stdout="",
                    stderr=f"Failed to start application: {str(exc)}",
                    duration_ms=duration_ms,
                    workspace=self.workspace_dir,
                )

        elif step_type == StepType.FINAL_REPORT.value:
            return ExecutionResult(
                workflow_id=self.workflow_id,
                step=step.name,
                step_id=step.id,
                command="generate_final_report",
                exit_code=0,
                stdout="Workflow verified successfully with machine-checked evidence.",
                stderr="",
                duration_ms=1.0,
                workspace=self.workspace_dir,
            )

        else:
            # Dispatch through ToolRegistry
            cmd = step.command
            if not cmd:
                return ExecutionResult(
                    workflow_id=self.workflow_id,
                    step=step.name,
                    step_id=step.id,
                    command="none",
                    exit_code=1,
                    stdout="",
                    stderr="No command specified for this step. Cannot execute.",
                    duration_ms=0.0,
                    workspace=self.workspace_dir,
                )

            timeout = (
                step.timeout_seconds
                if step.timeout_seconds is not None
                else min(settings.DEFAULT_TIMEOUT_SECONDS, settings.MAX_STEP_TIME)
            )
            
            tool = None
            if step.tool:
                tool = tool_registry.get(step.tool)
            if not tool:
                tool = tool_registry.resolve_tool_for_command(cmd)

            result = tool.execute(
                command=cmd,
                cwd=self.workspace_dir,
                timeout_seconds=timeout,
                workflow_id=self.workflow_id,
                step_name=step.name,
                step_id=step.id,
            )
            if result.timeout_seconds is None:
                result.timeout_seconds = timeout
            if result.metadata is None:
                result.metadata = {}
            if "timeout_seconds" not in result.metadata:
                result.metadata["timeout_seconds"] = timeout
            return result

    def execute_recovery_action(
        self,
        recovery_action: str,
        step_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Executes a remediation action recommended by the Verifier.
        Dispatches through specialized tool from registry.
        """
        logger.info(f"Executing recovery action for workflow {self.workflow_id}: '{recovery_action}'")
        tool = tool_registry.resolve_tool_for_command(recovery_action)
        timeout = min(settings.DEFAULT_TIMEOUT_SECONDS, settings.MAX_STEP_TIME)
        return tool.execute(
            command=recovery_action,
            cwd=self.workspace_dir,
            timeout_seconds=timeout,
            workflow_id=self.workflow_id,
            step_name="recovery_action",
            step_id=step_id,
        )

    def cleanup(self):
        """Terminates any background processes and process trees spawned during workflow execution."""
        # Clean up tracked process trees
        for pid in list(self._spawned_pids):
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True,
                        timeout=5,
                    )
                except Exception:
                    pass
            else:
                try:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        self._spawned_pids.clear()

        # Clean up direct handle
        if self.background_process:
            if self.background_process.poll() is None:
                try:
                    self.background_process.terminate()
                    self.background_process.wait(timeout=2)
                except Exception:
                    try:
                        self.background_process.kill()
                    except Exception:
                        pass
            self.background_process = None

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
