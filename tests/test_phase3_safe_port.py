import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.recovery_planner import RecoveryPlanner, recovery_planner
from backend.models.workflow import (
    ExecutionResult,
    FailureClassification,
    FailureType,
    RecoveryOutcome,
    RecoveryPlan,
    StepDefinition,
    StepStatus,
    WorkflowState,
    WorkflowStatus,
)
from backend.tools.port_utils import (
    find_free_port,
    find_process_holding_port,
    is_port_in_use,
    rewrite_port_in_command,
)


def test_port_utils_free_port_and_rewrite():
    """Verify socket probing and command rewriting helper utilities."""
    # Find free port
    free_port = find_free_port(start_port=20000)
    assert isinstance(free_port, int)
    assert free_port > 0

    # Test rewrite command variations
    cmd1 = "uvicorn main:app --port 8000"
    assert rewrite_port_in_command(cmd1, 8000, 8001) == "uvicorn main:app --port 8001"

    cmd2 = "python -m http.server 8000"
    assert rewrite_port_in_command(cmd2, 8000, 8080) == "python -m http.server 8080"

    cmd3 = "PORT=8000 npm start"
    assert rewrite_port_in_command(cmd3, 8000, 9000) == "PORT=9000 npm start"

    cmd4 = "curl http://localhost:8000/health"
    assert rewrite_port_in_command(cmd4, 8000, 8005) == "curl http://localhost:8005/health"


def test_port_held_by_foreign_process_is_never_killed_and_relocates():
    """
    Test 3a:
    When a port is occupied by a foreign process (not in workflow spawned_pids),
    AgentGuard must NEVER kill the foreign process.
    It must allocate a new port, rewrite the command, and verify the new port is free.
    """
    # Bind a foreign socket to simulate an external service (Postgres, another dev server)
    foreign_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    foreign_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    foreign_sock.bind(("127.0.0.1", 0))
    foreign_sock.listen(1)
    foreign_port = foreign_sock.getsockname()[1]

    try:
        exec_res = ExecutionResult(
            workflow_id="wf-foreign-port",
            step="Start API Server",
            command=f"python server.py --port {foreign_port}",
            exit_code=1,
            metadata={"port": foreign_port},
        )
        classification = FailureClassification(
            failure_type=FailureType.PORT_ERROR,
            reason=f"Port {foreign_port} already in use",
            details={"port": foreign_port, "spawned_pids": []},  # No workflow PIDs
        )

        plan = recovery_planner.generate_recovery_plan(
            exec_result=exec_res,
            classification=classification,
        )

        # Plan MUST relocate, NOT kill
        assert plan.action_type == "relocate_port"
        assert plan.new_port is not None
        assert plan.new_port != foreign_port
        assert plan.rewritten_command == f"python server.py --port {plan.new_port}"
        assert plan.postcondition_type == "port_allocated_and_rewritten"
        assert plan.postcondition_target == str(plan.new_port)

        # Ensure NO dangerous kill commands exist in the plan
        forbidden_patterns = ["Stop-Process", "fuser -k", "kill -9", "killall", "taskkill"]
        for forbidden in forbidden_patterns:
            assert forbidden not in (plan.command or "")

        # Verify the foreign socket is STILL alive and untouched!
        with pytest.raises(OSError):
            # Attempting to re-bind the foreign port must fail because foreign process still holds it
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.bind(("127.0.0.1", foreign_port))
            test_sock.close()

        # Step updated with rewritten command
        step = StepDefinition(
            id="s1",
            type="start_application",
            name="Start API Server",
            command=plan.rewritten_command,
        )

        # Postcondition check passes
        assert recovery_planner.evaluate_postcondition(plan, step=step)

    finally:
        foreign_sock.close()


