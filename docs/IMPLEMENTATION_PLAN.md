# Implementation Plan — backend changes (post-beamtime)

> **Execution order:** see `ROADMAP.md` (the sequencing authority). TL;DR: decisions → code
> (energy-led: WS0→WS1→WS2→WS3→WS6, with WS4/WS5/WS7 in parallel) → docs → legacy.
>
> **Companion docs:** `FIELD_LESSONS_BAR_PLANS.md` (the field evidence + decisions; source of truth),
> `DOC_CORRECTIONS_PLAN.md` (all documentation incl. the GUI), `LEGACY_REVIEW_PLAN.md` (re-annotating
> the legacy scripts). This doc is the **backend code** plan only.
>
> **Status:** plan only — no code written from this doc yet. Beamtime is over; safe to make breaking
> changes with full test coverage.
>
> **Decisions baked in (from FIELD_LESSONS + this session):**
> - Energy stepper: **GUT IT**, and **REMOVE** `max_step`/`fb_settle`/`double_set` entirely
>   (breaking — full caller sweep required, see Workstream 1).
> - Multi-run `UnresolvableForeignKeyError`: **already fixed** by another agent (per-run AreaDetector
>   staging + `multi_sample_run_split` + regression test, uncommitted in the working tree). Only a
>   hardware checkout remains — NOT re-implemented here.
> - Filename tokens must be real recorded data keys; validate in `acquire`.
> - Holder→SampleList Redis bridge: add it, **optional + dict-replaceable**.

## Conventions for every workstream
- **Test first where a bug is being fixed:** reproduce in sim (the `tests/conftest.py` harness +
  `tests/test_multi_sample_assets.py` style), then fix, then assert. No hardware-only claims without a
  sim test or an explicit "hardware checkout" line.
- **Run tests against the source tree**, not the installed package. NOTE (CI hygiene, do first):
  the `smi_plans` installed in the env shadows the working tree — `tests/test_multi_sample_assets.py`
  *fails* under the installed copy and *passes* with `PYTHONPATH=.../smi-plans/src`. Add an editable
  install (`pip install -e .`) or a `conftest`/CI step so tests always bind to source. **Workstream 0.**
- **Message purity** stays enforced (`tests/test_message_purity.py`): no bare `.put()/.get()/.set()`.
- Each workstream lands as its own commit with its tests green.

---

## Workstream 0 — test harness binds to source (prerequisite)
**Why:** the env has a stale installed `smi_plans`; tests can silently run against old code.
- Add editable install to the dev/test setup (`pip install -e .` or pixi task), or a CI step that sets
  `PYTHONPATH` to `src`. Document the run command in `README`/`tests`.
- **Acceptance:** `pytest` with no `PYTHONPATH` hack picks up working-tree changes;
  `test_multi_sample_assets.py` passes from a clean checkout.

---

## Workstream 1 — Energy stepper: gut + REMOVE params (BREAKING) — highest priority
**Goal:** `energy_axis`/`move_energy_fb` do a plain, settle-guarded `bps.mv(energy, E)`; the
device (+ default `energy_move_preprocessor`) owns feedback/gap/harmonic. **Remove**
`max_step`, `fb_settle`, `double_set` from all signatures.

### 1a. Rewrite the primitives — `src/smi_plans/_compose.py`
- `move_energy_fb(target, *, settle=2.0)`:
  ```python
  def move_energy_fb(target, *, settle=2.0):
      target = float(target)
      if abs(target - float((yield from bps.rd(energy))[...])) < ENERGY_TOL:  # skip-if-there guard
          return
      yield from bps.mv(energy, target)
      if settle:
          yield from bps.sleep(settle)
  ```
  (Keep a small `ENERGY_TOL` "already there" guard like `bar_plans.move_energy_step`.)
- `energy_axis(energies, *, settle=2.0, reverse_alternate=False, flux_signal=None,
  flux_threshold=None, max_reseek=3, record_name="energy_set")` — drop `max_step/fb_settle/double_set`.
  Keep the optional flux re-seek (that is a *plan-level* beam-loss recovery and is still valid;
  it just re-issues `bps.mv(energy, E)`).
