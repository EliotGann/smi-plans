"""
Tests for :mod:`smi_plans.analysis` -- the ``pf`` peak/edge analyzer.

These are PURE: synthetic numpy profiles only.  No bluesky, no databroker, no RunEngine, no
hardware -- so they run anywhere scipy is installed (the standalone dev env and the beamline
env alike).  The db-backed shell (``pf``) is exercised with a tiny fake catalog.
"""
import numpy as np
import pytest
import json

pytest.importorskip("scipy")

from smi_plans.analysis import (
    analyze_xy, make_figure, PeakResult, pf, LivePF,
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
    assert r.fw_base > r.fwhm
    assert r.cen_base == pytest.approx(2.5, abs=0.08)


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


def test_derivative_of_falling_edge_fits_negative_peak():
    x, y = erf_scan(cen=-0.15, k=10.0, amp=-500.0, noise=2.0)
    r = analyze_xy(x, y, der=True, model="auto")
    assert r.derivative is True
    assert r.profile_kind == "peak"
    assert r.model_name in ("gaussian", "lorentzian", "voigt")
    assert r.amplitude < 0
    assert r.cen == pytest.approx(-0.15, abs=0.05)


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
    assert np.isfinite(r.fw_base)
    assert np.isfinite(r.cen_base)


def test_baseline_width_handles_double_humped_profile():
    x = np.linspace(-4.0, 4.0, 401)
    sigma = 0.35
    y = (
        50.0
        + 900.0 * np.exp(-((x + 0.8) ** 2) / (2 * sigma ** 2))
        + 850.0 * np.exp(-((x - 0.8) ** 2) / (2 * sigma ** 2))
        + 160.0 * np.exp(-(x ** 2) / (2 * 0.9 ** 2))
    )
    r = analyze_xy(x, y, model="auto", baseline_sigma=3.0, baseline_merge_sigma=1.0)

    assert r.fw_base > r.fwhm
    assert r.left_base < -1.0
    assert r.right_base > 1.0
    assert r.cen_base == pytest.approx(0.0, abs=0.1)


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
    r = pf(-1, db=cat, plot=False, publish=False)
    assert r.scan_id == 4242
    assert r.motor == "piezo_th"
    assert r.detector == "pil2M"
    assert r.cen == pytest.approx(1.0, abs=0.03)
    # ps-style attributes set on the function
    assert pf.cen == pytest.approx(1.0, abs=0.03)
    assert np.isfinite(pf.fwhm)


def test_pf_uses_motor_readback_when_motor_column_is_absent():
    pytest.importorskip("pandas")
    import pandas as pd

    x, y = gaussian_scan(cen=1.0, fwhm=0.25, noise=4.0)

    class _ReadbackHeader:
        start = {
            "scan_id": 4242, "uid": "fake-uid", "time": 1_700_000_000.0,
            "motors": ["bdm_y"], "detectors": ["pil2M"],
        }

        def table(self):
            return pd.DataFrame({"bdm_y_readback": x, "pil2M_stats1_total": y})

    r = pf(-1, db=_FakeCatalog(_ReadbackHeader()), plot=False, publish=False)
    assert r.motor == "bdm_y"
    assert r.cen == pytest.approx(1.0, abs=0.03)


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
        pf(-1, db=cat, plot=False, publish=False)
    assert "Available numeric columns" in str(exc.value)


def test_pf_normalization():
    pytest.importorskip("pandas")
    x, y = gaussian_scan(cen=0.0, fwhm=0.3, noise=2.0)
    mon = np.full_like(y, 2.0)
    cat = _FakeCatalog(_FakeHeader(x, y, motor="m", det="pil2M", extra={"xbpm2_sumX": mon}))
    r = pf(-1, db=cat, norm="xbpm2_sumX", plot=False, publish=False)
    assert r.normalized is True
    assert r.cen == pytest.approx(0.0, abs=0.03)


def test_pf_publish_writes_config_payload():
    pytest.importorskip("pandas")
    x, y = gaussian_scan(cen=1.0, fwhm=0.25, noise=4.0)
    cat = _FakeCatalog(_FakeHeader(x, y, motor="piezo_th", det="pil2M"))
    writes = {}

    class _Client:
        def set(self, key, value):
            writes[key] = value

    r = pf(-1, db=cat, plot=False, publish=True, publish_client=_Client())

    assert r.scan_id == 4242
    assert set(writes) == {"swaxsconfig:alignment.pf.latest"}
    payload = json.loads(writes["swaxsconfig:alignment.pf.latest"])
    assert payload["version"] == 1
    assert payload["scan_id"] == 4242
    assert payload["uid"] == "fake-uid"
    assert payload["result"]["scan_id"] == 4242
    assert payload["result"]["motor"] == "piezo_th"
    assert len(payload["result"]["x"]) == len(x)
    assert pf.published["scan_id"] == 4242


def test_live_pf_callback_publishes_incremental_payload():
    x, y = gaussian_scan(cen=1.0, fwhm=0.25, noise=0.0, n=9)
    writes = {}

    class _Client:
        def set(self, key, value):
            writes[key] = value

    cb = LivePF(publish_client=_Client(), print_summary=False)
    start = {
        "scan_id": 4242, "uid": "fake-uid", "time": 1_700_000_000.0,
        "motors": ["bdm_y"], "detectors": ["pil2M"],
    }
    descriptor = {
        "uid": "desc", "name": "primary",
        "data_keys": {"bdm_y_readback": {}, "pil2M_stats1_total": {}},
    }
    cb("start", start)
    cb("descriptor", descriptor)
    for xi, yi in zip(x, y):
        cb("event", {"descriptor": "desc", "data": {"bdm_y_readback": xi,
                                                       "pil2M_stats1_total": yi}})
    cb("stop", {"exit_status": "success"})

    assert cb.result.cen == pytest.approx(1.0, abs=0.05)
    assert set(writes) == {"swaxsconfig:alignment.pf.live"}
    payload = json.loads(writes["swaxsconfig:alignment.pf.live"])
    assert payload["live"] is True
    assert payload["final"] is True
    assert payload["num_points"] == len(x)
    assert payload["result"]["motor"] == "bdm_y"


def test_live_pf_callback_can_be_run_without_publishing():
    x, y = erf_scan(cen=0.0, k=10.0, amp=-500.0, noise=0.0, n=11)
    cb = LivePF(der=True, publish=False, print_summary=False)
    cb("start", {"scan_id": 1, "uid": "u", "time": 1_700_000_000.0,
                 "motors": ["m"], "detectors": ["pil2M"]})
    cb("descriptor", {"uid": "desc", "name": "primary",
                      "data_keys": {"m": {}, "pil2M_stats1_total": {}}})
    for xi, yi in zip(x, y):
        cb("event", {"descriptor": "desc", "data": {"m": xi,
                                                       "pil2M_stats1_total": yi}})
    assert cb.result.amplitude < 0
    assert cb.result.cen == pytest.approx(0.0, abs=0.08)
