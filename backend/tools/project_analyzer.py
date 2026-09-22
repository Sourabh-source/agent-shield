import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from backend.models.workflow import ProjectManifest, ProjectAnalysis, ExecutionMode


IGNORE_DIRS = frozenset({
    ".git", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".next", "dist",
    "build", ".idea", ".vscode", ".mypy_cache",
})

HEAVY_ML_LIBRARIES = frozenset({
    "torch", "torchvision", "torchaudio", "tensorflow", "keras",
    "transformers", "diffusers", "cv2", "opencv-python",
    "easyocr", "pytesseract", "spacy", "nltk", "jax",
})

ML_LIBRARIES = HEAVY_ML_LIBRARIES | frozenset({
    "scikit-learn", "sklearn", "pandas", "numpy", "scipy",
    "matplotlib", "seaborn", "xgboost", "lightgbm", "statsmodels",
})


def classify_execution_mode(
    workspace_dir: Optional[str] = None,
    command: Optional[str] = None,
    manifest: Optional[ProjectManifest] = None,
    step_name: Optional[str] = None,
) -> ExecutionMode:
    """
    Classifies the execution mode (SHORT_LIVED, LONG_RUNNING, HTTP_SERVICE)
    based on command heuristics, step name, project manifest, or workspace file inspection.
    """
    cmd = (command or "").strip().lower()
    s_name = (step_name or "").strip().lower()

    # Step name explicit hints
    if any(k in s_name for k in ["long-running", "long_running", "daemon", "worker", "background"]):
        return ExecutionMode.LONG_RUNNING
    if any(k in s_name for k in ["http", "api", "web service"]):
        return ExecutionMode.HTTP_SERVICE

    # 1. Obvious test/build/compile/notebook/CLI help commands are ALWAYS SHORT_LIVED
    if any(cmd.startswith(p) for p in ["pytest", "python -m pytest", "python -m unittest", "ctest", "mvn test", "npm test", "yarn test", "pnpm test"]):
        return ExecutionMode.SHORT_LIVED
    if any(cmd.startswith(p) for p in ["npm run build", "yarn build", "pnpm build", "mvn compile", "make", "cmake", "python -m compileall"]):
        return ExecutionMode.SHORT_LIVED
    if "--help" in cmd or "-h" in cmd:
        return ExecutionMode.SHORT_LIVED

    # 2. Obvious HTTP server command patterns
    http_cmd_patterns = [
        "uvicorn",
        "gunicorn",
        "flask run",
        "runserver",
        "next dev",
        "next start",
        "catalina",
    ]
    if any(p in cmd for p in http_cmd_patterns):
        return ExecutionMode.HTTP_SERVICE

    # 3. Obvious worker / daemon command patterns
    long_running_patterns = [
        "celery worker",
        "celery -a",
        "rq worker",
        "daemon",
        "_srv.py",
        "sleep_srv",
    ]
    if any(p in cmd for p in long_running_patterns):
        return ExecutionMode.LONG_RUNNING

    # 4. If manifest is provided, leverage manifest properties
    if manifest:
        if manifest.execution_mode:
            try:
                return ExecutionMode(manifest.execution_mode)
            except ValueError:
                pass
        if manifest.is_api:
            return ExecutionMode.HTTP_SERVICE

    # 5. Inspect target script in workspace_dir if command runs a script
    if workspace_dir and Path(workspace_dir).is_dir():
        ws = Path(workspace_dir)
        target_path = None
        py_match = re.search(r"(?:python(?:3)?|node)\s+([^\s]+\.(?:py|js|ts))", command or "")
        if py_match:
            cand = ws / py_match.group(1)
            if cand.is_file():
                target_path = cand
        elif manifest and manifest.entrypoints:
            cand = ws / manifest.entrypoints[0]
            if cand.is_file():
                target_path = cand

        if target_path and target_path.is_file():
            try:
                if "_srv" in target_path.name or "server" in target_path.name:
                    return ExecutionMode.LONG_RUNNING
                content = target_path.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"(app\.run\s*\(|uvicorn\.run\s*\(|\.listen\s*\(|serve_forever\s*\(|FastAPI\s*\(|Flask\s*\()", content):
                    return ExecutionMode.HTTP_SERVICE
                if "time.sleep(" in content:
                    return ExecutionMode.LONG_RUNNING
                if re.search(r"(while\s+True:|while\s+1:|consumer\.poll|get_message)", content) and any(k in content for k in ["queue", "kafka", "redis", "consumer", "celery"]):
                    return ExecutionMode.LONG_RUNNING
            except Exception:
                pass

    return ExecutionMode.SHORT_LIVED


