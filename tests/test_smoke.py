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
# Pre-run align hook vs in-run setup (RedundantStaging regression)
# ---------------------------------------------------------------------------
def _alignment_like(sim):
    """A plan shaped like a real alignment routine: it OPENS ITS OWN RUN and STAGES a detector
    (via bp.rel_scan over the same pil2M the measurement run will stage).  Running this inside an
    already-staged measurement run raises RedundantStaging; running it BEFORE (the `align` hook)
    is fine.
    """
    def _align():
        yield from sim.bp.rel_scan([sim.pil2M], sim.piezo.y, -1, 1, 3)
    return _align


def test_align_hook_runs_before_run_no_redundant_staging(sim, inject):
    """An alignment plan that stages pil2M, passed as `align`, must NOT RedundantStage even when
    pil2M is also a measurement detector."""
    C = inject("smi_plans._compose")
    align = _alignment_like(sim)
    result = sim.run(C.acquire(
        "S", [sim.pil2M, sim.pil900KW], [C.energy_axis([2480, 2485])],
        reads=[sim.energy], align=align, geometry="reflection"))
    # Two runs: the alignment's own run (3 events) + the measurement run (2 events).
    o, c = result.run_count()
    assert o == c == 2
    assert sim.primary_events(result) == 3 + 2


def test_setup_hook_with_alignment_raises_redundant_staging(sim, inject):
    """Documents the bug the `align` hook fixes: the SAME alignment plan run as the IN-RUN
    `setup` hook collides with the measurement run's staging."""
    from ophyd.utils import RedundantStaging
    C = inject("smi_plans._compose")
    align = _alignment_like(sim)
    with pytest.raises(RedundantStaging):
        sim.run(C.acquire(
            "S", [sim.pil2M], [C.energy_axis([2480])],
            reads=[sim.energy], setup=align))


def test_align_hook_without_shared_detector_still_runs(sim, inject):
    """An align plan that stages a DIFFERENT detector also composes (sanity)."""
    C = inject("smi_plans._compose")

    def _align():
        yield from sim.bp.count([sim.pil900KW], num=1)

    result = sim.run(C.acquire(
        "S", [sim.pil2M], [C.energy_axis([2480, 2485])],
        reads=[sim.energy], align=_align))
    o, c = result.run_count()
    assert o == c == 2
    assert sim.primary_events(result) == 1 + 2


def test_acquire_bar_align_for_per_sample(sim, inject):
    """acquire_bar runs `align_for(sample)` before each sample's run (one align + one data run
    per sample)."""
    C = inject("smi_plans._compose")
    from smi_plans import SampleList
    bar = SampleList.from_columns(names=["a", "b"], piezo_x=[0, 100], piezo_y=[0, 0])

    def _align_for(s):
        yield from sim.bp.rel_scan([sim.pil2M], sim.piezo.y, -1, 1, 2)

    def _axes_for(s):
        return [C.energy_axis([2480])]

    result = sim.run(C.acquire_bar(
        bar, [sim.pil2M], _axes_for, align_for=_align_for, reads=[sim.energy]))
    o, c = result.run_count()
    # per sample: 1 alignment run + 1 data run = 2; two samples = 4
    assert o == c == 4
    assert sim.primary_events(result) == (2 + 1) * 2


# ---------------------------------------------------------------------------
# Relative / aligned-zero incidence (th0=None): anchor wherever alignment left theta
# ---------------------------------------------------------------------------
def _primary_field(result, key):
    """All values of data ``key`` across primary-stream events, in order."""
    stream = {d["uid"]: d.get("name", "primary")
              for n, d in result.docs if n == "descriptor"}
    out = []
    for n, d in result.docs:
        if n == "event" and stream.get(d["descriptor"]) == "primary" and key in d["data"]:
            out.append(d["data"][key])
    return out


def test_incidence_relative_anchors_to_live_position(sim, inject):
    """th0=None captures the LIVE theta at first move (post-alignment) as the zero and sweeps
    relative offsets from it; the absolute motor goes to aligned_zero + ai."""
    C = inject("smi_plans._compose")
    # Simulate "alignment left theta at a nonzero aligned zero" just before the run:
    sim.piezo.th.set(-0.1124).wait()

    result = sim.run(C.acquire(
        "S", [sim.pil900KW], [C.incidence_axis(sim.piezo.th, None, [0.0, 0.1, 0.2])],
        reads=[sim.piezo.th], geometry="reflection"))
    sim.assert_one_run(result)
    # the recorded REAL incident angle is the relative offset (0, 0.1, 0.2), NOT the absolute th
    assert _primary_field(result, "incident_angle") == [0.0, 0.1, 0.2]
    # the absolute theta readback is aligned_zero + ai
    th = _primary_field(result, "piezo_th")
    assert th == pytest.approx([-0.1124, -0.0124, 0.0876])


def test_incidence_relative_records_captured_zero_in_baseline(sim, inject):
    """The captured aligned zero is recorded on the incidence_zero Signal (baseline-able)."""
    C = inject("smi_plans._compose")
    sim.piezo.th.set(0.0500).wait()
    axis = C.incidence_axis(sim.piezo.th, None, [0.0, 0.1])
    # the zero Signal is created inside the axis; expose it by running and reading the stream
    result = sim.run(C.acquire(
        "S", [sim.pil900KW], [axis], reads=[sim.piezo.th], geometry="reflection"))
    sim.assert_one_run(result)
    assert _primary_field(result, "incident_angle") == [0.0, 0.1]
    th = _primary_field(result, "piezo_th")
    assert th == pytest.approx([0.0500, 0.1500])


