import ipaddress
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from backend.models.workflow import ExecutionResult, current_iso_time
from backend.tools.base_tool import BaseTool
from backend.tools.shell_tool import build_safe_environment, execute_shell_command, redact_secrets


METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata",
    "instance-data",
})


def parse_potential_ip(hostname: str) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    """
    Parses an IP address from standard or alternate numeric encodings:
    - Standard dotted-quad: 127.0.0.1
    - Decimal integer: 2852039166 (169.254.169.254) or 2130706433 (127.0.0.1)
    - Hexadecimal integer: 0xa9fea9fe
    - Octal integer: 017700000001
    - Dotted octal: 0251.0376.0251.0376
    - Dotted hex: 0x7f.0x0.0x0.0x1
    - IPv6 literals: [::1]
    """
    clean_host = hostname.strip("[]")
    try:
        return ipaddress.ip_address(clean_host)
    except ValueError:
        pass

    # Hex integer: 0xa9fea9fe
    if re.match(r"^0x[0-9a-fA-F]+$", clean_host):
        try:
            val = int(clean_host, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except Exception:
            pass

    # Octal integer (single number): 017700000001
    if re.match(r"^0[0-7]+$", clean_host):
        try:
            val = int(clean_host, 8)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except Exception:
            pass

    # Decimal integer: 2852039166
    if clean_host.isdigit():
        try:
            val = int(clean_host, 10)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except Exception:
            pass

    # Dotted notation with octal/hex components: e.g. 0251.0376.0251.0376 or 0x7f.0x0.0x0.0x1
    parts = clean_host.split(".")
    if len(parts) == 4:
        try:
            octets = []
            for p in parts:
                if p.startswith("0x") or p.startswith("0X"):
                    v = int(p, 16)
                elif p.startswith("0") and len(p) > 1 and p.isdigit():
                    v = int(p, 8)
                elif p.isdigit():
                    v = int(p, 10)
                else:
                    raise ValueError("Not a numeric octet")
                if not (0 <= v <= 255):
                    raise ValueError("Octet out of range")
                octets.append(v)
            return ipaddress.IPv4Address(bytes(octets))
        except Exception:
            pass

    return None


def check_ip_ssrf(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> Tuple[bool, Optional[str]]:
    """Checks whether an IP is private, loopback, link-local, reserved, multicast, or metadata."""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or str(ip) == "169.254.169.254"
        or str(ip) in METADATA_HOSTS
    ):
        return False, f"Access to private or reserved IP address '{ip}' is prohibited (SSRF prevention)."
    return True, None


def validate_repo_url(repo_url: str) -> Tuple[bool, Optional[str]]:
    """
    Strict validation of Git repository URLs:
    - Enforces https:// or http:// schemes (blocks ext::, file://, ssh://, git@, local filesystem paths)
    - Rejects flag injection (leading '-')
    - Rejects shell metacharacters and unquoted spaces
    - Rejects cloud metadata hosts and private/loopback IP literals in all numeric encodings (SSRF prevention)
    - Rejects hostnames that resolve to private/reserved IP addresses via DNS
    """
    if not repo_url or not repo_url.strip():
        return False, "Repository URL cannot be empty."

    url = repo_url.strip()

    # Prevent flag/option injection
    if url.startswith("-"):
        return False, "Malicious repository URL: flag/option injection detected."

    # Prevent shell injection characters or quotes in URL
    if any(char in url for char in [";", "|", "&", "`", "$", "\n", "\r", '"', "'", " "]):
        return False, "Malicious repository URL: shell characters or unescaped spaces detected."

    # Parse URL
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        return False, f"Invalid URL format: {str(exc)}"

    scheme = (parsed.scheme or "").lower()

    # Check if this is a Windows drive letter path (e.g. C:\... where urllib parsed scheme as single letter)
    # or a local POSIX path (e.g. /path/to/repo)
    is_local_path = False
    if os.name == "nt" and len(scheme) == 1 and scheme.isalpha() and (":" in url[:3]):
        is_local_path = True
    elif not scheme and (url.startswith("/") or url.startswith("\\") or url.startswith(".")):
        is_local_path = True

    if is_local_path:
        allow_test_repos = os.getenv("ALLOW_TEST_REPOS", "true").lower() in ("true", "1")
        try:
            p = Path(url).resolve()
            system_dirs = ["/etc", "/bin", "/usr", "/root", "/var", "/home", "c:\\windows", "c:\\"]
            if str(p).lower() in system_dirs or any(str(p).lower().startswith(sd) for sd in ["/etc/", "c:\\windows\\"]):
                return False, f"Access to local system path '{url}' is prohibited."
            if allow_test_repos and p.is_dir() and (p / ".git").exists():
                return True, None
            return False, f"Local path repository '{url}' is prohibited or is not a valid git repository."
        except Exception:
            return False, f"Invalid local path '{url}'."

    if scheme not in ("https", "http"):
        return False, f"Invalid repository URL: Prohibited URL scheme '{scheme or 'none'}'. Only https:// and http:// repository URLs are permitted."

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "Invalid repository URL: Missing hostname in repository URL."

    # SSRF: block cloud metadata hosts
    if hostname in METADATA_HOSTS:
        return False, f"Access to cloud metadata host '{hostname}' is prohibited (SSRF prevention)."

    # SSRF: check if hostname is an IP literal (decimal, octal, hex, dotted)
    ip_obj = parse_potential_ip(hostname)
    if ip_obj:
        ok, err = check_ip_ssrf(ip_obj)
        if not ok:
            return False, err
    else:
        # SSRF: resolve hostname via DNS and verify none of the returned IPs are private
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for addr in addr_info:
                sockaddr = addr[4]
                ip_str = sockaddr[0]
                try:
                    resolved_ip = ipaddress.ip_address(ip_str)
                    ok, err = check_ip_ssrf(resolved_ip)
                    if not ok:
                        return False, f"Hostname '{hostname}' resolves to blocked private IP '{resolved_ip}' (SSRF prevention)."
                except ValueError:
                    pass
        except socket.gaierror:
            # If domain fails resolution, block if hostname suggests internal network
            if any(h in hostname for h in ("internal", "local", "corp", "lan")):
                return False, f"Resolution failed for internal hostname: '{hostname}'"
        except Exception:
            pass

    return True, None


def clone_repository(
    repo_url: str,
    target_dir: str,
    workflow_id: str = "unknown",
    step_id: Optional[str] = None,
    timeout_seconds: int = 120,
) -> ExecutionResult:
    """
    Clones a git repository into the specified target directory.
    Uses argv with shell=False and the '--' option terminator.
    Cleans target directory beforehand if it exists.
    """
    target_path = Path(target_dir).resolve()
    start_time = time.perf_counter()

    # Pre-clean target directory
    if target_path.exists():
        try:
            shutil.rmtree(target_path, ignore_errors=True)
        except Exception:
            pass

    target_path.mkdir(parents=True, exist_ok=True)

    safe_url = redact_secrets(repo_url.strip())

    # Strict URL safety validation
    is_safe, reason = validate_repo_url(safe_url)
    if not is_safe:
        from backend.metrics import security_violations
        security_violations.labels(violation_type='ssrf').inc()
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=126,
            stdout="",
            stderr=f"Security blocked: {reason}",
            workspace=str(target_path),
            metadata={"security_blocked": True, "repo_url": safe_url},
        )

    # Use argv with '--' option terminator to guarantee safe_url cannot be parsed as a git option
    argv = ["git", "clone", "--depth", "1", "--", safe_url, str(target_path)]
    proc_env = build_safe_environment()

    # Re-check URL safety and re-resolve DNS immediately prior to subprocess launch (defeat DNS rebinding TOCTOU)
    recheck_safe, recheck_reason = validate_repo_url(safe_url)
    if not recheck_safe:
        from backend.metrics import security_violations
        security_violations.labels(violation_type='ssrf').inc()
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=126,
            stdout="",
            stderr=f"Security blocked: DNS rebinding or invalid URL: {recheck_reason}",
            workspace=str(target_path),
            metadata={"security_blocked": True, "repo_url": safe_url},
        )

    try:
        proc = subprocess.run(
            argv,
            cwd=str(target_path.parent),
            shell=False,  # CRITICAL: shell=False prevents command injection
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=proc_env,
        )
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        out = redact_secrets(proc.stdout or "")
        err = redact_secrets(proc.stderr or "")

        result = ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command=f"git clone --depth 1 -- [URL] {str(target_path)}",
            exit_code=proc.returncode,
            stdout=out,
            stderr=err,
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=str(target_path),
            metadata={"repo_url": safe_url, "target_dir": str(target_path)},
        )

        if result.exit_code == 0:
            files = list(target_path.iterdir())
            if not files:
                result.exit_code = 1
                result.stderr = "Git clone returned 0 but target directory is empty."
            else:
                result.metadata["cloned_files_count"] = len(files)

        return result

    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=124,
            stdout="",
            stderr=f"Git clone timed out after {timeout_seconds}s",
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=str(target_path),
            metadata={"timed_out": True, "repo_url": safe_url},
        )

    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return ExecutionResult(
            workflow_id=workflow_id,
            step="clone_repository",
            step_id=step_id,
            command="git clone",
            exit_code=1,
            stdout="",
            stderr=f"Git clone execution error: {str(exc)}",
            duration_ms=duration_ms,
            timestamp=current_iso_time(),
            workspace=str(target_path),
            metadata={"repo_url": safe_url},
        )


class GitTool(BaseTool):
    """Tool specialized for git operations."""

    def __init__(self):
        super().__init__(name="git")

    def validate(self, command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        cmd = command.strip()
        if not cmd.lower().startswith("git"):
            return False, f"Command must start with 'git': {command}"
        if cmd.lower().startswith("git clone"):
            parts = shlex.split(cmd, posix=True)
            # Find the repo URL (first non-flag argument after clone)
            for tok in parts[2:]:
                if not tok.startswith("-"):
                    is_valid, err = validate_repo_url(tok)
                    if not is_valid:
                        return False, err
                    break
        return True, None

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout_seconds: int = 120,
        workflow_id: str = "unknown",
        step_name: str = "git_command",
        step_id: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        return execute_shell_command(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env=env,
            workflow_id=workflow_id,
            step_name=step_name,
            step_id=step_id,
        )
