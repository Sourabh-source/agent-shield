import pytest
from pydantic import ValidationError
from unittest.mock import patch

from backend.agent.planner import (
    create_deterministic_plan,
    generate_plan,
    plan_workflow_with_gemini,
    update_plan_with_analysis,
    PlanOutput,
)
from backend.models.workflow import ProjectAnalysis, StepDefinition, StepStatus, StepType


def test_deterministic_planner_produces_valid_steps():
    repo = "https://github.com/example/sample-repo"
    task = "Check whether this project can be built and run successfully."
    steps = create_deterministic_plan(repo, task)

    assert len(steps) == 8
    step_types = [s.type for s in steps]
    assert StepType.CLONE_REPOSITORY.value in step_types
    assert StepType.ANALYZE_PROJECT.value in step_types
    assert StepType.INSTALL_DEPENDENCIES.value in step_types
    assert StepType.BUILD_PROJECT.value in step_types
    assert StepType.RUN_TESTS.value in step_types
    assert StepType.START_APPLICATION.value in step_types
    assert StepType.HEALTH_CHECK.value in step_types
    assert StepType.FINAL_REPORT.value in step_types

    for s in steps:
        assert s.status == StepStatus.PENDING
        assert s.retries == 0


def test_plan_output_validation_rejects_invalid_structure():
    # Valid plan passes
    valid_step = StepDefinition(
        id="step_1",
        type="clone_repository",
        name="Clone",
    )
    plan = PlanOutput(steps=[valid_step])
    assert len(plan.steps) == 1

    # Invalid missing required fields raises ValidationError
    with pytest.raises(ValidationError):
        PlanOutput.model_validate({"steps": [{"id": 123}]})  # missing name and type


def test_update_plan_with_analysis():
    steps = create_deterministic_plan("https://github.com/example/repo", "test task")
    analysis = ProjectAnalysis(
        language="python",
        package_manager="pip",
        install_command="pip install -r requirements.txt",
        build_command="pip install -e .",
        test_command="pytest",
        start_command="python main.py",
        health_check_url="http://localhost:8000/health",
    )

    updated = update_plan_with_analysis(steps, analysis)
    step_map = {s.type: s for s in updated}

    assert step_map[StepType.INSTALL_DEPENDENCIES.value].command == "pip install -r requirements.txt"
    assert step_map[StepType.BUILD_PROJECT.value].command == "pip install -e ."
    assert step_map[StepType.RUN_TESTS.value].command == "pytest"
    assert step_map[StepType.START_APPLICATION.value].command == "python main.py"
    assert step_map[StepType.HEALTH_CHECK.value].command == "http://localhost:8000/health"


def test_generate_plan_falls_back_when_llm_fails():
    with patch("backend.agent.planner.plan_workflow_with_gemini", return_value=None):
        steps = generate_plan("https://github.com/example/repo", "test task")
        assert len(steps) == 8
        assert steps[0].type == StepType.CLONE_REPOSITORY.value
