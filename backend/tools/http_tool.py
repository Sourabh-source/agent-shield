import time
from typing import Optional
from urllib.parse import urlparse
import httpx

from backend.models.workflow import ExecutionResult, current_iso_time


def perform_health_check(
    url: str,
    timeout_seconds: int = 5,
    workflow_id: str = "unknown",
    step_id: Optional[str] = None,
) -> ExecutionResult:
    """
    Performs an HTTP GET health check on the specified URL.
    Returns structured ExecutionResult:
    exit_code == 0 if response status is 2xx, 1 otherwise.
    """
    # SSRF Protection: block cloud metadata service IP/hostnames
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname in ["169.254.169.254", "metadata.google.internal", "metadata", "instance-data"]:
            return ExecutionResult(
                workflow_id=workflow_id,
                step="health_check",
                step_id=step_id,
                command=f"GET {url}",
                exit_code=126,
                stdout="",
                stderr="Security blocked: Access to cloud instance metadata service is prohibited (SSRF prevention).",
                workspace=None,
                metadata={"security_blocked": True},
            )
    except Exception:
        pass

    start_time = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(url)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            is_success = 200 <= response.status_code < 300
            exit_code = 0 if is_success else 1
            body_snippet = response.text[:500] if response.text else ""

            stdout_msg = (
                f"HTTP {response.status_code} OK from {url}\n{body_snippet}"
                if is_success
                else ""
            )
            stderr_msg = (
                ""
                if is_success
                else f"HTTP {response.status_code} Error from {url}\n{body_snippet}"
            )

            # Redact sensitive response headers
            safe_headers = {
                k: ("***REDACTED***" if k.lower() in ["set-cookie", "authorization"] else v)
                for k, v in response.headers.items()
            }

            return ExecutionResult(
                workflow_id=workflow_id,
                step="health_check",
                step_id=step_id,
                command=f"GET {url}",
                exit_code=exit_code,
                stdout=stdout_msg,
                stderr=stderr_msg,
                duration_ms=duration_ms,
                timestamp=current_iso_time(),
                metadata={
                    "url": url,
                    "status_code": response.status_code,
                    "headers": safe_headers,
                },
            )

    except httpx.ConnectError as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExecutionResult(
            workflow_id=workflow_id,
            step="health_check",
            step_id=step_id,
            command=f"GET {url}",
            exit_code=1,
            stdout="",
            stderr=f"Connection refused at {url}: {str(exc)}",
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            metadata={"url": url, "error": "connection_refused"},
        )

    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExecutionResult(
            workflow_id=workflow_id,
            step="health_check",
            step_id=step_id,
            command=f"GET {url}",
            exit_code=1,
            stdout="",
            stderr=f"Health check failed for {url}: {str(exc)}",
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            metadata={"url": url, "error": str(exc)},
        )


from backend.tools.base_tool import BaseTool
from typing import Dict, Tuple


class HttpTool(BaseTool):
    """Tool specialized for HTTP health and status checks."""

    def __init__(self):
        super().__init__(name="http")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        url = command.strip()
        if url.upper().startswith("GET "):
            url = url[4:].strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return False, f"Command must be a valid HTTP URL: {command}"
        return True, None

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 5,
        workflow_id: str = "unknown",
        step_name: str = "health_check",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        url = command.strip()
        if url.upper().startswith("GET "):
            url = url[4:].strip()
        return perform_health_check(
            url=url,
            timeout_seconds=timeout_seconds,
            workflow_id=workflow_id,
            step_id=step_id,
        )
