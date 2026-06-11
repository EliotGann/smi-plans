# SMI `profile_collection` — Restructure & Device-Debt Paydown Plan

> **Status: proposal only.** Companion to `STARTUP_AUDIT.md`. This is a **phased, beamline-safe**
> plan to (a) turn the SMI Bluesky startup directory into a real installable package and (b) pay
> down the device debt that currently forces `smi-plans` into `smi_plans._devices` wrappers. It
> proposes no code changes by itself — it is the roadmap. Each phase is an independently
> shippable PR that leaves the beamline runnable.

**Target repo for the work:** the profile collection
(`/nsls2/data1/smi/shared/config/bluesky/profile_collection/`). This plan lives in `smi-plans`
because it extends the device-debt tracking (`DEVICE_DEBT.md`) that the plans depend on.

---

## 1. Goals & non-negotiables

1. **The beamline keeps running at every phase.** No phase requires a flag-day rewrite; each ends
   with a profile that loads and acquires.
2. **Devices end message-clean.** Every quantity a plan sets or reads is a settable/readable
   ophyd Signal or positioner (Tenet 5). This is the *point* — it lets `smi-plans` delete its
   interim wrappers.
3. **The package becomes importable & testable** off-beamline (with `ophyd.sim`), like `smi-plans`
   already is.
4. **Device fixes are decoupled from packaging.** The highest-value work (humidity, Linkam,
   exposure-time, fast shutter, Huber-phi repoint) can land *before* any big structural move, so
   users feel the benefit early.
5. **Small, reviewable PRs**, each tied to audit items by ID (C1–C7, H1–H7, M1–M6, L1–L6).
6. **Two adjacent workstreams are planned alongside this** (see §7–§8): **queueserver (QS)
   enablement** (gated on the Phase-4 factory + four QS-specific artifacts) and **migrating
   persistent config/calibration into the Redis-backed dict** (folds into Phases 1 & 3, and is a
   soft prerequisite for a good QS experience because Redis is process-shared).

---

## 2. The core problems (recap from the audit)

- **Un-importable:** 23 `get_ipython().user_ns[...]` grabs (`RE`/`sd`/`bec`/`db`) + import-time
  `RE`/`sd` mutation + secret/Tiled/Redis reads in `base.py` mean modules only import inside a
  live, configured IPython session.
- **Circular layering:** `smibase.pilatus → smiclasses.pilatus → smibase.base/energy/beamstop`.
  The `smibase`/`smiclasses` axis ("instance vs class") is the wrong cut for the 7 logic modules
  (`alignment`, `beam`, `utils`, `config`, `humidity_cell`, `suspenders`, and `pilatus`'s
  functions), which are really plan/utility libraries.
- **Device debt:** a short, concrete list (audit §8) blocks tenets for live plans.
- **No tests, stale CI, repo bloat** (audit §9).

---

## 3. Target architecture

A `src/`-layout package (working name `smi_beamline`) that cleanly separates the axes that
actually differ — **device classes / config / instances / plans / utils** — instead of
"instance vs class":

```
smi_beamline/
  pyproject.toml
  src/smi_beamline/
    devices/            # ophyd DEVICE CLASSES only (was smiclasses/), pure & importable
      detectors.py      #   Pilatus/Prosilica/Amptek classes
      motion.py         #   SMARACT/STG/BDM/MIR/CRL/SLIT/...
      energy.py         #   Energy (PseudoPositioner), InsertionDevice
      temperature.py    #   Linkam/Lakeshore as a clean Heater-shaped device
      environment.py    #   Sample_Chamber, humidity, ioLogik, shutters, attenuators
      ...
    config/             # PV strings, calibration tables, magic numbers (OUT of class bodies)
      pvs.py            #   all the "XF:12ID..." strings, keyed by logical name
      calibration.py    #   bimorph voltage tables, rotation centers, energy->lens maps, SDD interp
    instances.py        # make_devices(context) -> namespace of live device objects (a FACTORY,
                        #   NO import-time side effects); dependency-injects RE/sd/db
    plans/              # the "logic" that lived in smibase/
      alignment.py      #   GISAXS/GIWAXS/XRR/BDM alignment plans (message-pure)
      beam.py           #   beamline-mode plans (modeAlignment/modeMeasurement)
      detectors.py      #   det_exposure_time AS A PLAN, threshold/energy-cam plans
    utils/              # peak-stats (ps), fitting, filename helpers, purge_cryo
    startup/            # THIN bootstrap: build context, call make_devices, push to user_ns,
                        #   subscribe Tiled, install suspenders. The only IPython-aware code.
  tests/                # pytest + ophyd.sim, mirroring smi-plans' approach
```

