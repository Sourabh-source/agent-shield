import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

def test_v1_start_with_api_key():
    response = client.post(
        "/v1/workflow/start",
        headers={"X-API-Key": "test-api-key"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    assert response.status_code == 201
    assert "workflow_id" in response.json()

def test_v1_start_without_api_key():
    response = client.post(
        "/v1/workflow/start",
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    assert response.status_code == 401

def test_legacy_start_works():
    response = client.post(
        "/workflow/start",
        headers={"X-API-Key": "test-api-key"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    assert response.status_code == 201

def test_idempotency_key_deduplication():
    # First call
    response1 = client.post(
        "/v1/workflow/start",
        headers={"X-API-Key": "test-api-key", "Idempotency-Key": "key-1"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    assert response1.status_code == 201
    wf1 = response1.json()["workflow_id"]

    # Second call with same key
    response2 = client.post(
        "/v1/workflow/start",
        headers={"X-API-Key": "test-api-key", "Idempotency-Key": "key-1"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    assert response2.status_code == 201
    wf2 = response2.json()["workflow_id"]

    assert wf1 == wf2

def test_different_idempotency_key_creates_new():
    response1 = client.post(
        "/v1/workflow/start",
        headers={"X-API-Key": "test-api-key", "Idempotency-Key": "key-2"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    wf1 = response1.json()["workflow_id"]

    response2 = client.post(
        "/v1/workflow/start",
        headers={"X-API-Key": "test-api-key", "Idempotency-Key": "key-3"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    wf2 = response2.json()["workflow_id"]

    assert wf1 != wf2

def test_missing_idempotency_key_creates_new():
    response1 = client.post(
        "/v1/workflow/start",
        headers={"X-API-Key": "test-api-key"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    wf1 = response1.json()["workflow_id"]

    response2 = client.post(
        "/v1/workflow/start",
        headers={"X-API-Key": "test-api-key"},
        json={"repo_url": "https://github.com/test/test", "task": "test task"}
    )
    wf2 = response2.json()["workflow_id"]

    assert wf1 != wf2
