"""WS1 regression tests: the gutted energy stepper.

``move_energy_fb`` / ``energy_axis`` were reduced to a plain, settle-guarded ``bps.mv(energy, E)``
(the ``energy`` device owns DCM feedback / IVU gap / harmonic).  These tests pin the new contract:

* exactly ONE ``set`` of ``energy`` per energy point (no double-set, no feedback-disable sets);
* the removed params (``max_step`` / ``fb_settle`` / ``double_set``) raise ``TypeError`` (the
  breaking change is intentional and discoverable).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_inject_leak_into_qserver():
    """Contain the ``inject`` fixture's global pollution.

    ``inject`` writes the sim device globals into EVERY loaded ``smi_plans.*`` module (including
    ``_qserver``), with no teardown.  ``_qserver.resolve`` looks names up in its own ``globals()``,
    so a leaked ``pil2M`` would make ``test_qserver``'s "device missing -> raises" assertion fail
    when this (alphabetically-earlier) module runs first.  Snapshot ``_qserver``'s namespace and
    remove anything this test added, restoring the offline precondition.
    """
    import importlib
    qs = importlib.import_module("smi_plans._qserver")
    before = dict(qs.__dict__)
    yield
    for k in list(qs.__dict__):
        if k not in before:
            delattr(qs, k)
        elif qs.__dict__[k] is not before[k]:
            setattr(qs, k, before[k])


def _energy_set_count(sim, plan, energy_obj):
    """Run ``plan`` on a RunEngine and count ``Msg('set', energy, ...)`` via a msg_hook.

    Mirrors ``SimBeamline.run`` (instant sleeps, non-interactive input) but installs a
    ``msg_hook`` so we can count messages -- the document stream alone can't see ``set``s.
    """
    from bluesky import RunEngine
    import bluesky.plan_stubs as _bps
    from unittest import mock
    import builtins

    counts = {"energy_set": 0}

    def _hook(msg):
        if msg.command == "set" and msg.obj is energy_obj:
            counts["energy_set"] += 1

    RE = RunEngine({})
    RE.msg_hook = _hook

    def _instant_sleep(t):
        yield from _bps.null()

    with mock.patch.object(_bps, "sleep", _instant_sleep), \
            mock.patch.object(builtins, "input", lambda prompt="": ""):
        RE(plan)
    return counts["energy_set"]


def test_energy_axis_sets_energy_once_per_point(sim, inject):
    """No double-set: an energy_axis over N points sets `energy` exactly N times."""
    C = inject("smi_plans._compose")
    energies = [2470, 2475, 2480, 2485]
    plan = C.acquire("S", [sim.pil900KW], [C.energy_axis(energies, settle=0.0)],
                     reads=[sim.energy])
    n = _energy_set_count(sim, plan, sim.energy)
    assert n == len(energies), (
        "expected one energy set per point ({}), got {} -- a double-set or feedback-toggle "
        "regression has returned".format(len(energies), n)
    )


def test_move_energy_fb_single_set(sim, inject):
    """The primitive issues exactly one energy set for a single move."""
    C = inject("smi_plans._compose")
    n = _energy_set_count(sim, C.move_energy_fb(2480, settle=0.0), sim.energy)
    assert n == 1


def test_move_energy_fb_skips_when_already_there(sim, inject):
    """The 'already there' guard: no set is issued if target ~= current energy."""
    C = inject("smi_plans._compose")
    # sim energy is a SynAxis: read its current setpoint/value via .read()
    reading = sim.energy.read()
    current = float(next(iter(reading.values()))["value"])
    n = _energy_set_count(sim, C.move_energy_fb(current, settle=0.0), sim.energy)
    assert n == 0


@pytest.mark.parametrize("badkw", ["max_step", "fb_settle", "double_set"])
def test_removed_energy_params_raise(sim, inject, badkw):
    """The removed params are gone (breaking, on purpose) -> TypeError on use."""
    C = inject("smi_plans._compose")
    with pytest.raises(TypeError):
        list(C.move_energy_fb(2480, **{badkw: 1}))
    with pytest.raises(TypeError):
        C.energy_axis([2480], **{badkw: 1})
