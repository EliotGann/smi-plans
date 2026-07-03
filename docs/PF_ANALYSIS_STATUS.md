# PF Analysis Status

Status: implemented, minimally live-validated, and integrated with `smi-acquire`.

`smi_plans.analysis.pf` is the quick peak/edge analysis helper intended to replace or complement
the profile collection's `ps()` helper.  It returns a `PeakResult` and also exposes ps-style
attributes such as `pf.cen`, `pf.peak`, and `pf.fwhm`.

## Live Validation

Minimal live testing from the `bsui` terminal on 2026-07-03 confirmed that `pf` works on a real
knife-edge scan using derivative mode:

```python
from smi_plans.analysis import pf

r = pf(-1, db=db, der=True, plot=False)
pf.cen
pf.fwhm
```

For knife-edge scans, `der=True` analyzes the derivative peak, so `pf.cen` is the fitted edge
position.

## Implemented Coverage

- Pure analysis entry point: `analyze_xy(...)`.
- Databroker-facing terminal helper: `pf(...)`.
- Bokeh figure builder: `make_figure(...)`.
- Peak models: Gaussian, Lorentzian, Voigt, and auto-selection by AIC.
- Edge model: erf step, plus derivative mode for knife-edge scans.
- Optional normalization by monitor columns.
- Clear error messages when expected detector or normalization columns are missing.

## Remaining Work

The implementation is ready for routine trial use.  Remaining work is broader comparison against
the existing `ps()` workflow across representative scan types, detectors, and plotting modes.

## Publish latest result for persistent viewers

Complete. `smi-acquire` now has a persistent Alignment-tab viewer that renders the latest `pf`
result from Redis without querying Tiled or calling `pf` itself. `pf(..., publish=True)` publishes
the latest result from the analysis/profile side, and live integration with `smi-acquire` was
confirmed on 2026-07-03.

API:

```python
pf(..., publish=True)   # default
```

When `publish=True`, after computing the `PeakResult`, `pf` writes a JSON payload containing
`PeakResult.as_dict(arrays=True)` to the operational Redis/config area.

Contract consumed by `smi-acquire`:

- Redis DB: operational/config DB, currently db=3, not db=0 and not the db=2 sample/list stores.
- Key with default config prefix: `swaxsconfig:alignment.pf.latest`.
- Logical config-store key: `alignment.pf.latest`.
- Payload shape:

```json
{
  "version": 1,
  "updated": "2026-07-03T12:34:56Z",
  "uid": "...",
  "scan_id": 12345,
  "sample_name": "optional",
  "result": {
    "...": "PeakResult.as_dict(arrays=True)"
  }
}
```

This keeps browser apps and QueueServer-compatible workflows decoupled from direct Tiled access:
`pf` publishes once from the beamline/profile process, and viewers poll/read the latest JSON.

Example from `bsui` after an alignment scan:

```python
from smi_plans.analysis import pf

r = pf(-1, db=db, der=True, plot=False, publish=True)
```

As of 2026-07-03, bare `pf()` defaults to `plot=False` and `publish=True`, so it reports in the
terminal and updates the `smi-acquire` Alignment-tab viewer without opening a browser window.
Publishing failures are reported but do not prevent `pf()` from returning the analysis result.

## Next: baseline-level full width

The current `fwhm` is intentionally a half-maximum metric, either from the fitted peak model or
from model-free half-max crossings. That is not always the desired alignment quantity.

For noisy profiles, double-humped profiles, and slit/beam-width work, the useful quantity is often
the full contiguous support of the signal above baseline noise: the left and right edges where the
profile rises out of the baseline/noise floor. This should be model-free and robust to non-Gaussian
shape.

Implemented additions to `PeakResult`:

- `fw_base`: full width above baseline/noise floor.
- `cen_base`: center of that full-width interval, `(left + right) / 2`.
- `left_base` and `right_base`: baseline-threshold edge positions.
- `baseline_noise`: robust noise estimate used to define the threshold.
- `baseline_threshold`: `baseline + n_sigma * baseline_noise`.

Implemented `pf`/`analyze_xy` options:

```python
r = pf(-1, db=db, baseline_sigma=3.0, baseline_merge_sigma=1.0, baseline_smooth=3)
```

Algorithm:

1. Estimate baseline from the low-intensity tail of the profile, as `pf` already does.
2. Estimate baseline noise robustly from points near baseline, for example with MAD scaled to sigma.
3. Define a threshold: `baseline + baseline_sigma * baseline_noise`.
4. Smooth or median-filter only for edge detection, not for reporting the raw data.
5. Find all contiguous above-threshold regions.
6. Choose the region containing the global peak, or merge nearby above-threshold islands when the
   valley between them remains significantly above baseline. This handles double-humped beam
   profiles as one physical width.
7. Interpolate the left/right threshold crossings for sub-step edge positions.
8. Report `fw_base = right_base - left_base` and `cen_base = (left_base + right_base) / 2`.

For slit centering, `cen_base` is likely the quantity we want: it is the motor position that would
exactly center the slit over the full measured beam support, rather than centering a Gaussian model
on one lobe or reporting only the half-max width.
