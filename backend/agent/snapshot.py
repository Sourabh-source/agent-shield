from pathlib import Path
from typing import Any, Dict, List, Optional
import os


def capture_workspace_snapshot(workspace_dir: Optional[str], max_files: int = 200) -> Dict[str, Any]:
    """
    Captures a lightweight snapshot of files in the workspace directory.
    Bounded to max_files to avoid memory and performance overhead.
    """
    if not workspace_dir:
        return {"file_count": 0, "total_size_bytes": 0, "files": {}}

    ws = Path(workspace_dir)
    if not ws.exists() or not ws.is_dir():
        return {"file_count": 0, "total_size_bytes": 0, "files": {}}

    files: Dict[str, Dict[str, Any]] = {}
    total_size = 0
    file_count = 0

    try:
        for entry in ws.rglob("*"):
            # Skip .git directory to keep snapshots clean and fast
            if ".git" in entry.parts:
                continue
            if entry.is_file():
                file_count += 1
                try:
                    stat = entry.stat()
                    total_size += stat.st_size
                    if len(files) < max_files:
                        rel_path = str(entry.relative_to(ws)).replace("\\", "/")
                        files[rel_path] = {
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                        }
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError):
        pass

    return {
        "file_count": file_count,
        "total_size_bytes": total_size,
        "files": files,
    }


def diff_snapshots(
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Computes added, removed, and modified files between two workspace snapshots.
    """
    before_files = before.get("files", {})
    after_files = after.get("files", {})

    added = [f for f in after_files if f not in before_files]
    removed = [f for f in before_files if f not in after_files]
    modified = [
        f for f in after_files
        if f in before_files
        and (
            after_files[f]["size"] != before_files[f]["size"]
            or after_files[f]["mtime"] != before_files[f]["mtime"]
        )
    ]

    return {
        "added": sorted(added),
        "removed": sorted(removed),
        "modified": sorted(modified),
        "files_added_count": len(added),
        "files_removed_count": len(removed),
        "files_modified_count": len(modified),
        "net_file_change": after.get("file_count", 0) - before.get("file_count", 0),
        "net_size_change_bytes": after.get("total_size_bytes", 0) - before.get("total_size_bytes", 0),
    }
