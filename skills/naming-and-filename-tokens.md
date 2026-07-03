---
name: smi-filename-tokens
description: Use when writing, reviewing, or debugging SMI-SWAXS acquisition plans that build a templated filename / sample_name with {tokens} (name_tokens, fname, md['sample_name']). Prevents the downstream file-naming/symlink KeyError that happens when a filename token does not match a real recorded data key. Covers how ophyd device names become data keys, the scan-naming preprocessor's skip_if_tokens behavior, the virtual-Signal pattern for recording a value under a chosen key, and a validation recipe to catch bad tokens before a run.
---

# SMI Filename Tokens — every `{token}` must be a real recorded data key

## What this does

SMI runs are named with a **template** string (`sample_name`) containing `{field}`
placeholders. The file writer / downstream Prefect symlink workflow fills each `{field}` by
looking it up in the run's recorded event documents and doing roughly:

```python
dest = target_template.format(**single_doc_data)   # single_doc_data = recorded data keys -> values
```

**If a `{field}` in the name is not a key in the recorded documents, this raises
`KeyError('<field>')` and the post-run naming/symlink step fails** — *after* the scan has
already taken data. The data is collected but mis-/un-named.

This skill makes the rule explicit and gives a way to verify it **before** running.

> **The one rule:** every `{token}` in a filename MUST equal a **data key that is actually
> recorded in the run's events**. Not the axis name, not the motor's Python attribute — the
> *recorded data key*.

## When to use this

- Writing or reviewing any plan that sets `name_tokens=`, calls `fname(...)`, or sets
  `md={'sample_name': '..._{something}_...'}`.
- Debugging a `KeyError('<x>')` from the file writer / `linker.py` /
  `smi-workflows` / a Prefect "Task run failed ... KeyError" after a run.
- Adding a new scan axis and wanting its value in the filename.

## The mechanism — how a device becomes a data key (the #1 trap)

ophyd records a component under `"<parent_device_name>_<component>"`, **not** the bare
component name.

| You move / read | Python attribute | **Recorded data key** | Correct token |
|---|---|---|---|
| `piezo.x` | `x` | `piezo_x` | `{piezo_x}` |
| `piezo.y` | `y` | `piezo_y` | `{piezo_y}` |
| `piezo.th` | `th` | `piezo_th` | `{piezo_th}` |
| `stage.phi` | `phi` | `stage_phi` | `{stage_phi}` |
| `energy` (`energy.energy`) | `energy` | `energy_energy` | `{energy_energy}` |
| `waxs` (`pil900KW.motors`) | `arc` | `waxs_arc` | `{waxs_arc}` |
| `xbpm2` | `sumX` | `xbpm2_sumX` | `{xbpm2_sumX}` |
| `pin_diode` | — | `pin_diode_current2_mean_value` | `{pin_diode_current2_mean_value}` |

**THE CLASSIC BUG (this exact one broke real runs):** `spatial_grid_axes(x_motor=piezo.x, ...)`
builds an axis *named* `"x"`, but the recorded key is `piezo_x`. Writing the natural-looking
token `x{x}` references a key `x` that **does not exist** → `KeyError('x')`. The axis name and
the data key are different things; the **token must use the data key**.

The canonical list of common tokens lives in `smi_plans._core.COMMON_TOKENS` — use it.

## Two correct ways to put a value in the filename

### Option A — token = the real device key (simplest)
Read the device, and use its **full** key as the token. No new objects.
```python
# piezo.x is read (recorded as piezo_x); name it with the real key:
name_tokens=[f"x{{piezo_x}}", f"y{{piezo_y}}"]     # -> {piezo_x}/{piezo_y}
# ensure piezo.x / piezo.y are actually READ (in reads, or via an axis that reads them)
```
Downside: records the **absolute** motor position (e.g. a big raw piezo number).