- Replace the docstrings: state that gap/harmonic/feedback is delegated to the `energy` device,
  validated live (100+ steps; 1516 µm harmonic-crossover jumps all `success=True`); explicitly warn
  against re-adding gap-freeze/accumulate/`abs_set(wait=False)`+`set_finished` (all discarded).

### 1b. BREAKING caller sweep (must land in the SAME change)
Every caller/passer of the removed params, found in the audit:
- `technique_A_energy_edge.py`
  - `nexafs_run(... settle=2.0, fb_settle=5.0, double_set=True ...)` → remove `fb_settle`/`double_set`
    from signature + docstring + the `energy_axis(...)` call.
  - `nexafs_bar(...)` same.
- `recipes_combined.py:build_axes_from_spec` energy branch — currently
  `energy_axis(s["values"], settle=s.get("settle", 2.0), ...)`. `settle` survives; no change beyond a
  comment, but verify it passes nothing removed.
- `_qserver.py:nexafs_from_spec` — `settle=spec.get("settle", 2.0)` survives; remove any
  `fb_settle`/`double_set` plumbing if present; update docstring example.
- `technique_J_xrr.py` (`xrr_resonant_run`) and `technique_N_xpcs.py`
  (`xpcs_resonant_burst_run`) — they call `move_energy_fb`; remove any removed kwargs.
- Grep the whole `src/` + `tests/` for `max_step`, `fb_settle`, `double_set` and fix every hit.
- `_compose.energy_axis`/`move_energy_fb` **callers in tests** — update.

### 1c. Tests
- Update existing technique tests (A/J/N) for the new signatures.
- New sim test: `energy_axis` emits exactly **one** energy `set` (not two) per point — assert the
  message stream has no double-set (this is the regression that proves the gut).
- New sim test: passing a removed kwarg raises `TypeError` (documents the breaking change).
- **Acceptance:** full safe suite green; no reference to the removed params anywhere in `src/`.

### 1d. Hardware checkout (post-merge, when beam returns)
- 100+ eV scan crossing a harmonic boundary (~7469 eV) with the `energy.ivugap.move` logger; confirm
  no `FailedStatus`, gap tracks peak. (Already field-validated via `bar_plans.py`; this confirms the
  backend path is identical.)

---

## Workstream 2 — Filename tokens: validate in `acquire`, no-collision, superset
**Goal:** a bad filename token fails at **build time** with a clear message, not as a post-run
`KeyError`. Also enforce the read-once + superset rules (skip_if_tokens interaction).

### 2a. Token validator in `acquire` — `src/smi_plans/_compose.py`  ✅ DONE
- `validate_name_tokens(sample_name, dets=, reads=, axes=)` + `validate_tokens=True` kwarg on
  `acquire` (escape hatch). Computes the recorded-key set from: axis `record` Signal names (exact),
  `COMMON_TOKENS` (the naming-preprocessor-injected tokens like `{energy_energy}`/`{waxs_arc}`), and
  each readable's `describe()` keys (+ `<device.name>` prefix). Raises a clear `ValueError` naming the
  offending token(s) and pointing at `skills/naming-and-filename-tokens.md`.
- **False-positive safety:** flags a token ONLY when describe() info is available and the token
  matches nothing (so off-beamline / GUI-side construction never spuriously raises; sim-vs-real key
  differences are bridged by `COMMON_TOKENS`).
- **SCOPE (resolved):** the *collision* (token device injected by the profile naming preprocessor AND
  in reads → `Data keys collide`) and the *superset* rule depend on the **profile-side** preprocessor,
  which this package cannot observe — they are enforced beamline-side, NOT here. This validator covers
  the high-value case that caused real post-run failures: a token with **no recorded key at all**
  (the `{x}` vs `{piezo_x}` trap). (Existing `dedup_readables` already handles the in-`acquire`
  duplicate-readable collision, with its own regression test.)

### 2b. Tests  ✅ DONE (`tests/test_name_tokens.py`, 6 tests)
- `{x}` with only `piezo_x` recorded → build-time `ValueError` (the reproduced bug).
- `{piezo_x}` / `{energy_energy}` (COMMON_TOKENS) / `{incident_angle}` (axis record) /
  `{xbpm2_sumX}` (describe prefix) → all accepted, no false positive.
- `validate_tokens=False` → bypasses.
- (Collision/superset sim repro deferred to the profile side — see SCOPE above.)
- **Acceptance:** met; full suite 100 passed.

