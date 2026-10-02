"""Reusable workflows: sequencing, saved coverage, changed loop order, failures."""

from dataclasses import replace

import pytest

from smi_plans import (
    AlignmentState,
    GrazingContext,
    GrazingScan,
    Sample,
    ScanCoverage,
    coverage_from_tiled,
    fresh_spot_grid,
    grazing_scan,
    select_samples,
)


@pytest.fixture(autouse=True)
def restore_injected_globals():
    """The legacy inject fixture mutates already imported modules, including qserver."""
    import sys

    from conftest import SimBeamline

    keys = set(SimBeamline().globals_dict())
    missing = object()
    snapshots = [(module, {key: getattr(module, key, missing) for key in keys})
                 for name, module in list(sys.modules.items())
                 if name.startswith("smi_plans") and module is not None]
    yield
    for module, values in snapshots:
        for key, value in values.items():
            if value is missing:
                if hasattr(module, key):
                    delattr(module, key)
            else:
                setattr(module, key, value)


def setup_context(sim, monkeypatch):
    import bluesky.preprocessors as bpp

    atten = sim.Signal(name="attenuation", value=1)
    atten.inserted = sim.Signal(name="attenuation_inserted", value="1_9,2_5")
    locked = sim.Signal(name="locked", value=0)
    moves, alignments, foil_settings, triggers = [], [], [], []
    original_set = sim.stage.theta.set
    original_foil = atten.set
    original_trigger = sim.pil2M.trigger

    def theta_set(value, **kwargs):
        moves.append(value)
        return original_set(value, **kwargs)

    def foil_set(value, **kwargs):
        foil_settings.append((sim.energy.position, value))
        return original_foil(value, **kwargs)

    def trigger():
        triggers.append(sim.pil2M.cam.acquire_time.get())
        assert locked.get() == 3
        return original_trigger()

    monkeypatch.setattr(sim.stage.theta, "set", theta_set)
    monkeypatch.setattr(atten, "set", foil_set)
    monkeypatch.setattr(sim.pil2M, "trigger", trigger)

    def exposure(t, total):
        assert t == total
        yield from sim.bps.mv(sim.pil2M.cam.acquire_time, t, sim.pil2M.cam.num_images, 1)

    def align(s):
        alignments.append(s.id)
        # An alignment that opens its own run and changes exposure/foils.
        yield from exposure(.5, .5)
        yield from sim.bps.mv(atten, 1, sim.stage.theta, .12, sim.stage.y, 1.23)
        yield from sim.bp.count([sim.pil2M], num=1)

    def lock(plan, *, start, stop, harmonic=None):
        assert harmonic in (None, 3)

        def body():
            yield from sim.bps.mv(locked, 3, sim.energy, start)
            yield from plan

        def restore():
            yield from sim.bps.mv(locked, 0)

        return (yield from bpp.finalize_wrapper(body(), restore()))

    ns = sim.globals_dict()
    ns.update(attenuation=atten, with_harmonic_lock=lock, det_exposure_time=exposure)
    ctx = GrazingContext.from_namespace(ns, align=align)
    ctx.log = {"moves": moves, "alignments": alignments, "foils": foil_settings,
               "triggers": triggers, "locked": locked}
    return ctx


def measurement_runs(result):
    starts = {d["uid"]: d for name, d in result.docs if name == "start" and "grazing_scan" in d}
    descriptors = {d["uid"]: d for name, d in result.docs if name == "descriptor"}
    stops = {d["run_start"]: d for name, d in result.docs if name == "stop"}
    events = {uid: [] for uid in starts}
    for name, doc in result.docs:
        if name == "event":
            desc = descriptors[doc["descriptor"]]
            if desc["name"] == "primary" and desc["run_start"] in starts:
                events[desc["run_start"]].append(doc)
    return [(s, events[uid], stops[uid]) for uid, s in starts.items()]


def test_selection_and_materialized_spec():
    from smi_plans import ListStore, NamedList

    samples = [Sample("Blank"), Sample("Au_sparse"), Sample("Au")]
    assert select_samples(samples, pattern="Au*") == samples[1:]
    assert select_samples(samples, names=["Au", "Blank"]) == [samples[0], samples[2]]
    assert select_samples(samples, exclude="Blank") == samples[1:]
    with pytest.raises(ValueError, match="found 0"):
        select_samples(samples, names="blank")
    with pytest.raises(ValueError, match="found 2"):
        select_samples([Sample("Au"), Sample("Au")], names="Au")
    store = ListStore({})
    store.put_list(NamedList("Au edge", "energy", values=[11860, 11919]))
    spec = GrazingScan.from_lists({"AuL3": "Au edge"}, [.3, .4], store=store)
    assert GrazingScan.from_dict(spec.to_dict()) == spec
    assert len(list(spec.points())) == 8
    assert replace(spec, fast_axis="incidence").fingerprint == spec.fingerprint
    assert replace(spec, exposure=2).fingerprint != spec.fingerprint
    assert spec.preview(samples)["events"] == 24
    with pytest.raises(ValueError, match="duplicate"):
        GrazingScan((("Au", [1, 1]),), (.3,))


