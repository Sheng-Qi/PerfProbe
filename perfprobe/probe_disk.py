from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
import time
from pathlib import Path

import psutil

from .cleanup_guard import SAFE_MARKER_CONTENT, SAFE_MARKER_FILE, SAFE_SAMPLE_PREFIX, safe_cleanup
from .stats import summarize_throughput
from .system_utils import bytes_from_kib, bytes_from_mib, run_command

PSEUDO_FS_TYPES = {
    "autofs",
    "binfmt_misc",
    "cgroup",
    "cgroup2",
    "configfs",
    "debugfs",
    "devpts",
    "devtmpfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "overlay",
    "proc",
    "pstore",
    "securityfs",
    "selinuxfs",
    "squashfs",
    "sysfs",
    "tmpfs",
    "tracefs",
}

BOOT_MOUNTS = {"/boot", "/boot/efi"}
FIO_IOENGINE = "psync"
FIO_BLOCK_SIZE = "4k"
FIO_TIMEOUT_SEC = 1800


def _write_fixed_size(path: Path, size_bytes: int) -> None:
    chunk = b"0" * min(size_bytes, 1024 * 1024)
    with path.open("wb") as handle:
        remaining = size_bytes
        while remaining > 0:
            piece = chunk[: min(len(chunk), remaining)]
            handle.write(piece)
            remaining -= len(piece)


def _prepare_sample_pool(
    base_mount: Path,
    run_id: str,
    group_count: int,
    image_size_mib: int,
    mask_size_kib: int,
) -> tuple[Path, list[tuple[Path, Path, int]]]:
    sample_dir = base_mount / f"{SAFE_SAMPLE_PREFIX}{run_id}"
    sample_dir.mkdir(parents=True, exist_ok=False)

    marker_file = sample_dir / SAFE_MARKER_FILE
    marker_file.write_text(SAFE_MARKER_CONTENT, encoding="utf-8")

    image_size = bytes_from_mib(image_size_mib)
    mask_size = bytes_from_kib(mask_size_kib)

    groups: list[tuple[Path, Path, int]] = []
    for idx in range(group_count):
        image_path = sample_dir / f"image_{idx:05d}.bin"
        mask_path = sample_dir / f"mask_{idx:05d}.bin"
        _write_fixed_size(image_path, image_size)
        _write_fixed_size(mask_path, mask_size)
        groups.append((image_path, mask_path, image_size + mask_size))

    return sample_dir, groups


def _write_fio_jobfile(file_paths: list[Path], jobfile_path: Path) -> None:
    lines = [
        "[global]",
        "rw=read",
        f"ioengine={FIO_IOENGINE}",
        "direct=1",
        "thread=1",
        "group_reporting=1",
        f"bs={FIO_BLOCK_SIZE}",
        "",  # spacing for readability
    ]

    for index, file_path in enumerate(file_paths):
        lines.append(f"[job_{index:05d}]")
        lines.append(f"filename={file_path}")
        lines.append("")

    jobfile_path.write_text("\n".join(lines), encoding="utf-8")


def _build_fio_command_with_jobfile(jobfile_path: Path) -> list[str]:
    return [
        "fio",
        "--max-jobs=1",
        "--output-format=json",
        str(jobfile_path),
    ]


def _extract_fio_round_metrics(fio_json: dict, group_size_bytes: int) -> dict:
    jobs = fio_json.get("jobs", [])
    if not jobs:
        raise ValueError("fio output missing jobs array")

    read_stats = jobs[0].get("read")
    if not isinstance(read_stats, dict):
        raise ValueError("fio output missing read stats")

    io_bytes = int(read_stats.get("io_bytes", 0))
    bw_bytes = float(read_stats.get("bw_bytes", 0.0))
    runtime_ms = float(read_stats.get("runtime", 0.0))
    iops = float(read_stats.get("iops", 0.0))

    if bw_bytes <= 0.0 and io_bytes > 0 and runtime_ms > 0:
        bw_bytes = io_bytes / (runtime_ms / 1000.0)

    if bw_bytes <= 0.0:
        raise ValueError("fio reported non-positive bandwidth")

    groups_per_sec = bw_bytes / float(group_size_bytes)
    mib_per_sec = bw_bytes / (1024.0 * 1024.0)
    return {
        "io_bytes": io_bytes,
        "bw_bytes_per_sec": bw_bytes,
        "runtime_ms": runtime_ms,
        "iops": iops,
        "groups_per_sec": groups_per_sec,
        "mib_per_sec": mib_per_sec,
    }


