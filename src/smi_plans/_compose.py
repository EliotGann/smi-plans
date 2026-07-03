"""
smi_plans._compose
=================

The **composable experiment model** for SMI-SWAXS.

A real SMI experiment is not "one of A-O".  It is an assembly of independent concerns:

    beam / q-range      -- which energies, which detectors + WAXS-arc (the q reach)
    apparatus / geometry -- grazing vs transmission, Linkam/Lakeshore, RH cell, e-chem ...
    sampling / scanning  -- a single spot, 5 locations, a grid, a phi rock ...
    manual / interactive -- "swap the sample and type its thickness", "I set T=35C, confirm",
                            "wait until I start the pump" -- captured as recorded Signals
    what to record       -- the detectors + context Signals captured at each point

This module lets you express an experiment as a **measurement core** wrapped by a **stack of
nested scan axes**, in an order you choose.  The A-O ``technique_*`` files are then just
*preset recipes* that assemble these same pieces -- and a GUI can assemble them on the fly.

Mental model
------------
An experiment is nested loops around one ``trigger_and_read``::

    for sample:                         # outer (handled by acquire / *_bar)
        <apparatus setup: geometry, align, heater on, atten in>   (once per run)
        for arc in waxs_arc:            # ScanAxis (slow, in-vacuum -> outer)
            for T in temperatures:      # ScanAxis (slow -> outer)
                for ai in incidence:    # ScanAxis
                    for e in energies:  # ScanAxis
                        at 5 x-locations # ScanAxis (fast -> inner)
                            trigger_and_read(dets + context)   # the core

Each loop level is a :class:`ScanAxis`.  You build the axes you want, put them in the order
you want (slow outermost), and :func:`acquire` nests them inside ONE run with the filename
templated from whatever the axes record.

Key types
---------
* :class:`ScanAxis` -- one loop dimension: values, how to move to a value, what Signal to
  record, settle, optional per-point hook, and a "slowness" hint for the ordering guardrail.
* :func:`nest_axes` -- turn a list of axes + a measurement core into a single nested plan.
* :func:`acquire` -- the experiment builder: ONE run for ONE sample = setup + nested axes +
  ``trigger_and_read``, with merged ``md`` and a templated filename.
* axis constructors -- :func:`energy_axis`, :func:`temperature_axis`, :func:`incidence_axis`,
  :func:`motor_axis`, :func:`spatial_grid_axes`, :func:`potential_axis`, :func:`rh_axis`,
  :func:`time_axis` -- ready-made axes for the common concerns.
* manual / interactive -- :func:`manual_step` / :func:`manual_value` (collect a hand-set value
  into a recorded Signal), :func:`manual_axis` (a user-driven enumerated loop),
  :func:`manual_loop` (open-ended "keep going until I stop"), :func:`pause_for_user`.

.. important::
    References beamline globals injected by the SMI profile collection at runtime (``bps``,
    ``Signal``, ``np``, ``energy``, ``waxs``, ``piezo``, ``xbpm2`` ...).  ``ScanAxis`` itself
    is plain data (GUI-safe to construct); only *running* a plan needs the live environment.
"""

import warnings

from ._core import (one_sample_run, goto_sample, fname, merge_md, dedup_readables,
                    COMMON_TOKENS)
from ._regions import sample_region, region_grid_offsets

try:
    import bluesky.plan_stubs as bps
    import bluesky.preprocessors as bpp
except Exception:  # pragma: no cover
    bps = None
    bpp = None


__all__ = [
    "ScanAxis",
    "nest_axes",
    "acquire",
    "acquire_bar",
    "polygon_region_run",
    "polygon_region_bar",
    "validate_name_tokens",
    "move_energy_fb",
    "energy_axis",
    "temperature_axis",
    "incidence_axis",
    "motor_axis",
    "spatial_grid_axes",
    "potential_axis",
    "rh_axis",
    "time_axis",
    "manual_step",
    "manual_value",
    "manual_axis",
    "manual_loop",
    "pause_for_user",
    "SPEED_SLOW", "SPEED_MEDIUM", "SPEED_FAST",
]


# Slowness hints used by the ordering guardrail (higher = slower / costlier to move).
SPEED_FAST = 0      # piezo.x/y/z, fast Signals
SPEED_MEDIUM = 1    # incident angle, energy (DCM), potential, RH
SPEED_SLOW = 2      # waxs.arc, stage.phi, temperature (equilibration), anything in-vacuum


