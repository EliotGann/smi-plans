# Legacy Script Review Plan — re-annotating migration advice (post-beamtime)

> **Execution order:** see `ROADMAP.md`. Legacy is **Phase 3 — LAST**: do it only after all code
> behavior + canonical doc wording is frozen (never regenerate ~530 annotations describing behavior
> that might still change).
>
> **Companion docs:** `FIELD_LESSONS_BAR_PLANS.md` (decisions), `IMPLEMENTATION_PLAN.md` (backend
> code), `DOC_CORRECTIONS_PLAN.md` (docs incl. GUI + the normative `_analysis` docs). This doc covers
> the **legacy user scripts** in `/home/xf12id/SWAXS_user_scripts/` and their embedded migration
> advice.
>
> **Status:** plan only. **Approach decided: REGENERATE the annotations from corrected templates**
> (the advice is ~530+ machine-generated comments from a fixed vocabulary — regenerating is more
> consistent and auditable than hand-editing 173 files).
>
> **Trigger:** the backend changes in `IMPLEMENTATION_PLAN.md` make some embedded advice WRONG.
> Most urgent: the energy stepper change is **breaking** (params removed) AND the dominant
> annotation (~530 sites) tells users `move_energy_fb`/`energy_axis` "handle the beam feedback and
> re-seek if the beam dips" — which is no longer how it works.

## Scope (from the audit)
| Corpus | Files | Annotated | Notes |
|---|---|---|---|
| `legacy/` | 134 | 133 (all but `30-user-UCR.py`) | 3,966 `💡` lines; the bulk |
| `CFN/` (mostly `CFN/Yugang/`) | 21 | ~20 | arc-economy advice concentrated here |
| `LBL/`, `nist/`, `SBU/`, `UVA/`, `Cornell/`, `Commissioning/`, `templates/` | ~22 | ~20 | incl. `LBL/30-user-Su.py` (multi_sample_run ×7) |
| **`templates/_analysis/` normative docs** | (BEST_PRACTICES_DRAFT, USE_CASE_TAXONOMY) | — | corrected via DOC plan Part F, executed here |
| `CDSAXS/` | (thousands, auto-gen) | — | OUT OF SCOPE (flagged by taxonomy) |

Total scripts to touch: **~173**. Annotation sites by category (tree-wide):
- **(a) Energy** "handles feedback & re-seek / drop this settle / set twice": ~530+ sites in ~45-53 files.
- **(b) Naming** bare `{x}`/`{y}`/`{energy}` tokens: 100 files use bare style vs 15 full-key (≈7:1).
- **(c) Spatial grid** advice: ~71 files reference `spatial_grid_axes`/`map_grid_run`.
- **(d) Arc-economy / multi-run** advice: 19 files recommend `giwaxs_bar_arc_economy`/`multi_sample_run`.

## Important nuance — these scripts WORK TODAY
The bare `{x}`/`{energy}` tokens in legacy scripts are filled by explicit `str.format(x=..., ...)`
kwargs at runtime — **they are not broken now.** The risk is **latent**: it materializes only when a
user follows the embedded advice and migrates the name into smi_plans templating (where tokens resolve
against recorded keys: `piezo_x`, `energy_energy`). So the fix is to **correct the ADVICE**, not the
legacy code itself. (We do not rewrite legacy plans; we re-annotate.)

---

## The corrected annotation templates (the heart of this plan)
Regenerate from these. Each template = the corrected `💡` text for one situation.

### (a) Energy — REPLACE the "handles feedback & re-seek / set twice" vocabulary
- **OLD (now wrong):** "`move_energy_fb`/`energy_axis` already wait for the energy to settle, handle
  the beam feedback, and re-seek if the beam dips" / "moves in ≤50 eV hops ... pauses the beam
  feedback ... turns feedback back on" / "set twice".
- **NEW template:** "smi_plans moves energy with a plain `bps.mv(energy, E)`; the `energy` device
  itself manages the DCM feedback, the undulator gap, and the harmonic (validated on the beamline),
  so you do **not** need feedback toggling, `max_step` hops, double-setting, or a manual re-seek. Just
  `yield from move_energy_fb(E)` (or an `energy_axis`), optionally with `settle=` and, if you want a
  beam-loss guard, `flux_signal=/flux_threshold=`."
- **CRITICAL (breaking):** any annotation that shows `move_energy_fb(E, max_step=..., fb_settle=...,
  double_set=...)` must be regenerated WITHOUT those kwargs (they're removed — they'd now raise
  `TypeError`). Grep the annotations for `max_step`/`fb_settle`/`double_set` and purge from examples.
- The "drop this settle wait" / "drop this beam re-seek" templates: keep the "you can drop the manual
  feedback/re-seek hand-coding" message, but stop attributing the re-seek to the device knobs; if the
  user wants a guard it's `flux_signal/flux_threshold` on the axis.

