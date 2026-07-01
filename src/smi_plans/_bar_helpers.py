"""
Pure helper utilities promoted from field-run bar scripts.

These functions are intentionally not plans.  They operate on the holder/sample store or build
filename templates, so they can be used from the live console, tests, or a future GUI without
requiring beamline devices at import time.
"""

import re as _re

from ._core import fname
from ._holder import load_holder
from ._samples import Position


__all__ = [
    "POSITION_AXES",
    "bar_name_tokens",
    "apply_name_prefix",
    "preview_bar_name",
    "preview_name",
    "adjust_holder_positions",
    "adjust_bar_positions",
    "sort_holder_by_name",
    "sort_bar_by_name",
]


POSITION_AXES = (
    "piezo_x", "piezo_y", "piezo_z", "piezo_th",
    "stage_x", "stage_y", "stage_z", "stage_theta", "stage_chi", "stage_phi",
)


def bar_name_tokens(arc_value, *, include_energy=True, energy_token="{energy_energy}eV",
                    include_exposure=False, exposure_token="exp_{exposure_s}s",
                    include_arc=True, arc_fmt="wa{:04.1f}", include_incidence=False,
                    incidence_token="ai{incident_angle}", grid=False, extra_tokens=None,
                    name_prefix=None):
    """Return filename tokens for common bar runs.

    ``name_prefix`` is accepted for convenience with user-facing ``name_spec`` dictionaries, but is
    deliberately not included here.  Use :func:`apply_name_prefix` on the base sample name instead so
    the prefix appears before the sample name.
    """
    toks = []
    if include_energy:
        toks.append(energy_token)
    if include_exposure:
        toks.append(exposure_token)
    if include_incidence:
        toks.append(incidence_token)
    if include_arc:
        toks.append(arc_fmt.format(arc_value))
    if grid:
        toks += ["x{x}", "y{y}"]
    if extra_tokens:
        toks += list(extra_tokens)
    return toks


def apply_name_prefix(base, name_spec=None):
    """Prepend ``name_spec['name_prefix']`` to a filename base if present."""
    prefix = (name_spec or {}).get("name_prefix", "")
    return "{}_{}".format(prefix, base) if prefix else base


_FAKE_TOKEN_VALUES = {
    "energy_energy": 2480.123,
    "exposure_s": 1.0,
    "incident_angle": 0.12,
    "x": -100.0,
    "y": 100.0,
    "piezo_x": 55000.0,
    "piezo_y": 4000.0,
    "piezo_z": -1200.0,
    "piezo_th": 0.15,
    "stage_x": 1.0,
    "stage_y": 2.0,
    "stage_z": 3.0,
    "stage_theta": 0.0,
    "stage_chi": 0.0,
    "stage_phi": 20.0,
    "xbpm2_sumX": 1234.5,
    "xbpm3_sumX": 2345.6,
    "pin_diode_current2_mean_value": 0.0123,
}


def preview_bar_name(sample="S1", *, arc=20.0, grid=False, incidence=False, exposure=None,
                     name_spec=None, fake=None, printer=print):
    """Preview the ``sample_name`` template and a fake filled example for a bar run.

    Returns the unfilled template.  Set ``printer=None`` to suppress console output.
    """
    spec = {k: v for k, v in (name_spec or {}).items()
            if k not in ("grid", "include_incidence")}
    toks = bar_name_tokens(arc, grid=grid, include_incidence=incidence, **spec)
    template = fname(apply_name_prefix(sample, name_spec), *toks)

    values = dict(_FAKE_TOKEN_VALUES)
    if exposure is not None:
        values["exposure_s"] = float(exposure)
    if fake:
        values.update(fake)

    fields = _re.findall(r"\{([^}:!]+)", template)
    missing = [f for f in fields if f not in values]
    fill = dict(values)
    for field in missing:
        fill[field] = "<{}>".format(field)
    try:
        filled = template.format(**fill)
    except Exception as exc:
        filled = "(could not fill: {!r})".format(exc)

    if printer is not None:
        printer("name_spec : {}".format(name_spec or {}))
        printer("TEMPLATE  : {}".format(template))
        printer("EXAMPLE   : {}   (data tokens filled with fake values)".format(filled))
        if fields:
            printer("runtime tokens (filled from recorded data at scan time): {}".format(
                ", ".join("{{{}}}".format(f) for f in fields)))
        if missing:
            printer("  !! NOTE: {} not in the fake-value table. If real, make sure the device "
                    "is recorded.".format(["{{{}}}".format(f) for f in missing]))
    return template


preview_name = preview_bar_name