**Key design moves**

- **Dependency injection over `user_ns` grabs.** `make_devices(context)` receives
  `{RE, sd, db, bec, md, ...}` (built by the thin `startup/` bootstrap) and wires baselines and
  subscriptions explicitly. Device classes never call `get_ipython()`.
- **Config out of class bodies.** Hardcoded PVs (e.g. SmarAct axes), bimorph voltage tables,
  rotation centers, energy→lens maps, and SDD interpolation tables move to `config/`. This also
  kills the duplicate-PV objects (M2).
- **`smiclasses` → `devices/`; `smibase` instantiation → `instances.py`.** The "instance vs
  class" split survives only as "factory vs class," which is a real and useful boundary.
- **`alignment`/`beam`/`config`/`utils`/`suspenders` → `plans/` + `utils/`.** They stop being
  "base" modules.
- **`smi-plans` stays a separate package** (it is the *user-facing* plan library). The profile's
  `plans/` holds only *beamline-infrastructure* plans (alignment, mode switching, exposure) that
  `smi-plans` techniques call. Optionally, `smi-plans` later imports `smi_beamline.devices` for
  off-beamline sim tests.

---

## 4. Phased migration

Each phase = one or a few PRs. **Audit-item IDs in brackets.**

### Phase 0 — Hygiene & safe bug-fixes (no behavior change, no restructure)
Lowest risk, immediate value; can be done today against the current layout.

- **Repo bloat** [audit §9]: delete tracked `content.json` (18 MB) and `mi.setDirectBeamROI`;
  confirm `.gitignore` covers `__pycache__`/`.ipynb_checkpoints`; remove the on-disk stale
  `.ipynb_checkpoints/` old-startup fossils.
- **CI** [§9]: align `azure-pipelines.yml` to the pixi Python 3.12 pin (or replace with a pixi
  task) so the smoke-import check matches reality.
- **Dead code** [L1, L2, L5]: remove `STG_pseudo` + `stage_pseudo` (zero consumers), duplicate
  `readbackEpicsMotor`, triplicated imports, unused imports, dead-commented instances.
- **Latent bugs** [L3, L6, H1, H7, M5]: fix `pil900kwroi1`×4 (→ roi1–4), `*_writing` name reuse,
  `OAV2` stage-sig keyed off `OAV.cam`, WAXS `_SAXS.tif` template, `Energy.small_move` `gapspeed`
  typo, `linkam.states()`/`arr2word`, `remove_rod/pin` missing `raise`, `save_beamstop`
  Signal==str, `set_energy_cam` `thresh_ev=None` guard, LakeShore `D`→D-gain PV.
- **Secret** [L4]: move the detector-PC SSH password out of `smibase/pilatus.py` into a secret
  file / env var (like `base.py` already does for Redis).
- **Verify:** profile loads in `pixi run terminal.start`; `RE(bp.count([pil2M]))` works; a GISAXS
  alignment runs.

### Phase 1 — Packaging shell (importable `devices/`, sim test harness)
Make the device *classes* importable and tested without breaking the running profile.

