"""
reconcile -- merge the CSS, profile and baseline PV sets into one categorised record table.

Everything is compared at **record** level (the PV with any trailing ``.FIELD`` removed), which is
how a device's coverage is best judged: the CSS screens reference ``...}Mtr.RBV`` while the
profile models ``...}Mtr`` with many field children -- both collapse to the same record.

Categories (per record)
------------------------
* ``covered``               -- on a screen, modelled by the profile, and captured in baseline.
* ``profile_not_baseline``  -- on a screen and modelled, but NOT in baseline (capture candidate).
* ``css_only``              -- shown on a screen but the profile does not model it (gap).
* ``baseline_not_css``      -- captured in baseline but not shown on any audited screen.
* ``profile_only``          -- modelled but neither screened nor baselined.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class AuditRow:
    record: str
    subsystem: str
    on_css: bool
    in_profile: bool
    in_baseline: bool
    category: str
    css_fields: set = field(default_factory=set)
    css_screens: set = field(default_factory=set)
    profile_device: str = ""
    profile_dotted: str = ""
    classname: str = ""
    read_only: str = ""
    note: str = ""


_PREFIX_SUBSYS = [
    (re.compile(r"\{.*Mir:"), "mirrors"),
    (re.compile(r"\{.*Mono:|DCM|Mono"), "mono"),
    (re.compile(r"\{.*Fltr:"), "attenuators"),
    (re.compile(r"\{.*Slt:"), "slits"),
    (re.compile(r"\{.*BS:|beamstop", re.I), "beamstop"),
    (re.compile(r"\{.*EM:|BPM|xbpm", re.I), "diagnostics"),
    (re.compile(r"\{.*HEX:|HUB:|Stg|Exch"), "manipulators"),
    (re.compile(r"\{.*Pilatus|Det-|Cam:|Det:"), "detectors"),
    (re.compile(r"-VA|CCG|TCG|IP:|GV:|Valve|RGA|RGC"), "vacuum"),
    (re.compile(r"-UT|Cryo|LN2|Temp|Flow|Water|FCV"), "utilities"),
    (re.compile(r"-EPS|EPS"), "eps"),
    (re.compile(r"^FE:"), "frontend"),
    (re.compile(r"^SR:|IVU|undulator", re.I), "undulator"),
]


def _subsys_from_record(rec):
    for rx, name in _PREFIX_SUBSYS:
        if rx.search(rec):
            return name
    return "other"


def _field_of(pv):
    return pv.split(".", 1)[1] if "." in pv.split("}")[-1] else ""


def reconcile(css_pvs, profile_result):
    """Build the categorised :class:`AuditRow` list + an ``unresolved`` CSS list for the appendix.

    Parameters
    ----------
    css_pvs : list[CssPV]
    profile_result : ProfileResult

    Returns
    -------
    (rows, unresolved_css)
    """
    # --- CSS: record -> (fields, screens, subsystem) ---
    css = {}
    unresolved = []
    for p in css_pvs:
        if not p.resolved_ok:
            unresolved.append(p)
            continue
        rec = p.resolved.split(".")[0]
        d = css.setdefault(rec, {"fields": set(), "screens": set(), "subsystem": p.subsystem})
        fld = _field_of(p.resolved)
        if fld:
            d["fields"].add(fld)
        d["screens"].add(p.leaf_file)

    # --- profile: record -> representative ProfilePV (prefer baseline + a readback field) ---
    prof = {}
    prof_baseline = set()
    for pv in profile_result.pvs:
        if pv.in_baseline:
            prof_baseline.add(pv.record)
        cur = prof.get(pv.record)
        if cur is None or (pv.in_baseline and not cur.in_baseline):
            prof[pv.record] = pv

    all_records = set(css) | set(prof)
    rows = []
    for rec in sorted(all_records):
        on_css = rec in css
        in_prof = rec in prof
        in_base = rec in prof_baseline
        if on_css and in_prof and in_base:
            cat = "covered"
        elif on_css and in_prof:
            cat = "profile_not_baseline"
        elif on_css and not in_prof:
            cat = "css_only"
        elif in_base:
            cat = "baseline_not_css"
        else:
            cat = "profile_only"

        c = css.get(rec)
        p = prof.get(rec)
        subsystem = (c["subsystem"] if c and c.get("subsystem") not in (None, "?", "(root)")
                     else (p.source_file if p else None)) or _subsys_from_record(rec)
        rows.append(AuditRow(
            record=rec, subsystem=subsystem, on_css=on_css, in_profile=in_prof,
            in_baseline=in_base, category=cat,
            css_fields=(c["fields"] if c else set()),
            css_screens=(c["screens"] if c else set()),
            profile_device=(p.device if p else ""),
            profile_dotted=(p.dotted if p else ""),
            classname=(p.classname if p else ""),
            read_only=("RO" if p and p.read_only else ("RW" if p else "")),
        ))
    return rows, unresolved
