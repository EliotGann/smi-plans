"""
smi_plans.analysis
==================

A friendly, web-ready replacement for the profile's ``ps`` peak-stats helper.

Intent (same as ``ps``)
-----------------------
Give a quick, friendly display + peak/edge fit of the latest scan, exposing the numbers an
alignment cares about (peak / center / FWHM / center-of-mass) as both a returned object and as
function attributes (``pf.cen``, ``pf.peak`` ...), so it can drop into alignment plans later.

What it adds over ``ps``
------------------------
* **Robust field resolution** -- when the auto-detected intensity column is missing it raises a
  clear message that *lists the available columns* (instead of an opaque ``KeyError`` -- the
  "it's asking for a detector" symptom).
* **Real model fits** with uncertainties: Gaussian / Lorentzian / Voigt for peaks (``auto``
  picks the best by AIC) and an erf step for edges, via :func:`scipy.optimize.curve_fit`.
  Reports center, FWHM, amplitude, baseline *with ±1σ errors* and an R².
* **Baseline-aware model-free stats** -- COM no longer skewed by background; sub-point PEAK by
  parabolic refinement.
* **Optional normalization** by a monitor column (``norm=``), with error propagation.
* **Beautiful, interactive, web-ready graphics** -- a Bokeh figure with shaded error bands
  (data Poisson band + fit confidence band), hover, zoom/save tools.  Pops up in a browser now;
  the figure object is returned so it can be embedded into a Bokeh app later.

Layering (so the GUI can reuse the pieces)
------------------------------------------
* :func:`analyze_xy`     -- PURE: ``(x, y) -> PeakResult``.  numpy + scipy only.  No db, no GUI.
* :func:`make_figure`    -- PURE: ``PeakResult -> bokeh figure``.  bokeh only.
* :func:`pf`             -- the terminal shell: pull the latest scan from ``db``, analyze, show.

Dependencies
------------
``numpy`` + ``scipy`` for the analysis (both already in the beamline env and the standalone dev
env), ``bokeh`` for the figure (lazily imported), ``databroker`` only for :func:`pf` (lazily
resolved).  The pure functions therefore import and test without bluesky/databroker present.

Status
------
Automated synthetic-profile tests exist.  Minimal live validation from the ``bsui`` terminal on
2026-07-03 confirmed ``pf(-1, db=db, der=True, plot=False)`` works for a real knife-edge scan.
Broader comparison against the profile-collection ``ps()`` helper is still pending.
"""

from __future__ import annotations

import json
import datetime as _dt
import math
import os
import warnings
from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence

import numpy as np

__all__ = ["PeakResult", "analyze_xy", "make_figure", "pf", "LivePF", "publish_pf_result"]


PF_PUBLISH_KEY = "alignment.pf.latest"
PF_LIVE_PUBLISH_KEY = "alignment.pf.live"
PF_PUBLISH_PREFIX = "swaxsconfig"


# ===========================================================================================
# Result container
# ===========================================================================================
@dataclass
class PeakResult:
    """Everything :func:`analyze_xy` computes -- the GUI/web payload and the alignment numbers.

    The headline scalars (``cen``/``peak``/``com``/``fwhm``) mirror ``ps``'s attributes so this
    can be a drop-in.  ``*_err`` are 1σ uncertainties from the fit covariance (``nan`` if the fit
    did not converge).  Arrays are kept so a figure can be (re)built anywhere.
    """

    # identity / context
    scan_id: Optional[int] = None
    uid: Optional[str] = None
    motor: Optional[str] = None
    detector: Optional[str] = None
    timestamp: Optional[str] = None

    # what we decided to do
    profile_kind: str = "peak"          # "peak" | "edge"
    model_name: str = "none"            # gaussian | lorentzian | voigt | erf | none
    derivative: bool = False
    normalized: bool = False

    # headline numbers (ps-compatible)
    peak: float = float("nan")          # x of the maximum (model-free, parabola-refined)
    com: float = float("nan")           # baseline-subtracted center of mass
    cen: float = float("nan")           # best center: fit center if available else half-max mid
    fwhm: float = float("nan")          # best FWHM: fit if available else half-max width

    # fit detail
    amplitude: float = float("nan")
    baseline: float = float("nan")
    cen_err: float = float("nan")
    fwhm_err: float = float("nan")
    r_squared: float = float("nan")

    # model-free cross-checks (always present)
    cen_halfmax: float = float("nan")
    fwhm_halfmax: float = float("nan")
    left_base: float = float("nan")
    right_base: float = float("nan")
    cen_base: float = float("nan")
    fw_base: float = float("nan")
    baseline_noise: float = float("nan")
    baseline_threshold: float = float("nan")

    # arrays (data + fit curve + bands)
    x: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    y: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    yerr: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    fit_x: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    fit_y: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    fit_lo: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)
    fit_hi: np.ndarray = field(default_factory=lambda: np.array([]), repr=False)

    # diagnostics
    message: str = ""

    # -- niceties -------------------------------------------------------------------------
    def as_dict(self, arrays=False):
        """Plain dict for JSON / logging.  ``arrays=False`` drops the big arrays."""
        d = asdict(self)
        if not arrays:
            for k in ("x", "y", "yerr", "fit_x", "fit_y", "fit_lo", "fit_hi"):
                d.pop(k, None)
        else:
            for k in ("x", "y", "yerr", "fit_x", "fit_y", "fit_lo", "fit_hi"):
                d[k] = np.asarray(d[k]).tolist()
        return d

    def summary(self):
        """One-line human summary."""
        return (
            "scan {sid} | {mot} vs {det} | {kind}/{mdl} | "
            "cen={cen:.5g}±{cerr:.2g} fwhm={fw:.4g} fw_base={fwb:.4g} "
            "cen_base={cenb:.5g} peak={pk:.5g} com={com:.5g} R²={r:.4f}"
        ).format(
            sid=self.scan_id, mot=self.motor, det=self.detector,
            kind=self.profile_kind, mdl=self.model_name,
            cen=self.cen, cerr=self.cen_err, fw=self.fwhm,
            fwb=self.fw_base, cenb=self.cen_base, pk=self.peak, com=self.com, r=self.r_squared,
        )

    def __repr__(self):
        return "PeakResult({})".format(self.summary())


