from pathlib import Path

from perfprobe.cleanup_guard import (
    SAFE_MARKER_CONTENT,
    SAFE_MARKER_FILE,
    SAFE_SAMPLE_PREFIX,
    safe_cleanup,
    validate_sample_dir,
)


def test_validate_sample_dir_rejects_wrong_prefix(tmp_path: Path) -> None:
    bad_dir = tmp_path / "wrong-name"
    bad_dir.mkdir()
    (bad_dir / SAFE_MARKER_FILE).write_text(SAFE_MARKER_CONTENT, encoding="utf-8")

    ok, reason = validate_sample_dir(bad_dir)
    assert not ok
    assert "safe prefix" in str(reason)


def test_safe_cleanup_deletes_valid_sample_dir(tmp_path: Path) -> None:
    good_dir = tmp_path / f"{SAFE_SAMPLE_PREFIX}unit"
    good_dir.mkdir()
    (good_dir / SAFE_MARKER_FILE).write_text(SAFE_MARKER_CONTENT, encoding="utf-8")
    (good_dir / "payload.bin").write_bytes(b"123")

    outcome = safe_cleanup(good_dir)
    assert outcome["status"] == "success"
    assert not good_dir.exists()
