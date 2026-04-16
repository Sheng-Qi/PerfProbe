from __future__ import annotations

import json
from pathlib import Path


def _fmt(value: object, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _status_line(section: dict) -> str:
    status = section.get("status", "unknown")
    reason = section.get("reason")
    if reason:
        return f"status={status}, reason={reason}"
    return f"status={status}"


def _short_text(value: object, max_len: int = 220) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def write_json_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)


def render_markdown_report(report: dict) -> str:
    lines: list[str] = []
    lines.append("# PerfProbe Report")
    lines.append("")
    lines.append(f"- Generated At (UTC): {report.get('generated_at', '-')}")
    lines.append(f"- Hostname: {report.get('host', {}).get('hostname', '-')}")
    lines.append(f"- Platform: {report.get('host', {}).get('platform', '-')}")
    lines.append("")

    lines.append("## CPU")
    cpu = report.get("cpu", {})
    lines.append(f"- {_status_line(cpu)}")
    info = cpu.get("info", {})
    if info:
        lines.append("")
        lines.append("| Model | Logical Cores | Physical Cores |")
        lines.append("| --- | ---: | ---: |")
        lines.append(
            f"| {info.get('model', '-')} | {info.get('logical_cores', '-')} | {info.get('physical_cores', '-')} |"
        )
    bench = cpu.get("benchmark_fp32", {})
    if bench and bench.get("enabled"):
        lines.append("")
        lines.append("| CPU FP32 MatMul (GFLOPS) | Best | Median | Avg |")
        lines.append("| --- | ---: | ---: | ---: |")
        lines.append(
            f"| n={bench.get('matrix_size', '-')} | {_fmt(bench.get('best'))} | {_fmt(bench.get('median'))} | {_fmt(bench.get('avg'))} |"
        )
    lines.append("")

    lines.append("## RAM")
    memory = report.get("memory", {})
    lines.append(f"- {_status_line(memory)}")
    summary = memory.get("summary", {})
    if summary:
        lines.append("")
        lines.append("| Total MiB | Available MiB |")
        lines.append("| ---: | ---: |")
        lines.append(f"| {_fmt(summary.get('total_mib'))} | {_fmt(summary.get('available_mib'))} |")

    dimms = memory.get("dimms", {})
    lines.append("")
    lines.append(f"- DIMM details: {_status_line(dimms)}")
    slots = dimms.get("slots", [])
    if slots:
        lines.append("")
        lines.append("| Slot | Bank | Capacity | Vendor | Part Number | Serial Number |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for slot in slots:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(slot.get("slot") or "-"),
                        str(slot.get("bank") or "-"),
                        str(slot.get("capacity") or "-"),
                        str(slot.get("vendor") or "-"),
                        str(slot.get("part_number") or "-"),
                        str(slot.get("serial_number") or "-"),
                    ]
                )
                + " |"
            )
    lines.append("")

    lines.append("## GPU")
    gpu = report.get("gpu", {})
    lines.append(f"- {_status_line(gpu)}")
    lines.append(f"- TF32 disabled for benchmark path: {gpu.get('tf32_disabled', False)}")
    lines.append("")
    lines.append("| GPU | Name | Total MiB | Free MiB | Status | Reason |")
    lines.append("| ---: | --- | ---: | ---: | --- | --- |")
    for dev in gpu.get("devices", []):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(dev.get("index", "-")),
                    str(dev.get("name", "-")),
                    _fmt(dev.get("memory_total_mib")),
                    _fmt(dev.get("memory_free_mib")),
                    str(dev.get("status", "-")),
                    str(dev.get("reason") or "-"),
                ]
            )
            + " |"
        )

    lines.append("")
    lines.append("| GPU FP32 / Copy Bandwidth | MatMul Best GFLOPS | MatMul Avg GFLOPS | Copy Best GiB/s | Copy Median GiB/s |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for dev in gpu.get("devices", []):
        matmul = dev.get("fp32_matmul") or {}
        copy = dev.get("memory_copy_bandwidth") or {}
        lines.append(
            f"| gpu:{dev.get('index', '-')} | {_fmt(matmul.get('best'))} | {_fmt(matmul.get('avg'))} | {_fmt(copy.get('best'))} | {_fmt(copy.get('median'))} |"
        )
    lines.append("")

    lines.append("## Disk Mixed Read")
    disk = report.get("disk", {})
    lines.append(f"- {_status_line(disk)}")
    if disk.get("tool"):
        lines.append(f"- Benchmark tool: {disk.get('tool')}")
    definition = disk.get("definition", {})
    if definition:
        lines.append(
            f"- Group definition: {definition.get('group', '-')} (image~{definition.get('image_target_mib', '-')} MiB, mask~{definition.get('mask_target_kib', '-')} KiB)"
        )
    lines.append("")
    lines.append(
        "| Mount | Status | Best groups/s | Median groups/s | Avg groups/s | Best MiB/s | Median MiB/s | Cleanup |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for mount in disk.get("mounts", []):
        bench = mount.get("benchmark") or {}
        groups = bench.get("groups_per_sec") or {}
        mib = bench.get("mib_per_sec") or {}
        cleanup = mount.get("cleanup") or {}
        cleanup_text = cleanup.get("status", "-")
        if cleanup.get("reason"):
            cleanup_text += f" ({_short_text(cleanup.get('reason'))})"
        reason = mount.get("skip_reason")
        status_text = mount.get("status", "-")
        if reason:
            status_text += f" ({_short_text(reason)})"
        lines.append(
            f"| {mount.get('mount_point', '-')} | {status_text} | {_fmt(groups.get('best'))} | {_fmt(groups.get('median'))} | {_fmt(groups.get('avg'))} | {_fmt(mib.get('best'))} | {_fmt(mib.get('median'))} | {cleanup_text} |"
        )

    skipped_mounts = disk.get("skipped_mounts", [])
    if skipped_mounts:
        lines.append("")
        lines.append("### Skipped Mount Points")
        lines.append("")
        lines.append("| Mount | Device | Fstype | Reason |")
        lines.append("| --- | --- | --- | --- |")
        for mount in skipped_mounts:
            lines.append(
                f"| {mount.get('mount_point', '-')} | {mount.get('device', '-')} | {mount.get('fstype', '-')} | {mount.get('reason', '-')} |"
            )

    lines.append("")
    lines.append("### Multi-Disk Ranking (groups/s)")
    lines.append("")
    lines.append("| Rank | Mount | groups/s median | groups/s best | MiB/s median |")
    lines.append("| ---: | --- | ---: | ---: | ---: |")
    for row in disk.get("ranking_by_groups_per_sec", []):
        lines.append(
            f"| {row.get('rank', '-')} | {row.get('mount_point', '-')} | {_fmt(row.get('groups_per_sec_median'))} | {_fmt(row.get('groups_per_sec_best'))} | {_fmt(row.get('mib_per_sec_median'))} |"
        )

    lines.append("")
    lines.append("## Network")
    network = report.get("network", {})
    lines.append(f"- {_status_line(network)}")
    lines.append(f"- Active NIC count: {network.get('active_count', 0)}")
    best_active = network.get("best_active")
    if best_active:
        lines.append(
            f"- Best active NIC: {best_active.get('name')} ({best_active.get('speed_mbps')} Mbps, up={best_active.get('is_up')})"
        )

    lines.append("")
    lines.append("| Interface | Up | Speed Mbps | MTU | Duplex |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    for nic in network.get("interfaces", []):
        lines.append(
            f"| {nic.get('name', '-')} | {nic.get('is_up', False)} | {_fmt(nic.get('speed_mbps'))} | {_fmt(nic.get('mtu'))} | {_fmt(nic.get('duplex'))} |"
        )

    lines.append("")
    lines.append("## Cleanup Summary")
    cleaned = 0
    cleanup_failed = 0
    cleanup_skipped = 0
    for mount in disk.get("mounts", []):
        status = (mount.get("cleanup") or {}).get("status")
        if status == "success":
            cleaned += 1
        elif status == "failed":
            cleanup_failed += 1
        else:
            cleanup_skipped += 1
    lines.append(f"- success: {cleaned}")
    lines.append(f"- failed: {cleanup_failed}")
    lines.append(f"- not-executed/skipped: {cleanup_skipped}")

    return "\n".join(lines) + "\n"


def write_markdown_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown_report(report)
    path.write_text(markdown, encoding="utf-8")
