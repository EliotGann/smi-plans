"""
smi_plans._qserver
==================

**The curated bluesky-queueserver (QS) surface for ``smi_plans``.**

This module is the single, versioned place that decides *which* ``smi_plans`` plans are exposed
to the SMI queueserver, and how.  The profile collection's QS startup imports it with one line::

    from smi_plans._qserver import *      # in a profile startup module

and ``qserver-list-plans-devices`` then introspects exactly the names re-exported here into
``existing_plans_and_devices.yaml`` (the generated plan/device cache).  Nothing else in
``smi_plans`` needs to change to add/remove a QS plan -- edit :data:`__all__` here.

Two tiers (see ``docs/QSERVER_WIRING.md``)
------------------------------------------
**Tier 1 -- direct re-exports (visibility).**  The composition helpers (:func:`acquire`,
the axis builders, the manual steps) and every ``technique_*`` ``*_run`` / ``*_bar`` preset are
re-exported under stable, *namespaced* names (``A_nexafs_bar``, ``C_temperature_ramp_run`` ...).
QS can introspect all of them (their required arguments are annotation-free positionals, which QS
records as free parameters).  Presets whose required args are plain data (energies, setpoints,
sample columns, counts) are immediately runnable from the queue; presets that need a *live device
object* (a ``Heater``, a ``dets`` list, a ``set_potential`` callable) are listed but are not
practically submittable from a generic queue client, because there is no string a user can type
that QS will turn into a live ophyd object.

**Tier 2 -- ``*_from_spec`` string-arg wrappers (submittability).**  For the high-value presets,
this module adds thin wrapper plans that take a single **JSON-serializable ``spec`` dict** and
resolve device *names* -> live device *objects* inside the worker (via :func:`resolve` and the
:data:`DEVICE_REGISTRY`).  These are the plans a queue client / GUI should actually submit:
their only argument is data.  They reuse
:func:`smi_plans.recipes_combined.build_axes_from_spec` (the existing name->axes seam) so the
spec schema matches the one the GUI builder already targets.

How device names are resolved (important)
-----------------------------------------
Like every other ``smi_plans`` module, this one references beamline devices as **bare globals
injected at runtime** -- the SMI profile collection (and the test harness) set them as attributes
on the module.  :func:`resolve` therefore looks a name up in *this module's* ``globals()`` (then a
couple of fallbacks).  A wrapper that asks for ``{"detectors": ["pil2M", "pil900KW"]}`` gets the
live ``pil2M`` / ``pil900KW`` the profile injected -- no live object ever travels over the wire.

.. important::
    Importing this module standalone exposes the plan *functions* (so QS can introspect their
    signatures, which is all env-open needs).  *Running* a plan needs the live beamline globals
    (``bps``, ``energy``, ``pil2M`` ...), exactly as for the rest of the package.
"""

# ---------------------------------------------------------------------------
# Tier 1: direct re-exports of the composition layer + the technique presets
# ---------------------------------------------------------------------------
# The composition PLANS (kept un-prefixed -- they are the stable public API).  Only the
# generator-function plans are re-exported here; the axis *builders* (``energy_axis`` etc.) are
# not plans, so a queue client composes via ``acquire_from_spec`` and in-session/GUI code imports
# the builders directly from ``smi_plans._compose``.
from ._compose import (  # noqa: F401
    acquire,
    acquire_bar,
    manual_step,
    manual_value,
    manual_loop,
    pause_for_user,
)

