"""
Regression Test Suite for the 3 Targeted Hardening Fixes:
1. Fix 1: Explicit Real Verifier vs Mock Verifier Config
2. Fix 2: Strengthen Workspace Path Containment
3. Fix 3: Proper Member 3 Verification Callback / Recovery Flow
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app
from backend.agent.verifier_client import (
    MockVerifierClient,
    HttpVerifierClient,
    UnavailableVerifierClient,
    get_verifier_client,
    VerificationClient,
)
from backend.tools.shell_tool import is_safe_command, check_path_containment
from backend.tools.file_tool import FileTool
from backend.models.workflow import (
    ExecutionResult,
    VerificationResult,
    StepDefinition,
    StepStatus,
    WorkflowState,
    WorkflowStatus,
)
from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store

client = TestClient(app, headers={"X-API-Key": "test-api-key"})


# =========================================================================
# FIX 1 — EXPLICIT REAL VERIFIER vs MOCK VERIFIER CONFIG
# =========================================================================

def test_fix1_mock_verifier_selected_when_mock_verifier_true():
    """Case A: MOCK_VERIFIER=true -> MockVerifierClient selected."""
    with patch.object(settings, 'MOCK_VERIFIER', True):
        verifier = get_verifier_client()
        assert isinstance(verifier, MockVerifierClient)


def test_fix1_http_verifier_selected_when_mock_false_and_url_present():
    """Case B: MOCK_VERIFIER=false + valid MEMBER3_VERIFIER_URL -> HttpVerifierClient selected."""
    with patch.object(settings, 'MOCK_VERIFIER', False),          patch.object(settings, 'MEMBER3_VERIFIER_URL', 'http://127.0.0.1:8080/verify'):
        verifier = get_verifier_client()
        assert isinstance(verifier, HttpVerifierClient)
        assert verifier.endpoint_url == 'http://127.0.0.1:8080/verify'


def test_fix1_unavailable_verifier_selected_when_mock_false_and_url_empty():
    """Case C: MOCK_VERIFIER=false + empty MEMBER3_VERIFIER_URL -> UnavailableVerifierClient."""
    with patch.object(settings, 'MOCK_VERIFIER', False),          patch.object(settings, 'MEMBER3_VERIFIER_URL', ''):
        verifier = get_verifier_client()
        assert isinstance(verifier, UnavailableVerifierClient)

    with patch.object(settings, 'MOCK_VERIFIER', False),          patch.object(settings, 'MEMBER3_VERIFIER_URL', None):
        verifier = get_verifier_client()
        assert isinstance(verifier, UnavailableVerifierClient)


def test_fix1_unavailable_verifier_prevents_verified_success():
    """Case C runtime: Workflow must enter VERIFICATION_UNAVAILABLE and never claim VERIFIED_SUCCESS."""
    with patch.object(settings, 'MOCK_VERIFIER', False),          patch.object(settings, 'MEMBER3_VERIFIER_URL', None):
        orchestrator = WorkflowOrchestrator()
        assert isinstance(orchestrator.verifier_client, UnavailableVerifierClient)

        wf = orchestrator.create_workflow(repo_url='https://github.com/example/repo', task='Test health')
        wf.steps = [
            StepDefinition(id='s1', type='shell_command', name='build', command='echo building'),
        ]
        workflow_store.save(wf)

        finished = orchestrator.run_workflow(wf.workflow_id)

        assert finished.overall_status == WorkflowStatus.VERIFICATION_UNAVAILABLE
        assert finished.steps[0].status != StepStatus.VERIFIED_SUCCESS
        assert 'VERIFIED SUCCESS' not in (finished.final_result or '')
        assert 'unavailable' in (finished.final_result or '').lower()


def test_fix1_real_verifier_response_false_triggers_recovery_or_failure():
    """Case D: Real verifier response verified=false -> workflow must enter recovery/failure handling."""
    class RealVerifierMock(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason='Dependency check failed: missing libpq-dev',
                recovery_required=True,
                recovery_action='echo installing libpq-dev',
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=RealVerifierMock(), max_retries=1)
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/real', task='Check build')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='build', command='echo build'),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert finished.retries == 1
    assert len(finished.recovery_history) >= 1
    assert finished.steps[0].status == StepStatus.FAILED


# =========================================================================
# FIX 2 — STRENGTHEN WORKSPACE PATH CONTAINMENT
# =========================================================================

def test_fix2_normal_file_inside_workspace_allowed():
    """1. Normal file inside workspace -> ALLOW."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir).resolve()
        contained, err = check_path_containment('src/main.py', ws)
        assert contained is True
        assert err is None

        safe, reason = is_safe_command('cat src/main.py', cwd=str(ws))
        assert safe is True


