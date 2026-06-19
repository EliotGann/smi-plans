"""
smi_plans._core
==============

Shared run-shaping primitives for SMI-SWAXS plans.  These encode the *target* acquisition
architecture (see ``templates/_analysis/BEST_PRACTICES_DRAFT.md``):

* **One run per logical sample** (:func:`one_sample_run`) -- a single ``@run_decorator`` +
  ``@stage_decorator`` envelope around an inner measurement plan, with merged ``md={}`` and
  a filename templated from *recorded* fields.
* **Multiple runs open at once** (:func:`multi_sample_run`) -- the run-key interleave that
  lets a slow / in-vacuum axis (``waxs.arc``, ``stage.phi``) move once while every sample's run is
  open simultaneously.  (Generalized from the "Tom" prototype in ``30-user-Gann.py``.)
* **Sample positioning** (:func:`goto_sample`) -- expand a :class:`_samples.Sample` into the
  right ``bps.mv`` calls for piezo and/or hexapod.
* **Detector selection** (:func:`saxs_waxs_dets`) -- the arc-aware SAXS/WAXS choice, plus
  helpers to *declare separate SAXS/WAXS streams* (the recommended modern form).
* **Filename tokens** (:func:`fname`, :data:`COMMON_TOKENS`) -- build ``sample_name``
  templates that reference recorded stream fields (``{energy_energy}``, ``{xbpm2_sumX}`` ...)
  instead of ``.get()``-baked strings.

.. important::
    References beamline globals injected by the SMI profile collection at runtime and **not
    importable standalone**: ``bps``, ``bpp``, ``Signal``, ``np``, and the device objects
    ``piezo``, ``stage``, ``waxs``, ``energy``, ``pil2M``, ``pil900KW``, ``pil2M_pos``,
    ``xbpm2``, ``xbpm3``, ``det_exposure_time`` ...  Import / run only inside the live beamline
    IPython environment.

Filename templating contract
----------------------------
The SMI file writer substitutes ``{<field>}`` tokens in the ``sample_name`` metadata from the
**recorded primary-stream event** of each frame.  The field name is the *data key* of a read
signal, conventionally ``<device-name>_<signal-attr>`` (e.g. ``energy_energy``,
``pin_diode_current2_mean_value``, ``xbpm2_sumX``).  Therefore: **anything you want in the
filename must be in your ``trigger_and_read`` list** -- including small artificial ``Signal``
objects you create (which carry whatever name you gave them at construction and need not be
strings).  This is the mechanism behind ``nist/richter/Cl_nexafs.py``.
"""

from collections import OrderedDict

try:
    import bluesky.plan_stubs as bps
    import bluesky.preprocessors as bpp
except Exception:  # pragma: no cover - outside the beamline env
    bps = None
    bpp = None

try:
    # The area-detector trigger mixin whose `trigger` enforces stage-before-trigger
    # (QuadEMV33 / pin_diode and the Pilatus detectors derive from it).  Used to decide which
    # *reads* must be staged with the run (see `_needs_staging`).
    from ophyd.areadetector.trigger_mixins import TriggerBase as _TriggerBase
except Exception:  # pragma: no cover - ophyd layout differs / outside beamline env
    _TriggerBase = None


__all__ = [
    "one_sample_run",
    "multi_sample_run",
    "goto_sample",
    "saxs_waxs_dets",
    "declare_saxs_waxs_streams",
    "fname",
    "COMMON_TOKENS",
    "merge_md",
    "dedup_readables",
    "stageable_reads",
]


# ---------------------------------------------------------------------------
# Readable de-duplication (avoid duplicate-data-key collisions in trigger_and_read)
# ---------------------------------------------------------------------------
def _is_ancestor(maybe_ancestor, dev):
    """True if ``maybe_ancestor`` is ``dev`` or one of its ophyd ancestors (via ``.parent``)."""
    node = dev
    seen = 0
    while node is not None and seen < 50:        # cycle / runaway guard
        if node is maybe_ancestor:
            return True
        node = getattr(node, "parent", None)
        seen += 1
    return False


