"""
technique_P_power
=================

Archetype P -- Power-supply-biased SAXS/WAXS time series.

This covers operando experiments where a Sorensen supply is pre-set to a voltage, a zero-bias
baseline image is captured, then the supply output is enabled and images are recorded at a
user-selected cadence for a fixed count or duration.  The Sorensen measured voltage and current
are recorded with every image event, not baked into filenames or sampled outside the RunEngine.

.. important::
    Beamline globals required at runtime (injected by the SMI profile collection; not importable
    standalone): ``bps``, ``bpp``, ``Signal``, ``energy``, ``waxs``, ``pil2M``, ``pil900KW``,
    ``xbpm2``, ``xbpm3``, ``pil2M_pos``, ``det_exposure_time``, and ``sorensen_ps1`` if the
    supply is not passed explicitly.
"""

import math
import time
from time import monotonic as _monotonic

from ._core import dedup_readables, fname, merge_md, one_sample_run
from ._preprocessors import ensure_in_wrapper

try:
    import bluesky.plan_stubs as bps
    import bluesky.preprocessors as bpp
except Exception:  # pragma: no cover
    bps = None
    bpp = None


__all__ = ["sorensen_bias_series_run", "sorensen_voltage_program_run"]


_VOLTAGE_SETPOINT_ATTRS = (
    "out_main_setpoint", "voltage", "voltage_setpoint", "voltage_sp", "vset", "v_setpoint"
)
_CURRENT_LIMIT_ATTRS = ("max_current", "current_limit", "current_limit_setpoint", "i_lim")
_OUTPUT_ATTRS = ("out_main_command", "output_enable", "enable")
_VOLTAGE_READBACK_ATTRS = (
    "out_main_readback", "voltage_readback", "voltage_meas", "measured_voltage", "voltage_actual",
    "v_mon", "vread",
)
_CURRENT_READBACK_ATTRS = (
    "current_readback", "current_meas", "measured_current", "current_actual", "i_mon", "iread",
    "current",
)


def _default_supply():
    try:
        return sorensen_ps1                                      # noqa: F821
    except NameError:
        return sorensen                                          # noqa: F821


def _pick_attr(device, explicit, candidates, role):
    if explicit is not None:
        return explicit
    for attr in candidates:
        if hasattr(device, attr):
            return getattr(device, attr)
    raise ValueError(
        "could not find Sorensen {} on {!r}; pass the signal explicitly".format(
            role, getattr(device, "name", device)
        )
    )


def _default_dets(detector):
    if detector == "saxs":
        return [pil2M, xbpm2, xbpm3]                            # noqa: F821
    if detector == "waxs":
        return [pil900KW, xbpm2, xbpm3]                         # noqa: F821
    if detector in ("saxs_waxs", "swaxs", "both"):
        return [pil2M, pil900KW, xbpm2, xbpm3]                  # noqa: F821
    raise ValueError("detector must be 'saxs', 'waxs', or 'saxs_waxs'")


def _default_reads(detector):
    reads = [energy, xbpm2, xbpm3]                              # noqa: F821
    if detector in ("waxs", "saxs_waxs", "swaxs", "both"):
        reads.insert(1, waxs)                                   # noqa: F821
    return reads