# ===========================================================================================
# Model functions  (kept module-level so curve_fit + the figure can both use them)
# ===========================================================================================
def _gaussian(x, amp, cen, sigma, base):
    return base + amp * np.exp(-((x - cen) ** 2) / (2.0 * sigma ** 2))


def _lorentzian(x, amp, cen, gamma, base):
    return base + amp * (gamma ** 2) / ((x - cen) ** 2 + gamma ** 2)


def _voigt(x, amp, cen, sigma, gamma, base):
    from scipy.special import voigt_profile
    # voigt_profile is area-normalized; scale so 'amp' is the peak height
    peak = voigt_profile(0.0, sigma, gamma)
    peak = peak if peak != 0 else 1.0
    return base + amp * voigt_profile(x - cen, sigma, gamma) / peak


def _erf_step(x, amp, cen, k, base):
    from scipy.special import erf
    return base + amp * erf(k * (x - cen))


_GAUSS_FWHM = 2.0 * math.sqrt(2.0 * math.log(2.0))   # sigma -> FWHM


def _voigt_fwhm(sigma, gamma):
    """Olivero & Longbothum (1977) approximation for the Voigt FWHM."""
    fg = _GAUSS_FWHM * abs(sigma)
    fl = 2.0 * abs(gamma)
    return 0.5346 * fl + math.sqrt(0.2166 * fl * fl + fg * fg)


# ===========================================================================================
# Model-free statistics
# ===========================================================================================
def _baseline(y):
    """Robust background estimate: median of the lowest ~10% of points (>= min)."""
    n = max(1, int(round(0.10 * len(y))))
    return float(np.median(np.sort(y)[:n]))


