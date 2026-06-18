# Legacy SWAXS Patterns → smi-plans Mapping

## What this does

This skill captures the **knowledge distilled from a survey of ~230 legacy SMI user scripts**
(the `SWAXS_user_scripts` repo) and maps each recurring *legacy pattern* and *anti-pattern* onto
its modern `smi_plans` equivalent. It exists so that:

1. `smi_plans` "knows" the patterns it replaces (for code review, migration, and authoring).
2. The future task of **annotating each legacy function** in `SWAXS_user_scripts` (a comment
   noting how it would be re-implemented with `smi_plans`) can be done from this mapping.

> The full survey lives in `SWAXS_user_scripts/templates/_analysis/` (`USE_CASE_TAXONOMY.md`,
> `BEST_PRACTICES_DRAFT.md`, and the per-batch reports). This skill is the actionable summary.

## When to use this

- Migrating or reviewing a legacy `30-user-*.py` plan.
- Annotating legacy scripts with their `smi_plans` equivalent (the eventual cross-repo task —
  NOT to be done preemptively).
- Recognizing an anti-pattern a user is about to reproduce and steering them to the modern form.

## Beamline vocabulary (the device substrate)

- **Detectors:** `pil2M`=SAXS, `pil900KW`/`pil300KW`=WAXS, `rayonix`=MAXS,
  `amptek`=fluorescence-yield SDD, `pin_diode`/`pdcurrent2`=transmitted flux,
  `xbpm2`/`xbpm3` (`.sumX`/`.sumY`)=I0.
- **Geometry:** GI "double-stack" = SmarAct `piezo.{x,y,z,th}` (fine) + hexapod
  `stage.{x,y,z,th}` (coarse); `piezo.th`/`stage.th` = incident angle. `waxs.arc` = in-air WAXS
  arc (SLOW); `prs` = Precision Rotation Stage = phi (SLOW, CD-SAXS/tomography); `energy` = DCM
  (tender ~2.1–5 keV to hard ≥11 keV). SDD = `pil2M_pos.z`.
- **Beam conditioning:** attenuators `att2_*`, `SMIBeam().insertFoils`; `GV7` SAXS gate valve;
  `pil2M.insert_beamstop(...)`.
- **Environment:** Lakeshore `ls` (`ls.input_A`, `ls.output1.mv_temp`), Linkam `LThermal`,
  Instec; RH MFCs (`setDryFlow`/`setWetFlow`/`readHumidity`); syringe pump `syringe_pu`; 3D
  printer EPICS digital-IO bits.

`smi_plans` references these same bare globals (injected by the SMI profile collection); see the
`.. important::` block atop each module.

## The acquisition-maturity tiers (what you'll find, worst → best)

| Tier | Legacy signature | smi-plans target |
|---|---|---|
| 0 | `RE(...)` inside a `for`/`while`; or `cam.acquire.put(1)` + busy-wait → `/ramdisk/` (no documents) | a generator plan; `acquire(...)` or a configured-burst (`technique_N_xpcs`) |
| 1 | nested `for` + `bp.count(dets, num=1)` per point (one run per data point); `sample_id(...)` filename; context `.get()`-ed into the name | `acquire(...)` with stacked axes → ONE run; context recorded by the axes; `{token}` filename |
| 2 | one coordinated `bp.scan`/`grid_scan`/`list_scan` per unit, readbacks as channels; still `sample_id` | a preset / `acquire`; `technique_D_mapping` keeps `bp.rel_grid_scan` where it's the better primitive |
| 3 | `@run_decorator`+`@stage_decorator`+`trigger_and_read` but hard-coded bars, `.get()`-into-name, throwaway `target_file_name` Signal | `acquire(...)` with `SampleList` + auto `{token}` filenames |
| 4 | one run/sample, context as recorded Signals, filename from recorded fields (`nist/richter/Cl_nexafs.py`) | the target — what `smi_plans` produces by construction |

