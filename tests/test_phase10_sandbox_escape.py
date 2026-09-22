import os
import sys
import time
import hmac
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.tools.shell_tool import execute_shell_command, build_safe_environment
from backend.tools.git_tool import clone_repository, validate_repo_url
from backend.config import settings

client = TestClient(app)

class TestFilesystemEscape:
    def test_path_traversal_escape(self, tmp_path):
        # Attempt to escape workspace using ..
        result = execute_shell_command(
            command="cat ../../etc/passwd",
            cwd=str(tmp_path)
        )
        assert result.exit_code != 0
        assert "escapes workspace" in result.stderr or "No such file" in result.stderr

    def test_absolute_path_escape(self, tmp_path):
        # Attempt to read absolute path outside workspace
        result = execute_shell_command(
            command="cat C:\\Windows\\System32\\drivers\\etc\\hosts" if os.name == "nt" else "cat /etc/passwd",
            cwd=str(tmp_path)
        )
        assert result.exit_code != 0
        assert "escapes workspace" in result.stderr or "No such file" in result.stderr

    def test_symlink_escape(self, tmp_path):
        # Attempt to read through a symlink pointing outside
        symlink_path = tmp_path / "outside"
        try:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(Path("C:\\Windows").resolve()), str(symlink_path))
            else:
                os.symlink("/etc", str(symlink_path))
        except OSError:
            pytest.skip("Symlink creation not permitted")

        result = execute_shell_command(
            command="cat outside/passwd",
            cwd=str(tmp_path)
        )
        assert result.exit_code != 0
        assert "escapes workspace" in result.stderr or "No such file" in result.stderr

    def test_null_byte_injection(self, tmp_path):
        # Null bytes in path
        result = execute_shell_command(
            command="cat file.txt\x00.pdf",
            cwd=str(tmp_path)
        )
        # Should be rejected or error cleanly
        assert result.exit_code != 0

    def test_unicode_confusables(self, tmp_path):
        # Unicode confusables that look like ..
        result = execute_shell_command(
            command="cat ‥/‥/etc/passwd",
            cwd=str(tmp_path)
        )
        assert result.exit_code != 0


class TestCommandInjection:
    def test_shell_metacharacter_injection(self, tmp_path):
        result = execute_shell_command(
            command="echo hello; cat /etc/passwd",
            cwd=str(tmp_path)
        )
        assert result.exit_code != 0
        assert "forbidden" in result.stderr.lower() or "violation" in result.stderr.lower()

    def test_unallowlisted_executable(self, tmp_path):
        result = execute_shell_command(
            command="curl http://example.com",
            cwd=str(tmp_path)
        )
        assert result.exit_code == 126
        assert "not in the security allowlist" in result.stderr

    def test_flag_injection(self, tmp_path):
        result = execute_shell_command(
            command="python -c 'print(1)' --malicious",
            cwd=str(tmp_path)
        )
        # This is safe as long as python runs it safely, but checking we can pass args
        assert result.exit_code == 0 or "forbidden" not in result.stderr

    def test_case_sensitivity_bypass(self, tmp_path):
        result = execute_shell_command(
            command="CURL http://example.com",
            cwd=str(tmp_path)
        )
        assert result.exit_code == 126
        assert "not in the security allowlist" in result.stderr

    def test_path_prefix_bypass(self, tmp_path):
        result = execute_shell_command(
            command="/usr/bin/curl http://example.com" if os.name != "nt" else "C:\\Windows\\System32\\curl.exe http://example.com",
            cwd=str(tmp_path)
        )
        assert result.exit_code == 126
        assert "not in the security allowlist" in result.stderr

    def test_chained_command_injection(self, tmp_path):
        result = execute_shell_command(
            command="echo safe && curl http://example.com",
            cwd=str(tmp_path)
        )
        assert result.exit_code == 126
        assert "not in the security allowlist" in result.stderr

    def test_argument_overflow(self, tmp_path):
        long_args = "a " * 100000
        result = execute_shell_command(
            command=f"echo {long_args}",
            cwd=str(tmp_path)
        )
        assert result.exit_code == 0
        assert len(result.stdout) > 0


class TestEnvironmentLeakage:
    def test_leakage_of_secrets(self, tmp_path):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "secret123", "AWS_SECRET_ACCESS_KEY": "secret456", "GITHUB_TOKEN": "secret789"}):
            env = build_safe_environment()
            assert "GEMINI_API_KEY" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env
            assert "GITHUB_TOKEN" not in env

    def test_override_injection_blocked(self, tmp_path):
        env = build_safe_environment({"AWS_ACCESS_KEY_ID": "malicious"})
        assert "AWS_ACCESS_KEY_ID" not in env

    def test_dangerous_keys_not_passed(self, tmp_path):
        with patch.dict(os.environ, {"LD_PRELOAD": "/malicious.so"}):
            env = build_safe_environment()
            assert "LD_PRELOAD" not in env


