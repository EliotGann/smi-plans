"""
smi_plans.audit
===============

Tooling for the SMI device-completeness audit: reconcile the PVs shown on the CS-Studio /
Phoebus operator screens (``cs-studio-xf/12id``) against the PVs modelled by the bluesky profile
(``smi_beamline`` devices) and the subset captured in the per-scan **baseline**.

Status: complete but unpruned first-pass audit. The generated results are retained for later
logging/baseline work but are not yet used by acquisition code.

Hard rule for this audit: **NO EPICS CONNECTIONS.**  The profile side is enumerated with
``ophyd.sim`` *fake* devices (which never touch channel access) and by static AST parsing -- the
real EpicsSignal/EpicsMotor control layer is never instantiated.

Modules
-------
* :mod:`css_resolver` -- recursive Phoebus/BOY macro+include resolver: screens -> concrete PVs.
* :mod:`profile_pvs`  -- enumerate the profile's PVs via fake devices (no CA).
* :mod:`baseline`     -- which devices the profile registers into ``sd.baseline`` (AST parse).
* :mod:`reconcile`    -- normalise + categorise CSS vs profile vs baseline.
* :mod:`report`       -- write CSV / HTML / Markdown artifacts.
* ``__main__``        -- CLI tying it together.
"""
