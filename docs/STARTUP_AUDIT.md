# SMI `profile_collection` Startup — Device & Structure Audit

> **Status: analysis only.** This document inventories the SMI beamline Bluesky startup code
> (the IPython "profile collection") and registers its *device debt* against the `smi-plans`
> tenets. It proposes **no code changes** to the profile collection; the remediation/migration
> plan lives in `STARTUP_RESTRUCTURE_PLAN.md`.

**Audited tree:** `/nsls2/data1/smi/shared/config/bluesky/profile_collection/`
(startup code under `startup/`, split into `startup/smibase/` and `startup/smiclasses/`).

**Why this matters to `smi-plans`:** every `technique_*` plan and every `_compose` axis runs
*inside* this profile and drives *these* device objects. A plan can only obey **Tenet 5 —
"plans contain ONLY messages"** if the underlying ophyd device exposes each quantity as a
settable/readable Signal the RunEngine can drive via `bps.mv`/`bps.rd`. Where it does not, the
plan is forced into a `smi_plans._devices` wrapper (see `DEVICE_DEBT.md`) or breaks the tenet.
This audit is the device-side companion to that doc.

---

## 1. Scope, method, and the yardstick

- **Read in full:** all 27 modules of `smibase/` + 21 modules of `smiclasses/`, plus
  `startup.py`, the repo-root packaging files (`pixi.toml`, `.ci/`, `azure-pipelines.yml`,
  `acceptance_tests/`, `ipython_config.py`), and the `smi-plans` package that consumes them.
- **Yardstick:** the 10 tenets in `PACKAGE_OVERVIEW.md`. The ones a *device* (not a plan) can
  violate:
  - **T2** — context is recorded as devices/Signals (not `.get()`/`.position` into a filename).
  - **T3** — filenames derive from *recorded* fields, never hand-formatted from live reads.
  - **T4** — intent travels in `md={}`, never `sample_id(...)` / `RE.md` mutation.
  - **T5** — *plans contain only messages*; a device must therefore expose every quantity a
    plan needs as a Signal/positioner (no bare `readHumidity()`, no `.temperature()` method,
    no `.put()`-only command, no blocking `.set().wait()` inside the device path).
  - **T6** — plans are generators end-to-end (no `RE()`-in-plan, no `cam.acquire.put`+busy-wait).
  - **T7** — slow/in-vacuum axes (`waxs.arc`, `prs`) are normal settable positioners.

- **Environment (from `pixi.toml`):** Python `>=3.12,<3.13`, `bluesky-base==1.15.0`,
  `ophyd>=1.11.2`, `ophyd-async>=0.13.7`, `nslsii==0.11.9`, `databroker==2.0.0`,
  `tiled-client==0.2.9`, `bluesky-tiled-plugins==2.0.2`, plus `paramiko`, `redis_json_dict`,
  `openai`/`anthropic`/`pyautogen`. Two environments: `terminal` (adds IPython 9.5, PySide6) and
  `qs` (queueserver + httpserver).

---

## 2. How the profile loads

- Launched by `pixi run terminal.start` → `ipython --profile-dir=.`. Stock IPython then
  auto-executes every `.py` in `startup/` in lexical order; there is exactly one file there,
  `startup/startup.py`. (`ipython_config.py` has **no** `exec_files`/`exec_lines` directive — the
  ~20k-line file is almost entirely commented-out defaults.)
- `startup.py` (35 lines) enables `autoreload 2`, then runs **27 sequential
  `from smibase.<module> import *`** statements. It imports **only from `smibase`**; `smiclasses`
  is pulled in transitively.
- All three `__init__.py` (`startup/`, `smibase/`, `smiclasses/`) are **empty**.
- **The import order is a hand-maintained topological sort** (see §6) — `base`/`base_dev` first
  (they create `RE`/`db`/`bec`/`sd`), then hardware in dependency order, with `pilatus` before
  `beam` before `alignment`, and `suspenders` after `machine`/`electrometers` (it installs
  suspenders as an import side effect).

---

## 3. Architecture map — the `smibase` / `smiclasses` split

**Intended contract:** `smibase/X.py` = device *instances* (the globals plans use);
`smiclasses/X.py` = the ophyd *class* definitions those instances use, via
`from smiclasses.X import SomeClass`.

**Verdict:** the contract holds for the ~14 simple hardware modules, but **fully breaks down**
for the logic-heavy modules, and the dependency direction is **circular** (see §6).

### 3.1 Full module table

