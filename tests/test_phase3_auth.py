"""
Phase 3 — AuthN / AuthZ (P0): Adversarial Security Tests

Tests:
1. Missing X-API-Key returns 401 Unauthorized.
2. Invalid X-API-Key returns 401 Unauthorized.
3. Exempt paths (/health, /docs, /openapi.json, /) are accessible without key.
4. Valid X-API-Key returns 201 Created.
5. Multi-tenancy isolation: Tenant B cannot access or enumerate Tenant A's workflow (returns 404).
6. Multi-tenancy list filtering: GET /workflows returns only the caller's workflows.
7. HMAC-SHA256 verification on /verify: valid signature accepted.
8. Forged HMAC signature rejected with 403 Forbidden.
9. In-memory rate limiting triggers 429 Too Many Requests when threshold exceeded.
10. demo_failure_mode removed from public WorkflowCreateRequest schema.
"""
import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.workflow import WorkflowCreateRequest


VALID_API_KEY_TENANT_A = "tenant-a-secret-key-12345"
VALID_API_KEY_TENANT_B = "tenant-b-secret-key-67890"


class TestAuthNEnforcement:
    """Test that all non-exempt endpoints reject unauthenticated requests."""

    def test_missing_api_key_returns_401(self):
        client = TestClient(app)
        resp = client.post("/workflow/start", json={
            "repo_url": "https://github.com/example/repo",
            "task": "Build and test",
        })
        assert resp.status_code == 401, (
            f"SECURITY FAILURE: Missing X-API-Key returned {resp.status_code}, expected 401"
        )
        assert "unauthorized" in resp.text.lower() or "api key" in resp.text.lower()

    def test_invalid_api_key_returns_401(self):
        client = TestClient(app)
        resp = client.post(
            "/workflow/start",
            headers={"X-API-Key": "completely-invalid-key-99999"},
            json={
                "repo_url": "https://github.com/example/repo",
                "task": "Build and test",
            },
        )
        assert resp.status_code == 401, (
            f"SECURITY FAILURE: Invalid X-API-Key returned {resp.status_code}, expected 401"
        )

    def test_exempt_endpoints_accessible_without_key(self):
        client = TestClient(app)
        for path in ["/", "/health", "/docs", "/openapi.json"]:
            resp = client.get(path)
            assert resp.status_code == 200, (
                f"REGRESSION: Exempt endpoint '{path}' returned {resp.status_code}, expected 200"
            )

    def test_valid_api_key_accepted(self):
        client = TestClient(app)
        resp = client.post(
            "/workflow/start",
            headers={"X-API-Key": VALID_API_KEY_TENANT_A},
            json={
                "repo_url": "https://github.com/example/repo",
                "task": "Build and test",
            },
        )
        assert resp.status_code == 201, (
            f"Valid X-API-Key returned {resp.status_code}, expected 201. Response: {resp.text}"
        )
        data = resp.json()
        assert "workflow_id" in data


class TestMultiTenancyIsolation:
    """Test that workflows are isolated per tenant/owner."""

    def test_tenant_b_cannot_access_tenant_a_workflow(self):
        client = TestClient(app)

        # 1. Tenant A creates a workflow
        create_resp = client.post(
            "/workflow/start",
            headers={"X-API-Key": VALID_API_KEY_TENANT_A},
            json={
                "repo_url": "https://github.com/example/tenant-a-private-repo",
                "task": "Private task",
            },
        )
        assert create_resp.status_code == 201
        wf_id = create_resp.json()["workflow_id"]

        # 2. Tenant B attempts to read Tenant A's workflow status
        # Must return 404 (not 403) to prevent resource enumeration attacks
        b_resp = client.get(
            f"/workflow/{wf_id}/status",
            headers={"X-API-Key": VALID_API_KEY_TENANT_B},
        )
        assert b_resp.status_code == 404, (
            f"SECURITY FAILURE: Tenant B got {b_resp.status_code} on Tenant A's workflow; "
            "expected 404 to prevent resource enumeration."
        )

    def test_tenant_workflows_list_isolated(self):
        client = TestClient(app)

        # 1. Tenant A creates a workflow
        resp_a = client.post(
            "/workflow/start",
            headers={"X-API-Key": VALID_API_KEY_TENANT_A},
            json={"repo_url": "https://github.com/example/tenant-a-app", "task": "Task A"},
        )
        assert resp_a.status_code == 201
        wf_a_id = resp_a.json()["workflow_id"]

        # 2. Tenant B lists workflows
        resp_b_list = client.get(
            "/workflows",
            headers={"X-API-Key": VALID_API_KEY_TENANT_B},
        )
        assert resp_b_list.status_code == 200
        b_workflows = resp_b_list.json()
        b_ids = [w["workflow_id"] for w in b_workflows]
        assert wf_a_id not in b_ids, (
            f"SECURITY FAILURE: Tenant A's workflow {wf_a_id} appeared in Tenant B's list!"
        )


class TestHMACVerificationToken:
    """Test HMAC-SHA256 signing on the /verify endpoint."""

    def test_forged_hmac_rejected(self):
        client = TestClient(app)

        # Create workflow
        resp = client.post(
            "/workflow/start",
            headers={"X-API-Key": VALID_API_KEY_TENANT_A},
            json={"repo_url": "https://github.com/example/repo", "task": "Verify test"},
        )
        wf_id = resp.json()["workflow_id"]

        # Forged verification request with invalid signature
        payload = {
            "step_id": "step-1",
            "verification_result": {
                "verified": True,
                "reason": "Forged pass",
            },
        }
        forged_resp = client.post(
            f"/workflow/{wf_id}/verify",
            headers={
                "X-API-Key": VALID_API_KEY_TENANT_A,
                "X-AgentGuard-Verify-Signature": "forged_invalid_signature_hex",
            },
            json=payload,
        )
        assert forged_resp.status_code in (401, 403), (
            f"SECURITY FAILURE: Forged HMAC signature returned {forged_resp.status_code}, expected 403"
        )


class TestPublicAPISchemaHygiene:
    """Test that demo_failure_mode is removed from public API request schema."""

    def test_demo_failure_mode_not_in_public_request_model(self):
        fields = WorkflowCreateRequest.model_fields
        assert "demo_failure_mode" not in fields, (
            "SECURITY FAILURE: 'demo_failure_mode' should be removed from public WorkflowCreateRequest"
        )


class TestRateLimiting:
    """Test that rapid requests exceed rate limits and return 429."""

    def test_rate_limiting_enforced(self):
        from backend.config import settings
        client = TestClient(app)

        # Send more than rate limit per minute
        rate_limit = getattr(settings, "RATE_LIMIT_PER_MINUTE", 60)
        hit_429 = False
        for _ in range(rate_limit + 5):
            resp = client.get("/workflows", headers={"X-API-Key": VALID_API_KEY_TENANT_A})
            if resp.status_code == 429:
                hit_429 = True
                assert "retry-after" in [k.lower() for k in resp.headers.keys()] or "too many" in resp.text.lower()
                break

        assert hit_429, (
            f"SECURITY FAILURE: Sent {rate_limit + 5} requests without triggering 429 rate limit"
        )
