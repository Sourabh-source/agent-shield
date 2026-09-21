"""
AgentGuard Demo Runner (Member 2 Backend - Hardened)
Demonstrates:
1. Normal successful workflow execution with machine-checked evidence
2. Failure -> Diagnosis -> Recovery execution -> Retry -> Verified success
3. Persistent failure -> Retry limit reached -> Verified failure (never claims false success)
4. Safe Dry-Run mode -> Plan produced without real command execution
5. Checkpoint Resumption -> Resuming workflow skips already-verified steps
6. Verifier Service Unavailable -> Safe transition to VERIFICATION_UNAVAILABLE
"""
import sys
import tempfile
import time
from pathlib import Path

from backend.agent.orchestrator import WorkflowOrchestrator, workflow_store
from backend.agent.verifier_client import HttpVerifierClient, MockVerifierClient, VerificationClient
from backend.models.workflow import (
    ExecutionResult,
    StepDefinition,
    StepStatus,
    VerificationResult,
    WorkflowStatus,
)


def print_banner(title: str):
    print("\n" + "=" * 65)
    print(f"  {title}")
    print("=" * 65)


def demo_scenario_a_success():
    print_banner("DEMO A: Happy Path (Execute -> Evidence -> Verify -> PASS)")
    orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())

    with tempfile.TemporaryDirectory() as tmp_repo:
        repo_path = Path(tmp_repo)
        (repo_path / "requirements.txt").write_text("pytest\n")
        (repo_path / "main.py").write_text("print('AgentGuard Demo App Running')\n")
        (repo_path / "test_app.py").write_text("def test_ok(): assert 1 + 1 == 2\n")

        wf = orchestrator.create_workflow(
            repo_url=str(repo_path),
            task="Check project health and verify builds",
        )
        print(f"[*] Workflow Created: ID = {wf.workflow_id}, Status = {wf.overall_status.value}")

        wf.steps = [
            StepDefinition(id="s1", type="shell_command", name="Analyze Project", tool="python", reason="Inspect project structure", command="python -c \"print('Analysis complete: python project detected')\""),
            StepDefinition(id="s2", type="shell_command", name="Install Dependencies", tool="shell", reason="Install base requirements", command="echo Successfully installed dependencies"),
            StepDefinition(id="s3", type="shell_command", name="Run Tests", tool="python", reason="Run unit test suite", command="python -c \"print('1 test passed')\""),
            StepDefinition(id="s4", type="shell_command", name="Health Check", tool="shell", reason="Verify readiness", command="echo HTTP 200 OK"),
        ]
        workflow_store.save(wf)

        print("[*] Starting Orchestration...")
        finished = orchestrator.run_workflow(wf.workflow_id)

        print(f"[+] Workflow Completed with Status: {finished.overall_status.value}")
        print(f"[+] Final Result: {finished.final_result}")
        print(f"[+] Steps Verified: {sum(1 for s in finished.steps if s.status == StepStatus.VERIFIED_SUCCESS)}/{len(finished.steps)}")
        print(f"[+] Total Retries: {finished.retries}")
        assert finished.overall_status == WorkflowStatus.COMPLETED


def demo_scenario_b_healing():
    print_banner("DEMO B: Failure & Self-Healing (FAIL -> Diagnose -> Recover -> Retry -> PASS)")

    class HealingScenarioVerifier(VerificationClient):
        def __init__(self):
            self.build_attempts = 0

        def verify(self, exec_result: ExecutionResult) -> VerificationResult:
            if exec_result.step == "Build project":
                self.build_attempts += 1
                if self.build_attempts == 1:
                    print("  [!] Verifier: Build evidence shows exit_code 1, ModuleNotFoundError: 'pandas'")
                    return VerificationResult(
                        verified=False,
                        reason="Missing module 'pandas'",
                        recovery_required=True,
                        recovery_action="echo 'pip install pandas' (simulated recovery)",
                        retry_allowed=True,
                    )
                else:
                    print("  [OK] Verifier: Re-execution of Build project shows exit_code 0. Verified!")
                    return VerificationResult(
                        verified=True,
                        reason="Build succeeded after installing missing module",
                        recovery_required=False,
                    )
            return VerificationResult(verified=True, reason="Exit code 0")

    orchestrator = WorkflowOrchestrator(verifier_client=HealingScenarioVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/broken-dependency-repo",
        task="Check whether project builds with self-healing",
    )

    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Install Dependencies", tool="shell", command="echo Installing base packages"),
        StepDefinition(id="s2", type="shell_command", name="Build project", tool="shell", command="echo Compiling project..."),
        StepDefinition(id="s3", type="shell_command", name="Run Tests", tool="shell", command="echo Tests passed"),
    ]
    workflow_store.save(wf)

    print(f"[*] Workflow Created: ID = {wf.workflow_id}")
    finished = orchestrator.run_workflow(wf.workflow_id)

    print(f"[+] Final Status: {finished.overall_status.value}")
    print(f"[+] Recovery retries performed: {finished.retries}")
    print(f"[+] Build step final status: {finished.steps[1].status.value}")
    print(f"[+] Recovery History Records: {len(finished.recovery_history)}")
    assert finished.overall_status == WorkflowStatus.COMPLETED
    assert finished.retries == 1


