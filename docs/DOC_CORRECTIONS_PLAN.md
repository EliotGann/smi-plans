# Documentation Corrections Plan — post-beamtime

> **Execution order:** see `ROADMAP.md`. Docs are Phase 2 — each part ships **with its code
> workstream's PR**; resolve the 2 open GUI/spec decisions (Phase 0) before finalizing the GUI skill.
>
> **Companion docs:** `FIELD_LESSONS_BAR_PLANS.md` (decisions/source of truth), `IMPLEMENTATION_PLAN.md`
> (backend code), `LEGACY_REVIEW_PLAN.md` (legacy scripts). This doc covers **all documentation**:
> the GUI builder skill (emphasis), the backend docstrings, the qserver/spec docs, PACKAGE_OVERVIEW,
> and the backend skills.
>
> **Status:** plan only. Several corrections are forced by breaking code changes in
> `IMPLEMENTATION_PLAN.md` and MUST land in the same PR series so docs never describe removed APIs.
>
> **Why this is big:** an audit found the GUI skill's spec field names ALREADY diverge from the actual
> `build_axes_from_spec` (independent of our changes), the energy "feedback/re-seek" story is about to
> become false, the `{x}` vs `{piezo_x}` token trap is undocumented in the GUI, and arc-economy is
> documented as a ready option (it WAS blocked, now fixed — so the framing must be corrected to
> "available, use when arc travel dominates").

## Driving changes (what forces each correction)
- **C1 Energy gut + param removal (BREAKING):** `move_energy_fb`/`energy_axis` lose
  `max_step`/`fb_settle`/`double_set`; they no longer "manage feedback / re-seek" (the device does).
- **C2 Token rule + validator:** filename tokens must be real recorded keys; `acquire` will enforce.
- **C3 spatial_grid_axes records `{x}`/`{y}`:** relative-offset Signals; spec field reconciliation.
- **C4 Positioning contract:** plans move from `nominal`/`refined` `Position`; GUI writes that.
- **C5 Multi-run FIXED:** arc-economy un-blocked (per-run AreaDetector staging + `multi_sample_run_split`).
- **C6 Redis-first GUI (NEW):** the Redis store is the preferred GUI↔profile channel. The GUI should
  reference samples/holders **and named lists** (edges/angles/temperatures/times) **by name** and emit
  the most elegant `*_bar(...)` call — NOT copy-paste lists of positions/energies as the first option.
  (See `NAMED_LISTS_PLAN.md` for the named-list library.)

---

## Part A — GUI builder skill (`skills/smi-plans-gui-builder.md`)  ★ PRIORITY
The GUI is the user-facing surface; its doc currently has the most drift. Corrections:

### A0. Redis-first generation (C6) — the headline reframe
- **Principle (field-proven):** the Redis sample store is a *great* GUI↔profile channel — no
  copy-paste. The GUI's primary output should be the **most elegant call that references stored data
  by name**, e.g. `RE(giwaxs_bar(holder="bar1", incident_angles="grazing_fine"))`, NOT a script that
  pastes coordinate/energy lists inline.
- **Concretely:**
  - Samples/holders: reference by holder name via the holder→SampleList bridge
    (IMPLEMENTATION_PLAN Workstream 5), not `SampleList.from_columns(piezo_x=[...], ...)`. The
    `from_columns` paste path becomes a *fallback*/import tool, not the default generated code.
  - Big lists (energies/angles/temperatures/times): reference **named lists** by name
    (`energies="Fe_K_XANES"`) per `NAMED_LISTS_PLAN.md`; the GUI's list-builders become **editors**
    for those stored entries, not code generators that dump a Python list.
  - Keep a literal-list / explicit-columns escape hatch for genuine one-offs, clearly secondary.
- This reframes the GUI doc's whole "generated script" section: the example scripts should lead with
  name-referencing calls; the inline-list versions move to an "advanced / one-off" note.
- New GUI panels implied: a **Lists panel** beside Samples/Holders (browse/edit/add named lists by
  kind — see `NAMED_LISTS_PLAN.md` §5).

### A1. Energy axis (C1) — lines ~112-113, ~190-191
- Stop implying the feedback machinery is meaningful. The generated-script example
  `energy_axis(..., flux_signal=..., flux_threshold=...)` is fine (re-seek is a plan-level concern and
  stays), but remove/avoid any `max_step`/`fb_settle`/`double_set` in examples and in the spec.
- The energy spec block currently uses `grid` + `flux_reseek:{signal,threshold}` (skill) which does
  **not** match `build_axes_from_spec`'s `values` + `settle` + `flux_threshold`. **Reconcile to the
  real bridge fields** (this is a pre-existing mismatch, fix it now).
