# Wiring `smi-plans` into the SMI queueserver

> **Status: implemented in `smi-plans`; the profile-collection side is a documented handoff
> (staff apply on the QS host).** This doc explains how the plans in this repo are exposed to the
> SMI `bluesky-queueserver` (QS), what was built here, and the **exact** profile-collection edits
> to finish the wiring.

> **⚠️ PRODUCTION QS IS DEFERRED (decision, 2026-06).** Production queueserver use at SMI is **on
> hold pending a facility-level solution**, so the profile-collection handoff steps below are **not
> to be applied in production yet**. The blocker is **proposal/project metadata**: today the
> beamline sets it with `proposal_id` in the IPython session, which writes `RE.md` *in that
> process*; a QS worker is a **separate process** and would not inherit it, so runs submitted
> through the queue would carry the wrong/empty `data_session`/proposal/project. Wrapping this
> per-beamline (stamping proposal/project into every plan + carrying it on each loaded sample) was
> considered, but the beamline has decided this needs a **facility/NSLS-II-wide answer** (a shared
> proposal source the queueserver worker reads), not a local workaround. **Everything in `smi-plans`
> here (the `_qserver` surface, the `*_from_spec` wrappers, the tests) stays valid and ready** — it
> is the *deployment* that waits. Revisit when the facility provides the shared-proposal mechanism;
> see "Deferred: proposal/project metadata" below.

---

## TL;DR

- **"Wiring plans into the YAML" = getting the plan functions imported into the QS startup
  namespace.** QS does **not** read plans from a hand-authored YAML.
  `existing_plans_and_devices.yaml` is a *generated cache*: `qserver-list-plans-devices`
  introspects whatever lands in the startup namespace and writes the list. The profile's
  `user_group_permissions.yaml` `root` group already does `allowed_plans: [null]` (**allow all**),
  so any introspected plan is allowed.
- **You do NOT need to vendor/merge `smi-plans` into the profile.** Anything that makes
  `import smi_plans` succeed in the QS worker is enough. We use a **git pypi-dependency** in the
  profile's `pixi.toml` (reproducible + deployable to the QS host).
- **`smi-plans` now ships a curated QS surface:** `smi_plans._qserver`. The profile imports it with
  **one line** and regenerates the cache. Proven: `qserver-list-plans-devices
  --ignore-invalid-plans=OFF` exits 0 with **87 plans** and **zero rejections**.
- **Deferred (not blocking the above):** production rollout waits on a facility-level
  proposal/project-metadata solution — see the banner above and the dedicated section below.

---

## Background: how QS loads plans (the governing facts)

1. The SMI profile is loaded by QS via `start-re-manager --startup-dir=startup` (see the profile's
   `pixi.toml` `[feature.qs.tasks]`). The worker execs `startup/startup.py` and **introspects the
   resulting namespace** — every public *generator function* it finds becomes an "allowed plan."
2. `qserver-list-plans-devices` is the offline tool that performs exactly that introspection and
   writes `existing_plans_and_devices.yaml`. The profile already wires it as `pixi run -e qs
   qs-list`.
3. QS rejects a plan only when it cannot **reconstruct a parameter default** (e.g. a default that
   is a device object, a lambda, or a bare type like `float`) or rebuild a **type annotation**. It
   does **not** reject a plan merely for having required, annotation-free device parameters — it
   records those as free parameters.
4. **Submittable ≠ listed.** A plan is *listed* if it introspects; it is *practically submittable
   from a generic queue client* only if its required args can be supplied as JSON (numbers,
   strings, lists). A plan that needs a live ophyd object as a required arg is listed but a queue
   client has no string that becomes that object.

These four facts shape the two-tier design below.

---

## What was built in `smi-plans`

### 1. `smi_plans/_qserver.py` — the curated QS surface (the single source of truth)

The profile imports this module's public names; that is the entire QS plan surface. Editing the
exposed set means editing `_qserver.__all__` (via the helpers below) — nothing else.