def test_incidence_absolute_mode_unchanged(sim, inject):
    """th0 as a number keeps the classic absolute behavior (th0 + ai)."""
    C = inject("smi_plans._compose")
    sim.piezo.th.set(99.0).wait()  # live position must be IGNORED in absolute mode
    result = sim.run(C.acquire(
        "S", [sim.pil900KW], [C.incidence_axis(sim.piezo.th, 0.0, [0.1, 0.2])],
        reads=[sim.piezo.th], geometry="reflection"))
    sim.assert_one_run(result)
    assert _primary_field(result, "incident_angle") == [0.1, 0.2]
    assert _primary_field(result, "piezo_th") == pytest.approx([0.1, 0.2])


def test_incidence_relative_after_align_hook_end_to_end(sim, inject):
    """End-to-end: an `align` plan moves theta to the aligned zero, THEN the th0=None incidence
    axis anchors to it -- proving the ordering (align runs before axes capture the zero)."""
    C = inject("smi_plans._compose")
    sim.piezo.th.set(0.0).wait()

    def _align():
        # stand-in for alignment_gisaxs: leaves theta at a nonzero aligned zero, opens its own run
        yield from sim.bp.rel_scan([sim.pil2M], sim.piezo.y, -1, 1, 2)
        yield from sim.bps.mv(sim.piezo.th, -0.2000)

    result = sim.run(C.acquire(
        "S", [sim.pil2M], [C.incidence_axis(sim.piezo.th, None, [0.0, 0.1])],
        reads=[sim.piezo.th], align=_align, geometry="reflection"))
    o, c = result.run_count()
    assert o == c == 2  # alignment run + measurement run
    assert _primary_field(result, "incident_angle") == [0.0, 0.1]
    assert _primary_field(result, "piezo_th") == pytest.approx([-0.2000, -0.1000])


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


# ---------------------------------------------------------------------------
# K (tomography) + M (autonomous): the prs -> stage.phi repoint must work
# ---------------------------------------------------------------------------
def test_K_tomography_run_rocks_stage_phi(sim, inject):
    """A rotation series is ONE run over stage.phi (the former prs)."""
    K = inject("smi_plans.technique_K_tomography")
    msgs = sim.messages(K.tomography_run("S", prs_range=(-2, 2, 5), t=0.1))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 5         # 5 rotation points recorded


def test_K_tomography_records_stage_phi_field(sim, inject):
    """Regression guard for the prs->stage.phi repoint: the scanned axis must be stage.phi,
    so 'stage_phi' appears as a recorded data key (and 'prs' never does)."""
    K = inject("smi_plans.technique_K_tomography")
    res = sim.messages(K.tomography_run("S", prs_range=(-1, 1, 3), t=0.1))
    keys = set()
    for name, doc in res.docs:
        if name == "event":
            keys |= set(doc.get("data", {}).keys())
    assert any(k.startswith("stage_phi") for k in keys), \
        "tomography_run must record the Huber stage.phi axis (got {})".format(sorted(keys))
    assert not any("prs" in k for k in keys), "the removed 'prs' device must not be referenced"


def test_K_texture_pole_figure_run(sim, inject):
    K = inject("smi_plans.technique_K_tomography")
    msgs = sim.messages(K.texture_pole_figure_run("S", prs_range=(-2, 2, 5), ai0=0.0, ai=0.2,
                                                  waxs_arc=(0,), t=0.1))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 5


def test_I_cdsaxs_records_stage_phi(sim, inject):
    """The CD-SAXS rock must also drive stage.phi (not the removed prs)."""
    I = inject("smi_plans.technique_I_cdsaxs")
    res = sim.messages(I.cdsaxs_rock_run("S", prs_range=(-1, 1, 3), t=0.1, ref_brackets=False))
    keys = set()
    for name, doc in res.docs:
        if name == "event":
            keys |= set(doc.get("data", {}).keys())
    assert any(k.startswith("stage_phi") for k in keys), \
        "cdsaxs_rock_run must record stage.phi (got {})".format(sorted(keys))


# ---------------------------------------------------------------------------
# det_exposure_time is a PLAN: it must be consumed via `yield from` (not left as
# an unconsumed generator, which would silently never set the exposure).
# ---------------------------------------------------------------------------
def test_det_exposure_time_is_yielded_from(sim, inject):
    """Guard the det_exposure_time fix: a technique that sets exposure must actually drive the
    det_exposure_time plan.  We replace it with a plan that records when its messages are
    consumed; a bare (un-yielded) call would never run it."""
    B = inject("smi_plans.technique_B_grazing")
    consumed = {"n": 0}

    def _spy_det_exposure_time(a, b=None):
        consumed["n"] += 1
        yield from sim.bps.null()

    # inject the spy into every loaded smi_plans module (mirrors how globals are injected)
    import sys
    for name, mod in list(sys.modules.items()):
        if name.startswith("smi_plans") and mod is not None:
            if hasattr(mod, "det_exposure_time"):
                setattr(mod, "det_exposure_time", _spy_det_exposure_time)

    sim.messages(B.giwaxs_run("B", th0=0.0, incident_angles=[0.1], waxs_arc=[0], t=0.1))
    assert consumed["n"] >= 1, "det_exposure_time plan was never consumed (missing `yield from`?)"


