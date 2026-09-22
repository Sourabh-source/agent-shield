"""
scripts/verify_security_review.py
Automated Honesty Enforcement & Security Claim Verification.
Parses SECURITY_REVIEW.md, extracts all rows claiming MITIGATED status,
resolves their cited test targets, and executes pytest against each citation.
Fails with exit code 1 if ANY claim lacks an automated test or if any cited test fails.
"""
import os
import re
import subprocess
import sys
from pathlib import Path


def main():
    root_dir = Path(__file__).resolve().parent.parent
    sec_review_path = root_dir / "SECURITY_REVIEW.md"

    if not sec_review_path.exists():
        print(f"ERROR: {sec_review_path} does not exist", file=sys.stderr)
        sys.exit(1)

    content = sec_review_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    mitigated_claims = []
    
    # Parse markdown table rows
    for idx, line in enumerate(lines, 1):
        if not line.strip().startswith("|") or line.strip().startswith("| :---"):
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) < 4:
            continue

        # Check if any cell indicates MITIGATED
        has_mitigated = any("MITIGATED" in cell.upper() for cell in parts)
        if not has_mitigated:
            continue

        # Locate the test citation (looks for tests/... or test_...)
        test_citation = None
        for cell in parts:
            clean_cell = cell.strip("`* ")
            match = re.search(r"(tests/[a-zA-Z0-9_\-/\.:]+)", clean_cell)
            if match:
                test_citation = match.group(1)
                break

        row_label = parts[0].strip("`* ")
        mitigated_claims.append({
            "line": idx,
            "label": row_label,
            "citation": test_citation,
            "raw": line,
        })

    if not mitigated_claims:
        print("ERROR: No MITIGATED claims found in SECURITY_REVIEW.md table", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(mitigated_claims)} MITIGATED claim(s) in SECURITY_REVIEW.md:")
    missing_citations = []
    for claim in mitigated_claims:
        if not claim["citation"]:
            missing_citations.append(claim)
            print(f"  [X] Line {claim['line']}: '{claim['label']}' claims MITIGATED but has NO test citation!")
        else:
            print(f"  [v] Line {claim['line']}: '{claim['label']}' -> {claim['citation']}")

    if missing_citations:
        print(f"\nFAIL: {len(missing_citations)} claims marked MITIGATED lack an automated test citation.", file=sys.stderr)
        sys.exit(1)

    print("\nRunning automated tests for all cited MITIGATED claims...")
    all_passed = True
    failed_claims = []

    # Locate pytest in virtualenv or environment
    pytest_bin = None
    venv_pytest_nt = root_dir / ".venv" / "Scripts" / "pytest.exe"
    venv_pytest_posix = root_dir / ".venv" / "bin" / "pytest"
    if venv_pytest_nt.exists():
        pytest_cmd_prefix = [str(venv_pytest_nt)]
    elif venv_pytest_posix.exists():
        pytest_cmd_prefix = [str(venv_pytest_posix)]
    else:
        pytest_cmd_prefix = [sys.executable, "-m", "pytest"]

    for claim in mitigated_claims:
        citation = claim["citation"]
        test_target = root_dir / citation.split("::")[0]
        if not test_target.exists():
            print(f"FAIL: Test file does not exist: {test_target}", file=sys.stderr)
            failed_claims.append(claim)
            all_passed = False
            continue

        cmd = pytest_cmd_prefix + [citation, "-q", "--tb=short"]
        proc = subprocess.run(cmd, cwd=root_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if proc.returncode != 0:
            print(f"\nFAIL: Claim '{claim['label']}' test citation failed: {citation}")
            print(proc.stdout)
            print(proc.stderr)
            failed_claims.append(claim)
            all_passed = False
        else:
            print(f"  PASSED: {claim['label']} ({citation})")

    if not all_passed:
        print(f"\nVERIFICATION FAILED: {len(failed_claims)} claim(s) failed automated test verification.", file=sys.stderr)
        sys.exit(1)

    print(f"\nALL {len(mitigated_claims)} MITIGATED CLAIMS VERIFIED SUCCESSFULLY WITH AUTOMATED TESTS.")
    sys.exit(0)


if __name__ == "__main__":
    main()
