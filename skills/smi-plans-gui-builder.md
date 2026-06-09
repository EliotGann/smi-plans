# smi-plans GUI Builder — Development Plan

## What this does

This skill is the **development plan and contract** for a GUI that helps an SMI-SWAXS user
**assemble an experiment and produce a Bluesky script they copy-paste** into their beamline
IPython session. The GUI is, for now, a **code generator** — it does NOT talk to a RunEngine,
a queueserver, or any backend. It outputs text that references the `smi_plans` package.

The architecture is deliberately staged so a backend (direct-RE execution, or
`bluesky-queueserver`) can be added **later without rewriting** the GUI or the experiment model.

## When to use this

- Building, extending, or reviewing the SMI experiment-builder GUI.
- Designing the serializable "experiment spec" that the GUI edits and the code generator emits.
- Deciding how to keep the GUI decoupled from execution so qserver can be slotted in later.

## Scope (read this first)

IN SCOPE NOW:
- A model of an experiment as data (a JSON-serializable **ExperimentSpec**).
- Editing that model (the GUI: pick beam/q, geometry/apparatus, scan axes, manual steps,
  samples, metadata; reorder axes; see guardrail warnings).
- A **code generator**: ExperimentSpec → a clean, runnable Python script that imports
  `smi_plans` and calls `acquire(...)` / a preset, which the user pastes into their session.
- **Dry-run preview/validation** of the generated plan using the package's simulated-device
  test harness (no hardware), so the GUI can show "this will produce 1 run, N events" and catch
  errors before the user takes beam.

EXPLICITLY OUT OF SCOPE NOW (but the design must not preclude it):
- Any live connection to the RunEngine, the data broker/Tiled, EPICS, or a queueserver.
- Submitting/queuing/running plans. The output is text only.
- Live device readback, motor moves, plots of real data.

## The core principle: spec in the middle

Everything hinges on a single **serializable experiment model** that sits between the GUI and
all consumers. The GUI edits the spec; consumers (code generator now; a qserver submitter later;
the dry-run validator) read it. Nothing in the GUI calls `smi_plans` plan functions directly —
it only manipulates the spec and asks a generator to render it.

```
   ┌────────┐   edits    ┌──────────────┐   render    ┌─────────────────────┐
   │  GUI   │ ─────────► │ ExperimentSpec│ ──────────► │ code generator      │ → script text (copy-paste)
   └────────┘            │  (JSON/dict)  │ ──────────► │ dry-run validator   │ → "1 run, N events / errors"
                         └──────────────┘  ──────────► │ (LATER) qserver sub │ → submit to queue
                                              future
```

Because the spec is the contract, swapping "render to text" for "submit to qserver" later is an
additive change — a new consumer of the same spec.

## The ExperimentSpec (the data model to build)

A plain dict / dataclass tree, fully JSON-serializable (so it can be saved, loaded, shared, and
later sent over the wire). Suggested shape:

```python
ExperimentSpec = {
  "version": 1,
  "project_name": "311234_Doe",          # -> md
  "scan_name": "giwaxs_Tramp_NEXAFS",    # -> run scan_name
  "geometry": "reflection",              # or "transmission"
  "md": {"edge": "S_K"},                 # extra free-form intent

  "beam": {                              # beam / q-range
    "detectors": ["pil900KW", "pil2M"],  # names; generator maps to objects
    "arc_aware": true,                   # use saxs_waxs_dets() vs explicit list
    "reads": ["energy", "waxs", "xbpm2", "xbpm3"],
  },

  "apparatus": {                         # geometry / environment -> the setup() plan
    "align": {"routine": "alignement_gisaxs_hex", "angle": 0.1},
    "attenuators_in": ["att2_9"],
    "heater": {"kind": "linkam"},        # or {"kind": "lakeshore"} or null
    # extensible: rh controller, echem, beamstop, gate valve ...
  },

  "samples": {                           # one run per sample
    "source": "columns",                 # or "csv" / "inline"
    "names":   ["s1", "s2"],
    "piezo_x": [-56000, -45000],
    "piezo_y": [4000, 4000],
    "incident_angles": [0.1, 0.2],       # shared, or per-sample
    # ... -> SampleList.from_columns
  },

  "axes": [                              # scan stack, OUTERMOST FIRST
    {"type": "temperature", "values": [30, 60, 90], "soak": 120, "first_soak": 300},
    {"type": "motor", "name": "arc", "device": "waxs", "values": [0, 20], "speed": "slow"},
    {"type": "incidence", "values": [0.10, 0.20]},
    {"type": "energy", "grid": {"edge": 2472, "near": [-2, 2, 0.25], "post": [2, 60, 5]},
                       "flux_reseek": {"signal": "xbpm2.sumX", "threshold": 50}},
    {"type": "spatial", "x_step": 30, "x_n": 5},     # 5 fresh spots
    # {"type": "manual", "name": "temp_manual", "prompt": "Dial hot stage to",
    #  "values": [35, 50], "record_name": "temp_manual"}   # manual axis
    # {"type": "time", "n_frames": 20, "period": 10}        # kinetics
    # {"type": "potential", "values": [0,0.4,0.8]}          # echem
    # {"type": "rh", "values": [30,50,70]}                  # humidity
  ],

  "manual_setup": [                      # one-shot manual steps (-> setup + baseline)
    {"prompt": "Load the bar; read prep sheet",
     "values": [{"name": "thickness_nm", "cast": "float"}]}
  ],

  "exposure_s": 1.0,                     # det_exposure_time
}
```

