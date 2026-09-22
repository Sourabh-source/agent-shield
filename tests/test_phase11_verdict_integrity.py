import pytest
import copy
from backend.audit.hash_chain import HashChain
from backend.audit.signing import VerdictSigner
from backend.models.reason_codes import ReasonCode
from backend.agent.verifier_client import DeterministicEvidenceVerifier
from backend.models.workflow import ExecutionResult, StepType
import tempfile
import os

def test_hash_chain_valid():
    chain = HashChain()
    events = []
    
    for i in range(3):
        data = {"event": i}
        event_hash = chain.append(data)
        data_with_meta = data.copy()
        data_with_meta["metadata"] = {
            "previous_hash": chain._previous_hash if i > 0 else "GENESIS",
            "event_hash": event_hash
        }
        # Actually in append we do previous_hash = event_hash.
        events.append(data_with_meta)
        
    # Wait, in orchestrated code we did:
    chain = HashChain()
    events = []
    prev_hash = chain._previous_hash
    for i in range(3):
        event = {"name": f"Event {i}"}
        h = chain.append(event)
        event["metadata"] = {"previous_hash": prev_hash, "event_hash": h}
        events.append(event)
        prev_hash = h
        
    is_valid, msg = HashChain.verify_chain(events)
    assert is_valid, f"Chain should be valid: {msg}"

def test_hash_chain_insertion():
    chain = HashChain()
    events = []
    prev_hash = chain._previous_hash
    for i in range(3):
        event = {"name": f"Event {i}"}
        h = chain.append(event)
        event["metadata"] = {"previous_hash": prev_hash, "event_hash": h}
        events.append(event)
        prev_hash = h
        
    # Insert a malicious event
    events.insert(1, {"name": "Malicious", "metadata": {"previous_hash": events[0]["metadata"]["event_hash"], "event_hash": "dummy"}})
    is_valid, msg = HashChain.verify_chain(events)
    assert not is_valid

def test_hash_chain_deletion():
    chain = HashChain()
    events = []
    prev_hash = chain._previous_hash
    for i in range(3):
        event = {"name": f"Event {i}"}
        h = chain.append(event)
        event["metadata"] = {"previous_hash": prev_hash, "event_hash": h}
        events.append(event)
        prev_hash = h
        
    events.pop(1)
    is_valid, msg = HashChain.verify_chain(events)
    assert not is_valid

def test_hash_chain_modification():
    chain = HashChain()
    events = []
    prev_hash = chain._previous_hash
    for i in range(3):
        event = {"name": f"Event {i}"}
        h = chain.append(event)
        event["metadata"] = {"previous_hash": prev_hash, "event_hash": h}
        events.append(event)
        prev_hash = h
        
    events[1]["name"] = "Modified"
    is_valid, msg = HashChain.verify_chain(events)
    assert not is_valid

def test_verdict_signing():
    signer = VerdictSigner()
    verdict = {"status": "VERIFIED", "verified": True}
    sig = signer.sign(verdict)
    
    assert signer.verify(verdict, sig)
    
    # Forged
    verdict["verified"] = False
    assert not signer.verify(verdict, sig)

def test_reason_codes_and_verifier_version():
    verifier = DeterministicEvidenceVerifier()
    
    # Test valid execution
    res = ExecutionResult(workflow_id="1", step="Test", exit_code=0, command="pytest", stdout="1 passed")
    verdict = verifier.verify(res)
    assert verdict.reason_code == ReasonCode.EXIT_ZERO_CLEAN
    assert verdict.metadata.get("verifier_version") == "2.0.0"
    
    # Test failure execution
    res2 = ExecutionResult(workflow_id="1", step="Test", exit_code=1, command="pytest", stdout="1 failed")
    verdict2 = verifier.verify(res2)
    assert verdict2.reason_code == ReasonCode.EXIT_NONZERO
    assert verdict2.metadata.get("verifier_version") == "2.0.0"

    # Test build error
    res3 = ExecutionResult(workflow_id="1", step="Build", exit_code=1, command="npm run build", stdout="SyntaxError:")
    verdict3 = verifier.verify(res3)
    assert verdict3.reason_code == ReasonCode.BUILD_ERROR_DETECTED
    assert verdict3.metadata.get("verifier_version") == "2.0.0"
