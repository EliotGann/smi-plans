"""Timed, document-producing voltage programs without EPICS or wall-clock sleeps."""
import re
from types import SimpleNamespace

import pytest
from bluesky import RunEngine, RunEngineInterrupted
from ophyd import Component as Cpt
from ophyd import Device, Signal
from test_multi_sample_assets import (
    ExternalAssetDetector,
    _assert_events_reference_resources_in_same_run,
)


@pytest.fixture(autouse=True)
def _isolate_queue_namespace():
    # The shared inject fixture populates all loaded modules without teardown.
    from smi_plans import _qserver as q
    before = dict(vars(q))
    yield
    for key in list(vars(q)):
        if key not in before:
            delattr(q, key)
        elif vars(q)[key] is not before[key]:
            setattr(q, key, before[key])


class Camera(Device):
    acquire_time = Cpt(Signal, value=0.25)
    acquire_period = Cpt(Signal, value=0.3)
    num_images = Cpt(Signal, value=7)


class AssetCamera(ExternalAssetDetector):
    cam = Cpt(Camera, "")

    def trigger(self):
        assert self.cam.num_images.get() == 1
        return super().trigger()


@pytest.fixture
def rig(sim, inject, monkeypatch):
    p = inject("smi_plans.technique_P_power")
    ps = sim.sorensen_ps1
    ps.out_main_status = Signal(name="sorensen_ps1_out_main_status", value=0)

    def update(**kwargs):
        enabled = ps.out_main_command.get()
        ps.out_main_status.put(enabled)
        ps.out_main_readback.put(ps.out_main_setpoint.get() if enabled else 0.0)
        ps.current.put(0.02 if enabled else 0.0)

    ps.out_main_command.subscribe(update)
    ps.out_main_setpoint.subscribe(update)
    det = AssetCamera(name="pil2M")
    waxs_det = AssetCamera(name="pil900KW")
    monkeypatch.setattr(p, "pil2M", det)
    monkeypatch.setattr(p, "pil900KW", waxs_det)
    clock = SimpleNamespace(now=0.0, acquisition=1.0, write_delay=0.0)
    monkeypatch.setattr(p, "_monotonic", lambda: clock.now)
    docs, messages, triggers = [], [], []
    re_engine = RunEngine({})
    re_engine.subscribe(lambda name, doc: docs.append((name, doc)))

    def hook(msg):
        messages.append(msg)
        if msg.command == "trigger" and msg.obj in (det, waxs_det):
            triggers.append((msg.obj.name, clock.now))
        if msg.command == "wait" and msg.kwargs.get("group") == "sorensen_program_frame":
            clock.now += clock.acquisition
        if msg.command == "set" and msg.obj is ps.out_main_setpoint:
            clock.now += clock.write_delay

    re_engine.msg_hook = hook

    def sleep(delay):
        clock.now += delay
        yield from p.bps.null()

    monkeypatch.setattr(p.bps, "sleep", sleep)

    def plan(**kwargs):
        opts = {"voltages": [0.8, 1.0], "hold_times": [4, 4], "reads": [], "baseline": []}
        opts.update(kwargs)
        return p.sorensen_voltage_program_run("sample", **opts)

    return SimpleNamespace(p=p, ps=ps, det=det, waxs=waxs_det, clock=clock, RE=re_engine,
                           docs=docs, messages=messages, triggers=triggers, plan=plan)


def events(rig):
    primary = {d["uid"] for n, d in rig.docs if n == "descriptor" and d["name"] == "primary"}
    return [d for n, d in rig.docs if n == "event" and d["descriptor"] in primary]


def assert_restored(rig):
    assert rig.ps.out_main_command.get() == 0
    for det in (rig.det, rig.waxs):
        assert det.cam.num_images.get() == 7
        assert det.cam.acquire_time.get() == 0.25
        assert det.cam.acquire_period.get() == 0.3
        assert det._resource_uid is None