Design notes for the model:
- **Device references are names, not objects** (`"waxs"`, `"piezo.x"`, `"att2_9"`). The
  generator maps names → the bare globals the user's session has. This keeps the spec pure data
  (serializable, GUI-safe, future-wire-safe). Maintain a single **device registry** (name →
  how to reference it in generated code + metadata like axis-speed default, units) — model it on
  the registry in `CDSAXS/DummyBluSky/user_scripts/bluesky_server.py` (`_motors`, `_detectors`).
- **Axis order in the list = nesting order** (outermost first), mirroring
  `smi_plans._compose.acquire(axes=[...])`.
- Reuse `smi_plans.Sample`/`SampleList` for the `samples` block — `from_columns`/`from_csv`
  already validate. The GUI sample table maps 1:1 to `SampleList.to_dicts()` /
  `from_dicts(...)`.
- Keep a `"version"` field from day one (forward-compat for the eventual qserver payload).

## The code generator (the deliverable users consume now)

`spec → str` (a runnable script). It should emit code that looks hand-written and idiomatic, so
the user trusts and can edit it. Mirror `smi_plans.recipes_combined.build_axes_from_spec` — that
function already turns an axis spec list into `ScanAxis` objects; the generator emits the
*source text* that builds the same axes and calls `acquire`.

Generated script shape (what to produce):

```python
# ===== generated by smi-plans GUI — paste into the beamline IPython session =====
import numpy as np
from smi_plans._compose import (acquire, acquire_bar, energy_axis, temperature_axis,
                                incidence_axis, motor_axis, spatial_grid_axes, manual_step,
                                SPEED_SLOW, SPEED_MEDIUM, SPEED_FAST)
from smi_plans._core import saxs_waxs_dets
from smi_plans import SampleList
from smi_plans.technique_C_temperature import linkam_heater

bar = SampleList.from_columns(
    names=["s1", "s2"], piezo_x=[-56000, -45000], piezo_y=[4000, 4000],
    incident_angles=[0.1, 0.2], md={"project_name": "311234_Doe"})

dets = saxs_waxs_dets()
reads = [energy, waxs, xbpm2, xbpm3]
heater = linkam_heater()
thickness = Signal(name="thickness_nm", value=0.0)

def setup():
    yield from alignement_gisaxs_hex(0.1)
    yield from bps.mv(att2_9.close_cmd, 1); yield from bps.sleep(1)
    yield from manual_step("Load the bar; read prep sheet", signals=[thickness])

def axes_for(s):
    th0 = piezo.th.position
    return [
        temperature_axis(heater, [30, 60, 90], soak=120, first_soak=300),
        motor_axis("arc", waxs, [0, 20], speed=SPEED_SLOW),
        incidence_axis(piezo.th, th0, s.incident_angles or [0.1, 0.2]),
        energy_axis(np.unique(np.r_[np.arange(2470,2474,0.25), np.arange(2474,2532,5)]),
                    flux_signal=xbpm2.sumX, flux_threshold=50),
        motor_axis("x", piezo.x, [piezo.x.position + i*30 for i in range(5)], speed=SPEED_FAST),
    ]

det_exposure_time(1.0, 1.0)
# RUN THIS:
RE(acquire_bar(bar, dets, axes_for, reads=reads,
               setup_for=lambda s: setup(), geometry="reflection",
               scan_name="giwaxs_Tramp_NEXAFS", md={"edge": "S_K"},
               baseline_for=lambda s: [thickness]))
# ================================================================================
```