def demo_scenario_c_max_retries_failure():
    print_banner("DEMO C: Bounded Retries & Verified Failure (Never Fake Success)")

    class FatalFailureVerifier(VerificationClient):
        def verify(self, exec_result: ExecutionResult) -> VerificationResult:
            return VerificationResult(
                verified=False,
                reason="Corrupt source files syntax error",
                recovery_required=True,
                recovery_action="echo 'Attempting re-parse'",
                retry_allowed=True,
            )

    orchestrator = WorkflowOrchestrator(verifier_client=FatalFailureVerifier(), max_retries=2)
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/fatal-repo",
        task="Check fatally broken project",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Build project", tool="shell", command="echo Build failed"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)

    print(f"[+] Final Status: {finished.overall_status.value}")
    print(f"[+] Retries attempted: {finished.retries}/{finished.max_retries}")
    print(f"[+] Step status: {finished.steps[0].status.value}")
    print(f"[+] Final Result: {finished.final_result}")
    assert finished.overall_status == WorkflowStatus.VERIFIED_FAILURE
    assert finished.retries == 2


def demo_scenario_d_dry_run():
    print_banner("DEMO D: Safe Dry-Run Mode (Planning & Validation Without Execution)")
    orchestrator = WorkflowOrchestrator()
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/dryrun-repo",
        task="Check project health in dry-run mode",
        dry_run=True,
    )
    finished = orchestrator.run_workflow(wf.workflow_id)

    print(f"[+] Final Status: {finished.overall_status.value}")
    print(f"[+] Final Result: {finished.final_result}")
    print(f"[+] Steps Planned: {len(finished.steps)}")
    print(f"[+] Executions Performed: {sum(1 for s in finished.steps if s.execution_result is not None)}")
    assert finished.overall_status == WorkflowStatus.COMPLETED
    assert all(s.execution_result is None for s in finished.steps)


def demo_scenario_e_resumption():
    print_banner("DEMO E: Checkpointing & Resumption from Interrupted State")
    orchestrator = WorkflowOrchestrator(verifier_client=MockVerifierClient())
    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/resume-repo",
        task="Check resume capability",
    )
    # Simulate step 1 already verified before server restart
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Step 1", command="echo step1", status=StepStatus.VERIFIED_SUCCESS),
        StepDefinition(id="s2", type="shell_command", name="Step 2", command="echo step2", status=StepStatus.PENDING),
    ]
    workflow_store.save(wf)

    print(f"[*] Resuming Workflow {wf.workflow_id} (Step 1 already VERIFIED_SUCCESS)...")
    finished = orchestrator.run_workflow(wf.workflow_id, resume=True)

    print(f"[+] Final Status: {finished.overall_status.value}")
    print(f"[+] All steps verified: {all(s.status == StepStatus.VERIFIED_SUCCESS for s in finished.steps)}")
    assert finished.overall_status == WorkflowStatus.COMPLETED


def demo_scenario_f_verifier_unavailable():
    print_banner("DEMO F: Verifier Unavailable Handling (Never Fake Success)")
    unreachable_client = HttpVerifierClient("http://127.0.0.1:59999/verify")
    orchestrator = WorkflowOrchestrator(verifier_client=unreachable_client)

    wf = orchestrator.create_workflow(
        repo_url="https://github.com/example/unreachable-verifier",
        task="Verify with down verifier service",
    )
    wf.steps = [
        StepDefinition(id="s1", type="shell_command", name="Build", command="echo ok"),
    ]
    workflow_store.save(wf)

    finished = orchestrator.run_workflow(wf.workflow_id)
    print(f"[+] Final Status: {finished.overall_status.value}")
    print(f"[+] Final Result: {finished.final_result}")
    assert finished.overall_status == WorkflowStatus.VERIFICATION_UNAVAILABLE


if __name__ == "__main__":
    demo_scenario_a_success()
    demo_scenario_b_healing()
    demo_scenario_c_max_retries_failure()
    demo_scenario_d_dry_run()
    demo_scenario_e_resumption()
    demo_scenario_f_verifier_unavailable()
    print("\n" + "=" * 65)
    print("  ALL 6 HARDENED DEMO SCENARIOS VERIFIED SUCCESSFULLY!")
    print("=" * 65)