@pytest.mark.parametrize("detector,expected", [
    ("saxs", ["pil2M"]), ("waxs", ["pil900KW"]), ("saxs_waxs", ["pil2M", "pil900KW"]),
])
def test_cadence_readbacks_and_assets(rig, detector, expected):
    rig.ps.out_main_command.put(1)  # baseline must force off, even if initially enabled
    rig.RE(rig.plan(detector=detector, current_limit=0.1))
    data = [d["data"] for d in events(rig)]
    assert len(data) == 5
    assert [d["bias_phase"] for d in data] == ["baseline"] + ["biased"] * 4
    assert [d["frame_index"] for d in data] == [-1, 0, 1, 2, 3]
    assert [d["program_step"] for d in data] == [-1, 0, 0, 1, 1]
    assert [d["elapsed_s"] for d in data] == [-1, 0, 2, 4, 6]
    assert [d["read_elapsed_s"] for d in data] == [-1, 1, 3, 5, 7]
    assert [d["step_applied_elapsed_s"] for d in data] == [-1, 0, 0, 4, 4]
    assert [d["sorensen_ps1_out_main_readback"] for d in data] == [0, 0.8, 0.8, 1, 1]
    assert [d["sorensen_ps1_out_main_status"] for d in data] == [0, 1, 1, 1, 1]
    assert [d["sorensen_ps1_current"] for d in data] == [0, 0.02, 0.02, 0.02, 0.02]
    assert all(d["sorensen_ps1_max_current"] == 0.1 for d in data)
    assert rig.clock.now == 9  # one baseline second + full eight-second program
    for name in expected:
        assert [t for n, t in rig.triggers if n == name] == [0, 1, 3, 5, 7]
        assert len({d[name + "_image"] for d in data}) == 5
        _assert_events_reference_resources_in_same_run(rig, key=name + "_image")
    start = next(d for n, d in rig.docs if n == "start")
    assert start["sorensen_program"]["n_frames"] == 4
    tokens = re.findall(r"\{([^}:]+)", start["sample_name"])
    assert all(set(tokens) <= set(d) for d in data)
    assert [n for n, _ in rig.docs].count("start") == 1
    assert [n for n, _ in rig.docs].count("stop") == 1
    close = next(i for i, msg in enumerate(rig.messages) if msg.command == "close_run")
    off = [i for i, msg in enumerate(rig.messages)
           if msg.command == "set" and msg.obj is rig.ps.out_main_command and msg.args[0] == 0]
    assert any(rig.messages[i].command == "save" for i in range(off[0], off[1]))
    assert off[1] < close  # output disabled before run-close callbacks/unstage
    assert_restored(rig)


def test_explicit_reads_occur_after_acquisition_wait(rig):
    rig.RE(rig.plan(baseline_image=False))
    first = next(i for i, m in enumerate(rig.messages) if m.command == "trigger")
    point = rig.messages[first:]
    wait = next(i for i, m in enumerate(point) if m.command == "wait")
    for signal in [rig.ps.current, rig.ps.out_main_readback]:
        assert next(i for i, m in enumerate(point) if m.command == "read" and m.obj is signal) > wait
    assert rig.clock.now == 8
    assert len(events(rig)) == 4


@pytest.mark.parametrize("overrides", [
    {"voltages": []}, {"hold_times": [4]}, {"hold_times": [3, 4]},
    {"hold_times": [0, 4]}, {"voltages": [float("nan"), 1]},
    {"frame_period": 0}, {"frame_period": float("inf")}, {"exposure_time": -1},
    {"exposure_time": 2}, {"current_limit": 0}, {"max_lateness": -1},
    {"max_lateness": 2}, {"detector": "unknown"}, {"dets": []},
])
def test_validation_before_hardware_messages(rig, overrides):
    with pytest.raises(ValueError):
        rig.RE(rig.plan(**overrides))
    assert not rig.messages


def test_slow_acquisition_fails_without_catchup(rig):
    rig.clock.acquisition = 2.2
    with pytest.raises(RuntimeError, match="cadence missed"):
        rig.RE(rig.plan(baseline_image=False))
    assert len(events(rig)) == 1  # completed image retained; no catch-up or next voltage
    assert rig.ps.out_main_setpoint.get() == 0.8
    assert next(d for n, d in rig.docs if n == "stop")["exit_status"] == "fail"
    assert_restored(rig)


def test_small_overhead_is_recorded_without_clock_drift(rig):
    rig.clock.write_delay = 0.05
    rig.RE(rig.plan(baseline_image=False))
    data = [d["data"] for d in events(rig)]
    assert [d["elapsed_s"] for d in data] == pytest.approx([0, 2, 4.05, 6])
    assert data[2]["timing_lateness_s"] == pytest.approx(0.05)
    assert data[2]["step_applied_elapsed_s"] == pytest.approx(4.05)


def test_slow_voltage_write_fails_before_exposure(rig):
    rig.clock.write_delay = 0.2
    with pytest.raises(RuntimeError, match="cadence missed"):
        rig.RE(rig.plan(baseline_image=False))
    assert len(events(rig)) == 2
    assert_restored(rig)


