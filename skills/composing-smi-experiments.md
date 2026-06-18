# Composing SMI-SWAXS Experiments

## What this does

This skill explains how to build a **bespoke SMI-SWAXS data-acquisition experiment** by
composing reusable pieces from the `smi_plans` package, instead of writing a one-off monolithic
script or forcing the experiment into a single fixed "technique."

An SMI experiment is an assembly of independent concerns:

- **beam / q-range** — which energ(ies)? which detectors + WAXS-arc positions (the q reach)?
- **apparatus / geometry** — transmission or grazing? Linkam/Lakeshore? RH cell? e-chem?
- **sampling / scanning** — a single spot, 5 locations, a grid, an energy sweep, a temperature
  ramp — usually *several of these nested together*.
- **manual / interactive** — "swap the bar and type the thickness", "I set T=35 °C, confirm".
- **what to record** — detectors + context Signals captured at every point.

You express this as a **measurement core** wrapped by a **stack of scan axes**, nested in an
order you choose, producing **one well-formed Bluesky run**.

## When to use this

- A user describes an experiment that combines concerns (e.g. "tender energy sweep at each
  temperature, in grazing, at 5 spots per condition").
- You need to write or review an SMI acquisition plan and want it to follow best practice (one
  run per sample; context recorded as devices; filename from recorded fields; `md={}`).
- You are about to hand-roll nested `for` loops with `bp.count` — stop and compose instead.

## The mental model

```
for sample:                         # outer (acquire / *_bar handles this)
    <apparatus setup: geometry, align, heater on, atten in>   (once per run)
    for arc in waxs_arc:            # ScanAxis (slow, in-vacuum -> outer)
        for T in temperatures:      # ScanAxis (slow -> outer)
            for ai in incidence:    # ScanAxis
                for e in energies:  # ScanAxis
                    at 5 x-locations # ScanAxis (fast -> inner)
                        trigger_and_read(dets + context)   # the core
```

Each loop level is a `ScanAxis`. You build the axes you want, order them (slow/in-vacuum
outermost), and `acquire(...)` nests them inside ONE run with the filename templated from
whatever the axes record.

## The recipe (step by step)

1. **Beam / q:** choose `dets` and the `reads` you always want recorded.
   ```python
   from smi_plans._core import saxs_waxs_dets
   dets = saxs_waxs_dets()              # arc-aware [pil900KW (+pil2M)]
   reads = [energy, waxs, xbpm2, xbpm3] # recorded every event -> available as {tokens}
   ```

2. **Apparatus / geometry:** split *pre-run* from *in-run*.
   - **`align`** (pre-run): an alignment routine — it opens its OWN runs and stages detectors, so
     it must run *before* the measurement run (else `RedundantStaging`).
   - **`setup`** (in-run): config recorded in this run (heater on, attenuators in). Runs once after
     `open_run`; its moves are recorded.
   ```python
   def align():
       yield from alignement_gisaxs_hex(0.1)         # PRE-run: opens its own runs / stages dets
   def setup():
       yield from bps.mv(att2_9.close_cmd, 1); yield from bps.sleep(1)   # IN-run: recorded
   ```

3. **Sampling / scanning:** build the axes, OUTERMOST FIRST (slow/in-vacuum first).
   ```python
   from smi_plans._compose import (energy_axis, temperature_axis, incidence_axis,
                                    motor_axis, spatial_grid_axes, SPEED_SLOW, SPEED_FAST)
   from smi_plans.technique_C_temperature import linkam_heater
   axes = [
       temperature_axis(linkam_heater(), [30, 60, 90]),        # slow
       motor_axis("arc", waxs, [0, 20], speed=SPEED_SLOW),     # slow, in-vacuum
       incidence_axis(piezo.th, None, [0.10, 0.20]),           # None -> relative to aligned zero
       energy_axis([2470, 2475, 2480], flux_signal=xbpm2.sumX, flux_threshold=50),
       motor_axis("x", piezo.x, [0, 30, 60, 90, 120], speed=SPEED_FAST),  # 5 fresh spots
   ]
   ```

4. **Manual / interactive (if any):** see the manual-axis section below.

5. **Assemble into ONE run:**
   ```python
   from smi_plans._compose import acquire
   RE(acquire("PS40nm", dets, axes, reads=reads, align=align, setup=setup,
              geometry="reflection", scan_name="giwaxs_Tramp_NEXAFS_5loc",
              md={"project_name": "311234"}))
   ```
   `acquire` auto-builds the filename from each axis's recorded Signal, captures constants you
   pass in `baseline=[...]`, and **warns** if you nested a slow axis inside a faster one.

6. **For a sample bar:** loop with `acquire_bar(samples, dets, axes_for, ...)` (one run per
   sample) where `axes_for(sample) -> list[ScanAxis]`; or use `_core.multi_sample_run` for
   slow-axis economy across the whole bar (multiple runs open at once so the arc moves once).

## The available axes (one per concern)

| Axis | Builder | Speed | Records |
|---|---|---|---|
| Energy sweep | `energy_axis(energies, flux_signal=…, flux_threshold=…)` | medium | energy + I0 re-seek |
| Temperature ramp | `temperature_axis(heater, setpoints, soak=…, first_soak=…)` | slow | live T (equilibrates) |
| Incident angle | `incidence_axis(th_axis, th0, angles)` (use `th0=None` after alignment: relative/aligned-zero) | medium | relative ai |
| Any motor (arc/prs/piezo) | `motor_axis(name, device, values, speed=…)` | you set | device position |
| Spatial (single/line/grid) | `spatial_grid_axes(x_motor=…, x=…, y_motor=…, y=…)` → list | fast | positions |
| Applied potential | `potential_axis(set_potential, V_list, readback=…)` | medium | commanded V |
| Humidity (SVA) | `rh_axis(set_rh, rh_list, live_rh=…)` | slow | commanded + live RH |
| Time series | `time_axis(n_frames, period=…, elapsed_signal=…)` | fast | frame + elapsed |

Build a brand-new kind of axis directly with `ScanAxis(name, values, move=…, device=…,
record=Signal(...), settle=…, per_point=…, speed=…)`. `move` is a plan-function (how to reach a
value) OR a plain function (software-only, e.g. just set a Signal).

## Manual / interactive steps

Steps the beamline cannot automate are first-class and composable. The value a user types is
**recorded as a Signal** (never lost or baked into a filename). All prompts go through
`bps.input_plan` (RunEngine-driven, pause/resume-safe), never raw `input()`.

```python
from smi_plans._compose import manual_step, manual_axis, manual_loop, pause_for_user

# (a) a hand-set value as run context — in setup + baseline:
thickness = Signal(name="thickness_nm", value=0.0)
acquire("S1", dets, axes,
        setup=lambda: manual_step("Load S1; read the prep sheet", signals=[thickness]),
        baseline=[thickness])               # recorded -> usable as {thickness_nm}

# (b) a user-stepped scan dimension (e.g. temperatures dialed by hand):
axes = [manual_axis("temp_manual", "Dial the hot stage to", values=[35, 50, 65]),
        energy_axis(energies)]

# (c) open-ended user-paced bar ("keep loading until I stop"):
#     see recipes_combined.giwaxs_manual_swap_bar

# (d) just wait:
pause_for_user("Start the pump, then <enter>")
```

Put manual steps OUTERMOST (they are the slowest thing in any experiment).

## Presets for the common cases

When one concern dominates, the `technique_*` files pre-assemble the standard thing — they are
thin recipes over `_compose`, not a separate system:

```python
from smi_plans import SampleList, technique_A_energy_edge as A
bar = SampleList.from_columns(names=["s1", "s2"], piezo_x=[-56000, -45000], piezo_y=[4000, 4000])
RE(A.nexafs_bar(bar, A.energy_grid(2822), t=1.0, flux_signal=xbpm2.sumX, flux_threshold=50))
```

Worked CROSS-CONCERN combinations are in `smi_plans.recipes_combined` (read these for patterns):
`giwaxs_tempramp_energy_5loc`, `transmission_rh_kinetics`, `operando_echem_energy`,
`giwaxs_manual_swap_bar`.

## Rules to enforce (the tenets)

1. ONE run per logical sample (use `acquire` / a preset / `multi_sample_run`) — never one run
   per data point (`bp.count` in nested loops is the legacy anti-pattern).
2. Context recorded as devices/Signals in the event stream (or `baseline` if constant) — never
   `.get()`/`.position` into a filename string. Keep the values the user cares about *also* as a
   structured dict via `acquire(..., user_hints={...})` → `md['user_hints']` (a queryable bundle
   of hints; analysis need not parse the filename).
3. Filename = `{recorded_field}` tokens (auto-built by `acquire`, or via `_core.fname`). It is a
   *convenience* derived from recorded data — never the source of truth. Anything in the
   filename MUST be in the read list.
4. Intent via `md={}` — never `sample_id(...)` / `RE.md` mutation.
5. **Plans contain ONLY messages — never `.put()`/`.get()`/`.set()`.** Set with
   `yield from bps.mv(sig, val)`; read for a decision with `x = yield from bps.rd(sig)`. If a
   value comes from non-message hardware (a function like `readHumidity()`, a method like
   `LThermal.temperature()`), wrap it as a `bps.rd`-able Signal in `smi_plans._devices` and
   **fix the ophyd device** — don't call it inline (see `docs/DEVICE_DEBT.md`).
6. Plans are generators end-to-end — never `RE()` inside a plan, never `cam.acquire.put` +
   busy-wait. (User prompts go through `bps.input_plan`.)
7. Slow / in-vacuum axes (`waxs.arc`, `prs`, temperature) outermost.

## Verifying a composed plan WITHOUT hardware

Use the package's simulated-device test harness to dry-run any plan and confirm it produces one
well-formed run before taking beam:

```bash
cd ~/get/smi/smi-plans && pip install -e ".[test]"
```
```python
# in a pytest test or a python -c, using the conftest fixtures `sim` and `inject`:
C = inject("smi_plans._compose")
msgs = sim.messages(C.acquire("S", [sim.pil900KW, sim.pil2M], my_axes, reads=[sim.energy]))
sim.assert_one_run(msgs)                 # one balanced run, balanced events
print("primary events:", sim.primary_events(msgs))
```

See `tests/conftest.py` (the `SimBeamline` fixture) and `tests/test_smoke.py`.
