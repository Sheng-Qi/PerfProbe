from __future__ import annotations

import argparse
import os
import pwd
from pathlib import Path
from typing import Sequence

from .probe_cpu import probe_cpu
from .probe_disk import probe_disk
from .probe_gpu import probe_gpu
from .probe_memory import probe_memory
from .probe_network import probe_network
from .report_schema import make_base_report, skipped_section
from .report_writer import write_json_report, write_markdown_report
from .system_utils import capture_identity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PerfProbe deep-learning hardware benchmark")

    parser.add_argument("--run-only", action="store_true", help="run benchmark only without any install step")

    parser.add_argument("--no-cpu", action="store_true", help="skip CPU benchmark")
    parser.add_argument("--no-memory", action="store_true", help="skip memory probe")
    parser.add_argument("--no-gpu", action="store_true", help="skip GPU benchmark")
    parser.add_argument("--no-disk", action="store_true", help="skip disk benchmark")
    parser.add_argument("--no-network", action="store_true", help="skip network probe")

    parser.add_argument("--cpu-rounds", type=int, default=5, help="CPU benchmark rounds")
    parser.add_argument("--cpu-matrix-size", type=int, default=2048, help="CPU FP32 matrix size")

    parser.add_argument("--gpu-rounds", type=int, default=5, help="GPU benchmark rounds")
    parser.add_argument("--gpu-matrix-size", type=int, default=4096, help="GPU FP32 matrix size")
    parser.add_argument("--gpu-copy-size-mib", type=int, default=512, help="GPU copy benchmark size in MiB")

    parser.add_argument("--disk-rounds", type=int, default=5, help="disk benchmark rounds")
    parser.add_argument("--disk-groups", type=int, default=64, help="image+mask group count")
    parser.add_argument("--disk-image-size-mib", type=int, default=5, help="image sample size in MiB")
    parser.add_argument("--disk-mask-size-kib", type=int, default=200, help="mask sample size in KiB")

    parser.add_argument("--single-disk", action="store_true", help="disable multi-disk detection")
    parser.add_argument(
        "--include-root-mount",
        action="store_true",
        help="include / mount point in multi-disk benchmark",
    )
    parser.add_argument("--no-auto-cleanup", action="store_true", help="keep disk sample pool after run")

    parser.add_argument(
        "--execution-identity",
        choices=["effective", "root", "caller"],
        default="effective",
        help="control execution identity in sudo scenario",
    )

    parser.add_argument("--output-dir", default="reports", help="output directory")
    parser.add_argument("--report-prefix", default="perfprobe_report", help="report file prefix")
    return parser


def _switch_to_sudo_caller() -> tuple[bool, str | None]:
    if os.geteuid() != 0:
        return False, "effective user is not root; cannot switch to sudo caller"

    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if not sudo_uid or not sudo_gid:
        return False, "SUDO_UID or SUDO_GID is missing"

    uid = int(sudo_uid)
    gid = int(sudo_gid)

    try:
        user_entry = pwd.getpwuid(uid)
        os.setgid(gid)
        os.setuid(uid)
        os.environ["HOME"] = user_entry.pw_dir
        os.environ["USER"] = user_entry.pw_name
        os.environ["LOGNAME"] = user_entry.pw_name
    except Exception as exc:
        return False, f"failed to switch to sudo caller: {exc}"

    return True, None


def resolve_execution_identity(requested: str) -> dict:
    before = capture_identity()
    control = {
        "requested_identity": requested,
        "switch_status": "not-requested",
        "switch_reason": None,
        "before": before,
    }

    if requested == "effective":
        control["switch_status"] = "not-required"
    elif requested == "root":
        if os.geteuid() == 0:
            control["switch_status"] = "ok"
        else:
            control["switch_status"] = "skipped"
            control["switch_reason"] = "root identity requested but process is not root"
    elif requested == "caller":
        ok, reason = _switch_to_sudo_caller()
        control["switch_status"] = "ok" if ok else "failed"
        control["switch_reason"] = reason

    control["after"] = capture_identity()
    return control


def _config_from_args(args: argparse.Namespace) -> dict:
    return {
        "run_only": bool(args.run_only),
        "benchmarks": {
            "cpu": not args.no_cpu,
            "memory": not args.no_memory,
            "gpu": not args.no_gpu,
            "disk": not args.no_disk,
            "network": not args.no_network,
        },
        "cpu_rounds": args.cpu_rounds,
        "cpu_matrix_size": args.cpu_matrix_size,
        "gpu_rounds": args.gpu_rounds,
        "gpu_matrix_size": args.gpu_matrix_size,
        "gpu_copy_size_mib": args.gpu_copy_size_mib,
        "disk_rounds": args.disk_rounds,
        "disk_groups": args.disk_groups,
        "disk_image_size_mib": args.disk_image_size_mib,
        "disk_mask_size_kib": args.disk_mask_size_kib,
        "multi_disk": not args.single_disk,
        "include_root_mount": args.include_root_mount,
        "auto_cleanup": not args.no_auto_cleanup,
        "execution_identity": args.execution_identity,
        "output_dir": args.output_dir,
        "report_prefix": args.report_prefix,
    }


def run(args: argparse.Namespace) -> tuple[dict, Path, Path]:
    execution = resolve_execution_identity(args.execution_identity)
    config = _config_from_args(args)

    report = make_base_report(config=config, execution=execution)

    if args.no_cpu:
        report["cpu"] = skipped_section("disabled by --no-cpu")
    else:
        report["cpu"] = probe_cpu(rounds=args.cpu_rounds, matrix_size=args.cpu_matrix_size)

    if args.no_memory:
        report["memory"] = skipped_section("disabled by --no-memory")
    else:
        report["memory"] = probe_memory()

    if args.no_gpu:
        report["gpu"] = skipped_section("disabled by --no-gpu")
    else:
        report["gpu"] = probe_gpu(
            rounds=args.gpu_rounds,
            matrix_size=args.gpu_matrix_size,
            copy_size_mib=args.gpu_copy_size_mib,
        )

    if args.no_disk:
        report["disk"] = skipped_section("disabled by --no-disk")
    else:
        report["disk"] = probe_disk(
            rounds=args.disk_rounds,
            group_count=args.disk_groups,
            image_size_mib=args.disk_image_size_mib,
            mask_size_kib=args.disk_mask_size_kib,
            multi_disk=not args.single_disk,
            include_root_mount=args.include_root_mount,
            auto_cleanup=not args.no_auto_cleanup,
        )

    if args.no_network:
        report["network"] = skipped_section("disabled by --no-network")
    else:
        report["network"] = probe_network()

    output_dir = Path(args.output_dir)
    json_path = output_dir / f"{args.report_prefix}.json"
    md_path = output_dir / f"{args.report_prefix}.md"

    write_json_report(report, json_path)
    write_markdown_report(report, md_path)

    return report, json_path, md_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    _report, json_path, md_path = run(args)

    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
