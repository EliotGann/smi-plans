"""Tests for the pure-Python sample model (no bluesky needed) and the combined recipes."""
import warnings

import pytest


# ---------------------------------------------------------------------------
# _samples (pure python)
# ---------------------------------------------------------------------------
def test_sample_basic():
    from smi_plans import Sample
    s = Sample(name="s1", piezo_x=55000, piezo_y=5000, incident_angles=[0.1, 0.2],
               md={"project_name": "p"})
    assert s.piezo_moves() == {"x": 55000.0, "y": 5000.0}
    assert s.hexa_moves() == {}
    # base_md() now also carries the stable id/holder/slot join-keys (SAMPLE_SYSTEM_PLAN §2.4),
    # so assert the stable keys instead of full equality (sample_id is a minted uuid).
    bm = s.base_md()
    assert bm["sample_name"] == "s1"
    assert bm["project_name"] == "p"
    assert "sample_id" in bm


def test_samplelist_from_columns_shared():
    from smi_plans import SampleList
    bar = SampleList.from_columns(names=["a", "b", "c"], piezo_x=[1, 2, 3],
                                  incident_angles=[0.1, 0.2], md={"project_name": "X"})
    assert len(bar) == 3
    assert bar[1].piezo_x == 2.0
    assert bar[2].incident_angles == [0.1, 0.2]


def test_samplelist_length_mismatch_raises():
    from smi_plans import SampleList
    with pytest.raises(ValueError):
        SampleList.from_columns(names=["a", "b"], piezo_x=[1])


def test_samplelist_duplicate_names_raise():
    from smi_plans import Sample, SampleList
    with pytest.raises(ValueError):
        SampleList([Sample(name="dup"), Sample(name="dup")])


def test_sample_roundtrip_dict():
    from smi_plans import Sample
    s = Sample(name="a", piezo_x=1.0, md={"k": "v"})
    assert Sample.from_dict(s.to_dict()).name == "a"


def test_samplelist_from_csv(tmp_path):
    from smi_plans import SampleList
    p = tmp_path / "bar.csv"
    p.write_text("name,piezo_x,piezo_y,incident_angles,thickness_nm\n"
                 "A,1000,5000,0.1 0.2,40\nB,2000,5000,0.15;0.3,55\n")
    bar = SampleList.from_csv(str(p))
    assert len(bar) == 2
    assert bar[0].incident_angles == [0.1, 0.2]
    assert bar[1].incident_angles == [0.15, 0.3]
    assert bar[0].md == {"thickness_nm": "40"}      # unknown col -> md


# ---------------------------------------------------------------------------
# _core pure helpers (fname / merge_md)  -- import guarded, run without bluesky too
# ---------------------------------------------------------------------------
def test_fname_template():
    from smi_plans._core import fname
    n = fname("PS40nm", "{energy_energy}eV", "ai{incident_angle}", "bpm{xbpm2_sumX}")
    assert n == "PS40nm_{energy_energy}eV_ai{incident_angle}_bpm{xbpm2_sumX}_"


def test_merge_md_precedence():
    from smi_plans._core import merge_md
    assert merge_md({"a": 1, "b": 2}, None, {"b": 3, "c": 4}) == {"a": 1, "b": 3, "c": 4}


# ---------------------------------------------------------------------------
# recipes_combined (need sim devices)
# ---------------------------------------------------------------------------
def test_recipe_giwaxs_tempramp_energy_5loc(sim, inject):
    R = inject("smi_plans.recipes_combined")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        msgs = sim.messages(R.giwaxs_tempramp_energy_5loc(
            "PS40", edge_energies=[2470, 2475, 2480], temperatures=[30, 60],
            incident_angles=[0.1, 0.2], waxs_arc=[0, 20], x_locations=5, x_step=30,
            t=0.1, align=sim.alignement_gisaxs_hex))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 2 * 2 * 2 * 3 * 5   # 120


def test_recipe_operando_echem_energy(sim, inject):
    R = inject("smi_plans.recipes_combined")

    def setv(v):
        yield from sim.bps.null()

    msgs = sim.messages(R.operando_echem_energy("dev", potentials=[0.0, 0.4, 0.8],
                                                edge_energies=[2470, 2475],
                                                set_potential=setv, t=0.1))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 6


def test_recipe_build_axes_from_spec(sim, inject):
    """The GUI bridge: a declarative spec -> nested axes -> one run."""
    R = inject("smi_plans.recipes_combined")
    C = inject("smi_plans._compose")
    spec = [
        {"type": "motor", "name": "arc", "device": "waxs_arc", "values": [0, 20],
         "speed": C.SPEED_SLOW},
        {"type": "incidence", "values": [0.1, 0.2]},
        {"type": "energy", "values": [2470, 2475, 2480]},
    ]
    ctx = {"waxs_arc": sim.waxs.arc, "th_axis": sim.piezo.th, "th0": 0.0, "energy": sim.energy}
    axes = R.build_axes_from_spec(spec, context=ctx)
    assert [a.name for a in axes] == ["arc", "incidence", "energy"]
    msgs = sim.messages(C.acquire("S", [sim.pil900KW, sim.pil2M], axes,
                                  reads=[sim.energy, sim.waxs], check_order=False))
    sim.assert_one_run(msgs)
    assert sim.primary_events(msgs) == 12


