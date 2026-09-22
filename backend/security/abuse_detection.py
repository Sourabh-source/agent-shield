"""CPU and resource abuse detection for sandboxed workflows."""
import re
from typing import Tuple, Optional

# Known crypto-miner process names and patterns
MINER_PATTERNS = [
    r'\bxmrig\b', r'\bminerd\b', r'\bcpuminer\b', r'\bethminer\b',
    r'\bnbminer\b', r'\bphoenixminer\b', r'\bt-rex\b', r'\blolminer\b',
    r'\bstratum\+tcp\b', r'\bstratum\+ssl\b', r'\bmining\.pool\b',
    r'\bnicehash\b', r'\bcoinbase.*wallet\b',
]

def detect_mining_activity(stdout: str, stderr: str, command: str) -> Tuple[bool, Optional[str]]:
    """Scan output and command for crypto-mining indicators."""
    combined = f"{command} {stdout} {stderr}".lower()
    for pattern in MINER_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return True, f"Crypto-mining activity detected: pattern '{pattern}' matched"
    return False, None


def check_cpu_budget(duration_ms: float, max_cpu_ms: float = 300_000) -> Tuple[bool, Optional[str]]:
    """Check if a workflow step exceeded its CPU time budget."""
    if duration_ms > max_cpu_ms:
        return True, f"CPU budget exceeded: {duration_ms:.0f}ms > {max_cpu_ms:.0f}ms limit"
    return False, None
