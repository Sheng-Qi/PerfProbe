from __future__ import annotations

import getpass
import os
import platform
import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str


def run_command(command: list[str], timeout_sec: int = 20) -> CommandResult:
    """Run a command and return non-throwing output."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        return CommandResult(
            code=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
    except FileNotFoundError:
        return CommandResult(code=127, stdout="", stderr=f"command not found: {command[0]}")
    except subprocess.TimeoutExpired:
        return CommandResult(code=124, stdout="", stderr="command timeout")


def mib_from_bytes(value: int | float) -> float:
    return float(value) / (1024.0 * 1024.0)


def bytes_from_mib(value: int | float) -> int:
    return int(float(value) * 1024.0 * 1024.0)


def bytes_from_kib(value: int | float) -> int:
    return int(float(value) * 1024.0)


def read_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def capture_identity() -> dict[str, str | int | None]:
    return {
        "effective_user": getpass.getuser(),
        "effective_uid": os.geteuid(),
        "effective_gid": os.getegid(),
        "sudo_user": os.environ.get("SUDO_USER"),
        "sudo_uid": os.environ.get("SUDO_UID"),
        "sudo_gid": os.environ.get("SUDO_GID"),
    }
