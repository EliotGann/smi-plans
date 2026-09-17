# Explicit-frame Sorensen voltage program

`P_sorensen_voltage_program_run` takes one output-off reference image, then one
primary event per image at a requested start-to-start cadence. Every image event
contains the Sorensen setpoint, measured voltage/current, output status, current
limit, program step, and timing. SAXS and WAXS are triggered together when selected.

## In the SMI Bluesky session

After updating the profile's installed `smi-plans` revision and restarting the session:

```python
RE(P_sorensen_voltage_program_run(
    "sample1_voltage_response",
    voltages=[0.8, 1.0, 1.2, 1.385, 1.2, 1.0, 0.8],
    hold_times=[4, 4, 4, 20, 4, 4, 4],
    frame_period=2.0,
    exposure_time=1.0,
    detector="saxs",  # "waxs" or "saxs_waxs" also supported
    baseline_image=True,
    md={"experiment": "voltage_response"},
))
```

This records 22 biased images over 44 seconds, plus one baseline image. For a
single-voltage hold use `voltages=[1.385], hold_times=[120]` (60 biased frames).
Optional `current_limit=...` is in **amperes at the Sorensen output**. Omit it to
keep the configured limit; its readback is still recorded.

**Voltage values are direct Sorensen command voltages.** No high-voltage amplifier
calibration is applied. The earlier 1.385 V calibration point is approximately
500 V at the measured amplifier output for that setup; the recorded Sorensen
voltage/current are not independent HV-output or sample-leakage measurements.

Select the sample, beam/shutter configuration, and WAXS geometry before this plan.
The plan does not move the sample or detector. Use a WAXS position that leaves the
SAXS beam path clear when requesting SAXS. `atten_in=` can supply a measurement
setup plan before the baseline image.

## Timing contract

- Holds must be positive integer multiples of `frame_period`.
- Time zero is immediately after the output-enable command completes, excluding
  baseline and camera configuration time. This is not a measured electrical edge.
- Image deadlines are `0, 2, 4, ...` seconds with the defaults. Voltage writes take
  place at step boundaries before triggering. The schedule does not drift by adding
  a fixed sleep after acquisition.
- `elapsed_s` records the host time immediately before triggering; it is not a
  hardware shutter timestamp. `read_elapsed_s` is sampled after the trigger wait.
  Electrical signal timestamps are retained in each event.
- V/I are explicit post-exposure reads, not exposure averages; EPICS freshness is
  limited by the IOC/PV update rate. Brief electrical transients between frames
  will require additional monitoring.
- `step_applied_elapsed_s` records the completion time of the step's voltage write.
  The first step is preprogrammed while output is off and assigned time zero.
- `scheduled_elapsed_s` and `timing_lateness_s` allow cadence checks in analysis.
- `max_lateness=0.1` allows 100 ms of scheduling delay. Larger delays fail the run
  and turn output off, rather than skip steps or acquire catch-up images. Camera
  completion/readout overruns are checked too. A nominal exposure is not started
  if it would cross the next frame slot.
- Output remains enabled until the final hold deadline; normal readout can finish
  after that deadline, bounded by the same lateness policy once control returns.
  This is software scheduling, not a hard real-time output cutoff.

Use a 1 s exposure / 2 s period initially. SMI's actual detector/file-writing
overhead still needs measurement; the CMS 1.99 s / 2 s burst timing is not a
verified explicit-read setting.

## Data and lifecycle

One staged run owns all image resource/datum documents, including the baseline.
Each trigger has one frame; each image gets a unique datum in a primary event.
The baseline has `bias_phase="baseline"`, `frame_index=-1`, `program_step=-1`,
and time fields of -1 because output enable has not occurred yet. Biased frames
are numbered from zero. Filename tokens come from the same event.

Camera `acquire_time`, `acquire_period`, and `num_images` are captured and restored
after unstage, including on failure. Only selected cameras are configured, with
the profile's double-write convention. `det_exposure_time(t, meas_t)` is not used
as a cadence setter: its second argument is total measurement time and can change
the internal frame count.

Output is commanded off before setup and in cleanup before the run closes. The
last voltage and current-limit setpoints remain programmed, output disabled.
Generator cleanup covers normal completion and RE abort/errors, not process loss
or `RE.halt()`. The timed section clears checkpoints to prevent replaying voltage
history: under Bluesky 1.15 an immediate pause without a checkpoint aborts and
cleans up. A deferred pause cannot stop at a checkpoint inside this program.

## Development and deployment

```bash
pixi run test-power
pixi run test
```

To additionally test the real profile device class with fake EPICS signals:

```bash
SMI_PROFILE_SRC=/nsls2/data/smi/shared/config/bluesky/profile_collection/src pixi run test-power
```

Tests cover deterministic timing, real-clock scheduling, electrical reads, unique
image assets, all camera selections, invalid input, overrun, and failure cleanup.
They do not connect to live PVs or prove live Tiled ingestion.

The profile imports the curated queue surface after building `sorensen_ps1`; the
new name is exported automatically from technique P's `__all__`. To fetch a pushed
revision of the existing Git dependency, run in the profile collection:

```bash
pixi update smi-plans
```

Then restart the terminal session or reopen the queue worker environment. The
profile lockfile determines the installed Git revision; restarting alone does
not fetch new commits. Validate live image retrieval in Tiled with a short run
before the full experiment.