class ScanAxis(object):
    """One dimension of a scan: a set of values plus how to *visit* and *record* each.

    A ``ScanAxis`` is essentially a ``for`` loop turned into data.  You can construct it by
    hand, or use the ready-made constructors (:func:`energy_axis`, etc.).

    Parameters
    ----------
    name : str
        Human / metadata label (e.g. ``"energy"``, ``"temperature"``, ``"x_grid"``).
    values : sequence
        The points to visit, in order.  ``None`` / empty means a single pass with no move
        (a degenerate axis -- useful so a recipe can "turn off" a dimension uniformly).
    move : callable(value) -> plan, optional
        How to get to a value.  MUST be a *plan* (a generator-function that ``yield from``\\s
        messages -- e.g. ``bps.mv``), never a function that does direct ``.put()``/``.set()``.
        Default: ``bps.mv(device, value)`` if ``device`` is given.  Pass a custom plan for
        non-trivial moves (e.g. ``goto_temperature`` with equilibration, or
        "incident angle = th0 + value").
    device : ophyd positioner, optional
        Shortcut: if given and ``move`` is None, the axis does ``bps.mv(device, value)``.
    record : ophyd Signal, optional
        A Signal set to ``value`` at each point (via ``bps.mv`` -- a message, never ``.put()``)
        so the value is recorded in the primary stream (and thus usable as a ``{record.name}``
        filename token).  If you set ``device`` and it is itself a readable you include in
        ``reads``, you may not need a separate ``record``.
    settle : float
        Sleep (s) after moving, before descending to the inner axis.
    per_point : callable() -> plan, optional
        Extra plan run at this level *after* moving + settling, before the inner axis (e.g. a
        beam-loss re-seek tied to energy, or a fresh-spot nudge tied to this loop).
    reads : list, optional
        Extra readables to add to the event *because of this axis* (rarely needed; usually the
        ``record`` Signal is enough).  Collected and merged by :func:`acquire`.
    speed : int
        Slowness hint (:data:`SPEED_FAST` / ``MEDIUM`` / ``SLOW``) used to warn when a slow
        axis is nested too far inside (i.e. moved too often).  Does not constrain order.
    reverse_alternate : bool
        If True, reverse this axis's direction on every other pass of the *outer* axes
        (boustrophedon / snake) to avoid backtracking.  Useful for slow axes.
    """

    def __init__(self, name, values, *, move=None, device=None, record=None,
                 settle=0.0, per_point=None, reads=None, speed=SPEED_MEDIUM,
                 reverse_alternate=False):
        self.name = name
        self.values = list(values) if values is not None else []
        self.device = device
        self._move = move
        self.record = record
        self.settle = settle
        self.per_point = per_point
        self.reads = list(reads) if reads else []
        self.speed = speed
        self.reverse_alternate = reverse_alternate

    def __repr__(self):
        return "ScanAxis({!r}, n={}, speed={})".format(self.name, len(self.values), self.speed)

    def is_degenerate(self):
        """True if this axis has 0/1 points and nothing to move (a no-op pass)."""
        return len(self.values) == 0

    def move_to(self, value):
        """Plan: move to ``value`` -- ALL via Bluesky messages (no direct ``.put()``).

        Resolution order:
        * a custom ``move`` plan (a generator-function that ``yield from``\\s messages), else
        * ``yield from bps.mv(device, value)`` if a ``device`` is set, else
        * nothing to move (a pure "record" axis).

        The ``record`` Signal (if any) is set with ``yield from bps.mv(record, value)`` -- a
        Signal is settable through the RunEngine, so the value is set *as a message* and will be
        captured when ``record`` is read.  This keeps the whole axis message-pure (Tenet:
        plans contain only messages, never ``.put()``).

        A custom ``move`` MUST be a plan (generator).  If you find yourself wanting a plain
        side-effecting function here, the right fix is a proper settable ophyd device driven by
        ``bps.mv`` -- see ``smi_plans._devices`` and ``docs/DEVICE_DEBT.md``.
        """
        if self._move is not None:
            yield from self._move(value)
        elif self.device is not None:
            yield from bps.mv(self.device, value)
        # else: nothing to move (a pure "record" axis)
        if self.record is not None:
            yield from bps.mv(self.record, value)     # set the recorded Signal via a message
        if self.settle:
            yield from bps.sleep(self.settle)
        if self.per_point is not None:
            yield from self.per_point()

    def token(self, fmt=None):
        """Convenience: the ``{field}`` filename token for this axis's recorded value.

        Returns ``"{<record.name>}"`` if a ``record`` Signal is set, else ``""``.  ``fmt`` may
        wrap it, e.g. ``axis.token("ai{}")`` -> ``"ai{incident_angle}"``.
        """
        if self.record is None:
            return ""
        tok = "{" + self.record.name + "}"
        return fmt.format(tok) if fmt else tok


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------
def nest_axes(axes, measure):
    """Build the nested-loop plan: ``axes[0]`` outermost ... ``measure()`` innermost.

    Parameters
    ----------
    axes : list of ScanAxis
        Outermost first.  Degenerate axes (no values) are skipped but still execute their
        single move-to-None pass if they have a device (so a recipe can pass an "off" axis).
    measure : callable() -> plan
        The innermost measurement (typically ``trigger_and_read``).

    Returns
    -------
    A plan (generator); ``yield from`` it.
    """
    # filter to axes that actually do something, but keep order
    active = [a for a in axes if not a.is_degenerate()]

    def _build(i, reverse=False):
        if i >= len(active):
            yield from measure()
            return
        axis = active[i]
        vals = list(axis.values)
        if reverse and axis.reverse_alternate:
            vals = vals[::-1]
        for j, v in enumerate(vals):
            yield from axis.move_to(v)
            # alternate inner direction for snaking if the inner axis asked for it
            yield from _build(i + 1, reverse=(j % 2 == 1))

    return (yield from _build(0))


# ---------------------------------------------------------------------------
# Ordering guardrail
# ---------------------------------------------------------------------------
def _check_axis_order(axes):
    """Warn (do not raise) if a slow axis is nested inside a faster one (moved too often).

    Best practice: slow / in-vacuum axes (waxs.arc, stage.phi, temperature) outermost so they move
    the fewest times.  This computes how many times each axis moves given the nesting and warns
    if a slower axis moves more often than a faster one inside it.
    """
    active = [a for a in axes if not a.is_degenerate()]
    # moves(i) = product of lengths of all axes at or outside i (i.e. how often axis i moves)
    for i, a in enumerate(active):
        moves_i = 1
        for outer in active[:i + 1]:
            moves_i *= max(1, len(outer.values))
        for k in range(i + 1, len(active)):
            inner = active[k]
            if inner.speed < a.speed:
                continue  # a faster axis inside a slower one is fine
            if inner.speed > a.speed:
                moves_inner = moves_i * 1
                for mid in active[i + 1:k + 1]:
                    moves_inner *= max(1, len(mid.values))
                warnings.warn(
                    "Scan-axis order: slow axis '{}' (speed {}) is nested inside faster "
                    "axis '{}' (speed {}); it will move {} times. Consider putting slower "
                    "axes outermost to minimize travel of in-vacuum hardware."
                    .format(inner.name, inner.speed, a.name, a.speed, moves_inner),
                    stacklevel=3)


# ---------------------------------------------------------------------------
# Filename-token validation (catch the post-run KeyError at build time)
# ---------------------------------------------------------------------------
import re as _re

_TOKEN_FIELD_RE = _re.compile(r"\{([^}:!\[]+)")


def _template_fields(template):
    """The ``{field}`` names in a ``sample_name`` template (strip any ``:fmt``/``!conv``)."""
    return [m.split(".")[0].split("[")[0] for m in _TOKEN_FIELD_RE.findall(template or "")]


_POSITION_TOKEN_SPECS = (
    ("piezo_x", "x", "piezo", "x"),
    ("piezo_y", "y", "piezo", "y"),
    ("piezo_z", "z", "piezo", "z"),
    ("piezo_th", "th", "piezo", "th"),
    ("stage_x", "sx", "stage", "x"),
    ("stage_y", "sy", "stage", "y"),
    ("stage_z", "sz", "stage", "z"),
    ("stage_theta", "stheta", "stage", "theta"),
    ("stage_chi", "schi", "stage", "chi"),
    ("stage_phi", "sphi", "stage", "phi"),
)


def _position_token_reads(fields):
    """Live position readables needed for filename tokens such as ``{piezo_x}``."""
    wanted = set(fields or [])
    out = []
    for field, _label, root_name, attr in _POSITION_TOKEN_SPECS:
        if field not in wanted:
            continue
        root = globals().get(root_name)
        dev = getattr(root, attr, None) if root is not None else None
        if dev is not None:
            out.append(dev)
    return out


def _sample_position_name_tokens(sample):
    """Filename tokens for the coordinates a sample's runnable position actually sets."""
    pos = sample.runnable_position() if hasattr(sample, "runnable_position") else None
    tokens = []
    for field, label, _root_name, _attr in _POSITION_TOKEN_SPECS:
        value = getattr(pos, field, None) if pos is not None else None
        if value is None:
            value = getattr(sample, field, None)
        if value is not None:
            tokens.append("{}{{{}}}".format(label, field))
    return tokens


def _describe_keys(dev):
    """Best-effort recorded data keys for a readable: its ``describe()`` keys, or () if not
    describeable at build time (no live device / GUI-side construction)."""
    try:
        return tuple(dev.describe().keys())
    except Exception:
        return ()


