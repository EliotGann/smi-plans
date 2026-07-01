"""
CLI: build the SMI device-completeness audit artifacts.

Usage (run in the profile's pixi env so ophyd + the smi_beamline classes import; NO EPICS
connections are made -- the profile side uses fake devices only):

    PYTHONPATH=src:<profile>/src python -m smi_plans.audit \
        --css ~/git/cs-studio-xf \
        --profile /nsls2/.../profile_collection \
        --out docs/device_audit
"""

from __future__ import annotations

import argparse
import os
import sys

from .css_resolver import CssResolver
from .profile_pvs import enumerate_profile_pvs
from .reconcile import reconcile
from . import report


def main(argv=None):
    ap = argparse.ArgumentParser(description="SMI CSS vs profile vs baseline PV audit")
    ap.add_argument("--css", required=True, help="path to cs-studio-xf (parent of 12id/ + common/)")
    ap.add_argument("--profile", required=True, help="path to the profile_collection checkout")
    ap.add_argument("--out", required=True, help="output directory for csv/html/md")
    ap.add_argument("--beamline-dir", default="12id")
    ap.add_argument("--max-depth", type=int, default=20)
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    print("[1/4] resolving CSS screens (no network)…")
    resolver = CssResolver(args.css, beamline_dir=args.beamline_dir, max_depth=args.max_depth)
    css_pvs = resolver.resolve_all()
    css_records = len(set(p.resolved.split(".")[0] for p in css_pvs if p.resolved_ok))
    css_full = len(set(p.resolved for p in css_pvs if p.resolved_ok))
    print(f"      {resolver.stats.files_parsed} files, {css_full} full PVs / {css_records} "
          f"records, {len(set(p.resolved for p in css_pvs if not p.resolved_ok))} unresolved")

    print("[2/4] enumerating profile PVs via FAKE devices (NO EPICS connections)…")
    prof = enumerate_profile_pvs(args.profile)
    profile_records = len(set(p.record for p in prof.pvs))
    baseline_records = len(set(p.record for p in prof.pvs if p.in_baseline))
    print(f"      {len(prof.instances)} instances ({len(prof.skipped)} skipped), "
          f"{profile_records} records, {baseline_records} baselined")

    print("[3/4] reconciling…")
    rows, unresolved = reconcile(css_pvs, prof)

    meta = dict(
        css_files=resolver.stats.files_parsed, css_records=css_records,
        profile_records=profile_records, baseline_records=baseline_records,
        instances=len(prof.instances), skipped=len(prof.skipped),
    )

    print("[4/4] writing artifacts…")
    csv_path = os.path.join(args.out, "audit_master.csv")
    html_path = os.path.join(args.out, "audit_report.html")
    md_path = os.path.join(args.out, "audit_gaps.md")
    skip_path = os.path.join(args.out, "profile_skipped.txt")
    report.write_csv(rows, csv_path)
    report.write_html(rows, unresolved, html_path, meta)
    report.write_markdown(rows, unresolved, md_path, meta)
    with open(skip_path, "w") as f:
        f.write("Profile instances skipped during fake enumeration (no PVs harvested):\n\n")
        for var, why in prof.skipped:
            f.write(f"  {var}: {why}\n")

    print(f"\nWrote:\n  {csv_path}\n  {html_path}\n  {md_path}\n  {skip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
