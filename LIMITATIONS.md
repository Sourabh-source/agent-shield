# AgentGuard Known Limitations

While AgentGuard has been significantly hardened against common web and command injection vulnerabilities, it remains a prototype system with several critical security and operational limitations. Do not deploy AgentGuard in a multi-tenant production environment without addressing these issues.

## Infrastructure & Sandboxing
- **No real VM-level sandboxing**: Currently uses process-level isolation only. A determined attacker with a kernel exploit could escape the process boundaries.
- **No network isolation**: Subprocesses can make outbound network requests. Egress filtering is URL-level only, meaning malicious code can exfiltrate data or communicate with C2 servers.
- **No GPU isolation**: There are no GPU quotas or isolation mechanisms in place.
- **Single-node only**: There is no horizontal scaling, job queue, or distributed locking. The system is constrained to a single machine's resources.
- **SQLite only**: The persistence layer relies on SQLite. It is not suitable for concurrent production load; a PostgreSQL upgrade is required.
- **Windows-only testing**: AgentGuard has only been tested on Windows in CI. Compatibility with Linux and macOS is not guaranteed or tested.

## Security & Verification
- **Mock verifier by default**: The deterministic verifier is a pattern matcher (regex/heuristic), not a formal proof system.
- **No formal verification**: Evidence verification relies on heuristic pattern matching rather than cryptographically sound proofs of execution.
- **No supply chain verification**: Cloned repositories are not verified against signed commits or SBOMs.
- **No content scanning**: Cloned repository contents are not scanned for malware, backdoors, or malicious binaries before execution.
- **No secrets management**: API keys and tokens are stored in the application configuration or environment variables, not in a dedicated secrets vault.
- **Rate limiting is in-memory**: The sliding-window rate limit resets on server restart and is not distributed across nodes.

## Claims Audit Findings
A codebase audit for overclaiming language revealed the following:
- Found **"guaranteed"** in `README.md` (e.g., "guaranteed killed") and `backend/agent/planner.py` ("guaranteed valid"). These have been downgraded in the documentation to reflect reality (e.g., "reliably terminated").
- Did not find occurrences of "100% secure", "unhackable", "impossible to", "fully protected", or "unbreakable".
