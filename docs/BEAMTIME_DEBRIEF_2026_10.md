# September/October 2026 acquisition debrief

## Delivered reusable pieces

| Beamtime need | Public helper / existing primitive |
|---|---|
| Holder, new mount, sparse/blank/ID subsets | `load_holder`, `select_samples` |
| User/GUI-readable frozen scan intent | `GrazingScan`, `from_lists`, `preview`, JSON round-trip |
| Hardware wiring without hard imports | `GrazingContext`, `from_namespace` |
| Alignment + measurement settings + checks | `grazing_scan`, `capture_alignment`, `AlignmentState` |
| Few theta moves or few energy moves | `fast_axis='energy'/'incidence'`, explicit run grouping |
| Recorded-event continuation across loop order changes | `ScanCoverage`, `coverage_from_tiled` |
| One XY position per exposure, anchored or shifted | `fresh_spot_grid`; compose via `ScanAxis`/`per_point` |
| Explicit energy devices in library plans | `energy_axis(..., device=...)`, `move_energy_fb(..., device=...)` |
| Read-only log examination and basic timing | `recent_log_lines`, `run_timing` |

User recipes: [GRAZING_WORKFLOWS.md](GRAZING_WORKFLOWS.md).
Builder contract: [GRAZING_AGENT_CONTRACT.md](GRAZING_AGENT_CONTRACT.md).
Operational support: [SCAN_ERROR_RECOVERY.md](SCAN_ERROR_RECOVERY.md).

## Repository ownership decision (2026-10-02)

