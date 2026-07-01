"""
Tests for :mod:`smi_plans.analysis` -- the ``pf`` peak/edge analyzer.

These are PURE: synthetic numpy profiles only.  No bluesky, no databroker, no RunEngine, no
hardware -- so they run anywhere scipy is installed (the standalone dev env and the beamline
env alike).  The db-backed shell (``pf``) is exercised with a tiny fake catalog.
"""
import numpy as np
import pytest

pytest.importorskip("scipy")

from smi_plans.analysis import (
    analyze_xy, make_figure, PeakResult, pf,
    _gaussian, _lorentzian, _erf_step, _looks_like_edge,
)


RNG = np.random.default_rng(12345)


# --------------------------------------------------------------------------- synthetic data
def gaussian_scan(cen=2.5, fwhm=0.4, amp=1000.0, base=50.0, n=61, noise=0.0, rng=RNG):
    x = np.linspace(cen - 1.5, cen + 1.5, n)
    sigma = fwhm / (2 * np.sqrt(2 * np.log(2)))
    y = _gaussian(x, amp, cen, sigma, base)
    if noise:
        y = y + rng.normal(0, noise, size=n)
    return x, y


def lorentzian_scan(cen=-1.0, fwhm=0.3, amp=800.0, base=20.0, n=81, noise=0.0, rng=RNG):
    x = np.linspace(cen - 1.2, cen + 1.2, n)
    gamma = fwhm / 2.0
    y = _lorentzian(x, amp, cen, gamma, base)
    if noise:
        y = y + rng.normal(0, noise, size=n)
    return x, y


def erf_scan(cen=0.7, k=8.0, amp=500.0, base=600.0, n=71, noise=0.0, rng=RNG):
    x = np.linspace(cen - 1.0, cen + 1.0, n)
    y = _erf_step(x, amp, cen, k, base)
    if noise:
        y = y + rng.normal(0, noise, size=n)
    return x, y


# --------------------------------------------------------------------------- core: peaks
def test_gaussian_center_and_fwhm_recovered():
    x, y = gaussian_scan(cen=2.5, fwhm=0.4, noise=5.0)
    r = analyze_xy(x, y, model="auto")
    assert r.profile_kind == "peak"
    assert r.model_name in ("gaussian", "voigt")  # voigt can win and still be ~gaussian
    assert r.cen == pytest.approx(2.5, abs=0.02)
    assert r.fwhm == pytest.approx(0.4, rel=0.10)
    assert r.r_squared > 0.97
    # uncertainty is finite and small
    assert np.isfinite(r.cen_err) and r.cen_err < 0.05


def test_lorentzian_recovered_when_forced():
    x, y = lorentzian_scan(cen=-1.0, fwhm=0.3, noise=4.0)
    r = analyze_xy(x, y, model="lorentzian")
    assert r.model_name == "lorentzian"
    assert r.cen == pytest.approx(-1.0, abs=0.02)
    assert r.fwhm == pytest.approx(0.3, rel=0.12)


def test_auto_picks_a_peak_model_for_a_peak():
    x, y = gaussian_scan(noise=2.0)
    r = analyze_xy(x, y, model="auto")
    assert r.model_name in ("gaussian", "lorentzian", "voigt")
    assert r.profile_kind == "peak"


# --------------------------------------------------------------------------- core: edges
def test_edge_detected_and_fit():
    x, y = erf_scan(cen=0.7, k=8.0, noise=3.0)
    assert _looks_like_edge(x, y) is True
    r = analyze_xy(x, y, model="auto")
    assert r.profile_kind == "edge"
    assert r.model_name == "erf"
    assert r.cen == pytest.approx(0.7, abs=0.03)


def test_derivative_of_edge_is_a_peak():
    x, y = erf_scan(cen=0.2, k=10.0, noise=2.0)
    r = analyze_xy(x, y, der=True, model="auto")
    assert r.derivative is True
    # derivative of an erf edge peaks at the edge center
    assert r.cen == pytest.approx(0.2, abs=0.05)


# --------------------------------------------------------------------------- baseline / COM
def test_com_is_baseline_corrected():
    # gaussian peak sitting on a big flat background, in an ASYMMETRIC window (peak near the
    # left edge).  The naive sum(x*y)/sum(y) is dragged toward the window midpoint by the
    # background; the baseline-subtracted COM stays on the peak.
    cen = 0.5
    sigma = 0.3 / (2 * np.sqrt(2 * np.log(2)))
    x = np.linspace(0.0, 4.0, 81)            # window midpoint = 2.0, far from the peak at 0.5
    y = _gaussian(x, 1000.0, cen, sigma, 5000.0)
    r = analyze_xy(x, y, model="none")
    naive_com = np.sum(x * y) / np.sum(y)
    assert abs(r.com - cen) < abs(naive_com - cen)
    assert r.com == pytest.approx(cen, abs=0.05)


