import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.agent.executor import ToolExecutor
from backend.models.workflow import StepDefinition, StepType
from backend.tools.git_tool import clone_repository
from backend.tools.http_tool import perform_health_check
from backend.tools.project_analyzer import analyze_workspace
from backend.tools.shell_tool import execute_shell_command, is_safe_command, redact_secrets


def test_successful_shell_command():
    res = execute_shell_command("echo Hello AgentGuard", workflow_id="test-1")
    assert res.exit_code == 0
    assert "Hello AgentGuard" in res.stdout
    assert res.stderr == ""
    assert res.duration_ms >= 0


def test_failed_shell_command_returns_nonzero():
    res = execute_shell_command("exit 42", workflow_id="test-1")
    assert res.exit_code == 42


def test_shell_captures_stderr():
    with tempfile.TemporaryDirectory() as tmpdir:
        script = Path(tmpdir) / "err.py"
        script.write_text("import sys; sys.stderr.write('Test error message\\n')\n")
        res = execute_shell_command("python err.py", cwd=tmpdir, workflow_id="test-1")
        assert res.exit_code == 0
        assert "Test error message" in res.stderr


def test_command_timeout():
    with tempfile.TemporaryDirectory() as tmpdir:
        script = Path(tmpdir) / "sleep.py"
        script.write_text("import time; time.sleep(5)\n")
        res = execute_shell_command("python sleep.py", cwd=tmpdir, timeout_seconds=1, workflow_id="test-1")
        assert res.exit_code == 124
        assert res.metadata.get("timed_out") is True
        assert "timed out" in res.stderr


def test_destructive_command_blocking():
    safe, reason = is_safe_command("rm -rf /")
    assert not safe
    assert "destructive" in reason.lower()

    res = execute_shell_command("rm -rf /", workflow_id="test-1")
    assert res.exit_code == 126
    assert "Security Violation" in res.stderr
    assert res.metadata.get("security_blocked") is True


def test_secret_redaction():
    text = "Authorization token: ghp_123456789012345678901234567890123456 and api_key='sk_test_secret123'"
    redacted = redact_secrets(text)
    assert "ghp_123456789012345678901234567890123456" not in redacted
    assert "***REDACTED" in redacted


def test_project_analyzer_detects_python():
    with tempfile.TemporaryDirectory() as tmpdir:
        req = Path(tmpdir) / "requirements.txt"
        req.write_text("fastapi\nuvicorn\n")
        
        analysis = analyze_workspace(tmpdir)
        assert analysis.language == "python"
        assert analysis.package_manager == "pip"
        assert "pip install -r requirements.txt" in (analysis.install_command or "")


def test_project_analyzer_detects_nodejs():
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg = Path(tmpdir) / "package.json"
        pkg.write_text('{"name": "demo", "scripts": {"build": "next build", "test": "jest"}}')
        
        analysis = analyze_workspace(tmpdir)
        assert analysis.language in ("javascript", "typescript")
        assert analysis.package_manager == "npm"
        assert "build" in (analysis.build_command or "")
        assert "test" in (analysis.test_command or "")


def test_git_clone_returns_structured_result():
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        # Initialize a minimal git repository locally
        execute_shell_command("git init && git config user.name 'Test' && git config user.email 'test@example.com' && git commit --allow-empty -m 'initial'", cwd=src_dir)
        target = Path(dst_dir) / "cloned_repo"
        result = clone_repository(repo_url=src_dir, target_dir=str(target), workflow_id="test-clone")
        assert result.step == "clone_repository"
        assert result.exit_code == 0
        assert result.metadata is not None
        assert result.metadata["target_dir"] == str(target.resolve())
        assert target.exists()


def test_http_health_check_failure():
    # Connect to invalid port should fail cleanly with exit_code 1
    res = perform_health_check("http://localhost:59999/health", timeout_seconds=1, workflow_id="test-1")
    assert res.exit_code == 1
    assert "health_check" == res.step


def test_tool_executor_dispatches_step():
    with tempfile.TemporaryDirectory() as tmpdir:
        executor = ToolExecutor(workflow_id="test-exec", workspace_base=tmpdir)
        step = StepDefinition(
            id="step_test",
            type="shell_command",
            name="Test Command",
            command="echo executor_ok",
        )
        res = executor.execute_step(step)
        assert res.exit_code == 0
        assert "executor_ok" in res.stdout