def adjust_holder_positions(holder_name, *, delta=None, absolute=None, base="runnable",
                            store=None, dry_run=True, printer=print):
    """Bulk-adjust every sample's runnable position on a holder.

    The result is written to each sample's ``refined`` position, leaving ``nominal`` untouched.
    By default this is a dry run that prints the proposed changes and writes nothing.
    """
    delta = dict(delta or {})
    absolute = dict(absolute or {})
    if not delta and not absolute:
        raise ValueError("nothing to do: pass delta={...} and/or absolute={...}")
    bad = sorted((set(delta) | set(absolute)) - set(POSITION_AXES))
    if bad:
        raise ValueError("unknown position axis/axes {}. Valid axes: {}".format(
            bad, list(POSITION_AXES)))
    if base not in ("runnable", "nominal"):
        raise ValueError("base must be 'runnable' or 'nominal', got {!r}".format(base))

    bar = load_holder(holder_name, store=store)
    touched = sorted(set(delta) | set(absolute))
    _print(printer, "{} holder {!r}: {} sample(s); base={} axes {} | delta={} absolute={}".format(
        "DRY-RUN (no writes) --" if dry_run else "WRITING --",
        holder_name, len(bar), base, touched, delta or {}, absolute or {}))

    n_changed = 0
    for i, sample in enumerate(bar, 1):
        had_refined = sample.refined is not None
        run = sample.nominal if base == "nominal" else sample.runnable_position()
        new = Position.from_dict(run.to_dict())
        new.frame = "lab"

        changes = []
        skipped = []
        for axis in touched:
            old_val = getattr(run, axis)
            if axis in delta:
                if old_val is None:
                    skipped.append(axis)
                else:
                    setattr(new, axis, float(old_val) + float(delta[axis]))
            if axis in absolute:
                setattr(new, axis, float(absolute[axis]))
            new_val = getattr(new, axis)
            if new_val != old_val:
                changes.append("{}: {} -> {}".format(axis, _fmt_pos(old_val), _fmt_pos(new_val)))

        if base == "nominal":
            src = "nominal->refined(overwrite)" if had_refined else "nominal->refined(new)"
        else:
            src = "refined" if had_refined else "nominal->refined(new)"
        if changes:
            n_changed += 1
            _print(printer, "  [{}/{}] {:<24} ({}): {}".format(
                i, len(bar), sample.name, src, "; ".join(changes)))
            if not dry_run:
                bar.store.update_refined(sample.id, new)
                sample.refined = new
        else:
            _print(printer, "  [{}/{}] {:<24} ({}): no change".format(
                i, len(bar), sample.name, src))
        if skipped:
            _print(printer, "        (skipped delta on unset axis/axes: {} -- use `absolute` "
                            "to set them)".format(skipped))

    _print(printer, "{}: {} of {} sample(s) {}changed.".format(
        "DRY-RUN" if dry_run else "DONE", n_changed, len(bar),
        "would be " if dry_run else ""))
    if dry_run and n_changed:
        _print(printer, "  -> re-run with dry_run=False to persist these changes.")
    return n_changed


adjust_bar_positions = adjust_holder_positions


def sort_holder_by_name(holder_name, *, reverse=False, natural=True, store=None, dry_run=True,
                        printer=print):
    """Reorder a holder's run priority so samples are visited in name order.

    Positions and alignment are not changed.  Defaults to a dry run.
    """
    bar = load_holder(holder_name, store=store)
    key = _natural_key if natural else (lambda n: str(n).lower())
    old_order = [s.name for s in bar]
    ordered = sorted(bar, key=lambda s: key(s.name), reverse=reverse)
    new_order = [s.name for s in ordered]
    changed = old_order != new_order

    _print(printer, "{} holder {!r}: {} sample(s){}".format(
        "DRY-RUN (no writes) --" if dry_run else "WRITING --",
        holder_name, len(bar), " [reverse]" if reverse else ""))
    _print(printer, "  current order: {}".format(", ".join(old_order) or "(empty)"))
    _print(printer, "  name order   : {}".format(", ".join(new_order) or "(empty)"))

    if not changed:
        _print(printer, "DONE: already in name order -- nothing to do.")
        return False
    if not dry_run:
        bar.holder.sample_ids = [s.id for s in ordered]
        bar.store.put_holder(bar.holder)
        _print(printer, "DONE: holder run order updated to name order.")
    else:
        _print(printer, "DRY-RUN: order WOULD change. Re-run with dry_run=False to persist.")
    return True


sort_bar_by_name = sort_holder_by_name


def _fmt_pos(value):
    return "None" if value is None else "{:g}".format(value)


def _natural_key(name):
    parts = _re.split(r"(\d+)", str(name))
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _print(printer, text):
    if printer is not None:
        printer(text)
