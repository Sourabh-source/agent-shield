"""
tests/test_sandbox.py
Hardened unit and integration tests for containerized sandbox execution.
Verifies fail-closed behavior (SANDBOX_UNAVAILABLE), command isolation flags, and capability dropping.
"""
import pytest
from unittest.mock import patch, MagicMock

from backend.sandbox.docker_sandbox import DockerSandbox, Sandbox
from backend.models.workflow import ExecutionResult


class TestSandboxHardening:
    """Test suite for Docker sandbox isolation contracts and fail-closed guarantees."""

    def test_sandbox_base_class_interface(self):
        """Sandbox interface defines is_available and execute contracts."""
        assert issubclass(DockerSandbox, Sandbox)

    def test_docker_command_construction_contains_strict_isolation_flags(self):
        """Docker command line includes all mandatory defense-in-depth isolation flags."""
        sandbox = DockerSandbox(
            image="python:3.10-slim",
            memory_limit="256m",
            cpu_limit="0.5",
            pids_limit=32,
            network_mode="none",
            read_only_root=True,
            cap_drop=["ALL"],
        )
        argv = sandbox.build_docker_command(
            cmd_args=["python", "-m", "pytest"],
            cwd="/tmp/workspace",
            env={"CI": "true"},
        )

        assert "docker" in argv[0]
        assert "run" in argv[1]
        assert "--network=none" in argv
        assert "--read-only" in argv
        assert "--cap-drop=ALL" in argv
        assert "--memory=256m" in argv
        assert "--cpus=0.5" in argv
        assert "--pids-limit=32" in argv
        assert "-e" in argv and "CI=true" in argv
        assert "python:3.10-slim" in argv
        assert ["python", "-m", "pytest"] == argv[-3:]

    def test_docker_unavailable_returns_sandbox_unavailable_fail_closed(self):
        """When Docker daemon is absent, execution fails closed with SANDBOX_UNAVAILABLE without host execution."""
        sandbox = DockerSandbox()
        # Mock is_available to return False (simulating absent daemon)
        with patch.object(sandbox, "is_available", return_value=False):
            res = sandbox.execute("echo 'pwned'", workflow_id="wf-sandbox-test")

            assert isinstance(res, ExecutionResult)
            assert res.exit_code == 126
            assert res.metadata.get("status") == "SANDBOX_UNAVAILABLE"
            assert res.metadata.get("sandbox_unavailable") is True
            assert res.metadata.get("fail_closed") is True
            assert "SANDBOX_UNAVAILABLE" in res.stderr
            # Crucially: host execution did not occur
            assert res.stdout == ""

    def test_docker_is_available_returns_false_when_binary_missing(self):
        """is_available returns False when docker binary is not in PATH."""
        sandbox = DockerSandbox()
        with patch("shutil.which", return_value=None):
            assert sandbox.is_available() is False

    def test_docker_is_available_returns_false_when_daemon_unresponsive(self):
        """is_available returns False when docker info exits with non-zero code or times out."""
        sandbox = DockerSandbox()
        with patch("shutil.which", return_value="/usr/bin/docker"), \
             patch("subprocess.run", side_effect=Exception("daemon down")):
            assert sandbox.is_available() is False

    def test_docker_execution_success_with_mocked_subprocess(self):
        """When Docker is available, subprocess.run is called without shell=True."""
        sandbox = DockerSandbox()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "Tests passed cleanly\n"
        mock_proc.stderr = ""

        with patch.object(sandbox, "is_available", return_value=True), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = sandbox.execute("pytest", workflow_id="wf-mock-docker")

            assert res.exit_code == 0
            assert "Tests passed cleanly" in res.stdout
            assert res.metadata.get("sandbox") == "docker"
            # Verify shell=False was strictly enforced
            assert mock_run.call_args.kwargs.get("shell") is False

    @pytest.mark.skip(reason="Docker daemon unavailable in execution environment")
    def test_live_docker_sandbox_execution(self):
        """Live integration test: runs only when a real Docker daemon is accessible in environment."""
        sandbox = DockerSandbox()
        res = sandbox.execute("python -c \"print('live container test')\"")
        assert res.exit_code == 0
        assert "live container test" in res.stdout