def validate_name_tokens(sample_name, *, dets, reads, axes):
    """Raise ``ValueError`` if a ``{token}`` in ``sample_name`` cannot resolve to a recorded data key.

    The downstream file writer / symlink workflow fills ``{field}`` tokens from the run's recorded
    event keys (``<device>_<attr>``); a token with no matching key raises ``KeyError(field)`` AFTER
    the scan has already taken data.  This turns that into an immediate, actionable build-time error.

    A token field is accepted if ANY of:
      * it is a recognized scan-naming token (:data:`COMMON_TOKENS`, e.g. ``{energy_energy}``,
        ``{waxs_arc}``, ``{pin_diode_current2_mean_value}``) -- these are injected by the beamline
        naming preprocessor even when not in ``reads``;
      * it equals an axis ``record`` Signal name (exact), e.g. ``incident_angle``, ``x``;
      * it is (or prefixes to) a recorded key of a ``dets``/``reads`` device -- matched against the
        device ``describe()`` keys when available, else against ``<device.name>`` as a prefix.

    To avoid false positives off-beamline (where devices may not be describeable, and where sim keys
    differ from real ones), a token is flagged **only when** describe() info is available for the
    readables and the token matches none of the above.  If nothing is describeable, validation is
    skipped (can't prove absence).

    Scope note: the *read-once collision* (a token device injected by the beamline naming
    preprocessor AND also read by the plan -> ``Data keys ... collide``) and the *superset* rule
    (a custom token-bearing name must still record everything the default naming would have) depend
    on the **profile-side** scan-naming preprocessor, which this package cannot observe.  Those are
    enforced beamline-side; here we cover the high-value case that caused real post-run failures --
    a token with no recorded key at all (the ``{x}`` vs ``{piezo_x}`` trap).
    """
    fields = _template_fields(sample_name)
    if not fields:
        return

    common = {k.strip("{}") for k in COMMON_TOKENS}
    record_names = {a.record.name for a in axes if getattr(a, "record", None) is not None}

    readables = list(dets or []) + list(reads or [])
    described_keys = set()
    undescribed_name_prefixes = set()   # devices we could NOT describe -> fall back to name prefix
    any_describe = False
    for d in readables:
        keys = _describe_keys(d)
        if keys:
            any_describe = True
            described_keys.update(keys)
        else:
            nm = getattr(d, "name", None)
            if nm:
                undescribed_name_prefixes.add(nm)

    if not any_describe and not record_names:
        return  # cannot prove anything (no live devices) -> don't raise

    def _ok(field):
        if field in common or field in record_names:
            return True
        if field in described_keys:
            return True
        # A token may extend a real describe key with a stats suffix (e.g. the describe key is
        # 'pin_diode_current2' but the token is 'pin_diode_current2_mean_value').  Allow that.
        for k in described_keys:
            if field.startswith(k + "_"):
                return True
        # For devices we could NOT describe (off-beamline / GUI-side), we can't know their exact
        # keys -- accept anything that looks like '<device.name>_...'.  (When a device DID describe,
        # its exact keys are authoritative, so a typo like {piezo_QQ} on a read 'piezo' is rejected.)
        for nm in undescribed_name_prefixes:
            if field == nm or field.startswith(nm + "_"):
                return True
        return False

    missing = [f for f in fields if not _ok(f)]
    if missing:
        known = sorted(common | record_names | described_keys)
        raise ValueError(
            "filename token(s) {} in sample_name {!r} have no recorded data key. "
            "Tokens must equal a recorded key (e.g. {{piezo_x}} not {{x}}, {{energy_energy}} not "
            "{{energy}}), an axis record-Signal name, or a known/recorded key. "
            "Either fix the token, add the device to reads/dets, or record a Signal(name=...) for "
            "it. Recorded keys available: {}. See skills/naming-and-filename-tokens.md.".format(
                missing, sample_name, known))


# ---------------------------------------------------------------------------
# The experiment builder
# ---------------------------------------------------------------------------
def acquire(name, dets, axes, *, reads=None, setup=None, align=None, geometry=None,
            scan_name="acquire", md=None, baseline=None, name_tokens=None, check_order=True,
            sample=None, user_hints=None, validate_tokens=True):
    """Compose ONE run for ONE sample: (align) + setup + nested ``axes`` + ``trigger_and_read``.

    This is the compositional heart.  You provide the *beam/q config* (``dets`` + ``reads``),
    the *apparatus/geometry* (``setup`` plan, run once after the run opens), and the
    *sampling/scanning* (``axes``, nested in the given order).  Everything an axis records is
    available as a ``{field}`` filename token.

    Parameters
    ----------
    name : str
        Human sample label; the start of the templated filename.
    dets : list
        Detectors (the q-range / which-detector choice).  Staged for the run.
    axes : list of ScanAxis
        The scan dimensions, OUTERMOST FIRST.  Slow/in-vacuum axes (arc, stage.phi, temperature)
        should come first; a guardrail warns otherwise (see ``check_order``).
    reads : list, optional
        Extra readables recorded at every event (e.g. ``[energy, waxs, xbpm2, xbpm3]``).  The
        axes' own ``record`` Signals and ``reads`` are merged in automatically.
    setup : callable() -> plan, optional
        **In-run** apparatus/geometry setup run ONCE just after ``open_run`` *inside the staged
        measurement run* (e.g. ensure attenuators in, set ``pin_diode.averaging_time``, a
        :func:`manual_step` whose typed value you want recorded in this run's baseline).  Its
        moves/reads are recorded in the run.  **Do NOT run an alignment routine here** -- use
        ``align`` instead (see below): alignment plans open their OWN runs and stage the same
        detectors, which collides with this run's staging (``RedundantStaging``).
    align : callable() -> plan, optional
        **Pre-run** plan executed ONCE *before* the measurement run opens and before ``dets`` are
        staged.  This is the place for an **alignment routine** (e.g. ``alignement_gisaxs_hex`` /
        a GISAXS height+theta scan) or any self-contained plan that opens its own run(s) and
        stages its own detectors -- running it here avoids the ``RedundantStaging`` that occurs
        if it is run inside the measurement run via ``setup``.  Because it runs outside the run,
        its results are not recorded in THIS run's stream; capture an alignment offset you care
        about as a ``baseline`` Signal (see ``technique_B``'s ``aligned_th0`` for the pattern).
    geometry : str, optional
        ``"reflection"`` / ``"transmission"`` (goes in md).
    scan_name : str
        Names the run (e.g. ``"giwaxs_tempramp"``).
    md : dict, optional
        Caller intent merged into the run md (``project_name`` etc.).
    baseline : list, optional
        Constants recorded once (SDD, setpoints, alignment offsets ...).
    name_tokens : sequence of str, optional
        ``{field}`` tokens appended to the filename.  If None, auto-build from each axis's
        recorded Signal (``ai{incident_angle}`` etc.) so the filename reflects the scan.
    check_order : bool
        If True (default), warn when slow axes are nested inside faster ones.
    sample : _samples.Sample, optional
        If given, ``name`` defaults to ``sample.name`` and its ``md`` is merged.
    user_hints : dict, optional
        A structured bundle of the values the user considers important, recorded in the run
        metadata under ``md['user_hints']`` (NOT the reserved bluesky ``hints`` key).  This is
        the computational-convenience companion to the filename: the same intent that the
        ``{token}`` filename encodes (and that the axes record in the stream) is *also* kept as
        a queryable dict, so downstream analysis need not parse names.  Constant per run.

    Returns
    -------
    The plan; ``yield from`` it (or ``RE(acquire(...))``).
    """
    if sample is not None:
        name = name or sample.name
        md = merge_md(md, sample.md)

    if check_order:
        _check_axis_order(axes)

    reads = list(reads) if reads else []
    # auto-collect the axes' recorded Signals + extra reads so every event captures them
    axis_reads = []
    for a in axes:
        if a.record is not None and a.record not in reads and a.record not in axis_reads:
            axis_reads.append(a.record)
        for r in a.reads:
            if r not in reads and r not in axis_reads:
                axis_reads.append(r)
    all_reads = reads + axis_reads

    # auto filename tokens from the recording axes (unless caller overrides)
    if name_tokens is None:
        toks = []
        for a in axes:
            if a.record is not None:
                toks.append(a.name + a.token("{}"))   # e.g. "energy{energy}" -> "energy{energy}"
        name_tokens = toks
    sample_name = fname(name, *name_tokens)

    for r in _position_token_reads(_template_fields(sample_name)):
        if r not in reads and r not in axis_reads:
            reads.append(r)
    all_reads = reads + axis_reads

    # Fail fast on a filename token that can't resolve to a recorded key (else it surfaces as a
    # post-run KeyError in the file-naming/symlink step, after data is already taken).
    if validate_tokens:
        validate_name_tokens(sample_name, dets=dets, reads=all_reads, axes=axes)

    def _measure():
        # De-dup so a detector and one of its own sub-devices (e.g. pil900KW + waxs, where
        # waxs IS pil900KW.motors) don't both report the same keys in one Event (which raises
        # "Data keys ... collide").  The ancestor (the detector) is kept; the descendant's
        # {token} still resolves from it.
        yield from bps.trigger_and_read(dedup_readables(list(dets) + all_reads))

    def _body():
        if setup is not None:
            yield from setup()
        yield from nest_axes(axes, _measure)

    # Pre-run alignment / self-contained plans run OUTSIDE the measurement run, before staging,
    # so their own open_run/stage do not collide with this run's (RedundantStaging).
    if align is not None:
        yield from align()

    run_md = merge_md(md, {"user_hints": dict(user_hints)} if user_hints else {})
    return (yield from one_sample_run(
        _body, dets, sample_name=sample_name, scan_name=scan_name,
        geometry=geometry, md=run_md, baseline=baseline, reads=all_reads))