@pytest.mark.parametrize("fast_axis,expected_moves", [("energy", 1 + 2 + 2 + 2), ("incidence", 1 + 2 + 6 + 2)])
def test_full_sequence_and_order(fast_axis, expected_moves, sim, inject, monkeypatch):
    inject("smi_plans._compose")
    ctx = setup_context(sim, monkeypatch)
    spec = GrazingScan((("InL3", (3671, 3721, 3730)),), (.3, .4), fast_axis=fast_axis,
                       name_tokens=("energy{energy_set}", "ai{incident_angle}", "bpm3{xbpm3_sumX}"))
    sample = Sample("In", piezo_x=123, piezo_y=456)
    result = sim.run(grazing_scan([sample], spec, ctx, batch_id="batch"))
    runs = measurement_runs(result)
    assert sum(len(e) for _, e, _ in runs) == 10
    assert len(runs) == (4 if fast_axis == "energy" else 5)
    assert len(ctx.log["moves"]) == expected_moves
    assert ctx.log["foils"] == [(3671, 1), (3671, 10)]
    assert ctx.log["triggers"] == [.5] + [1] * 10
    assert ctx.log["locked"].get() == 0
    records = [e["data"] for _, es, _ in runs for e in es]
    assert [r["scan_point_id"] for r in records] == [p.id for p in spec.points()]
    for start, events, _ in runs:
        info = start["grazing_scan"]
        assert info["alignment"]["theta_zero"] == .12
        assert info["alignment"]["position"]["stage_y"] == 1.23
        assert info["batch_id"] == "batch"
        assert "bpm3{xbpm3_sumX}" in start["sample_name"]
        for event in events:
            assert event["data"]["stage_theta"] == pytest.approx(.12 + event["data"]["incident_angle"])


def test_multi_sample_multi_arc_selection(sim, inject, monkeypatch):
    inject("smi_plans._compose")
    ctx = setup_context(sim, monkeypatch)
    spec = GrazingScan((("A", (4000, 4010)), ("B", (5000,))), (.3,), arcs=(0, 20), damage_checks=False)
    samples = [Sample("one", piezo_x=10), Sample("two", piezo_x=20)]
    result = sim.run(grazing_scan(samples, spec, ctx))
    runs = measurement_runs(result)
    assert [s["sample_id"] for s, _, _ in runs] == [samples[0].id] * 4 + [samples[1].id] * 4
    for s, events, _ in runs:
        for event in events:
            has_saxs = any(k.startswith("pil2M_") for k in event["data"])
            assert has_saxs == (s["grazing_scan"]["arc"] == 20)


def test_resume_partial_fast_axis_change(sim, inject, monkeypatch):
    inject("smi_plans._compose")
    ctx = setup_context(sim, monkeypatch)
    samples = [Sample("one", piezo_x=123), Sample("two", piezo_x=321)]
    spec = GrazingScan((("A", (4000, 4010, 4020)),), (.3, .4), fast_axis="incidence")
    full = measurement_runs(sim.run(grazing_scan(samples, spec, ctx, batch_id="resume")))
    coverage = ScanCoverage(spec.fingerprint, "resume")
    # First sample complete, second before-check + one full energy and one event
    # from the next energy. Stop status says success, but coverage is still partial.
    for start, events, stop in full[:7]:
        coverage.add_run(start, events, stop=stop)
    start, events, stop = full[7]
    partial_stop = dict(stop, num_events={"primary": 1})
    coverage.add_run(start, events[:1], stop=partial_stop)
    coverage = ScanCoverage.from_dict(coverage.to_dict())
    reordered = replace(spec, fast_axis="energy")
    pending = coverage.pending(reordered, samples[1].id)
    assert len(pending) == 5
    ctx.log["alignments"].clear()
    ctx.log["moves"].clear()
    reused = {sid: AlignmentState(**state) for sid, state in coverage.alignments.items()}
    result = sim.run(grazing_scan(samples, reordered, ctx, coverage=coverage, reuse_alignment=reused))
    runs = measurement_runs(result)
    assert all(s["sample_id"] == samples[1].id for s, _, _ in runs)
    actual = [e["data"]["scan_point_id"] for _, events, _ in runs for e in events]
    assert actual == [p.id for p in pending]
    assert not set(actual) & set(coverage.completed[samples[1].id])
    assert ctx.log["alignments"] == []
    assert len(ctx.log["moves"]) == 5  # restore zero + 2 remaining theta scans + 2 check angles
    for s, _, _ in runs:
        assert s["grazing_scan"]["continuation_of"] == coverage.source_uids
    with pytest.raises(ValueError, match="Explicit original"):
        sim.run(grazing_scan(samples, reordered, ctx, coverage=coverage))
    with pytest.raises(ValueError, match="settings changed"):
        coverage.pending(replace(spec, exposure=2), samples[1].id)