# The technique presets.  We import the modules and re-export their *_run / *_bar plans under
# "<Letter>_<name>" so the QS plan list is unambiguous and free of name collisions (every
# technique has e.g. an ``example``; several share helper names).  We deliberately DO NOT export
# the ``example`` / ``example_*`` demo plans (they hardcode a specific bar and are not meant to be
# queue-driven).
from . import (  # noqa: F401
    technique_A_energy_edge as _A,
    technique_B_grazing as _B,
    technique_C_temperature as _C,
    technique_D_mapping as _D,
    technique_E_transmission as _E,
    technique_F_kinetics as _F,
    technique_G_humidity as _G,
    technique_H_echem as _H,
    technique_I_cdsaxs as _I,
    technique_J_xrr as _J,
    technique_K_tomography as _K,
    technique_L_printing as _L,
    technique_M_autonomous as _M,
    technique_N_xpcs as _N,
    technique_O_commissioning as _O,
)

#: Map of technique short-letter -> module.  Drives the namespaced re-export below.
_TECHNIQUE_MODULES = {
    "A": _A, "B": _B, "C": _C, "D": _D, "E": _E, "F": _F, "G": _G, "H": _H,
    "I": _I, "J": _J, "K": _K, "L": _L, "M": _M, "N": _N, "O": _O,
}

#: Demo/example plan names that are intentionally NOT exposed to the queue (they hardcode a bar).
_SKIP_PLAN_NAMES = ("example", "example_kinetics")


def _export_technique_plans():
    """Re-export every technique preset PLAN as ``<Letter>_<name>`` into this module's namespace.

    Returns the list of exported names (added to :data:`__all__`).  We export only the public
    *plans* a technique declares in its ``__all__``, where "plan" means a **generator function**
    (``inspect.isgeneratorfunction``).  This rule deliberately excludes:

    * the ``example*`` demos (they hardcode a specific bar);
    * non-plan helpers that merely happen to live in ``__all__`` -- ``energy_grid`` (returns an
      array), the ``*_heater`` builders (return a ``Heater``), the ``*_dets`` detector-list
      helpers, ``peizo_th_correction``, ``printer_trigger_signals``, etc.; and
    * the ``technique_M`` closed-loop *controllers* (``autonomous_loop`` / ``align_loop`` /
      ``ask_tell_loop``) -- these are plain functions that call ``RE(...)`` themselves (the one
      sanctioned place that happens), so they must NOT be submitted to the queue as plans.

    Genuine plan functions inside those controllers (e.g. ``measure_for_agent``) are generators
    and ARE exported.
    """
    import inspect

    g = globals()
    exported = []
    for letter, mod in _TECHNIQUE_MODULES.items():
        for nm in getattr(mod, "__all__", ()):
            if nm in _SKIP_PLAN_NAMES:
                continue
            obj = getattr(mod, nm, None)
            # A queue plan is a generator function.  This excludes classes (technique_C's
            # ``Heater``), plain helpers (``energy_grid``, ``*_dets``, ``*_heater``), and the
            # technique_M controller loops (which call RE() themselves).
            if not inspect.isgeneratorfunction(obj):
                continue
            export_name = "{}_{}".format(letter, nm)
            g[export_name] = obj
            exported.append(export_name)
    return exported


_TIER1_TECHNIQUE_NAMES = _export_technique_plans()


