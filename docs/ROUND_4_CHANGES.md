# Round 4 Security Remediation & Audit Findings

## Phase 0 Audit: Baseline Claims vs. Empirical Reality

Before implementing fixes in Round 4, every claim in `SECURITY_REVIEW.md` was re-tested with direct adversarial reproduction scripts. Below is the unvarnished measurement table:

| Attack Vector | Original Claim in `SECURITY_REVIEW.md` | Empirical Reproduction Result | Verdict | Target Test / Fix Phase |
| :--- | :--- | :--- | :--- | :--- |
| **Forged Verification** | "HMAC-SHA256 signature enforcement on `/verify` via `X-AgentGuard-Verify-Signature`" (MITIGATED) | Sent POST to `/{id}/verify` with valid API key, NO signature, and NO verify token. **Accepted with HTTP 200 OK** and marked workflow `VERIFIED SUCCESS`. | ❌ **FALSE CLAIM** (Downgraded to VULNERABLE) | Phase 3 (`tests/test_verify_hardening.py`) |
| **Path Traversal / Symlink Escape** | "Strict `Path.resolve().relative_to()`, directory containment without exception swallowing" (MITIGATED) | Planted junction `outside -> C:\Windows`. Executed `cat outside/win.ini`. **Returned exit code 0** and leaked contents. Test only passed because `/etc/passwd` didn't exist. | ❌ **FALSE CLAIM** (Downgraded to PARTIAL) | Phase 1 (`tests/test_phase10_sandbox_escape.py::test_symlink_escape`) |
| **Command Injection** | "Strict `shell=False`, argument vectors, executable allowlist, shell metacharacter rejection" (MITIGATED) | Executed `python -c "print(open('C:/Windows/win.ini').read())"`. **Returned exit code 0** and executed host Python; `powershell -Command` also runs on host. | ❌ **FALSE CLAIM** (Downgraded to PARTIAL) | Phase 2 (`tests/test_python_c_vector.py`) |
| **SSRF / Metadata Theft** | "Strict URL validator: HTTP/HTTPS only, blocked AWS 169.254.169.254, blocked private IP blocks" (MITIGATED) | Tested decimal IP `http://2852039166/` (169.254.169.254), octal `http://0251.0376.0251.0376/`, and hex `http://0xa9fea9fe/`. **All returned `(True, None)` (valid)**. | ❌ **FALSE CLAIM** (Downgraded to PARTIAL) | Phase 5 (`tests/test_ssrf_encodings.py`) |
| **Credential Leakage** | "Automated multi-pattern `redact_secrets` in output truncation, error logging, and `JsonFormatter`" (MITIGATED) | Tested 7 common secret formats (OpenAI, Anthropic, GitHub PAT, Slack, Stripe, short password, PEM private keys). **0 of 7 were redacted**. | ❌ **FALSE CLAIM** (Downgraded to PARTIAL) | Phase 6 (`tests/test_redaction_corpus.py`) |
| **Verification False Positives** | "Enum-based dispatch (`StepType`), 0-test collection detection, golden corpus error detectors" (MITIGATED) | Passes FAILED-line pytest without summary, npm failing, placeholder echo, Start step without PID, Health checks without status_code. | ❌ **FALSE CLAIM** (Downgraded to PARTIAL) | Phase 7 (`tests/test_verifier_false_positives.py`) |
| **Directory Name Poisoning** | "Verification restricted strictly to `node_modules` or `site-packages`, plus real postcondition checks" (MITIGATED) | `evaluate_postcondition` returns `True` unconditionally for `resource_ready`, `custom`, and `None` postcondition types. | ❌ **FALSE CLAIM** (Downgraded to PARTIAL) | Phase 7 (`tests/test_phase5_self_healing.py`) |
| **Denial of Service** | "MAX_WORKFLOW_TIME (300s budget), MAX_OUTPUT_SIZE (1MB limit), MAX_RECOVERY_ACTIONS (5 limit)" (MITIGATED) | Output truncation at 1MB and timeouts enforced cleanly. | ✅ **CONFIRMED** | `tests/test_phase10_sandbox_escape.py::TestResourceExhaustion` |
| **Tenant Enumeration & Bypass** | "AuthMiddleware with API key mapping, tenant scoping on all queries, uniform 404 anti-enumeration" (MITIGATED) | Cross-tenant access returns 404, list queries filtered by owner. | ✅ **CONFIRMED** | `tests/test_phase3_auth.py::TestMultiTenancyIsolation` |
| **Test Suite Claims** | "All 219/294/298 unit, regression, and adversarial integration tests pass with zero skips, zero regressions" | Missing runtime dependencies (`prometheus-client`, `cryptography`) in `requirements.txt`. Fresh checkout would fail. | ❌ **BROKEN REQUIREMENT** | Phase 4 (`scripts/check_requirements.py`, `ci.yml`) |

---

## Remediation Roadmap & Verification Status (All Phases Complete)

| Phase | Description | Key Changes | Verified By Test Suite | Final Status |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1 (P0)** | Symlink & Path Traversal Containment | `check_path_containment` normalized cross-platform separators, uses `os.path.realpath`, and inspects all ancestor symlinks. `cat` target re-verified. | `tests/test_phase10_sandbox_escape.py` (36 tests) | **MITIGATED** |
| **Phase 2 (P0)** | Close `python -c` & `powershell` Hole | Removed `powershell` & `taskkill` from allowlist. Banned `python -c` (scripts must be written to workspace). | `tests/test_python_c_vector.py` (9 tests) | **MITIGATED** |
| **Phase 3 (P0)** | Harden `/verify` & No Fallback Secrets | Mandatory HMAC-SHA256, 60s timestamp window, single-use nonce cache, evidence digest binding. Removed default fallback secrets. | `tests/test_verify_hardening.py` (6 tests) | **MITIGATED** |
| **Phase 4 (P0)** | Dependency Completeness & CI Pipeline | Added `prometheus-client`, `cryptography`, `hypothesis`. Built `scripts/check_requirements.py` and multi-platform `.github/workflows/ci.yml`. | `scripts/check_requirements.py` | **MITIGATED** |
| **Phase 5** | Close Remaining SSRF Gaps | Added `parse_potential_ip` handling decimal (`2852039166`), octal, and hex IP literals; pre-clone DNS TOCTOU resolution check. | `tests/test_ssrf_encodings.py` (8 tests) | **MITIGATED** |
| **Phase 6** | 25+ Secret Formats Redaction | Full coverage of OpenAI, Anthropic, GitHub PAT, Slack, Stripe, AWS, JWT, PEM keys, basic auth URLs, short passwords with 0 false positives. | `tests/test_redaction_corpus.py` (32 tests) | **MITIGATED** |
| **Phase 7** | Verifier 11/11 False Positives | Removed all 8 fake echo recovery actions; cross-step traceback detection; test failure patterns (`\d+ failing`, TAP `not ok`); PID liveness; status_code validation. | `tests/test_verifier_false_positives.py` (11 tests) | **MITIGATED** |
| **Phase 8** | Docker Sandbox Abstraction | Built `Sandbox` base class and `DockerSandbox` with `--network=none`, `--read-only`, `--cap-drop=ALL`. Returns `SANDBOX_UNAVAILABLE` fail-closed when daemon absent. | `tests/test_sandbox.py` (7 tests) | **MITIGATED** |
| **Phase 9** | Honesty Infrastructure | Built `scripts/verify_security_review.py` gate requiring passing tests for every `MITIGATED` row in `SECURITY_REVIEW.md`. Updated docs. | `scripts/verify_security_review.py` | **MITIGATED** |