def test_fix2_sibling_path_blocked():
    """2. ../sibling -> BLOCK."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / 'workspace_1'
        ws.mkdir()
        contained, err = check_path_containment('../sibling', ws)
        assert contained is False
        assert 'traversal' in err.lower()

        safe, reason = is_safe_command('cat ../sibling', cwd=str(ws))
        assert safe is False
        assert 'traversal' in reason.lower()


def test_fix2_outside_parent_path_blocked():
    """3. ../../outside -> BLOCK."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / 'sub' / 'workspace_1'
        ws.mkdir(parents=True)
        contained, err = check_path_containment('../../outside', ws)
        assert contained is False
        assert 'traversal' in err.lower()

        safe, reason = is_safe_command('cat ../../outside', cwd=str(ws))
        assert safe is False
        assert 'traversal' in reason.lower()


def test_fix2_absolute_outside_path_blocked():
    """4. Absolute outside path -> BLOCK."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / 'workspace_1'
        ws.mkdir()
        outside_path = str(Path(tempfile.gettempdir()).resolve() / 'outside_secrets.txt')

        contained, err = check_path_containment(outside_path, ws)
        assert contained is False
        assert 'traversal' in err.lower()

        safe, reason = is_safe_command(f'cat "{outside_path}"', cwd=str(ws))
        assert safe is False
        assert 'traversal' in reason.lower()


def test_fix2_normalized_traversal_blocked():
    """5. Normalized traversal (e.g. src/../../other) -> BLOCK."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir).resolve()
        contained, err = check_path_containment('src/../../escape', ws)
        assert contained is False
        assert 'traversal' in err.lower()

        # Mixed slashes
        contained_mixed, err_mixed = check_path_containment('src/..\\..\\escape', ws)
        assert contained_mixed is False
        assert 'traversal' in err_mixed.lower()


def test_fix2_legitimate_nested_path_allowed():
    """6. Legitimate nested path -> ALLOW."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir).resolve()
        contained, err = check_path_containment('src/components/deep/widget.py', ws)
        assert contained is True
        assert err is None

        safe, reason = is_safe_command('python src/components/deep/widget.py', cwd=str(ws))
        assert safe is True


def test_fix2_file_tool_enforces_path_containment():
    """FileTool validation and execution blocks sibling and outside paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / 'wf_ws'
        ws.mkdir()
        tool = FileTool()

        # Sibling access blocked in validation
        valid, err = tool.validate('cat ../sibling_file', cwd=str(ws))
        assert valid is False
        assert 'traversal' in err.lower()

        # Outside file blocked in execution
        res = tool.execute('read ../sibling_file', cwd=str(ws), workflow_id='w1')
        assert res.exit_code != 0
        assert 'traversal' in res.stderr.lower()


# =========================================================================
# FIX 3 — PROPER MEMBER 3 VERIFICATION CALLBACK / RECOVERY FLOW
# =========================================================================

def test_fix3_callback_pass_marks_verified_and_continues():
    """Case A: verified=true marks step verified, completes or continues without recovery."""
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/cb-pass', task='Callback pass test')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='compile', command='echo compile', status=StepStatus.RUNNING),
    ]
    wf.current_step = 'compile'
    workflow_store.save(wf)

    resp = client.post(
        f'/workflow/{wf.workflow_id}/verify',
        json={
            'step_id': 's1',
            'verification_result': {
                'verified': True,
                'reason': 'Artifact validated by Member 3 machine check',
                'recovery_required': False,
                'retry_allowed': False,
            }
        }
    )
    assert resp.status_code == 200
    state = resp.json()
    assert state['steps'][0]['status'] == StepStatus.VERIFIED_SUCCESS.value
    assert state['overall_status'] == WorkflowStatus.COMPLETED.value
    assert state['retries'] == 0


def test_fix3_callback_fail_with_recovery_triggers_recovery():
    """Case B: verified=false with recovery_required=true executes recovery and retries step."""
    orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient(), max_retries=2)
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/cb-heal', task='Callback heal test')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='build', command='echo build succeeded', status=StepStatus.RUNNING),
    ]
    wf.current_step = 'build'
    workflow_store.save(wf)

    # Member 3 posts verification failure with recovery action
    resp = client.post(
        f'/workflow/{wf.workflow_id}/verify',
        json={
            'step_id': 's1',
            'verification_result': {
                'verified': False,
                'reason': 'Missing dependency libfoo',
                'recovery_required': True,
                'recovery_action': 'echo installing libfoo',
                'retry_allowed': True,
            }
        }
    )
    assert resp.status_code == 200
    state = resp.json()
    assert state['retries'] == 1
    assert len(state['recovery_history']) == 1
    assert state['recovery_history'][0]['action'] == 'echo installing libfoo'
    assert state['steps'][0]['status'] == StepStatus.VERIFIED_SUCCESS.value
    assert state['overall_status'] == WorkflowStatus.COMPLETED.value


