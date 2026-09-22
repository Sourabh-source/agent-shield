import pytest
from backend.security.abuse_detection import detect_mining_activity, check_cpu_budget
from backend.middleware.auth import validate_api_key_format
from fastapi.testclient import TestClient
from backend.main import app

def test_crypto_miner_detection():
    # known patterns
    is_mining, msg = detect_mining_activity("output", "error", "./xmrig -o stratum+tcp://pool")
    assert is_mining
    assert "xmrig" in msg or "stratum+tcp" in msg

    # clean output
    is_mining, msg = detect_mining_activity("hello world", "", "echo hello")
    assert not is_mining

def test_cpu_budget_check():
    # excessive duration
    exceeded, msg = check_cpu_budget(400_000, 300_000)
    assert exceeded
    assert "300000" in msg
    
    # normal duration
    exceeded, msg = check_cpu_budget(100_000, 300_000)
    assert not exceeded

def test_api_key_format():
    assert validate_api_key_format("12345678901234567890")
    assert not validate_api_key_format("short")

def test_delete_account_unauthorized():
    client = TestClient(app)
    response = client.delete("/account")
    assert response.status_code == 401

def test_delete_account_authorized(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "REQUIRE_AUTH", False)
    client = TestClient(app)
    
    # We still need to mock request.state.owner_id because the endpoint requires it.
    # Without auth, owner_id defaults to "default-owner".
    # Wait, the endpoint expects request.state.owner_id. 
    # Let's mock the endpoint's check or just let it use the default.
    # Actually, the middleware sets it to "default-owner".
    response = client.delete("/account", headers={"Authorization": "Bearer test-key-1234567890123"})
    assert response.status_code == 200
    assert response.json()["status"] == "purged"
