"""
Shared pytest fixtures: a complete set of **simulated SMI beamline devices** injected as the
globals that ``smi_plans`` modules expect at runtime.

Why this exists
---------------
The ``smi_plans`` plan files reference beamline globals (``piezo``, ``waxs``, ``energy``,
``pil2M``, ``bps`` ...) that the SMI profile collection injects at runtime.  Off-beamline we
supply *simulated* stand-ins (``ophyd.sim``) and inject them into each module's namespace, then
run a plan by listing the messages it yields and asserting on the run structure -- **no hardware
and no RunEngine required**.

This is the harness that makes GUI / plan development fast: every generated or hand-written plan
can be "dry-run" to verify it produces exactly one well-formed run with the expected events.

Use in a test:
    def test_my_plan(sim, inject):
        mod = inject("smi_plans.technique_A_energy_edge")
        msgs = list(mod.nexafs_run("S1", [2470, 2475], t=0.1, updown=False))
        assert sim.primary_events(msgs) == 2
        assert sim.run_count(msgs) == (1, 1)
"""
import importlib

import pytest

pytest.importorskip("bluesky")
pytest.importorskip("ophyd")

import numpy as np
import bluesky.plan_stubs as bps
import bluesky.preprocessors as bpp
import bluesky.plans as bp
from ophyd import Signal, Device, Component as Cpt
from ophyd.sim import SynAxis, SynSignal, motor, Syn2DGauss
from collections import Counter


# ---------------------------------------------------------------------------
# Simulated device classes (mirror the real SMI device API surface used by smi_plans)
# ---------------------------------------------------------------------------
class _Stack(Device):
    """SmarAct piezo OR hexapod stage: .x/.y/.z/.th."""
    x = Cpt(SynAxis, name="x")
    y = Cpt(SynAxis, name="y")
    z = Cpt(SynAxis, name="z")
    th = Cpt(SynAxis, name="th")


class _Waxs(Device):
    arc = Cpt(SynAxis, name="arc")
    bs_y = Cpt(SynAxis, name="bs_y")


class _XBPM(Device):
    sumX = Cpt(SynSignal, func=lambda: 1000.0, name="sumX")
    sumY = Cpt(SynSignal, func=lambda: 1000.0, name="sumY")


class _PinDiode(Device):
    current2 = Cpt(SynSignal, func=lambda: 0.5, name="current2")
    averaging_time = Cpt(Signal, value=1.0, name="averaging_time")


class _SDDpos(Device):
    z = Cpt(SynAxis, name="z")


class _Cam(Device):
    num_images = Cpt(Signal, value=1, name="num_images")
    acquire = Cpt(Signal, value=0, name="acquire")
    acquire_time = Cpt(Signal, value=1.0, name="acquire_time")


class _AreaDet(Device):
    """Area detector w/ .cam (for XPCS burst config) and a stats readable."""
    cam = Cpt(_Cam, name="cam")
    stats = Cpt(SynSignal, func=lambda: 1.0, name="stats")


class _DetMotor(Device):
    """pil2M.motor.{x,y,z} detector-translation axes used by the commissioning preset."""
    x = Cpt(SynAxis, name="x")
    y = Cpt(SynAxis, name="y")
    z = Cpt(SynAxis, name="z")


class _Lakeshore(Device):
    input_A = Cpt(SynSignal, func=lambda: 300.0, name="input_A")
    input_A_celsius = Cpt(SynSignal, func=lambda: 27.0, name="input_A_celsius")
    ch1_read = Cpt(SynSignal, func=lambda: 27.0, name="ch1_read")
    ch1_sp = Cpt(Signal, value=300.0, name="ch1_sp")

    class _Out:
        def mv_temp(self, T):
            yield from bps.null()
    output1 = _Out()


class _Linkam(Device):
    """Linkam wrapper; setTemperature is a plain method, temperature() returns the last set
    value so the equilibration loop converges instantly in tests."""
    temperature_current = Cpt(SynSignal, func=lambda: 27.0, name="temperature_current")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sp = 27.0

    def setTemperature(self, T):
        self._sp = float(T)

    def on(self):
        pass

    def temperature(self):
        return self._sp


class _Att:
    def __init__(self, name):
        self.close_cmd = SynSignal(func=lambda: 0, name=name + "_close")
        self.open_cmd = SynSignal(func=lambda: 0, name=name + "_open")


