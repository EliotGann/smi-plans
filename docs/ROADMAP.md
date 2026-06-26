# Roadmap — execution order for the post-beamtime work

> **What this is:** the single sequencing authority over the four plan docs. Read this first; it says
> what order to do things in and why, which decisions are locked, and which open questions block
> others vs. are local.
>
> **Plan docs:** `FIELD_LESSONS_BAR_PLANS.md` (field evidence + decisions), `IMPLEMENTATION_PLAN.md`
> (backend code workstreams WS0–WS7), `DOC_CORRECTIONS_PLAN.md` (docs incl. GUI),
> `LEGACY_REVIEW_PLAN.md` (regenerate annotations), `NAMED_LISTS_PLAN.md` (the named-list library).

## The logical path (decided)
**Decisions → code (energy-led) → docs (concurrent per workstream) → legacy (last).**
Named lists is an **independent parallel track**, not the leader.

Rationale: legacy and most docs are **downstream** — they describe code behavior, so the code must be
frozen first (never regenerate ~530 legacy annotations describing behavior that might still change).
The work that **unblocks the most downstream effort** is the energy change (WS1), and it is already
fully decided. Named lists (WS7) is the most independent workstream — nothing depends on it and its
open questions affect nothing else — so it runs in parallel whenever convenient; doing it first would
not unblock anything.

---

## Phase 0 — Cross-cutting decisions (no code)
These are the only decisions that cause **rework if deferred**, because they ripple into GUI + legacy
wording. Status:

| Decision | Status | Drives |
|---|---|---|
| Energy params `max_step/fb_settle/double_set` → **REMOVE (breaking)** | ✅ locked | WS1 + doc + ~530 legacy annotations |
| Canonical spatial token → **`{x}`/`{y}` relative-offset Signals** | ✅ locked | WS3 + GUI spatial guidance + legacy spatial wording |
| Project/proposal metadata → **session context** (not per-call kwarg) | ✅ locked | bar signatures + GUI + QS |
| GUI spec field names (`x_step/x_n`,`grid/flux_reseek` → bridge's `x/y/snake`,`values`) | ⬜ OPEN (DOC) | DOC A3/A6 canonical spec table; not a code blocker, but resolve before GUI doc edits |
| `multi_sample_run_split` as the GUI default vs full arc-economy | ⬜ OPEN (GUI-only) | DOC A0/A5; resolve before GUI generation edits |

**Local / non-blocking decisions (decide in-workstream, no rush):**
- Where the holder bridge lives (`_holder.py` vs `_store.py`) — local to WS5.
- Named-list prefix string / Option A-B / store-both / provenance — local to WS7
  (recommendations already in `NAMED_LISTS_PLAN.md` §7; `swaxslists`, Option A, store both, yes).

> **Do before starting Phase 2 (docs):** resolve the two ⬜ OPEN items above. They don't block Phase 1
> code, but the GUI doc can't be finalized without them.

---

## Phase 1 — Backend code
Order within the phase (from `IMPLEMENTATION_PLAN.md`):

1. **WS0 — test harness binds to source** (editable install / PYTHONPATH). One-time prerequisite;
   without it tests can silently run against the stale installed `smi_plans`.
2. **WS1 — energy gut + REMOVE params (BREAKING).** Highest priority: it unblocks the largest share of
   docs + legacy. Fully decided. Lands with its doc edits (DOC Part B/C/D/E energy) in the SAME PR.
3. **WS2 — token validator in `acquire`** → then **WS3 — `spatial_grid_axes` records `{x}`/`{y}`**
   (WS3 depends on WS2). Together these freeze the token story for GUI + legacy.
4. **WS4 — positioning from `Position`** and **WS5 — holder bridge** — independent, parallelizable
   (both pure / dict-backed).
5. **WS7 — named lists** — independent parallel track; can be built any time in Phase 1 by another
   person (pure, dict-backed, blocks nothing). NL-3 (plumb name-or-list into bars) best after the bars
   are otherwise stable.
6. **WS6 — re-thin `bar_plans.py`** — last code step; depends on WS1/WS3/WS4/WS5 (and benefits from
   WS7 for named-list calls).

Parallelizable by a second person: **WS4, WS5, WS7** (all pure, no hardware, dict-backed).

Critical path: **WS0 → WS1 → WS2 → WS3 → WS6**. WS4/WS5/WS7 hang off to the side and join at WS6.

---

## Phase 2 — Documentation (mostly concurrent with Phase 1)
From `DOC_CORRECTIONS_PLAN.md`. Each doc part ships **with its code workstream's PR** so no doc ever
describes a removed API:
- Energy doc edits (Parts B/C/D/E) — **in WS1's PR** (mandatory; breaking).
- Token/spatial doc edits (Part D + GUI A2/A3) — with WS2/WS3.
- GUI Redis-first reframe (Part A0) — with WS5 (holder bridge) + WS7 (named lists).
- Positioning contract (A4) — with WS4.
- Arc-economy "available again" (A5) — can land immediately (multi-run already done).
- **Resolve the two ⬜ Phase-0 GUI/spec decisions before finalizing the GUI skill.**
- Final consistency pass: one canonical spec table (`build_axes_from_spec`), others derive from it.

---

## Phase 3 — Legacy annotations (LAST)
From `LEGACY_REVIEW_PLAN.md`. Only after **all** code behavior + canonical doc wording is frozen:
- Regenerate the ~530 `💡` annotations from corrected templates (energy no-ops; `{x}`/`{y}` is now
  the recorded relative token — teach it directly, no version-hedge needed since WS3 will have
  landed; arc-economy available; naming real-key caveat).
- **Mandatory grep gate:** zero regenerated examples reference `max_step`/`fb_settle`/`double_set`.
- Edit the normative `_analysis` docs (`BEST_PRACTICES_DRAFT.md`, `USE_CASE_TAXONOMY.md`) jointly.
- Validate by migrating 2–3 sample scripts per their regenerated advice in sim.

> Legacy strictly last because it is 100% downstream. Doing it earlier risks re-doing ~530 sites.

---

## One-line answer to "what's the logical path?"
**Phase 0 decisions (energy/spatial-token/project = locked; resolve 2 GUI/spec Qs before docs) →
Phase 1 code led by WS1 energy, with WS4/WS5/WS7 in parallel → Phase 2 docs concurrent per workstream
→ Phase 3 legacy last.** Named lists rides along in Phase 1 as a parallel track, not the opener.

## Open-decision blast-radius summary (so nothing is deferred that shouldn't be)
- **Affects others → decide before the dependent starts:** energy-remove (done), spatial token (done),
  project metadata (done), GUI spec field names (open — before GUI doc), split-vs-economy default
  (open — before GUI generation).
- **Local → decide in-workstream:** holder-bridge location (WS5), named-list prefix/options (WS7).
- **None of the named-list open questions affect any other workstream.**
