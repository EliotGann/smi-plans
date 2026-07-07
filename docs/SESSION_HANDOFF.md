# Session Handoff — SMI profile-collection restructure

> **Purpose:** resume this work on another machine. Captures exactly where we are, what's
> committed (and where), what's verified vs. not, and the next concrete steps.
> **Last updated:** Phase 5 — all factory-owned live instance construction has been moved out of
> `startup/smibase/` and into `src/smi_beamline/instances/`. The profile collection branch is
> `phase-5-package-startup-cleanup`; latest pushed commit is `d948b9f`. The legacy
> `startup/smibase/` package has been removed entirely. The `bsui` console is working well and
> `pixi run test-hardware` has passed on the beamline.

---

## Latest session (Phase 5 — package startup cleanup) — TL;DR

Branch **`phase-5-package-startup-cleanup`** in the profile collection. **Pushed** to GitHub as
`httporigin/phase-5-package-startup-cleanup`. Latest commits:

```
d948b9f startup: restore colored prompt fragments
832f8eb startup: protect prompt token from namespace merge
01a751c startup: use version-safe prompt tokens
bb59e0a startup: inline remaining smibase bootstrap
6f86938 startup: remove migrated smibase shims
aaeaf38 docs: record phase 5 live verification
c81345d instances: finish migrating startup modules
cab86f0 instances: migrate startup modules into package
```

This is a post-Phase-4 structural cleanup. Phase 4 already made the profile QS-worker-loadable; Phase
5 is moving remaining live beamline source out of `startup/` so `startup/` becomes only the execution
bootstrap/compatibility layer.

Completed in this batch:

- Converted `src/smi_beamline/instances.py` into the package `src/smi_beamline/instances/__init__.py`.
- Migrated all factory-owned instance-construction modules from `startup/smibase/` to
  `src/smi_beamline/instances/`: `amptek`, `attenuators`, `beam`, `beamstop`, `bladecoater`, `crls`,
  `electrometers`, `energy`, `ioLogik`, `linkam`, `machine`, `manipulators`, `mirrors`, `motors`,
  `pilatus`, `prosilica`, `shutter`, `slits`, `suspenders`, `waxschamber`, `xbpms`.
- Replaced each migrated `startup/smibase/*.py` file with a compatibility shim during migration,
  then deleted those temporary shims after confirming external user scripts use namespace devices
  rather than `smibase.*` imports.
- Inlined the remaining `base.py` / `base_dev.py` bootstrap code into `startup/startup.py`, moved
  the `zz_smi_plans.py` wiring helper into `startup/__init__.py` as `wire_smi_plans()`, and deleted
  the legacy `startup/smibase/` package entirely.
- Documented the old `base.py` and `base_dev.py` responsibilities directly in `startup/startup.py`
  and `startup/README` for DSSI maintainers used to conventional NSLS-II profile layouts.
- Updated the factory `DEVICE_MODULES` entries to import migrated device groups from
  `smi_beamline.instances.*`; the factory no longer imports device groups from `smibase.*`.
- Added profile-local handoff doc `docs/PHASE_5_PACKAGE_MIGRATION.md`.
- Adjusted live hardware smoke checks so shutters require only essential status/open/close PVs and
  WAXS arc readback connects only the arc motor, not the full detector tree.
- Repointed most package-internal plan/helper imports away from `smibase.*` shims. The remaining
  `src/` reference to `smibase.base` is the bootstrap-owned `mdsave` import used by the
  beam-snapshot helper.

Verification recorded in the profile-local handoff:

```
pixi run test-unit      # 110 passed
pixi run test-sim       # 181 passed
pixi run test-hardware  # passed
```

After deleting the temporary `startup/smibase` shims, offline tests were rerun:

```
pixi run test-unit      # 110 passed
pixi run test-sim       # 181 passed
```

After removing `startup/smibase/` entirely and inlining the bootstrap:

```
pixi run test-unit      # 110 passed
pixi run test-sim       # 181 passed
pixi run -e qs qs-list  # plan/device list created successfully
timeout 35s pixi run start  # reached the custom SMI IPython prompt successfully
```

The `bsui` console has also been confirmed working well on the beamline after the full instance
module migration.

There are no remaining `startup/smibase` modules.

Recommended next work: pull the latest profile branch on the beamline computer, rerun
`pixi run test-hardware`, and launch `bsui` once as the final live smoke before merging the branch.

---

## Latest session (Phase 4 — QS worker-aware + plans/) — TL;DR

Branch **`phase4-factory-package`** (off the untouched `phase3-device-cleanliness`). **Not pushed.**
**Hardware-confirmed** for the terminal path; **the headless worker load is verified with real QS
tooling.** Safe suite: **128 passed, 4 skipped, 15 deselected.**

Commits since the last handoff (newest first; the first three were made by staff):
```
b25ee1f Phase 4: relocate the pure-plan modules to src/smi_beamline/plans/
fef747e QS: fix the 2 plans with device-object defaults; correct the pixi qs tasks
80e4628 Phase 4: make the bootstrap worker-aware -- profile loads headless in the QS worker
77699d0 Set tiled clients to point at acquisition nodes                       (staff)
46ac465 Sample store: Redis db=2 link + _context seam accessor                (staff)
2fb9db3 Attenuators: energy-aware attenuation factor + foil auto-select       (staff)
```

### THE milestone: the profile loads headless in the QS worker
`base.py` / `base_dev.py` / `utils.py` now detect the worker via
`bluesky_queueserver.is_re_worker_active()` and guard the IPython/interactive-only code:
- **`configure_base` gets an explicit namespace** — the live IPython `user_ns` in the terminal, a
  plain `{}` dict in the worker (there is no IPython namespace there). It populates RE/db/bec/sd
  into that dict; we re-export **RE/bec/sd/db as MODULE globals** so `from smibase.base import *`
  lands them in the exec'd profile namespace — exactly where the worker reads `RE`
  (`worker.py: self._re_namespace.get("RE")`). `magics`/`mpl` are off in the worker.
- **Interactive Tiled READING clients (Duo push, `username=None`/`from_uri` w/o key) are skipped in
  the worker** (`db=None` there); the API-keyed WRITING clients are unchanged.
- **Prompt (`ip.prompts`), `configure_olog`, and `hardware_check()`'s namespace lookup** are guarded
  behind "real IPython and not worker."
- Renamed the misleading `_smiclasses_context` alias → `_seam`.

**Verified (real QS tooling, not a mock):** `qserver-list-plans-devices --startup-dir startup`
reports **"created successfully"** — the whole profile loads in the plain-Python worker, **139
devices + 38 plans introspected, 0 ignored**, no `get_ipython` crash / Duo hang / prompt error.
(Run it any time with `pixi run -e qs qs-list`.)