---

## Workstream 3 — `spatial_grid_axes` records `{x}`/`{y}` Signals (relative offsets)  ✅ DONE
**Goal:** make `{x}`/`{y}` valid tokens by recording a relative-offset `Signal(name="x"/"y")` — the
`incidence_axis`/`{incident_angle}` pattern — so the grid is filename-templatable with a meaningful
relative value. Promotes the field-proven `bar_plans._grid_axes_named` prototype into the backend.

### 3a. API — `src/smi_plans/_compose.py:spatial_grid_axes`  ✅ DONE
- Implemented as decided: `{x}`/`{y}` (relative-offset Signals) are canonical. Signature now:
  ```python
  spatial_grid_axes(*, x_motor=None, x=None, y_motor=None, y=None, center=None,
                    record_relative=True, snake=True, record=True, dose=False, role=None)
  ```
  - With a `center` (scalar or `(cx, cy)`) and `record_relative=True` (default): each dim's axis
    `values` become the relative offsets; `_move` drives the motor to `center + offset` (so absolute
    `piezo_x`/`piezo_y` is still recorded) AND a `Signal(name="x"/"y")` records the offset — `{x}`/
    `{y}` resolve. (Internal `_grid_axis` helper.)
  - No `center` (or `record_relative=False`): backward-compatible absolute mode → token `{piezo_x}`;
    a bare `{x}` then correctly fails the WS2 validator.
  - `role` accepted (advisory) for GUI/spec round-tripping.
- **STILL OPEN (DOC, not code):** the GUI `x_step`/`x_n` vs bridge `x`/`y`/`snake` field-name
  reconciliation — handled in DOC_CORRECTIONS_PLAN, not here.

### 3b. Tests  ✅ DONE (`tests/test_spatial_grid.py`, 4 tests)
- Centered grid records both `x`/`y` (relative) and `piezo_x`/`piezo_y` (absolute); `{x}`/`{y}` resolve.
- Recorded `x` values are the offsets (`-100, 0, +100`), not absolutes.
- No-center → absolute mode (`piezo_x` key, no bare `x`); `{x}` then rejected by the WS2 validator.
- **Acceptance:** met; full suite 104 passed. `bar_plans._grid_axes_named` can be deleted in WS6.

---

## Workstream 4 — Sample positioning from `Position` — `_core.goto_sample`  ✅ DONE
**Goal:** `goto_sample` moves from `runnable_position()` (nominal/refined `Position`), not legacy flat
`piezo_*`. **GUI contract** (coordinate with DOC plan + SAMPLE_SYSTEM).

### 4a. Code — `src/smi_plans/_core.py`  ✅ DONE
- `goto_sample` now reads `runnable_position()` (refined else nominal) first via a new
  `position_moves(position, piezo_dev, stage_dev, *, skip=())` helper, and **falls back** to the
  legacy flat `piezo_moves()`/`hexa_moves()` only when the Position carries no coordinates (old
  in-code samples still work).
- `position_moves` maps `piezo_x/y/z/th` → `piezo.{x,y,z,th}` and the renamed Huber
  `stage_x/y/z/theta/chi/phi` → `stage.{x,y,z,theta,chi,phi}`.
- `goto_sample(..., skip={piezo.y, piezo.th})` excludes alignment-owned axes for grazing (mirrors
  `bar_plans._goto_xz_stage`). `position_moves` exported from `_core`.
- `Position.frame` is carried through the model; moves use the resolved Position as-is (the
  holder→lab transform is deferred per SAMPLE_SYSTEM D4, unchanged here).

### 4b. Tests  ✅ DONE (`tests/test_positioning.py`, 5 tests)
- A `Sample` with ONLY a `nominal` Position (flat fields None) moves (the GUI/spreadsheet case that
  broke on the floor); refined overrides nominal; `skip` excludes an axis; legacy flat fields still
  work; `position_moves` maps `stage_theta/chi/phi` correctly.
- **Acceptance:** met — ledger row "Position-based positioning" is backend-done + sim-tested. Full
  suite 109 passed.

---