def dedup_readables(readables):
    """Drop readables that would collide on data keys in one ``trigger_and_read``.

    bluesky raises ``ValueError: Data keys ... collide`` if two readables in the same Event
    report overlapping keys.  The common SMI case is reading a **detector together with one of
    its own sub-devices** -- e.g. ``pil900KW`` (whose ``.motors`` ``kind`` is ``normal``, so it
    records ``waxs_arc``/``waxs_bsx``/``waxs_bsy``) and ``waxs``, which **is** ``pil900KW.motors``
    (``waxs = pil900KW.motors`` on the beamline).  Reading both records those keys twice.

    Rule: keep a readable unless it is an **ancestor/descendant of**, or **identical to**, a
    readable already kept.  When both an ancestor and a descendant are requested, the **ancestor
    wins** (it records a superset, so the descendant's ``{token}`` still resolves).  Order is
    otherwise preserved; non-ophyd readables (no ``.parent``) are de-duplicated only by identity.
    """
    kept = []
    for r in readables:
        replaced = False
        skip = False
        for i, k in enumerate(kept):
            if k is r:
                skip = True
                break
            if _is_ancestor(r, k):        # r is an ancestor of kept k -> r records a superset
                kept[i] = r               # keep the ancestor instead of the descendant
                replaced = True
                break
            if _is_ancestor(k, r):        # kept k is an ancestor of r -> k already covers r
                skip = True
                break
        if replaced:
            # collapse any OTHER kept descendants of r so the ancestor isn't duplicated
            kept = [x for x in kept if x is r or not _is_ancestor(r, x)]
            continue
        if skip:
            continue
        kept.append(r)
    return kept


def _needs_staging(obj):
    """True if ``obj`` is a read whose ``trigger`` *requires* prior staging.

    The failure this guards against is the ophyd area-detector trigger mixin
    (``ophyd.areadetector.trigger_mixins.TriggerBase`` / ``SingleTrigger``), whose ``trigger``
    raises ``"This detector is not ready to trigger. Call the stage() method before
    triggering."`` when the device was not staged.  ``QuadEMV33`` (``pin_diode``) and the
    Pilatus detectors are of this family.

    We deliberately do **not** treat every ``Stageable`` device as needing staging: a generic
    ophyd ``Device`` (``energy``, ``xbpm2``, ``waxs`` ...) also exposes a *no-op* ``stage``, but
    those are only read -- staging them was never required and doing so now would (a) change
    long-standing behavior and (b) risk ``RedundantStaging`` if a nested plan stages them too.
    So we restrict to devices that actually enforce stage-before-trigger.

    Detection is by the ``_acquisition_signal`` attribute that ``TriggerBase`` installs (robust
    to import paths), with an ``isinstance`` fast-path when the mixin is importable.  Plain
    ``Signal`` filename-token objects have no ``stage`` at all and are excluded too.
    """
    if not callable(getattr(obj, "stage", None)):
        return False
    if _TriggerBase is not None and isinstance(obj, _TriggerBase):
        return True
    # Fallback when the mixin class is not importable in this env: the staged-trigger family is
    # exactly the set of devices that own an ``_acquisition_signal``.
    return hasattr(obj, "_acquisition_signal")


def stageable_reads(dets, reads):
    """Reads that must be staged but are not already covered by ``dets``.

    ``trigger_and_read(dets + reads)`` will *trigger* every triggerable in ``reads`` (e.g.
    ``pin_diode``), but historically only ``dets`` were staged -- so a triggerable read blew up
    with ``RuntimeError: This detector is not ready to trigger``.  This returns the subset of
    ``reads`` that (a) is stageable and (b) is not ``dets`` nor an ancestor/descendant of a
    ``det`` (which would cause ophyd ``RedundantStaging``).  Order is preserved.
    """
    dets = list(dets or [])
    extra = []
    for r in (reads or []):
        if not _needs_staging(r):
            continue
        # already staged as a det, or a det is its ancestor/descendant -> skip (avoid
        # RedundantStaging); also skip if we already picked an overlapping read.
        if any(d is r or _is_ancestor(d, r) or _is_ancestor(r, d) for d in dets):
            continue
        if any(e is r or _is_ancestor(e, r) or _is_ancestor(r, e) for e in extra):
            continue
        extra.append(r)
    return extra


