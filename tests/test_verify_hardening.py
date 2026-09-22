"""
tests/test_verify_hardening.py
Adversarial tests for mandatory HMAC signature verification on /verify endpoint.
"""
import hashlib
import hmac
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.models.workflow import StepDefinition, StepType, ExecutionResult, current_iso_time
from backend.config import settings

client = TestClient(app)

TEST_HMAC_SECRET = "production-grade-random-hmac-secret-999888777"


@pytest.fixture(autouse=True)
def configure_hmac_secret(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    monkeypatch.setattr(settings, "API_KEYS", {"tenant-a-secret-key-12345": "tenant-a"})
    monkeypatch.setattr(settings, "VERIFY_HMAC_SECRET", TEST_HMAC_SECRET)


def create_test_workflow_with_step(step_id="step-build", evidence_digest="digest-12345"):
    orch = WorkflowOrchestrator()
    wf = orch.create_workflow(
        repo_url="https://github.com/example/repo",
        task="Build and test",
        owner_id="tenant-a",
    )
    step = StepDefinition(
        id=step_id,
        type=StepType.BUILD_PROJECT.value,
        name="build",
        command="npm run build",
    )
    exec_res = ExecutionResult(
        workflow_id=wf.workflow_id,
        step="build",
        step_id=step_id,
        exit_code=0,
        stdout="build ok",
        stderr="",
        evidence_digest=evidence_digest,
    )
    step.execution_result = exec_res
    step.evidence_digest = evidence_digest
    wf.steps.append(step)
    workflow_store.save(wf)
    return wf


def compute_hardened_signature(secret, workflow_id, step_id, evidence_digest, timestamp, nonce):
    msg = f"{workflow_id}:{step_id}:{evidence_digest}:{timestamp}:{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


class TestVerifyEndpointHardening:
    """Test mandatory, non-replayable, evidence-bound HMAC signatures on /verify."""

    def test_verify_missing_signature_rejected(self):
        wf = create_test_workflow_with_step()
        resp = client.post(
            f"/workflow/{wf.workflow_id}/verify",
            headers={"X-API-Key": "tenant-a-secret-key-12345"},
            json={
                "step_id": "step-build",
                "verification_result": {"verified": True, "status": "VERIFIED"},
            },
        )
        assert resp.status_code in (401, 403), (
            f"SECURITY FAILURE: /verify accepted verdict with no signature header: {resp.status_code}"
        )

    def test_verify_old_hardcoded_default_secret_rejected(self):
        wf = create_test_workflow_with_step()
        now = int(time.time())
        nonce = str(uuid.uuid4())
        # Signed with the old committed secret
        old_secret = "agentguard-hmac-secret-key-prod"
        bad_sig = compute_hardened_signature(
            old_secret, wf.workflow_id, "step-build", "digest-12345", now, nonce
        )
        resp = client.post(
            f"/workflow/{wf.workflow_id}/verify",
            headers={
                "X-API-Key": "tenant-a-secret-key-12345",
                "X-AgentGuard-Verify-Signature": bad_sig,
                "X-AgentGuard-Verify-Timestamp": str(now),
                "X-AgentGuard-Verify-Nonce": nonce,
            },
            json={
                "step_id": "step-build",
                "verification_result": {"verified": True, "status": "VERIFIED"},
            },
        )
        assert resp.status_code == 403, (
            f"SECURITY FAILURE: /verify accepted old default hardcoded secret: {resp.status_code}"
        )

    def test_verify_replayed_nonce_rejected(self):
        wf = create_test_workflow_with_step()
        now = int(time.time())
        nonce = str(uuid.uuid4())
        sig = compute_hardened_signature(
            TEST_HMAC_SECRET, wf.workflow_id, "step-build", "digest-12345", now, nonce
        )
        headers = {
            "X-API-Key": "tenant-a-secret-key-12345",
            "X-AgentGuard-Verify-Signature": sig,
            "X-AgentGuard-Verify-Timestamp": str(now),
            "X-AgentGuard-Verify-Nonce": nonce,
        }
        body = {
            "step_id": "step-build",
            "verification_result": {"verified": True, "status": "VERIFIED"},
        }
        # First request: valid
        resp1 = client.post(f"/workflow/{wf.workflow_id}/verify", headers=headers, json=body)
        assert resp1.status_code == 200, f"Initial valid verify failed: {resp1.text}"

        # Second request with identical nonce: must be rejected as replay
        resp2 = client.post(f"/workflow/{wf.workflow_id}/verify", headers=headers, json=body)
        assert resp2.status_code == 403, (
            f"SECURITY FAILURE: Replayed nonce was accepted: {resp2.status_code}"
        )
        assert "replay" in resp2.text.lower() or "nonce" in resp2.text.lower()

    def test_verify_expired_timestamp_rejected(self):
        wf = create_test_workflow_with_step()
        past = int(time.time()) - 120  # 2 minutes ago (> 60s skew)
        nonce = str(uuid.uuid4())
        sig = compute_hardened_signature(
            TEST_HMAC_SECRET, wf.workflow_id, "step-build", "digest-12345", past, nonce
        )
        resp = client.post(
            f"/workflow/{wf.workflow_id}/verify",
            headers={
                "X-API-Key": "tenant-a-secret-key-12345",
                "X-AgentGuard-Verify-Signature": sig,
                "X-AgentGuard-Verify-Timestamp": str(past),
                "X-AgentGuard-Verify-Nonce": nonce,
            },
            json={
                "step_id": "step-build",
                "verification_result": {"verified": True, "status": "VERIFIED"},
            },
        )
        assert resp.status_code == 403, (
            f"SECURITY FAILURE: Expired timestamp was accepted: {resp.status_code}"
        )
        assert "timestamp" in resp.text.lower() or "expired" in resp.text.lower() or "skew" in resp.text.lower()

    def test_verify_mismatched_evidence_digest_rejected(self):
        wf = create_test_workflow_with_step(evidence_digest="digest-real-execution")
        now = int(time.time())
        nonce = str(uuid.uuid4())
        # Attacker signs with a different evidence digest
        tampered_sig = compute_hardened_signature(
            TEST_HMAC_SECRET, wf.workflow_id, "step-build", "digest-forged", now, nonce
        )
        resp = client.post(
            f"/workflow/{wf.workflow_id}/verify",
            headers={
                "X-API-Key": "tenant-a-secret-key-12345",
                "X-AgentGuard-Verify-Signature": tampered_sig,
                "X-AgentGuard-Verify-Timestamp": str(now),
                "X-AgentGuard-Verify-Nonce": nonce,
            },
            json={
                "step_id": "step-build",
                "verification_result": {"verified": True, "status": "VERIFIED"},
            },
        )
        assert resp.status_code == 403, (
            f"SECURITY FAILURE: Mismatched evidence digest signature was accepted: {resp.status_code}"
        )

    def test_verify_valid_signature_accepted(self):
        wf = create_test_workflow_with_step(evidence_digest="digest-legit-step")
        now = int(time.time())
        nonce = str(uuid.uuid4())
        sig = compute_hardened_signature(
            TEST_HMAC_SECRET, wf.workflow_id, "step-build", "digest-legit-step", now, nonce
        )
        resp = client.post(
            f"/workflow/{wf.workflow_id}/verify",
            headers={
                "X-API-Key": "tenant-a-secret-key-12345",
                "X-AgentGuard-Verify-Signature": sig,
                "X-AgentGuard-Verify-Timestamp": str(now),
                "X-AgentGuard-Verify-Nonce": nonce,
            },
            json={
                "step_id": "step-build",
                "verification_result": {"verified": True, "status": "VERIFIED"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["verification_status"] == "PASS"
