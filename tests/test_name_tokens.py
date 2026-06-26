"""WS2 regression tests: filename-token validation in ``acquire``.

A ``{token}`` in the run's ``sample_name`` is filled by the downstream file writer from the recorded
event keys (``<device>_<attr>``).  A token with no matching key raises ``KeyError(field)`` AFTER the
scan has taken data.  ``acquire`` now validates tokens at build time and raises a clear ``ValueError``
instead -- catching the exact ``KeyError('x')`` that broke real runs (axis named ``x`` but key is
``piezo_x``).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_inject_leak_into_qserver():
    """Contain ``inject``'s global pollution of ``_qserver`` (see test_energy_move for rationale)."""
    import importlib
    qs = importlib.import_module("smi_plans._qserver")
    before = dict(qs.__dict__)
    yield
    for k in list(qs.__dict__):
        if k not in before:
            delattr(qs, k)
        elif qs.__dict__[k] is not before[k]:
            setattr(qs, k, before[k])


def _build(plan):
    """Drive a plan generator far enough to trigger build-time validation (which runs before the
    first yielded message), without needing a RunEngine.  Returns on first message or completion."""
    next(iter(plan), None)


def test_bare_axis_token_x_is_rejected(sim, inject):
    """The classic bug: token {x} while only piezo_x is recorded -> build-time ValueError."""
    C = inject("smi_plans._compose")
    plan = C.acquire("S", [sim.pil900KW],
                     [C.motor_axis("x", sim.piezo.x, [0, 100], record=True)],
                     reads=[sim.energy], name_tokens=["x{x}"])
    with pytest.raises(ValueError) as ei:
        _build(plan)
    assert "x" in str(ei.value) and "piezo_x" in str(ei.value)  # message is actionable


def test_full_device_key_token_is_accepted(sim, inject):
    """{piezo_x} (the real recorded key) is fine."""
    C = inject("smi_plans._compose")
    res = sim.run(C.acquire("S", [sim.pil900KW],
                            [C.motor_axis("x", sim.piezo.x, [0, 100], record=True)],
                            reads=[sim.energy], name_tokens=["x{piezo_x}"]))
    sim.assert_one_run(res)


def test_common_token_energy_energy_not_flagged(sim, inject):
    """{energy_energy} is a COMMON_TOKENS entry (naming preprocessor injects it) -> never flagged,
    even though the SIM energy device's describe() key is 'energy', not 'energy_energy'."""
    C = inject("smi_plans._compose")
    res = sim.run(C.acquire("S", [sim.pil900KW], [C.energy_axis([2480], settle=0.0)],
                            reads=[sim.energy], name_tokens=["{energy_energy}eV"]))
    sim.assert_one_run(res)


def test_axis_record_signal_name_is_accepted(sim, inject):
    """A token matching an axis record-Signal name (incident_angle) resolves."""
    C = inject("smi_plans._compose")
    axis = C.incidence_axis(sim.piezo.th if hasattr(sim.piezo, "th") else sim.piezo.x,
                            0.0, [0.1, 0.2])
    res = sim.run(C.acquire("S", [sim.pil900KW], [axis], reads=[sim.energy],
                            name_tokens=["ai{incident_angle}"]))
    sim.assert_one_run(res)


def test_xbpm_prefixed_token_accepted(sim, inject):
    """{xbpm2_sumX} resolves via the xbpm2 device's describe() keys (prefix match)."""
    C = inject("smi_plans._compose")
    res = sim.run(C.acquire("S", [sim.pil900KW], [C.energy_axis([2480], settle=0.0)],
                            reads=[sim.energy, sim.xbpm2], name_tokens=["bpm{xbpm2_sumX}"]))
    sim.assert_one_run(res)


def test_validate_tokens_false_escape_hatch(sim, inject):
    """validate_tokens=False bypasses the check (advanced/one-off use)."""
    C = inject("smi_plans._compose")
    # would raise with validation on; with it off, the plan builds (the bad token would only bite
    # the post-run namer, which the sim doesn't run).
    plan = C.acquire("S", [sim.pil900KW],
                     [C.motor_axis("x", sim.piezo.x, [0, 100], record=True)],
                     reads=[sim.energy], name_tokens=["x{x}"], validate_tokens=False)
    _build(plan)  # no raise
