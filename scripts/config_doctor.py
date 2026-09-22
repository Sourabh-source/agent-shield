#!/usr/bin/env python3
"""AgentGuard Configuration Doctor \u2014 validates deployment readiness."""
import sys
import os
from pathlib import Path

def check_config():
    issues = []
    warnings = []
    
    # Check required secrets
    if not os.getenv('GEMINI_API_KEY'):
        warnings.append('GEMINI_API_KEY not set (LLM planner will use fallback)')
    
    hmac = os.getenv('VERIFY_HMAC_SECRET', '')
    if hmac == 'agentguard-hmac-secret-key-prod' or not hmac:
        issues.append('VERIFY_HMAC_SECRET uses default value \u2014 MUST be changed for production')
    
    # Check API keys
    if 'test-api-key' in os.getenv('API_KEYS', 'test-api-key'):
        warnings.append('Default test-api-key is still configured')
    
    # Check workspace dir
    ws = os.getenv('WORKSPACE_BASE_DIR', '')
    if ws and not Path(ws).exists():
        issues.append(f'WORKSPACE_BASE_DIR {ws} does not exist')
    
    # Check database
    db = os.getenv('DATABASE_PATH', '')
    if db:
        db_dir = Path(db).parent
        if not db_dir.exists():
            issues.append(f'DATABASE_PATH parent directory {db_dir} does not exist')
    
    # Check auth
    if os.getenv('REQUIRE_AUTH', 'true').lower() not in ('true', '1', 'yes'):
        issues.append('REQUIRE_AUTH is disabled \u2014 all endpoints are unprotected')
    
    # Report
    print('=== AgentGuard Config Doctor ===')
    if issues:
        print(f'\n[X] {len(issues)} ISSUE(S):')
        for i in issues:
            print(f'  - {i}')
    if warnings:
        print(f'\n[!]  {len(warnings)} WARNING(S):')
        for w in warnings:
            print(f'  - {w}')
    if not issues and not warnings:
        print('\n[+] All checks passed!')
    
    return 1 if issues else 0

if __name__ == '__main__':
    sys.exit(check_config())
