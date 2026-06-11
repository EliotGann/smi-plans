"""Guard test: plan code is message-pure -- no bare device ``.put()``/``.get()``/``.set()``.

A Bluesky plan must contain ONLY messages.  Direct ``device.put(...)`` / ``.get()`` / ``.set()``
calls inside a plan break the message model (and the RunEngine / queueserver).  This test scans
the package source and fails if any such call appears OUTSIDE a string/comment.

Allowed exceptions:
- ``_devices.py``: ``FunctionBackedSignal.get()`` is an ophyd *device method* the RunEngine
  calls when handling a read message -- that's the whole point of the wrapper, not a plan
  calling ``.get()``.
- Dict ``.get(...)`` (``md.get``, ``kwargs.get``, ``ctx.get`` ...) is plain Python, not a device.
"""
import ast
import glob
import os

import pytest


PKG = os.path.join(os.path.dirname(__file__), "..", "src", "smi_plans")

# files where a .get() is a legitimate ophyd device method (the RunEngine calls it), not a
# plan calling .get() directly.
_ALLOW_GET_FILES = {"_devices.py"}

# attribute names that are plain-Python dict/obj .get(), never a device read.
_DICT_GET_OWNERS = {"md", "kwargs", "ctx", "context", "spec", "s", "d", "state", "t0",
                    "params", "cfg", "config", "self", "_re_results", "globals"}


def _device_calls(path):
    """Return [(lineno, code)] of bare .put()/.set()/.get() that look like device calls."""
    with open(path) as fh:
        src = fh.read()
    tree = ast.parse(src)
    lines = src.splitlines()
    hits = []
    fname = os.path.basename(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        attr = func.attr
        if attr not in ("put", "set"):
            if not (attr == "get" and fname not in _ALLOW_GET_FILES):
                continue
        # owner of the call, e.g. `foo.bar.put` -> owner chain ends in some Name
        owner = func.value
        # skip plain dict/obj .get()
        if attr == "get":
            base = owner
            # get the left-most Name in the attribute chain
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in _DICT_GET_OWNERS:
                continue
            # x.get("string-key", ...) is almost always a dict; device reads are .get() no-arg
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
                    node.args[0].value, str):
                continue
        hits.append((node.lineno, lines[node.lineno - 1].strip()))
    return hits


def test_no_bare_put_get_set_in_plan_source():
    offenders = {}
    for path in glob.glob(os.path.join(PKG, "*.py")):
        hits = _device_calls(path)
        if hits:
            offenders[os.path.basename(path)] = hits
    assert not offenders, (
        "Bare device .put()/.get()/.set() found in plan source (use yield from bps.mv / "
        "bps.rd instead; fix the ophyd device if a message can't reach it -- see "
        "docs/DEVICE_DEBT.md):\n"
        + "\n".join("  {}:{}  {}".format(f, ln, code)
                    for f, hits in offenders.items() for ln, code in hits))