### Option B — virtual `Signal` recorded under a chosen key (preferred for relative/derived values)
Make a `Signal(name="<key>")`, set it with `bps.mv` to whatever value you want recorded, and
read it — exactly how `incidence_axis` records the *relative* incident angle under
`{incident_angle}` instead of the absolute `piezo_th`.
```python
xsig = Signal(name="x", value=0.0)               # the key WILL be exactly "x"
def _move_x(off):
    yield from bps.mv(piezo.x, center + off)      # absolute move (still recorded as piezo_x)
    yield from bps.mv(xsig, float(off))           # record the RELATIVE offset -> key "x"
axis = ScanAxis("x", offsets, move=_move_x, record=xsig, reads=[piezo.x])
name_tokens=["x{x}"]                              # {x} now resolves to the relative offset
```
This is the pattern to use when the meaningful filename value is **relative or derived**
(grid offset, incident angle, commanded setpoint) rather than the raw motor readback.

> A `ScanAxis`'s `record=<Signal>` makes that Signal the recorded key; its `reads=[device]`
> add more keys. `acquire` auto-collects both. The token must match a `Signal.name` or a
> `<device>_<component>` key that ends up in the event.

## The scan-naming preprocessor — `skip_if_tokens` (do not get silently bitten)

The beamline installs a scan-naming preprocessor. Its behavior:

- If the name you provide **already contains `{tokens}`**, it is treated as authoritative:
  the preprocessor **does NOT append its default name**, and it **only injects reads for the
  token devices it recognizes** in your name (e.g. it will read `energy` for `{energy_energy}`,
  `waxs` for `{waxs_arc}`). This is `skip_if_tokens=True`.
- Consequences you must honor:
  1. **Do not also read a token device yourself** if the preprocessor injects it, or
     `trigger_and_read` bundles the same key twice → `Data keys ... collide`. (So with
     `{energy_energy}` in the name, do **not** put `energy` in `reads` and do **not**
     `record=True` an energy axis.)
  2. **Custom names must be a SUPERSET of what the default naming would have recorded.** Because
     a custom (token-bearing) name suppresses the default path, any field the default would have
     injected but your custom name omits is **silently lost**. If you need it, record it
     yourself (a `reads=` entry or an axis `record`).

## Validation recipe — check tokens BEFORE running (catch the KeyError early)

Tokens are only resolved post-run, so validate up front. Two cheap checks:

1. **Static read**: list the tokens, list the keys you record, diff them.
   ```python
   import re
   tokens = set(re.findall(r"{([^}:!]+)", sample_name_template))   # field names only
   # keys you will record = each Signal.name + each <device>_<component> for devices in reads/dets
   missing = tokens - recorded_keys
   assert not missing, f"filename tokens with no recorded key: {missing}"
   ```
2. **Dry-run in sim** (preferred, definitive): run the plan on the sim RunEngine
   (`tests/conftest.py` harness), capture the primary event's `data.keys()`, and assert every
   token is present. A token not in the emitted event is the bug — found without touching
   hardware or the file writer. Add such an assertion as a test for any new bar/recipe.

### Quick triage of a live `KeyError('foo')` from the naming/symlink step
- The filename template contains `{foo}` but no recorded key `foo`.
- Find where `foo` comes from: is it an **axis name** that doesn't match its recorded key
  (the `piezo_x` vs `x` trap)? → either use the full key token (`{piezo_x}`) or record a
  `Signal(name="foo")` (Option B).
- Was `foo` supposed to be injected by the naming preprocessor but the **custom name suppressed
  it**? → record it yourself.
- Is `foo` only in a non-primary stream (baseline/monitor) that the linker doesn't read? →
  record it in the primary stream.

## GUI-generated bookmark scans

For a simple bookmark scan that only moves to saved samples and takes one frame, generate:

```python
return (yield from acquire_bar(
    bar, dets, axes_for,
    scan_name="bookmark_scan",
))
```

with `axes_for(s)` returning `[]`. Backend behavior:

