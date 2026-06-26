"""WS4 regression tests: goto_sample moves from the runnable Position (nominal/refined).

The field bug: a GUI/spreadsheet sample stores coordinates in ``nominal``/``refined`` ``Position``
with the legacy flat ``piezo_x`` fields left None.  The old ``goto_sample`` read only the flat fields
(``piezo_moves()``), so such a sample never moved.  ``goto_sample`` now reads
``runnable_position()`` first (falling back to flat fields for old in-code samples), and supports a
``skip`` set for alignment-owned axes.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_inject_leak_into_qserver():
    import importlib
    qs = importlib.import_module("smi_plans._qserver")
    before = dict(qs.__dict__)
    yield
    for k in list(qs.__dict__):
        if k not in before:
            delattr(qs, k)
        elif qs.__dict__[k] is not before[k]:
            setattr(qs, k, before[k])


def _run_and_positions(sim, plan, devs):
    """Run a plan; return {name: position} for the given devices after it completes."""
    sim.run(plan)
    out = {}
    for d in devs:
        r = d.read()
        out[d.name] = float(next(iter(r.values()))["value"])
    return out


def test_goto_sample_moves_from_nominal_position(sim, inject):
    """A sample with ONLY a nominal Position (flat fields None) still moves -- the GUI case."""
    core = inject("smi_plans._core")
    from smi_plans import Sample, Position

    s = Sample(name="gui1", nominal=Position(frame="holder", piezo_x=1234.0, piezo_y=56.0,
                                             stage_phi=-3.0))
    # sanity: the legacy flat path would see nothing
    assert s.piezo_moves() == {}
    pos = _run_and_positions(sim, core.goto_sample(s),
                             [sim.piezo.x, sim.piezo.y, sim.stage.phi])
    assert pos["piezo_x"] == 1234.0
    assert pos["piezo_y"] == 56.0
    assert pos["stage_phi"] == -3.0


def test_goto_sample_refined_overrides_nominal(sim, inject):
    """runnable_position() prefers refined over nominal."""
    core = inject("smi_plans._core")
    from smi_plans import Sample, Position

    s = Sample(name="al1",
               nominal=Position(frame="holder", piezo_x=100.0),
               refined=Position(frame="lab", piezo_x=999.0))
    pos = _run_and_positions(sim, core.goto_sample(s), [sim.piezo.x])
    assert pos["piezo_x"] == 999.0


def test_goto_sample_skip_excludes_axes(sim, inject):
    """skip={piezo.y} leaves that axis where it was (alignment-owned)."""
    core = inject("smi_plans._core")
    from smi_plans import Sample, Position

    # park piezo.y at a known spot first, then goto with piezo.y skipped
    sim.run_count  # touch
    start = _run_and_positions(sim, core.bps.mv(sim.piezo.y, 7.0), [sim.piezo.y])["piezo_y"]
    s = Sample(name="gz1", nominal=Position(frame="holder", piezo_x=10.0, piezo_y=8888.0))
    pos = _run_and_positions(sim, core.goto_sample(s, skip={sim.piezo.y}),
                             [sim.piezo.x, sim.piezo.y])
    assert pos["piezo_x"] == 10.0
    assert pos["piezo_y"] == start  # unchanged (skipped), NOT 8888


def test_goto_sample_legacy_flat_fields_still_work(sim, inject):
    """Back-compat: an old in-code sample with flat piezo_x (no Position coords) still moves."""
    core = inject("smi_plans._core")
    from smi_plans import SampleList

    bar = SampleList.from_columns(names=["s1"], piezo_x=[321.0], piezo_y=[12.0])
    pos = _run_and_positions(sim, core.goto_sample(bar[0]), [sim.piezo.x, sim.piezo.y])
    assert pos["piezo_x"] == 321.0
    assert pos["piezo_y"] == 12.0


def test_position_moves_maps_stage_theta(sim, inject):
    """position_moves maps stage_theta/chi/phi -> stage.theta/chi/phi (Huber rename)."""
    core = inject("smi_plans._core")
    from smi_plans import Position

    p = Position(frame="lab", stage_theta=1.0, stage_chi=2.0, stage_phi=3.0, piezo_x=4.0)
    args = core.position_moves(p, sim.piezo, sim.stage)
    pairs = list(zip(args[::2], args[1::2]))
    devmap = {dev: val for dev, val in pairs}
    assert devmap[sim.stage.theta] == 1.0
    assert devmap[sim.stage.chi] == 2.0
    assert devmap[sim.stage.phi] == 3.0
    assert devmap[sim.piezo.x] == 4.0
