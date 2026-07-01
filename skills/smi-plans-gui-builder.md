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

> ## ⚠️ Backend changes the GUI must reflect (2026-06, post-beamtime) — READ FIRST
> These are field-validated backend changes (see `docs/FIELD_LESSONS_BAR_PLANS.md`,
> `docs/ROADMAP.md`); the examples further down this skill were written before them and are being
> corrected. Where this block and an older example disagree, **this block wins**.
>
> 1. **Redis-first generation (the headline).** The Redis store is the preferred GUI↔profile
>    channel and is field-proven (no copy-paste). The GUI's *primary* generated call should
>    **reference stored data by NAME**:
>    - samples/holders → `load_holder("bar1")` (a `SampleList` from the store), NOT
>      `SampleList.from_columns(piezo_x=[...], ...)`. The `from_columns` paste path is a *secondary*
>      one-off/import tool.
>    - big lists (energies/angles/temperatures/times) → **named lists** by name, e.g.
>      `energies="Fe_K_XANES"` (resolved via `smi_plans.resolve_list`). The GUI's list-builders
>      become **editors** for those stored entries — see the new **Lists panel** + `NAMED_LISTS_PLAN.md`.
> 2. **Energy moves are simpler.** `energy_axis(energies, *, settle=2.0, flux_signal=, flux_threshold=)`
>    — the `energy` device now manages DCM feedback, IVU gap (flux peak), and harmonic itself. The
>    params **`max_step` / `fb_settle` / `double_set` were REMOVED** (passing them now raises
>    `TypeError`). Do NOT emit them. `settle` (post-move dwell) and the flux re-seek stay.
> 3. **Filename tokens must be real recorded keys.** A `{token}` is filled from the recorded event
>    keys (`<device>_<attr>`); a bad token now fails at **build time** in `acquire` (a clear
>    `ValueError`), and would otherwise have crashed the post-run file namer (`KeyError`). Use
>    `{piezo_x}` not `{x}`, `{energy_energy}` not `{energy}`. See the new **Filename tokens** section
>    below and `skills/naming-and-filename-tokens.md`.
> 4. **Spatial grid records relative `{x}`/`{y}`.** `spatial_grid_axes(..., center=(cx,cy))` records
>    a relative-offset Signal named `x`/`y`, so `{x}`/`{y}` are valid tokens (the absolute
>    `{piezo_x}`/`{piezo_y}` are also recorded). Without a `center` you only get `{piezo_x}`.
> 5. **The spec bridge now honors the GUI shorthands.** `recipes_combined.build_axes_from_spec`
>    accepts: energy `grid:{edge,pre,near,post}` OR `values`; spatial `x_step`/`x_n`(+`center`) OR
>    explicit `x`/`y` lists; motor `speed:"slow"`/`"medium"`/`"fast"` strings; energy
>    `flux_reseek:{threshold}`. So the `ExperimentSpec` shown below is now faithfully buildable.
> 6. **Multi-arc topology is un-blocked.** The `UnresolvableForeignKeyError` is fixed (per-(sample,arc)
>    detector staging). Default to **one run per (sample, arc)** (`giwaxs_bar`/`transmission_bar` with
>    `waxs_arc=`); offer **arc-economy** (`giwaxs_bar_arc_economy` / `multi_sample_run`) as an opt-in
>    "when arc travel dominates" (each arc its own stream; `multi_sample_run_split` is the
>    no-concurrent-runs fallback).

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
    # PRIMARY (Redis-first): reference a holder by name -> load_holder("bar1").
    "source": "holder",                  # -> load_holder(holder) from the store
    "holder": "bar1",
    # SECONDARY / one-off fallback (no store): paste columns -> SampleList.from_columns
    #   "source": "columns", "names": ["s1","s2"], "piezo_x": [...], "piezo_y": [...],
    #   "incident_angles": [0.1, 0.2],
  },

  "axes": [                              # scan stack, OUTERMOST FIRST
    {"type": "temperature", "values": [30, 60, 90], "soak": 120, "first_soak": 300},
    {"type": "motor", "name": "arc", "device": "waxs.arc", "values": [0, 20], "speed": "slow"},
    {"type": "incidence", "values": [0.10, 0.20]},   # th0 omitted -> relative/aligned-zero
    # energy: a stored-list NAME (preferred), OR a grid, OR an explicit values list.
    # NOTE: the GENERATED CODE turns "list" into resolve_list("S_K_XANES", kind="energy") (the
    # store is in the live session); the dry-run bridge (build_axes_from_spec) instead takes
    # "grid"/"values" (it has no store). The GUI should hold the resolved values for dry-run.
    {"type": "energy", "list": "S_K_XANES"},
    #   or: {"type": "energy", "grid": {"edge": 2472, "near": [-2,2,0.25], "post": [2,60,5]}},
    #   or: {"type": "energy", "values": [2470, 2475, 2480]},
    #   optional re-seek: "flux_reseek": {"signal": "xbpm2.sumX", "threshold": 50}
    {"type": "spatial", "x_step": 30, "x_n": 5, "center": [55000, 4000]},  # 5 fresh spots; a
    #   center -> relative {x}/{y} tokens (use the sample's piezo_x/y; "center" may be [cx,cy] or
    #   set context['spatial_center']); OMIT center -> absolute {piezo_x}/{piezo_y} only.
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

## The spec axis-type table (authoritative — from `build_axes_from_spec`)

Every `axes[]` entry is `{"type": <name>, ...}`. This is the exact set the bridge
(`recipes_combined.build_axes_from_spec`) accepts and the fields it reads (so the GUI's editors and
the dry-run validator agree). **This table is the source of truth**; if an older example below uses a
different field name, prefer this.

| `type` | backend axis | spec fields (bridge) | context handles |
|---|---|---|---|
| `energy` | `energy_axis` | `list` (name, generated code resolves) **or** `grid:{edge,pre,near,post}` **or** `values`; `settle`; `flux_reseek:{threshold}` or `flux_threshold` | `flux_signal` |
| `temperature` | `temperature_axis` | `values`, `soak`, `first_soak` | `heater` |
| `incidence` | `incidence_axis` | `values`, `th0` (omit → relative/aligned-zero) | `th_axis`, `th0` |
| `motor` | `motor_axis` | `name`, `device` (context key), `values`, `record`, `speed` (`"slow"`/`"medium"`/`"fast"` or int) | the named `device` |
| `spatial` | `spatial_grid_axes` | `x`/`y` (lists) **or** `x_step`+`x_n` / `y_step`+`y_n`; `center` ([cx,cy] or scalar) → relative `{x}`/`{y}`; `record_relative`; `snake` | `piezo_x`, `piezo_y`, `spatial_center` |
| `potential` | `potential_axis` | `values`, `equilibration` | `set_potential`, `potential_readback` |
| `rh` | `rh_axis` | `values` | `set_rh`, `live_rh` |
| `time` | `time_axis` | `n_frames`, `period` | `elapsed_signal` |
| `manual` | `manual_axis` | `name`, `prompt`, `values`, `record_name` | `manual_signal` |

Polygon regions are intentionally not listed as a normal axis type here. A polygon is a correlated
point list, not a rectangular product of independent axes. When `smi-acquire` stores drawn regions
in `sample.md['scan_regions']`, generated code should call `polygon_region_run(...)` or
`polygon_region_bar(...)`; keep explicit point-list scripts only for unsaved one-off polygons.

Order in the list = nesting (outermost first). The GUI ordering guardrail mirrors
`_compose._check_axis_order` (slow axes — arc/phi/temperature — outermost).

## Filename tokens (every `{token}` must be a REAL recorded data key)

The file writer / symlink workflow fills `{field}` tokens in `sample_name` from the run's recorded
event keys. **A token with no matching key fails** — at build time now (`acquire` raises a clear
`ValueError`), and historically as a post-run `KeyError` (this broke real runs: a grid axis named
`x` records key `piezo_x`, so `{x}` had no key → `KeyError('x')`). The GUI's token hint and codegen
MUST only offer/emit valid tokens. Authority: `skills/naming-and-filename-tokens.md`.

The rule: a token is one of
- a **`COMMON_TOKENS`** entry (the beamline naming preprocessor injects these even if not in `reads`):
  `{energy_energy}`, `{xbpm2_sumX}`, `{xbpm3_sumX}`, `{waxs_arc}`, `{pin_diode_current2_mean_value}`;
- an **axis `record`-Signal name**: `{incident_angle}` (incidence), `{x}`/`{y}` (spatial **with a
  center**), `{potential_v}`, `{rh}`, `{frame}`, `{energy_set}`;
- a **`<device>_<attr>`** key for a device in `reads`/`dets`: `{piezo_x}`, `{piezo_y}`, `{stage_phi}`.

Device → key mapping (the common trap is the left vs right column):

| you move/read | token to use | NOT |
|---|---|---|
| `piezo.x` (grid, no center) | `{piezo_x}` | `{x}` |
| `piezo.x` (grid **with center**) | `{x}` (relative offset) | — |
| `energy` | `{energy_energy}` | `{energy}` |
| `waxs` arc | `{waxs_arc}` | `{arc}` |
| `xbpm2` | `{xbpm2_sumX}` | `{xbpm2}` |

The GUI should **pre-validate** the chosen tokens against the axes/reads (same logic as
`_compose.validate_name_tokens`) and show the error *before* the user takes the script to beam.

## Naming UI contract from Gao/Gomez field use

The GUI needs a first-class filename/naming panel, not a free-text-only box. The field scripts show
that users repeatedly need predictable filenames for filesystem browsing, but the backend must keep
the authoritative context recorded in events and metadata. The GUI should therefore edit a structured
`name_spec` and show a live preview using `smi_plans.preview_bar_name(...)`, while still allowing an
advanced raw-token override.

### Helper functions the GUI should use

The package exports pure helpers promoted from the live `bar_plans.py` workflow:

| helper | GUI use |
|---|---|
| `preview_bar_name(...)` / `preview_name(...)` | Render the template and filled fake example in the naming panel. |
| `bar_name_tokens(...)` | Convert structured naming controls into `name_tokens` for generated code. |
| `apply_name_prefix(...)` | Show exactly how a prefix lands before the sample name. |
| `adjust_holder_positions(...)` / `adjust_bar_positions(...)` | Dry-run-first bulk offset/set tool for all samples on a holder. |
| `sort_holder_by_name(...)` / `sort_bar_by_name(...)` | Dry-run-first run-order cleanup for a holder. |

These helpers are intentionally not plans and do not move hardware. They can be called by the GUI
against a live `SampleStore.from_redis()` connection or an offline `SampleStore({})` during GUI
development.

### Structured `name_spec` editor

The GUI should expose the common knobs as widgets and store them as a dict:

```python
name_spec = {
    "name_prefix": "Gao",
    "include_energy": False,
    "include_exposure": True,
    "include_arc": True,
    "arc_fmt": "waxs_{:.0f}",
    "include_incidence": False,
    "extra_tokens": ["px_{piezo_x:.1f}", "py_{piezo_y:.1f}", "pz_{piezo_z:.1f}"],
}
```

Recommended UI fields:

| UI field | `name_spec` key | Notes |
|---|---|---|
| Prefix before sample | `name_prefix` | Field examples: `Gao`, `YZhang`, `SYang`, `Standard`; keep it literal text. |
| Include energy | `include_energy` / `energy_token` | Default token is `{energy_energy}eV`; Gao transmission snapshots often turned this off. |
| Include exposure | `include_exposure` / `exposure_token` | Field scripts wanted `exp_{exposure_s}s`; only enable when the plan records `exposure_s`. |
| Include WAXS arc | `include_arc` / `arc_fmt` | Gao used `waxs_{:.0f}` for filesystem-friendly arc labels. |
| Include incidence | `include_incidence` / `incidence_token` | Default on for GIWAXS/incident-angle scans, off for pure transmission. |
| Include grid offsets | `grid` | Only valid when centered spatial grid records relative `{x}`/`{y}`. |
| Position tokens | `extra_tokens` | Offer checkboxes for `px_{piezo_x:.1f}`, `py_{piezo_y:.1f}`, `pz_{piezo_z:.1f}`. |
| Advanced extra tokens | `extra_tokens` | Validate every `{field}` before allowing code generation. |

The live preview should show both:

```text
TEMPLATE: Gao_s1_exp_{exposure_s}s_waxs_15_px_{piezo_x:.1f}_py_{piezo_y:.1f}_pz_{piezo_z:.1f}_
EXAMPLE : Gao_s1_exp_1.0s_waxs_15_px_55000.0_py_4000.0_pz_-1200.0_
```

Use `preview_bar_name(sample, arc=..., grid=..., incidence=..., exposure=..., name_spec=...)` for
this. Do not reimplement the formatting rules in the GUI.

### Presets from field groups

The GUI should ship naming presets as editable starting points. These are not new backend defaults;
they only pre-fill `name_spec`.

| preset | Source pattern | `name_spec` |
|---|---|---|
| Gao transmission positions | 2026 pass 318919 command log | `{"include_energy": False, "include_exposure": True, "arc_fmt": "waxs_{:.0f}", "extra_tokens": ["px_{piezo_x:.1f}", "py_{piezo_y:.1f}", "pz_{piezo_z:.1f}"]}` |
| Gomez resonant WAXS | legacy sulfur/calcium edge scripts | include sample, `{energy_energy}eV`, WAXS arc, and beam monitor token such as `xbpm{xbpm3_sumY}` only if that exact key is recorded; prefer `xbpm2_sumX` if using the standard reads. |
| GIWAXS energy | mature bar wrapper | include `{energy_energy}eV`, `ai{incident_angle}`, WAXS arc, optional fresh-spot/grid offsets. |
| Simple transmission snapshot | repeated Gao/YZhang/SYang/Standard snapshots | omit energy, include exposure, include WAXS arc, include absolute piezo position tokens. |

When a preset mentions a token not known to `COMMON_TOKENS`, an axis record Signal, or a readable
device key, the GUI must either add the corresponding read or mark the token invalid. In particular,
Gomez legacy scripts baked `xbpm3.sumY.value` into names by hand; the GUI must not do that. It should
record the BPM device and use the real recorded key if available, or omit that token.

### Holder cleanup tools in the GUI

The Gao workflow also exposed two non-naming operations that belong in the Samples/Holders panel:

| operation | GUI behavior |
|---|---|
| Bulk adjust holder positions | Call `adjust_holder_positions(holder, delta=..., absolute=..., base=..., dry_run=True)` first and display the before/after table. Require an explicit second click to commit with `dry_run=False`. |
| Sort holder by sample name | Call `sort_holder_by_name(holder, dry_run=True)` and show current order versus proposed order. Require explicit commit. |

These are high-value during beamtime because they replace ad-hoc edits after a visual alignment pass.
Keep them dry-run-first, auditable, and scoped to a holder. They write `refined` or holder
`sample_ids`; they must not alter `nominal` sample definitions unless a future UI explicitly adds a
separate nominal-edit workflow.

### General Gomez/Gao guidance to encode as UI affordances

The unabsorbed guidance is mostly workflow, not new code:

| field lesson | GUI affordance |
|---|---|
| Users run whole-night sequences across proposals/projects. | Support saving/loading run-book specs and grouping generated plan calls, but do not solve proposal switching locally; proposal remains session/facility context. |
| Samples were often represented by separate `sample_list`, `x_list`, `y_list`, `z_list`. | Prefer holder/sample-table editing in `SampleStore`; show validation that names and positions are linked by sample id, not parallel-list order. |
| Users manually checked and nudged sample locations before scans. | Provide holder dry-run movement preview, bulk `adjust_holder_positions`, and an explicit “commit refined positions” path. |
| Resonant WAXS/NEXAFS scans used fresh spots by stepping `piezo.y` with energy. | Expose fresh-spot direction/step as scan options and reflect it in event-count/dose preview. |
| Legacy scripts hand-read BPMs and motor positions into filename strings. | Replace with token checkboxes backed by recorded keys; never generate `.value`, `.get()`, or `.position` into filenames. |
| Retired detector names (`pil300KW`) and old WAXS move forms appear in legacy scripts. | Device registry should only offer current names (`pil900KW`, `pil2M`, `waxs.arc`) and flag unknown legacy names during import. |
| `det_exposure_time(...)` must be run as a plan. | Generated code should use backend plan `t=` options or `yield from det_exposure_time(t, t)`, never a bare function call. |

Do not treat group-specific presets as hardcoded defaults. They are starting templates the user can
edit per beamtime.

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
from smi_plans import load_holder, resolve_list      # Redis-first: reference stored data by name
from smi_plans.technique_C_temperature import linkam_heater

# Samples by HOLDER NAME from the store (Redis-first; no copy-paste of coordinate lists).
# (Fallback for a genuine one-off: SampleList.from_columns(names=..., piezo_x=..., ...).)
bar = load_holder("bar1")

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
    cx, cy = s.runnable_position().piezo_x, s.runnable_position().piezo_y
    return [
        temperature_axis(heater, [30, 60, 90], soak=120, first_soak=300),
        motor_axis("arc", waxs.arc, [0, 20], speed=SPEED_SLOW),
        # th0=None -> RELATIVE / aligned-zero: anchor incidence to wherever the pre-run `align`
        # left theta (do NOT pre-read piezo.th.position here -- axes_for runs BEFORE align).
        # The recorded `incident_angle` pseudo-axis is the true relative angle (-> {incident_angle}).
        incidence_axis(piezo.th, None, s.incident_angles or [0.1, 0.2]),
        # energies by NAME from the store (resolve_list); or an explicit list. The energy device
        # owns gap/feedback/harmonic -- no max_step/fb_settle/double_set.
        energy_axis(resolve_list("S_K_XANES", kind="energy"),
                    flux_signal=xbpm2.sumX, flux_threshold=50),
        # 5 fresh spots; center=(cx,cy) so the filename records relative {x}/{y} offsets.
        *spatial_grid_axes(x_motor=piezo.x, x=[cx + i*30 for i in range(5)],
                           center=(cx, cy)),
    ]

det_exposure_time(1.0, 1.0)
# RUN THIS:
RE(acquire_bar(bar, dets, axes_for, reads=reads,
               align_for=align,                       # PRE-run alignment (opens its own runs)
               setup_for=lambda s: setup(), geometry="reflection",
               scan_name="giwaxs_Tramp_NEXAFS", md={"edge": "S_K"},
               # tokens must be REAL recorded keys: {incident_angle} (axis), {energy_energy}
               # (device), {x} (spatial relative offset). acquire validates these at build time.
               name_tokens=["ai{incident_angle}", "{energy_energy}eV", "x{x}"],
               baseline_for=lambda s: [thickness]))
# ================================================================================
```

Generator requirements:
- Emit only the imports actually needed (track which builders/presets the spec uses).
- Emit the samples from the samples block: **`load_holder("bar1")` when `source:"holder"`** (Redis-
  first), else `SampleList.from_columns(...)`. Choose `acquire` (single sample) vs `acquire_bar`
  (multiple, one run per sample — the DEFAULT for a bar) vs `multi_sample_run` /
  `giwaxs_bar_arc_economy` ONLY if the user explicitly opts into **arc-economy** (multiple runs open
  at once; use when arc travel dominates). Arc-economy is un-blocked (per-(sample,arc) staging,
  per-arc streams); `multi_sample_run_split` is the no-concurrent-runs fallback.
- Resolve a stored-list reference: an axis `"list":"NAME"` emits `resolve_list("NAME", kind=...)`.
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
   - **Positioning contract:** the GUI writes each sample's `nominal`/`refined` `Position`
     (frame `holder`/`lab`); the plans MOVE from `runnable_position()` (refined else nominal) via
     `goto_sample`. Field names + frame must match `_samples.Position` / `SAMPLE_SYSTEM_PLAN.md`
     (`piezo_x/y/z/th`, `stage_x/y/z/theta/chi/phi`). The legacy flat `piezo_x` columns are a
     fallback only.
4b. **Lists (named scan inputs):** a panel beside Samples/Holders to browse/edit/add **named lists**
   by kind — `energy` (edges; the existing edge/energy list-builder becomes this editor),
   `incidence`, `temperature`, `time` — persisted to the `ListStore` (Redis db=2, prefix
   `swaxslists`). An axis then references a list **by name** (`{"type":"energy","list":"S_K_XANES"}`
   → generated `resolve_list("S_K_XANES", kind="energy")`). The GUI holds the materialized values for
   dry-run. See `docs/NAMED_LISTS_PLAN.md`.
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
- Filename-token hint + **pre-validation**: show which `{tokens}` the chosen axes/reads make
  available (from `_core.COMMON_TOKENS` + axis record-Signal names + `<device>_<attr>` of
  reads/dets), and **validate the chosen name** with the same logic as
  `_compose.validate_name_tokens` so a bad token (`{x}` without a centered grid, `{energy}` instead
  of `{energy_energy}`) is caught in the GUI, not at beam. See the **Filename tokens** section above
  and `skills/naming-and-filename-tokens.md`.

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