- `acquire_bar` calls `goto_sample(s)` before each run, using the sample's runnable `Position`.
- With no scan axes and no explicit `name_tokens`, `acquire_bar` appends tokens for the saved
  sample-position coordinates that are set, e.g. `x{piezo_x}` and `y{piezo_y}`.
- `acquire` automatically adds those position motors to the primary-stream reads, so the tokens
  resolve without the GUI needing to add `piezo.x`/`piezo.y` itself.

If the GUI does provide an explicit position-bearing name, use real recorded keys:

```python
name_tokens=("x{piezo_x}", "y{piezo_y}")
```

not `{x}`/`{y}`. The backend will auto-read `piezo.x`/`piezo.y` for these tokens.

Do not add detector sub-devices as extra reads when the parent detector is already in `dets`.
For example, do **not** add `pil2M.motor` just to get `pil2M_motor_x` when `pil2M` is selected;
`pil2M` already reports those keys, and reading both can produce `Data keys ... collide`.
Prefer sample-position tokens (`{piezo_x}`, `{piezo_y}`, `{stage_phi}`, etc.) for bookmark names.

## Checklist (use on every plan that templates a filename)
- [ ] Every `{token}` equals a real recorded data key (`Signal.name` or `<device>_<component>`),
      verified against `COMMON_TOKENS` or a sim dry-run — NOT an axis name.
- [ ] No token device is both preprocessor-injected AND in `reads` (no `Data keys collide`).
- [ ] A custom token-bearing name still records everything the default name would have.
- [ ] Relative/derived values (grid offset, incident angle, setpoints) use a virtual
      `Signal(name=...)` (Option B), not the raw motor key.
- [ ] f-string-substituted literals in tokens (e.g. `f"wa{arc:04.1f}"`) are fine — they are
      baked at build time and are NOT `{}` template fields (double-check they don't leave a
      stray `{}`).

## Known-good vs known-bad (from real SMI plans)
```python
# GOOD: real keys / recorded Signals
name_tokens=["{energy_energy}eV", "ai{incident_angle}", f"wa{arc:04.1f}"]   # energy_energy + Signal + literal
md={'sample_name': name + "_x{piezo_x}_y{piezo_y}"}                          # technique_D: full device keys

# BAD: axis-name tokens that are not data keys  -> KeyError('x') / KeyError('y')
name_tokens=["x{x}", "y{y}"]    # only valid if a Signal(name="x")/(name="y") is recorded (Option B)
```

## GUI naming helper contract

The GUI should not make users type raw `name_tokens` for common cases. Use the pure helpers exported
by `smi_plans`:

```python
from smi_plans import preview_bar_name, bar_name_tokens, apply_name_prefix

name_spec = {
    "name_prefix": "Gao",
    "include_energy": False,
    "include_exposure": True,
    "arc_fmt": "waxs_{:.0f}",
    "extra_tokens": ["px_{piezo_x:.1f}", "py_{piezo_y:.1f}", "pz_{piezo_z:.1f}"],
}

template = preview_bar_name("s1", arc=15, exposure=1.0, name_spec=name_spec)
tokens = bar_name_tokens(15, **name_spec)
base = apply_name_prefix("s1", name_spec)
```

GUI rules:

- Offer structured controls for prefix, energy, exposure, arc, incidence, grid offsets, and common
  position tokens.
- Show both the unfilled template and a fake filled example before code generation.
- Treat `extra_tokens` as advanced input and validate every `{field}`.
- Do not enable `include_exposure` unless the generated plan records `Signal(name="exposure_s")` or
  otherwise records an `exposure_s` data key.
- Do not generate legacy filename baking such as `xbpm3.sumY.value`, `piezo.x.position`, `.get()`, or
  f-stringed live reads. Record the device or Signal and use a token.
- Use group presets only as editable starting points. Gao-style transmission snapshots often omit
  energy and include exposure, arc, and absolute piezo position tokens. Gomez-style resonant scans
  often include energy, arc, and beam-monitor context, but the BPM token must match a recorded key.
