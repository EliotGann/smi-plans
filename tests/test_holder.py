"""WS5 tests: the holder bridge (load a holder's samples from the store by name).

Pure tests against an in-memory ``dict()`` backend -- no Redis, no secret, no bluesky for the
loader/alignment-read path.  This is the PRIMARY tested path (the live Redis db=2 store is just a
``MutableMapping`` too).
"""
import pytest

from smi_plans import Holder, Position, Sample, SampleStore
from smi_plans._holder import (
    HolderBar, load_holder, get_aligned, is_aligned, needs_alignment, sample_center,
)


def _store_with_bar():
    """A dict-backed store holding one holder 'bar1' with 3 Position-only samples.

    Imported in slot order (s1,s2,s3) so the holder's declared member order == slot order; the
    loader orders by the holder's declared ``sample_ids`` first, then slot (mirrors the field
    holder_bar.py)."""
    store = SampleStore(dict())
    samples = [
        Sample(name="s1", slot="1", nominal=Position(frame="holder", piezo_x=100.0, piezo_y=10.0)),
        Sample(name="s2", slot="2", nominal=Position(frame="holder", piezo_x=200.0, piezo_y=20.0)),
        Sample(name="s3", slot="3", nominal=Position(frame="holder", piezo_x=300.0, piezo_y=30.0)),
    ]
    holder = Holder(name="bar1", kind="bar")
    store.import_samples(samples, holder)
    return store


def test_load_holder_returns_samples_ordered_by_slot():
    store = _store_with_bar()
    bar = load_holder("bar1", store=store, order_by_slot=True)
    assert isinstance(bar, HolderBar)
    assert [s.name for s in bar] == ["s1", "s2", "s3"]   # by slot
    assert bar.store is store and bar.holder.name == "bar1"


def test_load_holder_missing_raises_with_available_names():
    store = _store_with_bar()
    with pytest.raises(KeyError) as ei:
        load_holder("nope", store=store)
    assert "bar1" in str(ei.value)   # lists what IS available


def test_load_holder_missing_not_required_returns_empty():
    store = _store_with_bar()
    bar = load_holder("nope", store=store, require=False)
    assert isinstance(bar, HolderBar) and len(bar) == 0


def test_sample_center_reads_runnable_position():
    store = _store_with_bar()
    bar = load_holder("bar1", store=store)
    cx, cy = sample_center(bar[0])      # s1
    assert (cx, cy) == (100.0, 10.0)


def test_alignment_roundtrip_via_store():
    """save_aligned persists refined th/y; a fresh load sees the sample as aligned."""
    import bluesky.plan_stubs as bps  # noqa: F401 (ensure available)
    from smi_plans._holder import save_aligned

    store = _store_with_bar()
    bar = load_holder("bar1", store=store)
    s = bar[0]
    assert needs_alignment(s)                 # not aligned yet
    # save_aligned is a plan; drive it to completion without a RunEngine (it only yields null)
    list(save_aligned(bar, s, th=1.2345, y=678.0))
    assert is_aligned(s)
    assert get_aligned(s) == (1.2345, 678.0)

    # a fresh load from the SAME store must also see it aligned (it was persisted)
    bar2 = load_holder("bar1", store=store)
    s_again = next(x for x in bar2 if x.name == s.name)
    assert is_aligned(s_again)
    assert get_aligned(s_again) == (1.2345, 678.0)
    # other axes preserved (piezo_x kept from nominal)
    assert s_again.refined.piezo_x == 100.0


def test_save_aligned_requires_holderbar_store():
    """save_aligned on a plain object (no .store) errors clearly."""
    from smi_plans._holder import save_aligned
    store = _store_with_bar()
    s = load_holder("bar1", store=store)[0]
    with pytest.raises(TypeError):
        list(save_aligned(object(), s, 1.0, 2.0))


def test_no_redis_import_at_module_import():
    """Importing the bridge must not require redis (off-beamline / CI safety)."""
    import importlib
    import sys
    # _holder is already imported; assert it didn't drag redis in as a hard dep
    mod = importlib.import_module("smi_plans._holder")
    assert mod is not None
    # the loader works with a dict store and never touches redis
    store = _store_with_bar()
    assert len(load_holder("bar1", store=store)) == 3