### (b) Naming — ADD the token caveat wherever a name template is discussed
- **NEW template (attach to any naming advice that shows `{x}`/`{y}`/`{energy}`):** "When you migrate
  this filename to smi_plans, tokens are filled from the **recorded data keys**, so use the real key:
  `{piezo_x}`/`{piezo_y}`/`{energy_energy}` (NOT `{x}`/`{y}`/`{energy}`), or record a relative-offset
  `Signal(name='x')` for `{x}`. A token with no matching key fails the run's file naming. See
  `skills/naming-and-filename-tokens.md`."
- Where advice already uses full keys (`{energy_energy}`, `{piezo_x}` — 15 files), no change.

### (c) Spatial grid — note `{x}`/`{y}` availability is version-dependent
- **NEW template:** "smi_plans builds the grid and records positions for you. After the
  `spatial_grid_axes` update, `{x}`/`{y}` (relative offsets) are valid filename tokens; before it,
  use `{piezo_x}`/`{piezo_y}`." (Only assert `{x}`/`{y}` once IMPLEMENTATION_PLAN Workstream 3 lands —
  gate the template on that.)

### (d) Arc-economy / multi-run — REMOVE any "experimental/risky" hedging; state it works
- **NEW template:** "This per-sample align-then-arc-sweep pattern is smi_plans
  `giwaxs_bar_arc_economy` (multiple runs open at once; the arc moves once for the whole bar).
  AreaDetector documents are handled correctly (per-(sample,arc) staging; each arc is its own
  stream). If you prefer no concurrent open runs, `multi_sample_run_split` does one run per
  (sample,arc) with the same arc economy. Use arc-economy when arc travel dominates; otherwise
  `giwaxs_bar` (one run per sample) is simplest."
- Remove any older note that called this experimental/unsafe (the `UnresolvableForeignKeyError` is
  fixed).

---

## Execution

### Step 1 — recover/identify the annotation generator
The audit notes the annotations are machine-generated (tag `REVIEW 2026-06-22`) and that the
`_analysis/` survey docs "back a future pass that annotates each legacy function." **Locate that
generator/agent + its template vocabulary.** If it's a script, update its templates (a)-(d) above and
re-run. If it was an agent prompt, update the prompt with the corrected templates + the breaking-change
purge rule. (If the generator is unrecoverable, fall back to a scripted find/replace keyed on the
fixed phrase strings, which are enumerated in the audit — they're verbatim and high-frequency.)

### Step 2 — regenerate in a branch, diff-review by category
- Regenerate all annotations. Because the source phrases are fixed strings, the diff is reviewable in
  bulk per category. Spot-check the representative files the audit named: energy
  (`30-user-Gregory.py`, `30-user-Gann.py`), arc-economy (`CFN/Yugang/2026C1_CNam.py`,
  `30-user-Fakhraai.py`), naming (`30-user-Quan.py`, `34-oleg.py`), spatial (`Commissioning/microlistscan.py`).
- **Hard gate:** grep the regenerated tree for `max_step`/`fb_settle`/`double_set` in `💡` lines →
  must be zero (breaking-change purge).

### Step 3 — the normative `_analysis` docs (joint with DOC plan Part F)
- Edit `BEST_PRACTICES_DRAFT.md` (Tenets 3/6/9 + open-questions 1/3) and `USE_CASE_TAXONOMY.md`
  (§5.4, §B) per DOC_CORRECTIONS_PLAN Part F. These are hand-written, not generated — edit directly.

### Step 4 — verify a sample migrates cleanly
- Pick 2-3 representative legacy scripts, follow their *regenerated* advice to actually port them onto
  smi_plans, and run in sim. Confirm: no removed-param `TypeError`, no token `KeyError` (post-W2/W3),
  arc-economy path runs. This validates the advice end-to-end, not just textually.

## Sequencing & dependencies
- The **(a) energy** and **(d) arc-economy** template changes can/should be regenerated as soon as
  IMPLEMENTATION_PLAN Workstream 1 (energy, breaking) lands and the multi-run fix is committed.
- The **(c) spatial** `{x}`/`{y}` claim is gated on Workstream 3 — regenerate spatial advice after it
  lands, or use the version-hedged wording until then.
- The **(b) naming** caveat can go in immediately (it references the already-written naming skill).

## Acceptance
- Zero `💡` lines reference removed energy params or describe `move_energy_fb`/`energy_axis` as
  managing DCM feedback / re-seeking via device knobs / "set twice".
- Every naming-advice site that shows a bare `{x}`/`{energy}` token carries the real-key caveat.
- Arc-economy advice describes it as available (no "experimental"), with the staging/stream facts and
  the `_split` fallback.
- 2-3 sample scripts migrate per their regenerated advice and run clean in sim.
- `BEST_PRACTICES_DRAFT.md` / `USE_CASE_TAXONOMY.md` reconciled (no "keep hand-managed feedback",
  no "multi-run is an open question").

## Risks / watch-outs
- **Don't rewrite legacy plan code** — only annotations. The scripts must keep working as-is for users
  who haven't migrated.
- **Breaking-param purge is the highest-risk omission** — a regenerated example that still shows
  `max_step=` would hand users code that now raises `TypeError`. The Step-2 grep gate is mandatory.
- If the generator is an agent, capture the corrected templates in this doc so the regeneration is
  reproducible and not a one-off.
