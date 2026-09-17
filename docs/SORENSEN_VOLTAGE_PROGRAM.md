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

**Values passed to `voltages=` are direct Sorensen command voltages.** No high-voltage amplifier
calibration is applied in that mode. The earlier 1.385 V calibration point is approximately
500 V at the measured amplifier output for that setup; the recorded Sorensen
voltage/current are not independent HV-output or sample-leakage measurements.

## Calculate voltages from sample thickness and electric field

Instead of `voltages=`, supply thickness in micrometers and field targets in MV/m:

```python
RE(P_sorensen_voltage_program_run(
    "sample1_field_program",
    thickness_um=25,
    # fields_MV_m defaults to [0, 10, 20, ..., 150], matching CMS
    reverse=True,            # includes repeated peak, matching CMS
    hold_times=4,             # seconds at EACH step, including the return path
    frame_period=2.0,
    exposure_time=1.0,
    detector="saxs",
    baseline_image=True,
))
```

The conversion matches the active CMS **Sample.setVoltages** method:

1. `sample_voltage_V = thickness_um * field_MV_m`.
2. Force the **first** command to `0`.
3. Subsequent commands: `numpy.round((sample_voltage_V - 190) / 530, 2)`.
4. Retain commands `<= 11.5 V`.
5. Append the full reversed command list when `reverse=True`; otherwise append `0`.

These are intended sample voltage/field values, not measured readbacks. This uses
the CMS calibration requested for this setup, not the newer calibration measurements
made at a different input impedance or the older piecewise module-level CMS helper.

For 25 µm, the forward commands are
`[0, 0.11, 0.58, 1.06, 1.53, 2.0, 2.47, 2.94, 3.42, 3.89, 4.36, 4.83, 5.3, 5.77, 6.25, 6.72]` V.
With reversal there are **32 steps, 128 seconds, 64 biased images**, plus the reference.
The peak appears twice. `reverse=False` appends a final zero command.

`fields_MV_m=` can override the default field sequence. A hold list must match that
original sequence: holds follow retained steps through filtering and reversal.
The appended zero for `reverse=False` uses the last retained hold. A scalar repeats
for every resulting step. Holds must still be multiples of frame_period.

For exact CMS parity, negative converted commands are retained (there is only an
upper-command filter in CMS); the device's limits may reject them. Even if the first
requested field is nonzero, the first command is forced zero. A later zero-field
entry is converted by the formula, whereas the appended final zero is literal zero.
Previewing the generated commands makes these legacy edge cases visible.

Preview without operating hardware:

```python
from smi_plans.technique_P_power import build_sorensen_field_program
program = build_sorensen_field_program(25, reverse=True)
print(program["sample_voltages_V"])
print(program["voltages"])  # direct Sorensen commands
```

The run metadata saves thickness, expanded field/sample-voltage targets, calibration,
and actual command list. Each primary event additionally carries `sample_thickness_um`,
`target_field_MV_m`, and `target_sample_voltage_V`. Reference-image targets are zero
because the output is off; the programmed first setpoint is recorded separately.
Manual `voltages=` calls retain their existing behavior and cannot be mixed with
the thickness/field/reverse arguments.

Select the sample, beam/shutter configuration, and WAXS geometry before this plan.
The plan does not move the sample or detector. Use a WAXS position that leaves the
SAXS beam path clear when requesting SAXS. `atten_in=` can supply a measurement
setup plan before the baseline image.

## Timing contract

### Progress and expected output

By default, the plan prints a summary, a reference-image notice, and a progress
line after the first image of each step. The step line shows the commanded input,
requested sample voltage (in thickness mode), hold duration, measured input/current,
and expected HV output. Cleanup and successful completion have separate messages.
Pass `verbose=False` to silence these messages without affecting recorded data.

Every primary image event, including reference images and manual `voltages=` runs,
records **`expected_output_voltage_V`** as a software Signal. It uses the very same
input-voltage readback saved in that event, not an extra PV read:

```
expected_output_voltage_V = 530 * sorensen_ps1_out_main_readback + 190
```

This is the CMS model estimate, **not an independently measured HV output**. It is
set to zero for the output-off reference, a zero command, a zero input readback,
or a reported output-off state; this does not measure residual HV/discharge.
The model, source key, units, and zero convention are recorded in run metadata
under `sorensen_program.expected_output`.

`target_sample_voltage_V` remains the requested thickness × field voltage; the
expected output can differ due to command rounding or differences in the measured
input. Both are separate from the measured Sorensen voltage/current. Progress
output happens after saving the step's first image; console overhead counts toward
the timing budget.

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

The profile's `SupplementalData` owns the `baseline` stream. Plan-local metadata
(detector distance by default, or devices supplied via `baseline=`) is recorded in
`sorensen_baseline` at run start/end, avoiding incompatible declarations under the
same stream name. Set `baseline=[]` to omit that plan-local metadata stream. This
does not disable the output-off reference image: `baseline_image=True` records that
image in `primary`, with `bias_phase="baseline"`.

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

`summarize_plan(P_sorensen_voltage_program_run(...))` is supported as a message-only
preview. It does not execute reads, writes, exposures, or sleeps. The plan labels
preview progress explicitly, omits unknown camera restore values, and lists the
expected-output read without inventing voltage/current measurements. Preview timing
is nominal only; use a real acquisition to assess detector cadence. Missing data
from an executed RunEngine read still fails rather than substituting dummy values.

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
