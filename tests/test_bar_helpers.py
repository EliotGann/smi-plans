"""Tests for pure helpers promoted from field bar plans."""

import pytest

from smi_plans import Holder, Position, Sample, SampleStore
from smi_plans._bar_helpers import (
    adjust_holder_positions,
    adjust_bar_positions,
    apply_name_prefix,
    bar_name_tokens,
    preview_bar_name,
    preview_name,
    sort_holder_by_name,
    sort_bar_by_name,
)
from smi_plans._holder import load_holder


def _store_for_helpers():
    store = SampleStore(dict())
    samples = [
        Sample(name="s10", slot="1", nominal=Position(frame="holder", piezo_x=10, piezo_y=1)),
        Sample(name="s2", slot="2", nominal=Position(frame="holder", piezo_x=20, piezo_y=2)),
        Sample(name="s1", slot="3", nominal=Position(frame="holder", piezo_x=30, piezo_y=3)),
    ]
    holder = Holder(name="bar", kind="bar")
    store.import_samples(samples, holder)
    return store


def test_bar_name_tokens_and_preview():
    spec = {
        "name_prefix": "Gao",
        "include_energy": False,
        "include_exposure": True,
        "arc_fmt": "waxs_{:.0f}",
        "extra_tokens": ["px_{piezo_x:.1f}"],
    }
    assert apply_name_prefix("s1", spec) == "Gao_s1"
    assert bar_name_tokens(15, **spec) == ["exp_{exposure_s}s", "waxs_15", "px_{piezo_x:.1f}"]
    assert preview_bar_name("s1", arc=15, name_spec=spec, printer=None) == (
        "Gao_s1_exp_{exposure_s}s_waxs_15_px_{piezo_x:.1f}_")
    assert preview_name("s1", arc=15, name_spec=spec, printer=None) == (
        "Gao_s1_exp_{exposure_s}s_waxs_15_px_{piezo_x:.1f}_")


def test_adjust_holder_positions_dry_run_does_not_write():
    store = _store_for_helpers()
    assert adjust_holder_positions(
        "bar", delta={"piezo_y": 5}, store=store, dry_run=True, printer=None) == 3
    assert adjust_bar_positions(
        "bar", delta={"piezo_y": 5}, store=store, dry_run=True, printer=None) == 3
    bar = load_holder("bar", store=store)
    assert [s.refined for s in bar] == [None, None, None]


def test_adjust_holder_positions_writes_refined_only():
    store = _store_for_helpers()
    assert adjust_holder_positions(
        "bar", delta={"piezo_y": 5}, absolute={"piezo_z": -100},
        store=store, dry_run=False, printer=None) == 3
    bar = load_holder("bar", store=store)
    assert [s.nominal.piezo_y for s in bar] == [1, 2, 3]
    assert [s.refined.piezo_y for s in bar] == [6, 7, 8]
    assert [s.refined.piezo_z for s in bar] == [-100, -100, -100]
    assert all(s.refined.frame == "lab" for s in bar)


def test_adjust_holder_positions_rejects_bad_axis():
    with pytest.raises(ValueError, match="unknown position axis"):
        adjust_holder_positions(
            "bar", delta={"not_an_axis": 1}, store=_store_for_helpers(), printer=None)


def test_sort_holder_by_name_natural_order():
    store = _store_for_helpers()
    assert sort_holder_by_name("bar", store=store, dry_run=True, printer=None) is True
    assert sort_bar_by_name("bar", store=store, dry_run=True, printer=None) is True
    assert [s.name for s in load_holder("bar", store=store)] == ["s10", "s2", "s1"]

    assert sort_holder_by_name("bar", store=store, dry_run=False, printer=None) is True
    assert [s.name for s in load_holder("bar", store=store)] == ["s1", "s2", "s10"]
    assert sort_holder_by_name("bar", store=store, dry_run=True, printer=None) is False
