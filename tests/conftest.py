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
    """SmarAct piezo fine stage: .x/.y/.z/.th."""
    x = Cpt(SynAxis, name="x")
    y = Cpt(SynAxis, name="y")
    z = Cpt(SynAxis, name="z")
    th = Cpt(SynAxis, name="th")


class _HuberStage(Device):
    """The Huber coarse ``stage`` (STG_pseudo) as exposed on the live beamline:
    lab-frame ``x/y/z`` + rotations ``theta/chi/phi``, with the back-compat property aliases
    ``.th``/``.ph``/``.ch`` the real ``STG_pseudo`` provides (so ``bps.mv(stage.th, …)`` and
    ``stage.phi`` both work).  ``phi`` is the rotation axis the old ``prs`` was repointed to.
    """
    x = Cpt(SynAxis, name="x")
    y = Cpt(SynAxis, name="y")
    z = Cpt(SynAxis, name="z")
    theta = Cpt(SynAxis, name="theta")
    chi = Cpt(SynAxis, name="chi")
    phi = Cpt(SynAxis, name="phi")

    @property
    def th(self):
        return self.theta

    @property
    def ph(self):
        return self.phi

    @property
    def ch(self):
        return self.chi


class _WaxsMotors(Device):
    """The WAXS detector's motion sub-device (the real ``pil900KW.motors``).

    On the beamline ``waxs = pil900KW.motors`` and ``pil900KW.motors.kind = 'normal'`` -- so the
    detector ``pil900KW`` records ``.motors``' keys (``waxs_arc``/``waxs_bsx``/``waxs_bsy``), and
    ``waxs`` IS this same sub-device.  Reading BOTH ``pil900KW`` and ``waxs`` in one event would
    duplicate those keys (the collision ``_dedup_readables`` fixes).  The arc is moved via
    ``waxs.arc`` (``bps.mv(waxs.arc, angle)``), exactly as on the beamline.
    """
    arc = Cpt(SynAxis, name="waxs_arc")
    bs_x = Cpt(SynAxis, name="waxs_bsx")
    bs_y = Cpt(SynAxis, name="waxs_bsy")


class _WaxsDetector(Device):
    """Stand-in for the SMI ``pil900KW`` WAXS area detector.

    Has a readable image stat (``stats1.total``) AND a ``motors`` sub-device it records (kind
    normal), so it reports ``waxs_arc`` etc. -- the parent/child key overlap with ``waxs``.
    """
    stats = Cpt(SynSignal, func=lambda: 1.0, name="stats")
    motors = Cpt(_WaxsMotors, name="motors")


class _XBPM(Device):
    sumX = Cpt(SynSignal, func=lambda: 1000.0, name="sumX")
    sumY = Cpt(SynSignal, func=lambda: 1000.0, name="sumY")


