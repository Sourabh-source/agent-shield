import json
import logging
from typing import List, Optional, Tuple
from pydantic import BaseModel, ValidationError

from backend.config import settings
from backend.models.workflow import (
    ProjectAnalysis,
    StepDefinition,
    StepStatus,
    StepType,
)
from backend.tools.registry import tool_registry
from backend.tools.shell_tool import is_safe_command

logger = logging.getLogger("agentguard.planner")


class PlanOutput(BaseModel):
    steps: List[StepDefinition]


DEFAULT_MVP_STEPS = [
    {
        "id": "step_1",
        "type": StepType.CLONE_REPOSITORY.value,
        "name": "Clone repository",
        "tool": "git",
        "reason": "Target repository must be cloned to isolated local workspace before inspection",
        "description": "Clone the target repository to local workspace",
    },
    {
        "id": "step_2",
        "type": StepType.ANALYZE_PROJECT.value,
        "name": "Analyze project",
        "tool": "file",
        "reason": "Inspect workspace files to identify language, dependency manifests, and entry points",
        "description": "Detect languages, package managers, and configurations",
    },
    {
        "id": "step_3",
        "type": StepType.INSTALL_DEPENDENCIES.value,
        "name": "Install dependencies",
        "tool": "pip",
        "reason": "Install declared dependencies required for compiling and running the project",
        "description": "Install all required dependencies",
    },
    {
        "id": "step_4",
        "type": StepType.BUILD_PROJECT.value,
        "name": "Build project",
        "tool": "shell",
        "reason": "Compile source code and produce verified build artifacts",
        "description": "Compile or build the application",
    },
    {
        "id": "step_5",
        "type": StepType.RUN_TESTS.value,
        "name": "Run tests",
        "tool": "python",
        "reason": "Execute automated test suite to verify code correctness",
        "description": "Run the project test suite",
    },
    {
        "id": "step_6",
        "type": StepType.START_APPLICATION.value,
        "name": "Start application",
        "tool": "shell",
        "reason": "Launch application process in background to prepare for health check",
        "description": "Start the service in background for health check",
    },
    {
        "id": "step_7",
        "type": StepType.HEALTH_CHECK.value,
        "name": "Health check",
        "tool": "http",
        "reason": "Probe live HTTP endpoint to machine-verify service readiness",
        "description": "Verify application endpoint reachability",
    },
    {
        "id": "step_8",
        "type": StepType.FINAL_REPORT.value,
        "name": "Final report",
        "tool": "shell",
        "reason": "Synthesize verified evidence into final machine-checked audit report",
        "description": "Synthesize verified evidence into final status",
    },
]


def validate_plan_gate(steps: List[StepDefinition]) -> Tuple[bool, Optional[str]]:
    """
    Plan Validation Gate:
    Strictly verifies all planned steps against:
    - Pydantic schema
    - Registered tools in ToolRegistry
    - Command safety and forbidden patterns
    - Path traversal prevention
    """
    if not steps:
        return False, "Plan contains no steps"

    for step in steps:
        # 1. Validate tool if specified
        if step.tool:
            if not tool_registry.has(step.tool):
                return False, f"Unknown tool '{step.tool}' specified in step '{step.name}'"

        # 2. Validate command safety if specified
        if step.command:
            safe, reason = is_safe_command(step.command)
            if not safe:
                return False, f"Command safety violation in step '{step.name}': {reason}"

    return True, None


def create_deterministic_plan(
    repo_url: str,
    task: str,
    analysis: Optional[ProjectAnalysis] = None,
) -> List[StepDefinition]:
    """
    Deterministic fallback planner providing a guaranteed valid, structured workflow
    for the MVP project health task.
    """
    steps: List[StepDefinition] = []
    for item in DEFAULT_MVP_STEPS:
        step = StepDefinition(
            id=item["id"],
            type=item["type"],
            name=item["name"],
            tool=item.get("tool"),
            reason=item.get("reason"),
            description=item["description"],
            status=StepStatus.PENDING,
        )
        steps.append(step)

    if analysis:
        steps = update_plan_with_analysis(steps, analysis)

    return steps