- Add `pyproject.toml` (src-layout, `smi_beamline`), with a `[test]` extra (`pytest`, `ophyd`,
  `numpy`) mirroring `smi-plans`.
- Create `smi_beamline/devices/` and **move the `smiclasses` classes there**, moving hardcoded
  PVs/tables into `smi_beamline/config/`. **Break the cycle** [M1]: device classes must not import
  `smibase.base`/`energy`/`beamstop` — pass those dependencies in (e.g. `energy` as a constructor
  arg where a class genuinely needs it) or remove the coupling (e.g. `PilatusDetectorCamV33.energyset`
  default should not read EPICS at class-def time).
- Keep the live profile working via a **compatibility shim**: `smiclasses/X.py` re-exports from
  `smi_beamline.devices.*` so `smibase` imports still resolve during the transition.
- Add `tests/` running each device class against `ophyd.sim`/mocked PVs (instantiation, `describe`,
  `read`, `set` returns a Status) — the off-beamline safety net the profile currently lacks.
- **Verify:** `pip install -e ".[test]" && pytest` green off-beamline; profile still loads on-beamline.

### Phase 2 — High-value device-debt fixes (retire `smi_plans._devices` wrappers)
The payoff phase. Each item below makes a `smi-plans` wrapper or blocked technique unnecessary.
Can be interleaved with Phase 1 (they touch device classes, now in `devices/`).

- **C5 — `det_exposure_time` as a plan.** Provide `det_exposure_time(exp_t, meas_t=1)` as a
  generator (`yield from bps.mv(pil2M.cam.acquire_time, ...)` etc.), wire in the amptek
  `mca.preset_real_time`, and **update the ~13 alignment call sites** to `yield from`. Keep a
  thin deprecated shim for any out-of-tree caller. (Tenets 5/6.)
- **C1+C2 — Linkam Heater.** Expose `LThermal.temperature_current` (already exists) as the
  recordable readback; add `egu`/units and a `done`/"at setpoint" derived from STATUS bit-2 so
  `bps.mv(LThermal, T)` (or a `Heater`-shaped wrapper) works; delete the `.put()/.get()` methods
  (`temperature()`, `setTemperature`, `on/off`). → **`smi-plans` deletes
  `_devices.linkam_temperature_signal`; `technique_C.linkam_heater` simplifies.**
- **C3+C4 — Humidity signals.** Add `humidity = EpicsSignalRO("…AI:1-I", …)` wrapped in a
  `DerivedSignal`/small `Device` applying the offset/slope/T-correction so `bps.rd(humidity)` and
  `trigger_and_read([humidity])` work; convert `setDryFlow/setWetFlow` to plan stubs
  (`bps.mv`+`bps.sleep`) and add a `set_humidity(rh)` **plan**. → **`smi-plans` deletes
  `_devices.humidity_signal`; `{rh}` resolves; `technique_G` becomes message-pure.**
- **C6 — Fast shutter `set()`.** Give `SMIFastShutter` a status-tracking `set()` (mirroring
  `TwoButtonShutter`) so `bps.mv(fs, "open"/"close")` works; drop the import-time `.get()` in
  `__init__`.
- **C7 — `prs`.** With staff: either (re)instantiate the precision rotation stage as a normal
  settable positioner (so `motor_axis("prs", prs, …)` and `technique_I/J/K` work), or formally
  document its removal and update `smi-plans` (the `.. important::` blocks + CD-SAXS/tomography/XRR
  presets) accordingly.
- **Name reconciliation [audit §5]:** decide `pil2M_pos` vs `pil2m_pos` (alias one to the other),
  and clarify `pil300KW`/`rayonix` status in both repos.
- **Update `DEVICE_DEBT.md`** in `smi-plans` as each wrapper is retired.
- **Verify:** a humidity plan, a Linkam ramp (`technique_C`), and an alignment all run; the
  `smi-plans` message-purity test still passes with the wrappers removed.