# ---------------------------------------------------------------------------
# Device registry + name -> live-object resolution (for the Tier-2 wrappers)
# ---------------------------------------------------------------------------
#: The device *names* the spec schema may reference, grouped by role, with a one-line note.
#: This is documentation + a validation allow-list; the actual objects are resolved live from the
#: injected globals by :func:`resolve`.  Dotted names (``piezo.x``) resolve attribute-by-attribute.
DEVICE_REGISTRY = {
    # detectors (q reach)
    "pil2M": "SAXS area detector",
    "pil900KW": "WAXS area detector",
    "pin_diode": "transmitted-flux diode (I1)",
    "xbpm2": "beam-position monitor / I0 (BPM2)",
    "xbpm3": "beam-position monitor / I0 (BPM3)",
    # beam / energy
    "energy": "DCM photon energy (eV) pseudo-positioner",
    "waxs": "WAXS arc positioner (``waxs`` settable; ``waxs.arc.position`` readback)",
    # sample motion
    "piezo": "SmarAct fine stage (``.x/.y/.z/.th``)",
    "piezo.x": "SmarAct fine X",
    "piezo.y": "SmarAct fine Y",
    "piezo.z": "SmarAct fine Z",
    "piezo.th": "SmarAct fine theta (incidence)",
    "stage": "Huber coarse stage (``.x/.y/.z/.theta/.chi/.phi``)",
    "stage.phi": "Huber phi rotation axis (the former ``prs``)",
    "stage.th": "Huber theta (alias of ``stage.theta``)",
    # detector translation / SDD
    "pil2M_pos": "SAXS detector translation (``.z`` is the SDD)",
    # attenuators (names vary per hutch; commonly used ones)
    "att2_9": "attenuator foil 2_9 (``.close_cmd`` / ``.open_cmd``)",
    "att2_10": "attenuator foil 2_10",
    "att2_11": "attenuator foil 2_11",
    "att2_12": "attenuator foil 2_12",
    # alignment routines (profile-collection plan-functions, resolved by name for grazing specs)
    "alignement_gisaxs_hex": "GISAXS alignment routine (Huber/hexapod), called as align(angle)",
    "alignement_gisaxs_doblestack": "GISAXS alignment routine (double-stack), called as align(angle)",
}

#: Heater builders by name (technique_C abstraction).  Used by ``temperature`` specs so a queue
#: client can ask for ``{"heater": "linkam"}`` instead of passing a live ``Heater`` object.
_HEATER_BUILDERS = {
    "linkam": _C.linkam_heater,
    "lakeshore": _C.lakeshore_heater,
}


class DeviceResolutionError(KeyError):
    """A spec referenced a device name that is not available in the worker namespace."""


def _module_namespace():
    """The namespace device names resolve against (this module's globals)."""
    return globals()


def resolve(name):
    """Resolve a device *name* (possibly dotted, e.g. ``"piezo.x"``) to the live object.

    Looks the root name up in this module's globals (where the profile injects the beamline
    devices), then walks any ``.attr`` chain.  Raises :class:`DeviceResolutionError` with an
    actionable message if the root name is missing (e.g. the profile did not inject it, or it is
    misspelled) -- this surfaces in the worker log at submit time, not as a silent ``None``.

    A non-string ``name`` is returned unchanged (so callers may pass an already-resolved object).
    """
    if not isinstance(name, str):
        return name
    root, _, rest = name.partition(".")
    ns = _module_namespace()
    if root not in ns:
        raise DeviceResolutionError(
            "device {!r} is not available in the queueserver worker namespace "
            "(root name {!r} not found). Is it injected by the profile, and spelled correctly? "
            "Known names: {}".format(name, root, ", ".join(sorted(DEVICE_REGISTRY)))
        )
    obj = ns[root]
    for attr in [a for a in rest.split(".") if a]:
        obj = getattr(obj, attr)
    return obj


def resolve_all(names):
    """Resolve a list of device names to live objects (order preserved)."""
    return [resolve(n) for n in (names or [])]


def _build_heater(spec_value):
    """Build a technique_C ``Heater`` from a spec value.

    ``spec_value`` may be:
      * a string in :data:`_HEATER_BUILDERS` (``"linkam"`` / ``"lakeshore"``), or
      * a dict ``{"kind": "linkam"|"lakeshore", ...kwargs}`` forwarded to the builder, or
      * already a ``Heater`` (returned unchanged).
    """
    if spec_value is None:
        raise ValueError("temperature spec requires a 'heater' (\"linkam\" or \"lakeshore\")")
    if isinstance(spec_value, str):
        kind, kwargs = spec_value, {}
    elif isinstance(spec_value, dict):
        kind = spec_value.get("kind")
        kwargs = {k: v for k, v in spec_value.items() if k != "kind"}
    else:
        return spec_value  # assume already a Heater
    try:
        builder = _HEATER_BUILDERS[kind]
    except KeyError:
        raise ValueError(
            "unknown heater kind {!r}; expected one of {}".format(
                kind, ", ".join(sorted(_HEATER_BUILDERS)))
        )
    return builder(**kwargs)


