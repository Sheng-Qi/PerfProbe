from perfprobe.stats import summarize_throughput


def test_summarize_throughput_returns_expected_values() -> None:
    summary = summarize_throughput([1.0, 3.0, 2.0])
    assert summary["best"] == 3.0
    assert summary["median"] == 2.0
    assert summary["avg"] == 2.0
    assert summary["round_count"] == 3


def test_summarize_throughput_handles_empty_input() -> None:
    summary = summarize_throughput([])
    assert summary["best"] is None
    assert summary["round_count"] == 0