## Anti-patterns → fixes (highest priority first)

1. **`RE(...)` inside a plan / loop** (SSYang, OGang, QYu, Mao, HZhang, AFurst, JiaLu, SWong*,
   CFN 2024 drivers).
   → Make it one generator. The ONLY sanctioned `RE()`-from-Python is a closed-loop controller
   *above* the plans (`technique_M_autonomous.autonomous_loop`), never inside one.

2. **Detector triggering outside the RunEngine** — `cam.acquire.put(1)` + busy-wait
   (`chen_xpcs.grid_scan_xpcs`, Yu-Chung `trigger_alldet`, Gergaud `fly_scan_ai`).
   → `technique_N_xpcs.xpcs_burst_run` configures `cam.num_images` + a staged `trigger_and_read`
   so documents are emitted (Tiled-backed). For fly, use a real fly/monitor plan.

3. **Run-per-data-point** (`bp.count(num=1)` in nested loops) — the bulk of the corpus.
   → `acquire(name, dets, axes, ...)`: the loops become a `ScanAxis` stack inside ONE run.

4. **Filename as global mutable state** — `sample_id(...)`, `RE.md['sample'|'sample_name']`,
   even as an interprocess carrier (HZhang).
   → `md={'sample_name': fname(...)}` (handled by `acquire`); intent via `md={}`.

5. **Context baked into the filename string** via `.get()`/`.value`/`.position`/`time.time()`/
   `db[-1].table()` (universal — temperature, energy, xbpm, SDD, transmission, RH, strain, prs).
   → record the device/Signal in the event stream; the axis builders do this (`energy_axis` reads
   `energy`, `temperature_axis` records the heater readback, `incidence_axis` records the angle,
   etc.), and `{token}` resolves it into the filename.

6. **Hard-coded coordinate/sample tables in the plan body** (with commented-out alternates).
   → `SampleList.from_columns(...)` / `from_csv(...)` outside the plan.

7. **`global` / `RE.md` mutation** for alignment LUTs, timestamps, sample state.
   → pass via `md=`/arguments; record alignment offsets in `baseline=[...]`.

8. **Same function redefined many times** (Karen ×6, Patryk ×3, Gomez ×2 — last def wins).
   → one parameterized plan.

9. **Direct `cam.file_path.put(...)`** write-path overrides (Headrick, AFRL, chen_xpcs).
   → leave file writing to the configured Tiled/area-detector pipeline.

## Idioms to PRESERVE (they encode real physics) and how smi-plans keeps them

- **Fresh-spot dose walking** (`piezo.x = xs - counter*30µm`, `np.linspace` walks): a
  `motor_axis("x", piezo.x, [...], speed=FAST)` innermost, or `_preprocessors.fresh_spot_wrapper`.
- **Arc-conditional detectors** (`[pil900KW] if waxs.arc.position<15 else [..., pil2M]`):
  `_core.saxs_waxs_dets()`.
- **Slow-axis-outermost + `[::-1]` reversal:** order the axis list slow-first; `motor_axis(...,
  reverse_alternate=True)` snakes; the guardrail warns on bad order.
- **Beam-loss re-seek** (`if xbpm2.sumX.get()<50: re-move energy`): `energy_axis(...,
  flux_signal=xbpm2.sumX, flux_threshold=50)` or `_preprocessors.beam_loss_reseek_wrapper`.
- **Align-once/measure-many + failure logging:** alignment in the `align` pre-run hook (it opens
  its own runs/stages dets); offsets in `baseline`; `multi_sample_run` aligns up front then sweeps
  the slow axis once.
- **GI in-vacuum choreography** (GV7/atten/beamstop): the `setup` plan + `ensure_in_wrapper`.
- **Up/down energy passes, HDR brackets:** concatenate up+reversed values into one
  `energy_axis`; record an `energy_direction` Signal (see `technique_A_energy_edge`).