# ---------------------------------------------------------------------------
# Filename token helpers
# ---------------------------------------------------------------------------
#: Frequently-useful {tokens} -> the readable you must include in trigger_and_read for them
#: to resolve.  Use these to build ``sample_name`` templates.  (Values are the *device* you
#: pass to ``trigger_and_read``; the token text is the recorded data-key.)
COMMON_TOKENS = OrderedDict([
    ("{energy_energy}",            "energy"),       # DCM photon energy (eV)
    ("{xbpm2_sumX}",               "xbpm2"),        # I0 (BPM2)
    ("{xbpm3_sumX}",               "xbpm3"),        # I0 (BPM3)
    ("{waxs_arc}",                 "waxs"),         # WAXS arc angle (deg)
    ("{pin_diode_current2_mean_value}", "pin_diode"),  # transmitted flux
])


def fname(base, *tokens, sep="_"):
    """Compose a ``sample_name`` template from a base label and ``{field}`` tokens.

    This does *not* format any values -- it returns a template string with ``{field}``
    placeholders that the file writer fills from the recorded event.  Keep your *human* base
    label first (often the sample name), then append context tokens.

    Example
    -------
    >>> fname("PS40nm", "{energy_energy}eV", "ai{incident_angle}", "bpm{xbpm2_sumX}")
    'PS40nm_{energy_energy}eV_ai{incident_angle}_bpm{xbpm2_sumX}_'

    Note the trailing ``sep`` (matches the SMI convention of names ending in ``_``).
    """
    parts = [base] + [t for t in tokens if t]
    return sep.join(parts) + sep


def merge_md(*dicts):
    """Shallow-merge metadata dicts left-to-right (later wins).  ``None`` entries ignored."""
    out = {}
    for d in dicts:
        if d:
            out.update(d)
    return out


# ---------------------------------------------------------------------------
# Sample positioning
# ---------------------------------------------------------------------------
def goto_sample(sample, *, piezo_dev=None, hexa_dev=None, settle=0.0):
    """Move to ``sample``'s piezo and/or hexapod coordinates (only axes that are set).

    Parameters
    ----------
    sample : _samples.Sample
    piezo_dev : object with ``.x/.y/.z/.th`` (default: global ``piezo``)
    hexa_dev : object with ``.x/.y/.z/.th`` (default: global ``stage``)
    settle : float
        Optional sleep after the move.

    Notes
    -----
    The motors you move are recorded automatically if you include ``piezo``/``stage`` in your
    ``trigger_and_read`` list, so you need not bake positions into the filename.
    """
    p = piezo_dev if piezo_dev is not None else piezo            # noqa: F821 (global)
    h = hexa_dev if hexa_dev is not None else stage              # noqa: F821 (global)

    args = []
    for short, val in sample.piezo_moves().items():
        args += [getattr(p, short), val]
    for short, val in sample.hexa_moves().items():
        args += [getattr(h, short), val]

    if args:
        yield from bps.mv(*args)
    if settle:
        yield from bps.sleep(settle)


# ---------------------------------------------------------------------------
# Detector selection
# ---------------------------------------------------------------------------
def saxs_waxs_dets(*, use_saxs=True, use_waxs=True, arc_block_deg=15,
                   saxs_det=None, waxs_det=None, waxs_arc_dev=None):
    """Return the detector list, dropping SAXS when the WAXS arc occludes it.

    Reproduces the near-universal idiom
    ``[pil900KW] if waxs.arc.position < 15 else [pil900KW, pil2M]`` but as an explicit,
    parameterized helper.  Prefer :func:`declare_saxs_waxs_streams` for the cleaner
    separate-stream form when you can.

    Parameters
    ----------
    use_saxs, use_waxs : bool
        Whether each detector is wanted at all.  (Named ``use_*`` so they do not shadow the
        ``waxs`` device global used as the default arc source.)
    arc_block_deg : float
        SAXS is dropped when ``waxs.arc.position < arc_block_deg`` (the reflected/forward beam
        path is blocked by the WAXS detector at low arc angles).
    saxs_det, waxs_det : devices
        Defaults to globals ``pil2M`` / ``pil900KW``.
    waxs_arc_dev : device
        The device carrying ``.arc.position`` (default: global ``waxs``).
    """
    sdet = saxs_det if saxs_det is not None else pil2M           # noqa: F821
    wdet = waxs_det if waxs_det is not None else pil900KW        # noqa: F821
    arc = waxs_arc_dev if waxs_arc_dev is not None else waxs     # noqa: F821

    dets = []
    if use_waxs:
        dets.append(wdet)
    if use_saxs and arc.arc.position >= arc_block_deg:
        dets.append(sdet)
    return dets