**Tier 1 — direct re-exports (visibility).** Every `technique_*` `*_run`/`*_bar` preset is
re-exported as `<Letter>_<name>` (e.g. `A_nexafs_bar`, `C_temperature_ramp_run`), plus the
generator-function composition plans (`acquire`, `acquire_bar`, `manual_step`, `manual_value`,
`manual_loop`, `pause_for_user`). The re-export filter keeps **only generator-function plans**, so
it automatically drops:

- the `example*` demos (they hardcode a specific bar);
- non-plan helpers that merely live in a technique `__all__` — `energy_grid` (returns an array),
  the `*_heater` builders (return a `Heater`), the `*_dets` detector-list helpers, etc.;
- the **`technique_M` closed-loop controllers** (`autonomous_loop`/`align_loop`/`ask_tell_loop`) —
  these are plain functions that call `RE(...)` *themselves* (the one sanctioned place that
  happens), so they must never be submitted to the queue as plans. (`measure_for_agent`, a real
  generator plan, *is* exported.)

Tier-1 presets whose required args are plain data (energies, setpoints, sample columns, counts)
are immediately runnable from the queue; presets needing a live `Heater`/`dets`/callable are
listed but not practically submittable from a generic client — that is what Tier 2 is for.

**Tier 2 — `*_from_spec` string-arg wrappers (submittability).** Thin wrapper plans that take a
single **JSON-serializable `spec` dict** and resolve device *names* → live device *objects* inside
the worker. These are what a queue client / GUI should actually submit (their only argument is
data):

| Wrapper | Wraps | Spec shape (abridged) |
|---|---|---|
| `acquire_from_spec(spec)` | the full composition `acquire` | `{"name","geometry","detectors":[names],"reads":[names],"exposure_s","axes":[{type,...}],"context":{...}}` |
| `nexafs_from_spec(spec)` | `technique_A.nexafs_run` | `{"name","energies"|"edge"+"grid","detectors","flux_signal","atten":[names],...}` |
| `giwaxs_from_spec(spec)` | align + `technique_B.giwaxs_run` | `{"name","incident_angles","waxs_arc","align":name,"align_angle","sample":{coords},...}` |
| `temperature_ramp_from_spec(spec)` | `technique_C.temperature_ramp_run` | `{"name","heater":"linkam"|"lakeshore","setpoints":[degC],"soak",...}` |

`acquire_from_spec` reuses `recipes_combined.build_axes_from_spec` (the existing name→axes seam),
so the `axes` schema matches the one the GUI builder already targets
(`skills/smi-plans-gui-builder.md`). Device references are **names everywhere**; no live object
travels over the wire.

**Device resolution.** Like every `smi_plans` module, `_qserver` references beamline devices as
bare globals the profile injects at runtime. `resolve(name)` looks the (possibly dotted, e.g.
`"piezo.x"`) name up in the module's globals and raises a clear `DeviceResolutionError` if missing
(surfaced in the worker log at submit time, never a silent `None`). `DEVICE_REGISTRY` documents the
names the spec schema may use.

### 2. Two one-line fixes so the manual plans introspect

`manual_value` / `manual_axis` / `manual_loop` had `cast=float` *parameter defaults*; QS cannot
`ast.literal_eval` a bare type. Changed the default to `cast=None` (treated as `float` internally
by `_coerce`). Behavior is identical for every caller; the plans now introspect even under
`--ignore-invalid-plans=OFF`.

### 3. Tests (`tests/test_qserver.py`, 16 tests)

- the exposed surface (wrappers present; helpers/`technique_M` controllers/examples absent;
  internals not exposed; every technique export is a generator);
- `resolve` raises clearly when a device is missing;
- each `*_from_spec` wrapper **dry-runs** through the `SimBeamline` harness to exactly one
  well-formed run (names resolved to sim devices) — the queue-submittable path proven without
  hardware or a real QS.

Run: `pytest tests/test_qserver.py` (full suite: **74 passed**).

### Proof the real QS tool accepts it

