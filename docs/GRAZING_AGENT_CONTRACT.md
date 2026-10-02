# Contract for smi-acquire / coding agents

## Build with library APIs

Import public helpers from `smi_plans`; do not generate a clone of a numbered
beamtime continuation script. See [GRAZING_WORKFLOWS.md](GRAZING_WORKFLOWS.md).

1. Resolve holder name and explicit selection via `load_holder`/`select_samples`.
2. Resolve named lists with `GrazingScan.from_lists`; keep the materialized spec.
3. Set `fast_axis`, `theta_axis`, angles, arcs, exposure, factor, checks and tokens
   explicitly in a GUI-generated command. Preview sample names/IDs, counts, range
   and topology. A detector pair triggered simultaneously is one event, not two.
4. Bind the existing profile's alignment and harmonic-lock wrappers using
   `GrazingContext.from_namespace`, or explicit devices in a worker adapter.
5. Execute only through the user's RunEngine. All helpers yield messages. Do not
   import profile instances merely to inspect a plan, or trigger live motion during
   a code review/test.

## Data-only specification, version 1

`GrazingScan.to_dict()` is JSON-safe. Fields:

- `edges`: ordered `[label, [energies_eV...]]` pairs; unique labels and values.
- `angles`: ordered unique relative degrees.
- `arcs`: ordered unique WAXS arc positions, degrees.
- `fast_axis`: `energy` (default, fixed theta) or `incidence`.
- `theta_axis`: Position field key, typically `stage_theta` or `piezo_th`.
- `exposure`, `settle`: seconds; exposure > 0, settle >= 0.
- `attenuation_factor`: >= 1, applied at first edge's first energy after alignment.
- `damage_checks`: before/after angle scans at reference energy, per sample/arc.
- `theta_tolerance`: positive degrees, not an excuse to ignore a failed motor.
- `harmonic`: null for range-based profile selection or an explicit odd integer.
- `name_tokens`: event-backed strings; e.g. `bpm3{xbpm3_sumX}` requires BPM3 in reads.

Use `GrazingScan.from_dict(payload)` for validation. Do not mutate an existing
spec's nested lists; construction normalizes them to tuples. `fingerprint` covers
all settings except fast-axis order. This intentionally conservative first API
does not allow arbitrary acquisition-setting changes during continuation.

## Context/ownership boundary

Context accepts actual device objects and callables, not JSON device names:
`energy`, `arc`, `motors`, `detectors()`, `exposure(t,t)`, `harmonic_lock(plan,...)`,
`align(sample)`, `attenuation`, `reads`.

The GUI/worker owns name resolution; no QueueServer registration is automatic.
Profile owns alignment algorithm, motor internals, energy/IVU policy, optical modes,
attenuator calibration and harmonic range selection. smi-plans owns sequencing,
recordkeeping and comparisons of commanded intent to readback.

The default context records all supplied motors and common monitors. For unusual
devices provide a context with appropriate readables/motor fields. A theta motor
must expose its name as a scalar readback key. A missing optional foil-identity
signal means exact foil identity cannot be guaranteed on continuation.

## Run/event schema

Start documents contain top-level `sample_id`, `holder_id`, and `grazing_scan`:
`schema_version=1`, `batch_id`, `fingerprint`, full `spec`, `sample_order` (stable IDs),
`phase` (`before/main/after`), edge label, arc, planned `point_ids`, `alignment`,
`theta_readback_key`, optional `attenuation_inserted`, and `continuation_of` UIDs.
Profile wrapper adds `harmonic_lock`. Acquisition phase is also in scan/filename.

Primary events include `scan_point_id`, `energy_set`, `incident_angle`, `scan_arc`,
motor readbacks, and requested monitor/device readings. Point IDs are
`arc_index:phase:edge_index:angle_index:energy_index`; checks use edge_index=-1.
Identity is independent of fast-axis order and disambiguates checks from main data.

## Recovery procedure

1. Inspect logs, actual run metadata and event coverage. Never infer completion
   from a sample name, a printed range, success status, or last motor position.
2. Read explicitly selected runs with `coverage_from_tiled`; only closed measurement
   runs with schema are used. Stop-count mismatch means ingestion is incomplete.
3. Report completed samples, pending pairs and flagged exposed data to the user.
4. Establish whether the alignment is still valid and the fault is resolved.
   Explicitly supply `AlignmentState` when reusing a started sample. New samples
   align normally; fully covered samples are never repositioned.
5. Build continuation with the original spec (optionally changing `fast_axis`) and
   original sample IDs. Preserve batch ID; acquire new runs with source UIDs.
6. Refresh coverage after any further acquisition. Snapshot reuse can repeat dose.

Old ad hoc runs are not automatically migrated. A reviewed conversion can use
energy/angle pairs and baseline positions, but must identify duplicate/repeated
energies, zero conventions, checks and uncertain final triggers explicitly.

## Default response when a user only says “help, it failed”

Follow [SCAN_ERROR_RECOVERY.md](SCAN_ERROR_RECOVERY.md). Begin with bounded read-only
log examination, identify the failed operation/sample/phase, then give an evidence-
based next step. Ask for a UID/time/command only when the logs and catalog do not
identify it. Distinguish a plan bug from a hardware/fit problem. Explain what the
continuation will repeat and what saved exposures it will skip.
