"""
backend/sandbox/docker_sandbox.py
Isolated execution abstraction for untrusted code execution.
Implements zero-network, read-only, capability-dropped, resource-constrained container sandbox.
Fail-closed: Returns SANDBOX_UNAVAILABLE if container daemon is not present (never silent host execution).
"""
import abc
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

from backend.models.workflow import ExecutionResult, current_iso_time
from backend.tools.shell_tool import redact_secrets

logger = logging.getLogger("agentguard.sandbox")

DEFAULT_SANDBOX_IMAGE = "python:3.10-slim"
DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_CPU_LIMIT = "1.0"
DEFAULT_PIDS_LIMIT = 64


class Sandbox(abc.ABC):
    """Abstract interface for hardened sandboxed execution."""

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Returns True if the sandboxing environment (e.g. Docker daemon) is accessible."""
        pass

    @abc.abstractmethod
    def execute(
        self,
        command: Union[str, List[str]],
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        env: Optional[Dict[str, str]] = None,
        workflow_id: str = "unknown",
        step_name: str = "sandboxed_step",
        step_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute command inside the hardened sandbox."""
        pass


class DockerSandbox(Sandbox):
    """
    Docker container sandbox with strict isolation parameters:
    - --network=none: Network interface disabled (prevents SSRF, exfiltration, command-and-control)
    - --read-only: Root filesystem is read-only (prevents persistent modifications)
    - --cap-drop=ALL: Drops all Linux capabilities (prevents privilege escalation)
    - --memory, --cpus, --pids-limit: Hard resource limits (prevents fork bombs and DoS)
    - Fail-closed: returns status SANDBOX_UNAVAILABLE if Docker is missing.
    """

    def __init__(
        self,
        image: str = DEFAULT_SANDBOX_IMAGE,
        memory_limit: str = DEFAULT_MEMORY_LIMIT,
        cpu_limit: str = DEFAULT_CPU_LIMIT,
        pids_limit: int = DEFAULT_PIDS_LIMIT,
        network_mode: str = "none",
        read_only_root: bool = True,
        cap_drop: Optional[List[str]] = None,
    ):
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.network_mode = network_mode
        self.read_only_root = read_only_root
        self.cap_drop = cap_drop or ["ALL"]

    def is_available(self) -> bool:
        """Check if Docker CLI exists and Docker daemon is responding."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False
        try:
            res = subprocess.run(
                [docker_bin, "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    def build_docker_command(
        self,
        cmd_args: List[str],
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        """Construct the argv array for hardened container execution."""
        docker_argv = [
            "docker", "run", "--rm",
            f"--network={self.network_mode}",
            f"--memory={self.memory_limit}",
            f"--cpus={self.cpu_limit}",
            f"--pids-limit={self.pids_limit}",
        ]

        if self.read_only_root:
            docker_argv.append("--read-only")

        for cap in self.cap_drop:
            docker_argv.append(f"--cap-drop={cap}")

        # Writable, non-executable tmpfs for temp files
        docker_argv.extend(["--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])

        # Mount workspace volume if cwd is supplied
        if cwd:
            resolved_cwd = Path(cwd).resolve()
            docker_argv.extend(["-v", f"{resolved_cwd}:/workspace:rw"])
            docker_argv.extend(["-w", "/workspace"])

        # Inject safe environment variables
        if env:
            for k, v in env.items():
                docker_argv.extend(["-e", f"{k}={v}"])

        docker_argv.append(self.image)
        docker_argv.extend(cmd_args)
        return docker_argv

    def execute(
        self,
        command: Union[str, List[str]],
        cwd: Optional[str] = None,
        timeout_seconds: int = 60,
        env: Optional[Dict[str, str]] = None,
        workflow_id: str = "unknown",
        step_name: str = "sandboxed_step",
        step_id: Optional[str] = None,
    ) -> ExecutionResult:
        cmd_str = command if isinstance(command, str) else " ".join(command)
        cmd_tokens = [command] if isinstance(command, str) else command
        if isinstance(command, str):
            import shlex
            cmd_tokens = shlex.split(command, posix=(os.name != "nt"))

        # Fail-closed check: No silent host execution if Docker is unavailable!
        if not self.is_available():
            logger.warning(f"Docker sandbox unavailable for workflow {workflow_id}; returning SANDBOX_UNAVAILABLE")
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=redact_secrets(cmd_str),
                exit_code=126,
                stdout="",
                stderr="SANDBOX_UNAVAILABLE: Container daemon is not running or accessible in execution environment. Host execution is strictly blocked.",
                duration_ms=0.0,
                timestamp=current_iso_time(),
                workspace=cwd,
                metadata={
                    "status": "SANDBOX_UNAVAILABLE",
                    "sandbox": "docker",
                    "sandbox_unavailable": True,
                    "fail_closed": True,
                },
            )

        docker_argv = self.build_docker_command(cmd_tokens, cwd=cwd, env=env)
        start_time = time.perf_counter()

        try:
            proc = subprocess.run(
                docker_argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=redact_secrets(cmd_str),
                exit_code=proc.returncode,
                stdout=redact_secrets(proc.stdout or ""),
                stderr=redact_secrets(proc.stderr or ""),
                duration_ms=duration_ms,
                timestamp=current_iso_time(),
                workspace=cwd,
                metadata={
                    "status": "COMPLETED",
                    "sandbox": "docker",
                    "sandbox_argv": docker_argv[:6],
                },
            )
        except subprocess.TimeoutExpired:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=redact_secrets(cmd_str),
                exit_code=124,
                stdout="",
                stderr=f"Sandboxed command timed out after {timeout_seconds}s",
                duration_ms=duration_ms,
                timestamp=current_iso_time(),
                workspace=cwd,
                metadata={"status": "TIMEOUT", "sandbox": "docker"},
            )
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=redact_secrets(cmd_str),
                exit_code=1,
                stdout="",
                stderr=f"Sandbox execution error: {str(e)}",
                duration_ms=duration_ms,
                timestamp=current_iso_time(),
                workspace=cwd,
                metadata={"status": "ERROR", "sandbox": "docker"},
            )
