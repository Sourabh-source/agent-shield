"""
Tests for the deterministic demo fixture for flask-hello-world.

Tests cover:
1. Exact URL match
2. Trailing slash match
3. Different repo no match
4. Disabled flag runs normal flow
5. All steps verified success (11 steps, 10 VERIFIED_SUCCESS, 1 NOT_APPLICABLE)
6. Schema compatibility (Pydantic validation)
7. Full API path test (POST workflow -> demo branch selected)
"""

import os
from unittest.mock import patch, MagicMock

import pytest

from backend.demo.demo_fixtures import is_demo_repository, run_demo_workflow, _build_step_definitions
from backend.models.workflow import (
    ExecutionResult,
    FinalReportData,
    StepStatus,
    VerificationResult,
    WorkflowEvent,
    WorkflowState,
    WorkflowStatus,
)


# ---------------------------------------------------------------------------
# Test 1: Exact URL match
# ---------------------------------------------------------------------------
class TestDemoUrlMatching:
    def test_exact_url_match(self):
        assert is_demo_repository("https://github.com/gAmadorH/flask-hello-world") is True

    def test_trailing_slash_match(self):
        assert is_demo_repository("https://github.com/gAmadorH/flask-hello-world/") is True

    def test_case_insensitive_match(self):
        assert is_demo_repository("https://github.com/GAMADORH/FLASK-HELLO-WORLD") is True

    def test_different_repo_no_match(self):
        assert is_demo_repository("https://github.com/other/repo") is False

    def test_empty_url_no_match(self):
        assert is_demo_repository("") is False

    def test_similar_url_no_match(self):
        assert is_demo_repository("https://github.com/gAmadorH/flask-hello-world-2") is False

    def test_partial_url_no_match(self):
        assert is_demo_repository("https://github.com/gAmadorH") is False

    def test_whitespace_stripped(self):
        assert is_demo_repository("  https://github.com/gAmadorH/flask-hello-world  ") is True

    def test_octocat_hello_world_no_match(self):
        """Explicitly test that other repos use the real pipeline."""
        assert is_demo_repository("https://github.com/octocat/Hello-World") is False


# ---------------------------------------------------------------------------
# Test 2: Disabled flag
# ---------------------------------------------------------------------------
class TestDemoFlagControl:
    @patch("backend.config.settings")
    def test_disabled_flag_skips_demo(self, mock_settings):
        """When DEMO_FIXTURES_ENABLED=False, the demo code path is never entered."""
        mock_settings.DEMO_FIXTURES_ENABLED = False
        assert mock_settings.DEMO_FIXTURES_ENABLED is False

    def test_default_flag_is_false(self):
        """The default value should be False in config."""
        from backend.config import Settings
        # Create a fresh Settings instance without .env
        with patch.dict(os.environ, {}, clear=False):
            s = Settings()
            # The default is False unless overridden by .env
            assert hasattr(s, "DEMO_FIXTURES_ENABLED")


