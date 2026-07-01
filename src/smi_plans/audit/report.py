"""
report -- write the audit artifacts: a master CSV, an interactive HTML report, and a Markdown
gap summary.
"""

from __future__ import annotations

import csv
import html
import os
from collections import Counter, defaultdict


_CATEGORY_ORDER = [
    "css_only", "profile_not_baseline", "covered", "baseline_not_css", "profile_only",
]
_CATEGORY_LABEL = {
    "css_only": "On screen, NOT modelled by profile (gap)",
    "profile_not_baseline": "On screen + modelled, NOT in baseline (capture candidate)",
    "covered": "On screen + modelled + baselined (covered)",
    "baseline_not_css": "In baseline, not on any audited screen",
    "profile_only": "Modelled only (not screened, not baselined)",
}
_CATEGORY_COLOR = {
    "css_only": "#ffd5d5",
    "profile_not_baseline": "#fff0c2",
    "covered": "#d6f5d6",
    "baseline_not_css": "#e3e3ff",
    "profile_only": "#f0f0f0",
}


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record", "subsystem", "category", "on_css", "in_profile", "in_baseline",
                    "css_fields", "css_screens", "profile_device", "profile_dotted",
                    "classname", "read_only"])
        for r in rows:
            w.writerow([
                r.record, r.subsystem, r.category, int(r.on_css), int(r.in_profile),
                int(r.in_baseline),
                " ".join(sorted(r.css_fields)),
                " ".join(sorted(r.css_screens)),
                r.profile_device, r.profile_dotted, r.classname, r.read_only,
            ])


def _counts(rows):
    by_cat = Counter(r.category for r in rows)
    by_sub = defaultdict(Counter)
    for r in rows:
        by_sub[r.subsystem][r.category] += 1
    return by_cat, by_sub