# ---------------------------------------------------------------------------
# Tier 2: string-arg ``*_from_spec`` wrapper plans (the queue-submittable surface)
# ---------------------------------------------------------------------------
# These take a single JSON-serializable ``spec`` dict and resolve names -> live objects inside
# the worker.  They are the plans a generic queue client / the GUI should submit.

def _spec_detectors(spec):
    """Resolve the detector list for a spec.

    ``spec["detectors"]`` is a list of names; if absent, fall back to the arc-aware
    ``saxs_waxs_dets(use_saxs, use_waxs)`` using ``spec.get("use_saxs"/"use_waxs", True)``.
    """
    from ._core import saxs_waxs_dets
    if spec.get("detectors"):
        return resolve_all(spec["detectors"])
    return saxs_waxs_dets(use_saxs=spec.get("use_saxs", True),
                          use_waxs=spec.get("use_waxs", True))


def _spec_reads(spec, default_names=("energy", "waxs", "xbpm2", "xbpm3")):
    """Resolve the per-event ``reads`` list for a spec (names -> objects)."""
    names = spec.get("reads", list(default_names))
    return resolve_all(names)


def _spec_md(spec):
    """Assemble the run md from the spec's flat metadata fields."""
    from ._core import merge_md
    md = dict(spec.get("md") or {})
    for key in ("project_name", "scan_name", "edge", "technique", "process"):
        if key in spec and key not in md:
            md[key] = spec[key]
    return merge_md(md) or None


def acquire_from_spec(spec):
    """Queue-submittable :func:`acquire`: build axes from a JSON ``spec`` and run ONE sample.

    This is the general-purpose wrapper -- the same shape the GUI builder's ``ExperimentSpec``
    targets.  ``spec`` is a plain dict (JSON-serializable)::

        {
          "name": "PS40nm",
          "geometry": "reflection",                 # or "transmission"
          "detectors": ["pil2M", "pil900KW"],       # names; default = arc-aware saxs_waxs_dets()
          "reads": ["energy", "waxs", "xbpm2", "xbpm3"],
          "exposure_s": 1.0,
          "scan_name": "giwaxs_Tramp_NEXAFS",
          "project_name": "311234_Doe",
          "md": {"edge": "S_K"},
          "context": {"th0": 0.0, "flux_signal": "xbpm2.sumX", "flux_threshold": 50},
          "axes": [                                  # OUTERMOST FIRST (nesting order)
            {"type": "temperature", "values": [30, 60, 90], "heater": "linkam"},
            {"type": "motor", "name": "arc", "device": "waxs", "values": [0, 20], "speed": 2},
            {"type": "incidence", "values": [0.10, 0.20]},
            {"type": "energy", "values": [2470, 2472, 2474], "flux_threshold": 50},
            {"type": "spatial", "x": [0, 30, 60, 90, 120]}
          ]
        }

    Device references everywhere are **names**; they are resolved to live objects in the worker.
    Returns the plan (``yield from`` it / submit it to the queue).
    """
    from .recipes_combined import build_axes_from_spec

    name = spec.get("name") or spec.get("scan_name") or "acquire"
    dets = _spec_detectors(spec)
    reads = _spec_reads(spec)
    geometry = spec.get("geometry")
    scan_name = spec.get("scan_name", "acquire")
    md = _spec_md(spec)
    exposure = spec.get("exposure_s")

    context = _resolve_axis_context(spec)
    axes = build_axes_from_spec(spec.get("axes", []), context=context)

    if exposure is not None:
        yield from det_exposure_time(exposure, exposure)            # noqa: F821 (injected global)

    yield from acquire(
        name, dets, axes, reads=reads, geometry=geometry, scan_name=scan_name, md=md,
        user_hints=spec.get("user_hints"))