### QS plan-signature fixes
The two plans QS initially rejected (a device OBJECT as a parameter default can't be serialized):
- `fast_align_procedure(detector=pil2M, ...)` → `detector=None`, resolve to `pil2M` inside.
- `bisection_search_plan(motor=piezo.y, ...)` → `motor=None`, resolve to `piezo.y` inside.
No-arg calls behave identically. After this, **0 plans are ignored** (was 36+2 → 38 valid).
A repo-wide scan found no other device-object defaults.

### pixi `[feature.qs.tasks]` corrected
- `qs-backend`: `--profile-dir=.` (errored on this QS 0.0.23) → `--startup-dir=startup
  --ignore-invalid-plans=ON`.
- new **`qs-list`**: the headless QS-readiness smoke test (`qserver-list-plans-devices`).

### Relocation: pure-plan modules → `src/smi_beamline/plans/`
Per the "move just the plan modules" decision, the 4 smibase modules that build **no device
instances** (only plans/functions) were `git mv`'d into the package:
`{alignment, config, humidity_cell, utils}` → `src/smi_beamline/plans/`. Their relative
`from .pilatus import ...` etc. (which pointed within `smibase`) became absolute
`from smibase.X import ...` (they import the live instances from `smibase`); `alignment`↔`utils`
stays relative. Factory `DEVICE_MODULES` updated to load them from `smi_beamline.plans.*`.

### Current layout
```
src/smi_beamline/
  devices/      24 device-CLASS modules (import-clean)
  plans/        4 pure-plan modules (alignment, config, humidity_cell, utils)   <- new
  instances.py  make_devices(context): the factory + Option-C timed loader
startup/
  smibase/      24 INSTANCE-BUILDER modules (build pil2M/energy/... + baselines) -- stay for now
  startup.py    entry point: src on path -> bootstrap (base/base_dev) -> make_devices() -> globals()
```

### Staff commits folded in (context)
- `2fb9db3` energy-aware attenuation factor + foil auto-select (new `devices/attenuators.py`,
  `attenuator_data.py`, `_config.py` keys, tests).
- `46ac465` persistent **sample/holder store** on Redis db=2 (`samplestore`) + `_context`
  `get_sample_store()` accessor (same `{}`-fallback pattern as `mdsave`); `base.py` injects it.
- `77699d0` Tiled clients tagged `tiled-qos: acquisition`.
All green in the 128-passed suite.

### NOT done — remaining QS work is DEPLOYMENT/CONFIG, not code (deferred per staff)
The code is QS-ready (loads headless). To actually RUN a queue:
1. **`user_group_permissions.yaml`** with a `root` group — without it the manager runs but clients
   cannot submit plans. Author from a template (place in `startup/`).
2. **A queue/state Redis on `localhost:6379`** (SEPARATE from the metadata Redis) — the manager
   (`start-re-manager`) won't even start without it. No `redis-server` binary is in the pixi envs
   yet (`qs-backend` failed here only at this step; env-open itself is proven via `qs-list`).
3. **Worker mode decision (Q-QS-worker):** plain-Python (pure queue) vs **IPython-kernel**
   (`--use-ipython-kernel ON`: queue + a Jupyter console you can attach to the SAME RunEngine).
   Recommended: IPython-kernel for SMI. NB: in the IPython-kernel worker `get_ipython()` returns a
   REAL kernel, so revisit the `base.py` guards (they currently key off `is_re_worker_active()`,
   which is True in BOTH worker modes — that is correct, but confirm prompt/magics behavior in the
   kernel).
4. **Deployment infra** (production only): systemd units / ansible (the `pixi.toml` comment), an
   always-on deployed queue-Redis, the httpserver behind auth.

### Optional structural tidy-ups still deferred (low value)
The 24 `smibase` instance-builder modules remain in `startup/smibase/`; converting them to explicit
`make_X(context)` builders in `instances.py` (plan §3) is the remaining structural work — not needed
for QS. Also still deferred: `config/pvs.py` PV-string extraction; `detectors.py`/`motion.py` file
merges; `pip install -e` the package (currently `src/` is added to `sys.path`).

---

## (Historical) earlier Phase 4 follows

---

## Latest session (Phase 4 — factory + package) — TL;DR

Own branch **`phase4-factory-package`** (off the completed `phase3-device-cleanliness`, which is
**untouched** — instant rollback). **Not pushed.** **Hardware-confirmed by staff** (profile loads
via the factory with the timed display; scan + baseline OK; suspenders install; bec accessible).
Safe suite: **95 passed, 4 skipped, 15 deselected**.

Phase-4 commits (newest first):
```
bafea58 Step 4b: repoint imports to smi_beamline.devices; remove the smiclasses shim
0358f6a       drop redundant per-file "Loading X" prints (factory's timed line replaces them)
8012e75 Step 3:  switch the live boot path to the factory (timed loader goes live)
8bd6418 Step 2c: add make_devices(context) factory with Option-C timed loader
0fb785a Step 2b: convert RE/bec/db grabs to seam injection (grabs now only in base.py)
39c21f7 Step 2a: inject sd/bec/db into the seam; baseline_register replaces 16 sd-grabs
31616d5 Step 1:  move device classes to src/smi_beamline/devices (with smiclasses shim)
```

### Decisions taken (locked)
- **Package lives IN this repo** at `src/smi_beamline/` (not a separate repo). `startup/startup.py`
  stays the entry point that IPython/QS exec; it puts `src/` on `sys.path`.
- **One file per current module** (`devices/pilatus.py`, `devices/energy.py`, …) — no
  `detectors.py`/`motion.py` merges (kept the diff small + git history 1:1 via `git mv`).
- **Factory style = "orchestrate imports (timed)"**: `make_devices` imports the device modules in
  order, times each, reports ok/fail; modules still build at import (grabs gone), the factory
  owns/sequences/times it. Per-module `make_X()` conversion can happen later without changing the API.
- **Loader display = Option C** (per-module ok/fail + timing + a ✓/✗ summary).

### New layout
```
src/smi_beamline/
  __init__.py
  devices/            # the 24 former smiclasses modules (CLASSES only, import-clean)
    __init__.py
    _context.py       # the dependency seam (now also carries sd/bec/db + baseline_register)
    _config.py, pilatus.py, energy.py, bimorph.py, manipulators.py, ...
  instances.py        # make_devices(context): the FACTORY + Option-C timed loader
startup/
  smibase/            # the instance/logic modules (build instances at import; plans; mode logic)
  startup.py          # bootstrap: src on path -> import base/base_dev -> make_devices() -> globals()
```

### What landed, step by step
- **Step 1 (`31616d5`)** — `git mv` the 24 `smiclasses` modules to `src/smi_beamline/devices/`
  (history preserved); pilatus's 3 absolute `smiclasses.` imports → relative. A temporary
  `startup/smiclasses/` shim re-exported the new location so nothing else changed. `startup.py`
  puts `src/` on `sys.path`; `pyproject.toml` became a src-layout `smi-beamline` package.
- **Step 2a (`39c21f7`)** — extended the `_context` seam with `sd`/`bec`/`db` injection +
  `get_sd/get_bec/get_db` + **`baseline_register(*devices)`** (no-op when unconfigured). `base.py`
  injects them. **16 `sd = get_ipython().user_ns['sd']; sd.baseline.extend(...)` sites → 
  `_context.baseline_register(...)`.** Also fixed the long-standing intermittent test flake
  (`test_two_button_shutter_unified_and_polarity` raced the nslsii retry timer; now polls).
- **Step 2b (`0fb785a`)** — added `get_re()`; converted the remaining `RE`/`bec`/`db` grabs in
  `base_dev`/`config`/`suspenders`/`alignment`/`utils` to the seam; dropped a dead `get_ipython`
  import in `beam`. **Result: the only import-time `get_ipython().user_ns` grabs left are the 5 in
  `base.py`** (configure_base, RE, bec, sd, configure_olog). (`utils.hardware_check()` has one grab
  inside the function body — runs only when called, doesn't block headless import.)
- **Step 2c (`8bd6418`)** — `smi_beamline/instances.py` with **`make_devices(context)`**: imports
  the device modules in dependency order, times each, prints the Option-C report, collects every
  public name into one namespace dict; continue-on-error by default (`halt_on_error=True` to stop).
  `DEVICE_MODULES` = the historical `startup.py` order MINUS `base`/`base_dev` (the bootstrap).
- **Step 3 (`8012e75`)** — **switched the live boot path**: `startup.py` now runs the bootstrap
  (`from smibase.base/base_dev import *`, which create RE/sd/bec/db + wire the seam), then calls
  `make_devices(ctx)` and `globals().update(...)` to merge the built instances/plans into the user
  namespace. Verified the factory+bootstrap cover EXACTLY the same 27 modules as before.
- **`0358f6a`** — removed the now-redundant `print(f"Loading {__file__}")` from the 24 device
  modules (the factory's timed line replaces them); kept them in `base.py`/`base_dev.py` (pre-factory
  bootstrap feedback during the slow Tiled/Duo/Redis phase).
- **Step 4a/4b (`bafea58`)** — verified **all 24 device modules import fully headless** (no
  IPython/session — the factory payoff). Then repointed every `from smiclasses.X import Y` in
  smibase/startup/tests to `smi_beamline.devices`, renamed `test_smiclasses_import.py` →
  `test_devices_import.py`, and **deleted the shim** (`import smiclasses` now ModuleNotFoundError).

### What is verified
- **Hardware:** the live profile loads via the factory with the timed display; a count scan runs;
  the baseline populates and records; suspenders install; `bec`/`db` resolve. (A full alignment run
  was deferred by staff but uses the same confirmed `bec`.)
- **Offline:** safe suite 95 passed; every `smi_beamline.devices` module imports with
  `get_ipython() is None`; the seam degrades gracefully headless.

### NOT done — the remaining QS work (next session)
- **`base.py` is still IPython-coupled** (5 grabs: `configure_base(get_ipython().user_ns, ...)`,
  `RE`/`bec`/`sd` from user_ns, `configure_olog`; plus the prompt, Duo push, Tiled/Redis at import).
  For a **headless QS boot**, `base.py` must become worker-aware: pass the real namespace / guard
  the IPython-only bits with `is_re_worker_active()` / `is_ipython_mode()`, and move the
  prompt/Duo/suspender-install side effects behind those guards. This is the genuine **Q-QS** step.
- **`utils.hardware_check()`** has one in-function `get_ipython().user_ns` (terminal diagnostic) —
  guard it for the worker.
- **The 4 QS-specific artifacts (plan §7.3)**: `existing_plans_and_devices.yaml`,
  `user_group_permissions.yaml` (needs a `root` group), plan-signature validation of the profile's
  own plans, and deployment infra (Redis for the queue + systemd/ansible).
- **Optional tidy-ups (deferred, low value):** per-module `make_X(context)` builders; the
  `config/pvs.py` PV-string extraction; the `detectors.py`/`motion.py` file merges; `pip install -e`
  the package into the pixi env (currently `src/` is added to `sys.path` instead).

### Rollback
Phase 4 is entirely on `phase4-factory-package`. To abandon it: `git checkout
phase3-device-cleanliness`. To revert only the boot switch but keep the move: restore the flat
`from smibase.X import *` list in `startup.py`.

---

## (Historical) Phase 3 follows

---

## Latest session (Phase 3 — Redis-config workstream) — TL;DR

Continues on **`phase3-device-cleanliness`** (still **not pushed**; remote
`github.com/NSLS2/smi-profile-collection`). **All hardware-confirmed by staff** (energy moves,
bimorph save/load round-trip, Redis persistence across restart). Safe suite: **85 passed,
4 skipped, 15 deselected**.

Redis-config commits (newest first):
```
1313a4f Pilatus: replace hand-written mdsave persist walls with persist_from_signals
8a7a2d1 Bimorph: suppress the controller's spurious CA put-callback warnings during writes
f682924 Bimorph: named voltage states (save/load) + correct stage-then-apply mechanism
b6aaef4 Redis-config: helpers + migrate energy IVU offsets & bimorph default voltages
```

### Decision recap (from staff)
- **Use the existing `mdsave` (RedisJSONDict) as-is** — no Redis-server-level changes; trust it to
  persist. Anything that needs to touch Redis was verified by hardware tests in the terminal.

### What landed
- **`smiclasses/_config.py` (NEW)** — the generalized helper the plan (§8.3) called for:
  - **`CONFIG_KEYS` registry**: every persistent-config key, its default (= the value previously
    hardcoded, so behavior is unchanged), and a one-line description. One documented place.
  - **`load()` / `load_array()`**: seed from `mdsave.get(key, default)`; off-beamline
    (`_context.get_config()` → `{}`) falls back to the default, so classes stay importable.
  - **`persist()` / `persist_from_signals()`**: write config Signals back to `mdsave`; reject
    unregistered keys; normalize numpy→list.
  - **Storage = JSON (orjson)**: sequences are stored as lists and **always read back as lists**
    (numpy writes but reads back as a list), so tables are kept as lists and `np.asarray`'d at use.
- **energy.py** — the IVU-gap offset table (`e_exp`/`off_exp`, two hardcoded `np.array`s inside
  `energy_to_gap`) → two `kind="config"` Cpt Signals (`ivu_gap_offset_energies_eV`/`_values_um`)
  seeded from `mdsave`. `energy_to_gap(8980)` verified bit-identical to the old path.
- **bimorph.py** — the default HFM/VFM voltage tables + the `-80` low-div offset → `kind="config"`
  Cpt Signals seeded from `mdsave`. Commanded voltages verified unchanged.
- **Pilatus** — replaced the ~9 hand-written `mdsave[k]=sig.get()` persist lines
  (`save_all_offsets`/`save_rod_position`/`save_pd_position`) with `persist_from_signals`; the
  `saxs_*` keys are now registered. The nested `distance_calibration` LUT is left as-is.

### Bimorph named states (`save_bimorph`/`load_bimorph`) — the feature
A convenience layer for the previously hard-to-use bimorph, persisted in `mdsave` under
`bimorph_states = {name: {"hfm":[16 V], "vfm":[16 V]}}`. Plans (all `RE()`-able) in
`smibase/mirrors.py`: `save_bimorph(name)` (snapshot both mirrors' `GET-VOUT` + sync targets),
`stage_bimorph(name)` (stage only, no motion), `apply_bimorph()`, `load_bimorph(name)` (stage +
apply + wait), `list_bimorph_states()`, `delete_bimorph_state(name)`.

**The controller (CAENels) mechanism — hard-won, verified on hardware (correct this in any future
work):**
- `SET-VTRGT<n>` stages a per-channel target — **does NOT move**; `GET-VTRGT<n>` reflects it after ~1 s.
- **`SET-ALLTRGT = 1` is the APPLY trigger** — it ramps the staged targets onto the outputs (each
  `GET-STATUS<n>` goes `On → Busy → On`). It is **not** "broadcast a value to all targets"; the
  earlier all-to-0 incident was *applying* targets that were staged at 0.
- `SET-TRGTAPPLY` ("Apply a VTarget") is a **different, per-channel** command — **not used**.
- `GET-STATUS<n>` (`"On"`/`"Busy"`) is the **done signal**: `apply_and_wait` polls it until no
  channel is busy. There is no reliable put-completion.
- These write records have a **broken put-callback**: the put SUCCEEDS but the callback fails, so
  the CA C-library prints `CA.Client.Exception: Channel write request failed` to stderr (not
  Python logging). `smiclasses/bimorph.py::_quiet_ca_messages()` suppresses it **only** around the
  bimorph writes (via `ca.disable/enable_ca_messages`), restoring on exit; other devices unaffected.
- Per-channel Cpts are generated by a class-body loop (`ch<n>`, `ch<n>_trg`, `ch<n>_trg_rb`,
  `ch<n>_status`) — flat, so the historical `hfm_voltage.ch0` access still works — and the old
  `set_target` dir()-walk bug (matched `"trg"` substring, would have hit the new RO `_trg_rb`) was
  fixed by routing through `set_targets`.

### NOT done (Redis-config remainder — low priority)
- `STG_pseudo` rotation centers are **obsolete** (Huber swap) — not migrated; leave/remove later.
- `RE.md`-as-config in `beam.py:423-425` (`beamline_sample_environment`/`beamline_attenuators`) —
  still an anti-pattern; not migrated (staff have not flagged it as urgent).
- Static PV strings remain Python (correct — they are code, not runtime state).

---

## (Historical) Phase 3 device-cleanliness follows

---

## Latest session (Phase 3 — device cleanliness) — TL;DR

Branch **`phase3-device-cleanliness`** (off `phase2-device-debt`), **not pushed**. Remote is
`github.com/NSLS2/smi-profile-collection`. **All changes hardware-confirmed working by staff**
(BDM moves, attenuators, prompt, valves/shutters). Safe suite: **71 passed, 4 skipped,
15 deselected** (`pixi run -e test test`).

Phase-3 commits (newest first):
```
1025eb5 M3/M4: unify on one TwoButtonShutter with explicit per-valve polarity; GV7 aliased
e8ac113 H4: make BDM stage a positioner with real move-completion (readback-based)
363c064 M2: remove orphan dcm_theta (duplicate object on motor m65)
1a252ee M6: fix LakeShore D-gain PV + make output1..4 proper Components
a0257a8 test: prove the old multi-foil idiom is already settled/safe via per-foil set()
3b76cdc Workflow: richer IPython prompt + BEAM_DOWN suspender bypass
f9fb1cd Attenuator: settle_time 0.6->2 (hardware-tuned) + watchdog margin guard
32b0568 Fix attenuator bounce-back: settle-debounce + aggregate Attenuators device
66713c9 H3: rewrite Attenuator.set() as a non-blocking, retrying, safe-failing set
d21b69a docs: document the integration (fake-IOC) test tier in TESTING.md
377261b Add fake-IOC integration test tier (caproto) for energy move choreography
56e4409 H1/H2: non-blocking feedback + IVU-brake handling; fix small_move restore
5150741 H5/H6: deprecate sample_id and get_scan_md/get_more_md (keep working)
ef31a53 H5: alignment scans use run-scoped sample_name (drop RE.md mutation)
```

### What landed (by audit ID)

- **H1/H2 (`56e4409`)** — `Energy.set()` no longer blocks on DCM feedback toggling; the IVU brake
  is handled via `put(1, wait=True)` on the calling thread (the ophyd set-thread lacks a CA
  context, so `.set().wait()` there was wrong); fixed `small_move` restore. Two energy move paths
  preserved: `energy.move(E)`/`bps.mv` (blocking, console) vs `move_energy(E)` (message plan).
- **H3 + bounce-back (`66713c9`, `32b0568`, `f9fb1cd`, `a0257a8`)** — `Attenuator.set()` rewritten
  non-blocking, retrying, **safe-failing** (raises if a foil never confirms — a safe halt beats a
  wrong/unsafe foil position). Root-caused a hardware **bounce-back**: a foil reads its target
  momentarily then falls back, and the old `set` latched that transient as success. Fix:
  **settle-debounce** (`settle_time`, hardware-tuned to **2 s**) — the read-back must STAY at target
  for `settle_time` before success; plus a **watchdog-margin guard** so `timeout` can't be set
  `<= settle_time` (which would spuriously fail every move). Added an aggregate **`Attenuators`**
  device + `make_attenuator_bank` (`attenuators1`/`attenuators2`, foils `f1..f12`) for all-or-
  nothing combined moves. **Key result:** the OLD idiom `RE(bps.mv(att1_6, 0, att1_8, 0, att1_9, 0))`
  already routes through the settled/safe per-foil `set()` and leaves unmentioned foils alone —
  nothing to change for users (pinned by tests).
- **H4 (`e8ac113`)** — **BDM stage** `x`/`y`/`th` were bare `EpicsSignal`s (completed on CA put-ack,
  not on settle). Now `SmarActAxis(PVPositionerIsClose)`: a move completes when the **readback** is
  within `atol=0.01` of the setpoint. The SmarAct's own done flags are **unreliable on this
  hardware** (verified live: `RD_MOVING` stuck `1`, `RD_INRANGE` stuck `0`), so a done-flag
  positioner would hang — `POSITION` is the only trustworthy signal. Flags exposed as `kind="config"`
  diagnostics. Interface preserved (`bps.mv`, `rel_scan`); the 3 alignment sites that read via the
  old scalar `.get()` now use `.position`. **Hardware-confirmed.**
- **H5/H6 (`ef31a53`, `5150741`)** — alignment scans use a **run-scoped `sample_name`** decorator
  (`sample_name_decorator` in `smiclasses/_plan_helpers.py`) instead of mutating `RE.md`; 11 plans
  decorated. `sample_id`/`get_scan_md`/`get_more_md` are **deprecated-but-kept** (warn + doc) for
  unmigrated user scripts — never deleted.
- **M2 (`363c064`)** — removed the orphan `dcm_theta` (a 3rd ophyd object on motor m65, zero live
  consumers). GV7 dedup folded into M3/M4 (below). **Deliberately NOT touched:** `dcm_config`
  (DCMInternals) also duplicates m65/m66/m67 but is on baseline — per staff, leaving it (still
  measured) is fine; consolidation would only be cosmetic.
- **M3/M4 (`1025eb5`)** — unified on **one** `TwoButtonShutter` (the maintained `nslsii.devices`
  one) — retired the local divergent/buggier copy. Added a thin SMI subclass exposing **explicit
  per-valve polarity** (`cmd_actuate_val`, default `1`): per staff, a valve's actuation value can be
  `1` OR `0` (open/close always opposite) and the status read-back's open/closed meaning varies per
  valve but is consistent within it. **Default behavior unchanged**; no valve changes until given an
  explicit polarity. `GV7` de-duplicated → now `GV7 = chamber_pressure.waxs_saxs_valve` (defined
  once, aliased; still in baseline via `chamber`). **Hardware-confirmed.**
- **M6 (`1a252ee`)** — LakeShore `output_lakeshore.D` pointed at `Gain:I-SP` (a 2022 copy-paste;
  duplicated I) → now `Gain:D-SP`. `output1..4` were eager plain instances with absolute prefixes
  (invisible to ophyd, not faked under sim) → now proper `Cpt`s with relative suffixes (same PVs).
  **Hardware-facing** (D now connects a previously-unconnected PV) but nothing reads/writes
  `outputN.D`, so safe.
- **Workflow (`3b76cdc`)** — richer IPython prompt (colored `SMI` tag, `pass-` stripped from
  data_session, project_name colored, date/time). **`BEAM_DOWN`** env var (or `SMI_BEAM_DOWN`):
  suspenders are BUILT but NOT installed (prints a banner), so you can restart Bluesky beam-down
  without `turn_off_suspenders()` each time; `turn_on_suspenders()` re-enables (now also installs
  `susp_phi_motor`). pixi task `start-beamdown` = `start` with `BEAM_DOWN=1`.
- **Test infra (`377261b`, `d21b69a`)** — fake-IOC **integration tier** (caproto) for the energy
  move choreography: `tests/iocs/sim_energy_ioc.py`, `tests/integration/test_energy_iocs.py`,
  `pixi run -e test test-iocs` (`--run-iocs`). Documented in `docs/TESTING.md`.

### Phase-3 device debt: DONE
H1–H6 and M2/M3/M4/M6 are complete. **`L`-items / Phase-0 leftovers status:** LakeShore D-PV now
fixed (M6). WAXS `_SAXS.tif` template and the detector SSH-password-to-secret are **still open**.

### NOT done (Phase 3 remainder + later)
- **Phase 3 Redis-config workstream (§8 of the plan)** — migrate hardcoded calibration into
  Redis-seeded `kind="config"` Signals: energy IVU-gap offset arrays (`smiclasses/energy.py`),
  bimorph voltage tables (`smiclasses/bimorph.py`), `-80` offset. Stand up `config/redis_keys.py`
  registry + `load_config`/`persist_config` helpers (Pilatus already shows the pattern). Retire
  `smi_config.csv`/`intepolation_db_sdd2.txt`. **Not started.**
- **Phase 4** — the `make_devices(context)` factory (QS gate). See the rundown at the bottom of
  this file. **Not started.**
- Branches still **not pushed**; no PRs opened.
- Blocked: caproto bare-pvproperty PVs won't connect via ophyd `wait_for_connection` (pyepics
  connects fine) — attenuator integration uses `make_fake_device` sim tests instead.

### Known harmless test noise
In non-quiet pytest runs a `ChannelAccessException: Unexpected channel ID` may print at
interpreter **teardown** (a leaked CA channel GC'd at session end) — it appears AFTER all asserts
pass and does NOT affect exit code (suite still exits 0). Tests that only need a PV string read
`Cpt.suffix`/`.pvname` without `.get()` to avoid creating live channels.

---

## (Historical) Phase 2 start follows

---

## Latest session (Phase 2 start) — TL;DR

Worked **only** in a local clone at `/nsls2/users/egann/git/smi/profile_collection` (NOT the
shared `/nsls2/data1/smi/.../profile_collection`), with a hard rule: **no hardware contact**.
Everything verified offline (`py_compile` + `pixi run -e test test`, CA disabled, fakes only).

New branch **`phase2-device-debt`** (off `phase1-packaging-shell`), **not pushed**:
```
c92379e Add three-tier test infrastructure + per-device fake/real factory
4b9b438 Guard SAXS_Detector init-time position reads (retire xfail)
e7f14a5 C5: make det_exposure_time a Bluesky plan
```

1. **C5 done** — `det_exposure_time` is now a Bluesky plan (`yield from bps.mv(...)`); all 11
   active alignment.py call sites + `startWAXS()` use `yield from`; acceptance test uses
   `RE(...)`. Deprecated blocking shim `det_exposure_time_sync` kept.
2. **SAXS_Detector init guard** — `__init__` / `update_beam_center` now guard `None` positions
   (only affects disconnected/fake; real hardware unchanged). Retires the Phase-1 xfail.
3. **Three-tier test infra + device factory** (see `profile_collection/docs/TESTING.md`):
   - `startup/smiclasses/device_factory.py`: `make_device(cls, prefix, name=, force=, seed=)`
     builds real or **fake (ophyd.sim, non-broadcasting)** per device. Mode priority:
     `force` > env `SMI_REAL_DEVICES` > env `SMI_FAKE_DEVICES` > in-process overrides >
     `SMI_DEVICE_MODES_FILE` (CSV `name,mode`) > default `real`. Lets a broken device be pinned
     to fake in production while the rest stay real; `SMI_FAKE_DEVICES=all` fakes everything.
   - Tests reorganized into `tests/unit` (pure code), `tests/sim` (fakes + RunEngine plan runs),
     `tests/hardware` (real PVs, **deselected** unless `pytest --run-hardware`).
   - `conftest.py` locks down EPICS CA and skips the hardware tier by default.
   - pixi tasks: `test` (unit+sim), `test-unit`, `test-sim`, `test-hardware`.
   - **Result:** `pixi run -e test test` → 48 passed, 2 deselected.

**NOT done (needs hardware + Redis — resume next week):**
- **Wire the live `smibase/*.py` instantiations through `make_device`** so the production
  per-device fake/real toggle is actually live. Behaviour-preserving (default mode = real), but
  it edits the boot path and `smibase` still can't be imported off-beamline (`get_ipython()` +
  Redis at import), so it can't be verified without the live profile.
- On-beamline smoke test of Phase 1 **and** C5 (real Pilatus exposure via the new plan).
- Rest of Phase 2: C1/C2 Linkam, C3/C4 humidity, C6 fast shutter, C7 Huber phi.

---

## (Historical) end-of-Phase-1 handoff follows

---

## TL;DR — current state

- **Planning docs** live in **`smi-plans`** (`docs/STARTUP_AUDIT.md`, `docs/STARTUP_RESTRUCTURE_PLAN.md`)
  — committed + pushed to `origin/master` (`github.com/EliotGann/smi-plans`).
- **Code changes** live in **profile_collection** (`/nsls2/data1/smi/shared/config/bluesky/profile_collection/`)
  — committed on local branches, **NOT pushed**, remote is `github.com/NSLS2/smi-profile-collection`.
- **Phase 0** (hygiene + safe bug-fixes) and **Phase 1** (packaging shell: decouple device
  classes from `smibase`, add off-beamline pytest harness) are **done and committed**.
- **Verified offline only.** `py_compile` + `pixi run -e test test` (34 passed, 1 xfailed) pass.
  **Not yet smoke-tested on the live beamline** (real EPICS/Redis/Tiled).
- **Next:** Phase 2 — high-value device-debt fixes (det_exposure_time as a plan, Linkam Heater,
  humidity signals, fast-shutter `set()`).

---

## Repos & locations

| Repo | Path | Remote | Role |
|---|---|---|---|
| `smi-plans` | `/home/xf12id/git/smi/smi-plans` | `origin` = `github.com/EliotGann/smi-plans` (pushed) | user-facing Bluesky plans + the planning docs |
| profile_collection | `/nsls2/data1/smi/shared/config/bluesky/profile_collection` | `origin` = `git@github.com:NSLS2/smi-profile-collection.git`; `httporigin` = https | the IPython/QS startup code (devices) |

Pixi env interpreter: `.pixi/envs/terminal/bin/python` (Python 3.12). Tests: `pixi run -e test test`.

---

## Git state (as of this handoff)

### smi-plans — branch `master` (clean, pushed)
```
0b90ae6 Add QS-enablement + Redis-config workstreams; correct Huber stage swap
a3899c5 Add SMI profile-collection device audit + restructure plan
380fb41 Enforce message-purity in all plans; add user_hints; device-debt wrappers
```
Docs: `docs/STARTUP_AUDIT.md`, `docs/STARTUP_RESTRUCTURE_PLAN.md`, `docs/DEVICE_DEBT.md`,
`docs/PACKAGE_OVERVIEW.md`.

### profile_collection — branches (clean working tree, NOT pushed)
Base branch is `main`. Two new branches were created in this work:

- **`phase0-device-cleanup`** (off `main`):
  ```
  8f2f47f Remove dead STG_pseudo machinery from manipulators   <- MISTAKE
  4529db0 Fix latent device bugs in Pilatus modules
  051d0d8 Remove accidentally-tracked junk files
  ```
- **`phase1-packaging-shell`** (off `phase0-device-cleanup`, so it CONTAINS Phase 0 too):
  ```
  4e90301 Add off-beamline device-class test harness (pytest + ophyd.sim)
  c71b43a Decouple smiclasses device classes from smibase (break import cycle)
  638d793 Add backwards-compatible .th/.ph/.ch aliases on STG_pseudo
  fdc6f3d Switch 'stage' to the Huber STG_pseudo positioner; drop legacy STG
  29fa35b Revert "Remove dead STG_pseudo machinery from manipulators"
  8f2f47f Remove dead STG_pseudo machinery from manipulators
  4529db0 Fix latent device bugs in Pilatus modules
  051d0d8 Remove accidentally-tracked junk files
  ```

**`phase1-packaging-shell` is the branch to continue from** (it has everything).

> **History note / optional cleanup:** commits `8f2f47f` (mistaken removal of `STG_pseudo`) and
> `29fa35b` (its revert) cancel out. They were left as honest history. If you want a clean branch
> before pushing/PR, squash/drop that pair (e.g. interactive rebase) so the branch reads:
> junk-removal → pilatus-bugfixes → STG_pseudo-aliases → stage→STG_pseudo → decouple → tests.

---

## What was the goal (context)

Restructure the SMI Bluesky startup so device code follows the `smi-plans` **tenets** (esp.
Tenet 5: *plans contain only messages* → every quantity a plan touches must be a proper ophyd
Signal/positioner). Two driving documents in `smi-plans/docs/`:
- **`STARTUP_AUDIT.md`** — exhaustive device inventory + ranked device-debt register (IDs
  C1–C7, H1–H7, M1–M6, L1–L6) + the `smibase`/`smiclasses` structural analysis.
- **`STARTUP_RESTRUCTURE_PLAN.md`** — phased, beamline-safe plan (Phases 0–4) + QS-enablement
  section (§7) + Redis-config workstream (§8) + open questions.

### Hardware reality (confirmed by staff during this session)
- The old **hexapod** was replaced by a **Huber stage** (device KEEPS the name `stage`), driven
  through the `STG_pseudo` PseudoPositioner (lab-frame x/y/z/theta/chi/phi w/ rotation-center
  compensation). This was **mid-migration**.
- The old **`prs`** (precision rotation stage) is replaced by the **Huber phi axis**. Plans that
  used `prs` should be repointed to the Huber phi (likely `stage.phi`) — NOT given a new `prs`.
- **`piezo` (SmarAct fine stage) is UNCHANGED** and still sits on top of the Huber `stage`.

---

## Phase 0 — DONE (hygiene + safe bug-fixes)

Commits `051d0d8`, `4529db0`, plus the manipulators saga (`8f2f47f`→`29fa35b`→`fdc6f3d`→`638d793`).

- **Removed tracked junk:** `content.json` (18 MB base64 PNG dump), `mi.setDirectBeamROI`.
- **Pilatus bug-fixes** (`smibase/pilatus.py`, `smiclasses/pilatus.py`):
  - `pil900kwroi2/3/4` were all assigned to var `pil900kwroi1` → fixed names (all four now exist).
  - 4× `Exception(ValueError(...))` built-but-never-raised → `raise ValueError(...)`.
  - `save_beamstop` compared the Signal object to a string → `.get()`.
  - `set_energy_cam(thresh_ev=None)` crashed before its guard → divide only when not None; also
    fixed two branches that set `en_ev` instead of `thresh`.
- **Huber stage migration (corrected mid-session):**
  - Initially (wrongly) removed `STG_pseudo` as "dead code" → **reverted**.
  - `stage` now = `STG_pseudo("XF:12IDC-OP:2{HUB:Stg-Ax:", name="stage")`; legacy `STG` class and
    the dead `stage_pseudo` instance removed.
  - Added **backwards-compat property aliases on `STG_pseudo`**: `.th`→`.theta`, `.ph`→`.phi`,
    `.ch`→`.chi` (so existing user/plan code like `bps.mv(stage.th, …)` keeps working;
    `.x/.y/.z` already matched). Aliases are properties, NOT ophyd Components (verified they
    don't pollute `component_names`).

**SKIPPED in Phase 0 (deliberately, await confirmation):** LakeShore `D` PV (`Gain:I-SP`→`Gain:D-SP`),
WAXS `_SAXS.tif` file_template, moving the detector SSH password out of source. These remain
open (see "Open items").

**Phase 0 verification:** user confirmed "tested enough, seems to be working ok" on the beamline.

---

## Phase 1 — DONE (packaging shell)

Commits `c71b43a` (decouple) + `4e90301` (test harness).

### The decoupling (breaks the `smibase → smiclasses → smibase` cycle)
- **New `startup/smiclasses/_context.py`** — a lazy dependency seam. API:
  - `configure(*, run_engine=None, config_dict=None, energy_source=None)` — called by the profile
    bootstrap to inject the real objects.
  - `get_md()` → `RE.md` (or `{}` if unconfigured), `get_config()` → `mdsave` Redis dict (or `{}`),
    `current_energy_eV()` → live energy (or `None`), `is_configured()`.
  - **No `smibase` import.** Degrades gracefully when unconfigured (so off-beamline import/tests work).
- **Design principle (per user):** `RE.md` and `mdsave` STAY as the persistent metadata/config
  carriers (proposal, data-session, data-security tags, raw-data dir, calibration). They were NOT
  removed — only the *hard import of `smibase.base` from device classes* was removed. They're now
  reached through the seam.
- `smiclasses/pilatus.py`:
  - `SAXSBeamStops` now imported from sibling `smiclasses.beamstop` (was routed via `smibase`).
  - `mdsave = _context.get_config()` (module-level).
  - `energyset` Cpt no longer reads EPICS at class-definition (`value=0.0`); seeded in `__init__`
    via `_context.current_energy_eV()`.
  - `TIFFPluginWithFileStore` resolves `md` lazily (`get_md()` when `md is None`); removed the
    class-definition `md=RE.md` from the `tiff` Cpt.
- `smiclasses/prosilica.py`: dropped `from smibase.base import RE` + class-def `md=RE.md`.
- `smibase/base.py`: after `RE` exists → `_smiclasses_context.configure(run_engine=RE, config_dict=mdsave)`.
- `smibase/energy.py`: after `energy` exists → `_smiclasses_context.configure(energy_source=energy)`.
- **Load order guarantees correctness:** `startup.py` imports `base` (line 9) → `energy` (21) →
  `pilatus` (26) → `prosilica` (27), so the seam is wired before the detector modules import.

### Test harness
- `pyproject.toml` (minimal; `[test]` extra: pytest/ophyd/numpy; pytest config).
- `pixi.toml`: new `test` feature (pytest dep), `test` task (`pytest`, with
  `EPICS_CA_AUTO_ADDR_LIST=NO`/`EPICS_CA_ADDR_LIST=127.0.0.1` so no real CA + quiet teardown),
  and `test` environment in the shared `profile` solve-group. `pixi.lock` updated.
- `tests/conftest.py` — puts `startup/` on `sys.path`; resets `_context` between tests.
- `tests/test_smiclasses_import.py` — every smiclasses module imports hardware-free + guard that
  none import `smibase`.
- `tests/test_device_instantiation.py` — `make_fake_device` builds of STG_pseudo (+ alias check),
  SMARACT, BDM, SAXSBeamStops, Pilatus cam, WAXS, Energy, Lakeshore, Linkam.
- `tests/test_context.py` — `_context` seam unit tests.

### Run the tests
```bash
cd /nsls2/data1/smi/shared/config/bluesky/profile_collection
pixi run -e test test          # -> 34 passed, 1 xfailed, exit 0
```

### Known xfail (a real finding, not a regression)
`SAXS_Detector.__init__` reads `self.beamstop.x_pin.position` (etc.) at construction to infer the
initial active-beamstop state. On a fresh `make_fake_device` instance those positions are `None`
→ `TypeError`. Marked `xfail(strict=True)` in `test_device_instantiation.py`. **This is a
device-cleanup item for a later phase** (init-time hardware read; pairs with the bdm/positioner
fixes H4). Not introduced by Phase 1.

---

## NOT done / deferred

- **No on-beamline smoke test of Phase 1 yet.** Must verify on the live profile:
  `pixi run terminal.start`, confirm `pil2M`/`pil900KW`/`stage`/`energy` connect, and that a
  Pilatus exposure writes a TIFF (watch the lazy-`md` raw-data path resolution at stage()).
  Also confirm `stage` (now STG_pseudo) connects and a GISAXS alignment (uses `stage.th` via the
  alias, and `stage.y`) runs.
- **Branches not pushed**; no PRs opened.
- **Physical `src/smi_beamline/` package move deferred** (we chose "decouple-first, package
  incrementally"). Phase 1 delivered importability+tests without relocating files.
- **Phase 0 skipped items** still open (LakeShore D PV, WAXS template, SSH secret).

---

## NEXT — Phase 2 (high-value device-debt fixes)

Goal: make the devices message-clean so `smi-plans` can delete its `_devices.py` wrappers. Each
maps to a `STARTUP_RESTRUCTURE_PLAN.md` / `DEVICE_DEBT.md` item. Suggested order:

1. **C5 — `det_exposure_time` as a PLAN.** ✅ **DONE** (commit `e7f14a5` on
   `phase2-device-debt`). Generator using `yield from bps.mv(...)`; amptek `mca.preset_real_time`
   wired (guarded on `amptek_det`); 11 alignment call sites + `startWAXS()` updated; deprecated
   `det_exposure_time_sync` shim kept.
2. **C1+C2 — Linkam Heater.** Expose `LThermal.temperature_current` (already exists,
   `smiclasses/linkam.py:53`) as the recordable readback; add units + a `done`/at-setpoint;
   delete the `.put()/.get()` methods (`temperature()`, `setTemperature`, `on/off`).
   → retires `smi_plans._devices.linkam_temperature_signal`; simplifies `technique_C.linkam_heater`.
3. **C3+C4 — Humidity signals.** Add `EpicsSignalRO`/`DerivedSignal` on `…AI:1-I` (raw RH voltage)
   applying the offset/slope/T-correction so `bps.rd(humidity)` works; convert
   `setDryFlow/setWetFlow` to plan stubs; add a `set_humidity(rh)` plan.
   → retires `smi_plans._devices.humidity_signal`; `{rh}` filename token resolves.
4. **C6 — Fast shutter `set()`.** Give `SMIFastShutter` a status-tracking `set()` so
   `bps.mv(fs, "open"/"close")` works; drop the import-time `.get()` in `__init__`.
5. **C7 — Huber phi.** Repoint `smi-plans` `prs` references (`technique_I/J/K`,
   `_compose.motor_axis("prs", …)`, the `.. important::` blocks) to the Huber phi axis — confirm
   the attribute name first (Open Question Q-Huber).
6. Update `smi-plans/docs/DEVICE_DEBT.md` as each wrapper is retired.

Process used this session (keep it): feature branch, grouped commits, `py_compile` + offline
import + `pixi run -e test test` after changes, do NOT push without explicit OK, surface
hardware-semantics decisions before changing them.

---

## Open questions awaiting staff input (from STARTUP_RESTRUCTURE_PLAN.md §9)

1. **Q-Huber:** exact Huber `stage` axis set + the phi attribute name (`stage.phi`?) for the
   `prs`→Huber-phi repoint. Is the `STG_pseudo` rotation-center math (cx/cy/cz_*) finalized?
2. **RH readback:** confirm there's no dedicated %RH PV and that wrapping `…AI:1-I` (+ offset
   0.816887 / slope 0.028813 / T-corr) is the intended source.
3. **`pil2M_pos` vs `pil2m_pos`** casing (plans use uppercase; profile defines lowercase).
4. **`pil300KW` / `rayonix`** — decommissioned or to be restored? (commented out)
5. ~~**`Insert/Retract` enum** semantics differ for valves vs foils~~ — **ANSWERED (Phase 3):** no
   single convention; a valve's actuation value is `1` or `0` (open/close always opposite) and the
   status read-back's open/closed meaning varies per valve but is consistent within it. Handled by
   per-valve `cmd_actuate_val` on the unified `TwoButtonShutter` (default `1` = unchanged). Each
   valve's true polarity still to be confirmed against CSS before overriding.
6. **Package name/home** (`smi_beamline`?) and whether it lives in the profile repo or a new repo.
7. **Q-QS:** want the "QS-minimal shortcut" as an earlier milestone, or QS strictly after the
   Phase-4 factory?
8. **Q-Redis:** want a periodic "dump Redis config → git/JSON" snapshot for provenance, or is the
   live Redis dict the sole source of truth?
9. ~~**Phase 0 leftovers:** LakeShore `D` PV~~ — **DONE (M6).** Still open: WAXS `_SAXS.tif`
   template, and moving the detector SSH password to a secret.

---

## Quick orientation for the next session

1. `cd /nsls2/data1/smi/shared/config/bluesky/profile_collection && git checkout phase4-factory-package`
   (Phase 4 lives here; `phase3-device-cleanliness` is the untouched rollback point.)
2. Read `STARTUP_RESTRUCTURE_PLAN.md` §7.3 (the QS deployment artifacts). The Phase-4 TL;DR at the
   top of THIS file is the live state.
3. `pixi run -e test test` → green (expect **128 passed, 4 skipped, 15 deselected**). The old timer
   flake is fixed; the teardown `ChannelAccessException` is harmless GC noise (exit 0).
4. `pixi run -e qs qs-list` → confirms the profile loads **headless in the QS worker**
   ("created successfully", 139 devices + 38 plans, 0 ignored). This is the QS-readiness smoke test.
5. **Phases 0–3 done; Phase 4 = factory + package + QS-worker-aware, all proven.** The remaining
   work is **deployment/config, not code** (handoff TL;DR "NOT done"): `user_group_permissions.yaml`
   (+`root` group), a queue-Redis on localhost:6379 (no `redis-server` binary in the envs yet),
   and the **Q-QS-worker** decision (plain-Python vs `--use-ipython-kernel ON`). Then a real local
   `start-re-manager`. Optional structural tidy-up: convert the 24 `smibase` instance-builders to
   `make_X(context)`.

---

## Phase 4 rundown — the `make_devices(context)` factory (the QS gate)

**Goal:** remove the last import-time globals so the package imports **headless** (no live
IPython/EPICS), which is exactly what `bluesky_queueserver`'s pure-Python worker needs.

**The governing problem (audit §1, plan §7.1):** ~23 `get_ipython().user_ns[...]` grabs
(`RE`/`sd`/`bec`/`db`) scattered across `smibase/*.py`, plus import-time `RE`/`sd` mutation,
import-time secret/Tiled/Redis reads, `ip.prompts = ProposalIDPrompt(ip)`, an import-time Duo
push, and `RE.install_suspender(...)`. In the QS worker `get_ipython()` returns **`None`**, so the
profile crashes on the **first line of `base.py`** (`nslsii.configure_base(get_ipython().user_ns,
...)`). Modules therefore only import inside a live, configured IPython session.

**The fix (plan §3 "Key design moves", §4 Phase 4):**
1. **`make_devices(context)`** — a factory that *receives* `{RE, sd, db, bec, md, ...}` (built by a
   thin bootstrap) and returns the namespace of live device objects, wiring baselines and Tiled
   subscriptions **explicitly**. Replaces the `user_ns` grabs and import-time mutations. Device
   classes never call `get_ipython()`.
2. **Thin `startup/` bootstrap (~30 lines)** — the ONLY IPython-aware code: build the context,
   call `make_devices`, push results to `user_ns`, install suspenders, subscribe Tiled. Guard the
   IPython-only bits (`ip.prompts`, magics, Duo, `configure_olog`) with `is_ipython_mode()` /
   `is_re_worker_active()` so the worker skips them.
3. **Verify:** profile loads via BOTH `pixi run terminal.start` AND `pixi run qs.qs-backend`, and
   off-beamline `import smi_beamline` succeeds with no live IPython/EPICS.

**Why it's well-positioned now:** the `_context.py` seam (Phase 1) already broke the
`smibase`↔`smiclasses` import cycle and proved dependency-injection for `RE.md`/`mdsave`/energy;
the `device_factory.make_device(...)` (Phase 2) already builds real-or-fake per device. Phase 4
generalizes that seam to the WHOLE namespace and moves the bootstrap side-effects out of import.

**Risk:** Medium — it rewrites the boot path. Mitigation (plan §5): keep the current `startup.py`
working until the factory is proven in parallel; do a hardware smoke-test (count each detector, one
alignment, one energy move, one temperature setpoint, shutter open/close) before switching.

**Note — QS needs 4 more non-cleanup artifacts (plan §7.3), separate from the factory:**
`existing_plans_and_devices.yaml`, `user_group_permissions.yaml` (must include a `root` group),
plan-signature validation (the profile's own plans — `alignment`/beam-mode/exposure — are the risk;
`smi-plans` is already signature-clean), and deployment infra (a Redis for the queue store +
systemd/ansible). And the **Redis-config workstream (§8)** is a soft prerequisite for a *good* QS
experience (Redis is process-shared, so the worker and terminal agree on calibration).

**Optional "QS-minimal shortcut" (plan §7.4, Open Question Q-QS):** make `base.py` worker-aware
WITHOUT the full factory (guard IPython-only code, pass the real namespace, neutralize the sd-grabs
via a shared baseline helper). Yields a working QS backend sooner, but touches `base.py`/bootstrap
twice. Decide via Q-QS.

---

## Phase 4 readiness — concrete starting map (measured in the tree)

The `get_ipython()` surface as it stands today (run `grep -rn "get_ipython()" startup/` to refresh):

- **28 `get_ipython()` calls across 23 files.** What they grab:
  `sd` ×16, `bec` ×2, `db` ×1, plus **`base.py` has 5** (the bootstrap: `configure_base`, prompts,
  Duo, etc.).
- **The dominant pattern is trivial to factor:** 16 modules do exactly
  `sd = get_ipython().user_ns['sd']` then `sd.baseline.extend([...])` at import. A single
  **`baseline_register(*devices)` helper** (in the seam, fed the real `sd` by the bootstrap) removes
  all 16 in a mechanical, low-risk pass — this alone is most of the QS-minimal work.
- **`base.py` (the hard part):** the first line `nslsii.configure_base(get_ipython().user_ns, ...)`
  must take the real namespace from the bootstrap (or be guarded with `is_re_worker_active()`),
  and the import-time prompt / Duo push / Tiled / Redis / `RE.install_suspender` must move into the
  bootstrap and be worker-guarded.
- **`_context.py` already carries `RE`/`mdsave`/energy** (Phase 1) and **`device_factory`** already
  builds real-or-fake per device (Phase 2) — so the factory's plumbing largely exists; Phase 4 is
  mostly (a) the `sd`/`bec`/`db` baseline-injection helper, (b) moving `base.py`'s import-time side
  effects into a thin bootstrap, (c) worker guards.

**Suggested first PR of Phase 4 (smallest useful slice, behavior-preserving):** add
`baseline_register(...)` (and `get_sd()`/`get_bec()`/`get_db()`) to `_context.py`, wire the real
`sd`/`bec`/`db` in `base.py`, and convert the 16 `sd = get_ipython().user_ns['sd']` +
`sd.baseline.extend(...)` sites to `baseline_register(...)`. Default behavior identical on the
terminal; it removes 16 of the 28 grabs and is independently testable.

**Two open questions to settle before starting (both in plan §9 / handoff open-questions):**
- **Q-QS:** full clean Phase-4 factory, or the QS-minimal shortcut first (faster QS, `base.py`
  touched twice)?
- **Q-package:** does this become a real `src/smi_beamline/` package (plan §3), or keep the
  decouple-in-place approach (no file move) and just add the factory/bootstrap?
