"""
profile_pvs -- enumerate the PVs the SMI bluesky profile models, with **no EPICS connections**.

How (and why it never touches CA)
---------------------------------
The real ``EpicsSignal``/``EpicsMotor`` control layer would broadcast CA searches the moment a
signal is created.  So we never instantiate it:

1. **Static AST parse** of the profile's instance modules (``startup/smibase/*.py``) to collect,
   without executing them, every ``var = DeviceClass("PREFIX", name=...)`` and every
   ``_context.baseline_register([...])`` membership.
2. For each instance we import only the *class* (the ``smi_beamline.devices.*`` class modules are
   pure definitions -- importing them creates no devices and opens no connections), wrap it with
   :func:`ophyd.sim.make_fake_device` (whose signals never connect), and instantiate the **fake**.
3. PV names are then composed from the device tree using ophyd's own
   :meth:`Component.maybe_add_prefix` on the fake instance -- i.e. exactly the prefix/suffix math
   ophyd would do, but with zero channel access.

Soft components (``PseudoSingle``/``AttributeSignal``/plain ``Signal``) correctly yield no PV and
are skipped.  Instances with a non-literal (computed) prefix, or a class defined inline in an
instance module (which we refuse to import, to avoid its side effects), are reported as skipped.
"""

from __future__ import annotations

import ast
import glob
import importlib
import os
import warnings
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProfilePV:
    record: str            # PV record (pvname with any trailing .FIELD stripped)
    pvname: str            # full composed PV (read pv)
    device: str            # instance variable name (e.g. "stage")
    dotted: str            # dotted signal path within the device
    classname: str         # leaf signal class (EpicsSignal / EpicsSignalRO / ...)
    read_only: bool
    in_baseline: bool
    source_file: str = ""  # smibase module the instance came from (subsystem grouping)


@dataclass
class _Instance:
    var: str
    cls_name: str
    module: Optional[str]
    prefix: Optional[str]      # None == not a string literal (skip)
    name: str
    source_file: str


@dataclass
class ProfileResult:
    pvs: list = field(default_factory=list)
    instances: list = field(default_factory=list)
    baseline_vars: set = field(default_factory=set)
    skipped: list = field(default_factory=list)   # (var, reason)


# --------------------------------------------------------------------------- AST parsing
_OPHYD_FAKEABLE_HINT = ("Epics", "Motor", "Device", "Positioner", "Stage", "Signal")