### Phase 3 — Structural move of the logic modules + filename modernization
Now that devices are clean and tested, relocate the "logic" and remove the last anti-patterns.

- Move `alignment`, `beam`, `config`, `utils`, `suspenders` into `smi_beamline/plans/` and
  `utils/`. Update `startup/` to import from the new locations.
- **H5 — drop `sample_id`.** Replace `RE.md["sample_name"]` mutation with the `smi-plans`
  `acquire(..., md=...)`/`fname()` token approach; provide a deprecation note for muscle memory.
- **H6 — kill filename-baking.** Replace `get_scan_md`/`get_more_md` (`.position`/`.get()` into
  strings) with recorded-field `{token}` filenames (Tenets 2/3). Most of this is already provided
  by `smi-plans`; the profile just stops offering the anti-pattern helpers.
- **H1/H2/H3/H4 — finish message-cleanliness:** move DCM feedback toggling out of `Energy.set()`
  into plan messages; express the IVU brake as a message step; implement the `Attenuation`
  positioner so `bps.mv(att, 'Insert')` works; make `bdm` a positioner with move-completion.
- **M2/M3/M4/M6:** collapse duplicate PV objects, unify on one `TwoButtonShutter`, resolve the
  `Insert/Retract` enum hazard, make LakeShore `output1..4` proper `Cpt`s.
- **Verify:** full technique smoke (energy sweep, temperature ramp, grazing align, CD-SAXS rock).

### Phase 4 — Instances factory & queueserver-clean bootstrap
Remove the last import-time globals; make the package importable headless.

- **M1 — `make_devices(context)`.** Replace the 23 `user_ns` grabs and import-time
  `RE`/`sd`/`bec` mutations with an explicit factory the thin `startup/` calls; baselines and
  subscriptions are wired there.
- `startup/` becomes a ~30-line bootstrap; `pixi run qs.qs-backend` can import the package
  directly (no IPython).
- **Verify:** profile loads via both `terminal.start` and `qs.qs-backend`; off-beamline
  `import smi_beamline` succeeds without a live IPython/EPICS context.

---

## 5. Per-phase risk & verification

| Phase | Risk | Mitigation | Done-when |
|---|---|---|---|
| 0 | Low (deletions/bugfixes) | small PRs; on-beamline smoke after each | profile loads; count + 1 alignment OK |
| 1 | Medium (moving classes) | compatibility shim keeps `smiclasses` imports working; sim tests | `pytest` green off-beamline; profile loads |
| 2 | Medium (touching live device behavior) | one device per PR; keep deprecated shims; test the specific technique | humidity/Linkam/exposure/align all run |
| 3 | Medium-High (moving logic + filenames) | move first (no logic change), then modernize filenames separately | technique smoke suite passes |
| 4 | Medium (bootstrap rewrite) | keep old `startup.py` until the factory is proven in parallel | both `terminal` and `qs` envs load |

**Verification harness to build (Phase 1):** an `ophyd.sim`-based pytest suite plus an on-beamline
checklist script (extend `acceptance_tests/run_all_tests.py` into a documented smoke checklist:
count each detector, one alignment, one energy move, one temperature setpoint, shutter open/close).

---

## 6. Cross-repo payoff map

| Profile fix (phase) | Retires in `smi-plans` |
|---|---|
| C1/C2 Linkam Heater (2) | `_devices.linkam_temperature_signal`; `DEVICE_DEBT.md` item #2; simplifies `technique_C.linkam_heater` |
| C3/C4 Humidity signals (2) | `_devices.humidity_signal` + `FunctionBackedSignal` usage; `DEVICE_DEBT.md` item #1; `technique_G` message-pure |
| C5 `det_exposure_time` plan (2) | removes a bare-function call pattern alignment/techniques rely on; strengthens Tenet 6 |
| C6 fast-shutter `set()` (2) | enables `bps.mv(fs, …)` in any plan needing the fast shutter |
| C7 Huber phi axis (2) | unblocks `technique_I/J/K` + `_compose.motor_axis("prs", …)` once repointed from `prs` to the Huber phi axis (Tenet 7) |
| H5/H6 drop `sample_id`/`get_scan_md` (3) | confirms Tenets 2–4 end-to-end; removes the legacy filename path |

