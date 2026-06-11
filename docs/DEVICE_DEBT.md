# Device Debt — non-message hardware access to fix in ophyd

## What this is

A Bluesky **plan must contain only messages** (`yield from bps.mv / bps.rd /
trigger_and_read / ...`). It must never call `device.put()` / `device.get()` / `device.set()`
directly, and never a bare module-level function like `readHumidity()` mid-plan.

Where the underlying hardware is exposed only through such non-message paths, the **first-line
fix is the ophyd device, not the plan**: make the quantity a proper ophyd Signal/Device the
RunEngine can drive via messages. Until that is done, `smi-plans` routes through thin wrappers
(`smi_plans._devices`) so the *plan code stays message-pure today*, and the remaining work is
purely on the ophyd/PV side.

This file is the running list of that debt. The guard test
`tests/test_message_purity.py` enforces that no *plan* regresses to a bare `.put()/.get()`; this
doc tracks the *device* fixes that would let us delete the interim wrappers.

## How the interim wrappers work (so plans stay pure now)

`smi_plans._devices.FunctionBackedSignal(func=..., name=...)` is a read-only ophyd Signal whose
value comes from a Python callable. A plan reads it with `yield from bps.rd(sig)` (a message);
the RunEngine calls the signal's `.get()`, which delegates to `func()`. The forbidden thing is a
*plan* calling `.get()`; here the *RunEngine* calls it while handling a message. This is a
bridge, not the destination.

## The debt items

### 1. Humidity — `readHumidity()` is a function, not a signal
- **Where:** `technique_G_humidity.py` (`set_rh` equilibration poll; the live-RH recorded
  Signal in `rh_step_series_run` / `rh_swelling_kinetics_run`).
- **Legacy form:** `rh = "%3.2f" % readHumidity()` baked into the filename, or a `.put()` onto a
  throwaway Signal.
- **Interim (now):** `humidity = _devices.humidity_signal(readHumidity, name="rh")`; included in
  `reads`/`trigger_and_read` so the live value is recorded (and `{rh}` resolves), and read
  mid-plan via `yield from bps.rd(humidity)`.
- **Proper fix:** an `EpicsSignalRO` on the humidity PV, e.g.
  `humidity = EpicsSignalRO("<RH readback PV>", name="rh")`, wired into the profile collection.
  Then delete `humidity_signal` usage and pass the real signal directly. **Action:** get the RH
  readback PV from beamline staff.

### 2. Linkam live temperature — `LThermal.temperature()` is a method, not a signal
- **Where:** `technique_C_temperature.py::linkam_heater` (the heater read-back used by the
  equilibration loop and recorded each event).
- **Legacy form:** `read_value=lambda: LThermal.temperature()` plus a static Signal that a
  `sync_readback()` helper `.put()` into. (Both removed.)
- **Interim (now):** `readback = _devices.linkam_temperature_signal(LThermal,
  name="linkam_temperature")`; the equilibration loop reads it via `yield from
  bps.rd(heater.readback)`.
- **Proper fix:** expose the Linkam controller's live temperature as an ophyd component
  (e.g. a reliable `LThermal.temperature_current` `EpicsSignalRO`) and use it directly as the
  `Heater.readback`. **Action:** confirm/repair the Linkam ophyd device's temperature readback
  signal; then drop the wrapper.

### 3. (Watch) WAXS arc readback vs setpoint
- **Status: NOT debt — documented for clarity.** The real `waxs` is settable directly
  (`yield from bps.mv(waxs, angle)`) and exposes `.arc.position` as readback. `smi-plans` moves
  it with `motor_axis("arc", waxs, ...)` (message-based) and reads `waxs.arc.position` only in
  the *plan-construction* helper `saxs_waxs_dets()` (not inside a plan), which is fine. No fix
  needed; listed so nobody "fixes" it by mistake.

### 4. (Watch) Detector burst configuration — `cam.num_images`
- **Status: NOT debt.** `technique_N_xpcs.xpcs_burst_run` sets `cam.num_images` via
  `yield from bps.mv(det.cam.num_images, n)` (message-based) — this replaces the legacy
  `cam.acquire.put(1)` + busy-wait. `cam.num_images` is a normal settable ophyd signal. No fix
  needed.

## Adding a new debt item

When you must read/drive hardware that has no message path:
1. Add a `FunctionBackedSignal` (or a minimal ophyd Device) in `_devices.py` exposing it as a
   `bps.rd`-able / `bps.mv`-able signal.
2. Use it in the plan via `bps.rd`/`bps.mv` (never the raw call).
3. Add a `# DEVICE DEBT: <what> -- see docs/DEVICE_DEBT.md` comment at the use site.
4. Add an entry here with the **proper ophyd/PV fix** and an **Action** (what's needed to
   retire the wrapper).