class SimBeamline:
    """Container of simulated devices + helpers, exposed as the ``sim`` fixture."""

    def __init__(self):
        self.np = np
        self.bps = bps
        self.bpp = bpp
        self.bp = bp
        self.Signal = Signal

        self.piezo = _Stack(name="piezo")
        self.stage = _Stack(name="stage")
        self.waxs = _Waxs(name="waxs")
        self.prs = SynAxis(name="prs")
        self.energy = SynAxis(name="energy")
        self.xbpm2 = _XBPM(name="xbpm2")
        self.xbpm3 = _XBPM(name="xbpm3")
        self.pin_diode = _PinDiode(name="pin_diode")
        self.pil2M = _AreaDet(name="pil2M")           # has .cam and .motor
        self.pil2M.motor = _DetMotor(name="pil2M_motor")
        self.pil900KW = Syn2DGauss("pil900KW", motor, "motor", motor, "motor",
                                   center=0, Imax=1)
        self.amptek = Syn2DGauss("amptek", motor, "motor", motor, "motor", center=0, Imax=1)
        self.rayonix = Syn2DGauss("rayonix", motor, "motor", motor, "motor", center=0, Imax=1)
        self.pil2M_pos = _SDDpos(name="pil2M_pos")
        self.ls = _Lakeshore(name="ls")
        self.LThermal = _Linkam(name="LThermal")
        self.OAV_writing = Syn2DGauss("OAV_writing", motor, "motor", motor, "motor",
                                      center=0, Imax=1)
        self.syringe_pu = SynAxis(name="syringe_pu")
        self.thorlabs_su = SynAxis(name="thorlabs_su")
        self.att2_9 = _Att("att2_9")
        self.att2_10 = _Att("att2_10")
        self.att2_11 = _Att("att2_11")
        self.att2_12 = _Att("att2_12")

        # Keep the WAXS arc up by default so saxs_waxs_dets() keeps pil2M (SAXS) in the list.
        self.waxs.arc.set(20).wait()

    # -- callable globals smi_plans expects ---------------------------------
    def det_exposure_time(self, a, b=None):
        yield from bps.null()

    def alignement_gisaxs_hex(self, angle=0.1):
        yield from bps.mv(self.piezo.th, angle)

    # the doblestack alignment is the same shape for tests
    alignement_gisaxs_doblestack = alignement_gisaxs_hex

    def setDryFlow(self, v):
        yield from bps.null()

    def setWetFlow(self, v):
        yield from bps.null()

    def set_humidity(self, v):
        yield from bps.null()

    def readHumidity(self):
        return 45.0

    # -- the dict of globals to inject into smi_plans modules ----------------
    def globals_dict(self):
        return {
            "np": self.np, "bps": self.bps, "bpp": self.bpp, "bp": self.bp,
            "Signal": self.Signal,
            "piezo": self.piezo, "stage": self.stage, "waxs": self.waxs, "prs": self.prs,
            "energy": self.energy, "xbpm2": self.xbpm2, "xbpm3": self.xbpm3,
            "pin_diode": self.pin_diode, "pil2M": self.pil2M, "pil900KW": self.pil900KW,
            "amptek": self.amptek, "rayonix": self.rayonix, "pil2M_pos": self.pil2M_pos,
            "ls": self.ls, "LThermal": self.LThermal, "OAV_writing": self.OAV_writing,
            "syringe_pu": self.syringe_pu, "thorlabs_su": self.thorlabs_su,
            "att2_9": self.att2_9, "att2_10": self.att2_10, "att2_11": self.att2_11,
            "att2_12": self.att2_12,
            "det_exposure_time": self.det_exposure_time,
            "alignement_gisaxs_hex": self.alignement_gisaxs_hex,
            "alignement_gisaxs_doblestack": self.alignement_gisaxs_hex,
            "setDryFlow": self.setDryFlow, "setWetFlow": self.setWetFlow,
            "set_humidity": self.set_humidity, "readHumidity": self.readHumidity,
        }

    # -- message-stream assertions ------------------------------------------
    @staticmethod
    def messages(plan):
        """Drive a plan to exhaustion (no RunEngine) and return its Msg list."""
        return list(plan)

    @staticmethod
    def run_count(msgs):
        cmds = [m.command for m in msgs]
        return cmds.count("open_run"), cmds.count("close_run")

    @staticmethod
    def events_by_stream(msgs):
        return dict(Counter(m.kwargs.get("name", "primary")
                            for m in msgs if m.command == "create"))

    @classmethod
    def primary_events(cls, msgs):
        return cls.events_by_stream(msgs).get("primary", 0)

    @classmethod
    def assert_one_run(cls, msgs):
        """Assert the plan produced exactly one balanced run with balanced events."""
        o, c = cls.run_count(msgs)
        assert o == c, "open_run ({}) != close_run ({})".format(o, c)
        assert o == 1, "expected exactly one run, got {}".format(o)
        cmds = [m.command for m in msgs]
        assert cmds.count("create") == cmds.count("save"), "create/save unbalanced"


@pytest.fixture
def sim():
    """A fresh simulated beamline (devices + helpers) for each test."""
    return SimBeamline()


@pytest.fixture
def inject(sim):
    """Return a function that injects the sim globals into a smi_plans module and returns it.

    Injects the sim globals into EVERY already-imported ``smi_plans.*`` module (and the
    requested one), so cross-module references resolve -- e.g. ``recipes_combined`` importing
    ``linkam_heater`` from ``technique_C_temperature`` runs that helper in a namespace that also
    has the sim globals.
    """
    import sys
    g = sim.globals_dict()

    def _inject_into(mod):
        for k, v in g.items():
            setattr(mod, k, v)

    def _inject(modname):
        # make sure the target (and its dependencies) are imported
        importlib.import_module(modname)
        # inject into the shared device-dependent core modules first
        for base in ("smi_plans._core", "smi_plans._compose", "smi_plans._preprocessors"):
            _inject_into(importlib.import_module(base))
        # then into every loaded smi_plans.* module (techniques, recipes, the target)
        for name, mod in list(sys.modules.items()):
            if name.startswith("smi_plans") and mod is not None:
                try:
                    _inject_into(mod)
                except Exception:
                    pass
        return importlib.import_module(modname)

    return _inject