def _run_fio_once(file_paths: list[Path]) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".fio",
        prefix="perfprobe-round-",
        delete=False,
        encoding="utf-8",
    ) as handle:
        jobfile_path = Path(handle.name)

    try:
        _write_fio_jobfile(file_paths=file_paths, jobfile_path=jobfile_path)
        command = _build_fio_command_with_jobfile(jobfile_path)
        command_result = run_command(command, timeout_sec=FIO_TIMEOUT_SEC)
    finally:
        try:
            jobfile_path.unlink(missing_ok=True)
        except OSError:
            pass

    if command_result.code != 0:
        message = command_result.stderr or command_result.stdout or "fio execution failed"
        raise RuntimeError(message)

    try:
        return json.loads(command_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"fio returned invalid JSON: {exc}") from exc


def _run_mixed_read_benchmark(groups: list[tuple[Path, Path, int]], rounds: int) -> dict:
    if not groups:
        raise ValueError("no sample groups prepared for disk benchmark")

    randomizer = random.Random(20260416)
    group_size_bytes = groups[0][2]

    rounds_rows: list[dict] = []
    groups_series: list[float] = []
    mib_series: list[float] = []

    for round_index in range(rounds):
        ordered = groups.copy()
        randomizer.shuffle(ordered)

        round_paths: list[Path] = []
        expected_total_bytes = 0
        for image_path, mask_path, total_expected in ordered:
            round_paths.extend([image_path, mask_path])
            expected_total_bytes += total_expected

        fio_payload = _run_fio_once(round_paths)
        metrics = _extract_fio_round_metrics(fio_json=fio_payload, group_size_bytes=group_size_bytes)
        if metrics["io_bytes"] < expected_total_bytes:
            raise RuntimeError(
                "fio read bytes is lower than expected sample bytes "
                f"({metrics['io_bytes']} < {expected_total_bytes})"
            )

        groups_series.append(metrics["groups_per_sec"])
        mib_series.append(metrics["mib_per_sec"])
        rounds_rows.append(
            {
                "round": round_index + 1,
                "groups_per_sec": metrics["groups_per_sec"],
                "mib_per_sec": metrics["mib_per_sec"],
                "group_count": len(ordered),
                "fio_io_bytes": metrics["io_bytes"],
                "fio_bw_bytes_per_sec": metrics["bw_bytes_per_sec"],
                "fio_runtime_ms": metrics["runtime_ms"],
                "fio_iops": metrics["iops"],
                "expected_total_bytes": expected_total_bytes,
            }
        )

    groups_summary = summarize_throughput(groups_series)
    mib_summary = summarize_throughput(mib_series)

    return {
        "tool": "fio",
        "fio_config": {
            "ioengine": FIO_IOENGINE,
            "direct": True,
            "bs": FIO_BLOCK_SIZE,
            "max_jobs": 1,
            "input_mode": "jobfile",
        },
        "rounds": rounds_rows,
        "groups_per_sec": {
            "best": groups_summary["best"],
            "median": groups_summary["median"],
            "avg": groups_summary["avg"],
        },
        "mib_per_sec": {
            "best": mib_summary["best"],
            "median": mib_summary["median"],
            "avg": mib_summary["avg"],
        },
    }


def _candidate_mounts(multi_disk: bool, include_root_mount: bool) -> tuple[list[dict], list[dict]]:
    candidates: list[dict] = []
    skipped: list[dict] = []

    if not multi_disk:
        baseline = Path.cwd()
        candidates.append({"mount_point": str(baseline), "device": "cwd", "fstype": "path", "is_baseline": True})
        return candidates, skipped

    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if mount in seen:
            continue
        seen.add(mount)

        if mount in BOOT_MOUNTS:
            skipped.append(
                {
                    "mount_point": mount,
                    "device": part.device,
                    "fstype": part.fstype,
                    "reason": "boot mount is skipped by default",
                }
            )
            continue

        if mount == "/" and not include_root_mount:
            skipped.append(
                {
                    "mount_point": mount,
                    "device": part.device,
                    "fstype": part.fstype,
                    "reason": "root mount participation disabled",
                }
            )
            continue

        if part.fstype in PSEUDO_FS_TYPES:
            skipped.append(
                {
                    "mount_point": mount,
                    "device": part.device,
                    "fstype": part.fstype,
                    "reason": "pseudo filesystem skipped",
                }
            )
            continue

        candidates.append({"mount_point": mount, "device": part.device, "fstype": part.fstype})

    return candidates, skipped


