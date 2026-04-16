from __future__ import annotations

import os
import random
import time
from pathlib import Path

import psutil

from .cleanup_guard import SAFE_MARKER_CONTENT, SAFE_MARKER_FILE, SAFE_SAMPLE_PREFIX, safe_cleanup
from .stats import summarize_throughput
from .system_utils import bytes_from_kib, bytes_from_mib

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


def _read_all(path: Path) -> int:
    read_bytes = 0
    with path.open("rb") as handle:
        while True:
            data = handle.read(1024 * 1024)
            if not data:
                break
            read_bytes += len(data)
    return read_bytes


def _run_mixed_read_benchmark(groups: list[tuple[Path, Path, int]], rounds: int) -> dict:
    randomizer = random.Random(20260416)

    rounds_rows: list[dict] = []
    groups_series: list[float] = []
    mib_series: list[float] = []

    for round_index in range(rounds):
        ordered = groups.copy()
        randomizer.shuffle(ordered)

        started = time.perf_counter()
        total_bytes = 0
        for image_path, mask_path, _total_expected in ordered:
            total_bytes += _read_all(image_path)
            total_bytes += _read_all(mask_path)
        elapsed = time.perf_counter() - started

        groups_per_sec = len(ordered) / elapsed
        mib_per_sec = (total_bytes / (1024.0 * 1024.0)) / elapsed

        groups_series.append(groups_per_sec)
        mib_series.append(mib_per_sec)
        rounds_rows.append(
            {
                "round": round_index + 1,
                "elapsed_sec": elapsed,
                "groups_per_sec": groups_per_sec,
                "mib_per_sec": mib_per_sec,
                "group_count": len(ordered),
            }
        )

    groups_summary = summarize_throughput(groups_series)
    mib_summary = summarize_throughput(mib_series)

    return {
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
        "definition": {
            "group": "1 image + 1 mask",
            "image_target_mib": image_size_mib,
            "mask_target_kib": mask_size_kib,
        },
        "mounts": [],
        "skipped_mounts": [],
        "ranking_by_groups_per_sec": [],
    }

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
