import json
import logging
import os
from fastapi.testclient import TestClient

from backend.main import app
from backend.observability import JsonFormatter, correlation_id_ctx
from backend.metrics import workflow_total, security_violations, build_info
from backend.tools.shell_tool import execute_shell_command
from scripts.config_doctor import check_config

client = TestClient(app)

def test_prometheus_metrics_endpoint():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "agentguard_active_workflows" in response.text
    assert "agentguard_workflow_total" in response.text

def test_workflow_total_counter():
    # Record initial value
    initial_val = 0
    try:
        initial_val = workflow_total.labels(status='COMPLETED')._value.get()
    except:
        pass
    
    # Execute a workflow synchronously
    response = client.post(
        "/workflow/start",
        json={"repo_url": "https://github.com/test/repo", "task": "test", "dry_run": True},
        headers={"X-API-Key": "test-api-key"}
    )
    assert response.status_code == 201
    workflow_id = response.json()["workflow_id"]
    
    import time
    for _ in range(20):
        time.sleep(0.2)
        if workflow_total.labels(status='COMPLETED')._value.get() > initial_val:
            break
            
    final_val = workflow_total.labels(status='COMPLETED')._value.get()
    
    # Debug: Check actual status in db
    res = client.get(f"/workflow/{workflow_id}/status", headers={"X-API-Key": "test-api-key"})
    print("STATUS:", res.json())
    
    assert final_val > initial_val

def test_security_violations_counter():
    initial_val = 0
    try:
        initial_val = security_violations.labels(violation_type='command_injection')._value.get()
    except:
        pass
        
    result = execute_shell_command("rm -rf /")
    assert result.exit_code == 126
    
    final_val = security_violations.labels(violation_type='command_injection')._value.get()
    assert final_val > initial_val

def test_config_doctor_default_hmac(monkeypatch, capsys):
    monkeypatch.setenv("VERIFY_HMAC_SECRET", "agentguard-hmac-secret-key-prod")
    result = check_config()
    assert result == 1
    captured = capsys.readouterr()
    assert "VERIFY_HMAC_SECRET uses default value" in captured.out

def test_config_doctor_valid_config(monkeypatch):
    monkeypatch.setenv("VERIFY_HMAC_SECRET", "secure-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setenv("API_KEYS", "prod-key")
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    result = check_config()
    assert result == 0

def test_json_formatter_correlation_id():
    formatter = JsonFormatter()
    record = logging.LogRecord("test", logging.INFO, "test.py", 10, "Test message", None, None)
    
    token = correlation_id_ctx.set("test-corr-id")
    formatted = formatter.format(record)
    correlation_id_ctx.reset(token)
    
    data = json.loads(formatted)
    assert data["correlation_id"] == "test-corr-id"
    assert data["message"] == "Test message"

def test_build_info_populated():
    response = client.get("/metrics")
    assert "agentguard_build_info" in response.text