## Workstream 5 — Redis holder→SampleList bridge (OPTIONAL, dict-replaceable)  ✅ DONE
**Goal:** the one genuinely new capability — load a holder's samples from the store and run a `*_bar`
from a holder name, with alignment persisted. Built on the typed model + `SampleStore`, **not** ad-hoc
Redis. Promotes `holder_bar.py`.

### 5a. Code — new `src/smi_plans/_holder.py`  ✅ DONE
- `load_holder(holder_name, *, store=None, order_by_slot=True, require=True) -> HolderBar`
  (a `SampleList` subclass carrying `.store`/`.holder`). Resolves the holder by name, orders samples
  by the holder's declared `sample_ids` then slot. `store=None` opens `SampleStore.from_redis()` (the
  ONLY Redis touch, lazy). Missing holder → clear `KeyError` listing available names (or empty bar if
  `require=False`).
- Pure helpers: `get_aligned`/`is_aligned`/`needs_alignment`/`sample_center` (read `refined`/runnable
  Position). Plan helpers: `save_aligned`/`clear_aligned` round-trip through
  `SampleStore.append_alignment`/`update_refined` (bluesky imported lazily inside them). Positioning
  reuses Workstream 4's `_core.goto_sample`/`position_moves` (no duplication).
- Exported from the package top level (`from smi_plans import load_holder, ...`).
- **HARD CONSTRAINTS met:** no Redis import at package import (verified: `redis` not in `sys.modules`
  after `import smi_plans`); off-beamline import/use needs no Redis/secret; `save_aligned` on a
  non-`HolderBar` raises a clear `TypeError`.

### 5b. Tests  ✅ DONE (`tests/test_holder.py`, 7 tests, pure dict backend)
- load+order by slot; missing holder error lists available; `require=False` → empty; `sample_center`;
  alignment save→persist→reload round-trip (other axes preserved); `save_aligned` needs a HolderBar;
  no-Redis-import safety.
- **Acceptance:** met — runs in CI with no Redis; `redis` not a runtime dependency. Full suite 116.

---

## Workstream 6 — Re-thin the user scripts (after 1–5)  ✅ DONE (TARGETED re-thin)
**Goal:** remove the now-duplicated field machinery from `bar_plans.py`/`holder_bar.py` (it lives in
the backend now), keeping the proven scan **structure**.

**Decision:** TARGETED re-thin (keep structure), not a full swap onto `technique_*` bars — the
technique bars don't exactly replicate the field-tuned structure (arc-outer one-run-per-(sample,arc),
arc-aware SAXS drop `ARC_SAXS_BLOCK_DEG`, fresh-spot walk, `{energy_energy}`/`wa{arc}` naming), so a
full swap risked silently changing how scans run. **NL-3** (name-or-list resolution) is done in the
user file (per the decision), needing no backend change.

**Done (in the user-scripts repo `/home/xf12id/SWAXS_user_scripts/`, a SEPARATE git repo):**
- `bar_plans.py`: deleted the local `move_energy_step`/`reliable_energy_axis`/`_energy_now`/
  `ENERGY_TOL_eV` (→ backend `energy_axis`, gutted in WS1), `_grid_axes_named` (→ backend
  `spatial_grid_axes(center=...)` which records `{x}`/`{y}`, WS3), `_goto_xz_stage` (→ backend
  `goto_sample(skip={piezo.y, piezo.th})`, WS4). Imports switched from `holder_bar` to `smi_plans`
  (`load_holder`/`get_aligned`/`needs_alignment`/`save_aligned`/`sample_center` + `_core.goto_sample`)
  and `resolve_list`. The 3 plans + their parameters/prints are unchanged; `energies`/
  `incident_angles` now accept a list OR a stored-list **name** (`resolve_list`).
- `holder_bar.py`: reduced to a thin **compatibility shim** re-exporting the backend bridge (aliases
  `load_holder_bar`→`load_holder`, `goto_runnable`→`goto_sample`).
- **Verified:** both files `py_compile` and **import-resolve** against the new backend (with `src` on
  the path); all 3 plans present. (Full sim-run of the user plans is gated on deploying the backend +
  the live devices; the backend pieces they call are each sim-tested in this repo.)
- **DEPLOYMENT DEPENDENCY (IMPORTANT):** the beamline `terminal` env currently has a STALE installed
  `smi_plans` (no `load_holder`/`resolve_list`). The re-thinned files **will import-fail until the
  updated `smi-plans` is deployed** into that env (an env rebuild / install — owned by beamline
  staff, NOT done here). A header note in `bar_plans.py` states this.