| Module | smibase (LOC; def/cls) | smiclasses (LOC; def/cls) | smibase role | smiclasses role |
|---|---|---|---|---|
| `base` | 101; 2/2 | — | **Bootstrap**: `nslsii.configure_base`, Tiled writer/reader, Redis md, prompts; defines `RE/db/bec` | — |
| `base_dev` | 54; 2/0 | — | 2nd Tiled (SQL/migration) writer + doc patches | — |
| `waxschamber` | 29; 1/0 | 104; 7/2 | instance `chamber_pressure` + `get_chamber_pressure()` | `Sample_Chamber` (+ pump/vent **plans**), `Valve` |
| `shutter` | 55; 2/0 | 118; 8/2 | instances + `shopen/shclose` **plans** | `TwoButtonShutter`, `SMIFastShutter` |
| `beamstop` | 16; 0/0 | 9; 0/1 | pure instances (`saxs_bs`, `waxs_bs`) | `SAXSBeamStops` |
| `machine` | 20; 0/0 | 44; 2/3 | pure instances (`ring`, shutter-enable) | `Ring`, `InsertionDevice` |
| `attenuators` | 36; 0/0 | 107; 2/1 | pure instances (`att1_1..att2_12`) | `Attenuator` (+ dead `Attenuation`) |
| `crls` | 13; 0/0 | 29; 0/1 | pure instance (`crl`) | `CRL` |
| `manipulators` | 23; 0/0 | 503; 9/6 | pure instances (`bdm`, `stage`, `piezo`) | `BDMStage/STG/SMARACT/STG_pseudo` (+ plans) |
| `mirrors` | 20; 0/0 | 11; 0/1 (+`bimorph` 170; 8/2) | pure instances | `MIR`; `bimorph` HV controllers (+ plans) |
| `motors` | 22; 0/0 | 65; 0/7 | pure instances (`MDrive`, `thorlabs_su`) | 7 motor classes (most unused) |
| `slits` | 31; 0/0 | 65; 0/4 | pure instances | `SLIT/SLTH/SLTV/APER` |
| `energy` | 48; 1/0 | 313; 8/2 | instances + `feedback()` | `Energy` (PseudoPositioner), `DCMInternals` (+ plans) |
| `xbpms` | 16; 0/0 | 12; 0/1 | pure instances (`xbpm*_pos`) | `XBPM` (motor) |
| `ioLogik` | 15; 0/0 | 66; 3/3 | pure instances (Moxa, diag) | `ioLogik1240/1241`, `Diag_Module` (+ plans) |
| `electrometers` | 52; 0/0 | 93; 5/4 | pure instances (`ls`, `xbpm2/3`, `pin_diode`, `keithly2450`) | `XBPM`, `new_LakeShore`, `Keithly2450` |
| `amptek` | 17; 0/0 | 163; 7/5 | pure instances (`amptek`, `amptek_pos`) | `SMIAmptek`, `AmptekPositions` |
| `pilatus` | **253; 9/0** | **753; 39/11** | **Logic**: `det_exposure_time()` + 8 fns, camserver restart (paramiko/telnet) | detector classes + beamstop/threshold **plans** |
| `prosilica` | 86; 0/0 | 89; 6/4 | pure instances (OAV/FS/WBS/VFM…) | `StandardProsilicaV33` etc. |
| `bladecoater` | 6; 0/0 | 23; 0/2 | pure instances (`bc_smaract`, `syringe_pu`) | `bladecoater_smaract`, `syringe_pump` |
| `linkam` | 5; 0/0 | **538; 28/3** | pure instances (`LThermal`, `LTensile`) | `Linkam/LinkamThermal/LinkamTensile` (+ many plans) |
| `beam` | **553; 24/7** | — | **Logic**: `SMIBeam`, beamline-mode state machine, metadata devices, flux plans | — |
| `alignment` | **1033; 26/1** | — | **Logic**: all GISAXS/GIWAXS/XRR/BDM alignment **plans** | — |
| `config` | **173; 4/0** | — | **Logic**: `sample_id`, `proposal_swap`, `project_set` (Redis proposal switching) | — |
| `humidity_cell` | **96; 4/0** | — | **Logic**: `readHumidity*()`, `setDryFlow/setWetFlow` | — |
| `utils` | **425; 8/0** | — | **Logic**: peak-stats `ps`, fitting, filename helpers, `purge_cryo` | — |
| `suspenders` | **63; 3/0** | — | **Logic + import side effects**: builds & `RE.install_suspender(...)` at import | — |

### 3.2 Where the split breaks down

- **Logic-only `smibase` modules with no `smiclasses` peer** — these are really *plan/utility
  libraries* misfiled as "base": `alignment` (1033 LOC), `beam` (553), `utils` (425),
  `pilatus`'s 9 functions (253), `config` (173), `humidity_cell` (96), `suspenders` (63). The
  `SMIBeam` and `SMI_*_detector` *classes* are even defined in `beam.py` (a "base" file), not in
  `smiclasses/`.
- **Most `smiclasses` modules are not "pure class defs"** — they bake `yield from bps.*`
  **plan-methods** into the class body (`pilatus` 39 defs, `linkam` 28, `manipulators` 9,
  `energy`, `bimorph`, `waxschamber`, `ioLogik`, …). This is fine in principle (a device may
  offer plan-methods), but it means "classes = pure definitions" is not the real boundary.
- **`bimorph`** is the only `smiclasses` module with **no** `smibase` peer (imported by `mirrors`).
- **Truly pure-instance `smibase` modules** (contract holds): `beamstop, machine, attenuators,
  crls, manipulators, mirrors, motors, slits, xbpms, ioLogik, electrometers, amptek, prosilica,
  bladecoater`.

