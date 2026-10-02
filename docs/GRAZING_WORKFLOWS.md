# Reusable holder grazing-incidence workflows

Public entry points are exported by `smi_plans`. These helpers replace copying
beamtime-specific plans and numbered restart scripts. Those scripts are now explicitly
historical recipes in [SWAXS_user_scripts](https://github.com/NSLS-II-SMI/SWAXS_user_scripts/tree/main/historical/2026-09_10_alignment_energy),
not a generic recovery interface. New ad hoc scripts belong in that repository;
see [SCRIPT_PLACEMENT.md](SCRIPT_PLACEMENT.md).

## A short Tender scan

In a configured Bluesky session with the profile's harmonic-lock wrapper loaded:

```python
from smi_plans import (
    GrazingContext, GrazingScan, ListStore, load_holder, select_samples, grazing_scan,
)

# A callback takes a Sample and returns a plan. Choose the actual alignment
# routine and theta motor together; smi-plans does not implement alignment fits.
context = GrazingContext.from_namespace(
    globals(), align=lambda sample: alignement_gisaxs_hex(0.2),
)
spec = GrazingScan.from_lists(
    {"InL3": "In L3 1ev 80 pts"},
    angles=[0.3, 0.4, 0.5, 0.6],
    store=ListStore.from_redis(),
    theta_axis="stage_theta",
    fast_axis="energy",          # theta outer; 80 energies at a fixed theta
    arcs=[20],                   # simultaneous SAXS/WAXS at this arc
    exposure=1, attenuation_factor=1,
    name_tokens=["energy{energy_set}", "ai{incident_angle}",
                 "wa{scan_arc}", "bpm3{xbpm3_sumX}"],
)
samples = select_samples(load_holder("Tender"))  # all entries, including blanks
print(spec.preview(samples))
RE(grazing_scan(samples, spec, context))
```

At each sample: move to its runnable recorded position; align; check final theta
target; capture aligned coordinates; restore exposure; set attenuation at the
reference energy; before-check; four fixed-theta energy runs; after-check.
At 80 energies and four angles this is 328 events/sample and six measurement runs,
plus the alignment's separate runs. Arc moves precede detector selection. All runs
are inside the **profile's** harmonic wrapper, including alignment runs.

`from_namespace` supplies the default device wiring (piezo + stage coordinates,
SAXS/WAXS selection, monitors, exposure plan, attenuation, harmonic wrapper). For
a different setup, instantiate `GrazingContext` explicitly. It is hardware-free
to import and does not create an EPICS, Tiled, or Redis connection.

### Change holder/subset without editing a plan

```python
samples = select_samples(load_holder("NewMount"))
samples = select_samples(load_holder("Hard"), pattern="*sparse*")
samples = select_samples(load_holder("Hard"), names=["BlankSi_11keV"])
samples = select_samples(load_holder("Tender"), exclude=["BlankSi_3p7keV"])
samples = select_samples(load_holder("Tender"), ids=["the-stable-sample-id"])
```

Selection preserves holder order. Names are exact/case-sensitive; an explicitly
requested missing or ambiguous name raises. Pattern matching uses shell wildcards.
No implicit special treatment of `AGB`, `blank`, or `sparse`. Select before building
the batch; loading a holder is a snapshot. Reload intentionally to include new
positions on a later invocation. A recovery should retain its original sample IDs.

For a subset saved for a later command, keep its IDs and resolve those against the
newly loaded holder. If the holder order was edited and exact original order matters,
rebuild that order explicitly from the saved ID sequence.

### Axis order and detector choices

```python
from dataclasses import replace

# For a motor that tolerates frequent incidence moves, favor fewer energy moves:
energy_outer = replace(spec, fast_axis="incidence")
# One energy per run, all incidence angles inside that run.

# Multiple edges, all on each sample before the next sample:
multi = GrazingScan.from_lists(
    {"MnK": "Mn K 1ev 87 pts", "FeK": "Fe K 1ev 87 pts"},
    [.1, .2], store=ListStore.from_redis(), arcs=[0, 20],
)
# Default context gives WAXS-only at 0 and SAXS+WAXS at 20.

# Override detector selection explicitly if the experiment requires it:
context.detectors = lambda: [pil2M]
```

The grouping is **sample → arc → phase → edge → slow axis → fast axis**.
Default `fast_axis="energy"` is the field-proven choice for reducing repeated
stage-theta moves/heating. One run per slow-axis value makes partial progress easy
to inspect; it is not one giant run for the entire holder. For a different run
topology, use `ScanAxis`, `nest_axes`, `acquire`, or `acquire_bar` directly.

### Blank and checks

```python
blank = GrazingScan((("blank", (11900,)),), (.05, .1, .15, .2, .25),
                    fast_axis="incidence", damage_checks=False, attenuation_factor=1)
RE(grazing_scan(select_samples(load_holder("Hard"), names="BlankSi_11keV"), blank, context))
```

Energies are **eV**, not keV. With checks enabled, each arc gets a before and after
incidence scan at the first edge's first energy; the after-check reverses the angle
order. They share the captured zero, position, exposure, and foils. Check exposures
add dose too. No automatic spatial walking occurs in `grazing_scan`.

Attenuation factor is `1/transmission`: 1 retracts foils, 10 requests ~10% transmission
at the reference energy. Foils are selected **after alignment**, once per sample.
Their transmission varies with energy; the setting is not recalculated at every
point. Exact foil identity is recorded/checked on recovery when the device exposes
`inserted`. Without that field, only the requested factor can be reproduced.

### Alignment reuse

An alignment function must leave the supplied theta axis at its zero. A successful
move status alone is insufficient: capture checks theta readback against its
setpoint, and every exposure checks `theta_zero + incident_angle`.

```python
from smi_plans import capture_alignment

aligned = {}
def align_and_capture():
    yield from alignement_gisaxs_hex(0.2)
    aligned[samples[0].id] = yield from capture_alignment(
        samples[0], context, "stage_theta", tolerance=spec.theta_tolerance,
    )
RE(align_and_capture())  # First position the intended sample if needed.
RE(grazing_scan(samples, spec, context, reuse_alignment=aligned))
```

Or call just `capture_alignment` immediately after an already completed alignment.
Do not call it after moving to a measurement angle and call that readback zero.
The context's `motors` determines which coordinates are saved/restored; include
all relevant axes. No automatic writeback to the holder database occurs. A remount
needs fresh alignment, not reuse of the previous mount's state.

## Continuation from saved events

After the beamline fault is resolved, gather **all measurement runs in the same
batch**, including any prior continuations. Do not guess from the latest alignment
UID or from `exit_status == "success"`: a stopped run can be successful but partial.

```python
from dataclasses import replace
from tiled.client import from_profile
from smi_plans import AlignmentState, GrazingScan, coverage_from_tiled

catalog = from_profile("auto")["smi"]["raw"]  # Site profile; not portable to every beamline.
runs = [catalog[uid] for uid in batch_run_uids]
coverage = coverage_from_tiled(runs)
measurement = next(r for r in runs if "grazing_scan" in r.metadata["start"])
info = measurement.metadata["start"]["grazing_scan"]
original = GrazingScan.from_dict(info["spec"])
remaining_spec = replace(original, fast_axis="energy")
original_samples = select_samples(load_holder("Tender"), ids=info["sample_order"])

for sample in original_samples:
    print(sample.name, len(coverage.pending(remaining_spec, sample.id)))
print("Flagged exposed data:", coverage.flags)

# Explicit decision: alignment remains valid after the interruption.
aligned = {sid: AlignmentState(**state) for sid, state in coverage.alignments.items()}
RE(grazing_scan(original_samples, remaining_spec, context,
                coverage=coverage, reuse_alignment=aligned))
```

The plan skips completed samples without positioning them, reuses original aligned
coordinates for started samples, and normally aligns unstarted samples. A partially
measured sample requires the explicit original aligned state. If that alignment was
bad, do not bypass this constraint: select the remaining unstarted samples as a new
batch or plan a reviewed fresh-position repeat. Changing alignment of an exposed
sample changes what “continuation” means.

`coverage` serializes via `to_dict()` / `ScanCoverage.from_dict(...)`. It is a
snapshot, not a live subscription: refresh after each retry. Only `fast_axis` may
change; exposure, angles, energy lists/order, arc settings, tolerances, and naming
are covered by the fingerprint. Duplicate list values are rejected because repeated
exposures require a separate repeat dimension, not ambiguous value matching.

### What recovery proves and does not prove

- `scan_point_id` encodes arc/phase/edge/angle-index/energy-index, independent of
  loop order. The energy and angle values remain separately recorded.
- Coverage means a saved primary event. A failed exposure that emitted no event
  may still have deposited dose; inspect logs before deciding to revisit it.
- Wrong-theta events are flagged but still skipped as exposed. Repeating them is
  a scientific/dose decision, not automatic repair.
- Open runs, inconsistent ingestion counts, mixed batches, changed settings,
  conflicting alignment states, and changed foil identity are rejected.
- Old ad hoc runs lack this schema; this API does not infer coverage from names.
  The historical scripts demonstrate reviewed conversions from their scalar data.
- Paused RunEngine plans should normally be resumed using the RunEngine after
  diagnosis. This API is for a new plan after a stopped/aborted run.

## Correlated fresh-spot paths for transmission

```python
from smi_plans import fresh_spot_grid
offsets = fresh_spot_grid(856, dx=100, dy=10, anchor="top_middle")
# 10 columns × 86 rows, four unused cells; X +/-450, Y 0..850 um.
next_block = fresh_spot_grid(856, dx=100, dy=10, anchor="top_left", block=1)
```

Use these pairs in a `ScanAxis` indexed by exposure, or an energy axis `per_point`
hook. Independent X and Y axes make a Cartesian product and multiply exposures.
`rows=21` means twenty 10-um Y moves per column. `shift=(100, 0)` explicitly moves
the whole path; anchors never repair an already out-of-range stored sample position.
Preflight the absolute footprint against motors and sample dimensions. Retain the
same origin/count/spacing for block recovery. A positive-Y “top” is only a convention;
ask about numerical motor direction. For grazing samples, Y walking changes height
and alignment; do not import the transmission recipe unchanged.

## Read-only timing measurements

```python
from smi_plans import run_timing
summary = run_timing(
    run.metadata["start"], run.metadata["stop"],
    run["primary"]["data"]["time"].read(),
)
```

This reports start-to-stop, start-to-first-event, final-event-to-stop and mean/median
event spacing. Failed runs retain their status. Group comparable complete runs by
energy range, detector combination, exposure, and loop order before extrapolating.
Compute between-run gaps separately and label interruptions rather than averaging
multi-hour waits into motor overhead. The beamtime-specific calibrated example is
in [HOLDER2_TIMING_EXAMINATION.md](HOLDER2_TIMING_EXAMINATION.md).
