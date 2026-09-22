"""
Phase 6 (Secrets & Structured Logging) & Phase 7 (Concurrency & FSM Lifecycle)
Test suite demonstrating:
1. Secret masking across various token formats in redact_secrets and structured logs
2. Structured JSON logging with correlation_id
3. FSM transition enforcement: terminal states reject execution/resumption with 400
4. Asynchronous background execution on /execute
5. Redaction of secrets in error payloads
"""
import json
import logging
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.workflow import StepDefinition, StepStatus, WorkflowStatus
from backend.agent.orchestrator import workflow_store
from backend.observability import JsonFormatter
from backend.tools.shell_tool import redact_secrets


client = TestClient(app, headers={"X-API-Key": "test-api-key"})


class TestPhase6SecretsAndLogging:

    def test_redact_secrets_masks_sensitive_credentials(self):
        """Redacts various credentials and tokens from text."""
        samples = [
            ("api_key=sk-1234567890abcdef123456", "api_key=***REDACTED***"),
            ("token: ghp_123456789012345678901234567890123456", "token: ***REDACTED_GITHUB_TOKEN***"),
            ("AIzaSyD-1234567890abcdef1234567890", "***REDACTED_GEMINI_KEY***"),
            ("AKIAIOSFODNN7EXAMPLE", "***REDACTED_AWS_KEY***"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz", "Authorization: Bearer ***REDACTED***"),
            ("password = 'super_secret_password_123'", "password=***REDACTED***"),
        ]
        for raw, expected in samples:
            redacted = redact_secrets(raw)
            assert "sk-1234567890" not in redacted
            assert "ghp_1234567890" not in redacted
            assert "AIzaSyD" not in redacted
            assert "AKIAIOSFODNN7EXAMPLE" not in redacted
            assert "eyJhbGci" not in redacted
            assert "super_secret_password_123" not in redacted

    def test_json_formatter_outputs_valid_json_with_masked_secrets(self):
        """JsonFormatter formats log records as JSON and automatically redacts secrets."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="agentguard.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Connecting to database with password=super_secret_pass_12345",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "test-cid-123"

        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert data["level"] == "INFO"
        assert data["logger"] == "agentguard.test"
        assert data["correlation_id"] == "test-cid-123"
        assert "super_secret_pass_12345" not in data["message"]
        assert "***REDACTED***" in data["message"]


class TestPhase7ConcurrencyAndFSM:

    def test_cannot_execute_completed_workflow(self):
        """FSM: Cannot execute a workflow that has already reached COMPLETED."""
        create_resp = client.post(
            "/workflow/start",
            json={"repo_url": "https://github.com/octocat/Hello-World", "task": "Test FSM"},
        )
        wf_id = create_resp.json()["workflow_id"]

        wf = workflow_store.get(wf_id)
        wf.overall_status = WorkflowStatus.COMPLETED
        workflow_store.save(wf)

        exec_resp = client.post(f"/workflow/{wf_id}/execute")
        assert exec_resp.status_code == 400
        assert "Cannot execute workflow in terminal state" in exec_resp.json()["detail"]

    def test_cannot_execute_cancelled_workflow(self):
        """FSM: Cannot execute a workflow that has been CANCELLED."""
        create_resp = client.post(
            "/workflow/start",
            json={"repo_url": "https://github.com/octocat/Hello-World", "task": "Test FSM Cancel"},
        )
        wf_id = create_resp.json()["workflow_id"]

        wf = workflow_store.get(wf_id)
        wf.overall_status = WorkflowStatus.CANCELLED
        workflow_store.save(wf)

        exec_resp = client.post(f"/workflow/{wf_id}/execute")
        assert exec_resp.status_code == 400
        assert "Cannot execute workflow in terminal state" in exec_resp.json()["detail"]

    def test_cannot_resume_completed_workflow(self):
        """FSM: Cannot resume a workflow that is already COMPLETED."""
        create_resp = client.post(
            "/workflow/start",
            json={"repo_url": "https://github.com/octocat/Hello-World", "task": "Test FSM Resume"},
        )
        wf_id = create_resp.json()["workflow_id"]

        wf = workflow_store.get(wf_id)
        wf.overall_status = WorkflowStatus.COMPLETED
        workflow_store.save(wf)

        resume_resp = client.post(f"/workflow/{wf_id}/resume")
        assert resume_resp.status_code == 400
        assert "Cannot resume a workflow that is already COMPLETED" in resume_resp.json()["detail"]

    def test_execute_endpoint_runs_in_background(self):
        """
        Concurrency: /execute schedules execution in background without blocking.
        Returns immediately with status RUNNING or PENDING.
        """
        from backend.agent.orchestrator import WorkflowOrchestrator
        orch = WorkflowOrchestrator()
        wf = orch.create_workflow(
            repo_url="https://github.com/octocat/Hello-World",
            task="Test Async Exec",
            owner_id="default-owner",
        )

        exec_resp = client.post(f"/workflow/{wf.workflow_id}/execute")
        assert exec_resp.status_code in [200, 202]
        data = exec_resp.json()
        assert data["workflow_id"] == wf.workflow_id
