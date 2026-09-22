import os
import re
import socket
import subprocess
from typing import Optional


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """
    Checks if a local TCP port is currently in use.
    Returns True if occupied, False if free to bind.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE") and os.name == "nt":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            elif hasattr(socket, "SO_REUSEADDR") and os.name != "nt":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return False
    except (OSError, ValueError):
        return True


def find_free_port(start_port: int = 8000, max_attempts: int = 200, host: str = "127.0.0.1") -> int:
    """
    Finds the next available free TCP port starting from start_port + 1.
    Falls back to OS ephemeral port assignment if all attempted ports are busy.
    """
    for port in range(start_port + 1, start_port + 1 + max_attempts):
        if not is_port_in_use(port, host=host):
            return port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def find_process_holding_port(port: int) -> Optional[int]:
    """
    Finds the PID of the process listening on the specified port.
    Returns None if cannot be determined or if port is not in use.
    """
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=5,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    line_clean = line.strip()
                    if not line_clean or not line_clean.startswith("TCP"):
                        continue
                    parts = line_clean.split()
                    if len(parts) >= 5:
                        local_addr = parts[1]
                        state = parts[3]
                        pid_str = parts[4]
                        if (local_addr.endswith(f":{port}") or f":{port}" in local_addr) and state.upper() == "LISTENING":
                            try:
                                pid = int(pid_str)
                                if pid > 0:
                                    return pid
                            except ValueError:
                                pass
        except Exception:
            pass
    else:
        try:
            res = subprocess.run(
                ["lsof", "-iTCP:" + str(port), "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                pid = int(res.stdout.strip().split()[0])
                return pid
        except Exception:
            pass

    return None


def rewrite_port_in_command(command: str, old_port: int, new_port: int) -> str:
    """
    Rewrites port references in command arguments from old_port to new_port using word boundaries.
    Correctly updates flags like --port 8000, -p 8000, PORT=8000, and URLs like http://localhost:8000.
    """
    if not command:
        return command
    return re.sub(rf"\b{old_port}\b", str(new_port), command)
