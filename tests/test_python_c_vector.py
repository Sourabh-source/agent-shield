"""
tests/test_python_c_vector.py
Adversarial tests specifically targeting the python -c and powershell execution vectors.
Every vector must be blocked or rejected by shell_tool.
"""
import pytest
from backend.tools.shell_tool import execute_shell_command, validate_and_parse_command, is_safe_command


class TestPythonCVectorAdversarial:
    """Test that python -c arbitrary command execution is completely blocked."""

    def test_python_c_os_system_blocked(self, tmp_path):
        cmd = 'python -c "import os; os.system(\'whoami\')"'
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe, "SECURITY FAILURE: python -c os.system was permitted"
        assert "inline python" in reason.lower() or "forbidden" in reason.lower() or "blocked" in reason.lower()

        res = execute_shell_command(cmd, cwd=str(tmp_path))
        assert res.exit_code != 0
        assert res.metadata and res.metadata.get("security_blocked")

    def test_python_c_open_file_blocked(self, tmp_path):
        cmd = 'python3 -c "print(open(\'/etc/passwd\').read())"'
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe, "SECURITY FAILURE: python3 -c file read was permitted"

        res = execute_shell_command(cmd, cwd=str(tmp_path))
        assert res.exit_code != 0
        assert res.metadata and res.metadata.get("security_blocked")

    def test_python_c_subprocess_blocked(self, tmp_path):
        cmd = 'python -c "__import__(\'subprocess\').run([\'id\'])"'
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe, "SECURITY FAILURE: python -c subprocess.run was permitted"

        res = execute_shell_command(cmd, cwd=str(tmp_path))
        assert res.exit_code != 0

    def test_python_c_pathlib_read_blocked(self, tmp_path):
        cmd = 'python -c "import pathlib; pathlib.Path(\'/etc/shadow\').read_text()"'
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe, "SECURITY FAILURE: python -c pathlib was permitted"

        res = execute_shell_command(cmd, cwd=str(tmp_path))
        assert res.exit_code != 0

    def test_python_c_env_dump_blocked(self, tmp_path):
        cmd = 'python -c "import os; print(os.environ)"'
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe

    def test_python_file_execution_allowed_within_workspace(self, tmp_path):
        """Legitimate python file execution within workspace MUST remain permitted."""
        script = tmp_path / "hello.py"
        script.write_text("print('HELLO FROM WORKSPACE')\n")
        safe, reason = is_safe_command("python hello.py", cwd=str(tmp_path))
        assert safe, f"Expected safe for workspace script, got error: {reason}"

        res = execute_shell_command("python hello.py", cwd=str(tmp_path))
        assert res.exit_code == 0
        assert "HELLO FROM WORKSPACE" in res.stdout


class TestPowershellVectorAdversarial:
    """Test that powershell and taskkill are removed from allowable shell commands."""

    def test_powershell_command_rejected(self, tmp_path):
        cmd = 'powershell -Command "Write-Host PWNED"'
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe, "SECURITY FAILURE: powershell execution was permitted"
        assert "not in the security allowlist" in reason.lower() or "forbidden" in reason.lower()

        res = execute_shell_command(cmd, cwd=str(tmp_path))
        assert res.exit_code != 0
        assert res.metadata and res.metadata.get("security_blocked")

    def test_powershell_encoded_command_rejected(self, tmp_path):
        cmd = 'powershell -EncodedCommand V3JpdGUtSG9zdCBQV05FRA=='
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe

    def test_taskkill_shell_command_rejected(self, tmp_path):
        cmd = 'taskkill /F /PID 9999'
        safe, reason = is_safe_command(cmd, cwd=str(tmp_path))
        assert not safe, "SECURITY FAILURE: taskkill execution was permitted via shell_tool"
        assert "not in the security allowlist" in reason.lower() or "forbidden" in reason.lower()

        res = execute_shell_command(cmd, cwd=str(tmp_path))
        assert res.exit_code != 0