When all Critical items land, `smi_plans._devices.py` can be reduced to (ideally) empty, and
`DEVICE_DEBT.md` items #1 and #2 are closed.

---

## 7. Queueserver (QS) enablement — where it fits

**Short answer to "is QS after the cleanup?": yes, mostly — the cleanup *is* what unblocks QS —
but QS readiness is a distinct milestone with four extra, QS-specific steps that are NOT part of
the package cleanup.** The decisive cleanup phase for QS is **Phase 4** (the instances factory
that removes the `get_ipython()` grabs).

### 7.1 How QS actually loads this profile (the governing fact)
`pixi run qs.qs-backend` runs `start-re-manager --profile-dir=.`, which (with the default
settings) uses the **pure-Python worker**, *not* IPython. It `exec`s the top-level `startup.py`
into a plain dict and injects only a **stub** `get_ipython()` (an `IPDummy` exposing just
`.user_ns` and `.log`). Crucially, that stub patch applies **only to the directly-exec'd
`startup.py`** — all real code lives in the `smibase` *package*, imported normally, where
`from IPython import get_ipython` is the **real** function and returns **`None`** in the worker.

**Therefore the profile fails at env-open today on the very first line of `base.py`:**
```python
nslsii.configure_base(get_ipython().user_ns, ...)   # get_ipython() is None -> AttributeError
```
…and would then hit the same `None` on all 23 `get_ipython().user_ns['sd'|'RE'|'bec'|'db']`
grabs, on `ip.prompts = ProposalIDPrompt(ip)`, on the import-time Duo push
(`from_profile(..., username=None)`), and on the import-time `RE.install_suspender(...)`.

(Confirmed against the installed `bluesky_queueserver` 0.0.23 + `nslsii` 0.11.9:
`profile_ops.load_profile_collection` execs files into a dict; `_startup_script_patch` provides
the `IPDummy` stub; `nslsii.configure_base` guards its IPython-only bits with `if ipython:` but
the **call site** passing `get_ipython().user_ns` crashes first when `get_ipython()` is `None`.)

### 7.2 QS blockers map onto the existing phases

| QS blocker | Fixed by | Phase |
|---|---|---|
| 23× `get_ipython().user_ns[...]` grabs + `None.user_ns` crash | dependency-injection factory `make_devices(context)` | **Phase 4 (M1) — the gate** |
| `ip.prompts`, magics, `configure_olog` IPython bits | guard with `is_ipython_mode()` / `is_re_worker_active()` | Phase 4 |
| import-time Duo push / Tiled / Redis / secret reads | move into the bootstrap, guard for the worker | Phase 1 / 4 (M1) |
| import-time `RE.install_suspender` / blocking EPICS `.get()`/`.wait()` | move out of import; guard | Phase 3 / 4 |
| interactive `input()` mid-plan, `plt.show()` | covered by the Tenet-5/6 cleanup + worker guards | Phase 2 / 3 |

### 7.3 The four QS-specific steps (NOT part of the cleanup)
These must be planned in addition to Phases 0–4:

1. **`existing_plans_and_devices.yaml`** — the cached plan/device list. Generate with
   `qserver-list-plans-devices --startup-dir . --file-dir .`. (QS will *start* without it and
   auto-populate on first env-open, but generating it explicitly catches problems early.)
2. **`user_group_permissions.yaml`** — allow/deny lists; **must include a `root` group**. Not
   auto-generated; author from a template. Without it QS starts but **clients cannot submit
   plans** ("USERS WILL NOT BE ABLE TO SUBMIT PLANS").
