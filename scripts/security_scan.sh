#!/bin/bash
set -euo pipefail
echo '=== AgentGuard Security Scan ==='

# Python security linting
echo '--- Running bandit ---'
python -m bandit -r backend/ -ll --format json -o bandit_report.json || true
python -m bandit -r backend/ -ll || true

# Dependency audit
echo '--- Running pip-audit ---'
python -m pip_audit --format json -o pip_audit_report.json || true
python -m pip_audit || true

# Secret scanning
echo '--- Checking for hardcoded secrets ---'
grep -rn 'password\|secret\|api_key\|token' backend/ --include='*.py' | grep -v 'test\|REDACTED\|example\|placeholder\|SENSITIVE\|PATTERN\|SECRET_PATTERNS\|BLOCKED\|setting' || echo 'No secrets found'

echo '=== Scan Complete ==='