- Add one line: energy gap/harmonic/feedback are handled by the `energy` device; the GUI exposes only
  energies + optional flux re-seek + `settle`.

### A2. Filename tokens (C2) — lines ~445-447, ~472-473 (currently ZERO token examples)
- Add a short "Filename tokens" subsection (the GUI's job per line 445 is to *show available tokens*):
  - State the rule: every `{token}` must be a real recorded data key (`<device>_<component>` or a
    recorded `Signal.name`).
  - Show the device→key mapping and the **`{x}` vs `{piezo_x}` trap** explicitly.
  - The GUI token-hint feature must list tokens from `_core.COMMON_TOKENS` + the chosen axes'
    recorded keys — and must NOT offer `{x}`/`{y}` unless the spatial axis records the `x`/`y` Signal
    (post-C3).
  - Cross-reference `skills/naming-and-filename-tokens.md` as the authority.
  - Note that `acquire` will reject bad tokens at build time (post-C2) — the GUI should pre-validate
    so the user sees it before generating a script.

### A3. Spatial axis (C3) — line ~114
- Reconcile spec fields: skill shows `x_step`/`x_n`; the bridge uses `x`/`y`/`snake` (lists). Pick one
  and make skill + bridge + qserver agree. If `x_step`/`x_n` shorthand is kept, the bridge must learn
  it; otherwise change the skill.
- Token guidance: a spatial axis yields `{piezo_x}`/`{piezo_y}` today; after C3 it ALSO yields
  `{x}`/`{y}` (relative). Document which, and default the GUI to the safe one.

### A4. Positioning contract (C4) — lines ~99-106 (transient spec) vs ~268-345 (bookmarks)
- The transient ExperimentSpec `samples` block uses legacy flat `piezo_x` via `from_columns` — fine,
  keep. The persistent bookmarks use `nominal`/`refined` Position — keep.
- Add an explicit statement of the **movement contract**: the GUI writes `nominal`/`refined`
  `Position` (frame `holder`/`lab`); plans move via `goto_sample`/`runnable_position()` (post-C4).
  This is the GUI↔plans seam; field names + frame must match SAMPLE_SYSTEM_PLAN.

### A5. Arc economy / multi-run (C5) — lines ~208, ~429
- Currently presents `multi_sample_run`/arc-economy as a ready opt-in (correct AGAIN now) — but make
  sure it does NOT carry any "experimental/blocked" caveat we might add elsewhere, and ADD the new
  facts: detectors stage per (sample, arc) point; each arc is its own stream; `multi_sample_run_split`
  exists as the no-concurrent-runs fallback. Recommend arc-economy only when arc travel dominates.

### A6. Spec axis-type table
- Publish the authoritative spec `type` → backend constructor table (from `build_axes_from_spec`):
  `energy, temperature, incidence, motor, spatial, potential, rh, time, manual`, with the EXACT field
  names each reads. Fix every example in the skill that uses a field name the bridge doesn't read
  (`grid`, `flux_reseek`, `x_step`, `x_n`, string `speed:"slow"` vs `SPEED_SLOW`/`2`).

---

## Part B — Backend docstrings (forced by C1/C3)
- `_compose.move_energy_fb` / `energy_axis` (`_compose.py:422-518`): rewrite to the plain-`bps.mv`
  model; remove all `max_step`/`fb_settle`/`double_set` prose; add the delegation + discarded-
  approaches note. (Same edit as IMPLEMENTATION_PLAN Workstream 1a.)
- `_compose.spatial_grid_axes` (`_compose.py:660-686`): document relative-offset Signal recording,
  `{x}`/`{y}` vs `{piezo_x}`/`{piezo_y}`, `role=`/`center=` params (Workstream 3a).
- `technique_A_energy_edge.nexafs_run` docstring (lines ~105-112): drop removed params.
- `_core.goto_sample` docstring: state Position-based movement + frame handling (Workstream 4).

## Part C — qserver / spec bridge docs (forced by C1/C3)
- `recipes_combined.build_axes_from_spec` (`recipes_combined.py:256-319`): the docstring's canonical
  field list must match the code after C3; energy branch comment notes `settle` survives, feedback
  params gone.
- `_qserver.acquire_from_spec` docstring example (`_qserver.py:288-314`) and `nexafs_from_spec`
  (`_qserver.py:401-435`): remove removed params; fix the spatial/energy spec examples to match the
  bridge; keep them consistent with the GUI skill (Part A6 — single source of truth for the spec).

## Part D — PACKAGE_OVERVIEW.md
- §"Filename templating contract" (lines ~292-305): correct on the rule; ADD the `{x}` vs `{piezo_x}`
  trap and a pointer to `skills/naming-and-filename-tokens.md`.
- §"Running with multiple runs open at once" (lines ~339-377): already updated by the multi-run fix
  (per-point staging note). Verify it reads coherently and mentions `multi_sample_run_split`.
- Anywhere energy stepping is described: align with C1.

## Part E — Backend skills
- `skills/legacy-swaxs-patterns.md` (line ~96): "Beam-loss re-seek → `energy_axis(..., flux_signal=,
  flux_threshold=)`" — the re-seek mapping is still valid, but remove any implication that
  `move_energy_fb` manages BPM feedback / does the "set twice"; align energy mapping with C1.
- `skills/composing-smi-experiments.md`: audit for energy `max_step`/feedback prose and `{x}`-style
  token examples; align with C1/C2.
- `skills/naming-and-filename-tokens.md`: already authoritative for C2. After C3 lands, update the
  "BAD: `x{x}`" note to "valid once `spatial_grid_axes` records the `x`/`y` Signal (now the default
  when `center=` is given)".

## Part F — The normative `_analysis` docs (shared with LEGACY plan)
These embed now-wrong recommendations; corrected here, executed alongside `LEGACY_REVIEW_PLAN.md`:
- `templates/_analysis/BEST_PRACTICES_DRAFT.md`:
  - Tenet 9 (line ~237) "Beam-loss re-seek ... and DCM suspenders — keep": reword — re-seek as a
    plan-level guard is fine, but the energy DEVICE now manages feedback/gap; do not tell users to
    hand-manage feedback or set twice.
  - Tenet 6 + Open-question 3 (multi-open-run / arc-economy): UPDATE — no longer an open question;
    the topology works (per-run staging fix). Document `multi_sample_run`/`_split`.
  - Tenet 3 + Open-question 1 (filename templating): add the "tokens must be real recorded keys" caveat
    (the `{x}` trap).
- `templates/_analysis/USE_CASE_TAXONOMY.md`: §5.4 "beam-loss recovery" idiom and §B multi-sample
  framing — reconcile with C1/C5. §8 gold naming already uses full keys (good).
- The factual `legacy_batch_*.md` / `folder_*.md`: light touch — only where they describe the
  re-seek/arc-economy idioms as *desirable* (they are descriptive classification, low priority).

---

## Single-source-of-truth rule (avoid re-drift)
The spec `type`→constructor→fields table currently exists in THREE places that disagree (GUI skill,
`build_axes_from_spec`, `_qserver` examples). **Designate `recipes_combined.build_axes_from_spec`’s
docstring as canonical**, generate/copy the GUI + qserver tables from it, and add a test that the
GUI skill’s documented fields are a subset of what the bridge accepts (or at least a checklist item),
so this mismatch can’t silently return.

## Sequencing
- Parts B/C/D/E that describe **removed energy params** MUST ship in the **same PR** as
  IMPLEMENTATION_PLAN Workstream 1 (no doc may reference a removed param after it's gone).
- Part A (GUI) can be split: A0 (Redis-first) with the holder bridge (Workstream 5) +
  `NAMED_LISTS_PLAN` NL-3/NL-4; A1/A6 with Workstream 1; A2 with Workstream 2; A3 with Workstream 3;
  A4 with Workstream 4; A5 immediately (multi-run already fixed).
- Part F is executed jointly with `LEGACY_REVIEW_PLAN.md`.

## Acceptance
- No doc/skill/example references `max_step`/`fb_settle`/`double_set` or describes
  `move_energy_fb`/`energy_axis` as managing feedback / re-seeking via the device knobs.
- GUI skill has a Filename-tokens section with the `{x}` vs `{piezo_x}` trap and a pointer to the
  naming skill.
- GUI spec field names match `build_axes_from_spec` (no `grid`/`flux_reseek`/`x_step`/`x_n` drift).
- Arc-economy is documented as available (not blocked), with the per-point-staging/stream facts and
  `multi_sample_run_split`.
- Positioning contract (nominal/refined Position, frame) stated in the GUI skill + SAMPLE_SYSTEM.
- One canonical spec table; others derive from it.
- **GUI generates name-referencing calls by default** (samples by holder name; big lists by named-list
  name) — copy-paste position/energy lists are a secondary "one-off" path, not the lead example
  (C6 / `NAMED_LISTS_PLAN.md`).