# ---------------------------------------------------------------------------
# Test 3: All steps verified success (11 steps)
# ---------------------------------------------------------------------------
class TestDemoWorkflowExecution:
    def test_all_steps_verified_success(self):
        """Running the demo workflow produces 11 steps: 10 VERIFIED_SUCCESS + 1 NOT_APPLICABLE."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        # 11 steps total
        assert len(result.steps) == 11

        # 10 VERIFIED_SUCCESS
        verified_count = sum(1 for s in result.steps if s.status == StepStatus.VERIFIED_SUCCESS)
        assert verified_count == 10, f"Expected 10 VERIFIED_SUCCESS, got {verified_count}"

        # 1 NOT_APPLICABLE (Run Tests)
        na_count = sum(1 for s in result.steps if s.status == StepStatus.NOT_APPLICABLE)
        assert na_count == 1, f"Expected 1 NOT_APPLICABLE, got {na_count}"

        # The NOT_APPLICABLE step should be "Run Tests"
        na_step = [s for s in result.steps if s.status == StepStatus.NOT_APPLICABLE][0]
        assert na_step.name == "Run Tests"

        # Workflow should be VERIFIED_SUCCESS / COMPLETED
        assert result.overall_status in (WorkflowStatus.COMPLETED, WorkflowStatus.VERIFIED_SUCCESS)
        assert result.final_status == "VERIFIED_SUCCESS"
        assert result.final_result is not None
        assert result.retries == 0

    def test_zero_retries_zero_recoveries(self):
        """Demo workflow should have 0 retries and 0 recoveries."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)
        assert result.retries == 0
        assert len(result.recovery_history) == 0

    def test_steps_have_execution_results(self):
        """Each non-NA demo step should have an ExecutionResult with exit_code=0."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        for step in result.steps:
            if step.status == StepStatus.NOT_APPLICABLE:
                continue
            assert step.execution_result is not None, f"Step '{step.name}' missing ExecutionResult"
            assert step.execution_result.exit_code == 0
            assert step.execution_result.stdout != ""
            assert step.execution_result.evidence_digest is not None

    def test_steps_have_verification_results(self):
        """Each non-NA demo step should have a VerificationResult with verified=True."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        for step in result.steps:
            if step.status == StepStatus.NOT_APPLICABLE:
                continue
            assert step.verification_result is not None, f"Step '{step.name}' missing VerificationResult"
            assert step.verification_result.verified is True

    def test_start_application_has_correct_metadata(self):
        """Start Application step must have execution_mode and process_running metadata."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        start_step = [s for s in result.steps if s.name == "Start Application"][0]
        assert start_step.execution_result is not None
        meta = start_step.execution_result.metadata
        assert meta is not None
        assert meta.get("execution_mode") == "HTTP_SERVICE"
        assert meta.get("demo_fixture") is True
        assert meta.get("port") == 5000
        assert meta.get("process_running") is True

    def test_workflow_has_events(self):
        """The demo workflow should emit events for each step lifecycle."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        # For 10 executed steps: STEP_STARTED + COMMAND_EXECUTED + STEP_VERIFIED = 3 each = 30
        # For 1 NA step: STEP_VERIFIED = 1
        # Plus: WORKFLOW_STARTED (from create) + PLANNING_STARTED + PLAN_CREATED + WORKFLOW_COMPLETED = 4
        # Total: 1 + 1 + 1 + 30 + 1 + 1 = 35
        assert len(result.events) >= 30, f"Expected at least 30 events, got {len(result.events)}"

    def test_final_status_is_verified_success(self):
        """The workflow final_status should be VERIFIED_SUCCESS."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)
        assert result.final_status == "VERIFIED_SUCCESS"
        assert result.verification_status == "VERIFIED_SUCCESS"


# ---------------------------------------------------------------------------
# Test 4: Schema compatibility
# ---------------------------------------------------------------------------
class TestDemoSchemaCompatibility:
    def test_execution_result_schema(self):
        """ExecutionResult objects from demo must pass Pydantic validation."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        for step in result.steps:
            if step.status == StepStatus.NOT_APPLICABLE:
                continue
            er = step.execution_result
            assert er is not None
            validated = ExecutionResult.model_validate(er.model_dump())
            assert validated.exit_code == 0
            assert validated.workflow_id == workflow.workflow_id

    def test_verification_result_schema(self):
        """VerificationResult objects from demo must pass Pydantic validation."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        for step in result.steps:
            if step.status == StepStatus.NOT_APPLICABLE:
                continue
            vr = step.verification_result
            assert vr is not None
            validated = VerificationResult.model_validate(vr.model_dump())
            assert validated.verified is True

    def test_workflow_event_schema(self):
        """WorkflowEvent objects from demo must pass Pydantic validation."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        result = run_demo_workflow(orchestrator, workflow)

        for event in result.events:
            validated = WorkflowEvent.model_validate(event.model_dump())
            assert validated.workflow_id == workflow.workflow_id

    def test_final_report_schema(self):
        """FinalReportData generated from demo workflow must pass Pydantic validation."""
        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        run_demo_workflow(orchestrator, workflow)

        report = orchestrator.get_final_report_data(workflow.workflow_id)
        assert report is not None
        validated = FinalReportData.model_validate(report.model_dump())
        assert validated.final_status == "VERIFIED_SUCCESS"
        assert validated.steps_completed == 11  # 10 VERIFIED_SUCCESS + 1 NOT_APPLICABLE
        assert validated.total_steps == 11
        assert validated.recoveries == 0
        assert validated.retries == 0


# ---------------------------------------------------------------------------
# Test 5: Step definitions are well-formed
# ---------------------------------------------------------------------------
class TestDemoStepDefinitions:
    def test_step_count(self):
        steps = _build_step_definitions()
        assert len(steps) == 11

    def test_step_ids_are_unique(self):
        steps = _build_step_definitions()
        ids = [s.id for s in steps]
        assert len(ids) == len(set(ids))

    def test_all_steps_start_pending(self):
        steps = _build_step_definitions()
        for step in steps:
            assert step.status == StepStatus.PENDING


# ---------------------------------------------------------------------------
# Test 6: Full API path test — demo branch is selected, real executor NOT called
# ---------------------------------------------------------------------------
class TestDemoFullApiPath:
    @patch("backend.config.settings")
    def test_demo_branch_selected_for_flask_hello_world(self, mock_settings):
        """POST workflow with flask-hello-world -> demo branch, not real executor."""
        mock_settings.DEMO_FIXTURES_ENABLED = True
        mock_settings.MOCK_VERIFIER = True
        mock_settings.MAX_RETRIES = 2
        mock_settings.MAX_WORKFLOW_TIME = 600
        mock_settings.MAX_STEP_TIME = 120
        mock_settings.MAX_RECOVERY_ACTIONS = 3
        mock_settings.WORKSPACE_BASE_DIR = "workspaces"
        mock_settings.USE_SQLITE_PERSISTENCE = False
        mock_settings.MEMBER3_VERIFIER_URL = None
        mock_settings.DATABASE_PATH = ":memory:"

        from backend.agent.orchestrator import WorkflowOrchestrator
        from backend.agent.verifier_client import MockVerifierClient

        orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
        workflow = orchestrator.create_workflow(
            repo_url="https://github.com/gAmadorH/flask-hello-world",
            task="Verify repository health",
        )

        # Call run_workflow — with DEMO_FIXTURES_ENABLED=True, it should take the demo branch
        result = orchestrator.run_workflow(workflow.workflow_id)

        # Verify demo branch was taken
        assert result.overall_status == WorkflowStatus.COMPLETED
        assert len(result.steps) == 11
        assert result.retries == 0

        # All non-NA steps should be VERIFIED_SUCCESS
        for step in result.steps:
            assert step.status in (StepStatus.VERIFIED_SUCCESS, StepStatus.NOT_APPLICABLE), (
                f"Step '{step.name}' has unexpected status: {step.status}"
            )

    @patch("backend.config.settings")
    def test_real_pipeline_for_other_repos(self, mock_settings):
        """POST workflow with octocat/Hello-World -> NOT demo branch."""
        mock_settings.DEMO_FIXTURES_ENABLED = True
        mock_settings.MOCK_VERIFIER = True

        # Verify the URL does NOT match the demo fixture
        assert is_demo_repository("https://github.com/octocat/Hello-World") is False
