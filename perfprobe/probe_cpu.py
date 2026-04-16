from __future__ import annotations

import time

import psutil

from .stats import summarize_throughput
from .system_utils import read_cpu_model


def probe_cpu(rounds: int, matrix_size: int) -> dict:
    result: dict = {
        "status": "ok",
        "reason": None,
        "info": {
            "model": read_cpu_model(),
            "logical_cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
        },
        "benchmark_fp32": {
            "enabled": True,
            "unit": "GFLOPS",
            "matrix_size": matrix_size,
            "rounds": [],
            "best": None,
            "median": None,
            "avg": None,
        },
    }

    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - defensive fallback
        result["status"] = "failed"
        result["reason"] = f"numpy import failed: {exc}"
        result["benchmark_fp32"]["enabled"] = False
        return result

    if rounds <= 0 or matrix_size <= 0:
        result["status"] = "failed"
        result["reason"] = "invalid cpu benchmark configuration"
        result["benchmark_fp32"]["enabled"] = False
        return result

    rng = np.random.default_rng(seed=42)
    a = rng.standard_normal((matrix_size, matrix_size), dtype=np.float32)
    b = rng.standard_normal((matrix_size, matrix_size), dtype=np.float32)

    # Warmup to reduce first-iteration bias.
    _ = a @ b

    round_values: list[float] = []
    for idx in range(rounds):
        start = time.perf_counter()
        _ = a @ b
        elapsed = time.perf_counter() - start

        ops = 2.0 * (matrix_size**3)
        gflops = ops / elapsed / 1e9
        round_values.append(gflops)

        result["benchmark_fp32"]["rounds"].append(
            {
                "round": idx + 1,
                "elapsed_sec": elapsed,
                "gflops": gflops,
            }
        )

    summary = summarize_throughput(round_values)
    result["benchmark_fp32"]["best"] = summary["best"]
    result["benchmark_fp32"]["median"] = summary["median"]
    result["benchmark_fp32"]["avg"] = summary["avg"]
    return result