# ---------------------------------------------------------------------------
# GUI bridge: spec shorthands (DOC_CORRECTIONS Part A3/A6 -- the GUI spec fields)
# ---------------------------------------------------------------------------
def test_spec_spatial_shorthand_step_n_records_relative_xy(sim, inject):
    """{"type":"spatial","x_step":30,"x_n":5,"center":[cx,cy]} -> 5 spots, relative {x} recorded."""
    R = inject("smi_plans.recipes_combined")
    C = inject("smi_plans._compose")
    spec = [{"type": "spatial", "x_step": 30, "x_n": 5, "center": [1000, 2000]}]
    ctx = {"piezo_x": sim.piezo.x, "piezo_y": sim.piezo.y}
    axes = R.build_axes_from_spec(spec, context=ctx)
    assert [a.name for a in axes] == ["x"]
    res = sim.run(C.acquire("S", [sim.pil900KW], axes, reads=[sim.energy],
                            name_tokens=["x{x}"]))   # {x} must resolve (relative offsets)
    sim.assert_one_run(res)
    xs = sorted(d["data"]["x"] for n, d in res.docs if n == "event" and "x" in d.get("data", {}))
    assert xs == [-60.0, -30.0, 0.0, 30.0, 60.0]     # centered on 1000, step 30, n=5
    assert sim.primary_events(res) == 5


def test_spec_spatial_explicit_xy_lists_still_work(sim, inject):
    """Explicit x/y position lists are also accepted (with center for {x}/{y})."""
    R = inject("smi_plans.recipes_combined")
    C = inject("smi_plans._compose")
    spec = [{"type": "spatial", "x": [970, 1000, 1030], "center": 1000}]
    ctx = {"piezo_x": sim.piezo.x, "piezo_y": sim.piezo.y}
    axes = R.build_axes_from_spec(spec, context=ctx)
    res = sim.run(C.acquire("S", [sim.pil900KW], axes, reads=[sim.energy], name_tokens=["x{x}"]))
    sim.assert_one_run(res)
    xs = sorted(d["data"]["x"] for n, d in res.docs if n == "event" and "x" in d.get("data", {}))
    assert xs == [-30.0, 0.0, 30.0]


def test_spec_spatial_shorthand_without_center_errors(sim, inject):
    """x_step/x_n with no center can't produce {x} -> clear error at build."""
    R = inject("smi_plans.recipes_combined")
    spec = [{"type": "spatial", "x_step": 30, "x_n": 5}]   # no center
    ctx = {"piezo_x": sim.piezo.x, "piezo_y": sim.piezo.y}
    with pytest.raises(ValueError):
        R.build_axes_from_spec(spec, context=ctx)


def test_spec_energy_grid_shorthand(sim, inject):
    """{"type":"energy","grid":{edge,...}} expands to a coarse/fine/coarse energy list."""
    R = inject("smi_plans.recipes_combined")
    spec = [{"type": "energy", "grid": {"edge": 2472}}]
    ctx = {"energy": sim.energy}
    axes = R.build_axes_from_spec(spec, context=ctx)
    assert axes[0].name == "energy"
    vals = list(axes[0].values)
    assert vals[0] < 2472 < vals[-1] and len(vals) > 10   # pre/near/post around the edge


def test_spec_motor_speed_string(sim, inject):
    """A motor axis speed given as the string 'slow' maps to SPEED_SLOW (GUI convenience)."""
    R = inject("smi_plans.recipes_combined")
    C = inject("smi_plans._compose")
    spec = [{"type": "motor", "name": "arc", "device": "waxs_arc", "values": [0, 20],
             "speed": "slow"}]
    ctx = {"waxs_arc": sim.waxs.arc}
    axes = R.build_axes_from_spec(spec, context=ctx)
    assert axes[0].speed == C.SPEED_SLOW


def test_spec_energy_flux_reseek_threshold(sim, inject):
    """The GUI 'flux_reseek':{threshold} maps onto the axis flux_threshold."""
    R = inject("smi_plans.recipes_combined")
    spec = [{"type": "energy", "values": [2480], "flux_reseek": {"signal": "xbpm2.sumX",
                                                                 "threshold": 50}}]
    ctx = {"energy": sim.energy, "flux_signal": sim.xbpm2.sumX}
    # builds without error and yields an energy axis (the threshold is wired into the axis closure)
    axes = R.build_axes_from_spec(spec, context=ctx)
    assert axes[0].name == "energy"