def test_model_free_only_still_gives_numbers():
    x, y = gaussian_scan(noise=3.0)
    r = analyze_xy(x, y, model="none")
    assert r.model_name == "none"
    assert np.isfinite(r.peak)
    assert np.isfinite(r.com)
    assert np.isfinite(r.cen_halfmax)


# --------------------------------------------------------------------------- normalization path
def test_explicit_yerr_used_for_weighting():
    x, y = gaussian_scan(noise=10.0)
    yerr = np.full_like(y, 10.0)
    r = analyze_xy(x, y, yerr=yerr, model="gaussian")
    assert r.model_name == "gaussian"
    assert np.isfinite(r.cen_err)


# --------------------------------------------------------------------------- robustness
def test_too_few_points():
    r = analyze_xy([0.0, 1.0], [1.0, 2.0])
    assert "not enough points" in r.message


def test_flat_data_does_not_crash():
    x = np.linspace(0, 1, 21)
    y = np.full_like(x, 100.0)
    r = analyze_xy(x, y, model="auto")
    assert isinstance(r, PeakResult)  # no exception


def test_result_serializes():
    x, y = gaussian_scan(noise=2.0)
    r = analyze_xy(x, y)
    d = r.as_dict(arrays=False)
    assert "x" not in d and "cen" in d
    d2 = r.as_dict(arrays=True)
    assert isinstance(d2["x"], list)


# --------------------------------------------------------------------------- figure
def test_make_figure_returns_bokeh():
    pytest.importorskip("bokeh")
    from bokeh.plotting import figure as BFig
    x, y = gaussian_scan(noise=3.0)
    r = analyze_xy(x, y)
    fig = make_figure(r)
    assert isinstance(fig, BFig)
    # log axis path
    fig2 = make_figure(r, logy=True)
    assert isinstance(fig2, BFig)


# --------------------------------------------------------------------------- pf shell + fake db
class _FakeHeader:
    def __init__(self, x, y, motor, det, extra=None):
        import pandas as pd
        data = {motor: x, det + "_stats1_total": y}
        if extra:
            data.update(extra)
        self._table = pd.DataFrame(data)
        self.start = {
            "scan_id": 4242, "uid": "fake-uid", "time": 1_700_000_000.0,
            "motors": [motor], "detectors": [det],
        }

    def table(self):
        return self._table


class _FakeCatalog:
    def __init__(self, header):
        self._h = header

    def __getitem__(self, uid):
        return self._h


def test_pf_against_fake_catalog():
    pytest.importorskip("pandas")
    x, y = gaussian_scan(cen=1.0, fwhm=0.25, noise=4.0)
    cat = _FakeCatalog(_FakeHeader(x, y, motor="piezo_th", det="pil2M"))
    r = pf(-1, db=cat, plot=False)
    assert r.scan_id == 4242
    assert r.motor == "piezo_th"
    assert r.detector == "pil2M"
    assert r.cen == pytest.approx(1.0, abs=0.03)
    # ps-style attributes set on the function
    assert pf.cen == pytest.approx(1.0, abs=0.03)
    assert np.isfinite(pf.fwhm)


def test_pf_missing_detector_column_is_helpful():
    pytest.importorskip("pandas")
    import pandas as pd

    class _BadHeader:
        start = {"scan_id": 1, "uid": "u", "time": 1_700_000_000.0,
                 "motors": ["m"], "detectors": ["nope"]}

        def table(self):
            return pd.DataFrame({"m": [0, 1, 2], "something_else": [1, 2, 3]})

    cat = _FakeCatalog(_BadHeader())
    with pytest.raises(KeyError) as exc:
        pf(-1, db=cat, plot=False)
    assert "Available numeric columns" in str(exc.value)


def test_pf_normalization():
    pytest.importorskip("pandas")
    x, y = gaussian_scan(cen=0.0, fwhm=0.3, noise=2.0)
    mon = np.full_like(y, 2.0)
    cat = _FakeCatalog(_FakeHeader(x, y, motor="m", det="pil2M", extra={"xbpm2_sumX": mon}))
    r = pf(-1, db=cat, norm="xbpm2_sumX", plot=False)
    assert r.normalized is True
    assert r.cen == pytest.approx(0.0, abs=0.03)
