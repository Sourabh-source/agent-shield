import os
import sys
import tempfile
import pytest
from pathlib import Path

from backend.models.workflow import (
    ExecutionMode,
    ExecutionResult,
    StepDefinition,
    StepType,
    StepStatus,
)
from backend.models.reason_codes import ReasonCode
from backend.agent.verifier_client import DeterministicEvidenceVerifier
from backend.tools.project_analyzer import classify_execution_mode, analyze_workspace
from backend.agent.executor import ToolExecutor
from backend.agent.planner import generate_plan


verifier = DeterministicEvidenceVerifier()


def make_exec_result(
    step: str = "Start application",
    step_type: str = StepType.START_APPLICATION.value,
    command: str = "python app.py",
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    execution_mode: str = None,
    metadata: dict = None,
) -> ExecutionResult:
    return ExecutionResult(
        workflow_id="test_wf",
        step=step,
        step_id="step_start",
        step_type=step_type,
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        execution_mode=execution_mode,
        metadata=metadata or {},
    )


# ==============================================================================
# TEST 1: Short-lived Python script (exit code == 0) -> VERIFIED
# ==============================================================================
def test_short_lived_script_exit_zero_verifies_success():
    res = make_exec_result(
        command="python script.py",
        exit_code=0,
        stdout="Hello World! Data processing complete.",
        stderr="",
        execution_mode=ExecutionMode.SHORT_LIVED.value,
        metadata={"execution_mode": "SHORT_LIVED", "process_persistence_required": False},
    )
    vr = verifier.verify(res)
    assert vr.verified is True
    assert vr.status == "VERIFIED"
    assert vr.reason_code == ReasonCode.EXIT_ZERO_CLEAN
    assert "Short-lived" in vr.reason


# ==============================================================================
# TEST 2: Short-lived Python script failure (non-zero exit / import error) -> FAILED
# ==============================================================================
def test_short_lived_script_failure_marks_failed():
    res = make_exec_result(
        command="python script.py",
        exit_code=1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'nonexistent_pkg'",
        execution_mode=ExecutionMode.SHORT_LIVED.value,
        metadata={"execution_mode": "SHORT_LIVED"},
    )
    vr = verifier.verify(res)
    assert vr.verified is False
    assert vr.status == "FAILED"
    assert vr.failure_type == "DEPENDENCY_ERROR"


def test_short_lived_script_syntax_error_marks_failed():
    res = make_exec_result(
        command="python script.py",
        exit_code=1,
        stdout="",
        stderr="SyntaxError: invalid syntax (script.py, line 12)",
        execution_mode=ExecutionMode.SHORT_LIVED.value,
        metadata={"execution_mode": "SHORT_LIVED"},
    )
    vr = verifier.verify(res)
    assert vr.verified is False
    assert vr.status == "FAILED"
    assert vr.failure_type in ("BUILD_ERROR", "SYNTAX_ERROR")


# ==============================================================================
# TEST 3: Long-running process (stays alive) -> VERIFIED
# ==============================================================================
def test_long_running_process_stays_alive_verifies_success():
    my_pid = os.getpid()
    res = make_exec_result(
        command="celery -A worker worker --loglevel=info",
        exit_code=0,
        stdout=f"Application started in background (PID {my_pid})",
        execution_mode=ExecutionMode.LONG_RUNNING.value,
        metadata={
            "pid": my_pid,
            "process_alive": True,
            "execution_mode": "LONG_RUNNING",
            "process_persistence_required": True,
        },
    )
    vr = verifier.verify(res)
    assert vr.verified is True
    assert vr.status == "VERIFIED"
    assert vr.reason_code == ReasonCode.EXIT_ZERO_CLEAN


# ==============================================================================
# TEST 4: Long-running process that exits -> FAILED with PROCESS_EXITED
# ==============================================================================
def test_long_running_process_exits_fails_with_process_exited():
    dead_pid = 99999999
    res = make_exec_result(
        command="celery -A worker worker --loglevel=info",
        exit_code=0,
        stdout="Application terminated prematurely",
        execution_mode=ExecutionMode.LONG_RUNNING.value,
        metadata={
            "pid": dead_pid,
            "process_alive": False,
            "execution_mode": "LONG_RUNNING",
            "process_persistence_required": True,
        },
    )
    vr = verifier.verify(res)
    assert vr.verified is False
    assert vr.status == "FAILED"
    assert vr.failure_type == "PROCESS_EXITED"
    assert vr.reason_code == ReasonCode.PROCESS_EXITED


# ==============================================================================
# TEST 5: Flask / FastAPI HTTP service (alive + listening) -> VERIFIED
# ==============================================================================
def test_http_service_alive_and_listening_verifies_success():
    my_pid = os.getpid()
    res = make_exec_result(
        command="python -m uvicorn app.main:app --port 8000",
        exit_code=0,
        stdout=f"Application started in background (PID {my_pid})",
        execution_mode=ExecutionMode.HTTP_SERVICE.value,
        metadata={
            "pid": my_pid,
            "process_alive": True,
            "port": 8000,
            "port_listening": True,
            "execution_mode": "HTTP_SERVICE",
            "process_persistence_required": True,
        },
    )
    vr = verifier.verify(res)
    assert vr.verified is True
    assert vr.status == "VERIFIED"
    assert vr.reason_code == ReasonCode.EXIT_ZERO_CLEAN