3. **Plan-signature validation** — QS rejects (by default, *fails env-open on*) plans whose
   parameter **defaults aren't reconstructable** (e.g. a default that is a device object or a
   lambda) or whose **type annotations** can't be rebuilt. Action: run
   `qserver-list-plans-devices` against the profile, then either annotate offending plans with
   `parameter_annotation_decorator` / fix the signatures, or run with `--ignore-invalid-plans ON`
   initially. **Most of the user-facing plans are in `smi-plans`** (already signature-clean and
   message-pure with a test enforcing it), so the risk concentrates in the *profile's own* plans
   (`alignment`, beam-mode, exposure) — another reason Phase 3 (logic move + cleanup) precedes QS.
4. **Deployment infra** — a **Redis** server for the queue/permissions store (separate from the
   metadata Redis), plus the systemd units / ansible roles the `pixi.toml` `[feature.qs.tasks]`
   comment already flags as "needs development work."

### 7.4 Sequencing recommendation
- **Recommended path (clean):** QS comes up after **Phases 0–4** + the four steps above. Phase 4
  (factory) is the gate; everything before it makes the worker *load*, and Phases 2–3 make the
  plans *safe and usable* (message-pure, no `input()`/`plt.show()` mid-plan).
- **Optional "QS-minimal" earlier milestone:** make `base.py` worker-aware *without* the full
  `devices/`+`config/`+`plans/` reorg — i.e. guard IPython-only code with `is_re_worker_active()`,
  pass the real namespace instead of `get_ipython().user_ns`, neutralize the ~22 `sd`-grabs via a
  shared baseline helper, and gate Duo/suspender/prompt code. This can yield a working QS backend
  after **Phases 0–2 + §7.3**, deferring the elegant factory to Phase 4.
  **Tradeoff:** faster QS, but `base.py`/bootstrap is touched twice (shortcut, then clean
  factory). Choose this only if QS is needed before the full restructure (see Open Question
  Q-QS).

> **Bottom line:** your instinct is right — QS lands *after* the cleanup, specifically gated on
> the Phase-4 factory. The only nuance is that QS also needs the four non-cleanup artifacts in
> §7.3, and (per §8 below) a process-shared **Redis config** so the worker and the terminal agree
> on calibration.

---

## 8. Redis-backed config — a parallel workstream (folds into Phases 1 & 3)

**Goal (from beamline staff):** anything we currently **write to a file**, **hardcode as a
"current good value,"** or **should persist across restarts** belongs in the **Redis-backed dict**
(`mdsave`), seeded into `kind="config"` ophyd Signals and written back through a helper — **never a
file**, and never `RE.md` strings.

### 8.1 Why this is its own axis (and why it helps QS)
This is orthogonal to both the package cleanup and QS, but synergistic: because Redis is
**process-shared**, config in `mdsave` is seen by *both* the IPython terminal and the QS worker,
whereas a file written by one process (or `RE.md` in one process) is not. So the Redis migration
is a **soft prerequisite for a good QS experience**.

### 8.2 Current state (audit §4.6)
- **Already migrated (reference pattern):** all SAXS beam-center/beamstop/sample-Z offsets and the
  whole `distance_calibration` SDD LUT (`smiclasses/pilatus.py:411-429,546-562,676-681`).
- **File-based, but already effectively dead:** `smi_config.csv`, `intepolation_db_sdd2.txt` (only
  in dead checkpoints/scripts) → retire. `agb_z_calibration_results/*.npy` are offline-calibration
  *inputs* read by `scripts/build_distance_calibration.py` (which already writes to `mdsave`) →
  keep as raw staging.
- **Hardcoded-in-source (the real targets):** energy IVU-gap offset arrays
  (`smiclasses/energy.py:144-145`), bimorph voltage tables + `-80` offset (`smiclasses/bimorph.py`),
  `STG_pseudo` rotation centers (obsolete with Huber).

