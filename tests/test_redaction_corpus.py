"""
tests/test_redaction_corpus.py
Permanent regression gate for secret redaction coverage (25+ real-shaped secret formats)
and false-positive prevention on legitimate non-secret output.
"""
import pytest
from backend.tools.shell_tool import redact_secrets


# 25+ real-shaped secret formats and edge cases
SECRET_EXAMPLES = [
    # 1. OpenAI API key
    ("sk-proj-abc12345678901234567890abcdef", "OpenAI modern project key"),
    # 2. OpenAI legacy key
    ("sk-abcdef12345678901234567890123456", "OpenAI legacy key"),
    # 3. Anthropic Claude API key
    ("sk-ant-api03-abcdef12345678901234567890", "Anthropic Claude key"),
    # 4. GitHub Fine-Grained Personal Access Token (PAT)
    ("github_pat_11AABCDEF0123456789012_abcdef123456789012345678901234567890123456", "GitHub Fine-grained PAT"),
    # 5. GitHub classic personal access token
    ("ghp_1234567890abcdef1234567890abcdef12", "GitHub classic token"),
    # 6. GitHub OAuth token
    ("gho_1234567890abcdef1234567890abcdef12", "GitHub OAuth token"),
    # 7. GitHub user-to-server token
    ("ghu_1234567890abcdef1234567890abcdef12", "GitHub user-to-server token"),
    # 8. GitHub Server-to-server token
    ("ghs_1234567890abcdef1234567890abcdef12", "GitHub Server token"),
    # 9. GitHub refresh token
    ("ghr_1234567890abcdef1234567890abcdef12", "GitHub refresh token"),
    # 10. Slack bot token
    ("xoxb-" + "1234567890-1234567890123-abcdef123456", "Slack Bot token"),
    # 11. Slack app token
    ("xapp-" + "1-A0123456789-1234567890123-abcdef123456", "Slack App token"),
    # 12. Stripe live secret key
    ("sk" + "_live_51A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P", "Stripe live secret"),
    # 13. Stripe live restricted key
    ("rk" + "_live_51A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P", "Stripe live restricted key"),
    # 14. Google AI Studio / Cloud API key
    ("AIzaSy" + "A1234567890abcdef-1234567890abcdef", "Google AI Studio API key"),
    # 15. AWS Access Key ID
    ("AKIA" + "IOSFODNN7EXAMPLE", "AWS Access Key ID"),
    # 16. AWS Secret Access Key in context
    ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "AWS Secret Access Key"),
    # 17. JSON Web Token (JWT)
    ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", "Standard JWT"),
    # 18. URL with embedded basic auth credentials
    ("https://admin:SuperSecretPassword123@internal.corp.net/api", "URL with embedded password"),
    # 19. PEM RSA Private Key block
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Y8Lz1...\n-----END RSA PRIVATE KEY-----", "RSA Private Key PEM"),
    # 20. PEM generic PRIVATE KEY block
    ("-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASC...\n-----END PRIVATE KEY-----", "Generic Private Key PEM"),
    # 21. Short password assignment (no 8-char minimum restriction)
    ("password = foo", "Short password assignment"),
    # 22. Secret token inside JSON string
    ('{"auth_token": "secret_val_xyz_999"}', "Secret inside JSON blob"),
    # 23. Secret as URL query parameter
    ("https://api.example.com/v1?token=super_secret_token_12345", "Secret in URL query string"),
    # 24. Secret at exact start of string
    ("sk-proj-start12345678901234567890 is at start", "Secret at start of string"),
    # 25. Secret at exact end of string
    ("end of string key sk-proj-end12345678901234567890", "Secret at end of string"),
    # 26. Multiple secrets in single blob
    ("Found keys: sk-proj-first12345678901234567890 and xoxb-9999-8888-7777 in same log", "Multiple secrets in one text"),
]

# Non-secrets that must NOT be redacted (avoid destroying legitimate output)
FALSE_POSITIVE_EXAMPLES = [
    ("commit 7f3b89a812ef4c6d9123456789abcdef01234567", "40-character Git SHA"),
    ("uuid: 123e4567-e89b-12d3-a456-426614174000", "Standard UUID v4"),
    ("npm install express@4.18.2 --save", "Standard npm command"),
    ("PATH=/usr/local/bin:/usr/bin:/bin", "PATH environment variable"),
    ("def validate_token(token: str) -> bool:", "Python function definition"),
    ("pytest tests/test_api.py::test_status_endpoint", "Pytest test ID"),
]


class TestRedactionCorpus:
    """Test 100% redaction across all real-world secret formats."""

    @pytest.mark.parametrize("secret_text, description", SECRET_EXAMPLES)
    def test_secret_is_redacted(self, secret_text, description):
        redacted = redact_secrets(secret_text)
        # 1. The literal secret string must not appear in the redacted output
        if "-----BEGIN" in secret_text:
            assert "BEGIN RSA PRIVATE KEY" not in redacted and "BEGIN PRIVATE KEY" not in redacted, (
                f"PEM block was not redacted: {description}"
            )
        elif "SuperSecretPassword123" in secret_text:
            assert "SuperSecretPassword123" not in redacted, f"URL password was not redacted: {description}"
        elif "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" in secret_text:
            assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in redacted
        elif "password = foo" in secret_text:
            assert "foo" not in redacted, f"Short password was not redacted: {description}"
        elif "secret_val_xyz_999" in secret_text:
            assert "secret_val_xyz_999" not in redacted
        elif "super_secret_token_12345" in secret_text:
            assert "super_secret_token_12345" not in redacted
        else:
            # For standalone token strings, the exact token should be redacted
            for word in secret_text.split():
                if any(word.startswith(prefix) for prefix in ("sk-", "github_pat_", "gh", "xox", "rk_", "AIza", "AKIA", "eyJ")):
                    assert word not in redacted, f"Secret token '{word}' was not redacted: {description}"

        # 2. Redaction placeholder must appear in output
        assert "***REDACTED" in redacted, f"Expected redaction placeholder for: {description}"

    @pytest.mark.parametrize("safe_text, description", FALSE_POSITIVE_EXAMPLES)
    def test_non_secrets_not_redacted(self, safe_text, description):
        redacted = redact_secrets(safe_text)
        assert redacted == safe_text, (
            f"Over-redaction: non-secret '{safe_text}' was modified to '{redacted}' ({description})"
        )
