# AgentGuard / Agent Shield — Evidence-Gated Self-Healing Agent Workflow
### Production-Hardened Autonomous DevOps & Verification Platform

AgentGuard is an autonomous AI agent engine that executes multi-step software tasks, verifies every critical action using **cryptographically bound, machine-checkable evidence**, diagnoses failures into typed classifications, and **safely self-heals** via structured remediation plans instead of hallucinating completion.

---

## 👥 Architecture & System Boundaries

```
PLAN (AI / Deterministic)
  │
  ▼
EXECUTE (Sandboxed Subprocess Runner / Tool Registry)
  │
  ▼
COLLECT MACHINE EVIDENCE (Exit Code, Stdout, Stderr, Digest, FS Snapshot Diff)
  │
  ▼
TAMPER-EVIDENT VERIFICATION GATE (Verifier Boundary)
  ├── PASS  ──► Checkpoint SQLite State ──► Next Step
  └── FAIL  ──► CLASSIFY FAILURE (Typed Category + Source Evidence)
                  │
                  ▼
                RECOVERY PLANNER (Structured, Idempotent Action)
                  │
                  ▼
                EXECUTE REMEDIATION (Clean Environment / Dependency Install / Port Freeing)
                  │
                  ▼
                RETRY STEP (New Execution ID + Fresh Observable Evidence)
                  │
                  ▼
                RE-VERIFY (Evidence Gate: Never Trust Exit Code Alone)
```

### Frontend: Modern DevOps Frontend (Next.js 16 / React 19 / TypeScript / Tailwind)
- **Workflow Control**: Target Git repository input, task specification, dry-run simulation mode, and live demo failure injection.
- **Real-Time Execution Timeline**: Sub-second polling with phase badges (`PENDING`, `RUNNING`, `RECOVERING`, `VERIFYING`, `VERIFIED_SUCCESS`, `NOT_APPLICABLE`, `FAILED`).
- **Cryptographic Evidence Panel**: Inspects raw subprocess commands, exit codes, execution IDs, timestamps, and deterministic SHA-256 `evidence_digest`.
- **Self-Healing Visualization**: Live inspection of failure classification, structured recovery plans, recovery actions, and bounded retry counters.
- **Executive Verdict**: Unambiguous terminal verdict directly from backend authority (`VERIFIED SUCCESS`, `VERIFIED FAILURE`, `VERIFICATION UNAVAILABLE`, `BUDGET EXCEEDED`, `CANCELLED`).

### Orchestrator: Orchestration, Planning, Execution & Storage (FastAPI / Python)
- **Validation Gate & AI Planner**: Dynamic Gemini LLM planning with deterministic fallback; validates every step against tool registry schemas.
- **Tool Sandbox**: Specialized runners for Git, Shell, Python, Pip, Npm, HTTP health checks, and Workspace File operations with path containment.
- **Closed-Loop Orchestrator**: Enforces verification preconditions, bounds retries (`max_retries=2`), limits recovery actions, tracks execution budgets, and guards concurrent runs with execution mutexes.
- **Filesystem Snapshotting**: Lightweight before/after workspace filesystem diffing tracking created, modified, and deleted files per step.
- **Process Lifecycle Guard**: Tracks spawned process trees and ensures termination (`taskkill /F /T` on Windows, signal groups on POSIX) on cancellation or failure.
- **Hardened SQLite Checkpoints**: Persistent storage configured with Write-Ahead Logging (`WAL`), 5000ms busy timeout, foreign key indexes, and pagination support.

### Verifier: Tamper-Evident Evidence Verification Engine
- **Deterministic SHA-256 Digest**: Raw execution evidence (`stdout`, `stderr`, `exit_code`, `step`, `command`) is cryptographically bound into an `evidence_digest`.
- **Strict Verification Gate**: `/workflow/{id}/verify` rejects replayed tokens on already verified steps (409), mismatched execution IDs (409), mismatched evidence digests (409), and cancelled workflows (409).
- **Mutual Authentication**: Protected by `X-AgentGuard-Verify-Token` header authentication.
- **Zero-Trust Rules**: Exit code 0 is necessary but never sufficient. Verifies non-empty clones, compiler outputs, assertion counts, and active process PIDs.

