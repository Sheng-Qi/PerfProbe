from __future__ import annotations

import shutil
from pathlib import Path

SAFE_SAMPLE_PREFIX = "perfprobe-sample-"
SAFE_MARKER_FILE = ".perfprobe_marker"
SAFE_MARKER_CONTENT = "managed-by-perfprobe"


FORBIDDEN_EXACT_PATHS = {
    Path("/"),
    Path("/boot"),
    Path("/boot/efi"),
    Path("/etc"),
    Path("/usr"),
    Path("/var"),
    Path("/root"),
    Path("/home"),
}


def validate_sample_dir(path: Path) -> tuple[bool, str | None]:
    """Strictly validate a directory before deleting sample data."""
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return False, "sample directory does not exist"
    except OSError as exc:
        return False, f"unable to resolve sample directory: {exc}"

    if resolved in FORBIDDEN_EXACT_PATHS:
        return False, "refuse to operate on protected system path"

    if not resolved.is_dir():
        return False, "sample target is not a directory"

    if not resolved.name.startswith(SAFE_SAMPLE_PREFIX):
        return False, "sample directory name does not match safe prefix"

    if len(resolved.parts) < 3:
        return False, "sample directory path is unexpectedly shallow"

    marker = resolved / SAFE_MARKER_FILE
    if not marker.exists():
        return False, "sample marker file not found"

    try:
        marker_text = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return False, f"unable to read sample marker: {exc}"

    if marker_text != SAFE_MARKER_CONTENT:
        return False, "sample marker content mismatch"

    return True, None


def safe_cleanup(path: Path) -> dict[str, str | None]:
    ok, reason = validate_sample_dir(path)
    if not ok:
        return {"status": "failed", "reason": reason}

    try:
        shutil.rmtree(path)
        return {"status": "success", "reason": None}
    except OSError as exc:
        return {"status": "failed", "reason": f"cleanup failed: {exc}"}