def test_fix3_callback_retry_is_bounded():
    """Case B2: Repeated verification failures exhaust retries and halt at VERIFIED_FAILURE."""
    class AlwaysFailVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason='Still failing verification check',
                recovery_required=True,
                recovery_action='echo trying fix',
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=AlwaysFailVerifier(), max_retries=1)
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/cb-bound', task='Callback bounded test')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='test_step', command='echo test', status=StepStatus.RUNNING),
    ]
    wf.current_step = 'test_step'
    workflow_store.save(wf)

    with patch("backend.api.workflow.WorkflowOrchestrator", return_value=orchestrator):
        resp = client.post(
            f'/workflow/{wf.workflow_id}/verify',
            json={
                'step_id': 's1',
                'verification_result': {
                    'verified': False,
                    'reason': 'Step failed machine check',
                    'recovery_required': True,
                    'recovery_action': 'echo trying fix',
                    'retry_allowed': True,
                }
            }
        )
    assert resp.status_code == 200
    state = resp.json()
    assert state['overall_status'] == WorkflowStatus.VERIFIED_FAILURE.value
    assert state['steps'][0]['status'] == StepStatus.FAILED.value
    assert state['retries'] == 1


def test_fix3_callback_failed_verification_never_produces_success():
    """Case C: verified=false with recovery_required=false halts with failure, never claims success."""
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/cb-fail', task='Callback failure test')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='security_check', command='echo clean', status=StepStatus.RUNNING),
    ]
    wf.current_step = 'security_check'
    workflow_store.save(wf)

    resp = client.post(
        f'/workflow/{wf.workflow_id}/verify',
        json={
            'step_id': 's1',
            'verification_result': {
                'verified': False,
                'reason': 'Security vulnerability detected in output artifacts',
                'recovery_required': False,
                'retry_allowed': True,
            }
        }
    )
    assert resp.status_code == 200
    state = resp.json()
    assert state['overall_status'] == WorkflowStatus.VERIFIED_FAILURE.value
    assert state['steps'][0]['status'] == StepStatus.FAILED.value
    assert 'VERIFIED SUCCESS' not in (state['final_result'] or '')


def test_fix3_callback_retry_disallowed_produces_terminal_failure():
    """Case D: verified=false with retry_allowed=false halts immediately with no retry."""
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/cb-noretry', task='Callback noretry test')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='deploy', command='echo deploy', status=StepStatus.RUNNING),
    ]
    wf.current_step = 'deploy'
    workflow_store.save(wf)

    resp = client.post(
        f'/workflow/{wf.workflow_id}/verify',
        json={
            'step_id': 's1',
            'verification_result': {
                'verified': False,
                'reason': 'Production lock active: manual intervention required',
                'recovery_required': True,
                'recovery_action': 'echo unlock',
                'retry_allowed': False,
            }
        }
    )
    assert resp.status_code == 200
    state = resp.json()
    assert state['overall_status'] == WorkflowStatus.VERIFIED_FAILURE.value
    assert state['retries'] == 0


def test_fix3_callback_verifier_unavailable_produces_verification_unavailable():
    """Case E: Verifier unavailable callback transitions to VERIFICATION_UNAVAILABLE."""
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/cb-unavail', task='Callback unavail test')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='build', command='echo build', status=StepStatus.RUNNING),
    ]
    wf.current_step = 'build'
    workflow_store.save(wf)

    resp = client.post(
        f'/workflow/{wf.workflow_id}/verify',
        json={
            'step_id': 's1',
            'verification_result': {
                'verified': False,
                'reason': 'Connection refused by Member 3 host',
                'recovery_required': False,
                'retry_allowed': False,
                'metadata': {'verifier_unavailable': True},
            }
        }
    )
    assert resp.status_code == 200
    state = resp.json()
    assert state['overall_status'] == WorkflowStatus.VERIFICATION_UNAVAILABLE.value
    assert state['steps'][0]['status'] == StepStatus.FAILED.value
    assert 'VERIFIED SUCCESS' not in (state['final_result'] or '')


def test_fix3_callback_successful_retry_requires_new_verification():
    """Confirmation that after recovery execution, step is NOT verified until re-verified."""
    verification_calls = []

    class CountingVerifier(VerificationClient):
        def verify(self, execution_result: ExecutionResult) -> VerificationResult:
            verification_calls.append(execution_result)
            if len(verification_calls) == 1:
                return VerificationResult(
                    verified=True,
                    reason='Second verification after recovery passed',
                )
            return VerificationResult(verified=False, reason='Unverified')

    orchestrator = WorkflowOrchestrator(verifier_client=CountingVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(repo_url='https://github.com/example/cb-twostep', task='Twostep test')
    wf.steps = [
        StepDefinition(id='s1', type='shell_command', name='build', command='echo retry_target', status=StepStatus.RUNNING),
    ]
    wf.current_step = 'build'
    workflow_store.save(wf)

    # Initial external callback fails with recovery requested
    with patch("backend.api.workflow.WorkflowOrchestrator", return_value=orchestrator):
        resp = client.post(
            f'/workflow/{wf.workflow_id}/verify',
            json={
                'step_id': 's1',
                'verification_result': {
                    'verified': False,
                    'reason': 'Build artifact missing checksum',
                    'recovery_required': True,
                    'recovery_action': 'echo re-generating checksum',
                    'retry_allowed': True,
                }
            }
        )
    assert resp.status_code == 200
    assert len(verification_calls) == 1
    state = resp.json()
    assert state['steps'][0]['status'] == StepStatus.VERIFIED_SUCCESS.value
    assert state['overall_status'] == WorkflowStatus.COMPLETED.value