---

## Workstream 7 — Named-lists library + name-or-list resolution  (full design: `NAMED_LISTS_PLAN.md`)
**Goal:** extend the Redis "reference by name" pattern from samples to the other big scan inputs
(edges/energies, incident angles, temperatures, times), so the GUI curates **named lists** and plans
reference them by name. Backward compatible (literals still work).
- **NL-1** `NamedList` pure model + `ListStore` facade (db=2, new prefix `swaxslists`,
  dict-replaceable, lazy Redis). Tests: dict-backend round-trip per kind.
- **NL-2** `resolve_list(value, *, kind, store=None)` (name → store values; literal → as-is; energy
  `spec`→`values` via `technique_A.energy_grid`). Tests: literal passthrough store-free; name resolve;
  spec materialize; clear errors.
- **NL-3** Plumb name-or-list into `technique_A/B/C/E` + `recipes_combined`/`_qserver` list params via
  `resolve_list`. Tests: sim bar with `energies="<name>"` and with a literal, identical results.
- **NL-4/NL-5** GUI Lists panel + session `ListStore` wiring (DOC plan / SAMPLE_SYSTEM addition).
- **Acceptance:** see `NAMED_LISTS_PLAN.md` §8 (dict-backend CI; name-or-list resolution; GUI
  references lists by name; no Redis import at package import).

---

## Sequencing & dependencies
```
0 (harness) ──► everything
1 (energy gut, BREAKING) ── independent; do first (touches most callers/docs/legacy)
2 (token validator) ──► enables 3, de-risks 6
3 (spatial Signals) ── depends on 2 (validator proves it)
4 (positioning) ── independent (GUI contract)
5 (holder bridge) ── independent (pure)
7 (named lists) ── independent (pure, mirrors 5); NL-3 best after the bars are stable
6 (re-thin) ── depends on 1,3,4,5 (and benefits from 7 for named-list calls)
```
Recommended order: **0 → 1 → 2 → 3 → 4 → 5 → 7 → 6**. Pure/independent workstreams **4, 5, 7** can be
done in parallel by another person (all dict-backed, no hardware). Multi-run (old item 3) is **DONE**
(beamline-tested, committed `536ec39`, pushed).

## Cross-doc triggers (what each code change forces elsewhere)
- **Workstream 1 (breaking energy)** → forces edits in `DOC_CORRECTIONS_PLAN.md` (energy docstrings,
  GUI energy spec, qserver/recipes examples) AND `LEGACY_REVIEW_PLAN.md` (the ~530 energy
  annotations + the BEST_PRACTICES "re-seek/feedback — keep" tenet). Do them in the same PR series.
- **Workstream 3 (spatial)** → forces the GUI spec field reconciliation (`x_step/x_n` vs `x/y/snake`)
  and the token guidance update.
- **Multi-run fix (already done)** → forces the GUI/legacy advice to stop calling arc-economy
  "experimental/blocked" (now available) — handled in the DOC + LEGACY plans.
- **Workstream 7 (named lists)** → forces the GUI "Redis-first" reframe (`DOC_CORRECTIONS_PLAN.md`
  Part A0): the GUI references named lists by name and edits them in a Lists panel, instead of
  emitting copy-paste energy/angle/temperature lists. Full design in `NAMED_LISTS_PLAN.md`.

## Acceptance for the whole effort
- Safe test suite green from a clean checkout (Workstream 0).
- No `max_step`/`fb_settle`/`double_set` anywhere in `src/` (Workstream 1).
- `acquire` rejects bad filename tokens at build time (Workstream 2).
- `{x}`/`{y}` are valid, relative-offset tokens (Workstream 3).
- Position-based positioning sim-tested (Workstream 4).
- Holder bridge works against a dict backend in CI (Workstream 5).
- Named-lists library + name-or-list resolution work against a dict backend in CI (Workstream 7).
- `bar_plans.py` is thin (Workstream 6).
- GUI generates name-referencing calls by default (samples + named lists), not copy-paste lists.
- Ledger in `FIELD_LESSONS_BAR_PLANS.md` fully filled; hardware-checkout rows scheduled for next beam.