def _literal(node):
    """Return a str/num literal value for ``node`` or ``None`` if not a simple literal."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def parse_instance_modules(smibase_dir):
    """AST-parse every ``smibase/*.py`` for device instances + baseline registrations.

    Returns ``(instances, baseline_vars, import_map_per_file)`` without executing any module.
    """
    instances = []
    baseline_vars = set()

    for path in sorted(glob.glob(os.path.join(smibase_dir, "*.py"))):
        fname = os.path.basename(path)
        if fname in ("base.py", "base_dev.py"):
            continue  # bootstrap, not device instances
        try:
            tree = ast.parse(open(path).read(), filename=path)
        except Exception:
            continue

        # name -> module from "from MODULE import A, B as C"
        import_map = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    import_map[alias.asname or alias.name] = node.module

        # top-level assignments + baseline_register calls
        for node in tree.body:
            # baseline_register([...]) possibly as _context.baseline_register or bare
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                _collect_baseline(node.value, baseline_vars)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                func = call.func
                cls_name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None)
                if not cls_name:
                    continue
                # targets: var = Class(...)
                for tgt in node.targets:
                    if not isinstance(tgt, ast.Name):
                        continue
                    var = tgt.id
                    prefix = None
                    if call.args:
                        prefix = _literal(call.args[0])
                    # prefix can also be passed as prefix=
                    name = var
                    for kw in call.keywords:
                        if kw.arg == "prefix" and prefix is None:
                            prefix = _literal(kw.value)
                        if kw.arg == "name":
                            nm = _literal(kw.value)
                            if isinstance(nm, str):
                                name = nm
                    instances.append(_Instance(
                        var=var, cls_name=cls_name, module=import_map.get(cls_name),
                        prefix=prefix if isinstance(prefix, str) else None,
                        name=name, source_file=fname,
                    ))
    return instances, baseline_vars


def _collect_baseline(call, baseline_vars):
    func = call.func
    is_baseline = (
        (isinstance(func, ast.Attribute) and func.attr == "baseline_register") or
        (isinstance(func, ast.Name) and func.id == "baseline_register")
    )
    if not is_baseline or not call.args:
        return
    arg = call.args[0]
    if isinstance(arg, (ast.List, ast.Tuple)):
        for el in arg.elts:
            if isinstance(el, ast.Name):
                baseline_vars.add(el.id)
            elif isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name):
                baseline_vars.add(el.value.id)   # e.g. pil2M.active_beamstop -> device pil2M


# --------------------------------------------------------------------------- fake instantiation
def _is_epics_leaf(obj):
    return type(obj).__name__ in (
        "FakeEpicsSignal", "FakeEpicsSignalRO", "FakeEpicsSignalWithRBV",
        "EpicsSignal", "EpicsSignalRO", "EpicsSignalWithRBV",
    )


def _read_only(obj):
    return "RO" in type(obj).__name__


import re as _re

_MISSING_KW_RE = _re.compile(r"missing \d+ required keyword-only argument[s]?: (.+)")


def _dummy_for(kw):
    low = kw.lower()
    if "path" in low or "root" in low or "template" in low or "dir" in low:
        return "/tmp/"
    if kw in ("banks",) or low.endswith("s"):
        return []
    return ""


def _instantiate_with_dummies(fake_cls, prefix, name, max_tries=8):
    """Instantiate a fake device, filling in any required keyword-only args with harmless dummies.

    Area detectors / attenuator sets require kwargs like ``asset_path``/``banks`` that have no
    bearing on the (CA-free) PV-name composition; we supply throwaway values so we can still walk
    their signal trees.
    """
    extra = {}
    last = None
    for _ in range(max_tries):
        try:
            return fake_cls(prefix, name=name, **extra)
        except TypeError as exc:
            m = _MISSING_KW_RE.search(str(exc))
            if not m:
                raise
            # parse the quoted kwarg names, e.g. "'asset_path'" or "'a' and 'b'"
            names = _re.findall(r"'([^']+)'", m.group(1))
            if not names:
                raise
            for nm in names:
                extra.setdefault(nm, _dummy_for(nm))
            last = exc
    raise last if last else RuntimeError("could not instantiate")


def _compose(dev, out, dotted="", _seen=None):
    """Recursively compose PV names from a (fake) device tree using ophyd's own prefix math."""
    if _seen is None:
        _seen = set()
    if id(dev) in _seen:
        return
    _seen.add(id(dev))
    cls = type(dev)
    for cname in getattr(dev, "component_names", ()):
        try:
            cpt = getattr(cls, cname)          # the Component descriptor
            child = getattr(dev, cname)
        except Exception:
            continue
        full = (dotted + "." + cname).lstrip(".")
        pv = None
        suffix = getattr(cpt, "suffix", None)
        if suffix is not None:
            try:
                pv = cpt.maybe_add_prefix(dev, "suffix", suffix)
            except Exception:
                pv = None
        if getattr(child, "component_names", None):
            _compose(child, out, full, _seen)
        elif pv and _is_epics_leaf(child):
            out.append((full, pv, type(child).__name__, _read_only(child)))


def enumerate_profile_pvs(profile_root):
    """Build the full :class:`ProfileResult` for the profile at ``profile_root``.

    ``profile_root`` is the ``profile_collection`` checkout (must have ``startup/smibase`` and
    ``src`` importable on ``sys.path``).
    """
    from ophyd.sim import make_fake_device

    smibase_dir = os.path.join(profile_root, "startup", "smibase")
    instances, baseline_vars = parse_instance_modules(smibase_dir)
    result = ProfileResult(instances=instances, baseline_vars=baseline_vars)

    _class_cache = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for inst in instances:
            if inst.prefix is None:
                result.skipped.append((inst.var, "non-literal/absent prefix"))
                continue
            if not inst.module:
                result.skipped.append((inst.var, f"unknown module for class {inst.cls_name}"))
                continue
            # refuse to import instance modules that define their class inline (side effects)
            if inst.module.startswith("smibase") and not inst.module.startswith("smibase.") :
                result.skipped.append((inst.var, f"inline class in {inst.module}"))
                continue
            key = (inst.module, inst.cls_name)
            if key not in _class_cache:
                try:
                    mod = importlib.import_module(inst.module)
                    cls = getattr(mod, inst.cls_name)
                    _class_cache[key] = make_fake_device(cls)
                except Exception as exc:  # noqa: BLE001
                    _class_cache[key] = exc
            fake_cls = _class_cache[key]
            if isinstance(fake_cls, Exception):
                result.skipped.append((inst.var, f"import/fake failed: {fake_cls}"))
                continue
            try:
                dev = _instantiate_with_dummies(fake_cls, inst.prefix, inst.name)
            except Exception as exc:  # noqa: BLE001
                result.skipped.append((inst.var, f"instantiate failed: {exc}"))
                continue
            rows = []
            _compose(dev, rows)
            in_base = inst.var in baseline_vars
            for dotted, pv, clsname, ro in rows:
                record = pv.split(".")[0]
                result.pvs.append(ProfilePV(
                    record=record, pvname=pv, device=inst.var, dotted=dotted,
                    classname=clsname.replace("Fake", ""), read_only=ro, in_baseline=in_base,
                    source_file=inst.source_file[:-3] if inst.source_file.endswith(".py")
                    else inst.source_file,
                ))
    return result