```
$ qserver-list-plans-devices --startup-script <import _qserver> --ignore-invalid-plans=OFF
The list of existing plans and devices was created successfully.   # exit 0
# -> existing_plans_and_devices.yaml: 87 plans, 0 ignored;
#    acquire_from_spec / nexafs_from_spec / giwaxs_from_spec / temperature_ramp_from_spec
#    each take a single 'spec' parameter.
```

(The 9 axis *builders* — `energy_axis`, `motor_axis`, … — are intentionally **not** exported to
QS: they return `ScanAxis` objects, not plans. A queue client composes via `acquire_from_spec`;
in-session/GUI code imports the builders from `smi_plans._compose`.)

---

## The profile-collection handoff (apply on the QS host)

> **⚠️ DEFERRED — do not apply in production yet.** These steps are correct and ready, but
> production QS rollout is on hold pending the facility proposal/project-metadata solution (see the
> banner at the top and "Deferred: proposal/project metadata" below). They remain useful **now**
> for *non-production* smoke-testing (e.g. `pixi run -e qs qs-list` to keep proving the worker
> loads the curated surface) and as the ready-to-go recipe for when QS is greenlit.

> These are the **only** changes needed in
> `/nsls2/data1/smi/shared/config/bluesky/profile_collection` (remote
> `github.com/NSLS2/smi-profile-collection`). Nothing in `smi-plans` is copied in.

### Prerequisite — push the `smi-plans` branch

The pixi git-dependency resolves from GitHub, so the intended `smi-plans` branch must be pushed
first (the repo is currently `master`, **ahead of origin** by the QS-wiring commits):

```bash
cd /home/xf12id/git/smi/smi-plans
git push origin master           # or push the feature branch you want the profile to pin
```

> If you'd rather iterate without pushing each time, use the **sys.path alternative** in the
> "Dev shortcut" section below instead of the git dependency.

### Step 1 — add `smi-plans` as a git pypi-dependency in `pixi.toml`

`smi_plans` declares `dependencies = []` (bluesky/ophyd/numpy are extras), and the profile env
already provides bluesky/ophyd/numpy — so install it **plain** (no `[beamline]` extra), which
won't perturb the pinned env.

In `pixi.toml`, under the existing `[feature.profile.pypi-dependencies]` (which already has
`pvxslibs`, `ag2`), add:

```toml
[feature.profile.pypi-dependencies]
# ... existing entries ...
smi-plans = { git = "https://github.com/EliotGann/smi-plans.git", branch = "master" }
```

Then resolve the env:

```bash
cd /nsls2/data1/smi/shared/config/bluesky/profile_collection
pixi install            # updates pixi.lock with smi-plans
```

(Pin to a tag/rev instead of `branch` for production reproducibility, e.g.
`{ git = "...", rev = "<sha>" }`.)

### Step 2 — import the curated surface in the QS startup namespace

The plans must be in the namespace the **worker** introspects. Add a small startup module so it
loads for both the terminal and the QS worker. Create
`startup/smibase/zz_smi_plans.py` (the `zz_` prefix makes it load last, after devices exist):

```python
"""Expose the curated smi-plans queueserver surface into the startup namespace.

`smi_plans._qserver` references beamline devices as bare globals; importing it here (after the
device factory has populated this namespace) lets its name->device resolver find them. QS then
introspects these plans into existing_plans_and_devices.yaml. See
smi-plans/docs/QSERVER_WIRING.md.
"""
from smi_plans._qserver import *          # noqa: F401,F403
```

…and import it from `startup/startup.py` **after** the device factory block (after the
`globals().update({...})` on line 41), so the devices exist when `_qserver` resolves names:

```python
# --- smi-plans queue surface (presets + *_from_spec wrappers) ---
# Imported after the factory so the device globals exist for smi_plans' name->device resolver.
from smibase.zz_smi_plans import *        # noqa: F401,F403
```

