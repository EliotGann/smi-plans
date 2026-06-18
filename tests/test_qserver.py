"""
Tests for the curated queueserver surface (:mod:`smi_plans._qserver`).

Two things are checked:

1. **Exposed surface (offline, no devices):** the module exposes the expected plan names, keeps
   the non-plan helpers / closed-loop controllers OUT, and exports the four ``*_from_spec``
   wrappers; ``resolve`` raises a clear error when a device is missing.

2. **Wrapper dry-runs (SimBeamline):** each ``*_from_spec`` wrapper, given a pure-JSON spec,
   resolves device *names* against the injected sim globals and produces exactly one well-formed
   Bluesky run.  This is the queue-submittable path proven end-to-end without hardware or a real
   queueserver.

The real ``qserver-list-plans-devices`` introspection is exercised separately by the profile's
``pixi run -e qs qs-list`` (it needs the QS env); here we assert the package-side contract.
"""
import inspect

import pytest

pytest.importorskip("bluesky")
pytest.importorskip("ophyd")

import smi_plans._qserver as q


# ---------------------------------------------------------------------------
# 1. Exposed surface (offline)
# ---------------------------------------------------------------------------
def test_exposes_the_four_from_spec_wrappers():
    names = q.qserver_plan_names()
    for w in ("acquire_from_spec", "nexafs_from_spec", "giwaxs_from_spec",
              "temperature_ramp_from_spec"):
        assert w in names


def test_exposes_namespaced_technique_presets():
    names = set(q.qserver_plan_names())
    # a representative spread across the A-O presets
    for n in ("A_nexafs_bar", "B_giwaxs_bar", "C_temperature_ramp_run",
              "E_transmission_run", "H_potential_step_run", "K_tomography_run"):
        assert n in names


def test_non_plan_helpers_and_controllers_are_not_exposed():
    names = set(q.qserver_plan_names())
    # detector-list / grid / heater builders are helpers, not plans
    for helper in ("A_energy_grid", "C_lakeshore_heater", "C_linkam_heater", "D_map_dets"):
        assert helper not in names
    # technique_M closed-loop controllers call RE() themselves -- must NOT be queue plans
    for ctrl in ("M_autonomous_loop", "M_align_loop", "M_ask_tell_loop"):
        assert ctrl not in names
    # the example/demo plans are excluded
    assert not any(n.endswith("_example") or n.endswith("_example_kinetics")
                   or n.split("_", 1)[-1] in ("example", "example_kinetics")
                   for n in names)


def test_internal_symbols_are_not_in_the_plan_surface():
    names = set(q.qserver_plan_names())
    for internal in ("resolve", "resolve_all", "DEVICE_REGISTRY", "qserver_plan_names"):
        assert internal not in names


def test_every_exposed_technique_name_is_a_generator_plan():
    # the namespaced technique exports must all be generator functions (genuine plans)
    for n in q.qserver_plan_names():
        if n[:2] in ("A_", "B_", "C_", "D_", "E_", "F_", "G_", "H_",
                     "I_", "J_", "K_", "L_", "M_", "N_", "O_"):
            obj = getattr(q, n)
            assert inspect.isgeneratorfunction(obj), n


def test_resolve_raises_clearly_when_device_missing():
    # offline there are no injected globals -> a clear, actionable error (not a silent None)
    with pytest.raises(q.DeviceResolutionError):
        q.resolve("pil2M")


# ---------------------------------------------------------------------------
# 2. Wrapper dry-runs against the simulated beamline
# ---------------------------------------------------------------------------
@pytest.fixture
def qmod(inject):
    """The _qserver module with sim globals injected (so ``resolve`` finds the sim devices)."""
    return inject("smi_plans._qserver")


def test_acquire_from_spec_builds_one_run(sim, qmod):
    spec = {
        "name": "PS40nm",
        "geometry": "reflection",
        "detectors": ["pil2M", "pil900KW"],
        "reads": ["energy", "waxs", "xbpm2", "xbpm3"],
        "exposure_s": 0.1,
        "scan_name": "giwaxs_Tramp",
        "project_name": "311234_Test",
        "axes": [
            {"type": "temperature", "values": [30, 60], "heater": "linkam"},
            {"type": "motor", "name": "arc", "device": "waxs", "values": [0, 20], "speed": 2},
            {"type": "incidence", "values": [0.10, 0.20]},
        ],
        "context": {"th_axis": "piezo.th", "th0": 0.0},
    }
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = sim.run(qmod.acquire_from_spec(spec))
    sim.assert_one_run(result)
    assert sim.primary_events(result) >= 1


def test_acquire_from_spec_resolves_names_to_objects(sim, qmod):
    # the spec carries only strings; the run must still succeed (names -> live sim devices)
    spec = {
        "name": "S1",
        "geometry": "transmission",
        "detectors": ["pil2M"],
        "reads": ["energy"],
        "axes": [{"type": "energy", "values": [2470, 2472, 2474]}],
    }
    result = sim.run(qmod.acquire_from_spec(spec))
    sim.assert_one_run(result)
    assert sim.primary_events(result) == 3