def update_plan_with_analysis(
    steps: List[StepDefinition],
    analysis: ProjectAnalysis,
) -> List[StepDefinition]:
    """
    Dynamically refines planned steps with concrete tools, commands, and reasons
    discovered from the repository analysis.
    """
    is_node = analysis.language in ["javascript", "typescript"]
    is_python = analysis.language == "python"

    for step in steps:
        if step.type == StepType.INSTALL_DEPENDENCIES.value:
            if is_node:
                step.tool = "npm"
                step.command = analysis.install_command or "npm install"
                step.reason = f"Detected {analysis.package_manager} with package.json dependencies"
            elif is_python:
                step.tool = "pip"
                step.command = analysis.install_command or "pip install -r requirements.txt"
                step.reason = f"Detected Python project with {analysis.package_manager} package manager"
            else:
                step.tool = "shell"
                step.command = analysis.install_command or "echo 'No install step required'"
                step.reason = "Generic project structure; no specific dependency manager detected"
            step.description = f"Install dependencies using {analysis.package_manager}"

        elif step.type == StepType.BUILD_PROJECT.value:
            if is_node and analysis.build_command:
                step.tool = "npm"
                step.command = analysis.build_command
                step.reason = "package.json contains a build script"
            elif is_python and analysis.build_command:
                step.tool = "pip"
                step.command = analysis.build_command
                step.reason = "pyproject.toml or setup.py detected for build/editable installation"
            elif analysis.build_command:
                step.tool = "shell"
                step.command = analysis.build_command
                step.reason = "Custom build command detected"
            else:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "No explicit build command defined in project manifest"
            step.description = f"Build project ({analysis.language})"

        elif step.type == StepType.RUN_TESTS.value:
            if is_node and analysis.test_command:
                step.tool = "npm"
                step.command = analysis.test_command
                step.reason = "package.json contains a test script"
            elif is_python and analysis.test_command:
                step.tool = "python"
                step.command = analysis.test_command
                step.reason = f"Tests detected in repository; running via {analysis.test_command}"
            elif analysis.test_command:
                step.tool = "shell"
                step.command = analysis.test_command
                step.reason = "Custom test command detected"
            else:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "No tests detected in project files"
            step.description = f"Run tests ({analysis.test_command or 'none'})"

        elif step.type == StepType.START_APPLICATION.value:
            if is_node and analysis.start_command:
                step.tool = "npm"
                step.command = analysis.start_command
                step.reason = f"package.json provides start command: {analysis.start_command}"
            elif is_python and analysis.start_command:
                step.tool = "python"
                step.command = analysis.start_command
                step.reason = f"Entry point script detected: {analysis.start_command}"
            elif analysis.start_command:
                step.tool = "shell"
                step.command = analysis.start_command
                step.reason = "Custom start command detected"
            else:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "No entry point script found for service start"
            step.description = f"Start application ({analysis.start_command or 'none'})"

        elif step.type == StepType.HEALTH_CHECK.value:
            start_step = next((s for s in steps if s.type == StepType.START_APPLICATION.value), None)
            if start_step and start_step.status == StepStatus.NOT_APPLICABLE:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "Health check skipped: application start is not applicable"
                step.description = "Health check (not applicable)"
            else:
                step.tool = "http"
                step.command = analysis.health_check_url or "http://localhost:8000/health"
                step.reason = f"Probe HTTP readiness on {step.command}"
                step.description = f"Check {step.command}"

    return steps


def plan_workflow_with_gemini(repo_url: str, task: str) -> Optional[List[StepDefinition]]:
    """
    Queries Gemini LLM to generate structured workflow steps, validated via Pydantic.
    Falls back gracefully if LLM is unconfigured, unreachable, or returns invalid schema.
    """
    if not settings.GEMINI_API_KEY:
        logger.info("No GEMINI_API_KEY configured, skipping LLM planner.")
        return None

    prompt = f"""You are the AI Planner for AgentGuard.
Convert this task into an ordered JSON list of workflow steps for testing project health.
Repository: {repo_url}
Task: {task}

Supported step types:
- clone_repository
- analyze_project
- install_dependencies
- build_project
- run_tests
- start_application
- health_check
- final_report

Supported tools: git, shell, python, pip, npm, http, file

Output MUST be valid JSON adhering exactly to:
{{
  "steps": [
    {{
      "id": "step_1",
      "type": "clone_repository",
      "name": "Clone repository",
      "tool": "git",
      "reason": "Need to clone repo to local workspace",
      "description": "...",
      "command": null
    }}
  ]
}}
Do NOT wrap in markdown fences. Only raw JSON.
"""
    try:
        from google import genai
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        data = json.loads(text)
        validated_plan = PlanOutput.model_validate(data)
        logger.info("Successfully planned workflow using Gemini.")
        return validated_plan.steps
    except Exception as exc:
        logger.warning(f"Gemini planning failed or returned invalid JSON ({exc}). Using deterministic fallback.")
        return None


def generate_plan(
    repo_url: str,
    task: str,
    analysis: Optional[ProjectAnalysis] = None,
) -> List[StepDefinition]:
    """
    Primary planner entrypoint. Tries Gemini LLM first if configured;
    passes through Plan Validation Gate, falling back cleanly if validation fails.
    """
    steps = plan_workflow_with_gemini(repo_url, task)
    if steps:
        # Validate LLM output through Plan Validation Gate
        valid, gate_reason = validate_plan_gate(steps)
        if not valid:
            logger.warning(f"LLM plan failed validation gate: {gate_reason}. Reverting to fallback.")
            steps = None

    if not steps:
        steps = create_deterministic_plan(repo_url, task, analysis)

    # Double check final plan structure
    try:
        PlanOutput(steps=steps)
    except ValidationError as err:
        logger.error(f"Planner output validation failed: {err}. Reverting to fallback plan.")
        steps = create_deterministic_plan(repo_url, task, analysis)

    return steps
