"""End-to-end smoke tests: every preset + the composition layer produce well-formed runs.

These assert the core invariants (one balanced run, balanced events, expected event counts)
against simulated devices.  They are the regression net for plan development.
"""
import warnings

import pytest


# ---------------------------------------------------------------------------
# Composition layer
# ---------------------------------------------------------------------------
def test_compose_nested_axes_one_run(sim, inject):
    C = inject("smi_plans._compose")
    th0 = 0.0
    axes = [
        C.motor_axis("arc", sim.waxs, [0, 20], record=True, speed=C.SPEED_SLOW),
        C.incidence_axis(sim.piezo.th, th0, [0.1, 0.2]),
        C.energy_axis([2470, 2475, 2480], settle=0.0),
        C.motor_axis("x", sim.piezo.x, [0, 30, 60, 90, 120], record=True, speed=C.SPEED_FAST),
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # correct order -> no guardrail warning
        msgs = sim.messages(C.acquire("PS40", [sim.pil900KW, sim.pil2M], axes,
                                      reads=[sim.energy, sim.waxs], geometry="reflection"))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 2 * 2 * 3 * 5  # 60


def test_compose_ordering_guardrail_warns(sim, inject):
    C = inject("smi_plans._compose")
    bad = [
        C.motor_axis("x", sim.piezo.x, [0, 30, 60], speed=C.SPEED_FAST),   # fast outer (bad)
        C.motor_axis("arc", sim.waxs, [0, 20], speed=C.SPEED_SLOW),        # slow inner (bad)
    ]
    with pytest.warns(UserWarning, match="slow axis"):
        list(C.acquire("S", [sim.pil900KW], bad, reads=[sim.waxs], check_order=True))


def test_compose_single_point_degenerate(sim, inject):
    C = inject("smi_plans._compose")
    msgs = sim.messages(C.acquire("S", [sim.pil900KW], [C.energy_axis([2480])],
                                  reads=[sim.energy]))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 1


def test_compose_software_only_axis_time(sim, inject):
    """time_axis has a software-only move (no hardware) -> must still compose."""
    C = inject("smi_plans._compose")
    msgs = sim.messages(C.acquire("S", [sim.pil900KW], [C.time_axis(5, period=0.0)],
                                  reads=[sim.energy]))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 5


# ---------------------------------------------------------------------------
# Manual / interactive (input messages)
# ---------------------------------------------------------------------------
def test_manual_step_emits_input_and_records(sim, inject):
    C = inject("smi_plans._compose")
    thickness = sim.Signal(name="thickness_nm", value=0.0)
    plan = C.acquire("S", [sim.pil900KW], [C.energy_axis([2480, 2485])],
                     reads=[sim.energy],
                     setup=lambda: C.manual_step("Load sample", signals=[thickness]),
                     baseline=[thickness])
    # interactive plans can't be driven by a RunEngine here (input prompts); inspect the
    # message stream directly.  No bps.rd in this plan, so list() is faithful for counting.
    cmds = [m.command for m in sim.messages_only(plan)]
    assert cmds.count("input") == 2          # value prompt + confirm
    assert cmds.count("open_run") == cmds.count("close_run") == 1
    assert cmds.count("create") == cmds.count("save")


def test_manual_axis_enumerated(sim, inject):
    C = inject("smi_plans._compose")
    temp = C.manual_axis("temp_manual", "Dial the hot stage to", values=[35, 50, 65])
    cmds = [m.command for m in sim.messages_only(
        C.acquire("S", [sim.pil900KW], [temp, C.energy_axis([2480])], reads=[sim.energy]))]
    assert cmds.count("input") == 3
    assert cmds.count("save") == 3           # 3 manual temps * 1 energy


# ---------------------------------------------------------------------------
# Presets A,B,C,D,E,G,H (refactored onto _compose) + others spot-checked
# ---------------------------------------------------------------------------
def test_A_nexafs_run_updown(sim, inject):
    A = inject("smi_plans.technique_A_energy_edge")
    msgs = sim.messages(A.nexafs_run("S1", [2818, 2820, 2822, 2824], t=0.1, updown=True))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 8        # 4 up + 4 down


def test_B_giwaxs_run(sim, inject):
    B = inject("smi_plans.technique_B_grazing")
    msgs = sim.messages(B.giwaxs_run("B", th0=0.0, incident_angles=[0.1, 0.2],
                                     waxs_arc=[0, 20], t=0.1))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 4        # 2 arc * 2 ai


def test_C_temperature_ramp_run(sim, inject):
    C = inject("smi_plans.technique_C_temperature")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        msgs = sim.messages(C.temperature_ramp_run("C", C.linkam_heater(), [30, 60, 90], t=0.1))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 3


def test_D_map_grid_manual_run(sim, inject):
    D = inject("smi_plans.technique_D_mapping")
    msgs = sim.messages(D.map_grid_manual_run("D", sim.piezo.x, [0, 1, 2],
                                              sim.piezo.y, [0, 1]))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 6


def test_E_transmission_run_multispot(sim, inject):
    E = inject("smi_plans.technique_E_transmission")
    msgs = sim.messages(E.transmission_run("E", points_fast=5, d_fast=150, t=0.1,
                                           fast_axis=sim.piezo.y))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 5


def test_G_rh_step_series_run(sim, inject):
    G = inject("smi_plans.technique_G_humidity")
    msgs = sim.messages(G.rh_step_series_run("g", [30, 50, 70], t=0.1))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 3


def test_H_potential_step_run(sim, inject):
    H = inject("smi_plans.technique_H_echem")

    def setv(v):
        yield from sim.bps.null()

    msgs = sim.messages(H.potential_step_run("h", [0.0, 0.4, 0.8], set_potential=setv, t=0.1))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 3


def test_I_cdsaxs_rock_run(sim, inject):
    I = inject("smi_plans.technique_I_cdsaxs")
    msgs = sim.messages(I.cdsaxs_rock_run("S", prs_range=(-2, 2, 5), t=0.1))
    # a rocking curve is ONE coherent measurement (may include bracketing ref sub-runs)
    o, c = sim.run_count(msgs)
    assert o == c and o >= 1


def test_N_xpcs_burst_emits_documents(sim, inject):
    N = inject("smi_plans.technique_N_xpcs")
    msgs = sim.messages(N.xpcs_burst_run("S", frame_time=0.01, n_frames=10))
    sim.assert_one_run(msgs)          # the fix: a burst still emits run documents