- **Equilibration-with-timeout** (`while abs(T-sp)>tol: sleep`, with a `time.time()` escape):
  `technique_C_temperature.goto_temperature` / `temperature_axis`.

## Use-case archetype → smi-plans entry point

These archetypes are **overlapping concerns, not exclusive bins** — most real scripts combine
several. Map each to "an axis or a setup step," then compose:

| Legacy archetype (examples) | smi-plans concern / entry point |
|---|---|
| Tender/NEXAFS edge (Richter, Gann, McNeil, Stingelin) | `energy_axis` / `technique_A_energy_edge` |
| GISAXS/GIWAXS + alignment, multi-sample bar (Fakhraai, Kim*, Ocko) | `incidence_axis` + arc `motor_axis` + `align`(pre-run) / `technique_B_grazing` |
| Temperature ramp/anneal/melt (Tenney, RPI, Harvard, AFurst) | `temperature_axis` / `technique_C_temperature` |
| Microfocus raster (Aiello, UCR, Clark, Ferron) | `spatial_grid_axes` / `technique_D_mapping` |
| Transmission/capillary/solution (Telles, Quan, Liu-Akron, Cai) | transmission geometry + spatial / `technique_E_transmission` |
| In-situ kinetics (Modestino, Murray, Chaney, bladecoating) | `time_axis` / `technique_F_kinetics` |
| RH / SVA (Richter, ETsai, Jones, Mao) | `rh_axis` / `technique_G_humidity` |
| Electrochemistry / doping (Meli, Karen, Richter) | `potential_axis` / `technique_H_echem` |
| CD-SAXS grating metrology (CDSAXS, Gergaud, Kline2) | `prs` rock as `motor_axis` / `technique_I_cdsaxs` |
| XRR incl. tender/liquid (Gann, Cordova, bounce_down_mirror) | `incidence_axis` / `technique_J_xrr` |
| Tomography / texture (CFN run_tomo, Kang, Tiwale, oleg) | `prs` rotation / `technique_K_tomography` |
| In-situ 3D-printing (ECD-3dprinterLutz, Printer) | external-master monitoring run / `technique_L_printing` |
| Autonomous/closed-loop (CFN DropletReactor, CDSAXS auto-align) | controller loop / `technique_M_autonomous` |
| XPCS bursts (chen_xpcs) | configured burst / `technique_N_xpcs` |
| Commissioning (AGB, atten ladder, BDM, microlistscan) | `technique_O_commissioning` |
| Manual swaps / hand-set conditions / "I set T by hand" | `manual_step`/`manual_axis`/`manual_loop` |

## How to annotate a legacy function (the FUTURE cross-repo task — guidance, do not run now)

When the time comes to add `smi_plans`-mapping comments to `SWAXS_user_scripts`:
1. Read the legacy function; identify its **concerns** (beam/q, geometry/apparatus, the scan
   loops = axes, any manual steps) and its **tier** (0–4) and **anti-patterns**.
2. Write a short comment of the form:
   `# smi-plans: acquire(<name>, <dets>, [<axis stack outer→inner>], setup=<align/atten>, ...)`
   naming the axis builders the loops map to, the preset if one fits, and which anti-patterns to
   drop (e.g. "replace sample_id + per-point bp.count with one acquire run; record T via
   temperature_axis instead of {temp}-in-filename").
3. Preserve the physics idioms (note them: fresh-spot, re-seek, equilibration).
4. Keep the comment in the legacy repo; the corresponding capability already lives here.

> Do this only when explicitly asked; it is a large, separate pass. This skill is the source of
> truth for the mapping when that work begins.

## Gold reference (study when in doubt)

`SWAXS_user_scripts/nist/richter/Cl_nexafs.py` — the cleanest legacy Tier-4 file: one staged
run, up/down energy sweep, and a filename templated from recorded fields
(`{energy_energy}eV_pd{pin_diode_current2_mean_value}_bpm2{xbpm2_sumX}_`). `smi_plans` generalizes
exactly this shape to every concern and to combinations of them.
