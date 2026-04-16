from __future__ import annotations

import platform
import socket
from datetime import datetime, timezone


def skipped_section(reason: str) -> dict:
    return {"status": "skipped", "reason": reason}


def make_base_report(config: dict, execution: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "machine": platform.machine(),
        },
        "execution": execution,
        "config": config,
        "cpu": skipped_section("not run"),
        "memory": skipped_section("not run"),
        "gpu": skipped_section("not run"),
        "disk": skipped_section("not run"),
        "network": skipped_section("not run"),
    }