**Conclusion for "does the base/class split make sense?":** Yes for *pure hardware* (a class +
a thin instantiation is a reasonable separation). No for the *logic* modules — `base`/`classes`
is the wrong axis there; the real axes are **device-classes vs. config vs. plans vs. utils**
(see the target layout in the restructure plan).

---

## 4. Device inventory by subsystem

> Notation: ✅ clean message target · ⚠️ usable but with caveats · ❌ device debt (blocks a tenet).

### 4.1 Detectors, beam, flux

| Global | Class @ file:line | Notes / cleanliness |
|---|---|---|
| `pil2M` | `SAXS_Detector` `smibase/pilatus.py:108` | SAXS Pilatus. Beamstop insert/remove/restore are proper **plans** ✅. `active_beamstop` recordable ✅. |
| `pil900KW` | `WAXS_Detector` `smibase/pilatus.py:77` | WAXS Pilatus. `waxs = pil900KW.motors` (`:134`); `waxs.arc` settable; `waxs.arc.position` is the arc-block readback ✅. |
| `pil2m_pos` | `pil2M.motor` (`DetMotor`) `smibase/pilatus.py:111` | ⚠️ **plans say `pil2M_pos`** (uppercase M) — `pil2m_pos.z` is the SDD. Name mismatch (see §5). |
| `amptek` | `SMIAmptek` `smibase/amptek.py:10` | Fluorescence SDD. `energy_channels` recordable ✅. Count-time is a python **property** (⚠️, standard nslsii MCA pattern). Its preset is **not** wired into `det_exposure_time` (dead branch, §4.5). |
| `pin_diode` | `QuadEMV33` `smibase/electrometers.py:39` | Transmitted flux; `current2.mean_value` hinted ✅. Has `averaging_time` (plans set it) ✅. |
| `pdcurrent2` | `EpicsSignal` `smibase/electrometers.py:30` | Plain readable ✅. |
| `xbpm2`, `xbpm3` | `XBPM` (current) `smibase/electrometers.py:12-13` | `.sumX/.sumY` hinted ✅ — I0 recordable, matches plans. |
| `xbpm1/2/3_pos` | `XBPM` (motor) `smibase/xbpms.py:6-8` | ⚠️ **second class also named `XBPM`** (`smiclasses/xbpms.py` vs `electrometers.py`) — confusing duplicate name. |
| OAV/OAV2/FS/WBS/VFM (+`_writing`) | `StandardProsilica*` `smibase/prosilica.py` | Webcams. Bugs: `*_writing` reuse non-writing `name` (`:39/44/49/54`); `OAV2` stage-sig keyed off `OAV.cam` (`:38`). |