def _resolve_axis_context(spec):
    """Build the ``context`` dict :func:`build_axes_from_spec` needs, resolving names -> objects.

    The spec carries a ``"context"`` sub-dict of *names* (and a few scalars like ``th0``); we
    resolve the device names here so ``build_axes_from_spec`` -- which expects live handles --
    gets real objects, while the wire payload stays pure data.  Per-axis ``temperature`` specs
    may carry their own ``heater`` (string), which we honor by injecting a built ``Heater`` into
    the context if the axis list needs one.
    """
    raw = dict(spec.get("context") or {})
    context = {}
    # scalars pass through untouched
    for k in ("th0", "flux_threshold"):
        if k in raw:
            context[k] = raw[k]
    # device-name fields -> live objects
    for k in ("energy", "th_axis", "waxs", "phi", "piezo_x", "piezo_y",
              "set_potential", "set_rh", "flux_signal", "potential_readback",
              "live_rh", "elapsed_signal", "manual_signal"):
        if k in raw:
            context[k] = resolve(raw[k])
    # Per-axis device references: a ``{"type": "motor", "device": "<name>"}`` axis is built by
    # ``build_axes_from_spec`` via ``context[<name>]``, so resolve every motor axis's device name
    # into the context (the device key is the NAME the axis references).  Likewise honor a per-
    # axis ``th_axis`` on an incidence axis if given.
    for ax in spec.get("axes", []):
        if ax.get("type") == "motor" and "device" in ax:
            dev_name = ax["device"]
            if dev_name not in context:
                context[dev_name] = resolve(dev_name)
        if ax.get("type") == "incidence" and "th_axis" in ax and "th_axis" not in context:
            context["th_axis"] = resolve(ax["th_axis"])
    # a heater referenced by the temperature axis (string) -> built Heater
    for ax in spec.get("axes", []):
        if ax.get("type") == "temperature" and "heater" in ax and "heater" not in context:
            context["heater"] = _build_heater(ax["heater"])
    if "heater" in raw and "heater" not in context:
        context["heater"] = _build_heater(raw["heater"])
    return context


def nexafs_from_spec(spec):
    """Queue-submittable NEXAFS / energy-edge sweep (wraps :func:`technique_A.nexafs_run`).

    ``spec`` (JSON)::

        {
          "name": "P3HT",
          "energies": [2818, 2820, 2822, ...],   # absolute eV (or use "edge"+grid below)
          "edge": 2822,                          # alt: build a grid around this edge
          "grid": {"pre": [-12,-2,2.0], "near": [-2,2,0.5], "post": [2,70,5.0]},
          "exposure_s": 1.0,
          "geometry": "transmission",
          "updown": true,
          "detectors": ["pil2M", "pin_diode", "xbpm2", "xbpm3"],
          "reads": ["energy"],
          "flux_signal": "xbpm2.sumX", "flux_threshold": 50,
          "dose_motor": "piezo.x", "dose_step": 30,
          "atten": ["att2_9"],                   # foils to close at run open
          "project_name": "311234", "md": {"edge": "S_K"}
        }
    """
    energies = _spec_energies(spec)
    dets = resolve_all(spec["detectors"]) if spec.get("detectors") else None
    reads = resolve_all(spec["reads"]) if spec.get("reads") else None
    flux_signal = resolve(spec["flux_signal"]) if spec.get("flux_signal") else None
    dose_motor = resolve(spec["dose_motor"]) if spec.get("dose_motor") else None
    atten_in = _atten_in_plan(spec.get("atten"))

    yield from _A.nexafs_run(
        spec.get("name", "sample"), energies,
        t=spec.get("exposure_s", 2.0), dets=dets, reads=reads,
        geometry=spec.get("geometry", "transmission"), updown=spec.get("updown", True),
        settle=spec.get("settle", 2.0), dose_motor=dose_motor, dose_step=spec.get("dose_step"),
        flux_signal=flux_signal, flux_threshold=spec.get("flux_threshold"),
        atten_in=atten_in, md=_spec_md(spec))


