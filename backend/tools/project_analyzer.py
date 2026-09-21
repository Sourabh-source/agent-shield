import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from backend.models.workflow import ProjectAnalysis


def analyze_workspace(workspace_dir: str) -> ProjectAnalysis:
    """
    Inspects workspace directory and identifies programming language,
    package manager, and appropriate build, test, and start commands.
    """
    workspace = Path(workspace_dir).resolve()
    if not workspace.exists() or not workspace.is_dir():
        return ProjectAnalysis(
            language="unknown",
            package_manager="none",
            details={"error": f"Directory does not exist: {workspace_dir}"},
        )

    detected_files = [f.name for f in workspace.iterdir()]
    analysis = ProjectAnalysis(detected_files=detected_files)

    # 1. Node.js detection
    if "package.json" in detected_files:
        analysis.language = "javascript"
        pkg_file = workspace / "package.json"
        scripts: Dict[str, str] = {}
        try:
            with open(pkg_file, "r", encoding="utf-8") as f:
                pkg_data = json.load(f)
                scripts = pkg_data.get("scripts", {})
                if pkg_data.get("dependencies", {}).get("typescript") or pkg_data.get("devDependencies", {}).get("typescript"):
                    analysis.language = "typescript"
        except Exception:
            pass

        # Package manager
        if "pnpm-lock.yaml" in detected_files:
            analysis.package_manager = "pnpm"
            analysis.install_command = "pnpm install"
            pkg_run = "pnpm run"
        elif "yarn.lock" in detected_files:
            analysis.package_manager = "yarn"
            analysis.install_command = "yarn install"
            pkg_run = "yarn"
        else:
            analysis.package_manager = "npm"
            analysis.install_command = "npm install"
            pkg_run = "npm run"

        # Commands
        if "build" in scripts:
            analysis.build_command = f"{pkg_run} build"
        else:
            analysis.build_command = None

        if "test" in scripts:
            analysis.test_command = f"{pkg_run} test"
        else:
            analysis.test_command = None

        if "start" in scripts:
            analysis.start_command = f"{pkg_run} start"
        elif "dev" in scripts:
            analysis.start_command = f"{pkg_run} dev"
        else:
            analysis.start_command = "node index.js" if (workspace / "index.js").exists() else None

        analysis.health_check_url = "http://localhost:3000"
        return analysis

    # 2. Python detection
    python_indicators = [
        "requirements.txt",
        "pyproject.toml",
        "Pipfile",
        "setup.py",
        "setup.cfg",
    ]
    has_python_files = any(f.endswith(".py") for f in detected_files) or any(
        ind in detected_files for ind in python_indicators
    )

    if has_python_files:
        analysis.language = "python"
        
        # Package manager & install command
        if "Pipfile" in detected_files:
            analysis.package_manager = "pipenv"
            analysis.install_command = "pipenv install"
        elif "poetry.lock" in detected_files:
            analysis.package_manager = "poetry"
            analysis.install_command = "poetry install"
        elif "requirements.txt" in detected_files:
            analysis.package_manager = "pip"
            analysis.install_command = "pip install -r requirements.txt"
        else:
            analysis.package_manager = "pip"
            analysis.install_command = None

        # Build command
        if "setup.py" in detected_files or "pyproject.toml" in detected_files:
            analysis.build_command = "python -m pip install -e ."
        else:
            analysis.build_command = None

        # Test command
        tests_exist = (workspace / "tests").exists() or (workspace / "test").exists()
        has_test_files = any(f.startswith("test_") or f.endswith("_test.py") for f in detected_files)
        if tests_exist or has_test_files:
            analysis.test_command = "pytest"
        else:
            analysis.test_command = "python -m unittest discover"

        # Start command
        if "main.py" in detected_files:
            analysis.start_command = "python main.py"
        elif "app.py" in detected_files:
            analysis.start_command = "python app.py"
        elif "server.py" in detected_files:
            analysis.start_command = "python server.py"
        else:
            analysis.start_command = None

        analysis.health_check_url = "http://localhost:8000/health"
        return analysis

    # 3. Fallback generic project
    analysis.language = "generic"
    analysis.package_manager = "unknown"
    analysis.install_command = None
    analysis.build_command = None
    analysis.test_command = None
    analysis.start_command = None
    return analysis