def _mad_sigma(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return float("nan")
    med = np.median(values)
    mad = np.median(np.abs(values - med))
    return float(1.4826 * mad)


def _refined_peak(x, y):
    """argmax with 3-point parabolic sub-step refinement (returns x at the vertex)."""
    i = int(np.argmax(y))
    if 0 < i < len(y) - 1:
        ym1, y0, yp1 = y[i - 1], y[i], y[i + 1]
        denom = (ym1 - 2.0 * y0 + yp1)
        if denom != 0:
            delta = 0.5 * (ym1 - yp1) / denom        # in index units, |delta|<~1
            delta = float(np.clip(delta, -1.0, 1.0))
            dx = (x[i + 1] - x[i - 1]) / 2.0
            return float(x[i] + delta * dx)
    return float(x[i])


def _com(x, y, base):
    """Baseline-subtracted center of mass (background no longer biases it)."""
    w = np.clip(y - base, 0.0, None)
    s = w.sum()
    return float(np.sum(x * w) / s) if s > 0 else float("nan")


def _halfmax_crossings(x, y, base):
    """Linearly-interpolated x where y crosses the half-maximum level.

    Returns the sorted list of crossing x-values (relative to a baseline-subtracted profile).
    """
    level = base + 0.5 * (np.max(y) - base)
    yc = y - level
    crossings = []
    for i in range(1, len(yc)):
        a, b = yc[i - 1], yc[i]
        if a == 0.0:
            crossings.append(float(x[i - 1]))
        elif (a < 0) != (b < 0):  # sign change
            t = a / (a - b)
            crossings.append(float(x[i - 1] + t * (x[i] - x[i - 1])))
    return crossings


def _smooth_for_edges(y, window=3):
    """Small median smoother for threshold decisions only; reported arrays remain raw."""
    y = np.asarray(y, dtype=float)
    window = int(window or 1)
    if window <= 1 or len(y) < 3:
        return y
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.pad(y, (half, half), mode="edge")
    return np.array([np.median(padded[i:i + window]) for i in range(len(y))])


def _threshold_crossing(x0, y0, x1, y1, threshold):
    if y1 == y0:
        return float(x0)
    frac = (threshold - y0) / (y1 - y0)
    frac = float(np.clip(frac, 0.0, 1.0))
    return float(x0 + frac * (x1 - x0))


def _baseline_width(x, y, base, *, sigma=3.0, merge_sigma=1.0, smooth_window=3):
    """Full contiguous beam support above baseline noise, robust to double-humped profiles.

    The selected interval is the above-threshold island containing the global peak, expanded across
    neighboring islands if the valley between them remains above a lower merge threshold.  This
    keeps a double-humped but physically continuous beam profile as one width.
    """
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    low_n = max(3, int(round(0.20 * len(y))))
    low = np.sort(y)[:low_n]
    noise = _mad_sigma(low)
    if not np.isfinite(noise) or noise <= 0:
        noise = float(np.std(low)) if len(low) else float("nan")
    if not np.isfinite(noise) or noise <= 0:
        # Last-resort floor avoids classifying perfectly flat synthetic data as wide.
        noise = max(float(np.std(y)), 1e-12)
    threshold = float(base + float(sigma) * noise)
    merge_threshold = float(base + float(merge_sigma) * noise)
    ys = _smooth_for_edges(y, smooth_window)
    above = ys > threshold
    if not np.any(above):
        return dict(left_base=float("nan"), right_base=float("nan"), cen_base=float("nan"),
                    fw_base=float("nan"), baseline_noise=noise, baseline_threshold=threshold)

    regions = []
    start = None
    for i, ok in enumerate(above):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            regions.append([start, i - 1])
            start = None
    if start is not None:
        regions.append([start, len(above) - 1])

    peak_i = int(np.argmax(y))
    selected = None
    for region in regions:
        if region[0] <= peak_i <= region[1]:
            selected = region
            break
    if selected is None:
        selected = max(regions, key=lambda r: np.max(y[r[0]:r[1] + 1]))

    # Merge adjacent above-threshold islands when the separating valley is still above the lower
    # baseline-noise threshold.  This handles double-humped profiles as one beam support.
    changed = True
    while changed:
        changed = False
        idx = regions.index(selected)
        if idx > 0:
            left = regions[idx - 1]
            valley = ys[left[1] + 1:selected[0]]
            if len(valley) and np.nanmin(valley) >= merge_threshold:
                selected[0] = left[0]
                regions.pop(idx - 1)
                changed = True
                continue
        idx = regions.index(selected)
        if idx < len(regions) - 1:
            right = regions[idx + 1]
            valley = ys[selected[1] + 1:right[0]]
            if len(valley) and np.nanmin(valley) >= merge_threshold:
                selected[1] = right[1]
                regions.pop(idx + 1)
                changed = True

    li, ri = selected
    if li > 0:
        left = _threshold_crossing(x[li - 1], ys[li - 1], x[li], ys[li], threshold)
    else:
        left = float(x[li])
    if ri < len(x) - 1:
        right = _threshold_crossing(x[ri], ys[ri], x[ri + 1], ys[ri + 1], threshold)
    else:
        right = float(x[ri])
    return dict(
        left_base=left,
        right_base=right,
        cen_base=0.5 * (left + right),
        fw_base=abs(right - left),
        baseline_noise=noise,
        baseline_threshold=threshold,
    )


def _model_free(x, y, *, baseline_sigma=3.0, baseline_merge_sigma=1.0, baseline_smooth=3):
    """All the model-free numbers in one pass.  Returns a dict."""
    base = _baseline(y)
    peak = _refined_peak(x, y)
    com = _com(x, y, base)
    cr = _halfmax_crossings(x, y, base)
    if len(cr) >= 2:
        cen_hm = 0.5 * (cr[0] + cr[-1])
        fwhm_hm = abs(cr[-1] - cr[0])
    else:
        cen_hm = float("nan")
        fwhm_hm = float("nan")
    bw = _baseline_width(x, y, base, sigma=baseline_sigma,
                         merge_sigma=baseline_merge_sigma, smooth_window=baseline_smooth)
    return dict(baseline=base, peak=peak, com=com, cen_halfmax=cen_hm, fwhm_halfmax=fwhm_hm,
                **bw)


def _looks_like_edge(x, y):
    """Heuristic peak-vs-edge classifier on a baseline/scale-normalized profile."""
    span = np.max(y) - np.min(y)
    if span <= 0:
        return False
    yn = (y - np.min(y)) / span
    edge_frac = max(1, int(round(0.15 * len(yn))))
    left = float(np.mean(yn[:edge_frac]))
    right = float(np.mean(yn[-edge_frac:]))
    step_score = abs(left - right)                 # large for a step
    peak_score = 1.0 - max(left, right)            # large for a localized bump on a low plateau
    return step_score > peak_score


# ===========================================================================================
# Curve fitting
# ===========================================================================================
def _fit_one(func, x, y, p0, yerr, bounds):
    """Run curve_fit; return (popt, perr, r2, aic) or None on failure."""
    from scipy.optimize import curve_fit
    try:
        kw = dict(p0=p0, maxfev=20000, bounds=bounds)
        if yerr is not None and np.all(np.isfinite(yerr)) and np.all(yerr > 0):
            kw.update(sigma=yerr, absolute_sigma=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            popt, pcov = curve_fit(func, x, y, **kw)
    except Exception:
        return None
    resid = y - func(x, *popt)
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1.0
    r2 = 1.0 - ss_res / ss_tot
    n, k = len(x), len(popt)
    # AIC for least-squares; smaller is better.  Guard ss_res==0.
    aic = n * math.log(ss_res / n) + 2 * k if ss_res > 0 else -math.inf
    with np.errstate(invalid="ignore"):
        perr = np.sqrt(np.diag(pcov))
    return dict(popt=popt, perr=perr, pcov=pcov, r2=r2, aic=aic, func=func)


def _band_from_cov(func, x, popt, pcov, nsamples=200):
    """Monte-Carlo 1σ confidence band: sample params ~ N(popt, pcov), take 16/84 percentiles."""
    try:
        if not np.all(np.isfinite(pcov)):
            raise ValueError
        draws = np.random.multivariate_normal(popt, pcov, size=nsamples)
        curves = np.array([func(x, *p) for p in draws])
        lo = np.nanpercentile(curves, 16, axis=0)
        hi = np.nanpercentile(curves, 84, axis=0)
        return lo, hi
    except Exception:
        y = func(x, *popt)
        return y.copy(), y.copy()


# ===========================================================================================
# The pure analyzer
# ===========================================================================================
def analyze_xy(
    x,
    y,
    yerr=None,
    der: bool = False,
    model: str = "auto",
    smooth: bool = False,
    baseline_sigma: float = 3.0,
    baseline_merge_sigma: float = 1.0,
    baseline_smooth: int = 3,
    **context,
):
    """Analyze a 1-D line profile.  Pure: no db, no plotting.

    Parameters
    ----------
    x, y : array-like
        Scan abscissa (motor) and intensity.
    yerr : array-like, optional
        Per-point uncertainty.  If ``None``, Poisson ``sqrt(max(y,1))`` is assumed for the fit
        weighting (counts-like).  Pass explicit errors for normalized/derived data.
    der : bool
        Analyze the derivative ``dy/dx`` (for edge -> peak alignment), like ``ps(der=True)``.
    model : str
        ``"auto"`` (default), ``"gaussian"``, ``"lorentzian"``, ``"voigt"``, ``"erf"`` or
        ``"none"`` (model-free only).  For peaks ``"auto"`` tries G/L/V and keeps the best AIC.
    smooth : bool
        Savitzky-Golay smooth before differentiating (tames noisy ``der``).
    baseline_sigma : float
        Threshold for full baseline-width support: ``baseline + baseline_sigma * noise``.
    baseline_merge_sigma : float
        Lower threshold used to merge nearby above-threshold islands into one double-humped beam
        support.
    baseline_smooth : int
        Median-filter window used only for baseline-threshold edge decisions.
    **context
        Optional ``scan_id``/``uid``/``motor``/``detector``/``timestamp``/``normalized`` carried
        straight into the :class:`PeakResult`.

    Returns
    -------
    PeakResult
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    yerr = None if yerr is None else np.asarray(yerr, dtype=float)

    # sort by x and drop non-finite samples
    order = np.argsort(x)
    x, y = x[order], y[order]
    if yerr is not None:
        yerr = yerr[order]
    good = np.isfinite(x) & np.isfinite(y)
    x, y = x[good], y[good]
    if yerr is not None:
        yerr = yerr[good]

    res = PeakResult(
        derivative=der,
        scan_id=context.get("scan_id"),
        uid=context.get("uid"),
        motor=context.get("motor"),
        detector=context.get("detector"),
        timestamp=context.get("timestamp"),
        normalized=bool(context.get("normalized", False)),
    )

    if len(x) < 3:
        res.message = "not enough points to analyze (need >=3)"
        res.x, res.y = x, y
        res.yerr = yerr if yerr is not None else np.full_like(y, np.nan)
        return res

    # derivative path
    if der:
        if smooth:
            try:
                from scipy.signal import savgol_filter
                win = min(len(y) - (1 - len(y) % 2), 7)
                if win >= 3:
                    y = savgol_filter(y, win, 2)
            except Exception:
                pass
        y = np.diff(y)
        x = x[1:]
        yerr = None  # error propagation through diff is not meaningful here

    # default Poisson errors for fit weighting (counts-like)
    if yerr is None:
        fit_err = np.sqrt(np.clip(np.abs(y), 1.0, None))
    else:
        fit_err = yerr

    # ---- model-free numbers (always) ----
    # A falling edge has a negative derivative peak.  Use an inverted copy for model-free peak
    # finding/seeding so PEAK/CEN/COM point at the trough while the fit still uses original y.
    peak_polarity = 1.0
    if der:
        y_mid = float(np.median(y))
        if (y_mid - float(np.min(y))) > (float(np.max(y)) - y_mid):
            peak_polarity = -1.0
    peak_y = peak_polarity * y

    mf = _model_free(x, peak_y, baseline_sigma=baseline_sigma,
                     baseline_merge_sigma=baseline_merge_sigma,
                     baseline_smooth=baseline_smooth)
    res.baseline = mf["baseline"] / peak_polarity
    res.peak = mf["peak"]
    res.com = mf["com"]
    res.cen_halfmax = mf["cen_halfmax"]
    res.fwhm_halfmax = mf["fwhm_halfmax"]
    res.left_base = mf["left_base"]
    res.right_base = mf["right_base"]
    res.cen_base = mf["cen_base"]
    res.fw_base = mf["fw_base"]
    res.baseline_noise = mf["baseline_noise"]
    res.baseline_threshold = mf["baseline_threshold"] / peak_polarity
    # sensible defaults before any fit
    res.cen = mf["cen_halfmax"]
    res.fwhm = mf["fwhm_halfmax"]

    res.x, res.y = x, y
    res.yerr = fit_err

    # ---- decide profile kind ----
    is_edge = (model == "erf") or (model == "auto" and not der and _looks_like_edge(x, y))
    res.profile_kind = "edge" if is_edge else "peak"

    if model == "none":
        res.message = "model-free only"
        return res

    span = float(np.max(y) - np.min(y))
    width0 = mf["fwhm_halfmax"]
    if not np.isfinite(width0) or width0 <= 0:
        width0 = 0.25 * (x[-1] - x[0])
    cen0 = mf["cen_halfmax"]
    if not np.isfinite(cen0):
        cen0 = mf["peak"]
    base0 = mf["baseline"] / peak_polarity
    dx_total = abs(x[-1] - x[0]) or 1.0

    best = None
    if is_edge:
        # erf edge: amplitude ~ half the step; sign allows rising or falling edges
        rising = np.mean(y[-3:]) >= np.mean(y[:3])
        amp0 = 0.5 * span * (1.0 if rising else -1.0)
        k0 = 4.0 / dx_total
        p0 = [amp0, cen0, k0, base0 + 0.5 * span]
        bounds = ([-2 * span - 1, x[0], 0, np.min(y) - span],
                  [2 * span + 1, x[-1], 1e6 / dx_total, np.max(y) + span])
        best = _fit_one(_erf_step, x, y, p0, fit_err, bounds)
        if best is not None:
            best["name"] = "erf"
            amp, cen, k, base = best["popt"]
            res.amplitude, res.cen, res.baseline = float(amp), float(cen), float(base)
            res.cen_err = float(best["perr"][1])
            # erf "width": x-distance over which it goes 8%->92% (~ erf arg ±1)
            res.fwhm = float(2.0 / k) if k else float("nan")
            res.fwhm_err = (float(2.0 * best["perr"][2] / k ** 2)
                            if k and np.isfinite(best["perr"][2]) else float("nan"))
    else:
        sigma0 = max(width0 / _GAUSS_FWHM, 1e-6)
        amp0 = peak_polarity * max(span, 1e-9)
        amp_abs0 = abs(amp0)
        amp_low = -10 * amp_abs0 - 1 if der else 0.0
        amp_high = 10 * amp_abs0 + 1
        base_high = np.max(y) + span if der else np.max(y)
        candidates = {"gaussian": _gaussian, "lorentzian": _lorentzian, "voigt": _voigt}
        if model in candidates:
            candidates = {model: candidates[model]}
        fits = {}
        for name, func in candidates.items():
            if name == "gaussian":
                p0 = [amp0, cen0, sigma0, base0]
                bounds = ([amp_low, x[0], 1e-9, np.min(y) - span],
                          [amp_high, x[-1], 10 * dx_total, base_high])
            elif name == "lorentzian":
                p0 = [amp0, cen0, max(width0 / 2, 1e-6), base0]
                bounds = ([amp_low, x[0], 1e-9, np.min(y) - span],
                          [amp_high, x[-1], 10 * dx_total, base_high])
            else:  # voigt
                p0 = [amp0, cen0, sigma0, max(width0 / 4, 1e-6), base0]
                bounds = ([amp_low, x[0], 1e-9, 1e-9, np.min(y) - span],
                          [amp_high, x[-1], 10 * dx_total, 10 * dx_total, base_high])
            fit = _fit_one(func, x, y, p0, fit_err, bounds)
            if fit is not None:
                fit["name"] = name
                fits[name] = fit
        if fits:
            best = min(fits.values(), key=lambda f: f["aic"])
            name = best["name"]
            popt, perr = best["popt"], best["perr"]
            if name == "gaussian":
                amp, cen, sigma, base = popt
                res.fwhm = float(_GAUSS_FWHM * abs(sigma))
                res.fwhm_err = float(_GAUSS_FWHM * perr[2]) if np.isfinite(perr[2]) else float("nan")
            elif name == "lorentzian":
                amp, cen, gamma, base = popt
                res.fwhm = float(2.0 * abs(gamma))
                res.fwhm_err = float(2.0 * perr[2]) if np.isfinite(perr[2]) else float("nan")
            else:  # voigt
                amp, cen, sigma, gamma, base = popt
                res.fwhm = float(_voigt_fwhm(sigma, gamma))
                res.fwhm_err = float("nan")
            res.amplitude, res.cen, res.baseline = float(amp), float(cen), float(base)
            res.cen_err = float(perr[1]) if np.isfinite(perr[1]) else float("nan")

    if best is not None:
        res.model_name = best["name"]
        res.r_squared = float(best["r2"])
        fx = np.linspace(x[0], x[-1], 400)
        res.fit_x = fx
        res.fit_y = best["func"](fx, *best["popt"])
        res.fit_lo, res.fit_hi = _band_from_cov(best["func"], fx, best["popt"], best["pcov"])
        res.message = "fit ok"
    else:
        res.model_name = "none"
        res.message = "fit did not converge; reporting model-free stats"

    return res


# ===========================================================================================
# Bokeh figure
# ===========================================================================================
def make_figure(result: PeakResult, logy: bool = False, title: Optional[str] = None,
                width: int = 760, height: int = 460):
    """Build an interactive Bokeh figure from a :class:`PeakResult`.

    Pure (bokeh only).  Returns the ``figure`` so the terminal can ``show`` it now and a Bokeh
    app can embed it later (``bokeh.embed.json_item`` / ``components``).
    """
    from bokeh.plotting import figure
    from bokeh.models import ColumnDataSource, Span, Label, HoverTool, Whisker, Band

    r = result
    if title is None:
        title = "scan {sid}  ·  {mot} vs {det}".format(
            sid=r.scan_id if r.scan_id is not None else "?",
            mot=r.motor or "x", det=r.detector or "y",
        )
        if r.timestamp:
            title += "   @ " + str(r.timestamp)

    y_axis_type = "log" if logy else "linear"
    p = figure(
        width=width, height=height, title=title,
        x_axis_label=(r.motor or "x") + (" (derivative)" if r.derivative else ""),
        y_axis_label=r.detector or "intensity",
        y_axis_type=y_axis_type,
        tools="pan,box_zoom,wheel_zoom,reset,save",
        toolbar_location="above",
        background_fill_color="#fafafa",
    )

    x = np.asarray(r.x); y = np.asarray(r.y)
    yerr = np.asarray(r.yerr) if len(r.yerr) == len(y) else np.zeros_like(y)
    lower = y - yerr
    upper = y + yerr
    src = ColumnDataSource(dict(x=x, y=y, lower=lower, upper=upper))

    # --- shaded data error band (Poisson / supplied) ---
    if np.any(yerr > 0) and not logy:
        p.add_layout(Band(base="x", lower="lower", upper="upper", source=src,
                          fill_color="#1f77b4", fill_alpha=0.12, line_width=0))
    elif np.any(yerr > 0):
        # log axis: a shaded band can dip <=0; use whiskers instead
        p.add_layout(Whisker(base="x", lower="lower", upper="upper", source=src,
                             line_color="#1f77b4", line_alpha=0.4))

    # --- fit confidence band + curve ---
    if len(r.fit_x):
        fsrc = ColumnDataSource(dict(x=np.asarray(r.fit_x),
                                     y=np.asarray(r.fit_y),
                                     lo=np.asarray(r.fit_lo),
                                     hi=np.asarray(r.fit_hi)))
        if len(r.fit_lo) and not logy:
            p.add_layout(Band(base="x", lower="lo", upper="hi", source=fsrc,
                              fill_color="#d62728", fill_alpha=0.15, line_width=0))
        p.line("x", "y", source=fsrc, line_color="#d62728", line_width=2.5,
               legend_label="fit: {} (R²={:.4f})".format(r.model_name, r.r_squared))

    # --- data points ---
    data_glyph = p.scatter("x", "y", source=src, size=7, marker="circle",
                           fill_color="#1f77b4", line_color="#1f77b4", legend_label="data")
    p.line("x", "y", source=src, line_color="#1f77b4", line_alpha=0.35)

    # --- vertical markers ---
    def _vspan(val, color, dash, label):
        if val is None or not np.isfinite(val):
            return
        p.add_layout(Span(location=float(val), dimension="height",
                          line_color=color, line_dash=dash, line_width=2))

    _vspan(r.cen, "#d62728", "solid", "CEN")
    _vspan(r.peak, "#000000", "dashed", "PEAK")
    _vspan(r.com, "#2ca02c", "dotted", "COM")

    # --- stats annotation box ---
    def _fmt(v, e=None):
        if v is None or not np.isfinite(v):
            return "n/a"
        s = "{:.5g}".format(v)
        if e is not None and np.isfinite(e):
            s += " ± {:.2g}".format(e)
        return s

    lines = [
        "kind: {} / {}".format(r.profile_kind, r.model_name),
        "CEN  : {}".format(_fmt(r.cen, r.cen_err)),
        "FWHM : {}".format(_fmt(r.fwhm, r.fwhm_err)),
        "PEAK : {}".format(_fmt(r.peak)),
        "COM  : {}".format(_fmt(r.com)),
        "R²   : {}".format(_fmt(r.r_squared)),
    ]
    label = Label(x=10, y=height - 130, x_units="screen", y_units="screen",
                  text="\n".join(lines), text_font_size="10pt",
                  text_font="monospace", background_fill_color="white",
                  background_fill_alpha=0.75, border_line_color="#cccccc")
    p.add_layout(label)

    p.add_tools(HoverTool(renderers=[data_glyph],
                          tooltips=[("x", "@x{0.000000}"), ("y", "@y{0.000}")],
                          mode="vline"))
    p.legend.location = "top_right"
    p.legend.click_policy = "hide"
    p.legend.background_fill_alpha = 0.7
    p.title.text_font_size = "11pt"
    return p


# ===========================================================================================
# Field resolution from a databroker header  (mirrors ps, but with a helpful failure)
# ===========================================================================================
def _numeric_columns(table):
    cols = []
    for c in table.columns:
        try:
            if np.issubdtype(table[c].dtype, np.number):
                cols.append(c)
        except Exception:
            pass
    return cols


def _intensity_field(header, det, suffix):
    """Replicate ps's detector->column logic."""
    start = header.start
    if det == "default":
        det0 = start["detectors"][0]
    else:
        det0 = det
    if det0 == "elm" and suffix == "default":
        return "elm_sum_all", det0
    if det0 == "elm":
        return "elm" + suffix, det0
    if suffix == "default":
        return det0 + "_stats1_total", det0
    return det0 + suffix, det0


def _motor_field_from_columns(motor, columns):
    """Resolve a motor name to the column/event key used for its live readback."""
    if motor in columns:
        return motor
    for candidate in (motor + "_readback", motor + "_setpoint", motor + "_user_setpoint"):
        if candidate in columns:
            return candidate
    return motor


def _resolve_xy_from_header(header, det="default", suffix="default", norm=None):
    """Return (x, y, yerr, motor_name, det_name) from a databroker header.

    Raises a clear, column-listing error when the intensity/normalization column is absent.
    """
    table = header.table()
    start = header.start
    motor = start["motors"][0]
    motor_field = _motor_field_from_columns(motor, table.columns)

    intensity_field, det_name = _intensity_field(header, det, suffix)

    if motor_field not in table.columns:
        raise KeyError(
            "motor column {!r} not in the scan table. Available numeric columns: {}".format(
                motor, _numeric_columns(table)))
    if intensity_field not in table.columns:
        raise KeyError(
            "intensity column {!r} not found (detector={!r}).\n"
            "  -> pass det=/suffix= explicitly.  Available numeric columns: {}".format(
                intensity_field, det_name, _numeric_columns(table)))

    x = table[motor_field].to_numpy(dtype=float)
    y = table[intensity_field].to_numpy(dtype=float)
    yerr = np.sqrt(np.clip(np.abs(y), 1.0, None))   # Poisson on raw counts
    normalized = False

    if norm is not None:
        if norm not in table.columns:
            raise KeyError(
                "norm column {!r} not found. Available numeric columns: {}".format(
                    norm, _numeric_columns(table)))
        m = table[norm].to_numpy(dtype=float)
        m_safe = np.where(m == 0, np.nan, m)
        # error propagation: assume Poisson on both, relative errors add in quadrature
        rel = np.sqrt((yerr / np.clip(np.abs(y), 1.0, None)) ** 2
                      + (np.sqrt(np.clip(np.abs(m_safe), 1.0, None)) / m_safe) ** 2)
        y = y / m_safe
        yerr = np.abs(y) * rel
        normalized = True

    return x, y, yerr, motor, det_name, normalized


# ===========================================================================================
# Latest-result publishing for persistent viewers
# ===========================================================================================
def _utc_now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _full_config_key(key=PF_PUBLISH_KEY, prefix=PF_PUBLISH_PREFIX):
    prefix = str(prefix or "").rstrip(":")
    return "{}:{}".format(prefix, key) if prefix else str(key)


def _json_clean(value):
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return _json_clean(value.tolist())
    if isinstance(value, np.generic):
        return _json_clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _default_redis_client(*, host=None, port=6380, ssl=True, db=3, password=None,
                          secret_path="/etc/bluesky/redis.secret"):
    import redis

    host = host or os.environ.get("SMI_ACQUIRE_REDIS_HOST", "xf12id2-smi-redis1.nsls2.bnl.gov")
    if password is None:
        with open(secret_path) as fh:
            password = fh.read().strip()
    return redis.Redis(host, db=db, ssl=ssl, port=port, password=password,
                       socket_timeout=2, socket_connect_timeout=2)


def publish_pf_result(result, *, client=None, key=PF_PUBLISH_KEY, prefix=PF_PUBLISH_PREFIX,
                      host=None, port=6380, ssl=True, db=3, password=None,
                      secret_path="/etc/bluesky/redis.secret", extra=None):
    """Publish a :class:`PeakResult` JSON payload for persistent alignment viewers.

    The default destination matches ``smi-acquire``'s operational config store:
    Redis db=3, key ``swaxsconfig:alignment.pf.latest``.  Tests or alternate callers may pass any
    mapping-like ``client`` that implements ``set(key, value)``.
    """
    payload = {
        "version": 1,
        "updated": _utc_now(),
        "uid": result.uid,
        "scan_id": result.scan_id,
        "sample_name": None,
        "result": _json_clean(result.as_dict(arrays=True)),
    }
    if extra:
        payload.update(dict(extra))
    full_key = _full_config_key(key, prefix)
    target = client if client is not None else _default_redis_client(
        host=host, port=port, ssl=ssl, db=db, password=password, secret_path=secret_path)
    target.set(full_key, json.dumps(payload, sort_keys=True, allow_nan=False))
    return payload


# ===========================================================================================
# Live RunEngine callback: analyze documents as they are emitted
# ===========================================================================================
class LivePF:
    """Bluesky document callback that publishes live :func:`pf`-style analysis.

    This object does not subscribe itself.  Pass an instance directly to a single RunEngine call,
    for example ``RE(plan(), LivePF(der=True, det="pil2M"))``, or subscribe/unsubscribe it using
    the RunEngine APIs during testing.
    """

    def __init__(
        self,
        det="default",
        suffix="default",
        norm=None,
        der=False,
        model="auto",
        smooth=False,
        baseline_sigma=3.0,
        baseline_merge_sigma=1.0,
        baseline_smooth=3,
        min_points=None,
        every=1,
        stream="primary",
        publish=True,
        publish_client=None,
        publish_key=PF_LIVE_PUBLISH_KEY,
        publish_prefix=PF_PUBLISH_PREFIX,
        publish_db=3,
        publish_host=None,
        publish_port=6380,
        publish_ssl=True,
        publish_password=None,
        publish_secret_path="/etc/bluesky/redis.secret",
        print_summary=True,
    ):
        self.det = det
        self.suffix = suffix
        self.norm = norm
        self.der = der
        self.model = model
        self.smooth = smooth
        self.baseline_sigma = baseline_sigma
        self.baseline_merge_sigma = baseline_merge_sigma
        self.baseline_smooth = baseline_smooth
        self.min_points = int(min_points or (5 if der else 3))
        self.every = max(1, int(every or 1))
        self.stream = stream
        self.publish = publish
        self.publish_client = publish_client
        self.publish_key = publish_key
        self.publish_prefix = publish_prefix
        self.publish_db = publish_db
        self.publish_host = publish_host
        self.publish_port = publish_port
        self.publish_ssl = publish_ssl
        self.publish_password = publish_password
        self.publish_secret_path = publish_secret_path
        self.print_summary = print_summary

        self.result = None
        self.published = None
        self.start_doc = None
        self.descriptor_uid = None
        self.motor = None
        self.motor_field = None
        self.detector = None
        self.intensity_field = None
        self.norm_field = None
        self.x = []
        self.y = []
        self.yerr = []
        self._event_count = 0
        self._redis_client = None

    def __call__(self, name, doc):
        if name == "start":
            self.start(doc)
        elif name == "descriptor":
            self.descriptor(doc)
        elif name == "event":
            self.event(doc)
        elif name == "stop":
            self.stop(doc)

    def start(self, doc):
        self.result = None
        self.published = None
        self.start_doc = doc
        self.descriptor_uid = None
        self.motor = (doc.get("motors") or [None])[0]
        self.motor_field = None
        self.detector = None
        self.intensity_field = None
        self.norm_field = self.norm
        self.x = []
        self.y = []
        self.yerr = []
        self._event_count = 0

    def descriptor(self, doc):
        if doc.get("name", "primary") != self.stream:
            return
        data_keys = doc.get("data_keys", {})
        columns = set(data_keys)
        start = self.start_doc or {}
        det0 = (start.get("detectors") or [None])[0] if self.det == "default" else self.det

        if self.motor is None:
            self.motor = (start.get("motors") or [None])[0]
        if self.motor is not None:
            self.motor_field = _motor_field_from_columns(self.motor, columns)
        if self.motor_field not in columns:
            return

        if det0 is None:
            return
        if det0 == "elm" and self.suffix == "default":
            intensity_field = "elm_sum_all"
        elif det0 == "elm":
            intensity_field = "elm" + self.suffix
        elif self.suffix == "default":
            intensity_field = det0 + "_stats1_total"
        else:
            intensity_field = det0 + self.suffix
        if intensity_field not in columns:
            return
        if self.norm_field is not None and self.norm_field not in columns:
            return

        self.descriptor_uid = doc.get("uid")
        self.detector = det0
        self.intensity_field = intensity_field

    def event(self, doc):
        if self.descriptor_uid is None or doc.get("descriptor") != self.descriptor_uid:
            return
        data = doc.get("data", {})
        try:
            x = float(data[self.motor_field])
            y_raw = float(data[self.intensity_field])
        except Exception:
            return
        if not (np.isfinite(x) and np.isfinite(y_raw)):
            return

        yerr = math.sqrt(max(abs(y_raw), 1.0))
        y = y_raw
        if self.norm_field is not None:
            try:
                monitor = float(data[self.norm_field])
            except Exception:
                return
            if not np.isfinite(monitor) or monitor == 0:
                return
            rel = math.sqrt((yerr / max(abs(y_raw), 1.0)) ** 2
                            + (math.sqrt(max(abs(monitor), 1.0)) / monitor) ** 2)
            y = y_raw / monitor
            yerr = abs(y) * rel

        self.x.append(x)
        self.y.append(y)
        self.yerr.append(yerr)
        self._event_count += 1
        if len(self.x) < self.min_points or self._event_count % self.every:
            return
        self.update(final=False)

    def stop(self, doc):
        if len(self.x) >= self.min_points:
            self.update(final=True, stop_doc=doc)

    def update(self, final=False, stop_doc=None):
        start = self.start_doc or {}
        try:
            ts = _dt.datetime.fromtimestamp(start["time"]).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts = None
        result = analyze_xy(
            np.asarray(self.x), np.asarray(self.y), yerr=np.asarray(self.yerr),
            der=self.der, model=self.model, smooth=self.smooth,
            baseline_sigma=self.baseline_sigma, baseline_merge_sigma=self.baseline_merge_sigma,
            baseline_smooth=self.baseline_smooth,
            scan_id=start.get("scan_id"), uid=start.get("uid"),
            motor=self.motor, detector=self.detector, timestamp=ts,
            normalized=self.norm_field is not None,
        )
        self.result = result
        if self.publish:
            extra = {
                "live": True,
                "final": bool(final),
                "num_points": len(self.x),
                "sample_name": start.get("sample_name") or start.get("sample"),
            }
            if stop_doc is not None:
                extra["exit_status"] = stop_doc.get("exit_status")
            try:
                client = self.publish_client
                if client is None:
                    if self._redis_client is None:
                        self._redis_client = _default_redis_client(
                            host=self.publish_host,
                            port=self.publish_port,
                            ssl=self.publish_ssl,
                            db=self.publish_db,
                            password=self.publish_password,
                            secret_path=self.publish_secret_path,
                        )
                    client = self._redis_client
                self.published = publish_pf_result(
                    result,
                    client=client,
                    key=self.publish_key,
                    prefix=self.publish_prefix,
                    extra=extra,
                )
            except Exception as exc:  # noqa: BLE001 - live analysis must not interrupt scans
                self.published = None
                if self.print_summary:
                    print("  (live pf publish failed: {}: {})".format(type(exc).__name__, exc))
        if self.print_summary:
            print("live pf ({}/{} pts): {}".format(
                "final" if final else "live", len(self.x), result.summary()))
        return result


# ===========================================================================================
# The terminal shell:  pf(-1)
# ===========================================================================================
def pf(
    uid=-1,
    det="default",
    suffix="default",
    norm=None,
    der=False,
    model="auto",
    smooth=False,
    baseline_sigma=3.0,
    baseline_merge_sigma=1.0,
    baseline_smooth=3,
    plot=False,
    logy=False,
    show=True,
    db=None,
    return_figure=False,
    publish=True,
    publish_client=None,
    publish_key=PF_PUBLISH_KEY,
    publish_prefix=PF_PUBLISH_PREFIX,
    publish_db=3,
    publish_host=None,
    publish_port=6380,
    publish_ssl=True,
    publish_password=None,
    publish_secret_path="/etc/bluesky/redis.secret",
):
    """Quick friendly display + peak/edge fit of a scan (a web-ready ``ps`` replacement).

    Parameters
    ----------
    uid : int | str
        databroker reference (default ``-1`` = latest scan).
    det, suffix : str
        Detector / column-suffix selection, same semantics as ``ps``.  ``"default"`` reads the
        scan's first detector and ``_stats1_total`` (``elm`` -> ``elm_sum_all``).
    norm : str, optional
        Monitor column to normalize the intensity by (e.g. an xbpm sum).
    der : bool
        Analyze the derivative (edge -> peak), like ``ps(der=True)``.
    model : str
        ``"auto"`` | ``"gaussian"`` | ``"lorentzian"`` | ``"voigt"`` | ``"erf"`` | ``"none"``.
    smooth : bool
        Savitzky-Golay smooth before differentiating.
    baseline_sigma, baseline_merge_sigma, baseline_smooth
        Controls for the model-free full width above baseline noise (``fw_base``) and its center
        (``cen_base``).  Useful for noisy, flat-topped, or double-humped profiles and slit
        centering.
    plot, show : bool
        Build / pop up the Bokeh figure.  The saved HTML path is printed as a headless fallback.
    logy : bool
        Log intensity axis.
    db : databroker catalog, optional
        Defaults to the ``db`` injected into this module by the profile (``analysis.db = db``),
        else a module/global ``db``.
    return_figure : bool
        Also return the Bokeh figure: returns ``(result, figure)``.
    publish : bool
        Publish the latest result for persistent viewers.  Writes JSON to Redis db=3 under
        ``swaxsconfig:alignment.pf.latest`` by default, matching ``smi-acquire``'s config store.
    publish_client : object, optional
        Test/advanced hook: object with ``set(key, value)``.  If omitted and ``publish=True``, a
        Redis client is opened lazily using the ``publish_*`` connection options.

    Returns
    -------
    PeakResult  (or ``(PeakResult, figure)`` when ``return_figure``).
        The headline numbers are also stored as attributes on ``pf`` (``pf.cen``, ``pf.peak``,
        ``pf.com``, ``pf.fwhm``, ``pf.result``, ``pf.figure``) for ps-style use.
    """
    catalog = db if db is not None else globals().get("db")
    if catalog is None:
        raise RuntimeError(
            "no databroker catalog available: pass db=... or set analysis.db = db "
            "(the profile injects this at runtime).")

    header = catalog[uid]
    start = header.start
    try:
        ts = _dt.datetime.fromtimestamp(start["time"]).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = None

    x, y, yerr, motor, det_name, normalized = _resolve_xy_from_header(
        header, det=det, suffix=suffix, norm=norm)

    result = analyze_xy(
        x, y, yerr=yerr, der=der, model=model, smooth=smooth,
        baseline_sigma=baseline_sigma, baseline_merge_sigma=baseline_merge_sigma,
        baseline_smooth=baseline_smooth,
        scan_id=start.get("scan_id"), uid=start.get("uid"),
        motor=motor, detector=det_name, timestamp=ts, normalized=normalized,
    )

    # ps-compatible attributes
    pf.result = result
    pf.cen = result.cen
    pf.peak = result.peak
    pf.com = result.com
    pf.fwhm = result.fwhm
    pf.fw_base = result.fw_base
    pf.cen_base = result.cen_base
    pf.cen_err = result.cen_err
    pf.r_squared = result.r_squared

    print(result.summary())

    if publish:
        try:
            payload = publish_pf_result(
                result,
                client=publish_client,
                key=publish_key,
                prefix=publish_prefix,
                host=publish_host,
                port=publish_port,
                ssl=publish_ssl,
                db=publish_db,
                password=publish_password,
                secret_path=publish_secret_path,
                extra={"sample_name": start.get("sample_name") or start.get("sample")},
            )
            pf.published = payload
            print("  published -> {}".format(_full_config_key(publish_key, publish_prefix)))
        except Exception as exc:  # noqa: BLE001 - publishing must not hide the analysis result
            pf.published = None
            print("  (publish failed: {}: {})".format(type(exc).__name__, exc))

    fig = None
    if plot:
        fig = make_figure(result, logy=logy)
        pf.figure = fig
        if show:
            try:
                from bokeh.io import output_file, show as bshow
                import tempfile, os
                fname = os.path.join(tempfile.gettempdir(),
                                     "pf_{}.html".format(result.scan_id or "scan"))
                output_file(fname, title="pf {}".format(result.scan_id))
                bshow(fig)
                print("  figure -> {}".format(fname))
            except Exception as exc:  # never let display failure break the numbers
                print("  (figure display failed: {}: {})".format(type(exc).__name__, exc))

    if return_figure:
        return result, fig
    return result


# back-compat attribute defaults (so `pf.cen` exists before the first call, like ps)
pf.result = None
pf.figure = None
pf.published = None
pf.cen = float("nan")
pf.peak = float("nan")
pf.com = float("nan")
pf.fwhm = float("nan")
pf.fw_base = float("nan")
pf.cen_base = float("nan")
pf.cen_err = float("nan")
pf.r_squared = float("nan")
