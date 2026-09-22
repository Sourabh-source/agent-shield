#!/usr/bin/env bash
# ==============================================================================
# AgentGuard Automated Adversarial Redteam Test Suite
# Tests security controls, auth, isolation, sanitization, and verification
# ==============================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
VALID_API_KEY="${API_KEY:-test-api-key}"
INVALID_API_KEY="evil-hacker-key-999"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0

assert_status() {
    local test_name="$1"
    local expected="$2"
    local actual="$3"

    if [ "$actual" -eq "$expected" ]; then
        echo -e "${GREEN}[PASS]${NC} $test_name (HTTP $actual)"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}[FAIL]${NC} $test_name (Expected HTTP $expected, got $actual)"
        FAILED=$((FAILED + 1))
    fi
}

echo "========================================================================"
echo "AgentGuard Security Redteam Adversarial Verification"
echo "Target: $BASE_URL"
echo "========================================================================"

# ------------------------------------------------------------------------------
# 1. AuthN & Rate Limiting Attacks
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[+] Testing Authentication & Rate Limiting Controls...${NC}"

# Test 1.1: Unauthenticated request to protected endpoint
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/workflow/start" \
    -H "Content-Type: application/json" \
    -d '{"repo_url":"https://github.com/octocat/Hello-World","task":"test"}')
assert_status "Unauthenticated request rejected with 401" 401 "$STATUS"

# Test 1.2: Invalid API key rejected
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/workflow/start" \
    -H "X-API-Key: $INVALID_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"repo_url":"https://github.com/octocat/Hello-World","task":"test"}')
assert_status "Invalid API key rejected with 401" 401 "$STATUS"

# Test 1.3: Health endpoint is publicly accessible
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$BASE_URL/health")
assert_status "Health check exempt from auth" 200 "$STATUS"

# ------------------------------------------------------------------------------
# 2. Command Injection & Metacharacter Attacks
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[+] Testing Command Injection & Metacharacter Rejection...${NC}"

INJECTION_PAYLOADS=(
    "https://github.com/octocat/Hello-World; rm -rf /"
    "https://github.com/octocat/Hello-World && whoami"
    "https://github.com/octocat/Hello-World | cat /etc/passwd"
    "https://github.com/octocat/Hello-World \`id\`"
    "https://github.com/octocat/Hello-World \$(calc.exe)"
    "--upload-pack=evil"
)

for payload in "${INJECTION_PAYLOADS[@]}"; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/workflow/start" \
        -H "X-API-Key: $VALID_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"repo_url\":\"$payload\",\"task\":\"test\"}")
    assert_status "Injection payload rejected with 400: $payload" 400 "$STATUS"
done

# ------------------------------------------------------------------------------
# 3. SSRF & Protocol Smuggling Attacks
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[+] Testing SSRF & Protocol Smuggling Protections...${NC}"

SSRF_PAYLOADS=(
    "http://169.254.169.254/latest/meta-data/"
    "file:///etc/passwd"
    "ext::sh -c whoami"
    "ssh://git@github.com/repo"
)

for payload in "${SSRF_PAYLOADS[@]}"; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/workflow/start" \
        -H "X-API-Key: $VALID_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"repo_url\":\"$payload\",\"task\":\"test\"}")
    assert_status "SSRF / Protocol payload rejected with 400: $payload" 400 "$STATUS"
done

# ------------------------------------------------------------------------------
# 4. Multi-Tenant Anti-Enumeration & Authorization
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[+] Testing Multi-Tenant Resource Isolation...${NC}"

# Query non-existent or other tenant's workflow returns 404 (not 403 or data leak)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X GET "$BASE_URL/workflow/non-existent-wf-id/status" \
    -H "X-API-Key: $VALID_API_KEY")
assert_status "Non-existent workflow returns 404 anti-enumeration" 404 "$STATUS"

# ------------------------------------------------------------------------------
# 5. Verification Gate HMAC Tamper Protection
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[+] Testing Verification Gate HMAC Tamper-Evidence...${NC}"

STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/workflow/test-wf/verify" \
    -H "X-API-Key: $VALID_API_KEY" \
    -H "X-AgentGuard-Verify-Signature: evil-forged-hmac-signature" \
    -H "Content-Type: application/json" \
    -d '{"step_id":"s1","verification_result":{"verified":true,"reason":"Forged pass"}}')
# Should fail with 403 or 404 (due to HMAC mismatch or unknown ID)
if [ "$STATUS" -eq 403 ] || [ "$STATUS" -eq 404 ]; then
    echo -e "${GREEN}[PASS]${NC} Forged verification signature rejected (HTTP $STATUS)"
    PASSED=$((PASSED + 1))
else
    echo -e "${RED}[FAIL]${NC} Forged verification accepted or unverified (HTTP $STATUS)"
    FAILED=$((FAILED + 1))
fi

echo "========================================================================"
echo -e "Redteam Summary: ${GREEN}$PASSED Passed${NC}, ${RED}$FAILED Failed${NC}"
echo "========================================================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