def write_markdown(rows, unresolved, path, meta):
    by_cat, by_sub = _counts(rows)
    lines = []
    lines.append("# SMI device-completeness audit\n")
    lines.append(f"_CSS screens vs profile devices vs baseline._  \n")
    lines.append(f"- CSS files parsed: **{meta['css_files']}**, "
                 f"resolved CSS records: **{meta['css_records']}** "
                 f"(+{len(set(p.resolved for p in unresolved))} unresolved)\n")
    lines.append(f"- Profile records: **{meta['profile_records']}** "
                 f"(baseline: **{meta['baseline_records']}**), "
                 f"instances: {meta['instances']}, skipped: {meta['skipped']}\n")
    lines.append(f"- Total reconciled records: **{len(rows)}**\n")
    lines.append("\n## Totals by category\n")
    lines.append("| Category | Records |")
    lines.append("|---|---:|")
    for cat in _CATEGORY_ORDER:
        lines.append(f"| {_CATEGORY_LABEL[cat]} | {by_cat.get(cat, 0)} |")

    lines.append("\n## Biggest gaps (`css_only`) and capture candidates "
                 "(`profile_not_baseline`) by subsystem\n")
    lines.append("| Subsystem | css_only | profile_not_baseline | covered | total |")
    lines.append("|---|---:|---:|---:|---:|")
    for sub in sorted(by_sub, key=lambda s: -(by_sub[s]["css_only"]
                                              + by_sub[s]["profile_not_baseline"])):
        c = by_sub[sub]
        tot = sum(c.values())
        lines.append(f"| {sub} | {c.get('css_only',0)} | {c.get('profile_not_baseline',0)} "
                     f"| {c.get('covered',0)} | {tot} |")

    # Highlight the capture candidates explicitly (records shown + modelled but not baselined)
    cand = [r for r in rows if r.category == "profile_not_baseline"]
    lines.append(f"\n## Capture candidates: modelled + on screen, not in baseline "
                 f"({len(cand)})\n")
    lines.append("These are the lowest-effort wins -- the profile already has the device; it is "
                 "just not in `sd.baseline`.\n")
    lines.append("| record | subsystem | profile device |")
    lines.append("|---|---|---|")
    for r in sorted(cand, key=lambda r: (r.subsystem, r.record))[:200]:
        lines.append(f"| `{r.record}` | {r.subsystem} | {r.profile_device} |")
    if len(cand) > 200:
        lines.append(f"\n_…and {len(cand) - 200} more (see CSV)._\n")

    lines.append(f"\n## Unresolved CSS PVs ({len(set(p.resolved for p in unresolved))} unique)\n")
    lines.append("Screen PVs whose macros could not be fully resolved (runtime-supplied macros, "
                 "or resolver blind spots). Listed for completeness.\n")
    seen = set()
    for p in unresolved:
        if p.resolved in seen:
            continue
        seen.add(p.resolved)
        lines.append(f"- `{p.resolved}`  ({p.leaf_file})")
        if len(seen) >= 100:
            lines.append(f"\n_…and {len(set(x.resolved for x in unresolved)) - 100} more._")
            break

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def write_html(rows, unresolved, path, meta):
    by_cat, by_sub = _counts(rows)

    def esc(s):
        return html.escape(str(s))

    out = []
    out.append("""<!doctype html><html><head><meta charset="utf-8">
<title>SMI device-completeness audit</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:1.5rem;color:#222}
 h1{margin:0 0 .2rem} .sub{color:#666;margin:0 0 1rem}
 table{border-collapse:collapse;width:100%;font-size:13px}
 th,td{border:1px solid #ddd;padding:4px 7px;text-align:left;vertical-align:top}
 th{background:#f3f3f3;position:sticky;top:0;cursor:pointer}
 tr:hover{background:#fafafa}
 .pill{padding:1px 7px;border-radius:9px;font-size:11px;white-space:nowrap}
 .summary td,.summary th{text-align:center}
 code{background:#f5f5f5;padding:0 3px;border-radius:3px}
 .controls{margin:1rem 0;display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
 input,select{padding:5px 7px;font-size:13px}
 .mono{font-family:ui-monospace,Menlo,Consolas,monospace}
</style></head><body>""")
    out.append(f"<h1>SMI device-completeness audit</h1>")
    out.append(f"<p class='sub'>CSS screens ({meta['css_files']} files, {meta['css_records']} "
               f"resolved records) vs profile ({meta['profile_records']} records, "
               f"{meta['baseline_records']} baselined) — {len(rows)} reconciled records</p>")

    # summary table
    out.append("<table class='summary' style='width:auto'><tr><th>Category</th><th>Records</th></tr>")
    for cat in _CATEGORY_ORDER:
        out.append(f"<tr><td style='background:{_CATEGORY_COLOR[cat]}'>{esc(_CATEGORY_LABEL[cat])}"
                   f"</td><td>{by_cat.get(cat,0)}</td></tr>")
    out.append("</table>")

    # per-subsystem
    out.append("<h3>By subsystem</h3><table class='summary' style='width:auto'>"
               "<tr><th>Subsystem</th>" + "".join(f"<th>{esc(c)}</th>" for c in _CATEGORY_ORDER)
               + "<th>total</th></tr>")
    for sub in sorted(by_sub, key=lambda s: -(by_sub[s]['css_only']
                                             + by_sub[s]['profile_not_baseline'])):
        c = by_sub[sub]
        out.append(f"<tr><td>{esc(sub)}</td>"
                   + "".join(f"<td>{c.get(cat,0)}</td>" for cat in _CATEGORY_ORDER)
                   + f"<td>{sum(c.values())}</td></tr>")
    out.append("</table>")

    # controls
    cats = "".join(f"<option value='{c}'>{esc(_CATEGORY_LABEL[c])}</option>"
                   for c in _CATEGORY_ORDER)
    subs = "".join(f"<option value='{esc(s)}'>{esc(s)}</option>" for s in sorted(by_sub))
    out.append(f"""<div class='controls'>
 <input id='q' placeholder='filter record / device…' size='30' oninput='flt()'>
 <select id='cat' onchange='flt()'><option value=''>(all categories)</option>{cats}</select>
 <select id='sub' onchange='flt()'><option value=''>(all subsystems)</option>{subs}</select>
 <span id='n'></span></div>""")

    # main table (sorted: gaps first)
    order = {c: i for i, c in enumerate(_CATEGORY_ORDER)}
    rows_sorted = sorted(rows, key=lambda r: (order.get(r.category, 9), r.subsystem, r.record))
    out.append("<table id='t'><thead><tr>"
               "<th>record</th><th>category</th><th>subsystem</th><th>CSS</th>"
               "<th>profile</th><th>baseline</th><th>device</th><th>fields (CSS)</th>"
               "<th>screens</th></tr></thead><tbody>")
    for r in rows_sorted:
        scr = sorted(r.css_screens)
        scr_txt = ", ".join(os.path.basename(s) for s in scr[:4]) + (" …" if len(scr) > 4 else "")
        out.append(
            "<tr data-cat='{cat}' data-sub='{sub}' data-k='{k}'>"
            "<td class='mono'>{rec}</td>"
            "<td><span class='pill' style='background:{col}'>{cat}</span></td>"
            "<td>{sub}</td><td>{css}</td><td>{prof}</td><td>{base}</td>"
            "<td class='mono'>{dev}</td><td>{flds}</td><td>{scr}</td></tr>".format(
                cat=r.category, sub=esc(r.subsystem),
                k=esc((r.record + " " + r.profile_device).lower()),
                rec=esc(r.record), col=_CATEGORY_COLOR.get(r.category, "#fff"),
                css="✓" if r.on_css else "", prof="✓" if r.in_profile else "",
                base="✓" if r.in_baseline else "",
                dev=esc(r.profile_device), flds=esc(" ".join(sorted(r.css_fields))[:60]),
                scr=esc(scr_txt)))
    out.append("</tbody></table>")

    out.append("""<script>
function flt(){
 var q=document.getElementById('q').value.toLowerCase();
 var cat=document.getElementById('cat').value, sub=document.getElementById('sub').value;
 var rows=document.querySelectorAll('#t tbody tr'), n=0;
 rows.forEach(function(tr){
   var ok=(!q||tr.dataset.k.indexOf(q)>=0)&&(!cat||tr.dataset.cat==cat)&&(!sub||tr.dataset.sub==sub);
   tr.style.display=ok?'':'none'; if(ok)n++;});
 document.getElementById('n').textContent=n+' rows';
}
flt();
</script></body></html>""")
    with open(path, "w") as f:
        f.write("\n".join(out))
