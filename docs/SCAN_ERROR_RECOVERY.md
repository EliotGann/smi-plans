# Operational error triage and continuation notes

## First response: collect evidence, do not guess a restart offset

For vague “help” requests, inspect the end of both logs, matching the user's time
and command. This is read-only and does not require loading live device instances.

```python
from smi_plans import recent_log_lines
print(recent_log_lines("~/.cache/bluesky/log/bluesky.log", lines=120))
print(recent_log_lines("~/.cache/bluesky/log/bluesky_ipython.log", lines=120))
```

Or use `tail -n 120` on those paths at the terminal. Prefer bounded reads, not a
whole-log dump. If the tail starts midway through a traceback, increase the window
or search around the timestamp. Logs may rotate; the newest error may be unrelated
to the reported scan. Missing/unreadable logs are a reason to ask for `%tb verbose`,
the exact command, last scan UID, and relevant traceback—not to infer hardware state.
Redact secrets and unrelated proposal/user information before sharing excerpts.

## Establish what stopped

- Identify the exception and first relevant plan/device frame, not only the final
  message. Was it positioning, alignment fitting, energy movement, detector trigger,
  event save, naming callback, or file writing?
- Confirm RunEngine state at the session: paused, idle after stop/abort, or still
  running. A paused plan retains wrapper state; normally diagnose then use the
  RunEngine's resume path. Do not start a competing acquisition.
- Use the exact UID in Tiled. Read start/stop metadata, primary event count and last
  scalar values. Alignment runs often have their own UIDs and generic sample names;
  correlate sample IDs and baseline coordinates with adjacent measurement runs.
- A `success` stop with fewer points is partial, not complete. A successful
  alignment *scan* can be followed by a failed fit or move outside that run.
- Readback and setpoint are distinct. In this beamtime a final alignment target of
  -0.190705° was not reached, yet the move returned; a subsequent near-zero readback
  was mistakenly used as zero. Reusing that alignment would preserve an error.

## Error classes observed in this beamtime

| Evidence | Best next step |
|---|---|
| Theta readback misses target; stage heating | Stop acquisition, allow stage recovery and involve beamline staff. Keep the exposure guard. Reduce theta cycling by making energy fast, once the stage is functional. |
| DCM roll OVAL moves wrong way / rail guard | Profile/device recovery first. Do not bypass feedback guards, freeze IVU, invert signs, or retry indefinitely in an acquisition script. |
| Alignment fit proposes enormous Y target | Inspect ROI/fit input and bounds with beamline scientist. Profile alignment owns the fix. Re-align the unmeasured sample after repair; do not trust cached zero. |
| Stored X or grid extent below motor limit | Compare full absolute path with limits and sample footprint. Moving the grid's anchor alone may still command an illegal initial nominal position. Any offset needs explicit sample-coordinate approval. |
| Exposure unexpectedly short | Alignment may have set 0.3/0.5 s. Restore detector exposure after alignment, before staging the measurement run. |
| “RedundantStaging” | Alignment opens/stages its own runs: place in a pre-run callback, not measurement `setup`. |
| Missing filename token / duplicate data keys | Inspect event-backed naming and parent/child readables. An event may already have exposed the sample despite a later naming failure. |

Do not treat this table as motor-reset instructions. Beamline personnel determine
the hardware recovery. During beamtime prefer small reviewed user-plan corrections;
do not silently deploy profile/library changes while an acquisition is active.

## Determine continuation scope

1. Last fully complete sample and phase.
2. Exact saved energy/angle/arc pairs (or new stable `scan_point_id` values).
3. Whether the final failed point reached detector trigger. Saved events prove
   exposure; absence of an event does not prove no dose.
4. Whether the alignment/position/foils/harmonic are still valid.
5. Which original samples remain. Holder entries added later should be an explicit
   new selection, not silently inserted into a recovery.

New library runs support [coverage-based recovery](GRAZING_WORKFLOWS.md).
For older plans use a one-off, reviewed snapshot. Identify the continuation by the
source UID and new output UIDs. Do not overwrite old data or mark wrong-angle events
good; flag them and ask before repeating exposed pairs.

## Deployment and verification

Use `pixi run pytest ...` in smi-plans for simulation. Profile tests use that
repository's Pixi environment; avoid OS Python and untracked pip installs. The
live session may hold old imported functions even after files change: reload the
approved entry point (or restart the session as appropriate) before running it.
Loading a file should print a preview, not call `RE()` automatically.

Give the user a short self-contained answer: identified sample/phase, what completed,
what remains, whether alignment is reused, the exact load/run command, and the
specific fault that must be resolved first. State simulation versus live verification.
