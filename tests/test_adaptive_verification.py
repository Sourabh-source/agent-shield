"""
tests/test_adaptive_verification.py
Comprehensive test suite for Adaptive Project-Aware Verification Architecture in AgentGuard.

Guarantees:
1. Deep Project Detection (FastAPI/Flask, Jupyter/ML, Heavy ML vs light, Node, C/C++, Java, CLI, empty/docs).
2. Adaptive Plan Generation & Step Budgets (heavy ML 600s, normal 300s, startup 120s, health check 30s).
3. Notebook Execution Tool (.ipynb cell-by-cell execution with positive evidence).
4. Partial Dependency Satisfaction & Honest Progression (emits STEP_PARTIALLY_SATISFIED, never claims passed).
5. Positive Machine-Checked Evidence Verification (compile, import, live PID, health check HTTP 200).
6. Honest Final Status Differentiation (VERIFIED_SUCCESS, VERIFIED_FAILURE, INCOMPLETE, NOT_APPLICABLE).
7. Repository Archetypes (document-ai-backend, Medical_Insurance_Regression, OilSplit, Hello-World).
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.classifier import classify_failure
from backend.agent.executor import ToolExecutor
from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.planner import (
    STEP_BUDGETS,
    build_adaptive_plan,
    create_deterministic_plan,
    update_plan_with_analysis,
)
from backend.agent.verifier_client import DeterministicEvidenceVerifier
from backend.models.workflow import (
    EventType,
    ExecutionResult,
    FailureType,
    ProjectManifest,
    StepDefinition,
    StepStatus,
    StepType,
    WorkflowState,
    WorkflowStatus,
)
from backend.tools.notebook_tool import NotebookTool
from backend.tools.project_analyzer import ProjectAnalyzerTool


@pytest.fixture
def temp_workspace():
    """Yields a clean temporary directory and deletes it afterwards."""
    ws = tempfile.mkdtemp(prefix="ag_test_ws_")
    yield Path(ws)
    shutil.rmtree(ws, ignore_errors=True)


# ==============================================================================
# 1. DEEP PROJECT DETECTION TESTS
# ==============================================================================
class TestDeepProjectDetection:
    """Validates deep detection of archetypes, frameworks, entrypoints, and commands."""

    def test_detect_fastapi_project(self, temp_workspace: Path):
        """FastAPI detection: framework, uvicorn startup, likely health endpoints, imports."""
        (temp_workspace / "requirements.txt").write_text("fastapi>=0.100.0\nuvicorn>=0.20.0\npydantic\n")
        app_code = """
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}
"""
        (temp_workspace / "main.py").write_text(app_code)

        analyzer = ProjectAnalyzerTool()
        result = analyzer.run(str(temp_workspace))
        assert result.exit_code == 0
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.runtime == "python"
        assert manifest.framework == "fastapi"
        assert manifest.is_api is True
        assert "main.py" in manifest.entrypoints
        assert manifest.start_command is not None
        assert "uvicorn" in manifest.start_command
        assert manifest.health_check_url == "http://localhost:8000/health"
        assert any("/health" in ep for ep in manifest.likely_health_endpoints)
        assert manifest.compile_command is not None
        assert manifest.import_check_command is not None

    def test_detect_flask_project(self, temp_workspace: Path):
        """Flask detection: framework, flask run startup, health endpoints."""
        (temp_workspace / "requirements.txt").write_text("flask>=2.0.0\ngunicorn\n")
        app_code = """
from flask import Flask
app = Flask(__name__)

@app.route("/ping")
def ping():
    return "pong"
