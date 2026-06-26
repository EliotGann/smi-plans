"""Regression tests for multi-run + external-asset detectors.

Classic ophyd AreaDetector file plugins create one Resource document per stage.  A detector staged
once and read into several simultaneously-open runs sends that Resource to the first run that reads
it, leaving later runs with Events that reference an unknown Resource.  ``multi_sample_run`` must
therefore stage detectors inside each run-keyed sample/slow-position point.
"""
import itertools
import time
import uuid

from ophyd import Device
from ophyd.status import Status


class ExternalAssetDetector(Device):
    """Small classic-ophyd-like detector that emits Resource/Datum documents."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._asset_docs_cache = []
        self._resource_uid = None
        self._counter = None
        self._last_datum_id = None

    def stage(self):
        ret = super().stage()
        self._resource_uid = str(uuid.uuid4())
        self._counter = itertools.count()
        self._asset_docs_cache.append((
            "resource",
            {
                "uid": self._resource_uid,
                "spec": "AD_TIFF",
                "root": "/tmp",
                "resource_path": "fake/image.tiff",
                "resource_kwargs": {"template": "%s%s_%6.6d.tiff", "filename": "fake"},
                "path_semantics": "posix",
            },
        ))
        return ret

    def unstage(self):
        self._asset_docs_cache.clear()
        self._resource_uid = None
        self._counter = None
        return super().unstage()

    def trigger(self):
        if self._resource_uid is None:
            raise RuntimeError("detector must be staged before triggering")
        self._last_datum_id = "{}/{}".format(self._resource_uid, next(self._counter))
        self._asset_docs_cache.append((
            "datum",
            {"resource": self._resource_uid, "datum_id": self._last_datum_id,
             "datum_kwargs": {}},
        ))
        status = Status()
        status.set_finished()
        return status

    def read(self):
        return {
            self.name + "_image": {"value": self._last_datum_id, "timestamp": time.time()}
        }

    def describe(self):
        return {
            self.name + "_image": {
                "source": "SIM:external",
                "dtype": "array",
                "shape": [1],
                "external": "FILESTORE:",
            }
        }

    def collect_asset_docs(self):
        items = list(self._asset_docs_cache)
        self._asset_docs_cache.clear()
        yield from items


def _noop_goto(sample):
    yield from ()


def _asset_docs_by_uid(result):
    resources = {doc["uid"]: doc for name, doc in result.docs if name == "resource"}
    datums = {doc["datum_id"]: doc for name, doc in result.docs if name == "datum"}
    return resources, datums


def _descriptor_runs(result):
    return {doc["uid"]: doc["run_start"] for name, doc in result.docs if name == "descriptor"}


def _assert_events_reference_resources_in_same_run(result, key="adet_image"):
    resources, datums = _asset_docs_by_uid(result)
    descriptor_runs = _descriptor_runs(result)
    checked = 0
    for name, doc in result.docs:
        if name != "event" or key not in doc["data"]:
            continue
        datum_id = doc["data"][key]
        datum = datums[datum_id]
        resource = resources[datum["resource"]]
        assert resource["run_start"] == descriptor_runs[doc["descriptor"]]
        checked += 1
    assert checked


def _core_with_sim_messages(sim):
    """Import _core and patch only the message modules it uses (avoid global inject leakage)."""
    import smi_plans._core as core
    core.bps = sim.bps
    core.bpp = sim.bpp
    return core


def test_multi_sample_run_external_assets_resolve_per_parallel_run(sim):
    core = _core_with_sim_messages(sim)
    from smi_plans import SampleList

    samples = SampleList.from_columns(names=["a", "b"])
    det = ExternalAssetDetector(name="adet")

    def point(sample, slow_value):
        # One stage covers several inner acquisitions for this sample/slow position.
        yield from core.bps.trigger_and_read([det])
        yield from core.bps.trigger_and_read([det])

    result = sim.run(core.multi_sample_run(
        samples, sim.waxs.arc, [20, 0], point,
        dets=[det], reads=[], scan_name="asset_parallel", goto=_noop_goto))

    assert result.run_count() == (2, 2)        # one run per sample, held open in parallel
    assert result.events_by_stream().get("primary") == 8
    resources, datums = _asset_docs_by_uid(result)
    assert len(resources) == 4                 # 2 samples x 2 slow positions
    assert len(datums) == 8                    # 2 events per staged point
    _assert_events_reference_resources_in_same_run(result)


def test_multi_sample_run_split_external_assets_resolve(sim):
    core = _core_with_sim_messages(sim)
    from smi_plans import SampleList

    samples = SampleList.from_columns(names=["a", "b"])
    det = ExternalAssetDetector(name="adet")

    def point(sample, slow_value):
        yield from core.bps.trigger_and_read([det])
        yield from core.bps.trigger_and_read([det])

    result = sim.run(core.multi_sample_run_split(
        samples, sim.waxs.arc, [20, 0], point,
        dets=[det], reads=[], scan_name="asset_split", goto=_noop_goto))

    assert result.run_count() == (4, 4)        # one run per (sample, slow position)
    assert result.events_by_stream().get("primary") == 8
    resources, datums = _asset_docs_by_uid(result)
    assert len(resources) == 4
    assert len(datums) == 8
    _assert_events_reference_resources_in_same_run(result)


def test_multi_sample_run_accepts_detector_callable(sim):
    core = _core_with_sim_messages(sim)
    from smi_plans import SampleList

    samples = SampleList.from_columns(names=["a"])
    det_low = ExternalAssetDetector(name="low")
    det_high = ExternalAssetDetector(name="high")

    def dets_for(sample, slow_value):
        return [det_high] if slow_value >= 10 else [det_low]

    def point(sample, slow_value):
        stream = "high" if slow_value >= 10 else "low"
        yield from core.bps.trigger_and_read(dets_for(sample, slow_value), name=stream)

    result = sim.run(core.multi_sample_run(
        samples, sim.waxs.arc, [20, 0], point,
        dets=dets_for, reads=[], scan_name="asset_callable", goto=_noop_goto))

    assert result.run_count() == (1, 1)
    assert result.events_by_stream().get("high") == 1
    assert result.events_by_stream().get("low") == 1
    resources, datums = _asset_docs_by_uid(result)
    assert len(resources) == 2
    assert len(datums) == 2
    _assert_events_reference_resources_in_same_run(result, key="high_image")
    _assert_events_reference_resources_in_same_run(result, key="low_image")
