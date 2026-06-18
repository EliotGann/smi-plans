# smi-plans GUI Builder — Development Plan

## What this does

This skill is the **development plan and contract** for a GUI that helps an SMI-SWAXS user
**assemble an experiment and produce a Bluesky script they copy-paste** into their beamline
IPython session. The GUI is, for now, a **code generator** — it does NOT talk to a RunEngine,
a queueserver, or any backend. It outputs text that references the `smi_plans` package.

The architecture is deliberately staged so a backend (direct-RE execution, or
`bluesky-queueserver`) can be added **later without rewriting** the GUI or the experiment model.

> **Two important status notes (2026-06), detailed in the "Future: queueserver" section:**
> (1) The package now ships the qserver target this skill predicted — `smi_plans._qserver` with
> data-only `acquire_from_spec(spec)` / `*_from_spec` wrapper plans whose `spec` is the same
> `ExperimentSpec` shape described here. Build the spec to validate against `acquire_from_spec`.
> (2) **Production queueserver at SMI is deferred** pending a facility-level proposal/project
> metadata solution, so the **copy-paste code generator is the primary shipping path** — do not
> gate GUI work on a live queue, and do **not** put proposal/project on samples or invent a local
> proposal source.

## When to use this

- Building, extending, or reviewing the SMI experiment-builder GUI.
- Designing the serializable "experiment spec" that the GUI edits and the code generator emits
  (and that maps 1:1 onto `smi_plans._qserver.acquire_from_spec`'s `spec`).
- Deciding how to keep the GUI decoupled from execution so qserver can be slotted in later (the
  wrapper plans now exist; production deployment is deferred — see "Future: queueserver").

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
- Submitting/queuing/running plans. The output is text only. (Production qserver at SMI is
  *deferred* anyway — see "Future: queueserver" — so the copy-paste generator is the shipping path;
  the qserver `*_from_spec` target already exists in the package for when it is greenlit.)
- Live device readback, motor moves, plots of real data.

## The core principle: spec in the middle

Everything hinges on a single **serializable experiment model** that sits between the GUI and
all consumers. The GUI edits the spec; consumers (code generator now; a qserver submitter later;
the dry-run validator) read it. Nothing in the GUI calls `smi_plans` plan functions directly —
it only manipulates the spec and asks a generator to render it.

```
   ┌────────┐   edits    ┌──────────────┐   render    ┌─────────────────────┐
   │  GUI   │ ─────────► │ ExperimentSpec│ ──────────► │ code generator      │ → script text (copy-paste)  [SHIPS NOW]
   └────────┘            │  (JSON/dict)  │ ──────────► │ dry-run validator   │ → "1 run, N events / errors"
                         └──────────────┘  ──────────► │ qserver sub via     │ → item: acquire_from_spec(spec)
                                            deferred    │ _qserver wrappers   │   [target EXISTS; deploy DEFERRED]
                                                        └─────────────────────┘
```

Because the spec is the contract, swapping "render to text" for "submit to qserver" later is an
additive change — a new consumer of the same spec. The qserver consumer's target already exists
(`smi_plans._qserver.acquire_from_spec` and the `*_from_spec` wrappers take this exact spec); only
the *production deployment* is deferred (facility proposal/metadata solution — see below).

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

  "apparatus": {                         # geometry / environment
    "align": {"routine": "alignement_gisaxs_hex", "angle": 0.1},  # -> acquire(align=...) PRE-run hook
    "attenuators_in": ["att2_9"],        # -> acquire(setup=...) IN-run hook (recorded)
    "heater": {"kind": "linkam"},        # or {"kind": "lakeshore"} or null
    # extensible: rh controller, echem, beamstop, gate valve ...
    # NB: alignment routines open their own runs + stage detectors, so they MUST map to the
    # `align` pre-run hook, NOT `setup` -- else RedundantStaging. The codegen/executor must keep
    # these separate (see _compose.acquire's align vs setup).
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
    {"type": "motor", "name": "arc", "device": "waxs.arc", "values": [0, 20], "speed": "slow"},
    {"type": "incidence", "values": [0.10, 0.20]},   # th0 omitted -> relative/aligned-zero
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

def align(s):
    # PRE-run: alignment opens its own runs + stages detectors -> must be the `align` hook,
    # NOT `setup` (else RedundantStaging when the measurement run stages the same detectors).
    yield from alignement_gisaxs_hex(0.1)

def setup():
    # IN-run: recorded in this run's documents/baseline.
    yield from bps.mv(att2_9.close_cmd, 1); yield from bps.sleep(1)
    yield from manual_step("Load the bar; read prep sheet", signals=[thickness])

def axes_for(s):
    return [
        temperature_axis(heater, [30, 60, 90], soak=120, first_soak=300),
        motor_axis("arc", waxs.arc, [0, 20], speed=SPEED_SLOW),
        # th0=None -> RELATIVE / aligned-zero: anchor incidence to wherever the pre-run `align`
        # left theta (do NOT pre-read piezo.th.position here -- axes_for runs BEFORE align).
        # The recorded `incident_angle` pseudo-axis is the true relative angle (-> {incident_angle}).
        incidence_axis(piezo.th, None, s.incident_angles or [0.1, 0.2]),
        energy_axis(np.unique(np.r_[np.arange(2470,2474,0.25), np.arange(2474,2532,5)]),
                    flux_signal=xbpm2.sumX, flux_threshold=50),
        motor_axis("x", piezo.x, [piezo.x.position + i*30 for i in range(5)], speed=SPEED_FAST),
    ]

det_exposure_time(1.0, 1.0)
# RUN THIS:
RE(acquire_bar(bar, dets, axes_for, reads=reads,
               align_for=align,                       # PRE-run alignment (opens its own runs)
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
- **The WAXS arc axis is `waxs.arc`, NOT `waxs`.** On the beamline `waxs = pil900KW.motors` (a
  readable container, *not* movable); the settable arc is `waxs.arc`. Emit `motor_axis("arc",
  waxs.arc, ...)` and, in a spec, `{"type": "motor", "device": "waxs.arc", ...}`. (`bps.mv(waxs,
  ...)` fails.) Keeping `waxs` in `reads` is fine -- it records the arc/beamstop readbacks.
- **Duplicate-key safety is automatic.** `acquire`/`*_from_spec` de-dup the `trigger_and_read`
  list, so listing both `pil900KW` (a det) and `waxs` (= its `.motors`) no longer raises "Data
  keys ... collide" -- the detector is kept and `{waxs_arc}` still resolves. The GUI need not
  prune `reads`, but should not list a device twice for no reason.
- **Alignment goes in the `align`/`align_for` PRE-run hook, never `setup`** (alignment opens its
  own runs + stages detectors -> `RedundantStaging` if run in-run). Keep `setup`/`setup_for` for
  in-run, recorded config (attenuators-in, manual steps).
- **Incidence anchoring: emit `incidence_axis(piezo.th, None, angles)` (relative/aligned-zero) by
  default** when an alignment runs -- do NOT emit `th0 = piezo.th.position` read at axis-build time
  (in `acquire_bar`, `axes_for` runs *before* the align hook, so a pre-read captures the *nominal*,
  not the aligned, theta). Only emit a numeric `th0` if the user explicitly anchors to an absolute
  theta. The recorded `incident_angle` is always the true relative angle.
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

## Sample bookmarks (the persistent sample/holder system)

This is **separate from the ExperimentSpec** above. The spec is *what to do* (transient, per
experiment); a **sample bookmark** is *a physical thing on a holder* (persistent, shared across
sessions). The bookmark system has its own full design in **`docs/SAMPLE_SYSTEM_PLAN.md`** — read
that first. This section is only the **GUI's contract** with it.

> Build bookmarks against the design in `SAMPLE_SYSTEM_PLAN.md`. Do **not** model them on how
> sample/position state is handled in the existing test GUIs / prototype servers — those predate
> this system and use ad-hoc per-app state. The canonical store is the one below.

### The one rule: the GUI talks to the store over its OWN Redis connection, not the RunEngine

Sample bookmarks live in **Redis db=2** (the **shared-state bus**), accessed through the
pure-python **`SampleStore`** facade (`smi_plans._store.SampleStore`). The GUI **connects directly
to Redis** — `store = SampleStore.from_redis()` — and does **not import the profile/`smi_beamline`,
not `_context`, not EPICS, not the RunEngine**. It needs only `redis` + `redis_json_dict` and the
package's pure-python `_samples`/`_store` in its own env, and to run where the db=2 host + secret
(`/etc/bluesky/redis.secret`) are reachable (the beamline workstation — see `SAMPLE_SYSTEM_PLAN.md`
§1b for the boundary). It performs **no motion** and makes **no RE/EPICS calls**.

```
   ┌────────┐  SampleStore.from_redis()      ┌──────────────────────┐   SampleStore(_context...)  ┌──────────────────┐
   │  GUI   │ ──own Redis conn──►            │   Redis db=2         │            ◄──seam reuse──── │ beamline plans   │
   │ (no    │ ◄────────────────►             │  'swaxssamples'      │            ◄──────────────►  │ load_sample(...) │
   │ profile│  put_/get_/list_               │  samples / holders / │   reads active ptr          │ + history callback│
   │ import)│  set_active_sample (intent)──► │  magazine / active   │ ◄───────────────────────────┤ writes ScanRecord │
   └────────┘                                │  + scan history      │                             └──────────────────┘
                                             └──────────────────────┘
            two independent processes, two independent Redis connections, ONE shared db=2 store
```

### What the GUI reads (render bookmarks)

From `store` (all pure data; see the dataclasses in `SAMPLE_SYSTEM_PLAN.md` §2):

- `list_holders()` / `list_samples(holder_id=...)` → draw the magazine and each bar's samples.
- per `Sample`: `name`, `slot`, `nominal`/`refined` `Position` (show "aligned ✓" when `refined`
  is set), `last_alignment(...)` (code/status/when), `n_scans()`, and `history[-1].energy_eV` /
  `.when` for an at-a-glance "last measured" badge.
- `get_active_sample()` → highlight what is currently **loaded**.
- `magazine()` → which holder is `at_measurement` (only one, by design D3), which are racked.

### What the GUI writes (edit bookmarks)

- **New / edited samples:** a sample table (name + holder + slot + nominal coords + free md) →
  `put_sample(...)` / bulk `import_samples(samples, holder)`. Identity is the stable `Sample.id`
  (D10) — **renaming in the GUI never breaks history links**, because history joins on id, not
  name. Unknown columns the user adds fold into `md` (same rule as `from_csv`).
- **Holder membership / slot labels:** `put_holder(...)`.
- **CSV import/export:** wire the GUI's import button to `import_samples` (the `samples.csv`
  schema in §6) and the export button to `export_tables()` → write `samples_out.csv` +
  `scans_out.csv` (the two joinable sheets — the user's enriched spreadsheet).

### How the GUI triggers a load (intent in the shared store, motion in the beamline process)

The GUI and the beamline are **separate processes sharing db=2**, so "load sample X" is the GUI
**writing intent** that the beamline process acts on — the GUI never moves anything itself:

- **The hand-off:** the GUI calls `store.set_active_sample(id)` (a write to the shared db=2). The
  beamline session/worker — which *does* have the RunEngine + devices — runs `load_sample(...)`
  (transfer the holder, go to position, confirm the active pointer; §4). Because both see the same
  Redis, the beamline can pick up "the GUI requested sample X" and the GUI can see "the beamline
  loaded it" — live.
- **Triggering the plan today (no qserver):** the beamline operator runs
  `RE(load_sample(store.get_sample("<id>"), store=store))` in the session (where `store` there is
  the in-profile `SampleStore(_context.get_sample_store())`). The GUI can surface that one-liner
  to copy, or just set the active pointer and let the operator load it.
- **Later (qserver):** "load sample X" enqueues a `load_sample` plan item to the worker; the active
  pointer in db=2 is the shared hand-off. Additive — no GUI rework, same store.

### How the on-axis viewer uses bookmarks (dose map)

The on-axis / SWAXS viewer can overlay the **irradiated-region map** by reading each sample's
`history[].spots` (`SpotSummary`, §7) — the visited spots/bbox in the **sample frame** — so the
user sees which parts of the sample have already seen beam before choosing a fresh spot. It reads
this from its own `store` connection (compact, fast); the full per-run data is recoverable from
Tiled via each `ScanRecord.run_uid` if needed. The viewer **reads**; it does not write bookmarks.

### Connecting / offline

- **Live (the real case):** `store = SampleStore.from_redis()` — a direct db=2 connection, no
  profile import. Requires `redis` + `redis_json_dict` in the GUI's env and reachability of the
  db=2 host + secret (`SAMPLE_SYSTEM_PLAN.md` §1b).
- **Offline / tests only:** `SampleStore({})` (in-memory) or a JSON-file backend — for developing
  the GUI with **no Redis**. This is a dev convenience, **not** a way to see live samples.

## Future: queueserver (the spec wrappers now EXIST; production is deferred)

**Status update (2026-06).** Two things changed since this skill was first written:

1. **The qserver plan surface now exists in the package.** `smi_plans._qserver` is a curated
   bluesky-queueserver surface: it re-exports the A–O presets AND ships **data-only
   `*_from_spec` wrapper plans** — `acquire_from_spec(spec)`, `nexafs_from_spec(spec)`,
   `giwaxs_from_spec(spec)`, `temperature_ramp_from_spec(spec)` — that take a **single
   JSON-serializable `spec` dict** and resolve device *names → live objects inside the worker*
   (reusing `recipes_combined.build_axes_from_spec`). This is **exactly the "string-arg wrapper
   plan" seam this skill predicted**, and it is the concrete target for a `QueueServerExecutor`.
   The `acquire_from_spec` spec schema is the same `ExperimentSpec` shape described in this skill
   (names not objects, axes outermost-first, `context` of names). See
   `docs/QSERVER_WIRING.md` and `tests/test_qserver.py`.

2. **Production queueserver at SMI is DEFERRED pending a facility-level solution.** Do **not** build
   the GUI assuming a live SMI queue is imminent. The blocker is **proposal/project metadata**:
   `proposal_id` sets `RE.md` in the terminal *process*, and a qserver worker is a *separate*
   process that would not inherit it, so queued runs would carry the wrong/empty
   `data_session`/proposal/project. The beamline decided this needs a facility/NSLS-II-wide
   shared-proposal mechanism, not a per-beamline hack (full analysis:
   `docs/QSERVER_WIRING.md` → "Deferred: proposal/project metadata"; restructure plan
   `docs/STARTUP_RESTRUCTURE_PLAN.md` §7.3 item 5).

**What this means for the GUI (unchanged direction, sharper target):**

- **Keep building the copy-paste code generator as the primary, shipping path.** It is unaffected
  by the QS deferral and is what users will actually use at the beamline now.
- **Keep the spec pure data and qserver-shaped** (the wrappers above prove the shape is right) so a
  `QueueServerExecutor` is a drop-in later. The mapping is now concrete, not hypothetical:

  ```python
  class Executor:
      def submit(self, spec): ...                 # returns something to show the user
  class CopyPasteExecutor(Executor):              # NOW (ships)
      def submit(self, spec): return render(spec)            # -> script text
  # class QueueServerExecutor(Executor):          # LATER (target exists, deployment deferred)
  #     def submit(self, spec):
  #         # the package already exposes acquire_from_spec / *_from_spec that take this spec
  #         RM.item_add({"name": "acquire_from_spec", "args": [spec], "kwargs": {}})
  ```

  i.e. a GUI `ExperimentSpec` becomes a qserver item by wrapping it as the single `spec` arg of
  `acquire_from_spec` (or the technique-specific `*_from_spec`). **No spec rework is needed** —
  validate against `acquire_from_spec`'s schema as you build the model.
- **Do NOT put proposal/project on the sample or bake a local proposal source into the GUI.** Until
  the facility mechanism exists, the GUI's "project / proposal" fields feed the *generated script's*
  `md`/`project_name` (copy-paste path), exactly as today. When a live queue arrives, the worker
  (not the GUI, not the sample bookmark) will seed proposal/`data_session` from the facility source;
  the GUI just keeps carrying `project_name`/intent in `md`. Treat proposal as a **session/queue**
  fact, never a sample fact.
- Prior art for a homegrown control channel at SMI is
  `CDSAXS/DummyBluSky/user_scripts/bluesky_server.py` (a ZMQ REP/PUB server with a
  motor/detector registry and main-thread RE dispatch). If a backend is added before the facility
  QS solution, this is the pattern to extend — but it's still a separate executor behind the same
  spec, and it inherits the same proposal/metadata caveat.

## GUI feature checklist (what the front-end must cover)

Organize the UI by the five concerns:

1. **Project / metadata:** project_name, scan_name, geometry, free-form md, exposure time. These
   flow into the generated script's `md`/`project_name` (and the `spec`'s `md`/`project_name`).
   **Proposal/`data_session` is intentionally NOT a GUI field** — it is set by the beamline session
   (`proposal_id`) today, and by a facility shared-proposal source on the worker once production QS
   lands; the GUI only carries user *intent* (`project_name`, free-form `md`), never the
   authoritative proposal/data-session. (See "Future: queueserver".)
2. **Beam / q:** detector multi-select (with the arc-aware SAXS/WAXS toggle), the per-event
   `reads` set (sensible default `[energy, waxs, xbpm2, xbpm3]`).
3. **Apparatus / geometry:** alignment routine + angle; attenuators-in selection; heater
   (none/Linkam/Lakeshore); (later) RH, echem, beamstop. These compose into `setup()`.
4. **Samples / bookmarks:** a table (name + holder + slot + piezo/stage coords + per-sample
   incident angles + free md) backed by the **persistent `SampleStore`** (Redis db=2), not just an
   in-memory `SampleList`. Import/export CSV (the two-sheet schema); single-sample vs bar; opt-in
   arc-economy (`multi_sample_run`); show aligned/last-measured state; "load sample" sets the
   active pointer (intent). See the **Sample bookmarks** section above and
   `docs/SAMPLE_SYSTEM_PLAN.md`. (An ExperimentSpec's transient `samples` block may also be
   populated *from* a bookmark selection.)
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
  dry-run report computed locally with simulated devices. No `RE`, no EPICS. (The **one** allowed
  network/persistence is the **`SampleStore`** for sample bookmarks — a **direct Redis db=2
  connection** via `SampleStore.from_redis()`, or an offline dict/JSON backend; it is still *not*
  RE/EPICS and *not* a profile import — just the shared sample-state bus.)
- **Generated scripts must obey the tenets** (one run/sample; recorded context; `{token}`
  filenames; `md={}`; generators; slow axes outermost) — because they are built from
  `smi_plans._compose`, this is automatic; do not generate raw `bp.count`/`sample_id` code.
- **Keep the spec pure data** (names/strings, JSON-serializable) — this is what makes the eventual
  qserver path additive.
- **Reuse, don't reinvent:** `SampleList` for samples, `build_axes_from_spec` for axis assembly,
  `SimBeamline` for validation, `recipes_combined` for preset starting points.

## Pointers

- **Sample bookmarks / persistent sample system:** `docs/SAMPLE_SYSTEM_PLAN.md` (the data model,
  Redis db=2 `SampleStore`, load/history lifecycle, CSV round-trip, GUI contract) — read this
  before building the Samples/bookmarks panel.
- Composition API + axes: `docs/PACKAGE_OVERVIEW.md`, `skills/composing-smi-experiments.md`,
  `src/smi_plans/_compose.py`.
- The seed of the GUI bridge: `src/smi_plans/recipes_combined.py::build_axes_from_spec` (axis
  spec → `ScanAxis` list) — the codegen renders the *text* equivalent; the validator can use the
  function directly.
- The simulated-device harness to power preview/validation: `tests/conftest.py` (`SimBeamline`,
  `sim`, `inject`) and the assertions in `tests/test_smoke.py`.
- Future backend prior art: `SWAXS_user_scripts/CDSAXS/DummyBluSky/user_scripts/bluesky_server.py`
  (ZMQ control server + device registry) — pattern for an eventual executor, kept behind the spec.