@pytest.mark.parametrize("fail_at", [1, 2])
def test_camera_failure_during_baseline_or_biased_image(rig, monkeypatch, fail_at):
    original = rig.det.trigger
    calls = []

    def trigger():
        calls.append(1)
        if len(calls) == fail_at:
            raise RuntimeError("camera failed")
        return original()

    monkeypatch.setattr(rig.det, "trigger", trigger)
    with pytest.raises(RuntimeError, match="camera failed"):
        rig.RE(rig.plan())
    assert_restored(rig)


def test_stage_failure_restores_configuration(rig, monkeypatch):
    def stage():
        raise RuntimeError("stage failed")
    monkeypatch.setattr(rig.det, "stage", stage)
    with pytest.raises(RuntimeError, match="stage failed"):
        rig.RE(rig.plan())
    assert_restored(rig)


def test_immediate_pause_aborts_nonrewindable_program(rig, monkeypatch):
    def pause(delay):
        yield from rig.p.bps.pause()
    monkeypatch.setattr(rig.p.bps, "sleep", pause)
    with pytest.raises(RunEngineInterrupted):
        rig.RE(rig.plan(baseline_image=False))
    assert rig.RE.state == "idle"
    assert_restored(rig)


def test_read_failure_after_trigger_cleans_up(rig, monkeypatch):
    def read():
        raise RuntimeError("current read failed")
    monkeypatch.setattr(rig.ps.current, "read", read)
    with pytest.raises(RuntimeError, match="current read failed"):
        rig.RE(rig.plan(baseline_image=False))
    assert_restored(rig)


def test_partial_configuration_failure_restores(rig, monkeypatch):
    original = rig.det.cam.num_images.set

    def set_value(value, **kwargs):
        if value == 1:
            raise RuntimeError("config failed")
        return original(value, **kwargs)

    monkeypatch.setattr(rig.det.cam.num_images, "set", set_value)
    with pytest.raises(RuntimeError, match="config failed"):
        rig.RE(rig.plan())
    assert_restored(rig)


def test_real_clock_short_program(rig, monkeypatch):
    import time

    from bluesky.utils import Msg

    monkeypatch.setattr(rig.p, "_monotonic", time.monotonic)

    def sleep(delay):
        yield Msg("sleep", None, delay)

    monkeypatch.setattr(rig.p.bps, "sleep", sleep)
    rig.RE(rig.plan(voltages=[0.8], hold_times=[0.6], frame_period=0.2,
                    exposure_time=0.01, max_lateness=0.15, baseline_image=False))
    data = [d["data"] for d in events(rig)]
    assert len(data) == 3
    assert [d["scheduled_elapsed_s"] for d in data] == pytest.approx([0, 0.2, 0.4])
    assert all(0 <= d["elapsed_s"] - d["scheduled_elapsed_s"] < 0.15 for d in data)
    assert_restored(rig)


def test_queue_surface():
    from smi_plans import _qserver as q
    from smi_plans.technique_P_power import sorensen_voltage_program_run
    assert q.P_sorensen_voltage_program_run is sorensen_voltage_program_run
    assert "P_sorensen_voltage_program_run" in q.qserver_plan_names()
    assert "P_build_sorensen_field_program" not in q.qserver_plan_names()


@pytest.mark.parametrize("holds,expected_holds", [(4, [4]*6), ([2, 4, 6], [2, 4, 6, 6, 4, 2])])
def test_thickness_program_commands_metadata_and_events(rig, holds, expected_holds):
    rig.RE(rig.plan(voltages=None, thickness_um=25, fields_MV_m=[0, 16, 20],
                    reverse=True, hold_times=holds))
    fields = [0, 16, 20, 20, 16, 0]
    targets = [0, 400, 500, 500, 400, 0]
    commands = [0, 0.4, 0.58, 0.58, 0.4, 0]
    start = next(d for n, d in rig.docs if n == "start")
    program = start["sorensen_program"]
    assert program["voltages"] == pytest.approx(commands)
    assert program["hold_times"] == expected_holds
    assert program["field_program"]["sample_voltages_V"] == targets
    assert program["field_program"]["fields_MV_m"] == fields
    assert program["field_program"]["calibration"]["name"] == "CMS Sample.setVoltages"
    data = [d["data"] for d in events(rig)]
    assert len(data) == 1 + sum(expected_holds)//2
    assert data[0]["target_field_MV_m"] == 0
    for d in data[1:]:
        i = d["program_step"]
        assert d["sample_thickness_um"] == 25
        assert d["target_sample_voltage_V"] == targets[i]
        assert d["target_field_MV_m"] == fields[i]
        assert d["bias_voltage_setpoint"] == pytest.approx(commands[i])
        assert d["sorensen_ps1_out_main_readback"] == pytest.approx(commands[i])
    assert_restored(rig)


