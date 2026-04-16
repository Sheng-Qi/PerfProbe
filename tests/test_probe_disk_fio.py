from pathlib import Path

from perfprobe.probe_disk import (
    _build_fio_command_with_jobfile,
    _extract_fio_round_metrics,
    _write_fio_jobfile,
)


def test_extract_fio_round_metrics_uses_bw_bytes_directly() -> None:
    fio_json = {
        "jobs": [
            {
                "read": {
                    "io_bytes": 1114112,
                    "bw_bytes": 10485760,
                    "runtime": 120,
                    "iops": 80.0,
                }
            }
        ]
    }

    metrics = _extract_fio_round_metrics(fio_json=fio_json, group_size_bytes=1114112)

    assert metrics["mib_per_sec"] == 10.0
    assert metrics["groups_per_sec"] == 10485760 / 1114112


def test_extract_fio_round_metrics_falls_back_to_runtime_when_bw_missing() -> None:
    fio_json = {
        "jobs": [
            {
                "read": {
                    "io_bytes": 2000,
                    "bw_bytes": 0,
                    "runtime": 200,
                    "iops": 0,
                }
            }
        ]
    }

    metrics = _extract_fio_round_metrics(fio_json=fio_json, group_size_bytes=1000)

    assert metrics["bw_bytes_per_sec"] == 10000
    assert metrics["groups_per_sec"] == 10


def test_build_fio_command_with_jobfile_uses_short_arguments(tmp_path: Path) -> None:
    jobfile = tmp_path / "round.fio"
    cmd = _build_fio_command_with_jobfile(jobfile)

    assert cmd[0] == "fio"
    assert "--max-jobs=1" in cmd
    assert "--output-format=json" in cmd
    assert any(str(jobfile) == token for token in cmd)
    assert not any(token.startswith("--filename=") for token in cmd)


def test_write_fio_jobfile_has_one_filename_per_line(tmp_path: Path) -> None:
    files = [tmp_path / f"file_{index:03d}.bin" for index in range(4)]
    for path in files:
        path.write_bytes(b"1")

    jobfile = tmp_path / "round.fio"
    _write_fio_jobfile(file_paths=files, jobfile_path=jobfile)

    text = jobfile.read_text(encoding="utf-8")
    for path in files:
        assert f"filename={path}" in text
    assert text.count("filename=") == len(files)