# ==============================================================================
# TEST 6: HTTP service crashes on start -> FAILED with PROCESS_EXITED
# ==============================================================================
def test_http_service_crashes_fails_with_process_exited():
    res = make_exec_result(
        command="python -m uvicorn app.main:app --port 8000",
        exit_code=1,
        stdout="",
        stderr="Application process exited prematurely with code 1\nTraceback (most recent call last):\n  File 'app.py', line 5, in <module>\n    import missing_dep",
        execution_mode=ExecutionMode.HTTP_SERVICE.value,
        metadata={
            "pid": 99999999,
            "process_alive": False,
            "execution_mode": "HTTP_SERVICE",
        },
    )
    vr = verifier.verify(res)
    assert vr.verified is False
    assert vr.status == "FAILED"
    assert vr.failure_type in ("PROCESS_EXITED", "RUNTIME_ERROR", "RUNTIME_FAILURE")


def test_http_service_clean_exit_fails_with_process_exited():
    res = make_exec_result(
        command="python -m uvicorn app.main:app --port 8000",
        exit_code=0,
        stdout="",
        stderr="",
        execution_mode=ExecutionMode.HTTP_SERVICE.value,
        metadata={
            "pid": 99999999,
            "process_alive": False,
            "execution_mode": "HTTP_SERVICE",
        },
    )
    vr = verifier.verify(res)
    assert vr.verified is False
    assert vr.status == "FAILED"
    assert vr.failure_type == "PROCESS_EXITED"
    assert vr.reason_code == ReasonCode.PROCESS_EXITED


# ==============================================================================
# TEST 7: HTTP service alive but wrong port / port not listening -> FAILED with SERVICE_NOT_LISTENING
# ==============================================================================
def test_http_service_alive_but_not_listening_fails():
    my_pid = os.getpid()
    res = make_exec_result(
        command="python -m uvicorn app.main:app --port 8000",
        exit_code=0,
        stdout=f"Application started in background (PID {my_pid})",
        execution_mode=ExecutionMode.HTTP_SERVICE.value,
        metadata={
            "pid": my_pid,
            "process_alive": True,
            "port": 8000,
            "port_listening": False,
            "execution_mode": "HTTP_SERVICE",
        },
    )
    vr = verifier.verify(res)
    assert vr.verified is False
    assert vr.status == "FAILED"
    assert vr.failure_type == "SERVICE_NOT_LISTENING"
    assert vr.reason_code == ReasonCode.SERVICE_NOT_LISTENING


# ==============================================================================
# TEST 8: 30-Days-Of-Python case (exit code 0 no longer marked failed)
# ==============================================================================
def test_30_days_of_python_case():
    with tempfile.TemporaryDirectory() as tmpdir:
        app_py = Path(tmpdir) / "app.py"
        app_py.write_text("print('Day 1 - 30 Days Of Python Challenge')\nprint('Done!')\n", encoding="utf-8")

        mode = classify_execution_mode(workspace_dir=tmpdir, command="python app.py")
        assert mode == ExecutionMode.SHORT_LIVED

        manifest = analyze_workspace(tmpdir)
        assert manifest.execution_mode == ExecutionMode.SHORT_LIVED.value

        executor = ToolExecutor(workflow_id="test_30days", workspace_base=tempfile.gettempdir())
        executor.workspace_dir = tmpdir

        step = StepDefinition(
            id="s_start",
            type=StepType.START_APPLICATION.value,
            name="Start application",
            command="python app.py",
            execution_mode=ExecutionMode.SHORT_LIVED.value,
        )

        exec_res = executor.execute_step(step)
        assert exec_res.exit_code == 0
        assert "Day 1" in exec_res.stdout
        assert "Application started in background" not in exec_res.stdout

        vr = verifier.verify(exec_res)
        assert vr.verified is True
        assert vr.status == "VERIFIED"
        assert vr.failure_type != "PROCESS_EXITED"


# ==============================================================================
# TEST 9: Classification heuristics test
# ==============================================================================
def test_classify_execution_mode_heuristics():
    assert classify_execution_mode(command="pytest") == ExecutionMode.SHORT_LIVED
    assert classify_execution_mode(command="python -m unittest discover") == ExecutionMode.SHORT_LIVED
    assert classify_execution_mode(command="npm run build") == ExecutionMode.SHORT_LIVED
    assert classify_execution_mode(command="python script.py --help") == ExecutionMode.SHORT_LIVED

    assert classify_execution_mode(command="python -m uvicorn main:app --port 8000") == ExecutionMode.HTTP_SERVICE
    assert classify_execution_mode(command="uvicorn app.main:app") == ExecutionMode.HTTP_SERVICE
    assert classify_execution_mode(command="gunicorn wsgi:app") == ExecutionMode.HTTP_SERVICE
    assert classify_execution_mode(command="flask run --port 5000") == ExecutionMode.HTTP_SERVICE
    assert classify_execution_mode(command="next dev") == ExecutionMode.HTTP_SERVICE

    assert classify_execution_mode(command="celery worker -A myapp") == ExecutionMode.LONG_RUNNING
    assert classify_execution_mode(command="python -m rq worker") == ExecutionMode.LONG_RUNNING