> **Device-name check (important).** `smi_plans._qserver.DEVICE_REGISTRY` lists the names the
> `*_from_spec` specs reference (`pil2M`, `pil900KW`, `energy`, `waxs`, `piezo`, `stage.phi`,
> `att2_9`, `alignement_gisaxs_hex`, …). Confirm each exists in the SMI startup namespace with that
> exact spelling. The Tier-1 presets and `acquire_from_spec` resolve names **lazily at submit
> time**, so a missing/misnamed device raises a clear `DeviceResolutionError` only when that plan
> is run — not at startup. (Known open item from the restructure docs: `pil2M_pos` casing; the
> Huber `phi` attribute name.)

### Step 3 — regenerate the plan/device cache and smoke-test the worker load

```bash
cd /nsls2/data1/smi/shared/config/bluesky/profile_collection
pixi run -e qs qs-list      # qserver-list-plans-devices --startup-dir=startup --file-dir=startup
```

This writes `startup/existing_plans_and_devices.yaml` including the smi-plans surface, and proves
the **whole** profile (devices + smi-plans) loads headless in the worker. Expect the profile's own
~38 plans + the ~87 smi-plans plans, "created successfully".

> Keep `--ignore-invalid-plans=ON` for `qs-backend`/`qs-list` (the profile already does) as a
> safety net for the profile's *own* plans; the smi-plans surface itself is clean under `OFF`.

### Step 4 — `user_group_permissions.yaml`: no change needed

`startup/user_group_permissions.yaml` already exists and its `root` group is `allowed_plans:
[null]` (allow all) — so every introspected smi-plans plan is permitted. Only tighten this if you
later want to *restrict* which plans a given user group may submit (e.g. expose only the four
`*_from_spec` wrappers to a GUI group). Example of a restricted group:

```yaml
  gui:
    allowed_plans:
      - ":^.*_from_spec$"     # only the data-only spec wrappers
    allowed_devices:
      - null
    allowed_functions:
      - null
```

### Dev shortcut (no push, shared-disk only) — alternative to Step 1

If the QS host mounts this checkout (e.g. via the shared `/nsls2/...` Lustre path) and you want to
pick up local edits without pushing/`pixi install`, mirror the existing `smi_beamline` trick: in
`startup/startup.py`, also add `smi-plans`' `src/` to `sys.path` near the top
(`_sys.path.insert(0, "/path/to/smi-plans/src")`) **instead of** the pixi git dependency. This is
not reproducible and won't survive deployment to a host without that mount, so use the git
dependency for production.

---

## Submitting a plan from the queue (what users do)

From `queue-monitor` (the profile's `pixi run -e qsgui qserver-gui`, pointed at
`xf12id2-smi-qs1`), a user adds a plan item. The data-only wrappers take a single `spec`:

```jsonc
// plan: acquire_from_spec
{ "spec": {
    "name": "PS40nm", "geometry": "reflection",
    "detectors": ["pil2M", "pil900KW"], "reads": ["energy","waxs","xbpm2","xbpm3"],
    "exposure_s": 1.0, "scan_name": "giwaxs_Tramp_NEXAFS", "project_name": "311234",
    "axes": [
      { "type": "temperature", "values": [30,60,90], "heater": "linkam" },
      { "type": "motor", "name": "arc", "device": "waxs", "values": [0,20], "speed": 2 },
      { "type": "incidence", "values": [0.10,0.20] },
      { "type": "energy", "values": [2470,2472,2474], "flux_threshold": 50 }
    ],
    "context": { "th_axis": "piezo.th", "th0": 0.0, "flux_signal": "xbpm2.sumX" }
} }
```

The Tier-1 presets (`A_nexafs_bar`, `C_temperature_ramp_run`, …) are also listed; the ones whose
required args are plain data are runnable directly, while those needing a live `heater`/`dets`
object are best driven through the corresponding `*_from_spec` wrapper.

---

## Extending the surface

- **Expose another preset:** it is auto-exported if it is a public generator-function plan in its
  `technique_*` module's `__all__`. Nothing to do beyond that.