def acquire_bar(samples, dets, axes_for, *, reads=None, setup_for=None, align_for=None,
                geometry=None, scan_name="acquire", md=None, baseline_for=None,
                name_tokens=None, check_order=True, goto=None):
    """Run :func:`acquire` for each sample on a bar (ONE run per sample).

    ``axes_for(sample) -> list[ScanAxis]`` and (optionally) ``setup_for(sample) -> plan`` /
    ``align_for(sample) -> plan`` / ``baseline_for(sample) -> list`` are callables so per-sample
    coordinates (e.g. an aligned incidence zero, per-sample energy lists) can vary.  Each sample
    is coarse-positioned via ``goto`` (default :func:`_core.goto_sample`) before its run.

    ``setup_for`` is the **in-run** hook (recorded; attenuators-in, manual steps).
    ``align_for`` is the **pre-run** hook (alignment routines / self-contained plans that open
    their own runs + stage their own detectors) -- run it here, NOT in ``setup_for``, to avoid
    ``RedundantStaging``.  See :func:`acquire` for the ``setup`` vs ``align`` distinction.

    For slow-axis economy across the WHOLE bar (move ``waxs.arc`` once for all samples), use
    :func:`_core.multi_sample_run` instead -- that is a different run topology (N runs open at
    once) and is offered separately.
    """
    _goto = goto if goto is not None else (lambda s: goto_sample(s))
    for s in samples:
        yield from _goto(s)
        axes = axes_for(s)
        setup = (lambda s=s: setup_for(s)) if setup_for is not None else None
        align = (lambda s=s: align_for(s)) if align_for is not None else None
        baseline = baseline_for(s) if baseline_for is not None else None
        sample_name_tokens = name_tokens
        if sample_name_tokens is None and not axes:
            sample_name_tokens = _sample_position_name_tokens(s)
        yield from acquire(
            s.name, dets, axes, reads=reads, setup=setup, align=align, geometry=geometry,
            scan_name=scan_name, md=merge_md(md, s.md), baseline=baseline,
            name_tokens=sample_name_tokens, check_order=check_order)


def polygon_region_run(sample, region_name, dets, *, reads=None, x_motor=None, y_motor=None,
                       setup=None, align=None, geometry=None, scan_name="polygon_region",
                       md=None, baseline=None, name_tokens=None, validate_tokens=True,
                       t=None, goto=True, allow_truncated=False):
    """Acquire a correlated point-list scan over a named polygon region on one sample.

    The polygon is read from ``sample.md['scan_regions']`` (or future ``sample.regions``) via
    :func:`smi_plans._regions.sample_region`. Region vertices are sample-relative offsets; the scan
    center is the sample's current runnable ``Position``. Each event records relative offset Signals
    named ``x`` and ``y`` so filename tokens ``{x}`` / ``{y}`` are valid, while ``reads`` should
    include the parent motor device (usually ``piezo``) to record absolute positions too.

    This helper deliberately does not return independent ``ScanAxis`` objects: polygon points are
    correlated ``(x, y)`` pairs, not a rectangular product.
    """
    region = sample_region(sample, region_name)
    axes = tuple(_region_value(region, "axes", ("piezo_x", "piezo_y")))
    if len(axes) != 2:
        raise ValueError("polygon region {!r} must define exactly two axes".format(region_name))
    if axes != ("piezo_x", "piezo_y") and (x_motor is None or y_motor is None):
        raise ValueError(
            "region axes {} need explicit x_motor/y_motor; default motors support "
            "('piezo_x', 'piezo_y')".format(axes))

    position = sample.runnable_position()
    center_x = getattr(position, axes[0], None)
    center_y = getattr(position, axes[1], None)
    if center_x is None or center_y is None:
        raise ValueError("sample {!r} has no runnable center for region axes {}".format(
            getattr(sample, "name", None), axes))

    offsets, truncated = region_grid_offsets(region)
    if truncated and not allow_truncated:
        raise ValueError("polygon region {!r} generated too many points; increase max_points or "
                         "coarsen grid spacing".format(region_name))
    if not offsets:
        raise ValueError("polygon region {!r} contains no grid points".format(region_name))

    if x_motor is None:
        x_motor = piezo.x                                           # noqa: F821
    if y_motor is None:
        y_motor = piezo.y                                           # noqa: F821

    x_sig = Signal(name="x", value=0.0)                             # noqa: F821
    y_sig = Signal(name="y", value=0.0)                             # noqa: F821
    region_sig = Signal(name="region_name", value=str(region_name))  # noqa: F821
    reads = list(reads or [])
    point_reads = reads + [x_sig, y_sig, region_sig]

    if name_tokens is None:
        name_tokens = ("region{region_name}", "x{x}", "y{y}")
    sample_name = fname(sample.name, *name_tokens)
    if validate_tokens:
        dummy_axes = [
            ScanAxis("x", [], record=x_sig),
            ScanAxis("y", [], record=y_sig),
            ScanAxis("region", [], record=region_sig),
        ]
        validate_name_tokens(sample_name, dets=dets, reads=point_reads, axes=dummy_axes)

    run_md = merge_md(
        md,
        sample.base_md() if hasattr(sample, "base_md") else getattr(sample, "md", {}),
        {
            "scan_region_name": region_name,
            "scan_region_kind": _region_value(region, "kind", "polygon"),
            "scan_region_axes": list(axes),
            "scan_region_points": len(offsets),
        },
        _region_value(region, "md", {}),
    )

    def _body():
        if setup is not None:
            yield from setup()
        yield from bps.mv(region_sig, str(region_name))
        for dx, dy in offsets:
            yield from bps.mv(
                x_motor, float(center_x) + float(dx),
                y_motor, float(center_y) + float(dy),
                x_sig, float(dx),
                y_sig, float(dy),
            )
            yield from bps.trigger_and_read(dedup_readables(list(dets) + point_reads))

    if align is not None:
        yield from align()
    if goto:
        yield from goto_sample(sample, skip={x_motor, y_motor})
    if t is not None:
        yield from det_exposure_time(t, t)                          # noqa: F821
    return (yield from one_sample_run(
        _body, dets, sample_name=sample_name, scan_name=scan_name,
        geometry=geometry, md=run_md, baseline=baseline, reads=point_reads))