Generator requirements:
- Emit only the imports actually needed (track which builders/presets the spec uses).
- Emit the `SampleList` from the samples block; choose `acquire` (single sample) vs `acquire_bar`
  (multiple) vs `multi_sample_run` (if the user opts into arc-economy) based on the spec.
- Render axis order verbatim from the list; render device names as bare identifiers.
- Put the actual `RE(...)` call clearly marked and LAST (and/or behind a `if __name__` guard so
  pasting the definitions is harmless and the user runs the final line deliberately).
- Be deterministic and diff-friendly (stable import order, consistent formatting). Consider
  `black`-compatible output.
- Include a header comment with the project, a timestamp, and a one-line summary of the axis
  stack (e.g. `# temperature x arc x incidence x energy x 5-spot, one run/sample`).

Keep the generator in the package (e.g. `src/smi_plans/_codegen.py`) so it is versioned with the
plans it targets and testable by the same harness. The GUI is a thin front-end over
`_codegen.render(spec)`.

## Dry-run preview / validation (use the test harness — this makes it fast)

The package ships a **complete set of simulated devices** (`tests/conftest.py::SimBeamline`) and
helpers that run a plan WITHOUT hardware and assert on the generated message stream. The GUI's
"Validate / Preview" button should use exactly this to give the user confidence before they
paste:

What the validator does (build it as `_codegen.dry_run(spec) -> report`):
1. Build the axes from the spec via `build_axes_from_spec` (or by `exec`-ing the generated
   script in a namespace populated with the SimBeamline globals — preferred, because it also
   validates the *generated text*).
2. Drive the resulting plan with `list(plan)` (no RunEngine).
3. Report: number of runs (expect 1 per sample), number of primary events per run, whether any
   ordering guardrail warnings fired, and any exception (with the offending axis).

```python
# pattern (mirrors tests/): 
sim = SimBeamline()
ns = {**sim.globals_dict(), "RE": (lambda plan: list(plan))}   # RE -> just exhaust the plan
exec(generated_script_text, ns)                                # runs the final RE(...) line
# inspect ns for the message list / counts, or have the script return them
```

Surface in the GUI: "✅ 1 run/sample, 120 events" / "⚠️ slow axis 'arc' nested inside 'x' —
will move 600× (reorder?)" / "❌ NameError: 'syringe_pu' not defined (device not in this
session)". This catches the classes of bugs the manual validation caught during development
(unbalanced runs, software-only axes, wrong axis order, missing devices) — automatically.

> The sim harness already converges the Linkam equilibration loop and keeps the WAXS arc up so
> SAXS stays in the det list — see `SimBeamline`. Reuse it; do not reinvent fake devices.

## Future: queueserver (design for it now, don't build it)

qserver is NOT in use at SMI today. Keep the door open by obeying these constraints:

- **The spec must be enough to produce a qserver item later.** A `bluesky-queueserver` plan item
  is `{"name": <plan_name>, "args": [...], "kwargs": {...}}` referencing a plan registered in the
  server's allowed-plans namespace. So: keep device references as **names/strings** (qserver
  serializes args; it cannot take live device objects), and keep the spec free of Python
  objects. The current `acquire(...)` signature takes live devices, so a qserver path will need a
  thin **string-arg wrapper plan** (e.g. `acquire_from_spec(spec_dict)`) that the server exposes
  and that resolves names → devices inside the worker. Note this as the future seam; the spec is
  already shaped for it.
- **Add an executor abstraction with one method**, even though only the codegen path exists now:
  ```python
  class Executor:
      def submit(self, spec): ...        # returns something to show the user
  class CopyPasteExecutor(Executor):     # NOW
      def submit(self, spec): return render(spec)          # -> script text
  # class QueueServerExecutor(Executor): # LATER
  #     def submit(self, spec): RM.item_add(spec_to_qserver_item(spec))
  # class DirectREExecutor(Executor):    # optional
  #     def submit(self, spec): RE(build_plan_from_spec(spec))
  ```
  The GUI calls `executor.submit(spec)`; today it's wired to `CopyPasteExecutor`. Adding qserver
  is a new class, not a GUI change.
