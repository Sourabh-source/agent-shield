# Threat Model: AgentGuard

This document provides a STRIDE-based threat model for AgentGuard.

## STRIDE Analysis

### Spoofing (Identity Verification)
- **Threat Description**: An attacker impersonates a legitimate user, agent, or backend component to gain unauthorized access or forge verdicts.
- **Current Mitigation**: API keys are used for client authentication. HMAC is utilized for verifying the integrity and authenticity of communication.
- **Residual Risk**: Key leakage or compromise could allow spoofing.
- **Severity**: High

### Tampering (Data Integrity)
- **Threat Description**: An attacker modifies data in transit or at rest, such as changing a security verdict or modifying audit logs.
- **Current Mitigation**: Hash chains are implemented to ensure the sequential integrity of events. Ed25519 cryptographic signing is used to guarantee that verdicts and critical data originate from trusted components and have not been altered.
- **Residual Risk**: Compromise of the private signing key would allow tampering.
- **Severity**: Critical

### Repudiation (Audit Logging)
- **Threat Description**: A user or component performs an action (e.g., bypassing a control) and later denies it, with no proof to contradict them.
- **Current Mitigation**: Comprehensive audit logging with a cryptographic event chain ensures all critical actions are indelibly recorded.
- **Residual Risk**: Logs could be destroyed if the storage backend is compromised.
- **Severity**: Medium

### Information Disclosure (Secret Redaction)
- **Threat Description**: Sensitive information (API keys, PII, internal architecture details) is leaked through error messages, logs, or command outputs.
- **Current Mitigation**: Automated secret redaction, environment variable filtering, and output truncation are applied to all potentially exposed data streams.
- **Residual Risk**: Unknown secret formats or zero-day bypasses in the redaction logic could lead to leaks.
- **Severity**: High

### Denial of Service (Resource Exhaustion)
- **Threat Description**: An attacker overwhelms the system with requests or computationally expensive tasks, making it unavailable to legitimate users.
- **Current Mitigation**: Rate limiting is enforced on APIs. Strict timeouts and resource limits (CPU, memory) are applied to tool executions and sandbox environments.
- **Residual Risk**: Distributed or sophisticated attacks might still exhaust resources before limits kick in.
- **Severity**: Medium

### Elevation of Privilege (Exec Allowlist)
- **Threat Description**: An attacker or rogue agent gains higher privileges than intended, escaping the sandbox or executing arbitrary system commands.
- **Current Mitigation**: An executable allowlist restricts which commands can be run. Path containment and strict sandboxing prevent access to the underlying host system.
- **Residual Risk**: Sandbox escape vulnerabilities or misconfigurations in the allowlist.
- **Severity**: Critical

## Attack Matrix Reference
Please refer to the [Attack Matrix](ATTACK_MATRIX.md) for a detailed breakdown of specific attack vectors and test coverage.