def test_acquire_from_spec_incidence_relative_with_align(sim, qmod):
    """An incidence axis with no th0 (relative) + an align routine: theta anchors to wherever
    align left it, and the recorded incident_angle is the relative offset."""
    sim.piezo.th.set(0.0).wait()
    spec = {
        "name": "PS_film",
        "geometry": "reflection",
        "detectors": ["pil2M"],
        "reads": ["piezo.th"],
        "align": "alignement_gisaxs_hex",   # sim moves piezo.th to 0.1 (see SimBeamline)
        "align_angle": 0.1,
        "axes": [{"type": "incidence", "values": [0.0, 0.05]}],   # no th0 -> relative
    }
    result = sim.run(qmod.acquire_from_spec(spec))
    # (the sim alignement_gisaxs_hex just moves piezo.th; it does not open its own run, so the
    # data run is the only one here -- the real routine opens alignment runs.)
    sim.assert_one_run(result)
    # the sim alignement_gisaxs_hex moves piezo.th to align_angle (0.1); relative incidence
    # then anchors to 0.1 and sweeps +[0, 0.05].  The recorded incident_angle is the RELATIVE
    # offset (0, 0.05), and the absolute theta is 0.1 + offset.
    stream = {d["uid"]: d.get("name", "primary")
              for n, d in result.docs if n == "descriptor"}
    rows = [(d["data"]["incident_angle"], d["data"]["piezo_th"])
            for n, d in result.docs
            if n == "event" and stream.get(d["descriptor"]) == "primary"]
    inc = [r[0] for r in rows]
    th = [r[1] for r in rows]
    assert inc == [0.0, 0.05]
    assert th == pytest.approx([0.1, 0.15])


def test_nexafs_from_spec_with_explicit_energies(sim, qmod):
    spec = {
        "name": "P3HT",
        "energies": [2818, 2820, 2822],
        "exposure_s": 0.1,
        "geometry": "transmission",
        "updown": False,
        "detectors": ["pil2M", "pin_diode", "xbpm2", "xbpm3"],
        "reads": ["energy"],
        "atten": ["att2_9"],
    }
    result = sim.run(qmod.nexafs_from_spec(spec))
    sim.assert_one_run(result)
    assert sim.primary_events(result) == 3


def test_nexafs_from_spec_with_edge_grid(sim, qmod):
    # 'edge' + 'grid' must build an energies array internally
    spec = {
        "name": "PVC",
        "edge": 2822,
        "grid": {"pre": [-4, -2, 2.0], "near": [-2, 2, 1.0], "post": [2, 6, 2.0]},
        "exposure_s": 0.1,
        "updown": False,
        "geometry": "transmission",
    }
    result = sim.run(qmod.nexafs_from_spec(spec))
    sim.assert_one_run(result)
    assert sim.primary_events(result) >= 1


def test_giwaxs_from_spec_aligns_then_runs(sim, qmod):
    spec = {
        "name": "PS_film",
        "incident_angles": [0.10, 0.20],
        "waxs_arc": [0, 20],
        "exposure_s": 0.1,
        "align": "alignement_gisaxs_hex",
        "align_angle": 0.1,
        "sample": {"piezo_x": 55000, "piezo_y": 5000, "piezo_z": 7000},
        "atten": ["att2_9"],
    }
    result = sim.run(qmod.giwaxs_from_spec(spec))
    sim.assert_one_run(result)
    # 2 arc positions x 2 incident angles = 4 events
    assert sim.primary_events(result) == 4


def test_giwaxs_from_spec_without_alignment(sim, qmod):
    # no 'align' -> measures at current theta; still one well-formed run
    spec = {
        "name": "PS_film",
        "incident_angles": [0.1],
        "waxs_arc": [0],
        "exposure_s": 0.1,
    }
    result = sim.run(qmod.giwaxs_from_spec(spec))
    sim.assert_one_run(result)
    assert sim.primary_events(result) == 1


def test_temperature_ramp_from_spec_lakeshore(sim, qmod):
    # The sim Lakeshore readback is a constant 27 degC, so use setpoints within tol of it (the
    # equilibration loop converges immediately) -- this exercises the lakeshore heater path and
    # the name-resolution wrapper without depending on a moving sim setpoint.
    spec = {
        "name": "BB40",
        "heater": "lakeshore",
        "setpoints": [27, 27, 27],
        "exposure_s": 0.1,
        "geometry": "transmission",
        "soak": 0.0,
        "tol": 1.0,
        "atten": ["att2_9"],
    }
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = sim.run(qmod.temperature_ramp_from_spec(spec))
    sim.assert_one_run(result)
    assert sim.primary_events(result) == 3


def test_temperature_ramp_from_spec_linkam(sim, qmod):
    # The sim Linkam readback tracks the last setpoint, so any setpoints converge instantly.
    spec = {
        "name": "PEO",
        "heater": "linkam",
        "setpoints": [120],
        "exposure_s": 0.1,
        "soak": 0.0,
    }
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = sim.run(qmod.temperature_ramp_from_spec(spec))
    sim.assert_one_run(result)
    assert sim.primary_events(result) == 1


def test_from_spec_missing_device_raises(sim, qmod):
    # a name not injected into the sim -> DeviceResolutionError at resolve time
    spec = {"name": "x", "detectors": ["not_a_real_detector"],
            "axes": [{"type": "energy", "values": [2470]}]}
    with pytest.raises(q.DeviceResolutionError):
        # consume the generator so the resolve() inside actually runs
        list(qmod.acquire_from_spec(spec))


def test_heater_spec_unknown_kind_raises(sim, qmod):
    spec = {"name": "x", "heater": "not_a_heater", "setpoints": [30]}
    with pytest.raises(ValueError):
        list(qmod.temperature_ramp_from_spec(spec))