def test_coverage_flags_and_ingestion_validation(sim, inject, monkeypatch):
    inject("smi_plans._compose")
    ctx = setup_context(sim, monkeypatch)
    spec = GrazingScan((("A", (4000,)),), (.3,), damage_checks=False)
    sample = Sample("one")
    start, events, stop = measurement_runs(sim.run(grazing_scan([sample], spec, ctx, batch_id="x")))[0]
    coverage = ScanCoverage(spec.fingerprint, "x")
    with pytest.raises(ValueError, match="count differs"):
        coverage.add_run(start, [], stop=stop)
    assert coverage.completed == coverage.alignments == {}
    with pytest.raises(ValueError, match="open run"):
        coverage.add_run(start, events, stop=None)
    events[0]["data"]["stage_theta"] += .1
    coverage.add_run(start, events, stop=stop)
    assert len(coverage.flags) == 1
    assert coverage.pending(spec, sample.id) == []  # exposed, even if wrong angle

    class Array:
        def __init__(self, values):
            self.values = values

        def read(self):
            return self.values

    class Run(dict):
        pass

    run = Run(primary={"data": {k: Array([events[0]["data"][k]]) for k in
                                ("scan_point_id", "incident_angle", "stage_theta")}})
    run.metadata = {"start": start, "stop": stop}
    assert coverage_from_tiled([run]).to_dict() == coverage.to_dict()


def test_alignment_failure_stops_before_measurement(sim, inject, monkeypatch):
    inject("smi_plans._compose")
    ctx = setup_context(sim, monkeypatch)
    spec = GrazingScan((("A", (4000,)),), (.3,))
    original = ctx.align

    def bad_align(sample):
        yield from original(sample)
        signal = sim.stage.theta.setpoint
        monkeypatch.setattr(signal, "read", lambda: {signal.name: {"value": -1, "timestamp": 0}})

    ctx.align = bad_align
    with pytest.raises(RuntimeError, match="Alignment theta"):
        sim.run(grazing_scan([Sample("one")], spec, ctx))
    assert ctx.log["triggers"] == [.5]
    assert ctx.log["locked"].get() == 0


def test_exposure_guard_and_noop_recovery(sim, inject, monkeypatch):
    inject("smi_plans._compose")
    ctx = setup_context(sim, monkeypatch)
    sample = Sample("one")
    spec = GrazingScan((("A", (4000, 4010)),), (.3,), damage_checks=False)
    full = measurement_runs(sim.run(grazing_scan([sample], spec, ctx, batch_id="guard")))
    coverage = ScanCoverage(spec.fingerprint, "guard")
    for start, events, stop in full:
        coverage.add_run(start, events, stop=stop)
    ctx.log["moves"].clear()
    assert sim.run(grazing_scan([sample], spec, ctx, coverage=coverage)).run_count() == (0, 0)
    assert ctx.log["moves"] == []
    # No automatic foil substitution when recovering a partially exposed sample.
    start, events, stop = full[0]
    partial = ScanCoverage(spec.fingerprint, "guard")
    partial.add_run(start, events[:1], stop=dict(stop, num_events={"primary": 1}))
    aligned = {sample.id: AlignmentState(**partial.alignments[sample.id])}
    ctx.attenuation.inserted.put("different")
    with pytest.raises(RuntimeError, match="foils differ"):
        sim.run(grazing_scan([sample], spec, ctx, coverage=partial, reuse_alignment=aligned))
    ctx.attenuation.inserted.put("1_9,2_5")
    # A false successful move must not produce a detector exposure.
    original_read = sim.stage.theta.read

    def wrong_read():
        result = original_read()
        result[sim.stage.theta.name]["value"] = -1
        return result

    monkeypatch.setattr(sim.stage.theta, "read", wrong_read)
    previous_triggers = len(ctx.log["triggers"])
    with pytest.raises(RuntimeError, match="before exposure"):
        sim.run(grazing_scan([sample], spec, ctx, coverage=partial, reuse_alignment=aligned))
    assert len(ctx.log["triggers"]) == previous_triggers
    assert ctx.log["locked"].get() == 0


def test_fresh_spot_grids():
    points = fresh_spot_grid(856, anchor="top_middle")
    assert len(set(points)) == 856
    assert min(x for x, y in points) == -450
    assert max(x for x, y in points) == 450
    assert min(y for x, y in points) == 0
    assert max(y for x, y in points) == 850
    first = fresh_spot_grid(100, rows=21)
    second = fresh_spot_grid(100, rows=21, block=1)
    assert min(x for x, y in second) - max(x for x, y in first) == 100
    assert first[21][0] - first[20][0] == 100
    assert first[21][1] == first[20][1]
    assert first[22][1] - first[21][1] == -10
    assert min(x for x, y in fresh_spot_grid(100, anchor="top_left", shift=(100, 0))) == 100
