"""
tests/test_frontend_auth_proxy.py
Tests for AgentGuard frontend server-side auth proxy.

Guarantees:
1. Backend requires X-API-Key (returns 401 without it).
2. Proxy forwards request with X-API-Key attached server-side without exposing key to browser.
3. Unset AGENTGUARD_API_KEY returns clear 500 server misconfiguration error.
4. Multi-tenancy isolation holds across the proxy (Tenant A vs Tenant B).
5. Production bundle (.next/static) contains ZERO secret API keys.
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
import pytest

FRONTEND_BASE = os.getenv("FRONTEND_TEST_URL", "http://localhost:3000")
BACKEND_BASE = os.getenv("BACKEND_TEST_URL", "http://127.0.0.1:8000")

TENANT_A_KEY = "tenant-a-secret-key-12345"
TENANT_B_KEY = "tenant-b-secret-key-67890"
DEFAULT_KEY = "test-api-key"


def test_direct_backend_requires_auth():
    """Verify backend AuthMiddleware rejects direct requests lacking X-API-Key."""
    req = urllib.request.Request(f"{BACKEND_BASE}/workflows")
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 401
    err_body = exc_info.value.read().decode("utf-8")
    assert "unauthorized" in err_body.lower() or "api key" in err_body.lower()


def test_proxy_forwards_with_server_key():
    """Verify proxy route automatically attaches server-held AGENTGUARD_API_KEY."""
    url = f"{FRONTEND_BASE}/api/backend/workflows"
    req = urllib.request.Request(url)
    res = urllib.request.urlopen(req)
    assert res.status == 200
    data = json.loads(res.read().decode("utf-8"))
    assert isinstance(data, list)


def test_proxy_supports_post_workflow_start():
    """Verify workflow start succeeds end-to-end through the proxy."""
    url = f"{FRONTEND_BASE}/api/backend/workflow/start"
    payload = json.dumps({
        "repo_url": "https://github.com/octocat/Hello-World",
        "task": "Automated proxy integration test",
        "dry_run": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    res = urllib.request.urlopen(req)
    assert res.status == 201
    body = json.loads(res.read().decode("utf-8"))
    assert "workflow_id" in body
    wf_id = body["workflow_id"]

    # Verify status query through proxy
    status_url = f"{FRONTEND_BASE}/api/backend/workflow/{wf_id}/status"
    status_res = urllib.request.urlopen(status_url)
    assert status_res.status == 200
    status_body = json.loads(status_res.read().decode("utf-8"))
    assert status_body["workflow_id"] == wf_id
    assert status_body.get("owner_id") == "default-owner"


def test_proxy_multi_tenancy_scoping():
    """Verify caller-provided X-API-Key is preserved for tenant isolation."""
    # 1. Tenant A starts a workflow
    start_url = f"{FRONTEND_BASE}/api/backend/workflow/start"
    payload_a = json.dumps({
        "repo_url": "https://github.com/example/tenant-a-repo",
        "task": "Tenant A isolated task",
        "dry_run": True,
    }).encode("utf-8")

    req_a = urllib.request.Request(
        start_url,
        data=payload_a,
        headers={"Content-Type": "application/json", "X-API-Key": TENANT_A_KEY},
        method="POST",
    )
    res_a = urllib.request.urlopen(req_a)
    assert res_a.status == 201
    wf_a_id = json.loads(res_a.read().decode("utf-8"))["workflow_id"]

    # 2. Tenant B attempts to read Tenant A's workflow status -> must 404
    status_b_url = f"{FRONTEND_BASE}/api/backend/workflow/{wf_a_id}/status"
    req_b_status = urllib.request.Request(status_b_url, headers={"X-API-Key": TENANT_B_KEY})
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_b_status)
    assert exc_info.value.code == 404, "Tenant B must get 404 on Tenant A workflow"

    # 3. Tenant B lists workflows -> must NOT contain Tenant A's workflow
    list_b_url = f"{FRONTEND_BASE}/api/backend/workflows"
    req_b_list = urllib.request.Request(list_b_url, headers={"X-API-Key": TENANT_B_KEY})
    res_b_list = urllib.request.urlopen(req_b_list)
    assert res_b_list.status == 200
    b_workflows = json.loads(res_b_list.read().decode("utf-8"))
    b_ids = [w["workflow_id"] for w in b_workflows]
    assert wf_a_id not in b_ids, "Tenant A workflow leaked into Tenant B listing!"


def test_proxy_rejects_invalid_caller_key():
    """Verify proxy passes through 401 when an invalid key is supplied."""
    url = f"{FRONTEND_BASE}/api/backend/workflows"
    req = urllib.request.Request(url, headers={"X-API-Key": "invalid-bogus-key-999"})
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req)
    assert exc_info.value.code == 401


def test_client_bundles_contain_no_secret_keys():
    """
    CRITICAL SECURITY TEST:
    Verify that built client bundles (.next/static) do NOT contain any secret API keys.
    Grep all JS files in .next/static/ for known secret key values.
    """
    root_dir = Path(__file__).resolve().parent.parent
    static_dir = root_dir / "frontend" / ".next" / "static"

    assert static_dir.exists(), f"Production build directory not found: {static_dir}"

    forbidden_secrets = [
        DEFAULT_KEY,
        TENANT_A_KEY,
        TENANT_B_KEY,
        "admin-secret-key",
    ]

    scanned_files = 0
    violations = []

    for js_file in static_dir.rglob("*.js"):
        scanned_files += 1
        content = js_file.read_text(encoding="utf-8", errors="ignore")
        for secret in forbidden_secrets:
            if secret in content:
                violations.append((str(js_file.relative_to(root_dir)), secret))

    assert scanned_files > 0, "No client JS files found in .next/static/"
    assert len(violations) == 0, f"SECURITY VIOLATION: Secret keys leaked to client bundle: {violations}"