**Detector exposure path (the #1 detector debt):** `det_exposure_time(exp_t, meas_t=1, …)`
(`smibase/pilatus.py:22`) is a **bare function** doing `.set()`/`.put()` with a swallow-all
`try/except` fallback (`:48-55`) and a blocking `w.wait()` (`:46-47`). It is called
**synchronously inside ~13 live alignment plans** (`alignment.py:197,240,289,322,372,419,451,
492,639,941,995`) and `startWAXS` (`:224`). Camera energy/threshold (`set_energy_cam`
`smiclasses/pilatus.py:717`) is likewise `.put()`-based and has a latent `thresh_ev=None`
`TypeError` (`:720`).

### 4.2 Motion & optics

> **HARDWARE CHANGE (2025/2026) — read before using this table.** Per beamline staff, the
> **coarse stage was replaced**: the old **hexapod** is gone, replaced by a **Huber stage** (the
> device **kept the name `stage`**) with a **Huber phi axis** that replaces the old `prs`
> (precision rotation stage). **`piezo` (the SmarAct fine stage) is UNCHANGED** — it still sits on
> top of the (now Huber) coarse stage. So the GI double-stack is now **Huber `stage` (coarse) +
> `piezo` (fine)**. Consequences the audit flags as action items:
> - **`prs` is not "missing-to-be-readded"** — it is **superseded by the Huber phi axis**
>   (expected `stage.phi` or similar). `smi-plans` (`technique_I/J/K`, `_compose.motor_axis("prs",…)`,
>   the `.. important::` blocks) must be **repointed to the Huber phi axis**, not given a new `prs`.
> - **`piezo` references in `smi-plans` remain valid** (the fine stage is still there).
> - **`stage` API may have changed** with the Huber swap (axis set / phi attribute); confirm
>   `stage.*` against what plans call. `STG_pseudo`'s rotation-center math was for the old stack
>   and is almost certainly obsolete. See Open Question Q-Huber in `STARTUP_RESTRUCTURE_PLAN.md`.

| Global | Class @ file:line | Cleanliness |
|---|---|---|
| `piezo` (SmarAct fine — **current**) | `SMARACT` `smibase/manipulators.py:12` | ✅ **Unchanged by the Huber swap.** `.x/.y/.z/.th/.ch` clean `EpicsMotor`; `piezo.th` = incident angle. PVs hardcoded in class body (⚠️ should be a prefix). |
| `stage` (**Huber** coarse — current) | `STG` (verify class post-swap) `smibase/manipulators.py:10` | Coarse stage; was hexapod `.x/.y/.z/.th/.ph/.ch`. **Confirm the Huber axis set + the phi attribute** (replaces `prs`). |
| `bdm` | `BDMStage` `smibase/manipulators.py:7` | ❌ `.x/.y/.th` are bare `EpicsSignal` write_pv pairs — **no move-completion**; `bps.mv` returns on write, not on arrival. Not in baseline. |
| `stage_pseudo` | `STG_pseudo` `smibase/manipulators.py:11` | ❌ `name="stage"` **collides** with real `stage`; **zero consumers**; rotation-center math was for the old hexapod stack → the ~400-LOC `STG_pseudo` class is dead and almost certainly obsolete with Huber. |
| `att1_1..12`, `att2_1..12` | `Attenuator` `smibase/attenuators.py:9-33` | In baseline ✅. `att2_*` present (plans use `att2_9`). Driven via `bps.mv(att.close_cmd,1)` in `beam.py` (message-based ✅) but leaks raw command-PV semantics; `Attenuator.set()` is dead+blocking (§4.5). |
| `crl` | `CRL` `smibase/crls.py:6` | 12 lens motors + stage; clean `EpicsMotor` ✅. No pseudo-positioner for energy→lens-count (logic lives in plans). |
| `hfm/vfm/vdm` | `MIR` `smibase/mirrors.py:7-9` | Clean `EpicsMotor` ✅. |
| `hfm/vfm_voltage` | `HFM/VFM_voltage` `smibase/mirrors.py:12,14` | Bimorph HV. Methods are `bps.mv`/`bps.sleep` **plans** ✅. Hardcoded voltage tables + magic `-80` offset (⚠️ config debt). |
| `wbs/ssa/cslit/eslit/hfmslit/vfmslit/dsa` | `SLIT/SLTH/SLTV/APER` `smibase/slits.py` | Clean `EpicsMotor` ✅. Baseline gaps (`wbs`, `hfmslit`, `vfmslit`). |
| `MDrive`, `thorlabs_su` | `MDriveMotor`, `ThorlabsMotor` `smibase/motors.py:8,17` | Clean. Baseline line commented out (⚠️). `SAXS`/`SBS` instances dead-commented. |
| **`prs`** | — | ❌ **DOES NOT EXIST** in the live profile (only commented/checkpoint refs). The slow φ axis from T7. See §5. |

### 4.3 Energy, machine, shutters, vacuum, IO, suspenders

| Global | Class @ file:line | Cleanliness |
|---|---|---|
| `energy` (`dcm`) | `Energy` (PseudoPositioner) `smibase/energy.py:6` | ⚠️ `bps.mv(energy, eV)` works and `energy.energy` reads back ✅, **but** `set()`/`forward()` carry blocking `.set().wait()` + `.put()/.get()` side effects (`smiclasses/energy.py:168-199,245-252`), and `small_move()` has a **live bug** `gapspeed`→`gap_speed` (`:297`). Duplicates: `dcm_config`/`dcm_theta` re-wrap `energy`'s motors. |
| `ring` + shutter-enable | `Ring` `smibase/machine.py:5` | RO machine signals ✅. `InsertionDevice.move()` does blocking `.set(1).wait()` to disengage brake (`smiclasses/machine.py:43`) — fires on every `bps.mv(energy,…)`. |
| `ph_shutter`, `GV7` | `TwoButtonShutter` `smibase/shutter.py:9,49` | ✅ **good pattern** — `set()` accepts `"open"/"close"`, status-tracked. `shopen/shclose` are plans. |
| `fs` | `SMIFastShutter` `smibase/shutter.py:44` | ❌ `open()/close()` are **methods** using `.put()`; **no `set()`** → not `bps.mv`-addressable. `check_status()` does `.get()` at import (`smiclasses/shutter.py:109`). |
| `saxs_bs`, `waxs_bs` | `SAXSBeamStops`, `EpicsMotor` `smibase/beamstop.py:9,10` | Clean `EpicsMotor` ✅ (consumers read `.position` in `beam.py`). |
| `chamber_pressure` | `Sample_Chamber` `smibase/waxschamber.py:18` | pump/vent are **plans** ✅ but poll via `.get()` inside the loop (⚠️). `waxs_saxs_valve` duplicates `GV7` PV. Two `TwoButtonShutter` classes in play (local vs `nslsii.devices`). |
| `moxa_in/out`, `diagA/B_pos` | `ioLogik1240/1241`, `Diag_Module` `smibase/ioLogik.py` | `Diag_Module` digital bits driven by `bps.mv` ✅ (**template** for trigger/gate bits). Moxa channels are clean Signals; their consumers (humidity) bypass messages (§4.4). |
| suspenders | `smibase/suspenders.py` | Fire on ring current, front-end shutter-enable, WAXS/φ over-temp. ❌ installed at **import time**; `stop_turbo()` creates ad-hoc `EpicsSignal` + `.put()` and duplicates `chamber_pressure` PVs; `pre_plan=stop_turbo()` (commented) is buggy (calls the fn). |

### 4.4 Sample environment (the named device-debt cluster)

| Global | Class @ file:line | Cleanliness |
|---|---|---|
| `LThermal` | `LinkamThermal` `smibase/linkam.py:5` | ❌ **`LThermal.temperature()` is a method doing `.get()`** (`smiclasses/linkam.py:98`) — the named DEVICE_DEBT item. **The readback Signal already exists**: `temperature_current` = `EpicsSignalRO("TEMP")` (`:53`). `on/off/setTemperature/setTemperatureRate` use `.put()` (`:80-95`). |
| `LTensile` | `LinkamTensile` `smibase/linkam.py:6` | ❌ Inherits the same debt; many `.put()/.get()` methods + busy-loops; broken `states()` (refs nonexistent attrs `:529-531`); `arr2word` undefined (`:104-126`). PV prefix has a stray `:` (`smibase/linkam.py:6`). |
| `ls` | `new_LakeShore` `smibase/electrometers.py:8` | ⚠️ Closest to a clean Heater: `ls.output1.mv_temp(T)` is a **plan** ✅ and `ls.input_A_celsius` is readable ✅. Caveats: readback & setpoint on different sub-devices; no units; `output_lakeshore.D` maps to the **I-gain PV** (bug, `smiclasses/electrometers.py:11`); `output1..4` are class-attr instances, not `Cpt`s (`:38-41`). |
| humidity | — (functions only) `smibase/humidity_cell.py` | ❌ **`readHumidity()`/`readHumidity2()` are functions** doing `moxa_out.chN_read.get()` (`:7,22`) — the named DEVICE_DEBT item. **No RH PV exists** (value is computed from raw voltage `…AI:1-I` with offset 0.816887 / slope 0.028813 / T-correction). `setDryFlow/setWetFlow` use `.put()` + blocking `time.sleep` (`:43,51`) and have a no-`return`-on-bad-input bug. `setHumidity` does **not** exist. |
| `bc_smaract` | `bladecoater_smaract` `smibase/bladecoater.py:6` | Clean `EpicsMotor` ✅. |
| `syringe_pu` | `syringe_pump` `smibase/bladecoater.py:7` | ⚠️ All `EpicsSignal`; driven via `bps.mv` ✅, but `vol/rate` are SP-only (no actual-flow readback); not in baseline / not hinted. |
| Instec / potentiostat / 3D-printer | — | **Do not exist** in the profile (exhaustive search). `syringe_pu` is the only fluidics device; humidity MFCs are Moxa voltages. |

### 4.5 Cross-cutting device-quality notes

- **Import-time hardware/RE side effects**: `SMI = SMI_Beamline()` runs `.get()/.position/.put()`
  at module load (`beam.py:547`); `PilatusDetectorCamV33.energyset` reads EPICS at *class
  definition* (`smiclasses/pilatus.py:53`); `suspenders` installs at import; `base.py` reads a
  secret file + opens Tiled/Redis at import.
- **Dead-but-dangerous**: `Attenuator.set()` (`smiclasses/attenuators.py:47-74`) — blocking
  `.set().wait()` + `.get()` loop, bare `except: pass`, **unconditional `st.set_finished()`**
  (false success). No caller today, but `bps.mv(att, 'Insert')` would block the RE.
- **Latent bugs**: `pil900kwroi1` assigned 4× (roi2-4 lost, `smibase/pilatus.py:80-83`);
  WAXS TIFFs templated `_SAXS.tif` (`smiclasses/pilatus.py:59`); `save_beamstop` compares a
  Signal object to a string (`:538`); `remove_rod/remove_pin` construct but never `raise`
  (`:507,517`); `SMI_SAXS_detector` defines `x0_pix` with `name=…y0_pix` (`beam.py:38`).
- **Secrets in source**: SSH host/user/password for the detector PC (`smibase/pilatus.py:155-157`).

### 4.6 Config & calibration persistence (file vs. Redis vs. hardcoded)

The beamline has begun migrating persistent config/calibration into a **Redis-backed dict**
(`mdsave = RedisJSONDict(...)`, `smibase/base.py:25`). State of play:

- **Already in Redis (the reference pattern):** all SAXS beam-center / beamstop / sample-Z
  offsets and the **`distance_calibration` SDD lookup table** live in `mdsave`
  (`smiclasses/pilatus.py:411-429` seed via `Cpt(Signal, value=mdsave.get(key, default),
  kind="config")`; `:546-562,676-681` persist via `mdsave[key] = sig.get()`). `beam.py`'s
  `interpolate_sdds()` (`:440`) reads from Redis. **This is the model to propagate.**
- **Still file-based:**
  - `smi_config.csv` (repo root, 373 rows back to 2019) — referenced **only in dead
    `.ipynb_checkpoints/` and the non-startup `scripts/`**; effectively already abandoned.
  - `intepolation_db_sdd2.txt` (`startup/`) — referenced **only in dead checkpoints**.
  - `agb_z_calibration_results/*.npy` (16 arrays) — **raw scan staging** read by
    `scripts/build_distance_calibration.py`, which itself **writes results into `mdsave`**. So the
    files are offline-calibration inputs, not live config (reasonable to keep as staging).
- **Hardcoded-in-source config (neither file nor Redis — the real migration targets):**
  - `smiclasses/energy.py:144-145` — IVU-gap experimental energy/offset arrays.
  - `smiclasses/bimorph.py` — `default_hfm_v2`/`default_vfm_v2`/`default_vfm_opls` voltage tables +
    magic `-80` offset.
  - `smiclasses/manipulators.py:245-258` — `STG_pseudo` rotation centers (**obsolete** with the
    hexapod→Huber swap).
- **`RE.md` as a config store (anti-pattern):** `beam.py:423-425` stuffs
  `beamline_sample_environment` / `beamline_attenuators` into `RE.md` strings.

**Note for `smi-plans`:** because Redis is **process-shared**, config in `mdsave` is visible to
*both* the IPython terminal and a future QS worker; file- or `RE.md`-based config is **not**. This
makes the Redis-config migration a soft prerequisite for a good queueserver experience (see the
QS section + Redis-config workstream in `STARTUP_RESTRUCTURE_PLAN.md`).

---

## 5. What `smi-plans` expects vs. what the profile provides

The `.. important::` blocks across `smi-plans` modules reference these globals. Status:

| Plans expect | Status | Evidence / action |
|---|---|---|
| `piezo` (`.x/.y/.z/.th`) | ✅ exist, clean, **unchanged** | SmarAct fine stage still present; `smibase/manipulators.py:12`. |
| `stage` (`.x/.y/.z/.th`) | ⚠️ **coarse stage replaced (hexapod→Huber)** | `stage` is now the Huber coarse stage; confirm axis set + phi attribute. See §4.2. |
| `energy` (`bps.mv(energy, eV)`, `{energy_energy}`) | ✅ works (with internal debt) | `smibase/energy.py:6`; see §4.3 |
| `xbpm2/3.sumX`, `pin_diode`, `pdcurrent2` | ✅ recordable | `smibase/electrometers.py` |
| `att2_9`, `att2_*` | ✅ exist | `smibase/attenuators.py:30` |
| `waxs`, `waxs.arc` | ✅ exist | `waxs = pil900KW.motors` `smibase/pilatus.py:134` |
| `pil2M`, `pil900KW` | ✅ exist | `smibase/pilatus.py:108,77` |
| `det_exposure_time(t, t)` | ⚠️ exists as a **blocking function** | `smibase/pilatus.py:22`; must become a plan (§4.1) |
| Lakeshore `ls.output1.mv_temp`, `ls.input_A_celsius` | ✅ exist | `technique_C` `lakeshore_heater()` works today |
| Linkam `LThermal.setTemperature/.on/.temperature` | ⚠️ exist but `.put()/.get()` debt | retire via `temperature_current` (§4.4) |
| `pil2M_pos` (SDD `.z`) | ⚠️ **name mismatch** | profile defines **`pil2m_pos`** (lowercase). Reconcile casing. |
| **`prs`** (φ; CD-SAXS/tomography/XRR) | ❌ **SUPERSEDED by Huber phi axis** | The old `prs` is gone (hexapod→Huber swap). Used by `technique_I/J/K` and `_compose.motor_axis("prs", …)` → **repoint to the Huber phi axis** (e.g. `stage.phi`), do **not** re-add `prs`. Confirm the exact attribute with staff. |
| `pil300KW` (WAXS) | ❌ missing (commented out) | `smiclasses/pilatus.py:283-302` |
| `rayonix` (MAXS) | ❌ missing | no class/instance |
| `saxs_waxs_dets` | n/a (provided by `smi_plans._core`) | not a profile global — clarified to avoid a false "missing" |

---

## 6. Import graph & the un-importability blockers

- **`smibase ← smiclasses`** (expected): each simple base module does `from smiclasses.X import …`.
- **`smiclasses → smibase` (the cycle)**: `smiclasses/pilatus.py` imports
  `from smibase.energy import energy`, `from smibase.base import RE, mdsave`,
  `from smibase.beamstop import SAXSBeamStops`; `smiclasses/prosilica.py` imports
  `from smibase.base import RE` and `from .pilatus import TIFFPluginWithFileStore`;
  `smiclasses/energy.py` imports `from .machine import InsertionDevice`. So
  `smibase.pilatus → smiclasses.pilatus → smibase.base/energy/beamstop` is **circular**.
- **23 `get_ipython().user_ns[...]` grabs** across 21 `smibase` modules pull
  `RE` (base, base_dev, config, suspenders), `bec` (base, alignment), `sd` (15 modules doing
  `sd.baseline.extend(...)`), `db` (utils). Combined with import-time `RE`/`sd` mutations and the
  secret/Tiled/Redis reads in `base.py`, these are **the core reasons the code cannot be imported
  or unit-tested outside a live, configured IPython profile** — and why the startup order in
  `startup.py` is a fragile manual topo-sort.

---

## 7. The good patterns already present (imitate these)

These are already message-pure generators and are the **refactoring templates** for the rest:

- `Diag_Module.fs_in/out/pd_in` — `bps.mv` digital-IO sequences (`smiclasses/ioLogik.py:37-67`).
- `output_lakeshore.turn_on/turn_off/mv_temp` — clean setpoint plans (`smiclasses/electrometers.py:14-21`).
- `Sample_Chamber.pump/vent/...` — plan-methods (`smiclasses/waxschamber.py:30-89`).
- `SAXS_Detector.insert_beamstop/remove_beamstop/restore_beamstop` — `bps.mv` plans
  (`smiclasses/pilatus.py:478-529`); `active_beamstop` is recordable.
- `TwoButtonShutter.set()` — a real status-tracking `set` (`smiclasses/shutter.py:30`).
- `bimorph` HV controllers — `bps.mv`/`bps.sleep` plan-methods (`smiclasses/bimorph.py`).

---

## 8. Device-debt register (ranked)

> Each item names the **clean fix** and the **`smi-plans` payoff** (which wrapper/tenet it retires).
> The phase column points to `STARTUP_RESTRUCTURE_PLAN.md`.

### Critical — blocks a tenet for real, live plans

| # | Device | file:line | Debt | Clean fix | Payoff | Phase |
|---|---|---|---|---|---|---|
| C1 | Linkam live T | `smiclasses/linkam.py:98` | `LThermal.temperature()` is a method (`.get()`) | use existing `temperature_current` (`:53`) as readback; give it units | retires `_devices.linkam_temperature_signal` | 2 |
| C2 | Linkam setpoint | `smiclasses/linkam.py:80-95` | `setTemperature/on/off` use `.put()` | plan stubs / a `Heater`/PVPositioner with `done` | `technique_C.linkam_heater` drops the wrapper | 2 |
| C3 | Humidity read | `smibase/humidity_cell.py:6,21` | `readHumidity()` is a function (`.get()`) | `EpicsSignalRO`/`DerivedSignal` on `…AI:1-I` + conversion | retires `_devices.humidity_signal`; resolves `{rh}` | 2 |
| C4 | Humidity set | `smibase/humidity_cell.py:39-52` | `.put()` + blocking `time.sleep`; no-`return` bug; no `setHumidity` | `set_dry/wet_flow` plan stubs (`bps.mv`+`bps.sleep`); a `set_humidity` **plan** | `technique_G` `set_rh` becomes message-pure | 2 |
| C5 | Detector exposure | `smibase/pilatus.py:22` | `det_exposure_time()` is a blocking `.set()/.put()` fn (13 plan call sites) | a `det_exposure_time` **plan** (`bps.mv`); wire amptek preset | every alignment/technique exposure becomes message-pure (T5/T6) | 2 |
| C6 | Fast shutter | `smiclasses/shutter.py:111-117` | `fs.open()/close()` `.put()`; no `set()` | add a status-tracking `set()` so `bps.mv(fs,"open")` | message-based fast-shutter control | 2 |
| C7 | `prs` φ axis | (missing) | undefined in the live profile | (re)instantiate as a normal positioner, or document removal | unblocks `technique_I/J/K` + `motor_axis("prs")` (T7) | 2 |

### High — latent T5 violations / abstraction debt / filename-baking

| # | Device | file:line | Debt | Clean fix | Phase |
|---|---|---|---|---|---|
| H1 | Energy | `smiclasses/energy.py:168-199,245-252,297` | blocking `.set().wait()`/`.put()/.get()` in `set/forward`; `gapspeed`→`gap_speed` bug | move feedback toggling to plan messages; fix the typo; keep `forward` pure | 0 (bug) / 3 |
| H2 | IVU brake | `smiclasses/machine.py:43` | blocking `.set(1).wait()` in `.move()` | express brake as a message-driven step | 3 |
| H3 | Attenuator | `smiclasses/attenuators.py:47-74` | blocking `set()` + bare `except` + false `set_finished` (dead) | implement the commented `Attenuation` positioner; `bps.mv(att,'Insert')` | 3 |
| H4 | `bdm` | `smiclasses/manipulators.py:39-42` | bare `EpicsSignal` (no move-completion) | make it a `PVPositioner`/`EpicsMotor` with `done`/tolerance | 3 |
| H5 | `config.sample_id` | `smibase/config.py:14` | mutates `RE.md["sample_name"]` (T4 anti-pattern) | drop in favor of `acquire(..., md=...)`/`fname()` | 3 |
| H6 | `utils.get_scan_md`/`get_more_md` | `smibase/utils.py:385-424` | bakes `.position`/`.get()` into filename strings (T2/T3) | replace with `{token}` recorded-field approach | 3 |
| H7 | Camera energy/threshold | `smiclasses/pilatus.py:717` | `.put()`-based; `thresh_ev=None` TypeError | drive `cam.*` Signals via `bps.mv`; guard `None` | 0 (bug) / 3 |

### Medium — duplication, import-time side effects

| # | Item | file:line | Note | Phase |
|---|---|---|---|---|
| M1 | Import-time effects | `beam.py:547`, `smiclasses/pilatus.py:53`, `suspenders.py`, `base.py` | `.get()/.put()`/EPICS/secret/Tiled at import | 1/4 |
| M2 | Duplicate PV objects | `GV7`↔`waxs_saxs_valve`; `dcm_theta`↔`bragg`; `dcm_config`↔`energy`; `stop_turbo`↔`chamber_pressure` | one PV, two ophyd objects | 3 |
| M3 | Two `TwoButtonShutter` | `smiclasses/shutter.py` vs `nslsii.devices` (in `waxschamber.py`) | unify | 3 |
| M4 | `Insert/Retract` enum hazard | `smiclasses/shutter.py:13-14,27-28` | semantics differ for valves vs foils (noted in-code, unresolved) | 3 |
| M5 | LakeShore `D` PV | `smiclasses/electrometers.py:11` | maps to I-gain PV | 0 (bug) |
| M6 | LakeShore `output1..4` | `smiclasses/electrometers.py:38-41` | class-attr instances, not `Cpt` | 3 |

### Low — hygiene / dead code

| # | Item | file:line | Phase |
|---|---|---|---|
| L1 | Dead `STG_pseudo` (~400 LOC) + `stage_pseudo` name="stage" | `smiclasses/manipulators.py:102-504`, `smibase/manipulators.py:11` | 0 |
| L2 | Duplicate `readbackEpicsMotor`/`ReadbackEpicsMotor`; triplicated imports | `smiclasses/manipulators.py:54-99` | 0 |
| L3 | `pil900kwroi1` ×4; `*_writing` name reuse; `OAV2` stage-sig off `OAV.cam`; WAXS `_SAXS.tif` | `smibase/pilatus.py:80-83`, `smibase/prosilica.py:38-54`, `smiclasses/pilatus.py:59` | 0 |
| L4 | Hardcoded SSH password | `smibase/pilatus.py:155-157` | 0 |
| L5 | Unused imports; commented instances; baseline gaps (`bdm`, `MDrive`, `thorlabs_su`, `wbs`, `hfmslit`, `vfmslit`, `amptek_pos`, `syringe_pu`) | many | 0/3 |
| L6 | `Energy.small_move` typo, `linkam.states()`/`arr2word` broken, `remove_rod/pin` no `raise`, `save_beamstop` Signal==str | various | 0 |

---

## 9. Repo hygiene (outside the device code)

- **Tracked junk to remove:** `content.json` (**18.4 MB** of base64-encoded PNGs — an accidental
  agent payload dump) and `mi.setDirectBeamROI` (a stray IPython `??` introspection dump).
- **On-disk cruft (untracked, but present):** multi-version `__pycache__/`
  (cpython-39/310/311/312 mixed) and `startup/.ipynb_checkpoints/` full of **old numbered-startup
  fossils** (`00-base-checkpoint.py`, `30-user-checkpoint.py`, …) that predate the
  `smibase/smiclasses` split.
- **CI is stale:** `azure-pipelines.yml` targets py310/311/312 via the shared
  `NSLS-II/profile-collection-ci` templates, contradicting `pixi.toml`'s Python 3.12 pin; it is a
  smoke-import check, not a test suite.
- **No device-level tests** exist (cf. `smi-plans`, which has `pytest` + `ophyd.sim`).
  `acceptance_tests/run_all_tests.py` is a 16-line live-hardware `%run -i` script.
- **Legacy catalog:** `smi-catalog.yml` is an intake/databroker-v1 Mongo catalog; the live stack
  is Tiled (`base.py`). Likely removable.

---

## 10. Headline conclusions

1. **The device layer is ~80% tenet-ready.** Motion, energy, flux, detectors, attenuators are
   real ophyd objects plans can drive; the *good patterns* (§7) already exist.
2. **A short, concrete debt list (§8 Critical)** — humidity, Linkam, exposure-time, fast shutter,
   the Huber-phi repoint — is what currently forces `smi-plans` into `_devices.py` wrappers or
   blocks whole techniques. Fixing those items has outsized payoff and directly retires
   `DEVICE_DEBT.md` entries.
3. **The structure problem is real but separable from the debt.** The `smibase/smiclasses` axis
   is wrong for the 7 logic modules and is circular; 23 `user_ns` grabs make it un-importable.
   Packaging is worthwhile, but should be done in **hygiene-first, beamline-safe phases** so
   device fixes can land independently — see `STARTUP_RESTRUCTURE_PLAN.md`.
4. **Queueserver readiness is gated on the same cleanup** (specifically the Phase-4 instances
   factory that removes the `get_ipython()` grabs) plus four QS-specific artifacts, and benefits
   from moving config into the process-shared Redis dict — both detailed in the restructure plan.