def polygon_region_bar(samples, region_name, dets, *, reads=None, setup_for=None, align_for=None,
                       geometry=None, scan_name="polygon_region", md=None, baseline_for=None,
                       name_tokens=None, t=None, **kwargs):
    """Run :func:`polygon_region_run` for each sample in ``samples``."""
    for sample in samples:
        setup = (lambda sample=sample: setup_for(sample)) if setup_for is not None else None
        align = (lambda sample=sample: align_for(sample)) if align_for is not None else None
        baseline = baseline_for(sample) if baseline_for is not None else None
        yield from polygon_region_run(
            sample, region_name, dets, reads=reads, setup=setup, align=align,
            geometry=geometry, scan_name=scan_name, md=merge_md(md, getattr(sample, "md", {})),
            baseline=baseline, name_tokens=name_tokens, t=t, **kwargs)


def _region_value(region, key, default=None):
    if isinstance(region, dict):
        return region.get(key, default)
    return getattr(region, key, default)


# ===========================================================================
# Ready-made axis constructors (the common concerns)
# ===========================================================================
#: A small "already there" guard for energy moves (eV): skip a move whose target is within this of
#: the current energy.  Avoids a redundant set + the device's settle when a step is a no-op.
ENERGY_TOL_eV = 0.05


def move_energy_fb(target, *, settle=2.0):
    """Plan: move the DCM ``energy`` to ``target`` (eV).

    A plain, settle-guarded ``bps.mv(energy, target)``.  The beamline ``energy`` pseudo-positioner
    (with the default ``energy_move_preprocessor`` installed) manages **everything** that used to be
    hand-coded here: it moves Bragg + DCM gap + IVU gap together, keeps the undulator gap on the flux
    peak at every step, handles the harmonic, sub-steps large jumps, and manages the DCM pitch/roll
    feedback itself.  This was validated on the live beamline (100+ fine steps; even a ~1516 um
    IVU-gap jump at a harmonic crossover completes ``success=True``), so the previous
    feedback-off / double-set / manual sub-stepping machinery is unnecessary and was the suspected
    cause of energy-move failures -- it has been removed.

    .. note::
       Do NOT reintroduce manual feedback toggling, ``max_step`` sub-stepping, double-setting, IVU-gap
       freezing/accumulation, or ``abs_set(energy, wait=False)`` + ``set_finished`` here.  Those were
       all tried in the field and discarded; the device + its preprocessor are the correct owners of
       that behavior.  A plan-level beam-loss *re-seek* (re-issuing the move when I0 dips) is a
       different, still-valid concern -- see :func:`energy_axis`'s ``flux_signal``/``flux_threshold``.

    Parameters
    ----------
    target : float
        Photon energy to move to (eV).
    settle : float
        Dwell (s) after the move (default 2), e.g. to let downstream optics/flux stabilize before
        measuring.  Set 0 to skip.
    """
    target = float(target)
    current = float((yield from bps.rd(energy)))                  # noqa: F821 (current energy, eV)
    if abs(target - current) < ENERGY_TOL_eV:
        return                                                    # already there; nothing to do
    yield from bps.mv(energy, target)                             # noqa: F821 (device owns gap/feedback)
    if settle:
        yield from bps.sleep(settle)


def energy_axis(energies, *, settle=2.0, reverse_alternate=False, flux_signal=None,
                flux_threshold=None, max_reseek=3, record_name="energy_set"):
    """A DCM energy scan axis.  Records energy via a Signal so ``{energy_set}`` is a token.

    The DCM ``energy`` device itself is also typically in ``reads`` (giving ``{energy_energy}``);
    this axis additionally records the *commanded* setpoint and can re-seek the beam.

    Each grid point moves energy via :func:`move_energy_fb` -- a plain ``bps.mv(energy, E)``.  The
    ``energy`` device manages the DCM feedback, IVU gap (flux peak), and harmonic itself, so no
    feedback toggling / double-setting / sub-stepping happens here (that machinery was removed; see
    :func:`move_energy_fb`).

    Parameters
    ----------
    energies : sequence
        Energies (eV), in visiting order (e.g. up, or up+down -- just concatenate).
    settle : float
        Dwell (s) after each energy move (default 2).
    reverse_alternate : bool
        Snake this axis on alternate passes of the outer axes.
    flux_signal, flux_threshold : optional
        If both given, re-seek (re-command energy + wait) when I0 drops below threshold -- a
        plan-level beam-loss guard (independent of the device's own management).
    max_reseek : int
        Max re-seek attempts per point when ``flux_signal``/``flux_threshold`` are set.
    record_name : str
        Name of the recorded commanded-setpoint Signal (``{energy_set}``).
    """
    sig = Signal(name=record_name, value=0.0)                     # noqa: F821

    def _move(value):
        yield from move_energy_fb(value, settle=settle)

    def _per_point():
        if flux_signal is not None and flux_threshold is not None:
            tries = 0
            # read I0 via a message (bps.rd), decide, re-seek -- all message-based
            flux = yield from bps.rd(flux_signal)
            while flux < flux_threshold and tries < max_reseek:
                target = yield from bps.rd(energy)               # noqa: F821 (current energy)
                yield from move_energy_fb(target, settle=settle)  # re-seek (re-command + settle)
                flux = yield from bps.rd(flux_signal)
                tries += 1
        else:
            yield from bps.null()

    return ScanAxis("energy", energies, move=_move,              # plain device move (gap/feedback owned)
                    record=sig, settle=0.0,                      # move_energy_fb already dwells
                    per_point=_per_point,
                    reads=[energy],                              # noqa: F821 (gives {energy_energy})
                    speed=SPEED_MEDIUM, reverse_alternate=reverse_alternate)


