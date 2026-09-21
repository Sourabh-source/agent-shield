# AgentGuard — Evidence-Gated Self-Healing Agent Workflow
### Problem Statement A1: Full-Stack Implementation (Members 1, 2, & 3 Finalized)

AgentGuard is an autonomous AI agent engine that executes real multi-step software tasks, verifies each critical action using **machine-checkable observable evidence**, detects failures, and **safely self-heals/recovers** instead of falsely claiming completion.

---

## 👥 Member Roles & Architecture

### Member 1: Frontend (Next.js / React / TypeScript / Tailwind)
- **Workflow Submission**: Repository URL, task definition, dry-run toggle, demo failure injection mode.
- **Real-Time Execution Timeline**: Live polling of workflow state machine, active step indicator, step status badges.
- **Evidence Panel**: Observable evidence per step (`command`, `exit_code`, `stdout`, `stderr`, `duration_ms`, `execution_id`, `timestamp`).
- **Self-Healing Visualization**: Explicit recovery plans, failure classification, recovery action execution, and retry counters.
- **Final Verdict Card**: Authoritative status directly from backend (`VERIFIED SUCCESS`, `VERIFIED FAILURE`, `VERIFICATION UNAVAILABLE`, `CANCELLED`).
- **Audit Log & Report**: Chronological event streaming and post-run summary report.

### Member 2: Orchestration, Planning, Execution & Backend (FastAPI / Python)
- **AI Planner & Validation Gate**: LLM-driven planning with deterministic fallback; validates step definitions against registered tool schemas.
- **Tool Sandbox & Registry**: Git clone, shell subprocess runner, Python interpreter, pip package manager, npm runner, HTTP health checks, and workspace file reader with path traversal containment and secret redaction.
- **State Machine & Orchestrator**: Closed-loop evidence verification, bounded retries (`max_retries=2`), execution budgets, and cancellation/resumption.
- **SQLite Checkpointing**: Persistent state across server restarts.

### Member 3: Evidence Verification & Integration Boundary
- **`ExecutionResult` Contract**: Standardized machine-checkable evidence payload sent from Member 2 to Member 3.
- **`VerificationResult` Contract**: Decision payload returned to Member 2 (`verified`, `reason`, `recovery_required`, `recovery_action`, `retry_allowed`).
- **Deterministic Evidence Verifier (`DeterministicEvidenceVerifier`)**: Real machine-checkable evidence inspection across:
  - **A. Command/Execution Verification**: Validates execution ID, timestamps, exit codes, and metadata security flags.
  - **B. Dependency Installation**: Inspects package manager logs for fatal resolution conflicts or broken wheels even if exit code is 0.
  - **C. Build Verification**: Scans output for syntax errors, missing modules, or compilation failures.
  - **D. Test Verification**: Confirms test execution without assertion failures.
  - **E. Health Check Verification**: Validates HTTP status codes (2xx/3xx) and rejects connection failures.
  - **F. Application Startup**: Verifies process readiness and checks for port conflicts.
  - **G. Final Report**: Ensures all required steps are verified before issuing `VERIFIED_SUCCESS`.
- **Verifier Selection**: Explicit configuration (`MOCK_VERIFIER`, `MEMBER3_VERIFIER_URL`, or `UnavailableVerifierClient`).

---

## 🔄 Core Loop Architecture

```
User Input (Repo URL + Task + DryRun Flag)
      │
      ▼
AI Planner (Gemini LLM / Fallback) + Tool Validation Gate
      │
      ▼
┌────────────────── Workflow Orchestrator Loop ──────────────────┐
│                                                                │
│   1. Check Budget & Cancellation Status                        │
│                                                                │
│   2. Tool Registry executes step via specialized tool          │
│      (Git, Shell, Python, Pip, Npm, HTTP, File)                │
│                                                                │
│   3. Collect raw ExecutionResult (exit code, stdout,           │
│      stderr, duration, workspace, correlation IDs)             │
│                                                                │
│   4. Send ExecutionResult to Member 3 Evidence Verifier        │
│                                                                │
│   5. Verifier evaluates machine evidence:                      │
│      ├── PASS  ──► Checkpoint State ──► Next Step              │
│      └── FAIL  ──► Trigger Self-Healing:                       │
│                     - Failure Classification                   │
│                     - Idempotent Recovery Plan                 │
│                     - Enforce Recovery & Retry Budgets         │
│                     - Execute recovery action                  │
│                     - Record RecoveryAttempt                   │
│                     - Re-execute step & Re-verify evidence     │
│                                                                │
│   6. Max Retries or Budget Exceeded?                           │
│      └──► VERIFIED_FAILURE / BUDGET_EXCEEDED (Never fake!)     │
└────────────────────────────────────────────────────────────────┘
      │
      ▼
Final Verified State (COMPLETED, VERIFIED_FAILURE, CANCELLED, or VERIFICATION_UNAVAILABLE)
      │
      ▼
Generate Final Structured Execution Report
```