def declare_saxs_waxs_streams(saxs_det=None, waxs_det=None):
    """(Advisory) Yield ``declare_stream`` messages for separate SAXS / WAXS streams.

    The recommended modern alternative to arc-conditional single lists: always declare both,
    and route each detector's readings to its own named stream.  Call inside an open run::

        yield from declare_saxs_waxs_streams()
        ...
        yield from bps.trigger_and_read([pil900KW, ...], name="waxs")
        yield from bps.trigger_and_read([pil2M, ...], name="saxs")

    Kept as a thin helper because stream layout is an experiment-wide decision (see open
    question #4 in the best-practices draft).
    """
    sdet = saxs_det if saxs_det is not None else pil2M           # noqa: F821
    wdet = waxs_det if waxs_det is not None else pil900KW        # noqa: F821
    yield from bps.declare_stream(wdet, name="waxs")
    yield from bps.declare_stream(sdet, name="saxs")


# ---------------------------------------------------------------------------
# One run per sample
# ---------------------------------------------------------------------------
def one_sample_run(measure, dets, *, sample_name, scan_name, geometry=None,
                   md=None, baseline=None, reads=None):
    """Wrap ``measure`` in a single staged run for one sample.

    This is the canonical Tier-4 envelope.  ``measure`` is a *generator-function taking no
    args* (a closure over your loop variables) that yields the actual
    ``trigger_and_read``/``mv`` messages.  The run carries ``sample_name`` (which may be a
    ``{field}`` filename template), ``scan_name``, ``geometry`` and any extra ``md``.

    Parameters
    ----------
    measure : callable () -> plan
        The inner measurement loop (no ``open_run``/``close_run`` of its own).
    dets : list
        Detectors to stage for the run.
    sample_name : str
        Often a :func:`fname` template, e.g. ``"PS_{energy_energy}eV_bpm{xbpm2_sumX}_"``.
    scan_name : str
        The plan's purpose (e.g. ``"giwaxs_energy_sweep"``).
    geometry : str, optional
        ``"reflection"`` or ``"transmission"``.
    md : dict, optional
        Extra metadata merged in (caller intent: ``project_name`` etc.).  Caller md wins
        over the standard keys *except* ``scan_name``/``geometry`` which are technique-fixed
        unless you override here.
    baseline : list, optional
        Devices recorded in a baseline stream at run open/close (constants: SDD, attenuator
        state, alignment offsets, temperature setpoint ...).
    reads : list, optional
        The same ``reads`` you pass to ``trigger_and_read`` alongside ``dets``.  Any read that
        is a stageable, *triggerable* device (e.g. ``pin_diode`` / ``QuadEMV33``) is staged
        with the run too -- otherwise triggering it raises ``"This detector is not ready to
        trigger. Call the stage() method before triggering."``.  Reads that don't need staging
        (``energy``, ``xbpm2``, ``Signal`` token objects) and reads already covered by ``dets``
        are ignored, so this is safe to always pass.

    Returns
    -------
    The plan; ``yield from`` it.
    """
    run_md = merge_md(
        {"scan_name": scan_name},
        {"geometry": geometry} if geometry else {},
        md,                                  # caller intent (project_name, sample md, ...)
        {"sample_name": sample_name},        # name template always last so it is authoritative
    )

    # Stage detectors AND any triggerable reads that need staging (e.g. pin_diode); skip reads
    # already covered by dets to avoid ophyd RedundantStaging.
    to_stage = list(dets) + stageable_reads(dets, reads)

    @bpp.stage_decorator(to_stage)
    @bpp.run_decorator(md=run_md)
    def _inner():
        yield from measure()

    body = _inner()
    if baseline:
        body = bpp.baseline_wrapper(body, baseline)
    return (yield from body)