### Self-Healing & Remediation Engine: Safe, Verified, and Idempotent
- **4-State Outcome Contract**: Remediations evaluate to `SUCCESS`, `FAILED`, `UNVERIFIABLE`, or `UNRECOVERABLE`.
- **AST-Enforced Postconditions**: Zero constant booleans in postcondition evaluations; every recovery requires live system state proof (import checks, socket probes, filesystem presence).
- **Safe Port Relocation**: Foreign/system processes occupying ports are never killed; AgentGuard probes free ports and dynamically rewrites commands/environments. Only workflow-owned child PIDs are terminated.
- **Honest Boundary Enforcement**: Out-of-memory errors (`RESOURCE_LIMIT`) are classified honestly as `UNRECOVERABLE` on unmanaged hosts without fake `gc.collect()` hoaxes. Step timeouts are bounded by `MAX_STEP_TIME`.
- **State-Aware Idempotency & Futility Ledger**: Avoids repeating satisfied actions or loops of futile failing remediations.
- **Honest End-to-End Metrics**: Recovery effectiveness is credited if and only if the downstream step reaches `StepStatus.VERIFIED_SUCCESS`.

---

## 🛡️ Production Hardening & Security Measures

| Security & Reliability Control | Implementation Detail | Expected Outcome |
| :--- | :--- | :--- |
| **Evidence Tamper-Proofing** | SHA-256 digest computed across stdout, stderr, exit code, and command. | Verifier decisions cannot be forged or replayed against stale executions. |
| **Host Secret Isolation** | Subprocess environment blocks `GEMINI_`, `AWS_`, `GITHUB_`, `SECRET_`, `TOKEN_`, `KEY_`. | Child processes and untrusted build scripts cannot exfiltrate host credentials. |
| **Cloud SSRF Protection** | HTTP tool blocks cloud instance metadata endpoints (`169.254.169.254`, `metadata.google.internal`). | Repositories cannot execute SSRF probes against infrastructure metadata services. |
| **Git Injection Prevention** | Git clone validates repository URLs against argument injection (`--upload-pack`) and metacharacters. | Untrusted repo URLs cannot hijack `git` subprocess commands. |
| **Process Tree Isolation** | Background processes tracked in `_spawned_pids` and killed via process tree termination. | Long-running servers or orphaned zombie processes are strictly terminated on exit/cancel. |
| **Bounded Snapshots** | Workspace diffs cap inspection to 1000 files and 5 depth levels; diffs cap at 50 changes. | Prevents high memory consumption or filesystem thrashing on large repositories. |
| **Execution Mutex** | Orchestrator tracks `_running_workflows` under an internal execution lock. | Prevents race conditions or duplicate concurrent executions of the same workflow. |
| **Secret Redaction** | Comprehensive regex masks API keys, bearer tokens, passwords, and private keys in logs and UI. | Sensitive strings never appear in stdout, stderr, database records, or event feeds. |
| **Crash-Proof SQLite** | SQLite initialized with `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`. | Eliminates `database is locked` errors during concurrent polling and writes. |
| **Observability Tracing** | `ObservabilityMiddleware` injects `X-Correlation-ID` and logs structured request durations. | Full end-to-end request tracing across all API calls and background jobs. |

---

## 🚀 Quick Start Guide

### Prerequisites
- Python 3.10+
- Node.js 18+ and npm

### 1. Backend Setup & Run
```powershell
# Navigate to repository root
cd "C:\Users\Sourabh Singh\Desktop\member 2"

# Activate virtual environment
.\.venv\Scripts\activate

# Install dependencies (if needed)
pip install -r backend/requirements.txt

# Run FastAPI backend (port 8000)
.\.venv\Scripts\uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
- API Docs (Swagger): [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health Status: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### 2. Frontend Setup & Run
```powershell
# In a new terminal
cd frontend

# Install dependencies
npm install

# Configure server-only environment variables
# Copy .env.example to .env.local and set AGENTGUARD_API_KEY to an authorized backend key (e.g. test-api-key)
# IMPORTANT: This key is strictly server-only. NEVER prefix with NEXT_PUBLIC_!
copy .env.example .env.local

# Run Next.js frontend (port 3000)
npm run dev
```
- Web Application: [http://localhost:3000](http://localhost:3000)

### 3. Run Full Automated Test Suite (402+ Passing)
```powershell
# Backend & integration test suite (including auth proxy tests)
.\.venv\Scripts\pytest -v