def temperature_axis(heater, setpoints, *, tol=1.0, poll=10.0, timeout=7200.0, soak=60.0,
                     first_soak=None, reverse_alternate=False):
    """A temperature ramp axis using a ``Heater`` (see ``technique_C_temperature``).

    ``heater`` must provide ``set_plan(setpoint)`` (a plan), ``units``, and a recordable
    ``readback`` ophyd Signal (the C-technique ``Heater`` abstraction).  Each point sets the
    temperature and equilibrates (reading the readback via ``bps.rd`` -- message-based) before
    descending inward.  Temperature is SLOW -> put this axis outermost.

    The heater's read-back Signal is recorded, so the *measured* temperature lands in the
    stream at each event.  (If your heater's live read-back is only available through a plain
    method, wrap it with ``smi_plans._devices.linkam_temperature_signal`` so it is a proper
    ``bps.rd``-able Signal -- see ``docs/DEVICE_DEBT.md``.)
    """
    def _move(setpoint):
        import time
        first = (setpoints and setpoint == setpoints[0])
        use_soak = (first_soak if (first and first_soak is not None) else soak)
        yield from heater.set_plan(setpoint)
        start = time.time()
        # read the live temperature via a message (bps.rd) for the convergence test
        t = yield from bps.rd(heater.readback)
        while abs(t - setpoint) > tol:
            yield from bps.sleep(poll)
            t = yield from bps.rd(heater.readback)
            if time.time() - start > timeout:
                break
        if use_soak:
            yield from bps.sleep(use_soak)

    # the recordable readback is the heater's own Signal; add it to reads
    return ScanAxis("temperature", setpoints, move=_move,
                    record=None, reads=[heater.readback],
                    speed=SPEED_SLOW, reverse_alternate=reverse_alternate)


def incidence_axis(th_axis, th0, incident_angles, *, settle=0.0, record_name="incident_angle",
                   zero_record_name="incidence_zero"):
    """A grazing-incidence-angle axis: visit ``th0 + ai`` for each ``ai``.

    The axis records the **real incident angle** ``ai`` (a *relative*, physical quantity) on a
    pseudo-axis :class:`Signal` (default name ``incident_angle``), so ``{incident_angle}`` is a
    filename token and the true angle lands in the **primary stream** -- regardless of the
    absolute motor readback.  Medium speed.

    Anchoring (``th0``)
    -------------------
    * ``th0`` a number -> absolute anchor: visit ``th0 + ai`` (the classic behavior).
    * ``th0 is None`` -> **relative / aligned-zero mode**: the axis captures the **live**
      ``th_axis`` position *at the moment it first runs* (i.e. *after* any alignment that ran in
      the run's pre-run ``align`` hook left the motor at the aligned theta-zero) and treats THAT
      as incidence-angle 0, visiting ``captured_zero + ai``.  The motor readback need not read 0;
      the recorded ``incident_angle`` is the true relative angle ``ai``.  The captured zero is
      also recorded once on a ``incidence_zero`` Signal (add it to ``baseline`` if you want it
      persisted with the run).

    The relative mode is the right choice when an alignment routine leaves ``th_axis`` at the
    grazing zero but its absolute readback is some nonzero aligned value (the SMI
    ``alignment_gisaxs`` case): build the axis with ``th0=None`` and it anchors to wherever
    alignment left theta -- you do NOT pre-read ``piezo.th.position`` at axis-build time (which,
    in ``acquire_bar``, happens *before* the align hook runs).

    Parameters
    ----------
    th_axis : positioner
        The theta axis to move (e.g. ``piezo.th``).
    th0 : float or None
        Absolute anchor, or ``None`` for relative/aligned-zero mode (capture live at first move).
    incident_angles : sequence
        The incident angles (deg), relative to the anchor.
    settle : float
        Sleep after each move.
    record_name : str
        Name of the recorded real-incident-angle pseudo-axis Signal (``{incident_angle}``).
    zero_record_name : str
        Name of the Signal that captures the (absolute) anchor zero (relative mode only).
    """
    sig = Signal(name=record_name, value=0.0)                    # noqa: F821
    zero_sig = Signal(name=zero_record_name, value=0.0)          # noqa: F821
    state = {"zero": th0}     # if th0 is None, filled lazily on the first move (post-alignment)

    def _move(ai):
        # Relative/aligned-zero mode: on the first point, read the LIVE theta via a message
        # (bps.rd -> the readback) and adopt it as the zero.  This runs after the run's `align`
        # hook, so it captures the aligned theta -- not the nominal value from axis-build time.
        if state["zero"] is None:
            state["zero"] = yield from bps.rd(th_axis)
            yield from bps.mv(zero_sig, state["zero"])           # record the captured zero once
        yield from bps.mv(th_axis, state["zero"] + ai)
        # the recorded value (ai, the real incident angle) is set by ScanAxis.move_to via
        # bps.mv(record, ai) -- so {incident_angle} carries the true relative angle.
        if settle:
            yield from bps.sleep(settle)

    return ScanAxis("incidence", incident_angles, move=_move, record=sig,
                    speed=SPEED_MEDIUM)


def motor_axis(name, device, values, *, settle=0.0, record=True, speed=SPEED_FAST,
               reverse_alternate=False):
    """A generic single-motor axis (e.g. ``waxs`` arc, ``stage.phi``, a piezo).

    If ``record`` is True, the device is added to ``reads`` so its position is in the stream
    (``{<device.name>_<...>}``).  Set ``speed=SPEED_SLOW`` for ``waxs``/``stage.phi`` so the
    guardrail keeps them outermost.
    """
    return ScanAxis(name, values, device=device, settle=settle,
                    reads=([device] if record else []),
                    speed=speed, reverse_alternate=reverse_alternate)


def _grid_axis(label, motor, positions, *, center=None, record_relative=True,
               record=True, speed=SPEED_FAST, reverse_alternate=False, role=None):
    """One spatial-grid dimension.

    Default (``record_relative`` + a ``center``): the axis **values are the relative offsets**
    ``position - center``; ``_move`` drives the motor to the absolute ``center + offset`` (so the
    motor still records its absolute ``<motor>_<attr>`` key, e.g. ``piezo_x``) AND a
    ``Signal(name=label)`` records the *relative* offset -- so the filename token ``{x}``/``{y}``
    resolves to a meaningful relative value (the ``incidence_axis``/``{incident_angle}`` pattern).

    Without a ``center`` (or ``record_relative=False``): falls back to the plain absolute-position
    axis whose recorded key is ``<motor>_<attr>`` (token ``{piezo_x}``) -- backward compatible.
    """
    if center is not None and record_relative:
        offsets = [float(p) - float(center) for p in positions]
        sig = Signal(name=label, value=0.0)                       # noqa: F821 (token {x}/{y})
        c = float(center)

        def _move(off, _c=c):
            yield from bps.mv(motor, _c + off)                    # absolute move -> records <motor>_*

        return ScanAxis(label, offsets, move=_move, record=sig,
                        reads=([motor] if record else []),        # also record absolute <motor>_*
                        speed=speed, reverse_alternate=reverse_alternate)

    # Absolute mode: token is {<motor>_<attr>} (e.g. {piezo_x}); no relative Signal.
    return motor_axis(label, motor, positions, record=record, speed=speed,
                      reverse_alternate=reverse_alternate)


