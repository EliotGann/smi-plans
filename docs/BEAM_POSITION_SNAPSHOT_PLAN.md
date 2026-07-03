# Beam Position Snapshot Plan

Status: initial implementation complete in the SMI profile collection; waiting for broader in situ
testing.

Live implementation: `~/.ipython/profile_collection/src/smi_beamline/plans/beam_snapshot.py`.

Minimal live testing on 2026-07-03 confirmed:

- `save_beam_position_snapshot(...)` saves the current beam-positioning state to `mdsave`.
- `list_beam_position_snapshots()` lists the saved snapshot index.
- `restore_beam_position_snapshot(..., dry_run=True)` reports a selected changed motor as
  `would move` without moving hardware.
- `restore_beam_position_snapshot(..., dry_run=False)` restores a selected motor to the saved
  snapshot value.
- Bimorph restore uses the dedicated bimorph target/apply helpers, not motor-style moves.

Unit coverage exists in the profile collection at `tests/unit/test_beam_snapshot.py` for save,
compare, motor restore, and selected bimorph-channel restore behavior.

## Goal

Provide one shared way to capture, compare, and safely restore the major beam-positioning state.
The same implementation should support a command-line workflow, a future GUI, and QueueServer use.

The desired user workflow is:

1. Save a named snapshot of important beam-positioning devices.
2. Show a friendly dry-run comparison between the current machine state and a saved snapshot.
3. Restore all allowed settable devices to the saved values, with safety checks and explicit opt-in for risky groups.

## Requested Device Scope

Capture at least positions, user offsets, setpoints, and limits where those fields exist.

The requested device groups are:

- White beam slits.
- SSA slits.
- ES slits.
- C slits.
- All vertical and horizontal slit positions and gaps.
- DCM theta / Bragg, height, X, pitch, and roll.
- BPM 2 and BPM 3 beam positions, X and Y.
- Undulator gap.
- HFM, VFM, and VDM positions and pitch.
- HFM and VFM voltages.

## Current Coverage From Audit

The audit artifacts used here are in `docs/device_audit/`, especially `audit_master.csv` and
`audit_gaps.md`.

| Component | Coverage | Notes |
|---|---|---|
| White beam slits `wbs` | Modelled, not baseline | `h`, `hg`, `v`, `vg` are present as `wbs.h`, `wbs.hg`, `wbs.v`, `wbs.vg`. |
| SSA slits `ssa` | Modelled and baseline | `h`, `hg`, `v`, `vg` are covered. |
| ES slits `eslit` | Modelled and baseline | `h`, `hg`, `v`, `vg` are covered. |
| C slits `cslit` | Modelled and baseline | `h`, `hg`, `v`, `vg` are covered. A separate `Top` CSS motor appears unmodelled and should be clarified. |
| HFM/VFM slits `hfmslit`, `vfmslit` | Modelled, not baseline | Include if these are considered part of the beam-positioning snapshot. |
| DCM Bragg/theta | Modelled and baseline | `energy.bragg.user_readback`. |
| DCM height/gap | Modelled and baseline | Audit shows `energy.dcmgap.user_readback`; confirm this is the requested DCM height. |
| DCM pitch/roll | Modelled and baseline | Via `dcm_config.pitch` and `dcm_config.roll`. |
| DCM X | Not confirmed | No clear modelled DCM X axis was identified in the audit. |
| BPM2/BPM3 beam positions | Modelled and baseline | `xbpm2.posX`, `xbpm2.posY`, `xbpm3.posX`, `xbpm3.posY`. |
| BPM2/BPM3 physical motor positions | Modelled and baseline-only | `xbpm2_pos.x/y`, `xbpm3_pos.x/y`; not shown on audited CSS screens. |
| Undulator gap | Modelled and baseline | `energy.ivugap.user_readback`. The setpoint and speed records appear CSS-only. |
| HFM/VFM/VDM positions and pitch | Modelled and baseline | `hfm.x/y/th`, `vfm.x/y/th`, `vdm.x/y/th`. |
| HFM/VFM voltages | Modelled and baseline | `hfm_voltage` and `vfm_voltage` channels are covered, including readbacks, targets, and status. |
| VDM voltage | Not found | No `vdm_voltage` coverage was found in the audit. |

## Proposed Architecture

Implementation note, 2026-07-03: the first live implementation was placed in the profile
collection as `smi_beamline.plans.beam_snapshot`, not in `smi_plans`, because it depends directly
on live profile devices and `mdsave`.

Implement this as a small beam-positioning snapshot service plus Bluesky plans. Do not put the
core logic in GUI code.

Suggested module: `src/smi_plans/beam_snapshot.py`.

The module should expose pure helpers and Bluesky-facing plans.

Pure helpers:

- `beam_snapshot_devices()` returns the canonical registry of snapshot entries.
- `format_snapshot_diff(current, saved)` returns a table-ready diff for CLI or GUI display.
- `validate_snapshot(snapshot, registry)` reports missing devices, schema mismatches, stale values, missing fields, and limit violations.

Bluesky-facing plans:

- `save_beam_position_snapshot(name=None, *, include=None, exclude=None)`.
- `compare_beam_position_snapshot(snapshot_or_name)`.
- `restore_beam_position_snapshot(snapshot_or_name, *, dry_run=True, groups=None, confirm=False)`.

