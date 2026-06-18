# smi-plans

Composable, GUI-ready **Bluesky data-acquisition plans** for the NSLS-II **SMI-SWAXS**
beamline (small/wide-angle X-ray scattering).

An SMI experiment is an *assembly of concerns* — which energ(ies)/detectors/WAXS-arc (beam +
q-range) × what apparatus/geometry (grazing, Linkam, RH, e-chem) × what scanning (single spot,
5 locations, grid, energy sweep, temperature ramp — usually several nested) × what
manual/interactive steps × what to record. This package lets you express that directly: a
**measurement core** wrapped by a stack of **scan axes** you nest in any order, producing
**one well-formed Bluesky run** with the filename templated from recorded fields.

It is the runnable embodiment of the SMI "good user script" tenets: one run per logical
sample; context recorded as devices/Signals (never `.get()`-ed into filenames); intent in
`md={}` (never `sample_id`/`RE.md`); plans are generators end-to-end; slow/in-vacuum axes move
sparingly.

> Provenance: this package began life in `SWAXS_user_scripts/templates/smi_plans/`, distilled
> from a survey of ~230 legacy user scripts. The design rationale and the legacy-pattern
> knowledge it replaces live in `docs/` and `skills/` here (and the original survey remains in
> the `SWAXS_user_scripts` repo).

## Install

The pure-Python parts and the test suite install standalone; `bluesky`/`ophyd`/`numpy` are
provided by the live beamline environment (or via the `beamline` extra for off-beamline dev).

```bash
cd ~/get/smi/smi-plans
pip install -e .                 # core (pure-Python: sample model, spec, code generator)
pip install -e ".[test]"         # + bluesky/ophyd/numpy + pytest for the full sim test suite
```

After install, `import smi_plans` works anywhere — no `sys.path` hacks.

## Quick start (in the beamline IPython session)

```python
from smi_plans._compose import acquire, energy_axis, temperature_axis, incidence_axis, motor_axis
from smi_plans.technique_C_temperature import linkam_heater

heater = linkam_heater()
th0 = piezo.th.position
axes = [
    temperature_axis(heater, [30, 60, 90]),                  # slow  -> outermost
    motor_axis("arc", waxs, [0, 20], speed=2),               # slow, in-vacuum
    incidence_axis(piezo.th, th0, [0.10, 0.20]),
    energy_axis(np.linspace(2470, 2490, 41),                 # DCM energy sweep
                flux_signal=xbpm2.sumX, flux_threshold=50),
    motor_axis("x", piezo.x, [0, 30, 60, 90, 120], speed=0), # 5 fresh spots -> innermost
]
RE(acquire("PS40nm", [pil2M, pil900KW, xbpm2, xbpm3], axes,
           reads=[energy, waxs], setup=lambda: alignement_gisaxs_hex(0.1),
           geometry="reflection", scan_name="giwaxs_Tramp_NEXAFS_5loc",
           md={"project_name": "311234"}))
```

For the common single-concern cases, the `technique_*` presets pre-assemble the standard thing.

## Queueserver

To run these plans from the SMI `bluesky-queueserver` (queue-monitor GUI), the profile collection
imports the curated surface `smi_plans._qserver` into its startup namespace (one line) and
regenerates `existing_plans_and_devices.yaml`. That module re-exports every `technique_*`
`*_run`/`*_bar` preset plus data-only `*_from_spec` wrapper plans (e.g. `acquire_from_spec`,
`nexafs_from_spec`, `temperature_ramp_from_spec`) that take a single JSON `spec` and resolve device
*names* → live objects inside the worker. See **`docs/QSERVER_WIRING.md`** for the exact
profile-collection wiring steps.

## Layout

```
smi-plans/
├── pyproject.toml          src/ layout; `pip install -e .` -> import smi_plans
├── README.md               (this file)
├── src/smi_plans/          the package
│   ├── _samples.py         Sample / SampleList (PURE PYTHON — GUI-safe)
│   ├── _preprocessors.py   opt-in plan-mutating decorators (fresh-spot, ensure-in, …)
│   ├── _core.py            run-shaping primitives (one_sample_run, multi_sample_run, …)
│   ├── _compose.py         ★ the composition layer: ScanAxis + axis builders + acquire()
│   ├── recipes_combined.py ★ worked cross-concern examples + spec→axes bridge
│   ├── _qserver.py         ★ curated bluesky-queueserver surface (presets + *_from_spec wrappers)
│   └── technique_<A–O>_*.py PRESET RECIPES, one per concern-bundle
├── docs/
│   ├── PACKAGE_OVERVIEW.md  the full package reference (was the package README)
│   └── GUI_PLAN.md          → also surfaced as a skill (see skills/)
├── skills/                  agent-loadable skills (composition, GUI builder, legacy knowledge)
├── tests/                   pytest suite running plans against simulated devices
└── examples/                generated / example experiment scripts
```

## Docs & skills

- `docs/PACKAGE_OVERVIEW.md` — the full reference: every module, the tenets, the filename
  templating contract, the manual/interactive layer, `multi_sample_run`/`RunRouter`.
- `skills/composing-smi-experiments.md` — how to assemble a bespoke experiment from axes.
- `skills/smi-plans-gui-builder.md` — the plan for the **copy-paste code-generator GUI**.
- `skills/legacy-swaxs-patterns.md` — what the legacy SMI scripts did and how each pattern maps
  onto smi-plans (the knowledge needed to later annotate `SWAXS_user_scripts`).

## Testing

The package is validated by running each plan against `ophyd.sim` devices and asserting on the
generated Bluesky message stream (one run, balanced events) — no hardware needed.

```bash
pip install -e ".[test]"
pytest -q
```
