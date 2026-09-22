#!/usr/bin/env python3
"""
scripts/check_requirements.py
Statically scans all import and from-import statements under backend/ and tests/,
and verifies that every third-party top-level package is declared in
requirements.txt or requirements-dev.txt.
"""
import ast
import os
import re
import sys
from pathlib import Path
from typing import Set

# Packages provided by Python standard library (cross-version baseline)
STDLIB_EXTRA = {
    "posixpath", "ntpath", "_winapi", "typing_extensions"
}

# Package name mappings: imported name -> distribution package name in requirements
PACKAGE_MAPPINGS = {
    "google": "google-genai",
    "pydantic_settings": "pydantic-settings",
    "starlette": "fastapi", # provided by fastapi
    "prometheus_client": "prometheus-client",
    "dotenv": "python-dotenv",
}


def parse_requirements_file(path: Path) -> Set[str]:
    """Extract normalized package names from requirements file."""
    if not path.exists():
        return set()
    packages = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            nested_path = path.parent / line[3:].strip()
            packages.update(parse_requirements_file(nested_path))
            continue
        # Extract package name before any specifier (>=, ==, <=, ;, etc.)
        match = re.match(r"^([a-zA-Z0-9_\-\.]+)", line)
        if match:
            pkg = match.group(1).lower().replace("_", "-")
            packages.add(pkg)
    return packages


def find_imports_in_dir(directory: Path) -> Set[str]:
    """Find all top-level module names imported in python files within directory."""
    imported_modules = set()
    stdlib = set(sys.stdlib_module_names) | STDLIB_EXTRA

    for root, _, files in os.walk(directory):
        for f in files:
            if not f.endswith(".py"):
                continue
            file_path = Path(root) / f
            try:
                content = file_path.read_text(encoding="utf-8-sig")
                tree = ast.parse(content, filename=str(file_path))
            except Exception as e:
                print(f"Warning: could not parse {file_path}: {e}")
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top not in stdlib and top not in ("backend", "scripts", "tests"):
                            imported_modules.add(top)
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        top = node.module.split(".")[0]
                        if top not in stdlib and top not in ("backend", "scripts", "tests"):
                            imported_modules.add(top)

    return imported_modules


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    backend_req = repo_root / "backend" / "requirements.txt"
    root_req = repo_root / "requirements.txt"
    dev_req = repo_root / "requirements-dev.txt"

    prod_packages = parse_requirements_file(root_req) | parse_requirements_file(backend_req)
    dev_packages = parse_requirements_file(dev_req)
    all_packages = prod_packages | dev_packages

    backend_imports = find_imports_in_dir(repo_root / "backend")
    test_imports = find_imports_in_dir(repo_root / "tests")

    print(f"Declared production packages: {sorted(prod_packages)}")
    print(f"Declared dev/test packages:   {sorted(dev_packages)}")
    print(f"Backend imports detected:     {sorted(backend_imports)}")
    print(f"Test imports detected:        {sorted(test_imports)}")

    missing_backend = set()
    for mod in backend_imports:
        mapped = PACKAGE_MAPPINGS.get(mod, mod).lower().replace("_", "-")
        if mapped not in prod_packages:
            missing_backend.add(f"{mod} (needs '{mapped}' in requirements.txt)")

    missing_tests = set()
    for mod in test_imports:
        mapped = PACKAGE_MAPPINGS.get(mod, mod).lower().replace("_", "-")
        if mapped not in all_packages:
            missing_tests.add(f"{mod} (needs '{mapped}' in requirements-dev.txt or requirements.txt)")

    has_errors = False
    if missing_backend:
        print("\n[FAIL] Missing backend requirements in requirements.txt:")
        for m in sorted(missing_backend):
            print(f"   - {m}")
        has_errors = True

    if missing_tests:
        print("\n[FAIL] Missing test requirements in requirements-dev.txt:")
        for m in sorted(missing_tests):
            print(f"   - {m}")
        has_errors = True

    if not has_errors:
        print("\n[PASS] All imports are declared in requirements.txt / requirements-dev.txt!")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