def sorensen_bias_series_run(
        name, voltage, *, supply=None, duration=None, n_frames=None, period=1.0, t=0.5,
        detector="saxs", dets=None, reads=None, voltage_setpoint=None, output=None,
        voltage_readback=None, current_readback=None, current_limit=None, output_on=1,
        output_off=0, settle=0.0, baseline_image=True, turn_off_at_end=True,
        geometry="transmission", atten_in=None, baseline=None, md=None,
        name_tokens=("V{bias_voltage_setpoint}", "t{elapsed_s}s", "n{frame_index}")):
    """ONE run: baseline image, then Sorensen-on biased image time series.

    Parameters
    ----------
    name : str
        Human sample / experiment label.
    voltage : float
        Sorensen output voltage setpoint.  The plan sets this before the baseline image while the
        output is still off, then enables the output before the time-series frames.
    supply : ophyd Device, optional
        Sorensen device.  Defaults to the live global ``sorensen_ps1`` from the profile
        collection, with a fallback to ``sorensen`` for local aliases.
    duration : float, optional
        Total biased observation time in seconds.  Frames are started until elapsed >= duration.
    n_frames : int, optional
        Number of biased frames.  Use either ``duration`` or ``n_frames``; if both are passed,
        ``duration`` bounds the run.
    period : float
        Delay between image events in seconds.  This is a software cadence after each event;
        detector exposure time is controlled separately by ``t``.
    t : float
        Detector exposure time in seconds.
    detector : {"saxs", "waxs", "saxs_waxs"}
        Convenience detector choice.  Default is SAXS (``pil2M``); WAXS and combined SWAXS are
        available without changing the plan body.
    dets, reads : list, optional
        Override the detector/read lists directly.  Sorensen readbacks and timing Signals are
        appended automatically to each event.
    voltage_setpoint, output, voltage_readback, current_readback : ophyd Signals, optional
        Explicit Sorensen component signals.  If omitted, common component names are looked up on
        ``supply``.  The live profile-collection ``PowerSupply`` uses ``out_main_setpoint``,
        ``out_main_command``, ``out_main_readback``, and ``current``.
    current_limit : float, optional
        If supplied, set ``supply.max_current`` before setting the voltage.
    output_on, output_off : object
        Values written to the output-enable signal.  Defaults are integer 1/0.
    settle : float
        Sleep after enabling output before the first biased frame.
    baseline_image : bool
        If true, record one baseline image before output enable.  It has ``bias_phase`` =
        ``"baseline"`` and ``frame_index`` = -1.
    turn_off_at_end : bool
        If true, write ``output_off`` in a finalize block, including on abort/error.
    geometry, atten_in, baseline, md, name_tokens : optional
        Standard SMI plan controls.  ``atten_in`` runs after run open and before any image.

    Notes
    -----
    The supply must be an ophyd-style device/signals from the profile collection.  This repo does
    not need to define EPICS PVs for the Sorensen; it only needs the global object injected at
    runtime (or passed as ``supply=``).
    """
    if duration is None and n_frames is None:
        raise ValueError("Pass either n_frames or duration to bound the biased time series.")

    ps = supply if supply is not None else _default_supply()
    v_sp = _pick_attr(ps, voltage_setpoint, _VOLTAGE_SETPOINT_ATTRS, "voltage setpoint")
    out = _pick_attr(ps, output, _OUTPUT_ATTRS, "output-enable signal")
    v_rb = _pick_attr(ps, voltage_readback, _VOLTAGE_READBACK_ATTRS, "voltage readback")
    i_rb = _pick_attr(ps, current_readback, _CURRENT_READBACK_ATTRS, "current readback")
    i_lim = None
    if current_limit is not None:
        i_lim = _pick_attr(ps, None, _CURRENT_LIMIT_ATTRS, "current-limit setpoint")

    if dets is None:
        dets = _default_dets(detector)
    if reads is None:
        reads = _default_reads(detector)

    base = list(baseline) if baseline else []
    try:
        base = base + [pil2M_pos.z]                              # noqa: F821
    except Exception:
        pass

    elapsed = Signal(name="elapsed_s", value=0.0)                # noqa: F821
    frame_index = Signal(name="frame_index", value=0)            # noqa: F821
    phase = Signal(name="bias_phase", value="baseline")         # noqa: F821
    commanded_v = Signal(name="bias_voltage_setpoint", value=float(voltage))  # noqa: F821

    yield from det_exposure_time(t, t)                           # noqa: F821
    sample_name = fname(name, *name_tokens)
    event_reads = dedup_readables(list(dets) + list(reads) + [
        v_rb, i_rb, commanded_v, elapsed, frame_index, phase,
    ])

    def _record(phase_value, frame_i, t0):
        yield from bps.mv(phase, phase_value)
        yield from bps.mv(frame_index, int(frame_i))
        yield from bps.mv(elapsed, time.monotonic() - t0)
        yield from bps.trigger_and_read(event_reads)

    def _measure():
        yield from bps.mv(commanded_v, float(voltage))
        yield from bps.mv(out, output_off)
        if i_lim is not None:
            yield from bps.mv(i_lim, float(current_limit))
        yield from bps.mv(v_sp, float(voltage))
        if baseline_image:
            now = time.monotonic()
            yield from _record("baseline", -1, now)
        yield from bps.mv(out, output_on)
        if settle:
            yield from bps.sleep(settle)

        t0 = time.monotonic()
        i = 0
        while True:
            if duration is not None and (time.monotonic() - t0) >= duration:
                break
            if n_frames is not None and i >= n_frames:
                break
            yield from _record("biased", i, t0)
            i += 1
            if n_frames is not None and i >= n_frames:
                break
            yield from bps.sleep(period)

    run_md = merge_md(
        {
            "sorensen_voltage_setpoint": float(voltage),
            "sorensen_period_s": float(period),
            "detector": detector,
        },
        md,
    )
    plan = one_sample_run(_measure, dets, sample_name=sample_name,
                          scan_name="sorensen_bias_series", geometry=geometry,
                          md=run_md, baseline=base, reads=event_reads)
    if atten_in is not None:
        plan = ensure_in_wrapper(plan, atten_in)

    if turn_off_at_end:
        return (yield from bpp.finalize_wrapper(plan, bps.mv(out, output_off)))
    return (yield from plan)


