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


__all__ = ["sorensen_bias_series_run", "sorensen_voltage_program_run",
           "build_sorensen_field_program"]


def _finite_number(value, label, *, positive=False):
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("{} must be finite{}".format(label, " and positive" if positive else ""))
    return value


def build_sorensen_field_program(thickness_um, fields_MV_m=None, *, reverse=True):
    """Preview direct Sorensen commands from thickness and electric-field targets.

    Pure calculation, no devices or messages. ``V_sample = thickness_um * field_MV_m``.
    Matches the active CMS Sample.setVoltages: first command 0, subsequent commands
    np.round((target - 190) / 530, 2), keeping only commands <= 11.5 V. Defaults to
    fields 0..150 MV/m in steps of 10. The full reverse is appended (peak repeated),
    or a final zero when reverse=False. Negative converted commands are retained,
    exactly as in CMS; hardware limits may reject them. This is not the older
    piecewise module-level setVoltages helper or the newer SMI impedance-dependent fit.
    Returned dict includes retained source indices to keep hold durations aligned
    when the legacy upper-command filter drops steps.
    """
    thickness = _finite_number(thickness_um, "thickness_um", positive=True)
    import numpy as np

    fields = [_finite_number(f, "field") for f in
              (range(0, 151, 10) if fields_MV_m is None else fields_MV_m)]
    if not fields or any(f < 0 for f in fields):
        raise ValueError("fields_MV_m must be nonempty and nonnegative")
    commands, targets, kept_fields, indices = [], [], [], []
    for i, field in enumerate(fields):
        target = thickness * field
        if not math.isfinite(target):
            raise ValueError("thickness times field must be finite")
        command = 0.0 if i == 0 else float(np.round((target - 190) / 530, 2))
        if command <= 11.5:
            commands.append(command)
            # First step is forced off in CMS even if the requested first field isn't zero.
            targets.append(0.0 if i == 0 else target)
            kept_fields.append(0.0 if i == 0 else field)
            indices.append(i)
    if reverse:
        commands += commands[::-1]
        targets += targets[::-1]
        kept_fields += kept_fields[::-1]
        indices += indices[::-1]
    else:
        commands.append(0.0)
        targets.append(0.0)
        kept_fields.append(0.0)
        indices.append(indices[-1])  # final zero uses the last retained hold
    return {"thickness_um": thickness, "fields_MV_m": kept_fields, "sample_voltages_V": targets,
            "requested_fields_MV_m": fields, "source_indices": indices,
            "voltages": commands, "reverse": bool(reverse),
            "calibration": {"name": "CMS Sample.setVoltages", "slope": 530.0,
                            "intercept": 190.0, "decimals": 2, "max_command_V": 11.5,
                            "zero_policy": "first command forced zero"}}


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
        name, voltages=None, hold_times=None, *, frame_period=2.0, exposure_time=1.0,
        detector="saxs", supply=None, dets=None, reads=None, current_limit=None,
        baseline_image=True, max_lateness=0.1, geometry="transmission",
        atten_in=None, baseline=None, md=None, thickness_um=None, fields_MV_m=None,
        reverse=None, verbose=True):
    """One run: output-off reference, then one image + electrical read per timed slot.

    ``voltages`` are DIRECT Sorensen setpoints in V, not calibrated sample/HV output.
    Alternatively omit voltages and supply thickness_um, with optional fields_MV_m.
    Uses the legacy CMS conversion (see build_sorensen_field_program), including its
    rounding and upper-command filter. Default fields 0..150 by 10, reverse=True.
    Computed targets are recorded separately from measured Sorensen V/I. The reversed
    sequence repeats the peak; reverse=False appends zero using the last retained hold.
    ``hold_times`` are positive seconds, each an integer multiple of ``frame_period``;
    a scalar repeats for every step. A list describes the original, unmirrored sequence.
    The number of biased frames is derived from these holds. ``frame_period`` means
    start-to-start, unlike the post-acquisition ``period`` in ``sorensen_bias_series_run``.

    ``verbose=True`` prints a run summary, baseline notice, step progress and cleanup.
    Every image also records ``expected_output_voltage_V`` calculated from that event's
    measured Sorensen voltage using the CMS model (530 * input + 190). The estimate
    is zero for the output-off baseline, zero command, zero measured input, or a
    reported off status. It is a model estimate, NOT a measured HV output. This model
    is also used for manual voltages= programs. Its assumptions are saved in metadata.

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
    _finite = _finite_number

    period = _finite(frame_period, "frame_period", positive=True)
    exposure = _finite(exposure_time, "exposure_time", positive=True)
    late_limit = _finite(max_lateness, "max_lateness")
    if not 0 <= late_limit < period:
        raise ValueError("max_lateness must be >= 0 and < frame_period")
    if exposure + 0.001 >= period:
        raise ValueError("exposure_time + 0.001 must be < frame_period to leave readout time")
    field_program = None
    if voltages is None:
        if thickness_um is None:
            raise ValueError("Supply voltages OR thickness_um")
        fields = list(range(0, 151, 10) if fields_MV_m is None else fields_MV_m)
        original_count = len(fields)
        field_program = build_sorensen_field_program(
            thickness_um, fields, reverse=True if reverse is None else reverse)
        vs = field_program["voltages"]
    else:
        if any(v is not None for v in (thickness_um, fields_MV_m, reverse)):
            raise ValueError("voltages cannot be combined with thickness/field/reverse arguments")
        vs = [_finite(v, "voltage") for v in voltages]
        original_count = len(vs)
    if hold_times is None:
        raise ValueError("hold_times is required (seconds per step, scalar or list)")
    if isinstance(hold_times, (int, float)):
        holds = [_finite(hold_times, "hold_time", positive=True)] * original_count
    else:
        holds = [_finite(h, "hold_time", positive=True) for h in hold_times]
    if len(holds) != original_count:
        raise ValueError("hold_times must match the original voltage or field sequence length")
    if field_program is not None:
        holds = [holds[i] for i in field_program["source_indices"]]
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
    expected_output = _signal("expected_output_voltage_V", 0.0)
    context = [phase, index, step, target, elapsed, read_elapsed, scheduled,
                lateness, step_elapsed, enable_time, exposure_sig]
    if field_program is not None:
        thickness_sig = _signal("sample_thickness_um", field_program["thickness_um"])
        field_sig = _signal("target_field_MV_m", 0.0)
        sample_voltage_sig = _signal("target_sample_voltage_V", 0.0)
        context += [thickness_sig, field_sig, sample_voltage_sig]
    point_reads = dedup_readables(dets + reads + electrical + context)
    trigger_reads = [r for r in dedup_readables(dets + reads)
                     if not any(r is sig for sig in electrical)]
    original = []
    preview = {"active": False, "elapsed": 0.0}

    def _check_time(t0, nominal, *, starting=False):
        if preview["active"]:
            return nominal
        actual = _monotonic() - t0
        if actual - nominal > late_limit:
            raise RuntimeError(f"Sorensen cadence missed: scheduled {nominal:.6f}s, actual {actual:.6f}s "
                               f"(max_lateness={late_limit}s)")
        if starting and actual + exposure > nominal + period:
            raise RuntimeError("Insufficient time for exposure before the next frame slot")
        return actual

    def _wait_until(t0, nominal):
        if preview["active"]:
            remaining = nominal - preview["elapsed"]
            if remaining > 0:
                yield from bps.sleep(remaining)
            preview["elapsed"] = nominal
            return nominal
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

        def drop_incomplete(exc):
            yield from bps.drop()

        # Close incomplete bundles before run-close baseline preprocessors emit their
        # own create messages. Otherwise a read error is masked by a second-create error.
        return (yield from bpp.contingency_wrapper(
            _read_point(t0), except_plan=drop_incomplete, else_plan=bps.save))

    def _read_point(t0):
        readings = {}
        for readable in point_reads:
            reading = yield from bps.read(readable)
            if reading:
                readings.update(reading)
        if preview["active"]:
            # summarize_plan sends None for every message: show the data field but
            # do not invent electrical measurements or an expected-output value.
            yield from bps.read(expected_output)
            return None, None, None
        # Use exactly the electrical readings saved in this event, not a second PV read.
        input_v = readings[ps.out_main_readback.name]["value"]
        command_v = readings[ps.out_main_setpoint.name]["value"]
        output_status = readings[ps.out_main_status.name]["value"]
        reported_off = output_status == 0 or str(output_status).strip().lower() in ("off", "disabled", "disable")
        expected_v = (0.0 if t0 is None or command_v == 0 or input_v == 0 or reported_off
                      else 530.0 * float(input_v) + 190.0)
        yield from bps.mv(expected_output, expected_v)
        yield from bps.read(expected_output)
        return input_v, readings[ps.current.name]["value"], expected_v

    def _measure():
        if atten_in is not None:
            yield from atten_in()
        if baseline_image:
            if verbose:
                print(f"[{name}] Baseline: output OFF; acquiring reference image and V/I.")
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
                if field_program is not None:
                    yield from bps.mv(field_sig, field_program["fields_MV_m"][step_i],
                                      sample_voltage_sig, field_program["sample_voltages_V"][step_i])
                input_v, current_a, expected_v = yield from _point(t0, nominal)
                if verbose and within_step == 0 and preview["active"]:
                    print(f"[{name}] Preview step {step_i + 1}/{len(vs)}: command {v:g} V; "
                          f"hold {holds[step_i]:g} s ({count} images). V/I and expected output "
                          "will be recorded during acquisition.")
                elif verbose and within_step == 0:
                    target_text = (f"; target sample {field_program['sample_voltages_V'][step_i]:g} V"
                                   if field_program is not None else "")
                    print(f"[{name}] Step {step_i + 1}/{len(vs)}: command {v:g} V{target_text}; "
                          f"hold {holds[step_i]:g} s ({count} images). "
                          f"First image: measured input {input_v:g} V, current {current_a:g} A; "
                          f"expected output {expected_v:.2f} V (CMS estimate). "
                          f"Image {frame_i + 1}/{sum(counts)} saved.")
                _check_time(t0, nominal + period)
                frame_i += 1
        yield from _wait_until(t0, sum(counts) * period)

    def _off():
        yield from bps.mv(out, 0)

    def _protected_measure():
        def cleanup():
            yield from _off()
            if verbose:
                if preview["active"]:
                    print(f"[{name}] Preview cleanup: would command output OFF.")
                else:
                    print(f"[{name}] Cleanup: output commanded OFF.")
        result = yield from bpp.finalize_wrapper(_measure(), cleanup())
        if verbose and preview["active"]:
            print(f"[{name}] Preview complete: {sum(counts)} scheduled program images; "
                  "no hardware operated or data saved.")
        elif verbose:
            print(f"[{name}] Complete: {sum(counts)} program images saved "
                  f"over {sum(counts) * period:g} s, plus {int(bool(baseline_image))} baseline image.")
        return result

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
            "voltage_mode": "field_from_thickness" if field_program is not None else "direct",
            "field_program": field_program,
            "expected_output": {
                "data_key": "expected_output_voltage_V", "units": "V",
                "source": ps.out_main_readback.name, "model": "CMS: 530 * measured_input_V + 190",
                "slope": 530.0, "intercept": 190.0, "measured": False,
                "zero_policy": "baseline, zero setpoint, zero input readback, or reported output off",
            },
        },
    })

    def _body():
        if verbose:
            print(f"[{name}] Starting Sorensen program: {len(vs)} steps, {sum(counts)} images, "
                  f"{sum(counts) * period:g} s; exposure {exposure:g} s every {period:g} s. "
                  "Expected HV output uses the CMS calibration.")
        yield from _off()
        if limit is not None:
            yield from bps.mv(ps.max_current, limit)
        yield from bps.mv(ps.out_main_setpoint, vs[0])
        for sig, value in camera_settings:
            reading = yield from bps.read(sig)
            if reading is None:
                # A real RE rejects None from read(); message-only summarizers return it.
                preview["active"] = True
            else:
                original.append((sig, reading[sig.name]["value"]))
        if preview["active"] and verbose:
            print(f"[{name}] Message-only preview: device values and restoration values "
                  "are unavailable; no timing performance is being tested.")
        for _ in range(2):
            for sig, value in camera_settings:
                yield from bps.mv(sig, value)
        run = one_sample_run(
            _protected_measure, dets,
            sample_name=fname(name, "{bias_phase}", "V{bias_voltage_setpoint}",
                              "n{frame_index}", "t{elapsed_s}s"),
            scan_name="sorensen_voltage_program", geometry=geometry,
            md=run_md, reads=point_reads)
        # The profile's SupplementalData owns "baseline". A second declaration
        # with only local fields would violate the stream's fixed data-key schema.
        if baseline:
            run = bpp.baseline_wrapper(run, baseline, name="sorensen_baseline")
        return (yield from run)

    def _restore():
        # Also covers configuration/staging failures. Restore settings only after unstage.
        try:
            yield from _off()
        finally:
            for sig, value in reversed(original):
                yield from bps.mv(sig, value)

    return (yield from bpp.finalize_wrapper(_body(), _restore()))