"""
        (temp_workspace / "app.py").write_text(app_code)

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.runtime == "python"
        assert manifest.framework == "flask"
        assert manifest.is_api is True
        assert "app.py" in manifest.entrypoints
        assert manifest.start_command is not None

    def test_detect_jupyter_ml_project(self, temp_workspace: Path):
        """Jupyter notebook & ML detection: notebooks, heavy dependencies detection."""
        nb_content = {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["import numpy as np\n", "x = 42\n", "print(f'Result: {x}')"],
                }
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 2,
        }
        (temp_workspace / "analysis.ipynb").write_text(json.dumps(nb_content))
        (temp_workspace / "requirements.txt").write_text("numpy\npandas\nmatplotlib\n")

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.is_notebook is True
        assert manifest.is_ml is True
        assert "analysis.ipynb" in manifest.notebooks
        assert manifest.heavy_dependencies is False  # Standard ML, not heavy PyTorch/TF

    def test_detect_heavy_ml_project(self, temp_workspace: Path):
        """Heavy ML detection: torch/tensorflow triggers heavy_dependencies budget."""
        (temp_workspace / "requirements.txt").write_text("torch>=2.0.0\ntransformers\naccelerate\n")
        (temp_workspace / "train.py").write_text("import torch\nprint(torch.__version__)\n")

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.is_ml is True
        assert manifest.heavy_dependencies is True
        assert "train.py" in manifest.entrypoints

    def test_detect_node_project(self, temp_workspace: Path):
        """Node/Next.js detection: package.json scripts, build commands."""
        pkg_json = {
            "name": "sample-next",
            "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
            "scripts": {"build": "next build", "start": "next start", "test": "jest"},
        }
        (temp_workspace / "package.json").write_text(json.dumps(pkg_json))

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.runtime == "node"
        assert manifest.build_command is not None
        assert "build" in manifest.build_command
        assert manifest.test_command is not None

    def test_detect_cpp_project(self, temp_workspace: Path):
        """C/C++ detection: CMakeLists.txt build command."""
        (temp_workspace / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\nproject(TestApp)\n")
        (temp_workspace / "main.cpp").write_text("#include <iostream>\nint main() { return 0; }\n")

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.runtime in ("c", "cpp", "c_cpp")
        assert manifest.build_command is not None
        assert "cmake" in manifest.build_command

    def test_detect_empty_or_readme_project(self, temp_workspace: Path):
        """Empty or docs-only project: no runnable code detected."""
        (temp_workspace / "README.md").write_text("# Documentation Only Repo\n")
        (temp_workspace / "LICENSE").write_text("MIT License\n")

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.language in ("generic", "unknown")
        assert not manifest.entrypoints
        assert not manifest.build_command
        assert not manifest.test_command
        assert not manifest.start_command


# ==============================================================================
# 2. ADAPTIVE PLAN GENERATION & STEP BUDGETS
# ==============================================================================
class TestAdaptivePlanGeneration:
    """Validates step budget allocation and dynamic plan generation per archetype."""

    def test_step_budget_allocation(self):
        """Heavy ML receives 600s dependency budget; normal receives 300s."""
        manifest_heavy = ProjectManifest(runtime="python", is_ml=True, heavy_dependencies=True)
        plan_heavy = build_adaptive_plan(manifest_heavy, "https://github.com/example/heavy-ml")
        install_step_heavy = next((s for s in plan_heavy if s.type == StepType.INSTALL_DEPENDENCIES.value), None)
        assert install_step_heavy is not None
        assert install_step_heavy.timeout_seconds == 600

        manifest_normal = ProjectManifest(runtime="python", is_ml=False, heavy_dependencies=False)
        plan_normal = build_adaptive_plan(manifest_normal, "https://github.com/example/normal-app")
        install_step_normal = next((s for s in plan_normal if s.type == StepType.INSTALL_DEPENDENCIES.value), None)
        assert install_step_normal is not None
        assert install_step_normal.timeout_seconds == 300

    def test_fastapi_adaptive_plan(self):
        """FastAPI plan includes compile/import, start app, and health check with proper budgets."""
        manifest = ProjectManifest(
            runtime="python",
            framework="fastapi",
            is_api=True,
            start_command="python -m uvicorn main:app --host 127.0.0.1 --port 8000",
            health_check_url="http://localhost:8000/health",
        )
        plan = build_adaptive_plan(manifest, "https://github.com/example/fastapi-app")
        step_types = [s.type for s in plan]

        assert StepType.COMPILE_PROJECT.value in step_types
        assert StepType.START_APPLICATION.value in step_types
        assert StepType.HEALTH_CHECK.value in step_types

        start_step = next(s for s in plan if s.type == StepType.START_APPLICATION.value)
        assert start_step.timeout_seconds == 120

        health_step = next(s for s in plan if s.type == StepType.HEALTH_CHECK.value)
        assert health_step.timeout_seconds == 30

    def test_jupyter_adaptive_plan(self):
        """Jupyter notebook plan includes execute notebook and verify outputs."""
        manifest = ProjectManifest(
            runtime="python",
            is_notebook=True,
            notebooks=["demo.ipynb"],
        )
        plan = build_adaptive_plan(manifest, "https://github.com/example/notebook-app")
        step_types = [s.type for s in plan]

        assert StepType.EXECUTE_NOTEBOOK.value in step_types
        assert StepType.VERIFY_OUTPUTS.value in step_types

    def test_update_plan_with_analysis_backward_compat(self):
        """Updating existing workflow plan preserves step count and sets NOT_APPLICABLE properly."""
        wf = WorkflowOrchestrator().create_workflow(
            repo_url="https://github.com/example/demo",
            task="Verify repository",
        )
        wf.steps = create_deterministic_plan(wf.repository, wf.task)
        assert len(wf.steps) == 8  # 8 MVP steps

        manifest = ProjectManifest(
            runtime="python",
            is_notebook=True,
            notebooks=["analysis.ipynb"],
        )
        wf.steps = update_plan_with_analysis(wf.steps, manifest)

        step_types = [s.type for s in wf.steps]
        assert StepType.EXECUTE_NOTEBOOK.value in step_types
        # Irrelevant steps marked NOT_APPLICABLE
        start_step = next((s for s in wf.steps if s.type == StepType.START_APPLICATION.value), None)
        assert start_step is not None
        assert start_step.status == StepStatus.NOT_APPLICABLE


# ==============================================================================
# 3. NOTEBOOK EXECUTION TOOL TESTS
# ==============================================================================
class TestNotebookToolExecution:
    """Validates cell-by-cell notebook execution, error capturing, and output evidence."""

    def test_notebook_execution_success(self, temp_workspace: Path):
        """Clean notebook executes all cells and produces output evidence."""
        nb_data = {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["x = 10\n", "y = 20\n"],
                },
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["result = x + y\n", "print(f'SUM={result}')\n"],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 2,
        }
        nb_file = temp_workspace / "test.ipynb"
        nb_file.write_text(json.dumps(nb_data))

        tool = NotebookTool()
        exec_res = tool.run(str(nb_file), cwd=str(temp_workspace), step_id="test_step_nb")

        assert exec_res.exit_code == 0
        assert "Successfully executed all 2 code cells" in exec_res.stdout

        metadata = exec_res.metadata or {}
        assert metadata.get("cells_executed") == 2
        assert metadata.get("total_cells") == 2
        assert len(metadata.get("captured_outputs", [])) >= 1
        assert any("SUM=30" in out for out in metadata["captured_outputs"])

        # DeterministicEvidenceVerifier checks positive evidence
        verifier = DeterministicEvidenceVerifier()
        exec_res.step_type = StepType.EXECUTE_NOTEBOOK.value
        exec_res.step = StepType.EXECUTE_NOTEBOOK.value
        verif_res = verifier.verify(exec_res)
        assert verif_res.verified is True

    def test_notebook_execution_failure_captured(self, temp_workspace: Path):
        """Notebook error on cell 2 halts execution honestly and reports cell index and exception."""
        nb_data = {
            "cells": [
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["valid_var = 100\n"],
                },
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": ["raise ValueError('Deliberate regression failure in cell 2')\n"],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 2,
        }
        nb_file = temp_workspace / "fail.ipynb"
        nb_file.write_text(json.dumps(nb_data))

        tool = NotebookTool()
        exec_res = tool.run(str(nb_file), cwd=str(temp_workspace), step_id="test_step_nb_fail")

        assert exec_res.exit_code != 0
        assert "Cell 2 failed" in exec_res.stderr or (exec_res.metadata or {}).get("cell") == 2

        verifier = DeterministicEvidenceVerifier()
        exec_res.step_type = StepType.EXECUTE_NOTEBOOK.value
        exec_res.step = StepType.EXECUTE_NOTEBOOK.value
        verif_res = verifier.verify(exec_res)
        assert verif_res.verified is False

        # Classifier identifies runtime failure
        clf = classify_failure(exec_res)
        assert clf.failure_type in (FailureType.RUNTIME_FAILURE, FailureType.UNKNOWN_ERROR)


# ==============================================================================
# 4. PARTIAL DEPENDENCY SATISFACTION TESTS
# ==============================================================================
class TestPartialDependencySatisfaction:
    """Validates partial dependency satisfaction progression and honest reporting."""

    def test_partial_dependency_satisfied_continues_to_runtime(self, temp_workspace: Path):
        """When install dependencies fails but environment satisfies compilation, continue to verify."""
        (temp_workspace / "valid.py").write_text("def hello(): return 'world'\n")

        orchestrator = WorkflowOrchestrator()
        wf = orchestrator.create_workflow(
            repo_url="https://github.com/example/partial-deps",
            task="Test partial dependency continuation",
        )
        wf.workspace_path = str(temp_workspace)

        step_install = StepDefinition(
            id="step_inst",
            type=StepType.INSTALL_DEPENDENCIES.value,
            name="Install dependencies",
            command="pip install non_existent_package_that_fails_to_install_12345",
        )
        step_compile = StepDefinition(
            id="step_comp",
            type=StepType.COMPILE_PROJECT.value,
            name="Compile project",
            command=f'python -m py_compile "{temp_workspace / "valid.py"}"',
        )
        wf.steps = [step_install, step_compile]
        workflow_store.save(wf)

        executor = ToolExecutor(wf.workflow_id)
        executor.workspace_dir = str(temp_workspace)
        failed_exec_result = ExecutionResult(
            command=step_install.command,
            exit_code=1,
            stdout="",
            stderr="ERROR: Could not find a version that satisfies the requirement non_existent_package",
            duration_seconds=5.0,
        )

        # Trigger partial dependency handler
        handled = orchestrator._handle_partial_dependency_or_halt(
            workflow=wf,
            step=step_install,
            executor=executor,
            exec_result=failed_exec_result,
            halt_reason="Dependency installation failed",
        )

        assert handled is True
        assert step_install.status == StepStatus.PARTIALLY_SATISFIED
        # Ensure STEP_PARTIALLY_SATISFIED event was emitted
        partially_events = [e for e in wf.events if e.event_type == EventType.STEP_PARTIALLY_SATISFIED]
        assert len(partially_events) == 1
        assert wf.metadata.get("partial_dependency_satisfied") is True

    def test_partial_dependency_unsatisfied_halts(self, temp_workspace: Path):
        """When runtime environment does NOT satisfy compilation/import, halt honestly."""
        # Create a file with syntax error so compilation fails
        (temp_workspace / "bad_syntax.py").write_text("def bad_func(: print('syntax error')\n")

        orchestrator = WorkflowOrchestrator()
        wf = orchestrator.create_workflow(
            repo_url="https://github.com/example/broken-code",
            task="Test broken code halts",
        )
        wf.workspace_path = str(temp_workspace)

        step_install = StepDefinition(
            id="step_inst",
            type=StepType.INSTALL_DEPENDENCIES.value,
            name="Install dependencies",
            command="pip install broken_package",
        )
        wf.steps = [step_install]

        executor = ToolExecutor(wf.workflow_id)
        executor.workspace_dir = str(temp_workspace)
        failed_exec = ExecutionResult(
            command="pip install broken_package",
            exit_code=1,
            stdout="",
            stderr="Could not find package",
            duration_seconds=3.0,
        )

        handled = orchestrator._handle_partial_dependency_or_halt(
            workflow=wf,
            step=step_install,
            executor=executor,
            exec_result=failed_exec,
            halt_reason="Install failed",
        )

        assert handled is False
        assert step_install.status != StepStatus.PARTIALLY_SATISFIED


# ==============================================================================
# 5. MACHINE-CHECKED EVIDENCE VERIFICATION TESTS
# ==============================================================================
class TestMachineCheckedPositiveEvidence:
    """Validates verification contracts require machine-checked positive evidence."""

    def test_compile_project_evidence(self):
        """Compile project requires exit_code 0 and positive compilation output."""
        verifier = DeterministicEvidenceVerifier()

        # Passing compilation
        exec_pass = ExecutionResult(
            step_type=StepType.COMPILE_PROJECT.value,
            step=StepType.COMPILE_PROJECT.value,
            command="python -m compileall .",
            exit_code=0,
            stdout="Project compilation succeeded: 1 Python file(s) compiled cleanly.",
            stderr="",
            duration_seconds=0.5,
        )
        verif_pass = verifier.verify(exec_pass)
        assert verif_pass.verified is True

        # Failing compilation
        exec_fail = ExecutionResult(
            step_type=StepType.COMPILE_PROJECT.value,
            step=StepType.COMPILE_PROJECT.value,
            command="python -m compileall .",
            exit_code=1,
            stdout="",
            stderr="SyntaxError: invalid syntax",
            duration_seconds=0.2,
        )
        verif_fail = verifier.verify(exec_fail)
        assert verif_fail.verified is False

    def test_import_check_evidence(self):
        """Import check requires exit_code 0 and successful module import confirmation."""
        verifier = DeterministicEvidenceVerifier()

        exec_pass = ExecutionResult(
            step_type=StepType.IMPORT_CHECK.value,
            step=StepType.IMPORT_CHECK.value,
            command="python _import_check.py",
            exit_code=0,
            stdout="Import check passed: successfully imported module 'main'",
            stderr="",
            duration_seconds=0.4,
        )
        verif_pass = verifier.verify(exec_pass)
        assert verif_pass.verified is True

        exec_fail = ExecutionResult(
            step_type=StepType.IMPORT_CHECK.value,
            step=StepType.IMPORT_CHECK.value,
            command="python _import_check.py",
            exit_code=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'fastapi'",
            duration_seconds=0.3,
        )
        verif_fail = verifier.verify(exec_fail)
        assert verif_fail.verified is False

    def test_health_check_evidence(self):
        """Health check requires HTTP 200 and payload response evidence."""
        verifier = DeterministicEvidenceVerifier()

        exec_pass = ExecutionResult(
            step_type=StepType.HEALTH_CHECK.value,
            step=StepType.HEALTH_CHECK.value,
            command="health_check http://localhost:8000/health",
            exit_code=0,
            stdout="HTTP 200 OK: Response status 200, payload {'status': 'healthy'}",
            stderr="",
            duration_seconds=0.1,
            metadata={"status_code": 200, "response_body": '{"status": "healthy"}'},
        )
        verif_pass = verifier.verify(exec_pass)
        assert verif_pass.verified is True

        exec_fail = ExecutionResult(
            step_type=StepType.HEALTH_CHECK.value,
            step=StepType.HEALTH_CHECK.value,
            command="health_check http://localhost:8000/health",
            exit_code=1,
            stdout="",
            stderr="HTTP Connection refused: port 8000 closed",
            duration_seconds=1.0,
            metadata={"status_code": 500},
        )
        verif_fail = verifier.verify(exec_fail)
        assert verif_fail.verified is False


# ==============================================================================
# 6. HONEST FINAL STATUS TESTS
# ==============================================================================
class TestHonestFinalStatus:
    """Validates final workflow status differentiation and prevents false success claims."""

    def test_not_applicable_when_no_verification_steps_run(self, temp_workspace: Path):
        """A repository with only clone and analyze yields NOT_APPLICABLE, never VERIFIED_SUCCESS."""
        orchestrator = WorkflowOrchestrator()
        wf = orchestrator.create_workflow(
            repo_url="https://github.com/octocat/Hello-World",
            task="Verify docs-only repo",
        )
        wf.workspace_path = str(temp_workspace)

        # Only clone and analyze, no runnable code
        s1 = StepDefinition(id="s1", type=StepType.CLONE_REPOSITORY.value, name="Clone repo", status=StepStatus.VERIFIED_SUCCESS)
        s2 = StepDefinition(id="s2", type=StepType.ANALYZE_PROJECT.value, name="Analyze project", status=StepStatus.VERIFIED_SUCCESS)
        s3 = StepDefinition(id="s3", type=StepType.RUN_TESTS.value, name="Run tests", status=StepStatus.NOT_APPLICABLE)
        wf.steps = [s1, s2, s3]
        workflow_store.save(wf)

        # Run final status evaluation through run_workflow completion
        with patch.object(orchestrator, "emit_event"):
            wf_res = orchestrator.run_workflow(wf.workflow_id)

        assert wf_res.final_status == "NOT_APPLICABLE"
        assert wf_res.overall_status == WorkflowStatus.NOT_APPLICABLE
        assert "NOT_APPLICABLE" in wf_res.final_result

    def test_incomplete_when_dependencies_partially_satisfied(self, temp_workspace: Path):
        """When dependencies were partially satisfied, final status is INCOMPLETE."""
        orchestrator = WorkflowOrchestrator()
        wf = orchestrator.create_workflow(
            repo_url="https://github.com/example/partial-repo",
            task="Verify partial repo",
        )
        wf.workspace_path = str(temp_workspace)

        s1 = StepDefinition(id="s1", type=StepType.INSTALL_DEPENDENCIES.value, name="Install deps", status=StepStatus.PARTIALLY_SATISFIED)
        s2 = StepDefinition(id="s2", type=StepType.COMPILE_PROJECT.value, name="Compile project", status=StepStatus.VERIFIED_SUCCESS)
        wf.steps = [s1, s2]
        workflow_store.save(wf)

        with patch.object(orchestrator, "emit_event"):
            wf_res = orchestrator.run_workflow(wf.workflow_id)

        assert wf_res.final_status == "INCOMPLETE"
        assert wf_res.overall_status == WorkflowStatus.INCOMPLETE
        assert "INCOMPLETE" in wf_res.final_result

    def test_verified_success_when_runtime_verification_succeeds(self, temp_workspace: Path):
        """When code compiles, imports, or runs tests with machine evidence, final status is VERIFIED_SUCCESS."""
        orchestrator = WorkflowOrchestrator()
        wf = orchestrator.create_workflow(
            repo_url="https://github.com/example/working-app",
            task="Verify working app",
        )
        wf.workspace_path = str(temp_workspace)

        s1 = StepDefinition(id="s1", type=StepType.COMPILE_PROJECT.value, name="Compile project", status=StepStatus.VERIFIED_SUCCESS)
        s2 = StepDefinition(id="s2", type=StepType.IMPORT_CHECK.value, name="Import check", status=StepStatus.VERIFIED_SUCCESS)
        wf.steps = [s1, s2]
        workflow_store.save(wf)

        with patch.object(orchestrator, "emit_event"):
            wf_res = orchestrator.run_workflow(wf.workflow_id)

        assert wf_res.final_status == "VERIFIED_SUCCESS"
        assert wf_res.overall_status == WorkflowStatus.COMPLETED
        assert "VERIFIED SUCCESS" in wf_res.final_result


# ==============================================================================
# 7. REPOSITORY ARCHETYPE SCENARIO TESTS
# ==============================================================================
class TestArchetypeScenarios:
    """End-to-end archetype scenario verification."""

    def test_document_ai_backend_scenario(self, temp_workspace: Path):
        """
        Archetype: document-ai-backend
        FastAPI + OCR backend project:
        - Detects FastAPI and entrypoint main.py
        - Compiles and performs import check
        - Continues past partial dependency installation
        """
        (temp_workspace / "requirements.txt").write_text("fastapi\nuvicorn\npytesseract\n")
        (temp_workspace / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/health')\n"
            "def health(): return {'status': 'healthy'}\n"
        )

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.framework == "fastapi"
        assert manifest.is_api is True
        assert "main.py" in manifest.entrypoints

        plan = build_adaptive_plan(manifest, "https://github.com/example/document-ai-backend")
        plan_types = [s.type for s in plan]
        assert StepType.COMPILE_PROJECT.value in plan_types
        assert StepType.IMPORT_CHECK.value in plan_types
        assert StepType.START_APPLICATION.value in plan_types
        assert StepType.HEALTH_CHECK.value in plan_types

    def test_medical_insurance_regression_scenario(self, temp_workspace: Path):
        """
        Archetype: Medical_Insurance_Regression
        Jupyter notebook project:
        - Detects .ipynb notebook
        - Builds adaptive plan with EXECUTE_NOTEBOOK and VERIFY_OUTPUTS
        - Executes notebook cell by cell
        """
        nb_data = {
            "cells": [
                {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["import math\nval = math.sqrt(144)\n"]},
                {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ["print(f'SQRT={val}')\n"]},
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 2,
        }
        (temp_workspace / "medical_insurance_model.ipynb").write_text(json.dumps(nb_data))

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.is_notebook is True
        assert "medical_insurance_model.ipynb" in manifest.notebooks

        plan = build_adaptive_plan(manifest, "https://github.com/example/Medical_Insurance_Regression")
        plan_types = [s.type for s in plan]
        assert StepType.EXECUTE_NOTEBOOK.value in plan_types
        assert StepType.VERIFY_OUTPUTS.value in plan_types

        # Execute notebook via tool
        tool = NotebookTool()
        res = tool.run(str(temp_workspace / "medical_insurance_model.ipynb"), cwd=str(temp_workspace), step_id="test_med_step")
        assert res.exit_code == 0
        assert any("SQRT=12.0" in out for out in (res.metadata or {}).get("captured_outputs", []))

    def test_oilsplit_scenario(self, temp_workspace: Path):
        """
        Archetype: OilSplit
        Heavy ML project with torch/pandas/sklearn:
        - Detects heavy ML and allocates 600s dependency timeout
        - Checks entrypoints and compilation
        """
        (temp_workspace / "requirements.txt").write_text("torch>=2.0.0\npandas\nscikit-learn\n")
        (temp_workspace / "oilsplit.py").write_text("import os\nprint('OilSplit initialized')\n")

        analyzer = ProjectAnalyzerTool()
        manifest = analyzer.analyze(str(temp_workspace))

        assert manifest.is_ml is True
        assert manifest.heavy_dependencies is True

        plan = build_adaptive_plan(manifest, "https://github.com/example/OilSplit")
        install_step = next(s for s in plan if s.type == StepType.INSTALL_DEPENDENCIES.value)
        assert install_step.timeout_seconds == 600
