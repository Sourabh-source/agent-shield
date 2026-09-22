import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.models.workflow import ExecutionResult, compute_evidence_digest
from backend.tools.base_tool import BaseTool
from backend.tools.shell_tool import execute_shell_command

logger = logging.getLogger("agentguard.tools.notebook")


class NotebookTool(BaseTool):
    """
    Controlled Jupyter Notebook (.ipynb) Executor.
    Executes notebook code cells sequentially in an isolated process,
    capturing cell-by-cell execution count, positive output evidence,
    and granular failure diagnostics (cell number, exception type, traceback).
    """

    def __init__(self):
        super().__init__("notebook")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        cmd = command.strip()
        if not cmd:
            return False, "Notebook path cannot be empty"
        # Support either raw notebook filename or command 'run_notebook <file>'
        nb_target = cmd.replace("run_notebook", "").replace("execute_notebook", "").strip().strip("'\"")
        if not nb_target.endswith(".ipynb"):
            return False, f"Target file must be a Jupyter notebook (.ipynb): {nb_target}"
        return True, None

    def run(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 300,
        workflow_id: str = "unknown",
        step_name: str = "execute_notebook",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        return self.execute(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            workflow_id=workflow_id,
            step_name=step_name,
            step_id=step_id,
            env=env,
        )

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 300,
        workflow_id: str = "unknown",
        step_name: str = "execute_notebook",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        start_time = time.perf_counter()
        workspace_dir = Path(cwd or ".").resolve()
        
        # Extract target notebook path
        cmd_clean = command.strip().replace("run_notebook", "").replace("execute_notebook", "").strip().strip("'\"")
        nb_path = (workspace_dir / cmd_clean).resolve()

        if not nb_path.exists() or not nb_path.is_file():
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=f"execute_notebook {cmd_clean}",
                exit_code=1,
                stdout="",
                stderr=f"Notebook file not found: {cmd_clean}",
                duration_ms=duration_ms,
                workspace=str(workspace_dir),
                metadata={"cell_error": "file_not_found"},
            )

        # Parse notebook JSON
        try:
            with open(nb_path, "r", encoding="utf-8") as f:
                nb_data = json.load(f)
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=f"execute_notebook {cmd_clean}",
                exit_code=1,
                stdout="",
                stderr=f"Invalid notebook JSON format: {e}",
                duration_ms=duration_ms,
                workspace=str(workspace_dir),
                metadata={"cell_error": "invalid_json"},
            )

        cells = [c for c in nb_data.get("cells", []) if c.get("cell_type") == "code"]
        if not cells:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=f"execute_notebook {cmd_clean}",
                exit_code=1,
                stdout="",
                stderr="Notebook contains zero executable code cells.",
                duration_ms=duration_ms,
                workspace=str(workspace_dir),
                metadata={"total_cells": 0, "cells_executed": 0},
            )

        # Generate runner script in workspace
        runner_filename = f"_nb_runner_{step_id or 'main'}.py"
        runner_path = workspace_dir / runner_filename

        # Escape path for python string literal
        escaped_nb_path = str(nb_path).replace("\\", "\\\\")

        runner_code = f"""# Auto-generated AgentGuard Notebook Runner
import sys
import os
import json
import traceback
import io

notebook_path = "{escaped_nb_path}"

try:
    with open(notebook_path, "r", encoding="utf-8") as f:
        nb_data = json.load(f)
except Exception as e:
    print(f"Failed to read notebook: {{e}}", file=sys.stderr)
    sys.exit(1)

code_cells = [c for c in nb_data.get("cells", []) if c.get("cell_type") == "code"]
global_scope = {{"__name__": "__main__", "__file__": notebook_path}}
executed_outputs = []

for idx, cell in enumerate(code_cells, 1):
    source = "".join(cell.get("source", []))
    if not source.strip():
        continue
    
    # Strip ipython magic commands
    cleaned_lines = []
    for line in source.splitlines():
        s = line.strip()
        if s.startswith("%") or s.startswith("!"):
            continue
        cleaned_lines.append(line)
    clean_code = "\\n".join(cleaned_lines)
    if not clean_code.strip():
        continue

    stdout_buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = stdout_buf
    try:
        compiled = compile(clean_code, f"<cell_{{idx}}>", "exec")
        exec(compiled, global_scope)
    except Exception as exc:
        sys.stdout = sys.__stdout__
        tb_lines = traceback.format_exc()
        err_meta = {{
            "status": "error",
            "cell": idx,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": tb_lines,
            "cells_executed": idx - 1,
            "total_cells": len(code_cells),
        }}
        print(f"__AGENTGUARD_NB_START__\\n{{json.dumps(err_meta)}}\\n__AGENTGUARD_NB_END__", file=sys.stderr)
        print(f"Cell {{idx}} failed with {{type(exc).__name__}}: {{exc}}\\n{{tb_lines}}", file=sys.stderr)
        sys.exit(1)
    finally:
        sys.stdout = old_stdout

    captured = stdout_buf.getvalue()
    if captured:
        executed_outputs.append({{"cell": idx, "output": captured[:500]}})

success_meta = {{
    "status": "ok",
    "total_cells": len(code_cells),
    "cells_executed": len(code_cells),
    "outputs_count": len(executed_outputs),
    "captured_outputs": [o["output"] for o in executed_outputs],
}}
print(f"__AGENTGUARD_NB_START__\\n{{json.dumps(success_meta)}}\\n__AGENTGUARD_NB_END__")
print(f"Successfully executed all {{len(code_cells)}} code cells in {{notebook_path}}.")
sys.exit(0)
"""
        try:
            runner_path.write_text(runner_code, encoding="utf-8")
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return ExecutionResult(
                workflow_id=workflow_id,
                step=step_name,
                step_id=step_id,
                command=f"execute_notebook {cmd_clean}",
                exit_code=1,
                stdout="",
                stderr=f"Failed to create notebook execution harness: {e}",
                duration_ms=duration_ms,
                workspace=str(workspace_dir),
            )

        # Execute runner via execute_shell_command
        try:
            exec_res = execute_shell_command(
                command=f"python {runner_filename}",
                cwd=str(workspace_dir),
                timeout_seconds=timeout_seconds,
                workflow_id=workflow_id,
                step_name=step_name,
                step_id=step_id,
                env=env,
            )
        finally:
            # Cleanup harness
            if runner_path.exists():
                try:
                    runner_path.unlink()
                except OSError:
                    pass

        # Parse structured marker
        meta: Dict[str, Any] = {
            "notebook_file": cmd_clean,
            "total_cells": len(cells),
        }
        all_logs = f"{exec_res.stdout}\n{exec_res.stderr}"
        match = re.search(r"__AGENTGUARD_NB_START__\s*([\s\S]*?)\s*__AGENTGUARD_NB_END__", all_logs)
        if match:
            try:
                parsed_meta = json.loads(match.group(1))
                meta.update(parsed_meta)
            except Exception:
                pass

        exec_res.command = f"execute_notebook {cmd_clean}"
        if exec_res.metadata is None:
            exec_res.metadata = {}
        exec_res.metadata.update(meta)
        exec_res.evidence_digest = compute_evidence_digest(exec_res.stdout, exec_res.stderr, exec_res.exit_code)
        return exec_res