def spatial_grid_axes(*, x_motor=None, x=None, y_motor=None, y=None, center=None,
                      record_relative=True, snake=True, record=True, dose=False, role=None):
    """Build 1-D or 2-D spatial-sampling axes (a single spot, a line, or a grid).

    Returns a LIST of axes (0, 1, or 2) you splice into your axis stack -- usually innermost
    (fast piezo).  ``x``/``y`` are the absolute positions to visit; pass just one for a line,
    both for a grid, neither for a single spot.

    Filename tokens
    ---------------
    * **With a ``center``** (recommended; ``record_relative`` default True): each axis records a
      ``Signal(name="x"/"y")`` holding the **relative offset** from ``center``, so ``{x}``/``{y}``
      are the canonical, meaningful filename tokens.  The motor's absolute position is still
      recorded too (``{piezo_x}``/``{piezo_y}``) for provenance.
    * **Without a ``center``**: the axes record only the absolute motor position, so the token is
      ``{piezo_x}``/``{piezo_y}`` (NOT ``{x}``/``{y}`` -- ``{x}`` would have no recorded key and the
      file-naming step would ``KeyError``).  This is the backward-compatible behavior.

    Parameters
    ----------
    x_motor, y_motor : positioners (e.g. piezo.x, piezo.y)
    x, y : sequences of absolute positions
    center : float or (cx, cy), optional
        Grid center.  If given, ``{x}``/``{y}`` record the relative offset from it.  A scalar
        applies to both axes; a 2-tuple is ``(cx, cy)``.
    record_relative : bool
        When a ``center`` is given, record the relative-offset Signal (default True).  False forces
        the absolute-key (``{piezo_x}``) behavior even with a center.
    snake : bool
        Snake the inner (y) axis to avoid backtracking.
    record : bool
        Record the (absolute) motor positions in the stream.
    dose : bool
        Mark these as the dose-walk axes (purely informational here; the fresh-spot behavior
        is better applied via the ``_preprocessors.fresh_spot_wrapper`` at the run level).
    role : str, optional
        Advisory tag (e.g. ``"spatial"``) for GUI/spec round-tripping.
    """
    if isinstance(center, (tuple, list)):
        cx, cy = float(center[0]), float(center[1])
    elif center is not None:
        cx = cy = float(center)
    else:
        cx = cy = None

    axes = []
    if x_motor is not None and x is not None:
        axes.append(_grid_axis("x", x_motor, x, center=cx, record_relative=record_relative,
                               record=record, speed=SPEED_FAST, role=role))
    if y_motor is not None and y is not None:
        axes.append(_grid_axis("y", y_motor, y, center=cy, record_relative=record_relative,
                               record=record, speed=SPEED_FAST, reverse_alternate=snake, role=role))
    return axes


def potential_axis(set_potential, potentials, *, equilibration=5.0, readback=None,
                   record_name="potential_v"):
    """An applied-potential (electrochemistry) axis.

    ``set_potential(V) -> plan`` is your rig-specific potentiostat command.  Records the
    commanded ``V`` (``{potential_v}``); add ``readback`` to also record measured cell V/I.
    """
    sig = Signal(name=record_name, value=0.0)                    # noqa: F821

    def _move(v):
        yield from set_potential(v)
        # commanded V (the record) is set by ScanAxis.move_to via bps.mv(record, v)
        if equilibration:
            yield from bps.sleep(equilibration)

    reads = [readback] if readback is not None else []
    return ScanAxis("potential", potentials, move=_move, record=sig, reads=reads,
                    speed=SPEED_MEDIUM)


def rh_axis(set_rh, rh_setpoints, *, record_name="rh", live_rh=None):
    """A relative-humidity (SVA) axis.

    ``set_rh(target) -> plan`` ramps the MFCs and equilibrates.  Records the *commanded* RH
    (set message-based by the axis).  Pass ``live_rh`` -- a ``bps.rd``-able Signal of the
    measured humidity (e.g. ``smi_plans._devices.humidity_signal(readHumidity)``) -- to also
    record the measured RH at each event.
    """
    sig = Signal(name=record_name, value=0.0)                    # noqa: F821

    def _move(target):
        yield from set_rh(target)
        # commanded RH (the record) is set by ScanAxis.move_to via bps.mv(record, target)

    reads = [live_rh] if live_rh is not None else []
    return ScanAxis("rh", rh_setpoints, move=_move, record=sig, reads=reads,
                    speed=SPEED_SLOW)


def time_axis(n_frames, *, period=0.0, record_name="frame", elapsed_signal=None):
    """A time-series axis: ``n_frames`` points, ``period`` seconds apart.

    Records the frame index (``{frame}``, set message-based by the axis).  Pass
    ``elapsed_signal`` (a Signal) to also record wall-clock elapsed seconds per event (set via
    ``bps.mv``).  The ``period`` sleep is applied as the axis settle.
    """
    sig = Signal(name=record_name, value=0)                      # noqa: F821
    t0 = {}

    def _move(i):
        # The frame index (record) is set by ScanAxis.move_to via bps.mv(record, i).
        # We only need to stamp elapsed time -- also message-based (bps.mv).
        import time
        if i == 0:
            t0["t"] = time.monotonic()
        if elapsed_signal is not None:
            yield from bps.mv(elapsed_signal, time.monotonic() - t0.get("t", time.monotonic()))

    reads = [elapsed_signal] if elapsed_signal is not None else []
    return ScanAxis("time", list(range(int(n_frames))), move=_move, record=sig,
                    settle=period, reads=reads, speed=SPEED_FAST)


# ===========================================================================
# Manual / interactive concern (prompt the user; capture what they tell us)
# ===========================================================================
# Real experiments often have steps the beamline cannot automate: "swap the sample bar and
# type the new thickness", "I set the Linkam to 35 C by hand -- confirm", "wait until I start
# the syringe pump".  These must be (a) composable like any other layer and (b) honor Tenet 2:
# a value the user types becomes a RECORDED Signal, not a filename string or lost prose.
#
# We use ``bps.input_plan(prompt)`` -- the RunEngine-driven prompt (NOT a raw ``input()``), so
# pause/resume and the document model still work.

def _coerce(value, cast):
    # ``cast=None`` means "use the default coercion" (float).  We deliberately default the
    # public ``cast`` parameters to ``None`` rather than ``float`` so the plans stay
    # introspectable by bluesky-queueserver, which rejects a parameter whose default is a bare
    # type object (it cannot ``ast.literal_eval`` ``<class 'float'>``).  Passing an explicit
    # ``str``/``int``/``float`` still works for in-session/GUI callers.
    if cast is None:
        cast = float
    try:
        return cast(value)
    except Exception:
        return value


def pause_for_user(prompt="Press <enter> to continue"):
    """Plan: stop and wait for the user to acknowledge, recording nothing.

    For the pure "wait until I tell you to go" case (e.g. "start the pump, then <enter>").
    Use as a ``setup`` step or splice into a sequence.  Nothing is recorded.
    """
    yield from bps.input_plan(prompt + ": ")


def manual_value(prompt, signal, *, cast=None, echo=True):
    """Plan: prompt the user for a value and set it onto a recordable ``signal`` (via a message).

    The value the user types is set on ``signal`` (e.g. ``Signal(name="thickness_nm")``) with
    ``yield from bps.mv(signal, value)`` -- message-based, so it stays a proper plan -- and is
    recorded the next time ``signal`` is read.  Include ``signal`` in your ``reads`` or
    ``baseline`` so it lands in the data (and is then a ``{thickness_nm}`` filename token).

    Parameters
    ----------
    prompt : str
        What to ask, e.g. ``"Measured film thickness (nm)"``.
    signal : ophyd Signal
        Recordable destination for the entered value (named whatever you want; the value need
        not be a string).
    cast : callable or None
        Coerce the typed string (``None`` -> ``float``, the default).  Pass ``str`` to keep
        text, ``int``, etc.  If coercion fails, the raw string is stored.  (Defaults to ``None``
        rather than ``float`` so the plan stays introspectable by bluesky-queueserver, which
        rejects a bare-type default.)
    echo : bool
        Print the captured value.
    """
    raw = yield from bps.input_plan("{} = ".format(prompt))
    val = _coerce(raw, cast)
    yield from bps.mv(signal, val)            # set via a message (no .put())
    if echo:
        print("recorded {} = {!r}".format(signal.name, val))
    return val