class _Energy(SynAxis):
    """Simulated SMI Energy pseudo-positioner surface used by energy_axis.

    The real ``energy`` device exposes DCM pitch/roll feedback-disable signals.  The reliable
    energy-move plan toggles them around moves, so the sim needs harmless stand-ins.
    """
    pitch_feedback_disabled = Cpt(Signal, value="0", name="pitch_feedback_disabled")
    roll_feedback_disabled = Cpt(Signal, value="0", name="roll_feedback_disabled")


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
        self.stage = _HuberStage(name="stage")
        # NOTE: the legacy ``prs`` (precision rotation stage) was removed on the live beamline
        # and replaced by the Huber ``stage.phi`` axis.  The sim intentionally does NOT define a
        # ``prs`` global, so any plan still referencing it fails loudly (it should use stage.phi).
        self.energy = _Energy(name="energy")
        self.xbpm2 = _XBPM(name="xbpm2")
        self.xbpm3 = _XBPM(name="xbpm3")
        self.pin_diode = _PinDiode(name="pin_diode")
        self.pil2M = _AreaDet(name="pil2M")           # has .cam and .motor
        self.pil2M.motor = _DetMotor(name="pil2M_motor")
        # WAXS detector + its motion sub-device; ``waxs`` IS ``pil900KW.motors`` (beamline wiring
        # ``waxs = pil900KW.motors``).  ``motors`` is recorded by the detector (kind normal), so
        # reading both pil900KW and waxs in one event collides on waxs_arc/_bsx/_bsy unless
        # de-duplicated -- the case smi_plans._compose._dedup_readables handles.
        self.pil900KW = _WaxsDetector(name="pil900KW")
        self.pil900KW.motors.kind = "normal"
        # NB: on the beamline the arc/bs readbacks are renamed to waxs_arc/waxs_bsx/waxs_bsy
        # (pilatus.py:116-118) so {waxs_arc} resolves; the sim keeps the default Cpt keys
        # (pil900KW_motors_arc, ...) -- the token *name* is a beamline detail, what the sim
        # exercises is the parent/child key OVERLAP (pil900KW records the same keys as waxs).
        self.waxs = self.pil900KW.motors           # the arc is moved via ``waxs.arc``
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
        # The arc is moved via ``waxs.arc`` (= pil900KW.motors.arc), as on the beamline.
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

    def readHumidity(self, verbosity=0, **kwargs):
        return 45.0

    # -- the dict of globals to inject into smi_plans modules ----------------
    def globals_dict(self):
        return {
            "np": self.np, "bps": self.bps, "bpp": self.bpp, "bp": self.bp,
            "Signal": self.Signal,
            "piezo": self.piezo, "stage": self.stage, "waxs": self.waxs,
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

    # -- run a plan through a RunEngine and collect documents --------------------
    # Plans are now message-pure (they use bps.rd / bps.mv, NOT .get()/.put()).  A bare
    # ``list(plan)`` therefore can't answer ``rd``/``read`` messages (it returns the default),
    # so we drive plans with a real RunEngine and assert on the emitted DOCUMENTS.  Sleeps are
    # made instant so the equilibration / settle waits don't slow tests.
    def run(self, plan):
        """Execute ``plan`` on a RunEngine against the sim devices; return a RunResult.

        Patches ``bluesky.plan_stubs.sleep`` (and ``asyncio.sleep`` via the RE) to be instant
        so settle/equilibration waits don't block tests, and answers manual ``input`` prompts
        with an empty string.
        """
        from bluesky import RunEngine
        import bluesky.plan_stubs as _bps
        from unittest import mock

        docs = []
        RE = RunEngine({})
        RE.subscribe(lambda name, doc: docs.append((name, doc)))
        # answer manual_step / manual_axis / manual_loop prompts non-interactively
        RE.register_command  # (no-op touch; input is handled below)
        RE.input_hook = None
        try:
            RE._input = lambda prompt='': ""    # some bluesky versions
        except Exception:
            pass

        # make sleeps instant: replace bps.sleep with a no-op message (bps.null)
        def _instant_sleep(t):
            yield from _bps.null()

        # provide input() answers for the 'input' message handler
        import builtins
        with mock.patch.object(_bps, "sleep", _instant_sleep), \
                mock.patch.object(builtins, "input", lambda prompt="": ""):
            RE(plan)
        return RunResult(docs)

    # keep ``messages`` as an alias that returns a RunResult (back-compat with existing tests)
    def messages(self, plan):
        return self.run(plan)

    @staticmethod
    def messages_only(plan):
        """Return the raw ``Msg`` list (``list(plan)``) -- for tests that only count message
        *commands* (e.g. how many ``input`` prompts).  Do NOT use to assert event counts on
        message-pure plans: ``list()`` cannot answer ``rd``/``read`` (use ``run``/``messages``).
        """
        return list(plan)

    # -- document-stream assertions (operate on a RunResult) --------------------
    @staticmethod
    def run_count(result):
        return result.run_count()

    @staticmethod
    def events_by_stream(result):
        return result.events_by_stream()

    @classmethod
    def primary_events(cls, result):
        return result.events_by_stream().get("primary", 0)

    @classmethod
    def assert_one_run(cls, result):
        """Assert the plan produced exactly one run with balanced start/stop + recorded events."""
        o, c = result.run_count()
        assert o == c, "run_start ({}) != run_stop ({})".format(o, c)
        assert o == 1, "expected exactly one run, got {}".format(o)


class RunResult(object):
    """Documents emitted by running a plan, with convenience counts.

    Holds the (name, doc) tuples a RunEngine published.  This is a more faithful thing to
    assert on than a raw message list: it reflects what was actually recorded.
    """

    def __init__(self, docs):
        self.docs = docs

    def names(self):
        return [n for n, _ in self.docs]

    def run_count(self):
        ns = self.names()
        return ns.count("start"), ns.count("stop")

    def events_by_stream(self):
        # map descriptor uid -> stream name, then count events per stream
        stream = {}
        for name, doc in self.docs:
            if name == "descriptor":
                stream[doc["uid"]] = doc.get("name", "primary")
        counts = Counter()
        for name, doc in self.docs:
            if name == "event":
                counts[stream.get(doc["descriptor"], "primary")] += 1
        return dict(counts)

    def input_count(self):
        # number of manual prompts is not in documents; tests that need it use the messages path
        return None


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
