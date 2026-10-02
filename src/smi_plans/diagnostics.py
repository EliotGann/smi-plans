"""Read-only support helpers: bounded log excerpts and measured run timing."""

import math
import statistics
from pathlib import Path


def recent_log_lines(path, *, lines=120, max_bytes=131072):
    """Read a bounded UTF-8 log tail, without loading a multi-GB beamline log.

    Caller selects the path; no implicit home-directory search or authentication.
    Returned text may begin after the start of a long traceback. Increase bounds
    deliberately when more context is needed. Missing files raise OSError.
    """
    if not isinstance(lines, int) or lines < 1 or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("lines/max_bytes must be positive integers")
    with Path(path).expanduser().open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        start = max(0, size - max_bytes)
        stream.seek(start)
        data = stream.read(max_bytes)
    if start:
        # First bytes may be an incomplete line or a partial UTF-8 character.
        data = data.partition(b"\n")[2]
    return "\n".join(data.decode("utf-8", errors="replace").splitlines()[-lines:])


def run_timing(start, stop, event_times):
    """Summarize one closed run using actual epoch-second primary event times.

    All times are seconds; no sorting, filtering, or substitution of missing data.
    Rejects ordinal xarray dimension indices masquerading as timestamps. Failed
    and short runs are returned with their real status/count, not called complete.
    Does not include before-open staging/positioning or classify inter-run gaps.
    """
    if not stop:
        raise ValueError("Run is still open")
    if stop.get("run_start") != start["uid"]:
        raise ValueError("Start/stop documents belong to different runs")
    begin, end = float(start["time"]), float(stop["time"])
    times = [float(t) for t in event_times]
    if not all(math.isfinite(t) for t in [begin, end] + times) or end < begin:
        raise ValueError("Invalid run timestamps")
    if any(t < begin or t > end for t in times) or any(b < a for a, b in zip(times, times[1:])):
        raise ValueError("Event times must be ordered epoch seconds inside start/stop bounds")
    if len(times) != stop.get("num_events", {}).get("primary", 0):
        raise ValueError("Event count differs from stop document; check stream/ingestion")
    intervals = [b - a for a, b in zip(times, times[1:])]
    return {
        "uid": start["uid"], "status": stop.get("exit_status"), "events": len(times),
        "duration_s": end - begin,
        "first_event_delay_s": times[0] - begin if times else None,
        "last_event_to_stop_s": end - times[-1] if times else None,
        "interval_mean_s": statistics.mean(intervals) if intervals else None,
        "interval_median_s": statistics.median(intervals) if intervals else None,
        "interval_max_s": max(intervals) if intervals else None,
    }