# Frontend proxy & secret isolation test suite
cd frontend && npm test
```

### 4. Run Demo Scenarios Verification Suite (All 7 Passing)
```powershell
.\.venv\Scripts\python.exe scratch\verify_all_demo_scenarios.py
```

---

## 📋 Comprehensive Demo Scenarios (A through G)

| Scenario | Objective | Execution Flow | Verified Terminal State |
| :--- | :--- | :--- | :--- |
| **Scenario A** | Clean Happy Path | 8-step build pipeline on clean repo; all steps pass verification. | `COMPLETED` (`PASS`, 0 recoveries, 0 retries) |
| **Scenario B** | Self-Healing Dependency | Real failing Python command triggers `ModuleNotFoundError: pandas`. Verifier diagnoses `DEPENDENCY_ERROR` and orders `pip install pandas`. Recovery executes, step retries, second verification passes. | `COMPLETED` (`PASS`, 1 recovery, 1 retry) |
| **Scenario C** | Persistent Failure / Bounded Retries | Unresolvable syntax error in source file. System attempts recovery, exhausts `MAX_RETRIES` (2), and safely halts without false claims. | `VERIFIED_FAILURE` (`FAIL`, 2 retries) |
| **Scenario D** | Verifier Unavailable | External verifier unreachable or unconfigured. Workflow halts safely rather than assuming success. | `VERIFICATION_UNAVAILABLE` |
| **Scenario E** | Execution Budget Exceeded | Workflow runtime exceeds `MAX_WORKFLOW_TIME`. State machine halts runaway process. | `BUDGET_EXCEEDED` |
| **Scenario F** | Dry-Run Simulation | Generates AI execution plan and validates schemas without running shell commands. | `COMPLETED` (`dry_run=true`) |
| **Scenario G** | Cancellation & Resume Safety | User requests cancellation; active child processes are terminated, state marked `CANCELLED`, and re-execution safely blocked. | `CANCELLED` |

---

## ⚙️ Configuration Reference

### Backend (`.env` or Environment Variables)
```ini
# Optional: Google Gemini API key for dynamic planning (falls back to deterministic planner if omitted)
GEMINI_API_KEY=

# Maximum retries per step during self-healing (default: 2)
MAX_RETRIES=2

# Maximum step execution timeout in seconds (default: 120)
MAX_STEP_TIME=120

# Maximum workflow runtime in seconds (default: 600)
MAX_WORKFLOW_TIME=600

# Verifier configuration:
MOCK_VERIFIER=true

# Optional: Shared secret token for Verifier verification gate (Header: X-AgentGuard-Verify-Token)
VERIFY_TOKEN=

# Require cryptographic evidence digest on all verification results (default: true)
REQUIRE_EVIDENCE_DIGEST=true
```

### Frontend (`frontend/.env.local`)
```ini
# Backend API Base URL for server-side proxy
BACKEND_URL=http://127.0.0.1:8000

# Server-only API key for authenticating with the AgentGuard backend
# CRITICAL: Do NOT prefix with NEXT_PUBLIC_. This key is kept on the Next.js server.
AGENTGUARD_API_KEY=test-api-key
```

---

## 🔒 Security

For detailed security information and our full threat model analysis, please see our [Security Guide](SECURITY.md) and [Security Review](SECURITY_REVIEW.md).

---

## ⚠️ Known Limitations

AgentGuard is a prototype system that runs subprocesses locally and relies on an MVP verifier implementation. For a full breakdown of current operational and security limitations, please review [LIMITATIONS.md](LIMITATIONS.md).

---

## 🧪 Testing & Verification
 
- **Automated Test Suite**: Executed via `pytest` across multi-platform CI (`ubuntu-latest` and `windows-latest`):
-   - Unit tests for all tool sandboxes (Shell, Git, HTTP, File, Python, Pip)
-   - Path traversal, symlink escape, and command injection security tests
-   - SSRF protection tests covering IP octal, decimal, hex, and cloud metadata bypasses
-   - Deterministic SHA-256 evidence digest verification
-   - Adversarial replay attack & execution ID mismatch rejection
-   - Mandatory cryptographic HMAC-SHA256 signature verification with nonces and timestamps
-   - Real subprocess failure injection & recovery execution
-   - Process tree termination on Windows/POSIX
-   - 25+ real-world secret redaction test corpus with zero false-positive assertions
-   - Automated claim verification gate: `python scripts/verify_security_review.py`
-   - Automated self-healing honesty gate: `python scripts/verify_self_healing_claims.py`
-   - AST-verified postcondition checks and real self-healing regression corpus
- - **Frontend Code Quality**:
-   - `npm run lint`: **0 errors, 0 warnings**
-   - `npm run build`: **Turbopack production build succeeded**