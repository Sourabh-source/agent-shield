# AgentGuard Attack Matrix

| Attack Vector | Category | Mitigation | Test Coverage | Residual Risk |
|--------------|----------|------------|---------------|---------------|
| Command injection | Elevation of Privilege | Executable allowlist, shell escaping | E2E tests, Adversarial | Low |
| SSRF | Information Disclosure | Allowed domains list, IP filtering (git_tool) | Unit tests | Low |
| Path traversal | Elevation of Privilege | Path containment, jail directories | Unit tests, Adversarial | Low |
| Auth bypass | Spoofing | API keys, HMAC, Auth middleware | E2E tests | Low |
| Rate limit bypass | Denial of Service | IP/Token based rate limiting | E2E tests | Medium |
| Verdict forgery | Tampering | Hash chain, Ed25519 signing (audit) | Unit tests | Low |
| Sandbox escape | Elevation of Privilege | Strict sandboxing, resource limits | E2E tests, Adversarial | Medium |
| Env leakage | Information Disclosure | Env filtering, secret redaction | Unit tests | Low |
| Prompt injection | Tampering / EoP | LLM guardrails, input validation | Adversarial | Medium |
| Resource exhaustion | Denial of Service | Timeouts, resource quotas | E2E tests | Medium |
| Crypto mining | Denial of Service | CPU limits, network egress filtering | Unit tests | Low |
| Supply chain | Tampering | Dependency pinning, static analysis | CI/CD | Medium |
