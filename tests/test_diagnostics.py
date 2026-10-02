import pytest

from smi_plans import recent_log_lines, run_timing


def test_log_tail_is_bounded(tmp_path):
    path = tmp_path / "bluesky.log"
    path.write_text("old log\n" * 10000 + "traceback\nerror: θ did not move\n", encoding="utf-8")
    assert recent_log_lines(path, lines=1, max_bytes=80) == "error: θ did not move"
    assert recent_log_lines(path, lines=2, max_bytes=80).startswith("traceback")
    assert recent_log_lines(path, max_bytes=1) == ""
    with pytest.raises(ValueError):
        recent_log_lines(path, lines=0)


def test_timing_uses_actual_timestamps():
    start = {"uid": "one", "time": 1000}
    stop = {"run_start": "one", "time": 1025, "exit_status": "fail", "num_events": {"primary": 3}}
    timing = run_timing(start, stop, [1005, 1011, 1017])
    assert timing["status"] == "fail"
    assert timing["duration_s"] == 25
    assert timing["interval_mean_s"] == 6
    assert timing["first_event_delay_s"] == 5
    assert timing["last_event_to_stop_s"] == 8
    with pytest.raises(ValueError, match="epoch"):
        run_timing(start, stop, [0, 1, 2])
    with pytest.raises(ValueError, match="count"):
        run_timing(start, stop, [1005])
    with pytest.raises(ValueError, match="open"):
        run_timing(start, None, [])
