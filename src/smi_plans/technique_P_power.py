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

import time

from ._core import one_sample_run, fname, merge_md, dedup_readables
from ._preprocessors import ensure_in_wrapper

try:
    import bluesky.plan_stubs as bps
    import bluesky.preprocessors as bpp
except Exception:  # pragma: no cover
    bps = None
    bpp = None


__all__ = ["sorensen_bias_series_run"]


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