def test_thickness_scaling_and_zero_policy():
    from smi_plans.technique_P_power import build_sorensen_field_program as build
    assert build(25, [0, 10, 20], reverse=False)["voltages"] == [0, 0.11, 0.58, 0]
    assert build(50, [0, 10, 20], reverse=False)["voltages"] == [0, 0.58, 1.53, 0]
    assert build(25, [20], reverse=True)["voltages"] == [0, 0]


@pytest.mark.parametrize("thickness", [1, 25, 50, 100])
@pytest.mark.parametrize("reverse", [False, True])
def test_exact_cms_default_command_parity(thickness, reverse):
    import numpy as np

    from smi_plans.technique_P_power import build_sorensen_field_program as build
    expected = []
    for i, v in enumerate(thickness * np.arange(0, 150.1, 10)):
        command = 0 if i == 0 else np.round((v - 190) / 530, 2)
        if command <= 11.5:
            expected.append(command)
    expected = expected + (expected[::-1] if reverse else [0])
    assert build(thickness, reverse=reverse)["voltages"] == expected


def test_filtered_steps_keep_correct_holds(rig):
    rig.RE(rig.plan(voltages=None, thickness_um=100, fields_MV_m=[0, 50, 100],
                    hold_times=[2, 4, 6], reverse=False))
    program = next(d for n, d in rig.docs if n == "start")["sorensen_program"]
    assert program["voltages"] == [0, 9.08, 0]  # 10000 V target excluded by CMS threshold
    assert program["hold_times"] == [2, 4, 4]
    assert len(events(rig)) == 6
    assert_restored(rig)


@pytest.mark.parametrize("override", [
    {"thickness_um": 0}, {"thickness_um": -25}, {"thickness_um": float("nan")},
    {"fields_MV_m": []}, {"fields_MV_m": [-1]}, {"fields_MV_m": [float("inf")]},
    {"voltages": [1]}, {"hold_times": [2, 2]},
])
def test_field_validation_before_any_hardware_message(rig, override):
    opts = {"voltages": None, "thickness_um": 25, "fields_MV_m": [20],
             "hold_times": 4}
    opts.update(override)
    with pytest.raises(ValueError):
        rig.RE(rig.plan(**opts))
    assert not rig.messages


def test_direct_scalar_hold_and_missing_arguments(rig):
    rig.RE(rig.plan(hold_times=4))
    assert len(events(rig)) == 5
    assert_restored(rig)
    rig.messages.clear()
    with pytest.raises(ValueError, match="hold_times is required"):
        rig.RE(rig.plan(hold_times=None))
    assert not rig.messages


def test_expected_output_uses_saved_input_readback(rig, monkeypatch):
    original = rig.ps.out_main_readback.read
    calls = []

    def read():
        reading = original()
        # Deliberately differ from the command; estimation must use the saved value.
        reading[rig.ps.out_main_readback.name]["value"] = 0.9
        calls.append(1)
        return reading

    monkeypatch.setattr(rig.ps.out_main_readback, "read", read)
    rig.RE(rig.plan(voltages=[0, 1], hold_times=4))
    data = [d["data"] for d in events(rig)]
    # Signal.describe() can call read() internally to infer dtype. Count actual RE
    # read messages to verify that the plan does not acquire an extra PV snapshot.
    assert sum(m.command == "read" and m.obj is rig.ps.out_main_readback
               for m in rig.messages) == len(data)
    assert [d["expected_output_voltage_V"] for d in data] == [0, 0, 0, 667, 667]
    assert all(d["sorensen_ps1_out_main_readback"] == 0.9 for d in data)
    estimate = next(d for n, d in rig.docs if n == "start")["sorensen_program"]["expected_output"]
    assert estimate["measured"] is False
    assert estimate["source"] == "sorensen_ps1_out_main_readback"


def test_progress_text_and_quiet_mode(rig, capsys):
    rig.RE(rig.plan())
    text = capsys.readouterr().out
    assert "Starting Sorensen program: 2 steps, 4 images, 8 s" in text
    assert "Baseline: output OFF" in text
    assert "Step 1/2: command 0.8 V" in text
    assert "Step 2/2: command 1 V" in text
    assert "expected output 614.00 V (CMS estimate)" in text
    assert "Cleanup: output commanded OFF" in text
    assert "Complete: 4 program images saved" in text
    rig.RE(rig.plan(verbose=False))
    assert capsys.readouterr().out == ""