def sorensen_voltage_program_run(
        name, voltages, hold_times, *, frame_period=2.0, exposure_time=1.0,
        detector="saxs", supply=None, dets=None, reads=None, current_limit=None,
        baseline_image=True, max_lateness=0.1, geometry="transmission",
        atten_in=None, baseline=None, md=None):
    """One run: output-off reference, then one image + electrical read per timed slot.

    ``voltages`` are DIRECT Sorensen setpoints in V, not calibrated sample/HV output.
    ``hold_times`` are positive seconds, each an integer multiple of ``frame_period``.
    The number of biased frames is derived from these holds. ``frame_period`` means
    start-to-start, unlike the post-acquisition ``period`` in ``sorensen_bias_series_run``.

    Default cameras: ``saxs`` (pil2M), ``waxs`` (pil900KW), or ``saxs_waxs`` (both).
    ``dets`` may instead supply camera devices with cam.acquire_time, acquire_period,
    and num_images. Selected cameras are configured twice (SMI Pilatus convention) for
    one image per trigger before staging. All three settings are restored after unstaging.
    ``reads`` defaults to energy, WAXS position, and BPM2/3. V/I, setpoint, output status,
    current limit and frame/program timing are always recorded in primary, even with reads=[].

    Output is disabled before setup and the optional baseline image. The first setpoint
    is written while off. Time zero is the completion of the output-enable command (not
    a measured electrical edge). Changes occur between frames at scheduled step boundaries.
    ``elapsed_s`` is sampled just before triggers; ``read_elapsed_s`` after acquisition.
    V/I are timestamped post-exposure snapshots, NOT exposure averages or sample leakage.
    Baseline times use -1 sentinels; phase/frame_index identify that pre-enable event.

    A slot may start at most ``max_lateness`` seconds late (default 0.1, must be smaller
    than frame_period). Failure to meet this, including slow voltage writes, raises
    RuntimeError and disables output; no catch-up exposures or skipped steps. The final
    hold lasts through its scheduled end even after the last image completes. Exposures
    are never deliberately started if their nominal duration would cross the next slot.
    Detector/read overhead can still overrun; actual times are recorded and checked.

    ``current_limit`` optionally writes max_current in A; otherwise it is only recorded.
    Output is disabled in cleanup before closing the run, including normal RE abort/error.
    The last voltage setpoint/current limit remain programmed with output off.
    ``atten_in`` is an optional pre-baseline measurement-configuration plan. Positioning,
    shutter setup and WAXS geometry should be established before calling this plan.

    The program is non-rewindable: checkpoints are cleared before enabling, so suspenders
    cannot replay voltage history. Deferred pauses wait until completion; immediate pause
    without a checkpoint aborts and cleans up under Bluesky 1.15. RE.halt/process loss
    cannot guarantee generator cleanup.
    """
    def _finite(value, label, *, positive=False):
        value = float(value)
        if not math.isfinite(value) or (positive and value <= 0):
            raise ValueError("{} must be finite{}".format(label, " and positive" if positive else ""))
        return value

    period = _finite(frame_period, "frame_period", positive=True)
    exposure = _finite(exposure_time, "exposure_time", positive=True)
    late_limit = _finite(max_lateness, "max_lateness")
    if not 0 <= late_limit < period:
        raise ValueError("max_lateness must be >= 0 and < frame_period")
    if exposure + 0.001 >= period:
        raise ValueError("exposure_time + 0.001 must be < frame_period to leave readout time")
    vs = [_finite(v, "voltage") for v in voltages]
    holds = [_finite(h, "hold_time", positive=True) for h in hold_times]
    if not vs or len(vs) != len(holds):
        raise ValueError("voltages and hold_times must be nonempty and have equal lengths")
    counts = [round(h / period) for h in holds]
    if any(n < 1 or not math.isclose(h / period, n, rel_tol=1e-9, abs_tol=1e-9)
           for h, n in zip(holds, counts)):
        raise ValueError("each hold_time must be an integer multiple of frame_period")
    limit = None if current_limit is None else _finite(current_limit, "current_limit", positive=True)

    ps = supply if supply is not None else _default_supply()
    # Exact profile-collection PowerSupply API; do not confuse output()/on() methods with Signals.
    electrical = [ps.out_main_setpoint, ps.out_main_readback, ps.current,
                  ps.out_main_status, ps.max_current]
    out = ps.out_main_command
    if dets is None:
        choices = {"saxs": ("pil2M",), "waxs": ("pil900KW",),
                   "saxs_waxs": ("pil2M", "pil900KW")}
        if detector not in choices:
            raise ValueError("detector must be 'saxs', 'waxs', or 'saxs_waxs'")
        dets = [globals()[key] for key in choices[detector]]
    dets = dedup_readables(list(dets))
    if not dets:
        raise ValueError("at least one camera is required")
    camera_settings = []
    for det in dets:
        camera_settings.extend([(det.cam.acquire_time, exposure),
                                (det.cam.acquire_period, exposure + 0.001),
                                (det.cam.num_images, 1)])
    if reads is None:
        reads = [energy, waxs, xbpm2, xbpm3]                     # noqa: F821
    reads = list(reads)
    if baseline is None:
        baseline = [pil2M_pos.z] if "pil2M_pos" in globals() else []  # noqa: F821

    def _signal(key, value):
        return Signal(name=key, value=value)                     # noqa: F821

    phase = _signal("bias_phase", "baseline")
    index = _signal("frame_index", -1)
    step = _signal("program_step", -1)
    target = _signal("bias_voltage_setpoint", vs[0])
    elapsed = _signal("elapsed_s", -1.0)
    read_elapsed = _signal("read_elapsed_s", -1.0)
    scheduled = _signal("scheduled_elapsed_s", -1.0)
    lateness = _signal("timing_lateness_s", 0.0)
    step_elapsed = _signal("step_applied_elapsed_s", -1.0)
    enable_time = _signal("output_enable_time", -1.0)
    exposure_sig = _signal("exposure_s", exposure)
    context = [phase, index, step, target, elapsed, read_elapsed, scheduled,
               lateness, step_elapsed, enable_time, exposure_sig]
    point_reads = dedup_readables(dets + reads + electrical + context)
    trigger_reads = [r for r in dedup_readables(dets + reads)
                     if not any(r is sig for sig in electrical)]
    original = []

    def _check_time(t0, nominal, *, starting=False):
        actual = _monotonic() - t0
        if actual - nominal > late_limit:
            raise RuntimeError(f"Sorensen cadence missed: scheduled {nominal:.6f}s, actual {actual:.6f}s "
                               f"(max_lateness={late_limit}s)")
        if starting and actual + exposure > nominal + period:
            raise RuntimeError("Insufficient time for exposure before the next frame slot")
        return actual

    def _wait_until(t0, nominal):
        remaining = t0 + nominal - _monotonic()
        if remaining > 0:
            yield from bps.sleep(remaining)
        return _check_time(t0, nominal)

    def _point(t0=None, nominal=-1.0):
        if t0 is not None:
            actual = _check_time(t0, nominal, starting=True)
            yield from bps.mv(elapsed, actual, lateness, max(0.0, actual - nominal))
            _check_time(t0, nominal, starting=True)
        # Trigger the selected cameras together; explicitly read electrical signals only
        # after their acquisitions finish. Other triggerable reads (e.g. BPMs) join the group.
        group = "sorensen_program_frame"
        for readable in trigger_reads:
            if callable(getattr(readable, "trigger", None)):
                yield from bps.trigger(readable, group=group)
        yield from bps.wait(group=group)
        yield from bps.mv(read_elapsed, -1.0 if t0 is None else _monotonic() - t0)
        yield from bps.create(name="primary")
        for readable in point_reads:
            yield from bps.read(readable)
        yield from bps.save()

    def _measure():
        if atten_in is not None:
            yield from atten_in()
        if baseline_image:
            yield from _point()
        # Do not permit a suspender/pause to replay an irreversible voltage program.
        yield from bps.clear_checkpoint()
        yield from bps.mv(out, 1)
        t0 = _monotonic()
        yield from bps.mv(enable_time, time.time(), phase, "biased", step_elapsed, 0.0)
        frame_i = 0
        for step_i, (v, count) in enumerate(zip(vs, counts)):
            for within_step in range(count):
                nominal = frame_i * period
                yield from _wait_until(t0, nominal)
                if step_i and within_step == 0:
                    yield from bps.mv(ps.out_main_setpoint, v)
                    applied = _check_time(t0, nominal)
                    yield from bps.mv(step_elapsed, applied)
                yield from bps.mv(index, frame_i, step, step_i, target, v, scheduled, nominal)
                yield from _point(t0, nominal)
                _check_time(t0, nominal + period)
                frame_i += 1
        yield from _wait_until(t0, sum(counts) * period)

    def _off():
        yield from bps.mv(out, 0)

    def _protected_measure():
        return (yield from bpp.finalize_wrapper(_measure(), _off()))

    run_md = merge_md(md, {
        "plan_name": "sorensen_voltage_program_run",
        "detectors": [d.name for d in dets],
        "sorensen_program": {
            "device": ps.name, "voltages": vs, "hold_times": holds,
            "voltage_units": "V (direct Sorensen setpoint)", "frame_period": period,
            "exposure_time": exposure, "n_frames": sum(counts),
            "duration": sum(counts) * period, "baseline_image": bool(baseline_image),
            "current_limit": limit, "max_lateness": late_limit,
            "time_zero": "output-enable command completed",
            "readback_timing": "post-exposure snapshots", "overrun_policy": "raise",
        },
    })

    def _body():
        yield from _off()
        if limit is not None:
            yield from bps.mv(ps.max_current, limit)
        yield from bps.mv(ps.out_main_setpoint, vs[0])
        for sig, value in camera_settings:
            original.append((sig, (yield from bps.rd(sig))))
        for _ in range(2):
            for sig, value in camera_settings:
                yield from bps.mv(sig, value)
        return (yield from one_sample_run(
            _protected_measure, dets,
            sample_name=fname(name, "{bias_phase}", "V{bias_voltage_setpoint}",
                              "n{frame_index}", "t{elapsed_s}s"),
            scan_name="sorensen_voltage_program", geometry=geometry,
            md=run_md, baseline=baseline, reads=point_reads))

    def _restore():
        # Also covers configuration/staging failures. Restore settings only after unstage.
        try:
            yield from _off()
        finally:
            for sig, value in reversed(original):
                yield from bps.mv(sig, value)

    return (yield from bpp.finalize_wrapper(_body(), _restore()))
