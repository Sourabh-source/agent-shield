# AgentGuard Known Limitations

While AgentGuard has been significantly hardened against common web and command injection vulnerabilities, it remains a prototype system with several critical security and operational limitations. Do not deploy AgentGuard in a multi-tenant production environment without addressing these issues.

## Infrastructure & Sandboxing
- **Container Sandboxing**: `DockerSandbox` implements `--network=none`, `--read-only`, `--cap-drop=ALL`, and CPU/memory/pids constraints. When a Docker daemon is not present in the host environment, sandboxed runs fail closed with `SANDBOX_UNAVAILABLE` rather than silently running on the host. Direct host execution is restricted to allowlisted executables without `shell=True`.
- **Network isolation**: Container sandbox enforces `--network=none`. On host subprocess runs, outbound network requests are restricted to validated Git clone URLs and explicit health check endpoints.
- **No GPU isolation**: There are no GPU quotas or isolation mechanisms in place.
- **Single-node only**: There is no horizontal scaling, job queue, or distributed locking. The system is constrained to a single machine's resources.
- **SQLite only**: The persistence layer relies on SQLite with WAL mode. It is suitable for single-node deployments; multi-node deployments require PostgreSQL.
- **Multi-Platform CI**: AgentGuard is continuously tested on both `ubuntu-latest` and `windows-latest` across Python 3.10 and 3.11.

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