The historical ad hoc plans, numbered restart scripts, list setup scripts, timing
artifacts and their script-specific tests have moved to
[SWAXS_user_scripts/historical/2026-09_10_alignment_energy](https://github.com/NSLS-II-SMI/SWAXS_user_scripts/tree/main/historical/2026-09_10_alignment_energy).
Future user-requested ad hoc scripts belong in SWAXS_user_scripts. Reusable APIs,
library tests and design/workflow documentation stay in smi-plans. The scope and
file inventory are recorded in [SCRIPT_PLACEMENT.md](SCRIPT_PLACEMENT.md) and both
repositories' agent guidance.

## Lessons that changed the design

1. **Loop order is an experimental parameter.** Frequent stage-theta cycling
   heated the stage. Holding theta across an entire energy list reduced main-scan
   commands from 540 to five. Energy changes and resets then cost more time; make
   that tradeoff explicit in the GUI and preview.
2. **Alignment is a separate acquisition.** It opens runs, stages detectors and
   changes exposure, foils, and sometimes arc. Run it outside measurement staging.
   Restore measurement settings after it. The 1 s plan otherwise acquired with
   the alignment's 0.3/0.5 s setting.
3. **Pair alignment motor and measurement motor.** Hex alignment uses stage theta;
   piezo alignment uses piezo theta. Capture the zero once for all related runs.
4. **A finished move can have a wrong readback.** We observed ~0.05–0.30 degree
   errors. Check final alignment target and each measurement theta. Do not widen
   tolerance or retry indefinitely to hide a thermal/device failure.
5. **One fit failure emitted an enormous height target.** Fitting bounds, peak/edge
   quality decisions, detector ROIs and motor-status reliability belong in the
   profile collection. Acquisition wrappers must stop and preserve evidence.
6. **Harmonic target is not a lock.** `target_harmonic` was a search ceiling. Use the
   profile's `with_harmonic_lock`, including same-energy gap establishment and
   cleanup. Keep IVU tracking on; don't freeze gap as a substitute.
7. **Filename labels do not move devices or select detectors.** Move arc explicitly,
   select detectors afterward, and record incidence/energy/BPM3 tokens from events.
8. **Boundaries matter for dose and limits.** Center/top-middle/top-left layouts
   solved different mounting constraints. A stored X can itself be below a limit;
   changing the grid anchor is insufficient. Keep numerical Y direction explicit.
9. **Recovery needs identity, not only count.** Some runs were marked success but
   short. Alignment UIDs interleaved with measurement UIDs. Stable point IDs, batch
   IDs, materialized lists, aligned positions, and exact foil records replace
   hand-written numerical restart offsets.
10. **No saved event does not necessarily mean no dose.** An energy move failed
    before XY/trigger in one case; other failures could occur after trigger. Logs
    determine that distinction. Wrong-angle saved events remain exposed data.
11. **Holder contents change during beamtime.** Reload deliberately for new batches;
    freeze IDs for continuation so newly added sparse/blank positions are not
    silently added to the old recovery.
12. **Tender versus hard is more than an energy move.** Optics, beamstop, detector
    setup and calibration remain beamline setup responsibilities.

## Timing baseline

[Initial timing examination](https://github.com/NSLS-II-SMI/SWAXS_user_scripts/blob/main/historical/2026-09_10_alignment_energy/HOLDER2_TIMING_EXAMINATION.md) used 72 full runs and
6,166 events: ~6.13 s/event at WAXS 0, ~6.26 s with SAXS/WAXS at 20, ~95 min/sample
for five edges × two arcs, and ~12 h 47 min for eight samples excluding interruptions.
Those numbers are calibration-specific, not universal multipliers for grazing scans.
Energy resets and arc/sample gaps need their own terms. Do not add exposure/settle
again to a measured event interval. Tiled's actual `time` field is essential;
an xarray dimension index is not an epoch timestamp. `run_timing` validates that.

## Environment/testing lessons

- Use `pixi run test` / `pixi run pytest ...` in smi-plans. The profile uses its
  own Pixi environment (e.g. `pixi run -e terminal ...` there). An OS `python` may
  be missing, and an installed package can shadow the checkout; root conftest
  intentionally prepends `src`.
- Resolve Redis/Tiled dependencies within Pixi and retain its lockfile. Binary
  dependencies such as PyArrow and Blosc may need conda-forge packages on the older
  beamline OS rather than attempting unsupported PyPI source builds.
- `auto/smi/raw` worked for this session's remote Tiled access. The older `smi`
  profile was a direct-server configuration; it is not interchangeable. Never
  print secrets or ask users to paste tokens into chat.
- Test simulated RunEngine documents, not just `list(plan)`: plans need real
  responses to reads. Verify event identity, phase/angle grouping, foil/exposure
  restoration, failure-before-exposure, and completed-sample skipping.
- The new reusable workflow tests restore injected globals to avoid contaminating
  later QueueServer resolution tests. Some older ad hoc tests still leak globals.

## Verification snapshot and outstanding work

After relocating the historical scripts and their tests (2026-10-02):

- Library suite: **287 passed, 1 skipped, 1 failed**. The remaining failure is the
  existing source-purity guard classifying `globals().get`, region dictionary
  lookups and Redis publication as direct hardware calls. The previously observed
  device-resolution ordering failure did not recur in this library-only run.
- Historical archive suite: **24 passed**, using the shared library simulation
  fixture with per-test restoration of injected globals.
- New reusable modules and their tests pass Ruff; both repository diffs pass
  whitespace checks. These are simulation checks, not live commissioning.

### Earlier integration history

Targeted existing composition/holder/list plus new workflow tests passed (61 at the
first integration pass; additional diagnostics/grid tests added subsequently).
The full suite initially reported 282 passed, 1 skipped, 4 failed. Running just the
tracked preexisting suite reproduced two analysis-fit failures and the existing
message-purity failures (dict lookups/Redis publication classified as device calls).
The fourth full-suite failure was the QueueServer missing-device test in the presence
of injected simulation globals. These are recorded rather than changing scientific
fit behavior or weakening purity checks as part of this acquisition refactor.
The new workflow/diagnostics tests together with QueueServer tests passed in a clean
process (28 passed); running legacy smoke tests before QueueServer reproduces the
existing injection-order failure. New production modules and new tests pass Ruff.

Follow-ups:
- GUI/QueueServer adapter for the new data-only specification; no automatic queue
  registration is claimed yet. Worker must resolve device names to context objects.
- Live commissioning of the reusable library plan before replacing the field-tested
  ad hoc scripts. Simulated verification does not validate beamline optics or fits.
- Automatic batch catalog queries and persistent dose ledger, with explicit policy
  for interrupted triggers and remeasurement of bad-quality points.
- Stable setup fingerprints beyond exposure/foils (optics/SDD/calibration identity).
- Per-message timing hooks to separate energy, theta, detector, and callback costs.
- Generic aligned-state persistence/backed-up recovery files and UI review of
  proposed remaining pairs. Current coverage JSON is explicit and user-managed.
