# PF Analysis Status

Status: implemented and minimally live-validated.

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