def giwaxs_from_spec(spec):
    """Queue-submittable GISAXS/GIWAXS grazing run (align, then wrap technique_B plans).

    ``spec`` (JSON)::

        {
          "name": "PS_film",
          "incident_angles": [0.10, 0.20],
          "waxs_arc": [0, 20],
          "exposure_s": 1.0,
          "align": "alignement_gisaxs_hex",      # alignment routine name (a beamline global)
          "align_angle": 0.1,
          "sample": {"piezo_x": 55000, "piezo_y": 5000, "piezo_z": 7000},  # coarse position
          "detectors": [...], "reads": [...],
          "atten": ["att2_9"],
          "project_name": "311234"
        }

    The grazing alignment routine (``alignement_gisaxs_hex`` / ``alignement_gisaxs_doblestack``)
    is a beamline global; the spec names it and the wrapper resolves it.  The wrapper coarse-
    positions the sample, runs alignment to find ``th0``, then runs ``technique_B.giwaxs_run``
    (which takes that aligned ``th0``).  If no ``align`` is given, it measures at the current
    ``th0`` (``piezo.th.position``) without aligning.
    """
    from ._samples import Sample
    from ._core import goto_sample

    name = spec.get("name", "sample")
    dets = resolve_all(spec["detectors"]) if spec.get("detectors") else None
    reads = resolve_all(spec["reads"]) if spec.get("reads") else None
    atten_in = _atten_in_plan(spec.get("atten"))
    th_axis = resolve(spec["th_axis"]) if spec.get("th_axis") else piezo.th   # noqa: F821
    dose_motor = resolve(spec["dose_motor"]) if spec.get("dose_motor") else None

    # Coarse-position the sample first (if a position is given), so alignment starts in the
    # right place.  ``Sample.from_dict`` accepts ``name`` + the flat ``piezo_*``/``hexa_*`` coords.
    if spec.get("sample"):
        sample_obj = Sample.from_dict(dict(spec["sample"], name=name))
        yield from goto_sample(sample_obj)

    # Align to find th0 (or use the current theta if no alignment routine was named).
    if spec.get("align"):
        align_routine = resolve(spec["align"])
        yield from align_routine(spec.get("align_angle", 0.1))
    th0 = th_axis.position

    yield from _B.giwaxs_run(
        name, th0=th0,
        incident_angles=spec.get("incident_angles", (0.1, 0.2)),
        waxs_arc=spec.get("waxs_arc", (0, 20)),
        t=spec.get("exposure_s", 1.0), dets=dets, reads=reads, th_axis=th_axis,
        dose_motor=dose_motor, dose_step=spec.get("dose_step"),
        atten_in=atten_in, md=_spec_md(spec))


def temperature_ramp_from_spec(spec):
    """Queue-submittable temperature ramp (wraps :func:`technique_C.temperature_ramp_run`).

    ``spec`` (JSON)::

        {
          "name": "BB40",
          "heater": "lakeshore",                 # or "linkam"
          "setpoints": [30, 60, 90, 60, 30],     # degC
          "exposure_s": 2.0,
          "geometry": "transmission",
          "soak": 60.0, "tol": 1.0,
          "detectors": [...], "reads": [...],
          "atten": ["att2_9"],
          "dose_motor": "piezo.x", "dose_step": 30,
          "project_name": "311234"
        }
    """
    heater = _build_heater(spec.get("heater"))
    dets = resolve_all(spec["detectors"]) if spec.get("detectors") else None
    reads = resolve_all(spec["reads"]) if spec.get("reads") else None
    dose_motor = resolve(spec["dose_motor"]) if spec.get("dose_motor") else None
    atten_in = _atten_in_plan(spec.get("atten"))

    yield from _C.temperature_ramp_run(
        spec.get("name", "sample"), heater, spec["setpoints"],
        t=spec.get("exposure_s", 1.0), dets=dets, reads=reads,
        geometry=spec.get("geometry", "transmission"),
        tol=spec.get("tol", 1.0), poll=spec.get("poll", 10.0),
        timeout=spec.get("timeout", 7200.0), soak=spec.get("soak", 60.0),
        first_soak=spec.get("first_soak"),
        dose_motor=dose_motor, dose_step=spec.get("dose_step"),
        atten_in=atten_in, md=_spec_md(spec))