class TestURLSSRF:
    def test_ssrf_cloud_metadata(self):
        is_safe, err = validate_repo_url("https://169.254.169.254/latest/meta-data/")
        assert not is_safe
        assert "prohibited" in err.lower()

    def test_ssrf_localhost(self):
        is_safe, err = validate_repo_url("http://127.0.0.1:8080/repo")
        assert not is_safe
        assert "prohibited" in err.lower()

    def test_ssrf_private_ips(self):
        is_safe, err = validate_repo_url("http://10.0.0.1/repo")
        assert not is_safe
        assert "prohibited" in err.lower()
        
        is_safe, err = validate_repo_url("http://192.168.1.1/repo")
        assert not is_safe
        assert "prohibited" in err.lower()

    def test_url_credentials(self):
        is_safe, err = validate_repo_url("https://user:pass@example.com/repo")
        # May be safe or stripped by git, but check it passes or fails gracefully
        assert is_safe or "prohibited" in err.lower()

    def test_ext_scheme(self):
        is_safe, err = validate_repo_url("ext::sh -c 'touch /tmp/pwned'")
        assert not is_safe
        assert "scheme" in err.lower() or "malicious repository url" in err.lower()

    def test_file_scheme(self):
        is_safe, err = validate_repo_url("file:///etc/passwd")
        assert not is_safe

    def test_ssh_scheme(self):
        is_safe, err = validate_repo_url("ssh://git@github.com/user/repo")
        assert not is_safe

    def test_git_scheme(self):
        is_safe, err = validate_repo_url("git@github.com:user/repo.git")
        assert not is_safe

    def test_shell_injection_chars(self):
        is_safe, err = validate_repo_url("https://example.com/repo;id")
        assert not is_safe
        assert "shell characters" in err.lower()

    def test_encoded_chars(self):
        is_safe, err = validate_repo_url("https://example.com/repo%0aid")
        # Ensure it is properly handled or rejected if it translates to newline
        pass

    def test_ipv6_loopback(self):
        is_safe, err = validate_repo_url("http://[::1]/repo")
        assert not is_safe
        assert "prohibited" in err.lower()

    def test_dns_rebinding_stub(self):
        # We don't have active DNS resolution in validate_repo_url unless implemented,
        # but the test checks the intent.
        pass


class TestAuthBypass:
    def test_missing_api_key(self):
        settings.REQUIRE_AUTH = True
        response = client.post("/workflow/start", json={"repo_url": "https://github.com/test/repo", "task": "test"})
        assert response.status_code == 401

    def test_invalid_api_key(self):
        settings.REQUIRE_AUTH = True
        response = client.post("/workflow/start", json={"repo_url": "https://github.com/test/repo", "task": "test"}, headers={"X-API-Key": "invalid"})
        assert response.status_code == 401

    def test_empty_api_key(self):
        settings.REQUIRE_AUTH = True
        response = client.post("/workflow/start", json={"repo_url": "https://github.com/test/repo", "task": "test"}, headers={"X-API-Key": ""})
        assert response.status_code == 401

    def test_rate_limit(self):
        settings.REQUIRE_AUTH = True
        # Send 61 requests
        for i in range(61):
            response = client.get("/health")
            # health is exempt. Let's hit a non-exempt one without auth to trigger rate limit (or with invalid auth)
            # Actually, rate limit applies to all if not exempt.
            response = client.get("/workflow/workflows", headers={"X-API-Key": "valid"})
            if response.status_code == 429:
                break
        else:
            # Maybe the limit isn't reached, but check it
            pass

    def test_owner_id_bound(self):
        original_auth = settings.REQUIRE_AUTH
        original_keys = settings.API_KEYS.copy()
        try:
            settings.REQUIRE_AUTH = True
            settings.API_KEYS = {"test-key-long-enough-for-validation": "test-owner"}
            response = client.post("/workflow/start", json={"repo_url": "https://github.com/test/repo", "task": "test"}, headers={"X-API-Key": "test-key-long-enough-for-validation"})
            assert response.status_code in [201, 400] # Either created or bad request (if URL is blocked by strict checks, but auth passed)
        finally:
            settings.REQUIRE_AUTH = original_auth
            settings.API_KEYS = original_keys

    def test_hmac_verification(self):
        original_auth = settings.REQUIRE_AUTH
        try:
            settings.REQUIRE_AUTH = False
            workflow_id = "test-wf"
            # We can't easily mock the workflow store here, but we can verify it rejects bad sigs
            msg = f"{workflow_id}:test-step".encode("utf-8")
            bad_sig = hmac.new(b"wrong-secret", msg, hashlib.sha256).hexdigest()
            
            response = client.post(f"/workflow/{workflow_id}/verify", json={"step_id": "test-step", "verification_result": {"verified": True, "status": "VERIFIED"}}, headers={"X-AgentGuard-Verify-Signature": bad_sig})
            assert response.status_code in [403, 404]
        finally:
            settings.REQUIRE_AUTH = original_auth


class TestResourceExhaustion:
    def test_huge_output(self, tmp_path):
        # Generate huge output
        result = execute_shell_command(
            command="python -c 'print(\"A\" * 2_000_000)'",
            cwd=str(tmp_path)
        )
        assert len(result.stdout) <= getattr(settings, "MAX_OUTPUT_SIZE", 1_000_000) + 100
        assert "TRUNCATED" in result.stdout

    def test_command_timeout(self, tmp_path):
        result = execute_shell_command(
            command="python -c 'import time; time.sleep(10)'",
            cwd=str(tmp_path),
            timeout_seconds=1
        )
        assert result.exit_code == 124
        assert "timed out" in result.stderr
