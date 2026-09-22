# AgentGuard Self-Healing Reliability Audit

## Executive Summary

AgentGuard executes multi-step autonomous workflows against untrusted repositories. The core value proposition of AgentGuard is that **every recovery action must either provably remediate the failure condition or honestly report that it cannot**. 

Prior to this reliability engineering phase, an audit of the self-healing subsystem revealed several critical reliability defects:
1. **Fake Postconditions**: Postconditions such as `resource_ready` simply returned `True` unconditionally, fabricating recovery success.
2. **In-Process Garbage Collection Hoax**: For out-of-memory (`RESOURCE_LIMIT`) errors, the recovery planner invoked `gc.collect()` inside the orchestrator's Python process, which had zero effect on the external subprocess that actually ran out of memory.
3. **Host Process Destruction**: For `PORT_ERROR`, the system attempted destructive global shell commands (`taskkill /F /PID` or PowerShell `Stop-Process`) targeting whatever process held the port—risking termination of critical system daemons, databases, or user IDEs.
4. **Premature Recovery Success**: The orchestrator marked recovery attempts as successful merely because the recovery command returned exit code 0, without waiting to see if the subsequent step execution actually passed machine verification.
5. **Futile Action Loops**: When a recovery action failed, the planner had no persistent ledger of attempted actions, repeatedly attempting the exact same failing remediation until retry limits were exhausted.

Over Phases 1 through 6, the self-healing engine was overhauled with strict contract definitions, AST-verified postconditions, safe process isolation, dynamic port relocation, idempotency checks, futile action tracking, and honest end-to-end metrics.

---

## 1. Initial Audit Findings & Remediation

| Component | Initial State (Pre-Hardening) | Defect Analysis | Remediated State (Post-Hardening) |
| :--- | :--- | :--- | :--- |
| **`RecoveryOutcome` Contract** | Binary boolean (`True`/`False`) or informal status strings. | Ambiguity between recoverable failure, unrecoverable defects, and unverifiable state. | Formal 4-state enum: `SUCCESS`, `FAILED`, `UNVERIFIABLE`, `UNRECOVERABLE`. Static AST test gate enforces no constant boolean returns. |
| **`TIMEOUT` Recovery** | Unbounded or arbitrary increments; fake postconditions. | Step timeouts could exceed system ceilings or mask deadlocks. | Doubling bounded timeout calculation: `min(max(cur * 2, cur + 5), MAX_STEP_TIME)`. If already at ceiling, marked `UNRECOVERABLE`. |
| **`RESOURCE_LIMIT` Recovery** | Executed in-process `gc.collect()` in orchestrator and returned `resource_ready: True`. | Complete hoax. Host orchestrator GC cannot free memory of an external killed subprocess. | Classified honestly as `UNRECOVERABLE` on unmanaged hosts. No fake actions or deceptive postconditions. |
| **`PORT_ERROR` Recovery** | Global `taskkill` / `Stop-Process` on any process occupying the port. | Severe host hazard: killed foreign processes (e.g., PostgreSQL, Docker, IDEs). | Foreign processes are never terminated. Ports relocate dynamically (`find_free_port`) and commands are rewritten. Only workflow-owned child PIDs are killed. |
| **Postcondition Evaluation** | Returned constant `True` for unhandled types. | Falsified recovery verification; permitted downstream steps to fail blindly. | AST test gate (`tests/test_recovery_contract.py`) bans constant returns. Every postcondition checks live system state (sockets, filesystem, imports). |
| **Idempotency & Futility** | Repeated the same remediation across retries; ignored existing state. | Wasted execution budgets re-installing existing packages or re-binding taken ports. | `is_action_already_satisfied` verifies prior state. `is_action_futile` halts repeated failing actions via `workflow.recovery_history`. |
| **Reporting & Metrics** | Recoveries counted as effective based solely on exit code 0 of recovery script. | False claim: recovery script passing does not mean the workflow step was fixed. | `step_resolved = True` and `recoveries_verified_effective` are recorded **only** after the target step reaches `StepStatus.VERIFIED_SUCCESS`. |

---

## 2. Technical Architecture of Self-Healing Engine

### 2.1 The 4-State Recovery Outcome Contract
Every remediation evaluation returns a typed `RecoveryOutcome`:
- **`SUCCESS`**: The recovery command executed successfully AND its postcondition verified live system state (e.g., package importable, port responsive, socket free).
- **`FAILED`**: The recovery command exited non-zero or the postcondition verification failed.
- **`UNVERIFIABLE`**: The remediation ran, but the host environment lacks the tooling or permissions to inspect postcondition state.
- **`UNRECOVERABLE`**: The error cannot be solved by autonomous agent action (e.g., host OOM, hard timeout ceilings reached, unresolvable syntax/logic bugs).

### 2.2 AST-Verified Postcondition Gate
To guarantee that no developer or future contributor can introduce a "fake" postcondition:
- `tests/test_recovery_contract.py` parses `backend/agent/recovery_planner.py` using Python's `ast` module.
- It scans every branch of `evaluate_postcondition()`.
- If any branch returns an unconditional literal `True` or constant boolean without state inspection, the test fails immediately.

