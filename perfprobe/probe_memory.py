from __future__ import annotations

import re

import psutil

from .system_utils import mib_from_bytes, run_command


def _parse_dmidecode_memory(stdout: str) -> list[dict[str, str | None]]:
    dimms: list[dict[str, str | None]] = []
    blocks = stdout.split("\n\n")
    for block in blocks:
        if "Memory Device" not in block:
            continue

        lines = [line.strip() for line in block.splitlines() if line.strip()]
        size = _field(lines, "Size")
        if size in {None, "No Module Installed", "Not Installed"}:
            continue

        dimms.append(
            {
                "capacity": size,
                "vendor": _field(lines, "Manufacturer"),
                "part_number": _field(lines, "Part Number"),
                "slot": _field(lines, "Locator"),
                "bank": _field(lines, "Bank Locator"),
                "serial_number": _field(lines, "Serial Number"),
            }
        )
    return dimms


def _field(lines: list[str], name: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(name)}:\s*(.*)$")
    for line in lines:
        match = pattern.match(line)
        if match:
            value = match.group(1).strip()
            return value or None
    return None


def probe_memory() -> dict:
    vmem = psutil.virtual_memory()
    result: dict = {
        "status": "ok",
        "reason": None,
        "summary": {
            "total_mib": mib_from_bytes(vmem.total),
            "available_mib": mib_from_bytes(vmem.available),
        },
        "dimms": {
            "status": "ok",
            "reason": None,
            "slots": [],
        },
    }

    command_result = run_command(["dmidecode", "-t", "memory"], timeout_sec=30)
    if command_result.code != 0:
        result["dimms"]["status"] = "skipped"
        stderr = command_result.stderr or command_result.stdout
        if command_result.code == 127:
            reason = "dmidecode not found"
        else:
            reason = stderr or "dmidecode execution failed"
        result["dimms"]["reason"] = reason
        return result

    slots = _parse_dmidecode_memory(command_result.stdout)
    result["dimms"]["slots"] = slots
    if not slots:
        result["dimms"]["status"] = "skipped"
        result["dimms"]["reason"] = "no installed memory module details discovered"

    return result
