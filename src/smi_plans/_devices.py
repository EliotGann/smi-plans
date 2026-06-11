"""
smi_plans._devices
=================

**Proper ophyd device wrappers** for hardware that the legacy SMI code reads/sets through
non-message paths (a plain ``some_obj.value()`` call, a ``.put()``, a module-level function).

Why this module exists
----------------------
A Bluesky **plan must contain only messages** -- ``yield from bps.mv(...)`` /
``bps.rd(...)`` / ``bps.trigger_and_read(...)``.  It must never call ``device.put()`` /
``device.get()`` / ``device.set()`` directly, and never a bare function like
``readHumidity()`` mid-plan.  Where the legacy code does, the **first-line fix is to make the
hardware a proper ophyd device** that the RunEngine can drive via messages -- not to paper over
it inside the plan.

This module provides those wrappers (as far as they can be written without the live PV names)
plus a clear contract for wiring them to the real EPICS PVs.  Until wired, they are documented
**device debt** (see ``docs/DEVICE_DEBT.md``): the plans route through these objects so the plan
code stays message-pure, and the remaining work is purely on the ophyd/PV side.

Two kinds of wrapper here
-------------------------
1. ``FunctionBackedSignal`` -- a read-only ``Signal`` whose value comes from a Python callable
   (e.g. ``readHumidity``).  Lets a plan do ``yield from bps.rd(humidity)`` instead of calling
   the function directly.  **Interim only**: the real fix is an EpicsSignalRO on the humidity
   PV.  Use it to get plans message-pure today; replace with the EPICS-backed signal when the
   PV is known.
2. Thin ``EpicsSignalRO``-based stubs (commented templates) for the specific debts -- humidity,
   Linkam live temperature -- showing exactly what to instantiate once the PV is known.

.. important::
    Like the rest of the package, instantiate these in the live beamline environment.  The
    EPICS-backed templates require ``ophyd`` + the real PV strings (filled in by beamline
    staff).  ``FunctionBackedSignal`` works anywhere ``ophyd`` is importable.
"""

try:
    from ophyd import Signal
    try:
        from ophyd import EpicsSignalRO  # noqa: F401  (used by the templates below)
    except Exception:  # pragma: no cover
        EpicsSignalRO = None
except Exception:  # pragma: no cover - outside the beamline env
    Signal = object
    EpicsSignalRO = None


__all__ = ["FunctionBackedSignal", "humidity_signal", "linkam_temperature_signal"]


class FunctionBackedSignal(Signal):
    """A read-only ophyd ``Signal`` whose value is produced by a Python callable.

    This is the bridge that lets a plan read a *function-backed* quantity through a message
    (``yield from bps.rd(sig)``) instead of calling the function inline (which is forbidden in a
    plan).  It is an **interim** device: the proper fix is an ``EpicsSignalRO`` on the real PV.

    Parameters
    ----------
    func : callable() -> value
        Returns the current reading (e.g. ``lambda: readHumidity(verbosity=0)``).
    name : str
        Ophyd name / data key (e.g. ``"humidity"`` -> filename token ``{humidity}``).

    Example
    -------
    >>> humidity = FunctionBackedSignal(func=lambda: readHumidity(verbosity=0), name="humidity")
    >>> # in a plan:
    >>> rh = yield from bps.rd(humidity)             # message-based read
    >>> yield from bps.trigger_and_read(dets + [humidity])   # recorded -> {humidity} token
    """

    def __init__(self, *, func, name, **kwargs):
        super().__init__(name=name, **kwargs)
        self._func = func

    def get(self, **kwargs):
        # ophyd calls .get() when the RunEngine processes a 'read'/'rd' message; we delegate to
        # the backing function so the value is fresh.  (The forbidden thing is a *plan* calling
        # .get() directly; here the RunEngine calls it as part of handling a message.)
        value = self._func()
        # keep ophyd's cached value coherent for read()/describe()
        self._readback = value
        return value


def humidity_signal(read_humidity_func, *, name="humidity"):
    """Wrap the SMI ``readHumidity`` function as a readable Signal (INTERIM -- see DEVICE_DEBT).

    Replace with an ``EpicsSignalRO("<RH PV>", name="humidity")`` once the humidity PV is known::

        # TARGET (fill in PV):
        # humidity = EpicsSignalRO("XF:11ID-ES{...}RH", name="humidity")
    """
    return FunctionBackedSignal(func=lambda: float(read_humidity_func(verbosity=0)), name=name)


def linkam_temperature_signal(linkam_obj, *, name="linkam_temperature"):
    """Wrap ``LThermal.temperature()`` as a readable Signal (INTERIM -- see DEVICE_DEBT).

    The Linkam controller's live temperature is exposed (in the legacy code) only via the
    plain method ``LThermal.temperature()``.  This wraps it so plans can ``bps.rd`` it.

    The proper fix is to make the Linkam ophyd ``Device`` expose its temperature as an
    ``EpicsSignalRO`` component (e.g. ``LThermal.temperature_current``) so the readback is a
    first-class signal and no wrapper is needed::

        # TARGET: use the real component once it exists / is reliable:
        # heater_readback = LThermal.temperature_current
    """
    return FunctionBackedSignal(func=lambda: float(linkam_obj.temperature()), name=name)
