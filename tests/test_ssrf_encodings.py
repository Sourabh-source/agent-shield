"""
tests/test_ssrf_encodings.py
Adversarial tests for alternate numeric IP encodings (decimal, octal, hex)
and DNS rebinding prevention in repo URL validation and clone tool.
"""
import socket
import pytest
from unittest.mock import patch

from backend.tools.git_tool import validate_repo_url, clone_repository


class TestSSRFEncodings:
    """Test detection and rejection of obfuscated IP literals."""

    def test_decimal_ip_metadata_blocked(self):
        # 2852039166 == 169.254.169.254
        ok, err = validate_repo_url("http://2852039166/repo.git")
        assert not ok, "SECURITY FAILURE: Decimal IP for 169.254.169.254 was permitted"
        assert "ssrf" in err.lower() or "blocked" in err.lower() or "private" in err.lower()

    def test_decimal_ip_loopback_blocked(self):
        # 2130706433 == 127.0.0.1
        ok, err = validate_repo_url("http://2130706433/repo.git")
        assert not ok, "SECURITY FAILURE: Decimal IP for 127.0.0.1 was permitted"

    def test_octal_dotted_ip_blocked(self):
        # 0251.0376.0251.0376 == 169.254.169.254
        ok, err = validate_repo_url("http://0251.0376.0251.0376/repo.git")
        assert not ok, "SECURITY FAILURE: Octal dotted IP was permitted"

    def test_octal_single_number_blocked(self):
        # 017700000001 == 127.0.0.1
        ok, err = validate_repo_url("http://017700000001/repo.git")
        assert not ok, "SECURITY FAILURE: Octal single number IP was permitted"

    def test_hex_single_number_blocked(self):
        # 0xa9fea9fe == 169.254.169.254
        ok, err = validate_repo_url("http://0xa9fea9fe/repo.git")
        assert not ok, "SECURITY FAILURE: Hex single number IP was permitted"

    def test_hex_dotted_ip_blocked(self):
        # 0x7f.0x0.0x0.0x1 == 127.0.0.1
        ok, err = validate_repo_url("http://0x7f.0x0.0x0.0x1/repo.git")
        assert not ok, "SECURITY FAILURE: Hex dotted IP was permitted"

    def test_dns_resolution_private_ip_blocked(self):
        # Mock getaddrinfo to return 10.0.0.1 for an innocent-looking domain
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80))]):
            ok, err = validate_repo_url("http://innocent-domain.com/repo.git")
            assert not ok, "SECURITY FAILURE: Domain resolving to private IP 10.0.0.1 was permitted"
            assert "private" in err.lower() or "blocked" in err.lower() or "ssrf" in err.lower()

    def test_dns_rebinding_at_clone_time_fails_closed(self, tmp_path):
        # Validation time returns public IP, but clone-time re-check returns 169.254.169.254
        rebound_responses = [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],  # First call (validation)
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))], # Second call (rebind at clone)
        ]
        with patch("socket.getaddrinfo", side_effect=rebound_responses):
            res = clone_repository("http://rebinding-target.com/repo.git", str(tmp_path))
            assert res.exit_code != 0
            assert "ssrf" in res.stderr.lower() or "blocked" in res.stderr.lower() or "rebind" in res.stderr.lower()
