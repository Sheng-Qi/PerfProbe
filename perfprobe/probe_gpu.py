from __future__ import annotations

from .stats import summarize_throughput
from .system_utils import mib_from_bytes


def _pick_inner_iterations(single_elapsed_sec: float, target_round_sec: float, max_iterations: int) -> int:
    if single_elapsed_sec <= 0:
        return 1

    estimate = int(target_round_sec / single_elapsed_sec)
    if estimate < 1:
        return 1
    if estimate > max_iterations:
        return max_iterations
    return estimate


def _cuda_elapsed_sec(torch_module, device: int, fn) -> float:
    start_event = torch_module.cuda.Event(enable_timing=True)
    end_event = torch_module.cuda.Event(enable_timing=True)

    torch_module.cuda.synchronize(device)
    start_event.record()
    fn()
    end_event.record()
    torch_module.cuda.synchronize(device)

    # elapsed_time returns milliseconds measured on GPU timeline.
    return float(start_event.elapsed_time(end_event)) / 1000.0


def _run_repeated(fn, repeat: int) -> None:
    for _ in range(repeat):
        fn()


def _disable_tf32(torch_module) -> bool:
    disabled = False
    try:
        torch_module.backends.cuda.matmul.allow_tf32 = False
        disabled = True
    except Exception:
        pass

    try:
        torch_module.backends.cudnn.allow_tf32 = False
        disabled = True
    except Exception:
        pass

    return disabled


def _gpu_matmul_fp32(torch_module, device: int, matrix_size: int, rounds: int) -> dict:
    torch_module.cuda.set_device(device)
    a = torch_module.randn((matrix_size, matrix_size), device=device, dtype=torch_module.float32)
    b = torch_module.randn((matrix_size, matrix_size), device=device, dtype=torch_module.float32)
    out = torch_module.empty_like(a)

    # Warmup to avoid startup cost skew.
    for _ in range(2):
        torch_module.mm(a, b, out=out)
    torch_module.cuda.synchronize(device)

    single_elapsed = _cuda_elapsed_sec(
        torch_module=torch_module,
        device=device,
        fn=lambda: torch_module.mm(a, b, out=out),
    )
    inner_iterations = _pick_inner_iterations(
        single_elapsed_sec=single_elapsed,
        target_round_sec=0.2,
        max_iterations=1024,
    )

    round_rows: list[dict] = []
    series: list[float] = []
    for idx in range(rounds):
        elapsed = _cuda_elapsed_sec(
            torch_module=torch_module,
            device=device,
            fn=lambda: _run_repeated(lambda: torch_module.mm(a, b, out=out), inner_iterations),
        )

        ops = 2.0 * (matrix_size**3) * inner_iterations
        gflops = ops / elapsed / 1e9
        series.append(gflops)

        round_rows.append(
            {
                "round": idx + 1,
                "elapsed_sec": elapsed,
                "inner_iterations": inner_iterations,
                "gflops": gflops,
            }
        )

    summary = summarize_throughput(series)
    return {
        "unit": "GFLOPS",
        "matrix_size": matrix_size,
        "timing_method": "cuda_event",
        "inner_iterations": inner_iterations,
        "rounds": round_rows,
        "best": summary["best"],
        "median": summary["median"],
        "avg": summary["avg"],
    }


def _gpu_copy_bandwidth(torch_module, device: int, copy_size_mib: int, rounds: int) -> dict:
    torch_module.cuda.set_device(device)
    free_bytes, _total_bytes = torch_module.cuda.mem_get_info(device)
    requested = int(copy_size_mib * 1024 * 1024)
    # Keep allocation conservative to avoid OOM under shared GPUs.
    max_safe = max(64 * 1024 * 1024, int(free_bytes * 0.25))
    test_bytes = min(requested, max_safe)

    element_count = max(1, test_bytes // 4)
    src = torch_module.randn(element_count, device=device, dtype=torch_module.float32)
    dst = torch_module.empty_like(src)

    torch_module.cuda.synchronize(device)

    single_elapsed = _cuda_elapsed_sec(
        torch_module=torch_module,
        device=device,
        fn=lambda: dst.copy_(src),
    )
    inner_iterations = _pick_inner_iterations(
        single_elapsed_sec=single_elapsed,
        target_round_sec=0.2,
        max_iterations=8192,
    )

    series: list[float] = []
    round_rows: list[dict] = []

    for idx in range(rounds):
        elapsed = _cuda_elapsed_sec(
            torch_module=torch_module,
            device=device,
            fn=lambda: _run_repeated(lambda: dst.copy_(src), inner_iterations),
        )

        moved_bytes = float(test_bytes) * float(inner_iterations)
        gib_per_sec = (moved_bytes / (1024.0**3)) / elapsed
        series.append(gib_per_sec)
        round_rows.append(
            {
                "round": idx + 1,
                "elapsed_sec": elapsed,
                "gib_per_sec": gib_per_sec,
                "copy_size_mib": test_bytes / (1024.0 * 1024.0),
                "inner_iterations": inner_iterations,
            }
        )

    summary = summarize_throughput(series)
    return {
        "unit": "GiB/s",
        "requested_copy_size_mib": copy_size_mib,
        "effective_copy_size_mib": test_bytes / (1024.0 * 1024.0),
        "timing_method": "cuda_event",
        "inner_iterations": inner_iterations,
        "rounds": round_rows,
        "best": summary["best"],
        "median": summary["median"],
        "avg": summary["avg"],
    }


def probe_gpu(rounds: int, matrix_size: int, copy_size_mib: int) -> dict:
    result: dict = {
        "status": "ok",
        "reason": None,
        "tf32_disabled": False,
        "torch": {
            "version": None,
            "cuda_available": False,
        },
        "device_count": 0,
        "devices": [],
    }

    try:
        import torch
    except Exception as exc:
        result["status"] = "failed"
        result["reason"] = f"pytorch import failed: {exc}"
        return result

    result["torch"]["version"] = getattr(torch, "__version__", "unknown")
    result["torch"]["cuda_available"] = bool(torch.cuda.is_available())

    if not torch.cuda.is_available():
        result["status"] = "skipped"
        result["reason"] = "cuda is not available"
        return result

    result["tf32_disabled"] = _disable_tf32(torch)

    device_count = int(torch.cuda.device_count())
    result["device_count"] = device_count

    for index in range(device_count):
        device_row: dict = {
            "index": index,
            "name": torch.cuda.get_device_name(index),
            "memory_total_mib": None,
            "memory_free_mib": None,
            "status": "ok",
            "reason": None,
            "fp32_matmul": None,
            "memory_copy_bandwidth": None,
        }
        try:
            props = torch.cuda.get_device_properties(index)
            device_row["memory_total_mib"] = mib_from_bytes(props.total_memory)

            free_bytes, _ = torch.cuda.mem_get_info(index)
            device_row["memory_free_mib"] = mib_from_bytes(free_bytes)

            device_row["fp32_matmul"] = _gpu_matmul_fp32(
                torch_module=torch,
                device=index,
                matrix_size=matrix_size,
                rounds=rounds,
            )
            device_row["memory_copy_bandwidth"] = _gpu_copy_bandwidth(
                torch_module=torch,
                device=index,
                copy_size_mib=copy_size_mib,
                rounds=rounds,
            )
        except Exception as exc:  # pragma: no cover - hardware dependent
            device_row["status"] = "failed"
            device_row["reason"] = str(exc)

        result["devices"].append(device_row)

    if not any(device["status"] == "ok" for device in result["devices"]):
        result["status"] = "failed"
        if result["reason"] is None:
            result["reason"] = "all gpu benchmarks failed"

    return result