def manual_step(prompt, *, signals=None, casts=None, confirm=True):
    """Plan: a one-shot manual checkpoint -- optionally collect several values into Signals.

    Use as a ``setup`` step (run once after the run opens, inside the run so values are
    recorded) or anywhere in a sequence.  Combines an acknowledgement and zero or more typed
    values.

    Parameters
    ----------
    prompt : str
        Instruction shown first, e.g. ``"Swap to the annealed bar"``.
    signals : list of ophyd Signal, optional
        One prompt per signal; each entered value is ``.put`` onto it (and should be in your
        ``baseline``/``reads`` to be recorded).  The prompt text uses each signal's name.
    casts : list, optional
        Per-signal coercion (default ``float`` for all).  Same length as ``signals``.
    confirm : bool
        If True, also require a final <enter> acknowledgement.

    Examples
    --------
    >>> thickness = Signal(name="thickness_nm", value=0.0)
    >>> temp_set  = Signal(name="temperature_set_manual", value=0.0)
    >>> # as a setup step, recording both values in the run baseline:
    >>> acquire("S1", dets, axes,
    ...         setup=lambda: manual_step("Load S1; read off the prep sheet",
    ...                                   signals=[thickness, temp_set]),
    ...         baseline=[thickness, temp_set])
    """
    print("\n*** MANUAL STEP: {} ***".format(prompt))
    signals = signals or []
    casts = casts or [float] * len(signals)
    out = []
    for sig, cast in zip(signals, casts):
        val = yield from manual_value(sig.name, sig, cast=cast)
        out.append(val)
    if confirm:
        yield from bps.input_plan("Done? <enter> to proceed: ")
    return out


def manual_axis(name, prompt, values=None, *, signal=None, cast=None,
                action_each=None, speed=SPEED_SLOW, record_name=None):
    """A scan axis driven by the USER at each point (manual swaps / hand-set conditions).

    Two modes:

    * **Enumerated** (``values`` given): iterate a known list (e.g. sample labels, hand-set
      temperatures the user dials in).  At each point the user is prompted to set up that point
      (``prompt`` shown with the value), then optionally to type a measured value onto
      ``signal``.  If ``signal`` is None but ``values`` are given, the value itself is recorded
      via an auto Signal (``{<name>}``), so e.g. the hand-set temperature is in the data.
    * **Open-ended** (``values`` None): repeat until the user signals stop.  Each iteration
      prompts for setup + a value; entering an empty/``stop`` value ends the axis.  Useful for
      "keep going as long as I keep loading samples".

    Parameters
    ----------
    name : str
        Axis label.
    prompt : str
        Instruction per point, e.g. ``"Set the sample to position"`` or ``"Dial the hot stage to"``.
    values : sequence or None
        The points (e.g. ``[35, 50, 65]`` for hand-set temperatures, or sample labels).  None
        = open-ended loop until the user stops.
    signal : ophyd Signal, optional
        Where to record a value the user types at each point.  If None and ``values`` given,
        an auto Signal records the enumerated value.
    cast : callable or None
        Coercion for the typed value (``None`` -> ``float``, the default).
    action_each : callable(value) -> plan, optional
        Extra plan run after the prompt (e.g. trigger something, or move a motor the user's
        value implies).
    speed : int
        Defaults to SLOW (manual swaps are the slowest thing in any experiment -> outermost).
    record_name : str, optional
        Name for the auto Signal (default ``name``).

    Notes
    -----
    Open-ended axes have unknown length; the ordering guardrail treats them as length 1 for
    its estimate.  Put manual axes outermost (they are slow) -- this is also where they make
    sense (you do not want to hand-swap a sample inside an energy loop).
    """
    rec = signal
    auto = None
    if rec is None and (values is not None):
        auto = Signal(name=(record_name or name), value=0.0)     # noqa: F821
        rec = auto

    def _move_enum(v):
        # show the instruction with the target value, then optionally capture a typed value
        yield from bps.input_plan("{} {}  -- ready? <enter>: ".format(prompt, v))
        if signal is not None:
            typed = yield from bps.input_plan("  enter {} = ".format(signal.name))
            yield from bps.mv(signal, _coerce(typed, cast))      # message-based
        # (when there is no typed signal, the enum value v is recorded on `auto` by
        #  ScanAxis.move_to via bps.mv(record, v))
        if action_each is not None:
            yield from action_each(v)

    if values is not None:
        # If we capture a TYPED value, set record=None so move_to doesn't overwrite it with v;
        # _move_enum sets `signal` itself.  Otherwise record the enum value via `auto`.
        record = None if signal is not None else auto
        return ScanAxis(name, values, move=_move_enum, record=record,
                        reads=([signal] if signal is not None else []), speed=speed)

    # open-ended: build a generator-of-values lazily is awkward for ScanAxis (which wants a
    # concrete list); instead we return a *plan factory* the recipe runs directly.  To keep a
    # uniform interface we expose it as a ScanAxis with a sentinel and a custom nesting helper
    # is overkill -- so for open-ended use, prefer composing ``manual_loop`` (below).
    raise ValueError(
        "manual_axis with values=None is open-ended; use manual_loop(...) instead, which "
        "yields the inner plan repeatedly until the user stops.")


def manual_loop(prompt, inner, *, signal=None, cast=None, stop_words=("", "stop", "q")):
    """Repeat ``inner()`` once per user-driven iteration until the user stops (open-ended).

    The composable counterpart to an open-ended manual axis (unknown count).  Each iteration:
    prompt the user to set up the next point, optionally capture a typed value onto ``signal``
    (recorded if ``signal`` is in your reads/baseline), run ``inner()`` (e.g. the rest of the
    nested axes + measurement), and ask whether to continue.

    Because the count is unknown, this is its own small driver rather than a :class:`ScanAxis`;
    splice it where a manual outer loop belongs (outermost).

    Parameters
    ----------
    prompt : str
        Per-iteration instruction, e.g. ``"Load the next sample"``.
    inner : callable() -> plan
        What to do for each manually-staged point (typically the inner axes + trigger_and_read,
        or a whole :func:`acquire` body).
    signal : ophyd Signal, optional
        Capture a per-iteration typed value (recorded if read/baselined).
    cast : callable or None
        Coercion for the typed value (``None`` -> ``float``, the default).
    stop_words : tuple of str
        Entering any of these (at the continue prompt) ends the loop.
    """
    i = 0
    while True:
        yield from bps.input_plan("{} (#{}) -- ready? <enter>: ".format(prompt, i))
        if signal is not None:
            typed = yield from bps.input_plan("  enter {} = ".format(signal.name))
            yield from bps.mv(signal, _coerce(typed, cast))      # message-based
        yield from inner()
        again = yield from bps.input_plan("Another? <enter>=yes, type 'stop' to finish: ")
        if again.strip().lower() in stop_words and again.strip() != "":
            break
        # treat empty as "yes, continue"; an explicit stop word ends it
        if again.strip().lower() in ("stop", "q", "n", "no"):
            break
        i += 1
