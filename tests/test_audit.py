"""
Tests for smi_plans.audit -- the CSS macro/include resolver and the reconciliation logic.

Pure: synthetic CSS XML written to a tmp tree + small fake PV objects.  No EPICS, no profile load,
no network.  The fake-device profile enumeration is exercised separately/manually (it needs the
smi_beamline classes + ophyd), but the *resolver* and *reconcile* -- the parts most likely to
regress -- are covered here.
"""
import os
import textwrap

import pytest

from smi_plans.audit.css_resolver import CssResolver, expand_macros, is_resolved
from smi_plans.audit.reconcile import reconcile
from smi_plans.audit.css_resolver import CssPV


# --------------------------------------------------------------------------- macro expansion
def test_expand_paren_and_curly_and_default():
    sc = {"Sys": "XF:12IDC", "Dev": "{HEX:Stg-Ax:Y}"}
    assert expand_macros("$(Sys)$(Dev)Mtr", sc) == "XF:12IDC{HEX:Stg-Ax:Y}Mtr"
    assert expand_macros("${Sys}:I", sc) == "XF:12IDC:I"
    assert expand_macros("$(Missing=def):x", sc) == "def:x"
    assert not is_resolved("$(Unknown)x")
    assert is_resolved("plain")


def test_chained_macro_expansion():
    sc = {"P": "$(Sys)$(Dev)", "Sys": "XF:12IDA", "Dev": "{Mir:HF}"}
    assert expand_macros("$(P)-Ax", sc) == "XF:12IDA{Mir:HF}-Ax"


# --------------------------------------------------------------------------- resolver tree
def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(textwrap.dedent(text).strip())


def test_resolver_nested_macros_and_include(tmp_path):
    repo = str(tmp_path)
    # a shared motor template that consumes macros
    _write(repo, "common/motor/_m.opi", """
        <display>
          <widget typeId="x">
            <pv_name>$(Sys)$(Branch){$(Dev)$(Ax)}Mtr.RBV</pv_name>
          </widget>
        </display>
    """)
    # the beamline page: outer group sets Sys/Branch/Dev, inner linking sets Ax + includes template
    _write(repo, "12id/main.opi", """
        <display>
          <widget typeId="group">
            <macros><Sys>XF:12IDC</Sys><Branch>-OP:2</Branch><Dev>HEX:Stg</Dev></macros>
            <widget typeId="linkingContainer">
              <macros><include_parent_macros>true</include_parent_macros><Ax>-Ax:Y</Ax></macros>
              <opi_file>../common/motor/_m.opi</opi_file>
            </widget>
            <widget typeId="linkingContainer">
              <macros><include_parent_macros>true</include_parent_macros><Ax>-Ax:Z</Ax></macros>
              <opi_file>../common/motor/_m.opi</opi_file>
            </widget>
          </widget>
        </display>
    """)
    r = CssResolver(repo, beamline_dir="12id")
    pvs = r.resolve_all(roots=["12id/main.opi"])
    resolved = sorted(p.resolved for p in pvs if p.resolved_ok)
    assert "XF:12IDC-OP:2{HEX:Stg-Ax:Y}Mtr.RBV" in resolved
    assert "XF:12IDC-OP:2{HEX:Stg-Ax:Z}Mtr.RBV" in resolved


def test_resolver_prefers_bob(tmp_path):
    repo = str(tmp_path)
    _write(repo, "12id/sub.opi", "<display><widget typeId='x'><pv_name>OLD:opi</pv_name></widget></display>")
    _write(repo, "12id/sub.bob", "<display><widget type='x'><pv_name>NEW:bob</pv_name></widget></display>")
    _write(repo, "12id/main.bob", """
        <display><widget type="x">
          <actions><action type="open_display"><file>sub.opi</file></action></actions>
        </widget></display>
    """)
    r = CssResolver(repo, beamline_dir="12id")
    pvs = [p.resolved for p in r.resolve_all(roots=["12id/main.bob"])]
    assert "NEW:bob" in pvs and "OLD:opi" not in pvs   # .bob supersedes .opi


def test_resolver_drops_placeholder_and_flags_unresolved(tmp_path):
    repo = str(tmp_path)
    _write(repo, "12id/main.opi", """
        <display><widget typeId="x">
          <pv_name>$(pv_name)</pv_name>
          <pv_name>$(P)$(M).RBV</pv_name>
          <pv_name>loc://thing(0)</pv_name>
        </widget></display>
    """)
    r = CssResolver(repo, beamline_dir="12id")
    pvs = r.resolve_all(roots=["12id/main.opi"])
    vals = [p.resolved for p in pvs]
    assert "$(pv_name)" not in vals          # pure placeholder dropped
    assert "loc://thing(0)" not in vals      # local PV skipped
    assert any(p.resolved == "$(P)$(M).RBV" and not p.resolved_ok for p in pvs)  # real, flagged


def test_resolver_scope_confinement(tmp_path):
    repo = str(tmp_path)
    _write(repo, "other/secret.opi", "<display><widget typeId='x'><pv_name>OTHER:pv</pv_name></widget></display>")
    _write(repo, "12id/main.opi", """
        <display><widget typeId="x">
          <actions><action type="OPEN_DISPLAY"><path>../other/secret.opi</path></action></actions>
        </widget></display>
    """)
    r = CssResolver(repo, beamline_dir="12id")  # default allow: 12id/, 12id1/, common/
    pvs = [p.resolved for p in r.resolve_all(roots=["12id/main.opi"])]
    assert "OTHER:pv" not in pvs              # out-of-scope screen not followed


# --------------------------------------------------------------------------- reconcile
class _ProfPV:
    def __init__(self, record, device="dev", dotted="d", classname="EpicsSignal",
                 read_only=False, in_baseline=False, source_file="mod"):
        self.record = record; self.device = device; self.dotted = dotted
        self.classname = classname; self.read_only = read_only
        self.in_baseline = in_baseline; self.source_file = source_file


class _Prof:
    def __init__(self, pvs):
        self.pvs = pvs


def _css(resolved, ok=True, sub="op", screen="12id/x.opi"):
    return CssPV(raw=resolved, resolved=resolved, leaf_file=screen, subsystem=sub,
                 root_file=screen, resolved_ok=ok)


def test_reconcile_categories():
    css = [
        _css("A{1}Mtr.RBV"), _css("A{1}Mtr.VAL"),   # record A{1}Mtr -> on css
        _css("B{2}Sts"),                            # on css, not in profile
        _css("$(x)bad", ok=False),                  # unresolved -> appendix
    ]
    prof = _Prof([
        _ProfPV("A{1}Mtr", in_baseline=True),       # covered
        _ProfPV("C{3}Pos", in_baseline=True),       # baseline_not_css
        _ProfPV("D{4}Pos", in_baseline=False),      # profile_only
    ])
    rows, unresolved = reconcile(css, prof)
    by = {r.record: r.category for r in rows}
    assert by["A{1}Mtr"] == "covered"
    assert by["B{2}Sts"] == "css_only"
    assert by["C{3}Pos"] == "baseline_not_css"
    assert by["D{4}Pos"] == "profile_only"
    assert len(unresolved) == 1
    # css fields aggregated at record level
    a = next(r for r in rows if r.record == "A{1}Mtr")
    assert a.css_fields == {"RBV", "VAL"}


def test_reconcile_profile_not_baseline():
    css = [_css("E{5}Mtr.RBV")]
    prof = _Prof([_ProfPV("E{5}Mtr", in_baseline=False)])
    rows, _ = reconcile(css, prof)
    assert rows[0].category == "profile_not_baseline"
