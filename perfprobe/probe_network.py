from __future__ import annotations

import psutil


def probe_network() -> dict:
    result: dict = {
        "status": "ok",
        "reason": None,
        "interfaces": [],
        "active_count": 0,
        "best_active": None,
    }

    stats = psutil.net_if_stats()
    if not stats:
        result["status"] = "skipped"
        result["reason"] = "no network interfaces discovered"
        return result

    active: list[dict] = []
    for name, stat in stats.items():
        speed_mbps = stat.speed if stat.speed is not None else 0
        item = {
            "name": name,
            "is_up": bool(stat.isup),
            "speed_mbps": speed_mbps,
            "mtu": stat.mtu,
            "duplex": int(stat.duplex),
        }
        result["interfaces"].append(item)
        if item["is_up"] and speed_mbps and speed_mbps > 0:
            active.append(item)

    result["active_count"] = len(active)
    if active:
        result["best_active"] = max(active, key=lambda x: x["speed_mbps"])

    return result
