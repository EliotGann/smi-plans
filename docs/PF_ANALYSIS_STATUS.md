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

## Publish latest result for persistent viewers

`smi-acquire` now has a persistent Alignment-tab viewer that can render the latest `pf` result from
Redis without querying Tiled or calling `pf` itself.  `pf` can now publish the latest result from
the analysis/profile side.

API:

```python
pf(..., publish=True)
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
