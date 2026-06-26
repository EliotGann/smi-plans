"""WS3 regression tests: spatial_grid_axes records relative-offset {x}/{y} Signals.

The field bug was that ``spatial_grid_axes`` named its axis ``x`` but recorded key ``piezo_x``, so a
``{x}`` filename token had no key -> ``KeyError('x')``.  With a ``center``, the grid now records a
``Signal(name="x"/"y")`` holding the *relative* offset, so ``{x}``/``{y}`` are valid tokens (and the
absolute ``piezo_x`` is still recorded for provenance).
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


def _event_keys(result):
    keys = set()
    for name, doc in result.docs:
        if name == "event":
            keys |= set(doc.get("data", {}).keys())
    return keys


def test_grid_with_center_records_relative_x_y(sim, inject):
    """A centered grid records both the relative offset (key 'x'/'y') AND absolute piezo_x/piezo_y."""
    C = inject("smi_plans._compose")
    axes = C.spatial_grid_axes(x_motor=sim.piezo.x, x=[900, 1000, 1100],
                               y_motor=sim.piezo.y, y=[1900, 2000, 2100],
                               center=(1000, 2000), snake=True)
    res = sim.run(C.acquire("S", [sim.pil900KW], axes, reads=[sim.energy],
                            name_tokens=["x{x}", "y{y}"]))   # tokens must resolve (WS2 validator)
    sim.assert_one_run(res)
    keys = _event_keys(res)
    assert "x" in keys and "y" in keys, "relative-offset Signals x/y must be recorded"
    assert any(k.startswith("piezo_x") for k in keys), "absolute piezo_x still recorded"
    assert any(k.startswith("piezo_y") for k in keys), "absolute piezo_y still recorded"


def test_grid_relative_offsets_are_recorded_values(sim, inject):
    """The recorded 'x' values are offsets from center (e.g. -100, 0, +100), not absolutes."""
    C = inject("smi_plans._compose")
    axes = C.spatial_grid_axes(x_motor=sim.piezo.x, x=[900, 1000, 1100], center=1000)
    res = sim.run(C.acquire("S", [sim.pil900KW], axes, reads=[sim.energy],
                            name_tokens=["x{x}"]))
    xs = []
    for name, doc in res.docs:
        if name == "event" and "x" in doc.get("data", {}):
            xs.append(doc["data"]["x"])
    assert sorted(xs) == [-100.0, 0.0, 100.0], "relative offsets expected, got {}".format(sorted(xs))


def test_grid_without_center_is_absolute_key(sim, inject):
    """No center -> absolute mode: key is piezo_x (token {piezo_x}); {x} would be invalid."""
    C = inject("smi_plans._compose")
    axes = C.spatial_grid_axes(x_motor=sim.piezo.x, x=[900, 1000, 1100])  # no center
    res = sim.run(C.acquire("S", [sim.pil900KW], axes, reads=[sim.energy],
                            name_tokens=["x{piezo_x}"]))
    sim.assert_one_run(res)
    keys = _event_keys(res)
    assert any(k.startswith("piezo_x") for k in keys)
    assert "x" not in keys, "absolute mode must NOT record a bare 'x' key"


def test_grid_x_token_invalid_without_center(sim, inject):
    """{x} with no center (absolute mode) is rejected by the WS2 validator at build time."""
    C = inject("smi_plans._compose")
    axes = C.spatial_grid_axes(x_motor=sim.piezo.x, x=[900, 1000, 1100])  # no center -> no 'x' key
    plan = C.acquire("S", [sim.pil900KW], axes, reads=[sim.energy], name_tokens=["x{x}"])
    with pytest.raises(ValueError):
        next(iter(plan), None)