### 8.3 The pattern to standardize
The Pilatus already shows it; generalize with two refinements:
1. **Seed:** `Cpt(Signal, value=cfg.get("saxs.beam_offset_x_mm", DEFAULT), kind="config")`.
2. **Persist:** a small helper instead of N hand-written lines, e.g.
   `persist_config(device, keys)` / `load_config(device)` (the Pilatus writes ~15
   `mdsave[...] = sig.get()` lines by hand today).
3. **Namespace + registry:** keys are going flat (`saxs_*`, `distance_calibration`). Adopt a
   dotted convention (`saxs.beam_offset_x_mm`, `energy.ivu_gap_offsets`, `bimorph.hfm_default`)
   and a single **`config/redis_keys.py` registry** documenting every key, default, and units.
   **This registry *is* the `config/` module from §3**, backed by Redis for *runtime/calibration*
   values. **Static PV strings stay as Python in `config/pvs.py`** — they are code, not runtime
   state, and do not belong in Redis.

### 8.4 Where it lands in the phases
- **Phase 1:** stand up `config/redis_keys.py` (registry + defaults) and the
  `load_config`/`persist_config` helpers; move *static PV strings* into `config/pvs.py`.
- **Phase 3:** migrate the hardcoded calibration tables (energy IVU offsets, bimorph tables) into
  Redis-seeded `kind="config"` Signals; delete `RE.md`-as-config (`beam.py:423-425`); formally
  retire `smi_config.csv` / `intepolation_db_sdd2.txt`.
- **Provenance (decide — Open Question Q-Redis):** Redis-as-source-of-truth has **no git history /
  rollback** for calibration. Options: accept it (as Pilatus does today), or add a periodic
  "dump Redis config → JSON in git" snapshot for provenance.

---

## 9. Open questions for beamline staff

1. **RH readback:** confirm there is no dedicated % RH PV and that wrapping `…AI:1-I` with the
   Python conversion (offset 0.816887 / slope 0.028813 / T-correction) is the intended source.
2. **Q-Huber — sample stack:** confirmed the **hexapod was replaced by a Huber stage (kept the
   name `stage`) with a Huber phi axis (replaces `prs`), and `piezo` (SmarAct fine) is unchanged
   and still on top.** Remaining: what is the Huber `stage`'s exact axis set, and what is the phi
   attribute name (`stage.phi`?) that `smi-plans` should target in place of `prs`? Is
   `STG_pseudo`/its rotation-center math now fully obsolete?
3. **`pil2M_pos` vs `pil2m_pos`:** which is canonical? (Plans use uppercase; profile defines
   lowercase.) Alias accordingly.
4. **`pil300KW` / `rayonix`:** decommissioned, or to be restored? (Both are commented out.)
5. **`Insert/Retract` enum semantics:** the in-code comments flag that the meaning differs for gate
   valves vs foils — confirm the correct mapping before unifying `TwoButtonShutter`.
6. **Package name & home:** confirm `smi_beamline` (or preferred name) and whether the package
   lives in the profile repo or a new repo installed by the profile.
7. **Q-QS — timeline:** do you want the **"QS-minimal shortcut"** (§7.4) as a real earlier
   milestone (faster, but `base.py`/bootstrap touched twice), or keep QS gated on the clean
   Phase-4 factory?
8. **Q-Redis — provenance:** do you want a periodic "dump Redis config → git/JSON" snapshot for
   version history/rollback (§8.4), or is the live Redis dict the sole source of truth (as the
   Pilatus treats it today)?

---

## 10. Suggested first PR (smallest useful slice)

Phase 0 only: delete `content.json` + `mi.setDirectBeamROI`, fix the unambiguous bugs (L3, L6,
H7, M5), remove dead `STG_pseudo`/unused imports, and move the SSH password to a secret. No
behavior change, no API change, immediately reviewable, and it shrinks the repo by ~18 MB. This
builds momentum and a clean base for Phase 1 packaging.