def _scan_files(workspace: Path, max_depth: int = 4) -> List[Path]:
    """Recursively scans workspace directory up to max_depth while pruning ignored dirs."""
    found: List[Path] = []
    
    def _walk(current: Path, depth: int):
        if depth > max_depth:
            return
        try:
            for item in current.iterdir():
                if item.name in IGNORE_DIRS:
                    continue
                if item.is_file():
                    found.append(item)
                elif item.is_dir():
                    _walk(item, depth + 1)
        except (PermissionError, OSError):
            pass

    _walk(workspace, 1)
    return found


def analyze_workspace(workspace_dir: str) -> ProjectManifest:
    """
    Deep Project Detection:
    Inspects workspace directory and identifies programming language, runtime,
    framework, package manager, entrypoints, build/test/startup commands,
    Jupyter notebooks, ML characteristics, and health endpoints.
    """
    workspace = Path(workspace_dir).resolve()
    if not workspace.exists() or not workspace.is_dir():
        return ProjectManifest(
            language="unknown",
            runtime="unknown",
            package_manager="none",
            dependency_manager="none",
            details={"error": f"Directory does not exist: {workspace_dir}"},
        )

    all_files = _scan_files(workspace)
    rel_files = [str(f.relative_to(workspace)).replace("\\", "/") for f in all_files]
    top_level_files = [f.name for f in workspace.iterdir() if f.is_file()]

    manifest = ProjectManifest(
        detected_files=rel_files[:200],  # cap list
        details={},
    )

    # -------------------------------------------------------------
    # 1. Jupyter Notebooks Detection
    # -------------------------------------------------------------
    notebook_files = [rf for rf in rel_files if rf.endswith(".ipynb")]
    if notebook_files:
        manifest.notebooks = notebook_files
        manifest.is_notebook = True

    # -------------------------------------------------------------
    # 2. Node.js / Vite / Next.js Detection
    # -------------------------------------------------------------
    if "package.json" in top_level_files or any(rf.endswith("package.json") for rf in rel_files):
        manifest.language = "javascript"
        manifest.runtime = "node"
        
        pkg_file = workspace / "package.json"
        if not pkg_file.exists():
            # pick first package.json found
            for rf in rel_files:
                if rf.endswith("package.json"):
                    pkg_file = workspace / rf
                    break

        scripts: Dict[str, str] = {}
        deps: Dict[str, str] = {}
        dev_deps: Dict[str, str] = {}

        if pkg_file.exists():
            try:
                with open(pkg_file, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                    scripts = pkg_data.get("scripts", {})
                    deps = pkg_data.get("dependencies", {})
                    dev_deps = pkg_data.get("devDependencies", {})
            except Exception:
                pass

        if "typescript" in deps or "typescript" in dev_deps or any(rf.endswith((".ts", ".tsx")) for rf in rel_files):
            manifest.language = "typescript"

        # Frameworks
        if "next" in deps:
            manifest.framework = "nextjs"
            manifest.is_api = True
        elif "vite" in deps or "vite" in dev_deps or any("vite.config" in rf for rf in rel_files):
            manifest.framework = "vite"
        elif "express" in deps or "fastify" in deps or "koa" in deps:
            manifest.framework = "express" if "express" in deps else "fastify"
            manifest.is_api = True

        # Package manager & lockfile
        if any(rf == "pnpm-lock.yaml" for rf in rel_files):
            manifest.package_manager = "pnpm"
            manifest.dependency_manager = "pnpm"
            manifest.install_command = "pnpm install"
            pkg_run = "pnpm run"
        elif any(rf == "yarn.lock" for rf in rel_files):
            manifest.package_manager = "yarn"
            manifest.dependency_manager = "yarn"
            manifest.install_command = "yarn install"
            pkg_run = "yarn"
        else:
            manifest.package_manager = "npm"
            manifest.dependency_manager = "npm"
            manifest.install_command = "npm install"
            pkg_run = "npm run"

        manifest.dependency_files = [rf for rf in rel_files if rf in ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml")]

        # Build command
        if "build" in scripts:
            manifest.build_command = f"{pkg_run} build"
            manifest.build_commands.append(manifest.build_command)

        # Test command
        if "test" in scripts:
            manifest.test_command = f"{pkg_run} test"
            manifest.test_commands.append(manifest.test_command)

        # Start command & health
        if "start" in scripts:
            manifest.start_command = f"{pkg_run} start"
            manifest.application_startup_commands.append(manifest.start_command)
        elif "dev" in scripts:
            manifest.start_command = f"{pkg_run} dev"
            manifest.application_startup_commands.append(manifest.start_command)
        elif (workspace / "index.js").exists():
            manifest.start_command = "node index.js"
            manifest.application_startup_commands.append("node index.js")

        if manifest.is_api:
            manifest.execution_mode = ExecutionMode.HTTP_SERVICE.value
        else:
            manifest.execution_mode = ExecutionMode.SHORT_LIVED.value
        manifest.health_check_url = "http://localhost:3000"
        manifest.likely_health_endpoints = ["http://localhost:3000", "http://localhost:3000/api/health"]
        return manifest

    # -------------------------------------------------------------
    # 3. Python / FastAPI / ML / Jupyter Detection
    # -------------------------------------------------------------
    python_indicators = [
        "requirements.txt",
        "pyproject.toml",
        "Pipfile",
        "setup.py",
        "setup.cfg",
        "environment.yml",
    ]
    py_files = [rf for rf in rel_files if rf.endswith(".py")]
    has_python = bool(py_files) or any(any(rf.endswith(ind) for ind in python_indicators) for rf in rel_files)

    if has_python or manifest.is_notebook:
        manifest.language = "python"
        manifest.runtime = "python"

        # Locate dependency manifests
        dep_files = [rf for rf in rel_files if any(rf.endswith(ind) for ind in python_indicators)]
        manifest.dependency_files = dep_files

        # Identify Package manager & install command
        if any(rf.endswith("Pipfile") for rf in dep_files):
            manifest.package_manager = "pipenv"
            manifest.dependency_manager = "pipenv"
            manifest.install_command = "pipenv install"
        elif any(rf.endswith("poetry.lock") for rf in rel_files) or any("tool.poetry" in rf for rf in dep_files):
            manifest.package_manager = "poetry"
            manifest.dependency_manager = "poetry"
            manifest.install_command = "poetry install"
        elif any(rf.endswith("requirements.txt") for rf in dep_files):
            # pick primary requirements.txt
            primary_req = next((rf for rf in dep_files if rf == "requirements.txt"), dep_files[0])
            manifest.package_manager = "pip"
            manifest.dependency_manager = "pip"
            manifest.install_command = f"pip install -r {primary_req}"
        elif dep_files:
            manifest.package_manager = "pip"
            manifest.dependency_manager = "pip"
            manifest.install_command = f"pip install -r {dep_files[0]}"
        else:
            manifest.package_manager = "pip"
            manifest.dependency_manager = "pip"
            manifest.install_command = None

        # Content scanning for ML, FastAPI, Flask, CLI across python files & requirements
        imported_modules: Set[str] = set()
        for py_path in all_files:
            if not py_path.name.endswith(".py"):
                continue
            try:
                content = py_path.read_text(encoding="utf-8", errors="ignore")
                for m in re.findall(r"^\s*(?:import|from)\s+([a-zA-Z0-9_]+)", content, re.MULTILINE):
                    imported_modules.add(m.lower())
            except Exception:
                pass

        # Scan requirements.txt content
        req_packages: Set[str] = set()
        for dep_file in dep_files:
            if "requirements" in dep_file:
                try:
                    r_text = (workspace / dep_file).read_text(encoding="utf-8", errors="ignore")
                    for line in r_text.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            pkg_name = re.split(r"[><=~;\[]", line)[0].strip().lower().replace("-", "_")
                            req_packages.add(pkg_name)
                except Exception:
                    pass

        combined_deps = imported_modules | req_packages

        # ML detection (OilSplit, document-ai-backend OCR)
        if any(lib in combined_deps for lib in ML_LIBRARIES):
            manifest.is_ml = True
            if any(lib in combined_deps for lib in HEAVY_ML_LIBRARIES):
                manifest.heavy_dependencies = True

        # FastAPI detection (document-ai-backend)
        is_fastapi = "fastapi" in combined_deps or "uvicorn" in combined_deps
        is_flask = "flask" in combined_deps

        # Detect entrypoints
        candidate_entrypoints = [
            "app/main.py",
            "main.py",
            "app.py",
            "server.py",
            "run.py",
            "src/main.py",
            "api.py",
        ]
        entrypoints: List[str] = []
        for cand in candidate_entrypoints:
            if cand in rel_files or (workspace / cand).exists():
                entrypoints.append(cand)
        # Fallback to any file with __main__
        if not entrypoints and py_files:
            entrypoints = [py_files[0]]

        manifest.entrypoints = entrypoints

        # Framework specifics
        if is_fastapi:
            manifest.framework = "fastapi"
            manifest.is_api = True
            manifest.execution_mode = ExecutionMode.HTTP_SERVICE.value
            primary_ep = entrypoints[0] if entrypoints else "main.py"
            # Format uvicorn module target: app/main.py -> app.main:app
            mod_target = primary_ep.replace("/", ".").replace("\\", ".")
            if mod_target.endswith(".py"):
                mod_target = mod_target[:-3]
            manifest.start_command = f"python -m uvicorn {mod_target}:app --port 8000"
            manifest.application_startup_commands.append(manifest.start_command)
            manifest.health_check_url = "http://localhost:8000/health"
            manifest.likely_health_endpoints = [
                "http://localhost:8000/health",
                "http://localhost:8000/docs",
                "http://localhost:8000/",
            ]
            manifest.http_endpoints = ["/health", "/docs", "/openapi.json", "/"]
        elif is_flask:
            manifest.framework = "flask"
            primary_ep = entrypoints[0] if entrypoints else "app.py"
            # Inspect primary_ep to see if it actually starts a server
            ep_file = workspace / primary_ep
            ep_has_server = False
            if ep_file.exists():
                try:
                    ep_text = ep_file.read_text(encoding="utf-8", errors="ignore")
                    ep_has_server = bool(re.search(r"(app\.run\s*\(|server\.run\s*\(|\brun\s*\(|Flask\s*\()", ep_text))
                except Exception:
                    pass
            if ep_has_server or "flask run" in (manifest.start_command or ""):
                manifest.is_api = True
                manifest.execution_mode = ExecutionMode.HTTP_SERVICE.value
                manifest.start_command = f"python {primary_ep}"
                manifest.application_startup_commands.append(manifest.start_command)
                manifest.health_check_url = "http://localhost:8000/health"
                manifest.likely_health_endpoints = [
                    "http://localhost:8000/health",
                    "http://localhost:8000/",
                ]
            else:
                manifest.execution_mode = ExecutionMode.SHORT_LIVED.value
                manifest.start_command = f"python {primary_ep}"
                manifest.application_startup_commands.append(manifest.start_command)
        elif entrypoints and not manifest.is_notebook:
            primary_ep = entrypoints[0]
            manifest.start_command = f"python {primary_ep}"
            manifest.application_startup_commands.append(manifest.start_command)
            ep_file = workspace / primary_ep
            if ep_file.exists():
                try:
                    ep_text = ep_file.read_text(encoding="utf-8", errors="ignore")
                    if re.search(r"(app\.run\s*\(|uvicorn\.run\s*\(|\.listen\s*\(|serve_forever\s*\(|FastAPI\s*\(|Flask\s*\()", ep_text):
                        manifest.execution_mode = ExecutionMode.HTTP_SERVICE.value
                        manifest.is_api = True
                    elif re.search(r"(while\s+True:|while\s+1:|consumer\.poll|get_message)", ep_text) and any(k in ep_text for k in ["queue", "kafka", "redis", "consumer", "celery"]):
                        manifest.execution_mode = ExecutionMode.LONG_RUNNING.value
                    else:
                        manifest.execution_mode = ExecutionMode.SHORT_LIVED.value
                except Exception:
                    manifest.execution_mode = ExecutionMode.SHORT_LIVED.value
            else:
                manifest.execution_mode = ExecutionMode.SHORT_LIVED.value
        else:
            manifest.execution_mode = ExecutionMode.SHORT_LIVED.value

        # CLI detection
        if any(cli_lib in combined_deps for cli_lib in ("click", "typer", "argparse")):
            manifest.is_cli = True
            if entrypoints:
                manifest.smoke_test_command = f"python {entrypoints[0]} --help"

        # Build / Compile commands
        if any(rf in ("setup.py", "pyproject.toml") for rf in rel_files):
            manifest.build_command = "python -m pip install -e ."
            manifest.build_commands.append(manifest.build_command)
        
        # Compile command for positive evidence
        if any(rf.startswith("app/") for rf in py_files):
            manifest.compile_command = "python -m compileall app"
        elif any(rf.startswith("src/") for rf in py_files):
            manifest.compile_command = "python -m compileall src"
        elif py_files:
            manifest.compile_command = "python -m compileall ."

        if manifest.compile_command and not manifest.build_command:
            manifest.build_command = manifest.compile_command
            manifest.build_commands.append(manifest.compile_command)

        # Import check command
        if entrypoints:
            ep_mod = entrypoints[0].replace("/", ".").replace("\\", ".")
            if ep_mod.endswith(".py"):
                ep_mod = ep_mod[:-3]
            manifest.import_check_command = ep_mod

        # Test command
        tests_exist = any(rf.startswith("tests/") or rf.startswith("test/") for rf in rel_files)
        has_test_files = any(f.startswith("test_") or f.endswith("_test.py") for f in [Path(rf).name for rf in py_files])
        if tests_exist or has_test_files:
            manifest.test_command = "python -m pytest"
            manifest.test_commands.append("python -m pytest")
        elif not manifest.is_notebook and not manifest.is_api:
            manifest.test_command = "python -m unittest discover"
            manifest.test_commands.append("python -m unittest discover")

        # If Jupyter notebook is the primary artifact (e.g. Medical_Insurance_Regression)
        if manifest.is_notebook and not is_fastapi and not is_flask and not tests_exist:
            manifest.runtime = "jupyter"

        return manifest

    # -------------------------------------------------------------
    # 4. Java Detection
    # -------------------------------------------------------------
    if any(rf == "pom.xml" for rf in rel_files) or any(rf.endswith(".java") for rf in rel_files):
        manifest.language = "java"
        manifest.runtime = "java"
        if any(rf == "pom.xml" for rf in rel_files):
            manifest.package_manager = "maven"
            manifest.dependency_manager = "maven"
            manifest.install_command = "mvn dependency:resolve"
            manifest.build_command = "mvn compile"
            manifest.test_command = "mvn test"
        elif any("gradle" in rf for rf in rel_files):
            manifest.package_manager = "gradle"
            manifest.dependency_manager = "gradle"
            manifest.build_command = "gradle build"
            manifest.test_command = "gradle test"
        return manifest

    # -------------------------------------------------------------
    # 5. C / C++ Detection
    # -------------------------------------------------------------
    if any(rf in ("Makefile", "CMakeLists.txt") for rf in rel_files) or any(rf.endswith((".c", ".cpp", ".cc", ".h", ".hpp")) for rf in rel_files):
        manifest.language = "c_cpp"
        manifest.runtime = "c_cpp"
        manifest.package_manager = "make"
        manifest.dependency_manager = "make"
        if any(rf == "CMakeLists.txt" for rf in rel_files):
            manifest.build_command = "cmake --build build"
            manifest.test_command = "ctest"
        else:
            manifest.build_command = "make"
            manifest.test_command = "make test"
        return manifest

    # -------------------------------------------------------------
    # 6. Fallback Generic
    # -------------------------------------------------------------
    manifest.language = "generic"
    manifest.runtime = "generic"
    manifest.package_manager = "none"
    manifest.dependency_manager = "none"
    return manifest


class ProjectAnalyzerTool:
    """Wrapper class providing object-oriented access to project analysis."""

    def analyze(self, workspace_dir: str) -> ProjectManifest:
        return analyze_workspace(workspace_dir)

    def run(self, workspace_dir: str):
        manifest = analyze_workspace(workspace_dir)
        from backend.models.workflow import ExecutionResult
        return ExecutionResult(
            command=f"analyze {workspace_dir}",
            exit_code=0,
            stdout=manifest.model_dump_json(),
            stderr="",
        )