- Prior art for a homegrown control channel at SMI is
  `CDSAXS/DummyBluSky/user_scripts/bluesky_server.py` (a ZMQ REP/PUB server with a
  motor/detector registry and main-thread RE dispatch). If a backend is added before qserver,
  this is the pattern to extend — but it's still a separate executor behind the same spec.

## GUI feature checklist (what the front-end must cover)

Organize the UI by the five concerns:

1. **Project / metadata:** project_name, scan_name, geometry, free-form md, exposure time.
2. **Beam / q:** detector multi-select (with the arc-aware SAXS/WAXS toggle), the per-event
   `reads` set (sensible default `[energy, waxs, xbpm2, xbpm3]`).
3. **Apparatus / geometry:** alignment routine + angle; attenuators-in selection; heater
   (none/Linkam/Lakeshore); (later) RH, echem, beamstop. These compose into `setup()`.
4. **Samples:** a table (name + piezo/hexa coords + per-sample incident angles + free md) backed
   by `SampleList`; import from CSV; single-sample vs bar; opt-in arc-economy (`multi_sample_run`).
5. **Scan axes:** an ordered, reorderable list; "add axis" of each type (energy / temperature /
   incidence / motor / spatial / potential / rh / time / **manual**); per-axis params; a live
   **ordering guardrail** indicator (slow axes should be higher). Show the resulting nesting and
   the estimated event count.
6. **Manual setup steps:** zero or more one-shot prompts that capture typed values into named
   Signals (recorded in baseline).

Cross-cutting:
- **Preview pane:** the generated script (read-only, copy button) + the dry-run report.
- **Save / load** the spec as JSON.
- **Validation gating:** warn (don't block) on guardrail issues; block on hard errors (missing
  required device, unbalanced run in dry-run).
- Filename-token hint: show which `{tokens}` the chosen axes/reads will make available, so the
  user understands their filenames (tie to the filename-templating contract in
  `docs/PACKAGE_OVERVIEW.md`).

## Suggested build phases

1. **Spec + registry + codegen (headless).** Define `ExperimentSpec`, the device registry, and
   `_codegen.render(spec)`. Cover with tests that render → `exec` under the sim harness →
   assert one run / expected events. *This is the foundation; do it first and it's independently
   useful (scriptable experiment generation without any GUI).*
2. **Dry-run validator.** `_codegen.dry_run(spec)` returning a structured report (runs, events,
   warnings, errors). Tests for each failure mode.
3. **Minimal GUI** over phases 1–2: the five concern panels + preview + copy + save/load. Any
   toolkit (the env has Qt via `bluesky_widgets`/PyQt; a web UI is also fine since there's no
   backend coupling).
4. **Polish:** guardrail visualization, token hints, CSV import, presets ("start from a
   recipe" → pre-fill the spec from a `recipes_combined` example).
5. **(Later, separate) Executor backends:** the `Executor` abstraction is already there; add
   `QueueServerExecutor` / `DirectREExecutor` when a backend exists.

## Rules / constraints

- **No backend calls now.** The GUI's only outputs are: script text, a saved spec file, and a
  dry-run report computed locally with simulated devices. No `RE`, no EPICS, no network.
- **Generated scripts must obey the tenets** (one run/sample; recorded context; `{token}`
  filenames; `md={}`; generators; slow axes outermost) — because they are built from
  `smi_plans._compose`, this is automatic; do not generate raw `bp.count`/`sample_id` code.
- **Keep the spec pure data** (names/strings, JSON-serializable) — this is what makes the eventual
  qserver path additive.
- **Reuse, don't reinvent:** `SampleList` for samples, `build_axes_from_spec` for axis assembly,
  `SimBeamline` for validation, `recipes_combined` for preset starting points.

## Pointers

- Composition API + axes: `docs/PACKAGE_OVERVIEW.md`, `skills/composing-smi-experiments.md`,
  `src/smi_plans/_compose.py`.
- The seed of the GUI bridge: `src/smi_plans/recipes_combined.py::build_axes_from_spec` (axis
  spec → `ScanAxis` list) — the codegen renders the *text* equivalent; the validator can use the
  function directly.
- The simulated-device harness to power preview/validation: `tests/conftest.py` (`SimBeamline`,
  `sim`, `inject`) and the assertions in `tests/test_smoke.py`.
- Future backend prior art: `SWAXS_user_scripts/CDSAXS/DummyBluSky/user_scripts/bluesky_server.py`
  (ZMQ control server + device registry) — pattern for an eventual executor, kept behind the spec.
