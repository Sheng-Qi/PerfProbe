# PerfProbe

PerfProbe is a focused deep-learning server probe tool. It collects only the required core indicators and outputs stable, explainable benchmark reports in both JSON and Markdown.

## What it measures

- CPU: model, logical cores, physical cores, FP32 matmul GFLOPS (best/median/avg)
- Memory: total/available RAM and DIMM details when accessible
- GPU: device info, FP32 non-Tensor-Core path benchmark (TF32 disabled), memory copy bandwidth
- Disk: mixed read benchmark for 1 image + 1 mask groups, outputs groups/s and MiB/s, supports multi-disk ranking
- Network: link status/speed, active NIC count, best active NIC summary

## Quick start

### Main script

```bash
python3 perfprobe.py
```

### One-click script for beginners

```bash
scripts/run_perfprobe.sh
```

The beginner script can install required system tools (`dmidecode`, `ethtool`, `fio`, `pciutils`, `util-linux`) and Python dependencies into `.venv`, then run the benchmark.

### Run-only mode

Skip install steps and run directly:

```bash
scripts/run_perfprobe.sh --run-only -- --no-gpu
python3 perfprobe.py --run-only --no-gpu
```

## Common options

```bash
python3 perfprobe.py \
  --cpu-rounds 5 --cpu-matrix-size 2048 \
  --gpu-rounds 5 --gpu-matrix-size 4096 --gpu-copy-size-mib 512 \
  --disk-rounds 5 --disk-groups 64 --disk-image-size-mib 5 --disk-mask-size-kib 200 \
  --include-root-mount \
  --output-dir reports --report-prefix perfprobe_report
```

Disable sub-benchmarks when needed:

```bash
python3 perfprobe.py --no-gpu --no-disk
```

## Output

By default, reports are written to:

- `reports/perfprobe_report.json`
- `reports/perfprobe_report.md`

The JSON schema is stable and includes:

- `status` and `reason` fields for each top-level section
- explicit skipped mount points with detailed reason
- disk ranking by `groups_per_sec` (median and best)
- execution identity and effective/sudo context
- cleanup outcome per mount point

## Report field notes

- Disk group definition is fixed as: `1 image + 1 mask`
- `groups/s` means number of complete image+mask groups read per second
- `MiB/s` is preserved for cross-checking throughput
- `best`, `median`, and `avg` are calculated from multi-round results
- Disk benchmark is driven by `fio` (`ioengine=psync`, `direct=1`) to reduce Python overhead and page-cache bias

## Permissions and execution identity

`--execution-identity` controls behavior in sudo context:

- `effective` (default): run as current effective user
- `root`: request root identity (works when current process is root)
- `caller`: when running via sudo, switch to the original caller identity

This affects mount coverage and DIMM details availability. Permission-limited items are marked as skipped with explicit reasons in both JSON and Markdown.

## Disk cleanup behavior

- Default: auto cleanup enabled
- Disable with `--no-auto-cleanup`
- Cleanup deletes only directories created by PerfProbe safe naming + marker constraints
- Cleanup result is recorded per mount (`success`, `not-executed`, `failed`)

## Troubleshooting

- GPU benchmark skipped/failed:
  - Verify PyTorch and CUDA availability (`python3 -c "import torch; print(torch.cuda.is_available())"`)
- DIMM details unavailable:
  - Install `dmidecode`
  - Run with suitable privileges if required
- Most mounts skipped:
  - Run with elevated privilege for broader access
  - Use `--include-root-mount` if root filesystem should be part of comparison
- Disk benchmark failed:
  - Verify `fio` is installed and in `PATH`
  - Some filesystems may reject direct I/O; check disk section `skip_reason` for details
- `.venv` creation failed with `ensurepip is not available`:
  - On Debian/Ubuntu, install venv support and rerun:
    - `sudo apt-get install -y python3-venv`
    - `sudo apt-get install -y python3.10-venv` (match your `python3 -V` minor version)

## Development notes

- Python package code: `perfprobe/`
- Entry point: `perfprobe.py`
- Beginner script: `scripts/run_perfprobe.sh`