def _ensure_baseline(candidates: list[dict]) -> list[dict]:
    """Always append a local baseline candidate for comparability fallback."""
    fallback = Path.cwd().resolve()

    has_fallback = False
    for candidate in candidates:
        try:
            candidate_path = Path(candidate["mount_point"]).resolve()
        except OSError:
            continue
        if candidate_path == fallback:
            has_fallback = True
            break

    if not has_fallback:
        candidates.append(
            {
                "mount_point": str(fallback),
                "device": "cwd",
                "fstype": "path",
                "is_baseline": True,
            }
        )

    return candidates


def probe_disk(
    rounds: int,
    group_count: int,
    image_size_mib: int,
    mask_size_kib: int,
    multi_disk: bool,
    include_root_mount: bool,
    auto_cleanup: bool,
) -> dict:
    result: dict = {
        "status": "ok",
        "reason": None,
        "tool": "fio",
        "definition": {
            "group": "1 image + 1 mask",
            "image_target_mib": image_size_mib,
            "mask_target_kib": mask_size_kib,
        },
        "mounts": [],
        "skipped_mounts": [],
        "ranking_by_groups_per_sec": [],
    }

    if shutil.which("fio") is None:
        result["status"] = "failed"
        result["reason"] = "fio not found in PATH"
        return result

    candidates, skipped = _candidate_mounts(multi_disk=multi_disk, include_root_mount=include_root_mount)
    candidates = _ensure_baseline(candidates)
    result["skipped_mounts"] = skipped

    run_id = str(int(time.time()))

    for index, candidate in enumerate(candidates):
        mount_point = candidate["mount_point"]
        mount_path = Path(mount_point)

        row: dict = {
            "mount_point": mount_point,
            "device": candidate.get("device"),
            "fstype": candidate.get("fstype"),
            "status": "ok",
            "skip_reason": None,
            "sample_pool": None,
            "benchmark": None,
            "cleanup": {
                "status": "skipped",
                "reason": "sample pool was not created",
            },
        }

        if not mount_path.exists():
            row["status"] = "skipped"
            row["skip_reason"] = "mount path does not exist"
            result["mounts"].append(row)
            continue

        if not os.access(mount_path, os.R_OK | os.W_OK | os.X_OK):
            row["status"] = "skipped"
            row["skip_reason"] = "insufficient permission for read/write/execute"
            result["mounts"].append(row)
            continue

        sample_dir: Path | None = None
        try:
            sample_dir, groups = _prepare_sample_pool(
                base_mount=mount_path,
                run_id=f"{run_id}-{index}",
                group_count=group_count,
                image_size_mib=image_size_mib,
                mask_size_kib=mask_size_kib,
            )
            row["sample_pool"] = {
                "path": str(sample_dir),
                "group_count": group_count,
            }

            row["benchmark"] = _run_mixed_read_benchmark(groups=groups, rounds=rounds)
        except PermissionError:
            row["status"] = "skipped"
            row["skip_reason"] = "permission denied while preparing sample pool"
        except Exception as exc:  # pragma: no cover - filesystem dependent
            row["status"] = "failed"
            row["skip_reason"] = str(exc)
        finally:
            if sample_dir is not None:
                if auto_cleanup:
                    row["cleanup"] = safe_cleanup(sample_dir)
                else:
                    row["cleanup"] = {
                        "status": "not-executed",
                        "reason": "auto cleanup disabled",
                    }

        result["mounts"].append(row)

    successful_mounts = [
        mount
        for mount in result["mounts"]
        if mount["status"] == "ok" and mount.get("benchmark") and mount["benchmark"]["groups_per_sec"]["median"] is not None
    ]

    ranking: list[dict] = []
    for mount in successful_mounts:
        ranking.append(
            {
                "mount_point": mount["mount_point"],
                "groups_per_sec_median": mount["benchmark"]["groups_per_sec"]["median"],
                "groups_per_sec_best": mount["benchmark"]["groups_per_sec"]["best"],
                "mib_per_sec_median": mount["benchmark"]["mib_per_sec"]["median"],
            }
        )

    ranking.sort(key=lambda row: row["groups_per_sec_median"], reverse=True)
    for idx, row in enumerate(ranking, start=1):
        row["rank"] = idx
    result["ranking_by_groups_per_sec"] = ranking

    if not successful_mounts:
        result["status"] = "failed"
        result["reason"] = "no mount point completed disk benchmark"

    return result