def test_progress_failure_does_not_claim_completion(rig, capsys):
    rig.clock.acquisition = 3
    with pytest.raises(RuntimeError, match="cadence missed"):
        rig.RE(rig.plan(baseline_image=False))
    text = capsys.readouterr().out
    assert "Cleanup: output commanded OFF" in text
    assert "Complete:" not in text


@pytest.mark.parametrize("baseline_image", [True, False])
def test_summarize_thickness_program_without_hardware(rig, capsys, monkeypatch, baseline_image):
    from bluesky.simulators import summarize_plan

    # A slow terminal preview must not trip real acquisition deadlines.
    def clock():
        rig.clock.now += 10
        return rig.clock.now

    monkeypatch.setattr(rig.p, "_monotonic", clock)
    summarize_plan(rig.plan(voltages=None, thickness_um=25,
                           fields_MV_m=list(range(0, 101, 10)), reverse=True,
                           hold_times=2, frame_period=1, exposure_time=0.5,
                           baseline_image=baseline_image))
    text = capsys.readouterr().out
    assert "Preview step 22/22" in text
    assert "Preview complete: 44 scheduled program images" in text
    assert "expected_output_voltage_V" in text
    assert "measured input" not in text
    assert "pil2M_cam_num_images -> 0" not in text
    assert not rig.messages  # RE never ran
    assert not rig.docs
    assert_restored(rig)


def test_missing_real_readback_is_not_treated_as_preview(rig, monkeypatch):
    monkeypatch.setattr(rig.ps.out_main_readback, "read", lambda: {})
    with pytest.raises(KeyError, match="sorensen_ps1_out_main_readback"):
        rig.RE(rig.plan())
    assert_restored(rig)


@pytest.mark.parametrize("custom_baseline", [False, True])
def test_profile_supplemental_baseline_coexists(rig, custom_baseline):
    from bluesky.preprocessors import SupplementalData

    beamline = Signal(name="beamline_context", value=42)
    distance = rig.p.pil2M_pos.z
    sd = SupplementalData(baseline=[beamline, distance])
    rig.RE.preprocessors.append(sd)
    local = Signal(name="sample_context", value=25)
    rig.RE(rig.plan(baseline=[local] if custom_baseline else None))
    descriptors = {d["uid"]: d for n, d in rig.docs if n == "descriptor"}
    streams = {d["name"]: set(d["data_keys"]) for d in descriptors.values()}
    assert set(streams) == {"baseline", "sorensen_baseline", "primary"}
    assert "beamline_context" in streams["baseline"]
    assert "beamline_context" not in streams["sorensen_baseline"]
    assert ("sample_context" if custom_baseline else distance.name) in streams["sorensen_baseline"]
    for stream in ("baseline", "sorensen_baseline"):
        assert sum(n == "event" and descriptors[d["descriptor"]]["name"] == stream
                   for n, d in rig.docs) == 2
    assert len(events(rig)) == 5  # reference image stays in primary
    assert events(rig)[0]["data"]["bias_phase"] == "baseline"
    assert sum(n == "start" for n, _ in rig.docs) == 1
    assert_restored(rig)


def test_profile_power_supply_contract(rig):
    """Opt-in: exercise the actual profile class using fake EPICS, never live instances."""
    import importlib.util
    import os
    from pathlib import Path

    from ophyd.sim import make_fake_device

    source = os.environ.get("SMI_PROFILE_SRC")
    if not source:
        pytest.skip("Set SMI_PROFILE_SRC to the profile collection src directory")
    path = Path(source) / "smi_beamline" / "devices" / "power_supply.py"
    spec = importlib.util.spec_from_file_location("profile_power_supply_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ps = make_fake_device(module.PowerSupply)("SIM:", name="sorensen_ps1")
    ps.out_main_readback.sim_put(0.75)
    ps.current.sim_put(0.012)
    ps.out_main_status.sim_put(1)
    rig.RE(rig.plan(supply=ps, baseline_image=False, current_limit=0.2))
    data = [d["data"] for d in events(rig)]
    assert all(d["sorensen_ps1_out_main_readback"] == 0.75 for d in data)
    assert all(d["sorensen_ps1_current"] == 0.012 for d in data)
    assert all(d["sorensen_ps1_max_current"] == 0.2 for d in data)
    assert ps.out_main_setpoint.get() == 1.0
    assert ps.out_main_command.get() == 0
