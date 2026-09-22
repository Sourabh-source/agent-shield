import json
import logging
from typing import List, Optional, Tuple
from pydantic import BaseModel, ValidationError

from backend.config import settings
from backend.models.workflow import (
    ExecutionMode,
    ProjectAnalysis,
    ProjectManifest,
    StepDefinition,
    StepStatus,
    StepType,
)
from backend.tools.registry import tool_registry
from backend.tools.shell_tool import is_safe_command

logger = logging.getLogger("agentguard.planner")


class PlanOutput(BaseModel):
    steps: List[StepDefinition]


STEP_BUDGETS = {
    "clone_repository": 60,
    "analyze_project": 30,
    "install_dependencies_normal": 300,
    "install_dependencies_heavy_ml": 600,
    "build_project": 300,
    "compile_project": 120,
    "import_check": 60,
    "execute_notebook": 300,
    "verify_outputs": 60,
    "run_tests": 300,
    "start_application": 120,
    "health_check": 30,
    "smoke_test": 60,
    "final_report": 30,
}


DEFAULT_MVP_STEPS = [
    {
        "id": "step_1",
        "type": StepType.CLONE_REPOSITORY.value,
        "name": "Clone repository",
        "tool": "git",
        "reason": "Target repository must be cloned to isolated local workspace before inspection",
        "description": "Clone the target repository to local workspace",
        "timeout_seconds": 60,
    },
    {
        "id": "step_2",
        "type": StepType.ANALYZE_PROJECT.value,
        "name": "Analyze project",
        "tool": "file",
        "reason": "Inspect workspace files to identify language, dependency manifests, and entry points",
        "description": "Detect languages, package managers, and configurations",
        "timeout_seconds": 30,
    },
    {
        "id": "step_3",
        "type": StepType.INSTALL_DEPENDENCIES.value,
        "name": "Install dependencies",
        "tool": "pip",
        "reason": "Install declared dependencies required for compiling and running the project",
        "description": "Install all required dependencies",
        "timeout_seconds": 300,
    },
    {
        "id": "step_4",
        "type": StepType.BUILD_PROJECT.value,
        "name": "Build project",
        "tool": "shell",
        "reason": "Compile source code and produce verified build artifacts",
        "description": "Compile or build the application",
        "timeout_seconds": 300,
    },
    {
        "id": "step_5",
        "type": StepType.RUN_TESTS.value,
        "name": "Run tests",
        "tool": "python",
        "reason": "Execute automated test suite to verify code correctness",
        "description": "Run the project test suite",
        "timeout_seconds": 300,
    },
    {
        "id": "step_6",
        "type": StepType.START_APPLICATION.value,
        "name": "Start application",
        "tool": "shell",
        "reason": "Launch application process in background to prepare for health check",
        "description": "Start the service in background for health check",
        "timeout_seconds": 120,
    },
    {
        "id": "step_7",
        "type": StepType.HEALTH_CHECK.value,
        "name": "Health check",
        "tool": "http",
        "reason": "Probe live HTTP endpoint to machine-verify service readiness",
        "description": "Verify application endpoint reachability",
        "timeout_seconds": 30,
    },
    {
        "id": "step_8",
        "type": StepType.FINAL_REPORT.value,
        "name": "Final report",
        "tool": "shell",
        "reason": "Synthesize verified evidence into final machine-checked audit report",
        "description": "Synthesize verified evidence into final status",
        "timeout_seconds": 30,
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
    Deterministic fallback planner providing a reliably valid, structured workflow
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
            timeout_seconds=item.get("timeout_seconds"),
            status=StepStatus.PENDING,
        )
        steps.append(step)

    if analysis:
        steps = update_plan_with_analysis(steps, analysis)

    return steps


def build_adaptive_plan(
    manifest: ProjectManifest,
    repo_url: str = "",
    task: str = "",
) -> List[StepDefinition]:
    """
    Generates an adaptive verification pipeline tailored to the detected project archetype:
    - FastAPI / Flask APIs: Clone -> Analyze -> Dependencies -> Compile -> Import Check -> Start App -> Health Check -> Smoke Test -> Final Report
    - Jupyter Notebooks: Clone -> Analyze -> Dependencies -> Execute Notebook -> Verify Outputs -> Final Report
    - Node / Vite / Next.js: Clone -> Analyze -> Dependencies -> Build -> Tests -> Start App (if applicable) -> Health Check -> Final Report
    - Python Libraries / CLI / ML: Clone -> Analyze -> Dependencies -> Compile -> Import Check -> Tests (if present) -> Smoke Test (if CLI) -> Final Report
    """
    steps: List[StepDefinition] = []
    dep_timeout = 600 if (manifest.heavy_dependencies or manifest.is_ml) else 300

    # 1. Clone
    steps.append(StepDefinition(
        id="step_1",
        type=StepType.CLONE_REPOSITORY.value,
        name="Clone repository",
        tool="git",
        reason="Target repository must be cloned to isolated local workspace before inspection",
        description="Clone the target repository to local workspace",
        timeout_seconds=60,
        status=StepStatus.PENDING,
    ))

    # 2. Analyze
    steps.append(StepDefinition(
        id="step_2",
        type=StepType.ANALYZE_PROJECT.value,
        name="Analyze project",
        tool="file",
        reason="Inspect workspace files to identify language, dependency manifests, and entry points",
        description="Detect languages, package managers, and configurations",
        timeout_seconds=30,
        status=StepStatus.PENDING,
    ))

    # 3. Dependencies
    dep_tool = "pip"
    if manifest.package_manager in ["npm", "yarn", "pnpm"] or manifest.language in ["javascript", "typescript"]:
        dep_tool = "npm"
    steps.append(StepDefinition(
        id="step_3",
        type=StepType.INSTALL_DEPENDENCIES.value,
        name="Install dependencies",
        tool=dep_tool,
        command=manifest.install_command or ("npm install" if dep_tool == "npm" else "pip install -r requirements.txt"),
        reason=f"Install declared dependencies using {manifest.package_manager} (budget: {dep_timeout}s)",
        description=f"Install dependencies using {manifest.package_manager}",
        timeout_seconds=dep_timeout,
        status=StepStatus.PENDING,
    ))

    # 4. Archetype-specific pipeline
    if manifest.is_notebook:
        nb_file = manifest.notebooks[0] if manifest.notebooks else "notebook.ipynb"
        steps.append(StepDefinition(
            id="step_4",
            type=StepType.EXECUTE_NOTEBOOK.value,
            name="Execute notebook",
            tool="notebook",
            command=nb_file,
            reason=f"Execute notebook cells sequentially with output capture ({nb_file})",
            description=f"Execute notebook cells in {nb_file}",
            timeout_seconds=300,
            status=StepStatus.PENDING,
        ))
        steps.append(StepDefinition(
            id="step_5",
            type=StepType.VERIFY_OUTPUTS.value,
            name="Verify outputs",
            tool="notebook",
            command="verify_outputs",
            reason="Verify positive machine-checked execution outputs from notebook cells",
            description="Verify notebook execution evidence",
            timeout_seconds=60,
            status=StepStatus.PENDING,
        ))

    elif manifest.is_api:
        steps.append(StepDefinition(
            id="step_4",
            type=StepType.COMPILE_PROJECT.value,
            name="Compile project",
            tool="python",
            command=manifest.compile_command or "python -m compileall .",
            reason="Verify bytecode compilation and detect syntax errors across project",
            description="Compile project sources",
            timeout_seconds=300,
            status=StepStatus.PENDING,
        ))
        steps.append(StepDefinition(
            id="step_5",
            type=StepType.IMPORT_CHECK.value,
            name="Import check",
            tool="python",
            command=manifest.import_check_command or "python _import_check.py",
            reason="Verify key module/entrypoint imports succeed cleanly",
            description="Import check entrypoints",
            timeout_seconds=60,
            status=StepStatus.PENDING,
        ))
        start_cmd = manifest.start_command or (manifest.application_startup_commands[0] if manifest.application_startup_commands else None)
        steps.append(StepDefinition(
            id="step_6",
            type=StepType.START_APPLICATION.value,
            name="Start application",
            tool="shell",
            command=start_cmd,
            reason=f"Launch API server process in background: {start_cmd}",
            description=f"Start application service ({start_cmd or 'none'})",
            timeout_seconds=120,
            status=StepStatus.PENDING,
        ))
        hc_url = manifest.health_check_url or (manifest.likely_health_endpoints[0] if manifest.likely_health_endpoints else "http://localhost:8000/health")
        steps.append(StepDefinition(
            id="step_7",
            type=StepType.HEALTH_CHECK.value,
            name="Health check",
            tool="http",
            command=hc_url,
            reason=f"Probe HTTP readiness on {hc_url}",
            description=f"Check {hc_url}",
            timeout_seconds=30,
            status=StepStatus.PENDING,
        ))
        steps.append(StepDefinition(
            id="step_8",
            type=StepType.SMOKE_TEST.value,
            name="Smoke test",
            tool="http",
            command=manifest.smoke_test_command or hc_url,
            reason="Execute functional smoke test against service endpoint",
            description="Run functional smoke test",
            timeout_seconds=60,
            status=StepStatus.PENDING,
        ))

    elif manifest.runtime == "node" or manifest.language in ["javascript", "typescript"]:
        steps.append(StepDefinition(
            id="step_4",
            type=StepType.BUILD_PROJECT.value,
            name="Build project",
            tool="npm",
            command=manifest.build_command or "npm run build",
            reason="Compile source code and produce verified build artifacts",
            description="Build project (node)",
            timeout_seconds=300,
            status=StepStatus.PENDING if manifest.build_command else StepStatus.NOT_APPLICABLE,
        ))
        steps.append(StepDefinition(
            id="step_5",
            type=StepType.RUN_TESTS.value,
            name="Run tests",
            tool="npm",
            command=manifest.test_command or "npm test",
            reason="Execute automated test suite to verify code correctness",
            description="Run test suite",
            timeout_seconds=300,
            status=StepStatus.PENDING if manifest.test_command else StepStatus.NOT_APPLICABLE,
        ))
        if manifest.start_command:
            steps.append(StepDefinition(
                id=f"step_{len(steps)+1}",
                type=StepType.START_APPLICATION.value,
                name="Start application",
                tool="npm",
                command=manifest.start_command,
                reason=f"Launch application: {manifest.start_command}",
                description="Start application",
                timeout_seconds=120,
                status=StepStatus.PENDING,
            ))
            steps.append(StepDefinition(
                id=f"step_{len(steps)+1}",
                type=StepType.HEALTH_CHECK.value,
                name="Health check",
                tool="http",
                command=manifest.health_check_url or "http://localhost:3000",
                reason="Probe application readiness",
                description="Health check",
                timeout_seconds=30,
                status=StepStatus.PENDING,
            ))

    else:
        # Python Library / CLI / ML
        steps.append(StepDefinition(
            id="step_4",
            type=StepType.COMPILE_PROJECT.value,
            name="Compile project",
            tool="python",
            command=manifest.compile_command or "python -m compileall .",
            reason="Compile source code and check for syntax errors",
            description="Compile project sources",
            timeout_seconds=300,
            status=StepStatus.PENDING,
        ))
        steps.append(StepDefinition(
            id="step_5",
            type=StepType.IMPORT_CHECK.value,
            name="Import check",
            tool="python",
            command=manifest.import_check_command or "python _import_check.py",
            reason="Verify module imports succeed cleanly",
            description="Import check entrypoints",
            timeout_seconds=60,
            status=StepStatus.PENDING,
        ))
        if manifest.test_command:
            steps.append(StepDefinition(
                id=f"step_{len(steps)+1}",
                type=StepType.RUN_TESTS.value,
                name="Run tests",
                tool="python",
                command=manifest.test_command,
                reason="Execute project test suite",
                description="Run tests",
                timeout_seconds=300,
                status=StepStatus.PENDING,
            ))
        if manifest.smoke_test_command or manifest.is_cli:
            steps.append(StepDefinition(
                id=f"step_{len(steps)+1}",
                type=StepType.SMOKE_TEST.value,
                name="Smoke test",
                tool="shell",
                command=manifest.smoke_test_command or "python -m ...",
                reason="Run functional CLI smoke test",
                description="Smoke test CLI execution",
                timeout_seconds=60,
                status=StepStatus.PENDING,
            ))

    # Final Report
    steps.append(StepDefinition(
        id=f"step_{len(steps)+1}",
        type=StepType.FINAL_REPORT.value,
        name="Final report",
        tool="shell",
        reason="Synthesize verified evidence into final machine-checked audit report",
        description="Synthesize verified evidence into final status",
        timeout_seconds=30,
        status=StepStatus.PENDING,
    ))

    return steps


def update_plan_with_analysis(
    steps: List[StepDefinition],
    analysis: ProjectAnalysis,
) -> List[StepDefinition]:
    """
    Dynamically refines planned steps with concrete tools, commands, reasons,
    and adaptive step extensions discovered from the repository analysis.
    """
    is_node = analysis.language in ["javascript", "typescript"] or analysis.runtime == "node"
    is_python = analysis.language == "python" or analysis.runtime == "python"
    is_notebook = getattr(analysis, "is_notebook", False) or analysis.runtime == "jupyter" or bool(getattr(analysis, "notebooks", None))
    is_heavy = getattr(analysis, "heavy_dependencies", False) or getattr(analysis, "is_ml", False)
    is_api = getattr(analysis, "is_api", False) or getattr(analysis, "framework", "") in ["fastapi", "flask"]

    dep_timeout = 600 if is_heavy else 300

    for step in steps:
        if step.type == StepType.CLONE_REPOSITORY.value:
            step.timeout_seconds = 60
        elif step.type == StepType.ANALYZE_PROJECT.value:
            step.timeout_seconds = 30
        elif step.type == StepType.FINAL_REPORT.value:
            step.timeout_seconds = 30

        elif step.type == StepType.INSTALL_DEPENDENCIES.value:
            step.timeout_seconds = dep_timeout
            if is_node:
                step.tool = "npm"
                step.command = analysis.install_command or "npm install"
                step.reason = f"Detected {analysis.package_manager} with package.json dependencies"
            elif is_python:
                step.tool = "pip"
                step.command = analysis.install_command or "pip install -r requirements.txt"
                step.reason = f"Detected Python project with {analysis.package_manager} package manager (budget: {dep_timeout}s)"
            else:
                if analysis.install_command:
                    step.tool = "shell"
                    step.command = analysis.install_command
                    step.reason = "Generic project structure; generic install command detected"
                else:
                    step.tool = "shell"
                    step.status = StepStatus.NOT_APPLICABLE
                    step.command = None
                    step.reason = "No install step required for this project type"
            step.description = f"Install dependencies using {analysis.package_manager}"

        elif step.type == StepType.BUILD_PROJECT.value:
            step.timeout_seconds = 300
            if is_notebook:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "Build step not applicable for notebook project"
            elif is_node and analysis.build_command:
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
            step.timeout_seconds = 300
            if is_notebook:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "Standard unit tests not applicable for notebook project; notebooks execute directly"
            elif is_node and analysis.test_command:
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
            step.timeout_seconds = 120
            step.execution_mode = getattr(analysis, "execution_mode", ExecutionMode.SHORT_LIVED.value)
            if is_notebook:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "Application start not applicable for notebook project"
            elif is_node and analysis.start_command:
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
            step.timeout_seconds = 30
            if is_notebook:
                step.status = StepStatus.NOT_APPLICABLE
                step.command = None
                step.reason = "Health check skipped: not applicable for notebook project"
                step.description = "Health check (not applicable)"
            else:
                start_step = next((s for s in steps if s.type == StepType.START_APPLICATION.value), None)
                if start_step and start_step.status == StepStatus.NOT_APPLICABLE:
                    step.status = StepStatus.NOT_APPLICABLE
                    step.command = None
                    step.reason = "Health check skipped: application start is not applicable"
                    step.description = "Health check (not applicable)"
                else:
                    step.tool = "http"
                    step.command = analysis.health_check_url or (analysis.likely_health_endpoints[0] if analysis.likely_health_endpoints else "http://localhost:8000/health")
                    step.reason = f"Probe HTTP readiness on {step.command}"
                    step.description = f"Check {step.command}"

    # Adaptive Step Additions: Add archetype-specific execution steps if not already in steps
    step_types = {s.type for s in steps}
    report_idx = next((i for i, s in enumerate(steps) if s.type == StepType.FINAL_REPORT.value), len(steps))

    if is_notebook and StepType.EXECUTE_NOTEBOOK.value not in step_types:
        nb_file = analysis.notebooks[0] if analysis.notebooks else "notebook.ipynb"
        exec_nb = StepDefinition(
            id=f"step_nb_{len(steps)+1}",
            type=StepType.EXECUTE_NOTEBOOK.value,
            name="Execute notebook",
            tool="notebook",
            command=nb_file,
            timeout_seconds=300,
            reason=f"Execute notebook cells sequentially with output capture ({nb_file})",
            description=f"Execute notebook cells in {nb_file}",
            status=StepStatus.PENDING,
        )
        verify_out = StepDefinition(
            id=f"step_vo_{len(steps)+2}",
            type=StepType.VERIFY_OUTPUTS.value,
            name="Verify outputs",
            tool="notebook",
            command="verify_outputs",
            timeout_seconds=60,
            reason="Verify positive machine-checked execution outputs from notebook cells",
            description="Verify notebook execution evidence",
            status=StepStatus.PENDING,
        )
        steps.insert(report_idx, exec_nb)
        steps.insert(report_idx + 1, verify_out)

    elif (is_api or is_python or is_heavy) and not is_notebook:
        # Check if compile_project and import_check should be added
        inst_idx = next((i for i, s in enumerate(steps) if s.type == StepType.INSTALL_DEPENDENCIES.value), None)
        insert_at = (inst_idx + 1) if inst_idx is not None else report_idx

        new_steps = []
        if StepType.COMPILE_PROJECT.value not in step_types:
            comp_cmd = analysis.compile_command or "python -m compileall ."
            new_steps.append(StepDefinition(
                id=f"step_comp_{len(steps)+len(new_steps)+1}",
                type=StepType.COMPILE_PROJECT.value,
                name="Compile project",
                tool="python",
                command=comp_cmd,
                timeout_seconds=300,
                reason="Bytecode compile project source to verify syntax correctness",
                description="Compile Python sources",
                status=StepStatus.PENDING,
            ))
        if StepType.IMPORT_CHECK.value not in step_types and (analysis.entrypoints or is_api):
            imp_cmd = analysis.import_check_command or "python _import_check.py"
            new_steps.append(StepDefinition(
                id=f"step_imp_{len(steps)+len(new_steps)+1}",
                type=StepType.IMPORT_CHECK.value,
                name="Import check",
                tool="python",
                command=imp_cmd,
                timeout_seconds=60,
                reason="Verify entrypoints and core modules import cleanly",
                description="Import check project modules",
                status=StepStatus.PENDING,
            ))

        for idx, ns in enumerate(new_steps):
            steps.insert(insert_at + idx, ns)

        if is_api and StepType.SMOKE_TEST.value not in step_types:
            hc_idx = next((i for i, s in enumerate(steps) if s.type == StepType.HEALTH_CHECK.value), None)
            smoke_ins = (hc_idx + 1) if hc_idx is not None else report_idx
            smoke_cmd = analysis.smoke_test_command or analysis.health_check_url or (analysis.likely_health_endpoints[0] if analysis.likely_health_endpoints else "http://localhost:8000/health")
            smoke_step = StepDefinition(
                id=f"step_smoke_{len(steps)+1}",
                type=StepType.SMOKE_TEST.value,
                name="Smoke test",
                tool="http",
                command=smoke_cmd,
                timeout_seconds=60,
                reason="Probe live application endpoints with functional HTTP request",
                description="Run functional smoke test",
                status=StepStatus.PENDING,
            )
            steps.insert(smoke_ins, smoke_step)

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