- **Make a preset queue-submittable (Tier 2):** add a `<name>_from_spec(spec)` wrapper in
  `_qserver.py` (resolve names with `resolve`/`resolve_all`, build a `Heater` with `_build_heater`,
  build axes with `build_axes_from_spec`), add its name to `_TIER2_WRAPPER_NAMES`, and a dry-run
  test in `tests/test_qserver.py`. The candidates next in line (they take live callables today) are
  echem (`set_potential`), humidity (`set_rh`), mapping/XRR/CD-SAXS/XPCS.
- **Restrict what a user group sees:** edit `user_group_permissions.yaml` (Step 4), not the
  package.

---

## Deferred: proposal/project metadata (the production blocker)

**Decision (2026-06): production QS at SMI waits on a facility-level solution to this.** It is
captured here so the eventual implementer has the full context; it is **not** something to solve
with a local hack.

### The problem

Today the beamline establishes the experiment's identity by running **`proposal_id(...)` in the
IPython session**. That call writes proposal/project identity into **`RE.md`** (the run-engine
metadata dict) — keys such as `data_session` / proposal / project / `data_security` — and those
keys are then stamped into every run's **start document** and used to tag the Tiled writing
clients. Crucially, `RE.md` lives **in the process that ran `proposal_id`** (the terminal's
RunEngine).

A queueserver worker is a **different process** with its **own RunEngine and its own `RE.md`**. It
does **not** run the interactive `proposal_id`, so unless something explicitly carries the
proposal/project into the worker, **queue-submitted runs would get the wrong or empty
`data_session`/proposal/project** in their start docs — a data-provenance failure (runs filed under
no/!wrong proposal).

This is independent of the plan wiring above: the `_qserver` surface and `*_from_spec` wrappers are
correct; the gap is *where the proposal/project comes from in the worker process*.

### Options that were considered (and why we are NOT doing them now)

1. **Stamp proposal/project into every plan** (e.g. have `acquire`/the `*_from_spec` wrappers read a
   shared proposal source and merge it into `md`). Workable mechanically, but it hardcodes a
   per-beamline policy into the plan library and still needs a *trustworthy shared source* of the
   current proposal that the worker can read.
2. **Carry proposal/project on each loaded sample** (put them on the `Sample`/`SampleStore` record
   so "load sample X" pulls its proposal into the run md). This couples experiment identity to the
   sample bookmark, which is the wrong place for it (one bar can be measured under different
   proposals; proposal is a *session/queue* fact, not a *sample* fact).
3. **A process-shared proposal source** (e.g. the proposal in a Redis dict both the terminal and the
   worker read, like the existing `mdsave`/db=2 pattern). This is the closest to right, but the
   *authoritative* proposal/data-session for a beamtime is a **facility concern** (it ties to the
   PASS/proposal system, data-security tags, and the Tiled/data-session contract), so SMI should
   adopt the facility's shared-proposal mechanism rather than invent a local one.

The beamline's decision: **this needs a facility / NSLS-II-wide answer** (a shared proposal source
the queueserver worker reads, consistent with how data-session/data-security are assigned
facility-wide). Production QS is deferred until that exists.

### What this means for the work in this repo

- **Nothing here is wasted or wrong.** The `_qserver` surface, the `*_from_spec` wrappers, the
  `cast` fix, and the tests are all valid and continue to pass; `pixi run -e qs qs-list` keeps
  proving the worker loads the curated plans. Only the *production deployment* waits.
- **When the facility solution lands,** the likely integration point is small and lives in the
  **profile** (a worker-side hook that seeds `RE.md` proposal/project from the shared source), plus
  possibly a thin `md`-merge in the `*_from_spec` wrappers if the policy is "stamp at plan time."
  Re-evaluate options 1/3 above against the facility mechanism at that point.
- **Sample bookmarks stay scoped to physical-sample facts** (position, alignment, history) — not
  proposal — per option 2's rejection.

> Cross-references: the sample-metadata design (`docs/SAMPLE_SYSTEM_PLAN.md`) deliberately keeps
> proposal/project OUT of the sample record for the reason above; the restructure plan's QS section
> (`docs/STARTUP_RESTRUCTURE_PLAN.md` §7) and the GUI builder skill
> (`skills/smi-plans-gui-builder.md`) both now note the production-QS deferral.