The default restore path must be a dry run.

## Snapshot Schema

Each snapshot should include top-level metadata:

- `schema_version`.
- `snapshot_name`.
- `created` timestamp.
- `beamline` / profile identifier if available.
- `operator` if available.
- Optional note or reason.

Each item should include:

- Stable logical name, for example `wbs.h` or `hfm_voltage.ch0`.
- Ophyd device name and dotted attribute path.
- PV prefix or PV name where available.
- Current readback value.
- Setpoint value where available.
- User offset where available.
- Low and high user limits where available.
- Units and precision where available.
- Read-only or settable classification.
- Timestamp.

For motors, collect motor fields when present:

- `.user_readback`.
- `.user_setpoint`.
- `.user_offset`.
- `.low_limit` / `.high_limit`, or equivalent limit signals.

For plain signals, collect only fields that actually exist.

## Registry Draft

The registry should be explicit and stable. It should not rely on walking all devices in baseline.

Initial groups:

- `slits`: `wbs`, `ssa`, `eslit`, `cslit`, optionally `hfmslit`, `vfmslit`.
- `dcm`: `energy.bragg`, `energy.dcmgap`, `dcm_config.pitch`, `dcm_config.roll`, and DCM X if identified.
- `diagnostics`: `xbpm2.posX`, `xbpm2.posY`, `xbpm3.posX`, `xbpm3.posY`, and optionally `xbpm2_pos.x/y`, `xbpm3_pos.x/y`.
- `undulator`: `energy.ivugap`.
- `mirrors`: `hfm.x/y/th`, `vfm.x/y/th`, `vdm.x/y/th`.
- `mirror_voltages`: `hfm_voltage.ch0` through `ch15`, `vfm_voltage.ch0` through `ch15`, with target readbacks and statuses if useful.

## Storage

Prefer the Redis-backed `mdsave` pattern already used by the profile collection. This makes saved
snapshots visible from the IPython terminal, QueueServer, and a GUI process.

Recommended key namespace:

- `beam_position_snapshots:<name>` for the snapshot payload.
- `beam_position_snapshots:index` for names, timestamps, and notes.

A JSON import/export helper is useful for sharing or archiving, but Redis should be the operational
source during commissioning if available.

## Dry-Run Comparison

The dry-run comparison should be friendly enough for an operator to inspect quickly.

Suggested columns:

- `name`.
- `group`.
- `current`.
- `snapshot`.
- `delta`.
- `units`.
- `limits`.
- `status`.
- `action`.

Suggested statuses:

- `unchanged`.
- `would move`.
- `read-only`.
- `missing device`.
- `missing saved value`.
- `outside limits`.
- `requires confirmation`.
- `blocked`.

## Restore Safety

The restore plan should be conservative.

Rules:

- Dry run is the default and should never move hardware.
- Never restore read-only diagnostics such as BPM beam-position readbacks.
- Check saved targets against live limits before moving.
- Stop before any target is outside limits, unless there is an explicit expert override added later.
- Support restoring by group so commissioning staff can restore slits, mirrors, or voltages separately.
- Require explicit confirmation for mirror voltages, DCM internals, and any coupled energy/undulator move.
- Prefer setting high-level pseudo-positioners where they exist instead of independently writing coupled internals.

Current profile-collection restore behavior:

- Slit and mirror motors marked `restore=True` are restored with Bluesky `bps.mv`.
- DCM, energy, undulator, and XBPM diagnostic axes are saved for comparison but are not restored.
- Bimorph voltages are restored through `read_outputs()`, `set_targets(...)`, and
  `apply_and_wait()`.
- For partial bimorph restores, unselected channels are staged from current outputs before apply;
  this avoids applying stale target values to unselected channels.

Likely restore ordering for review with beamline staff:

1. Slit gaps, then slit positions.
2. Mirror mechanical axes.
3. Mirror voltages, only with explicit opt-in.
4. DCM / undulator coupled state, only through the approved high-level device path.

## Open Commissioning Questions

- Does DCM height correspond to `energy.dcmgap`, or is there another axis to include?
- What is the correct DCM X device or PV, if any?
- Is there a VDM voltage controller, or are only HFM and VFM voltage-controlled?
- For BPM 2 and 3, should snapshots include only beam-position readbacks, physical BPM motor positions, or both?
- Should `hfmslit` and `vfmslit` be included in the required slit set?
- Which mirror-voltage fields should be restored: target signals, live output signals, or both with target-apply semantics?
- Should snapshots be stored only in Redis, or should every save also write a JSON artifact?

## Implementation Steps

Status update, 2026-07-03: steps 2, 3, 4, 5, and a minimal selected-motor restore test are done in
the profile collection. Remaining work is broader in situ validation across realistic beamline
recovery cases, especially full-mirror bimorph restores and mixed motor/bimorph restore sequences.

1. Confirm the open hardware naming questions during commissioning.
2. Add `beam_snapshot.py` with registry, snapshot collection, diff formatting, validation, and restore plans.
3. Add simulated-device tests for read-only items, settable motors, missing devices, limit violations, and dry-run behavior.
4. Add a minimal CLI or profile helper that prints the dry-run table.
5. Exercise save and dry-run compare on beamline hardware without moving anything.
6. Enable group-by-group restore after staff validates ordering and safety behavior.
