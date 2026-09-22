from fastapi.testclient import TestClient

from backend.main import app
from backend.models.workflow import StepDefinition, VerificationResult, WorkflowStatus
from backend.agent.orchestrator import workflow_store, WorkflowOrchestrator

client = TestClient(app, headers={"X-API-Key": "test-api-key"})


def test_root_endpoint():
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "AgentGuard Backend"
    assert data["module"] == "AgentGuard - Orchestrator & Execution Engine"


def test_start_workflow_endpoint():
    payload = {
        "repo_url": "https://github.com/example/sample-python-project",
        "task": "Check project health",
    }
    resp = client.post("/workflow/start", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert "workflow_id" in data
    assert data["status"] == WorkflowStatus.PENDING.value


def test_start_workflow_empty_url_rejected():
    resp = client.post("/workflow/start", json={"repo_url": "", "task": "Check"})
    assert resp.status_code == 400


def test_get_workflow_status_and_events():
    # Setup a workflow in store
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/demo",
        task="Test API task",
    )
    workflow_id = wf.workflow_id

    # Test status endpoint
    resp = client.get(f"/workflow/{workflow_id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["workflow_id"] == workflow_id
    assert data["repository"] == "https://github.com/example/demo"
    assert "events" in data
    assert "steps" in data
    assert "overall_status" in data

    # Test events endpoint
    events_resp = client.get(f"/workflow/{workflow_id}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()
    assert len(events) >= 1
    assert events[0]["event_type"] == "WORKFLOW_STARTED"


def test_get_unknown_workflow_404():
    resp = client.get("/workflow/nonexistent_123/status")
    assert resp.status_code == 404


def test_external_verify_endpoint():
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/demo-verify",
        task="Verification integration test",
    )
    wf.steps = [
        StepDefinition(id="step_bld", type="build_project", name="Build project"),
    ]
    wf.current_step = "Build project"
    workflow_store.save(wf)

    # Verifier posts a PASS verification decision
    verif_payload = {
        "step_id": "step_bld",
        "verification_result": {
            "verified": True,
            "reason": "Machine check: build artifacts generated cleanly",
            "recovery_required": False,
            "retry_allowed": True,
        },
    }

    resp = client.post(f"/workflow/{wf.workflow_id}/verify", json=verif_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_status"] == "PASS"
    assert data["steps"][0]["status"] == "VERIFIED_SUCCESS"


def test_list_workflows():
    resp = client.get("/workflows")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