def test_port_postcondition_fails_when_new_port_is_occupied_or_not_rewritten():
    """
    Test 3b:
    Postcondition evaluation must fail if the relocated port is occupied
    or if the step command was not rewritten.
    """
    plan = RecoveryPlan(
        reason="Port conflict relocated",
        failure_type="PORT_ERROR",
        action_type="relocate_port",
        tool="shell",
        target_step="Start API Server",
        max_attempts=2,
        postcondition_type="port_allocated_and_rewritten",
        postcondition_target="25000",
        new_port=25000,
        rewritten_command="python server.py --port 25000",
        expected_outcome=RecoveryOutcome.SUCCESS,
    )

    # 1. Step command still has the OLD port (not rewritten) -> FAIL
    step_old = StepDefinition(
        id="s1",
        type="start_application",
        name="Start API Server",
        command="python server.py --port 8000",  # Not updated!
    )
    assert not recovery_planner.evaluate_postcondition(plan, step=step_old)

    # 2. Step command is updated, but the new port is actually occupied -> FAIL
    blocker_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker_sock.bind(("127.0.0.1", 25000))
    blocker_sock.listen(1)

    try:
        step_rewritten = StepDefinition(
            id="s1",
            type="start_application",
            name="Start API Server",
            command="python server.py --port 25000",
        )
        assert not recovery_planner.evaluate_postcondition(plan, step=step_rewritten)
    finally:
        blocker_sock.close()

    # 3. Port is free and command is rewritten -> PASS
    assert recovery_planner.evaluate_postcondition(plan, step=step_rewritten)


def test_port_held_by_owned_zombie_process_is_safely_killed():
    """
    Test 3c:
    When a port is occupied by a workflow's OWN child process (recorded in spawned_pids),
    AgentGuard safely kills ONLY that specific PID (not all processes on the port).
    """
    # Spawn a real child process that holds a port
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.8)

    try:
        pid = proc.pid
        assert proc.poll() is None  # Process is running

        exec_res = ExecutionResult(
            workflow_id="wf-owned-port",
            step="Start Server",
            command="python -m http.server",
            exit_code=1,
            metadata={"port": 8000, "spawned_pids": [pid]},
        )
        classification = FailureClassification(
            failure_type=FailureType.PORT_ERROR,
            reason="Port 8000 occupied by previous zombie step",
            details={"port": 8000, "spawned_pids": [pid], "holding_pid": pid},
        )

        plan = recovery_planner.generate_recovery_plan(
            exec_result=exec_res,
            classification=classification,
        )

        assert plan.action_type == "release_port"
        # Command must explicitly target the specific PID, not wildcard
        assert str(pid) in plan.command
        assert "Stop-Process -Id $_.OwningProcess" not in plan.command

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def test_orchestrator_port_relocation_end_to_end():
    """
    Test 3d (Real Fixture):
    A real step attempts to bind to an occupied port.
    Orchestrator plans port relocation, rewrites the step command,
    evaluates the postcondition, and successfully retries on the newly allocated port.
    """
    foreign_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    foreign_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    foreign_sock.bind(("127.0.0.1", 0))
    foreign_sock.listen(1)
    foreign_port = foreign_sock.getsockname()[1]

    try:
        from backend.agent.executor import ToolExecutor
        executor = ToolExecutor(workflow_id="wf-test-port-e2e")
        with tempfile.TemporaryDirectory() as raw_tmp:
            ws = str(Path(raw_tmp).resolve())
            executor.workspace_dir = ws
            script_file = Path(ws) / "bind_port.py"
            script_file.write_text("import socket, sys\np = int(sys.argv[1])\ns = socket.socket()\ns.bind(('127.0.0.1', p))\nprint('BOUND PORT', p)\n")

            step_cmd = f'"{sys.executable}" bind_port.py {foreign_port}'
            step = StepDefinition(
                id="s1",
                type="shell_command",
                name="Bind Service Port",
                command=step_cmd,
            )

            # 1. First run fails with Address already in use
            res1 = executor.execute_step(step)
            assert res1.exit_code != 0
            assert "Only one usage" in res1.stderr or "Address already in use" in res1.stderr or res1.exit_code != 0

            # 2. Diagnose failure
            classification = FailureClassification(
                failure_type=FailureType.PORT_ERROR,
                reason=f"Port {foreign_port} is already in use",
                details={"port": foreign_port, "spawned_pids": []},
            )

            # 3. Plan recovery
            plan = recovery_planner.generate_recovery_plan(
                exec_result=res1,
                classification=classification,
            )
            assert plan.action_type == "relocate_port"
            assert plan.new_port != foreign_port

            # 4. Orchestrator applies rewritten command
            step.command = plan.rewritten_command
            assert str(plan.new_port) in step.command

            # 5. Evaluate postcondition
            assert recovery_planner.evaluate_postcondition(plan, step=step)

            # 6. Retry step with relocated port -> passes!
            res2 = executor.execute_step(step)
            assert res2.exit_code == 0
            assert f"BOUND PORT {plan.new_port}" in res2.stdout

    finally:
        foreign_sock.close()