---

## 🚀 Quick Start (Full Stack)

### 1. Prerequisites
- Python 3.10+
- Node.js 18+ and npm

### 2. Backend Setup & Startup
```bash
# Activate virtual environment
.\.venv\Scripts\activate

# Install dependencies (if not already installed)
pip install -r backend/requirements.txt

# Start FastAPI server (port 8000)
.\.venv\Scripts\uvicorn backend.main:app --host 127.0.0.1 --port 8000
```
- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### 3. Frontend Setup & Startup
```bash
cd frontend

# Install dependencies
npm install

# Start Next.js development server (port 3000)
npm run dev
```
- Web Application: [http://localhost:3000](http://localhost:3000)

### 4. Run Automated Test Suite (95/95 Tests Passing)
```bash
.\.venv\Scripts\pytest -q
```

### 5. Run Demo Scenarios A–F
```bash
.\.venv\Scripts\python.exe -m backend.demo_run
```

---

## ⚙️ Environment Variables

### Backend Configuration (`.env`)
```bash
# Optional: Google Gemini API key for dynamic planning (falls back to deterministic planner if omitted)
GEMINI_API_KEY=

# Maximum retries per step during self-healing (default: 2)
MAX_RETRIES=2

# Maximum step execution timeout in seconds (default: 120)
MAX_STEP_TIME=120

# Maximum workflow runtime in seconds (default: 600)
MAX_WORKFLOW_TIME=600

# Verifier configuration:
# Set to true for local development / deterministic evidence verifier
MOCK_VERIFIER=true

# Optional: External Member 3 verifier HTTP endpoint URL
# When MOCK_VERIFIER=false and URL is provided, HttpVerifierClient is used.
# When MOCK_VERIFIER=false and URL is empty, UnavailableVerifierClient is used.
MEMBER3_VERIFIER_URL=
```

### Frontend Configuration (`frontend/.env.local`)
```bash
# URL of the running FastAPI backend
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```
*SECURITY NOTE: Only `NEXT_PUBLIC_API_URL` is exposed to the browser. No API keys, GitHub tokens, or cloud credentials are sent to the client.*

---

## 📋 Verified Demo Scenarios

### DEMO A: Normal Workflow (Happy Path)
- **Repository**: `https://github.com/octocat/Hello-World`
- **Result**: `COMPLETED` / `VERIFIED SUCCESS` (8/8 steps, 0 recoveries, 0 retries).

### DEMO B: Self-Healing Workflow (Missing Dependency)
- **Failure Mode**: `missing_dependency`
- **Execution Flow**: Step fails with `ModuleNotFoundError: pandas` ➔ Verifier rejects with recovery action `pip install pandas` ➔ Recovery executed ➔ Step retried with new execution ID ➔ Re-verification passes ➔ `VERIFIED SUCCESS`.

### DEMO C: Persistent Failure (Bounded Retries)
- **Failure Mode**: `persistent_failure`
- **Execution Flow**: Step fails repeatedly ➔ Recovery attempted ➔ Retry 1 fails ➔ Retry 2 fails ➔ `MAX_RETRIES` (2) exhausted ➔ Transitions to `VERIFIED_FAILURE` (never falsely claims success).

### DEMO D: Safe Dry-Run Mode
- **Execution Flow**: Plans all steps and validates tool definitions without executing real shell commands.

### DEMO E: Resumption from Interrupted State
- **Execution Flow**: Resuming a workflow preserves already `VERIFIED_SUCCESS` steps from SQLite checkpoints without duplicate re-execution.

### DEMO F: Verifier Service Unavailable
- **Execution Flow**: When verifier service is unreachable or unconfigured, workflow halts with `VERIFICATION_UNAVAILABLE` rather than faking success.

---

## 🛡️ Security, Reliability & Safety Hardening

- **Evidence-Gated Completion**: An exit code of 0 NEVER directly produces `VERIFIED_SUCCESS` without machine verification.
- **Destructive Command Blocking**: Blocks dangerous operations (`rm -rf /`, formatting, fork bombs).
- **Workspace Containment**: Restricts file operations and command execution to the isolated workflow workspace directory.
- **Execution Budgets**: Enforces step timeouts (120s), workflow timeouts (600s), stdout limits (1MB), and recovery limits (3).
- **Secret Redaction**: Masks credentials, API keys, and Authorization headers in logs and events.
- **Zero Browser Secrets**: No private credentials or API keys exist in frontend code or bundle.