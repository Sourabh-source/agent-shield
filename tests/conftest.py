import hashlib
import hmac
import time
import uuid
import pytest

from backend.config import settings
from backend.middleware.auth import reset_rate_limiter
from backend.api.workflow import reset_verify_replay_cache

TEST_HMAC_SECRET = "production-grade-random-hmac-secret-999888777"


def make_test_verify_headers(workflow_id: str, step_id: str = "", evidence_digest: str = ""):
    settings.VERIFY_HMAC_SECRET = TEST_HMAC_SECRET
    ts = str(time.time())
    nonce = str(uuid.uuid4())
    msg = f"{workflow_id}:{step_id}:{evidence_digest}:{ts}:{nonce}".encode("utf-8")
    sig = hmac.new(TEST_HMAC_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {
        "X-AgentGuard-Verify-Signature": sig,
        "X-AgentGuard-Verify-Timestamp": ts,
        "X-AgentGuard-Verify-Nonce": nonce,
        "X-API-Key": "test-api-key",
    }


@pytest.fixture(autouse=True)
def reset_security_caches():
    """Reset in-memory rate limiting and verify replay nonces between tests."""
    settings.VERIFY_HMAC_SECRET = TEST_HMAC_SECRET
    reset_rate_limiter()
    reset_verify_replay_cache()
    yield
    reset_rate_limiter()
    reset_verify_replay_cache()