# ---------------------------------------------------------------------------
# Small spec helpers shared by the wrappers
# ---------------------------------------------------------------------------
def _spec_energies(spec):
    """Resolve an energies list from a spec: explicit ``energies`` or an ``edge``+``grid``."""
    if spec.get("energies"):
        return list(spec["energies"])
    if "edge" in spec:
        grid = spec.get("grid") or {}
        return list(_A.energy_grid(
            spec["edge"],
            pre=tuple(grid.get("pre", (-30, -2, 5.0))),
            near=tuple(grid.get("near", (-2, 2, 0.25))),
            post=tuple(grid.get("post", (2, 60, 5.0))),
        ))
    raise ValueError("energy spec requires either 'energies' or 'edge' (+ optional 'grid')")


def _atten_in_plan(foil_names):
    """Build an ``atten_in() -> plan`` that closes the named attenuator foils, or ``None``.

    Each foil is resolved from the worker namespace and its ``.close_cmd`` is set to 1 (the SMI
    "insert" idiom), with a 1 s settle.  Returns ``None`` when no foils are requested so the
    preset's ``atten_in`` default (do nothing) applies.
    """
    if not foil_names:
        return None
    foils = resolve_all(foil_names)

    def _atten_in():
        for foil in foils:
            yield from bps.mv(foil.close_cmd, 1)                    # noqa: F821 (injected global)
        yield from bps.sleep(1)                                     # noqa: F821

    return _atten_in


# ---------------------------------------------------------------------------
# The exported QS surface
# ---------------------------------------------------------------------------
#: Tier-2 wrapper plan names (the queue-submittable, data-only surface).
_TIER2_WRAPPER_NAMES = [
    "acquire_from_spec",
    "nexafs_from_spec",
    "giwaxs_from_spec",
    "temperature_ramp_from_spec",
]

#: Tier-1 composition-helper PLAN names (re-exported un-prefixed).  Only the generator-function
#: plans are listed here -- the axis *builders* (``energy_axis``/``motor_axis``/... and
#: ``manual_axis``) return ``ScanAxis`` objects, not plans, so they are not queue plans and are
#: intentionally NOT exported to QS (a queue client composes via ``acquire_from_spec`` instead;
#: in-session/GUI code imports the builders directly from ``smi_plans._compose``).
_TIER1_COMPOSE_NAMES = [
    "acquire",
    "acquire_bar",
    "manual_step",
    "manual_value",
    "manual_loop",
    "pause_for_user",
]

# ``__all__`` is what ``from smi_plans._qserver import *`` (and thus QS) sees.  It is the union of:
# the Tier-2 wrappers, the Tier-1 compose helpers, and the namespaced technique presets.  The
# ``resolve`` / ``DEVICE_REGISTRY`` symbols are intentionally NOT exported (they are not plans;
# the leading names keep them out of the QS plan list anyway).
__all__ = list(_TIER2_WRAPPER_NAMES) + list(_TIER1_COMPOSE_NAMES) + list(_TIER1_TECHNIQUE_NAMES)


def qserver_plan_names():
    """Return the sorted list of plan names this module exposes to the queueserver.

    Useful for tests and for a quick ``python -c`` sanity check of the exposed surface without
    standing up QS.
    """
    return sorted(__all__)