# ---------------------------------------------------------------------------
# Many runs open at once (slow-axis economy)
# ---------------------------------------------------------------------------
def multi_sample_run(samples, slow_axis, slow_positions, point, *,
                     dets, scan_name, geometry=None, md=None,
                     goto=None, settle=0.0, reads=None):
    """Open one run per sample, sweep a slow axis ONCE, and record every sample at each step.

    This is the sanctioned form of "multiple open runs at once": it minimizes travel of a
    slow / in-vacuum axis (``waxs.arc``, ``stage.phi``) by moving it in the *outer* loop while N
    per-sample runs are simultaneously open, writing each sample's frame into its own run via
    run keys.  Generalized + cleaned up from the ``30-user-Gann.py`` "Tom" prototype, with a
    ``finalize_wrapper`` so all runs close even on error.

    Parameters
    ----------
    samples : _samples.SampleList (or list of Sample)
        One run is opened per sample, keyed ``"run {i}"``.
    slow_axis : ophyd positioner
        The expensive axis to move once per outer step (e.g. ``waxs``, ``stage.phi``).
    slow_positions : sequence
        Outer-loop setpoints for ``slow_axis`` (consider ordering / ``[::-1]`` to avoid
        backtracking).
    point : callable(sample, slow_value) -> plan
        Per (sample, slow-position) measurement.  It must position the sample's *fast* axes
        as needed and end by yielding ``bps.trigger_and_read([...])`` -- but must NOT open or
        close runs.  It runs *inside* that sample's run (the wrapper sets the run key around
        it).
    dets : list
        Detectors staged for the whole interleaved acquisition.
    scan_name, geometry, md :
        As in :func:`one_sample_run`; ``md`` is merged per sample with the sample's own md.
    goto : callable(sample) -> plan, optional
        Plan to coarse-position a sample (default: :func:`goto_sample`).  Called once per
        (slow-step, sample) so the fast stage is at the right sample before the point plan.
    settle : float
        Sleep after each ``slow_axis`` move (let the arc/phi settle).
    reads : list, optional
        The ``reads`` your ``point`` plan passes to ``trigger_and_read`` alongside ``dets``.
        Any stageable, triggerable read (e.g. ``pin_diode`` / ``QuadEMV33``) is staged for the
        whole interleaved block too; reads that don't need staging or are already covered by
        ``dets`` are ignored, so it is safe to always pass.

    Notes
    -----
    * With multiple runs open, the BestEffortCallback's table is meaningless; configure a
      ``RunRouter`` that builds a fresh per-run callback and disables tables (see README).
    * ``stage``/``unstage`` of detectors is handled once for the whole block via
      ``stage_wrapper``.
    """
    samples = list(samples)
    n = len(samples)
    _goto = goto if goto is not None else (lambda s: goto_sample(s, settle=settle))

    # Track which run keys are currently open so the finalize clause closes ONLY those still
    # open (avoids a double-close on the normal success path).
    open_keys = set()

    def _body():
        # Open one run per sample, each with its own sample_name + merged md.
        for i, s in enumerate(samples):
            key = "run {}".format(i)
            run_md = merge_md(
                {"scan_name": scan_name},
                {"geometry": geometry} if geometry else {},
                md,
                s.base_md(),
            )
            yield from bpp.set_run_key_wrapper(bps.open_run(run_md), key)
            open_keys.add(key)

        # Slow axis outermost: moved exactly len(slow_positions) times for ALL n samples.
        for sv in slow_positions:
            yield from bps.mv(slow_axis, sv)
            if settle:
                yield from bps.sleep(settle)
            for i, s in enumerate(samples):
                yield from _goto(s)
                yield from bpp.set_run_key_wrapper(point(s, sv), "run {}".format(i))

        for i in range(n):
            key = "run {}".format(i)
            yield from bpp.set_run_key_wrapper(bps.close_run(), key)
            open_keys.discard(key)

    def _close_remaining():
        # Safety net: close only runs still open (error/abort path); no-op on success.
        for key in sorted(open_keys):
            yield from bpp.set_run_key_wrapper(bps.close_run(), key)
        open_keys.clear()

    plan = bpp.finalize_wrapper(_body(), _close_remaining())
    plan = bpp.stage_wrapper(plan, list(dets) + stageable_reads(dets, reads))
    return (yield from plan)