### 2.3 Safe Port Relocation vs. Workflow-Owned Process Termination
Port conflicts are handled in `backend/tools/port_utils.py`:
1. **Detection**: `is_port_in_use(port)` checks port state using `SO_EXCLUSIVEADDRUSE` on Windows and standard socket binding on POSIX.
2. **Ownership Check**: If a port is occupied, the orchestrator checks if the PID belongs to `workflow.metadata["spawned_pids"]`.
3. **Safe Termination**: If and only if the PID was spawned by the current workflow, it is terminated by specific PID.
4. **Relocation & Rewrite**: If the PID is foreign (or unidentifiable), AgentGuard **never** attempts to kill it. It calls `find_free_port()`, allocates a clean port, and calls `rewrite_port_in_command(command, old_port, new_port)` to update the workflow step transparently.

### 2.4 State-Aware Idempotency & Futile Action Ledger
Before running any recovery action:
- `is_action_already_satisfied(plan, workspace_dir, step)` checks if the requested state is already achieved (e.g., module already exists in `node_modules` or `site-packages`, port already free, timeout already at maximum).
- `is_action_futile(plan, step, workflow)` inspects `workflow.recovery_history`. If the exact same recovery action was already attempted on this step and resulted in `FAILED`, subsequent attempts are blocked immediately, preventing infinite recovery loops.

### 2.5 Honest End-to-End Metric Accounting
In `backend/agent/orchestrator.py` and `backend/models/workflow.py`:
```python
recoveries_attempted = sum(len(step.recovery_attempts) for step in workflow.steps)
recoveries_verified_effective = len([
    step for step in workflow.steps 
    if step.recovery_attempts and step.status == StepStatus.VERIFIED_SUCCESS
])
recoveries_unrecoverable = sum(
    1 for step in workflow.steps
    for attempt in step.recovery_attempts
    if attempt.get("outcome") == RecoveryOutcome.UNRECOVERABLE.value
)
```
A recovery attempt is never credited as "effective" unless the subsequent step execution passes tamper-evident evidence verification.

---

## 3. Remediation Category Effectiveness & Ground Truth

| Failure Category | Classification Trigger | Remediation Strategy | Postcondition Check | Measured Effectiveness |
| :--- | :--- | :--- | :--- | :--- |
| **`DEPENDENCY_ERROR` (Python)** | `ModuleNotFoundError`, `ImportError`, `No module named` | `pip install <pkg>` into workspace environment. | `package_installed`: `importlib.util.find_spec` + `pip show`. | **High**: Successfully resolves standard missing packages. |
| **`DEPENDENCY_ERROR` (Node)** | `Cannot find module`, `ERR_MODULE_NOT_FOUND` | `npm install <pkg>` in workspace directory. | `npm_package`: `node_modules/<pkg>` presence and non-empty package validation. | **High**: Successfully resolves standard npm dependencies. |
| **`PORT_ERROR`** | `EADDRINUSE`, `address already in use` | Relocate to free port & rewrite command; or terminate workflow child PID. | `port_free` / `port_relocated`: socket bind probe confirming port availability. | **High**: Zero host interference; transparent port rewrites succeed reliably. |
| **`TIMEOUT`** | Subprocess exceeded step timeout budget | Exponential backoff (`min(2*t, MAX_STEP_TIME)`). | `timeout_extended`: verifies step timeout increased without exceeding ceiling. | **Moderate**: Resolves slow builds or transient load spikes. Halts honestly on deadlocks. |
| **`RESOURCE_LIMIT`** | `MemoryError`, OOM killer exit codes | None on unmanaged hosts. Classify as `UNRECOVERABLE`. | N/A (honestly reports unrecoverable). | **Zero False Claims**: Does not pretend to recover from host resource exhaustion. |
| **`SYNTAX_ERROR`** | `SyntaxError`, `IndentationError` | None autonomous without verified patch. | N/A (halts at bounded retries). | **Defensive**: Bounded retries prevent runaway attempts. |
| **`BUILD_ERROR`** | Compiler or linker failure | Environment clean / cache clear where safe. | `exit_code == 0` on rebuild. | **Bounded**: Retries bounded to `MAX_RETRIES`. |

---

## 4. Verification & Testing Evidence

The self-healing reliability system is verified across dedicated test suites:
- **`tests/test_recovery_contract.py`**: AST analysis of `evaluate_postcondition`, ensuring zero constant boolean branches.
- **`tests/test_phase2_timeout_resource.py`**: Validates bounded timeout doubling, ceiling enforcement, and `UNRECOVERABLE` OOM handling without GC hoaxes.
- **`tests/test_phase3_safe_port.py`**: Validates foreign process protection, dynamic port relocation, command rewriting, and workflow-child PID isolation.
- **`tests/test_phase4_idempotency.py`**: Validates state satisfaction checks and the futile recovery ledger.
- **`tests/test_phase5_honest_reporting.py`**: Validates end-to-end `step_resolved` lifecycle and final report metrics.
- **`tests/test_self_healing_corpus.py`**: Regression corpus validating recovery outcomes across real failure modes.
- **`scripts/verify_self_healing_claims.py`**: Automated honesty verification gate run in CI to prevent regression of self-healing claims.
