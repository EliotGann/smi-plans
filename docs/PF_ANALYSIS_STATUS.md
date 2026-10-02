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

## Response-aware fitting and automatic fallback (2026-10-02)

`pf`, `analyze_xy`, and `LivePF` share these options:

```python
# Default: infer a Gaussian position spread of 0.25 times median nonzero step spacing.
r = pf(1170625, db=db)

# Known grazing-incidence alignment: peak, allow unequal left/right widths,
# and assume position spread of 0.1 of the scan spacing.
r = pf(1170625, db=db, profile="peak", symmetry="asymmetric",
       x_sigma_fraction=0.1)

# Explicit one-sigma spread in motor units (degrees for stage_theta).
r = pf(1170625, db=db, x_sigma=0.002)

# Point-sampling response; physical fit checks still apply.
r = pf(1170625, db=db, x_sigma=0)

# Same options for streaming analysis.
callback = LivePF(profile="peak", symmetry="asymmetric", x_sigma_fraction=0.1)
```

### What the position spread means

The fitted prediction is the intrinsic profile convolved with a normalized Gaussian of
standard deviation `x_sigma`. This represents **position averaging during exposure**, or an
assumed measurement resolution. It is not an errors-in-variables fit for unknown, fixed
position offsets, and does not add x noise to the y error bars. Analytic convolutions are used
for Gaussian, Lorentzian, Voigt, erf, and split-Gaussian models, avoiding numerical integration
that could miss narrow peaks. `x_sigma` is fixed during fitting.

The auto value is a heuristic, not measured hardware resolution: a step-and-expose scan does
not necessarily average over its step spacing. Known alignment plans should supply an explicit
spread when available. Smaller spreads allow sharper intrinsic profiles. The inferred scalar
is recorded as `result.x_sigma`, with `x_sigma_source="auto"` or `"explicit"`. Auto spacing
uses sorted, finite, unique positions, so repeated positions do not introduce zero steps.

`profile="auto"` retains peak/edge classification; `"peak"` or `"edge"` overrides it.
`symmetry="asymmetric"` selects a split Gaussian with independent left/right widths; this is
also available as `model="split_gaussian"`. Incompatible hints raise `ValueError`.

### Reported results

- Accepted fits set `fit_success=True`, `fit_status="accepted"`, and `model_name`.
- `cen` is the response's **half-height midpoint** for peaks. For symmetric models it equals
  the fitted mode; for asymmetric models it generally differs from `fit_peak` (the mode).
- `fwhm` and `amplitude` describe the **measurement-broadened** peak, as does the plotted curve.
  For erf steps, the existing approximate 8--92% transition-width convention is retained;
  amplitude is half the step height.
- `intrinsic_fwhm` is a separate, model-dependent estimate, left NaN when smaller than the
  Gaussian response FWHM or twice the observable-width error. It is not an independently
  measured width; covariance errors remain conditional on the response/model assumptions.
- `peak`, `com`, `cen_halfmax`, `fwhm_halfmax`, and the baseline-width quantities remain
  model-free. `peak` is still the parabolically refined measured maximum.
- `fit_diagnostics` records each converged candidate's rejection reasons, area ratio,
  reduced chi-squared, relative residual RMS, AICc, and candidate geometry. Rejected candidate
  numbers are diagnostic only. These fields travel in both latest and live Redis payloads.
- `model="none"` requests model-free statistics directly (`fit_status="model_free"`).

All candidate models, including explicitly requested ones, are checked before their numbers
replace model-free results. Checks include at least three distinct points above half height
(four for asymmetric peaks), width relative to local spacing, measured half-height coverage,
center/width covariance, parameter bounds, amplitude range, profile area, and residuals.
Nonnegative non-derivative peak data have a nonnegative fitted background. Model selection
uses weighted AICc consistent with the fit likelihood. The area comparison integrates the
baseline-subtracted response over the measured interval and allows a factor 0.67--1.5 relative
to the trapezoidal baseline-subtracted data area; it is a consistency check, not an exact
conservation law. These acceptance thresholds are deliberately conservative heuristics.

If all candidates fail, `model_name="none"`, `fit_success=False`, and `fit_status="rejected"`.
The summary includes a short warning/reason, headline numbers revert to measured statistics,
fit uncertainties remain NaN, and rejected curves are not plotted. Edge fallback reports an
interpolated midpoint only if both end plateaus are evident; its width is unavailable. When
neither a reliable fit nor a measured crossing exists, center/width remain NaN.

Derivative analysis now uses `diff(y)/diff(x)` at interval midpoints, removing the previous
half-step coordinate offset and handling unequal spacing. Propagated adjacent-point errors
are used, but correlations between differences (and from optional smoothing) are not modeled.

### Scan 1170625 regression

The 31-point `stage_theta` / `pil2M_stats1_total` profile has 0.02-degree steps and only one
point above half maximum. Previously a Voigt fit inferred an approximately 1.54-million-count
peak between observations (measured maximum 153,857) and about 2.8 times the sampled area.
It now automatically falls back to center **0.046322 degrees**, FWHM **0.021127 degrees**,
with an undersampling warning. The asymmetric / 0.1-step hint also falls back. These measured
statistics are still sampling-limited, rather than a recovery of the unresolved intrinsic peak.

Tests include the scan's scalar profile, independent numerical convolution of an asymmetric
peak, broadened Gaussian recovery, missing edge plateaus, double peaks, option validation,
and propagation through both `pf` and `LivePF` publication paths. Broader live validation of
the new defaults across alignment types remains to be done.

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
