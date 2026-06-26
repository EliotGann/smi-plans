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

### 2a. Token validator in `acquire` — `src/smi_plans/_compose.py`
- Compute the set of **keys that will actually be recorded**: each axis `record` Signal name,
  each `<device>_<component>` for devices in `reads`/`dets` (+ the token devices the naming
  preprocessor will inject for recognized tokens like `{energy_energy}`/`{waxs_arc}`).
- Parse `{field}` names out of the final `sample_name` template (the `fname(...)` result + any
  caller `md['sample_name']`).
- **Raise** a clear `ValueError` listing any token with no matching recorded key
  ("filename token {x} has no recorded data key; recorded keys are {...}. Did you mean {piezo_x}, or
  record a Signal(name='x')? See skills/naming-and-filename-tokens.md").
- **Collision check:** raise if a token device is BOTH preprocessor-injected AND in `reads`/an axis
  `record` (the `Data keys ... collide` cause) — reproduce that first in sim.
- **Superset check:** when a custom token-bearing name suppresses the default naming, assert every
  field the default naming would have recorded is still recorded somewhere; warn/raise on silent drop.

### 2b. Tests
- Sim: reproduce `KeyError`-equivalent by building a name with `{x}` while only `piezo_x` is recorded
  → assert `acquire` now raises at build time (before any run).
- Sim: reproduce `Data keys ... collide` (custom `{energy_energy}` + `energy` in reads) → assert the
  validator catches it.
- Sim: a custom name does not lose a field the default name would have recorded.
- **Acceptance:** these three are red before the validator, green after.

---

## Workstream 3 — `spatial_grid_axes` records `{x}`/`{y}` Signals (relative offsets)
**Goal:** make `{x}`/`{y}` valid tokens by recording a relative-offset `Signal(name="x"/"y")` — the
`incidence_axis`/`{incident_angle}` pattern — so the grid is filename-templatable with a meaningful
relative value. Promotes the field-proven `bar_plans._grid_axes_named` prototype into the backend.

### 3a. API — `src/smi_plans/_compose.py:spatial_grid_axes`
- **DECIDED (cross-cutting):** `{x}`/`{y}` (relative-offset Signals) are the **canonical** spatial
  filename tokens. `spatial_grid_axes` records a `Signal(name="x"/"y")` = relative offset by default;
  the GUI + legacy advice teach `{x}`/`{y}`. (Absolute `{piezo_x}` remains available but secondary.)
- New signature recording relative offsets and (per SAMPLE_SYSTEM_PLAN §7.2/§10.4) gaining role tags:
  ```python
  def spatial_grid_axes(*, x_motor=None, x=None, y_motor=None, y=None,
                        center=None, snake=True, record=True,
                        record_relative=True, role=None):
  ```
  - `record_relative=True` (default) with a `center` (or per-axis center): each axis moves the
    absolute motor (still recorded as `piezo_x`/`piezo_y`) AND records `Signal(name="x"/"y")` =
    relative offset (`value - center`). Tokens `{x}`/`{y}` resolve — **the canonical path**.
  - Absolute `{piezo_x}` mode stays available (`record_relative=False`) for callers that want it;
    document which token each mode yields. The GUI defaults to `{x}`/`{y}`.
  - Add `role="spatial_x"/"spatial_y"` tags if/when the spec needs them (coordinate with GUI plan).
- Reconcile with the **spec field names**: `build_axes_from_spec`/`_qserver` use `x`/`y`/`snake`
  (lists). Decide whether the GUI's `x_step`/`x_n` shorthand is supported here or only in the spec
  bridge (see DOC plan — there is an existing GUI-vs-bridge field mismatch to fix regardless). [This
  sub-point is a DOC/spec reconciliation, NOT a token decision — still open, see ROADMAP.]

### 3b. Tests
- Sim: a grid built via `spatial_grid_axes(..., center=..., record_relative=True)` emits an `x`/`y`
  data key; assert `{x}`/`{y}` resolve in the templated name (ties to Workstream 2's validator).
- Sim: snake/`reverse_alternate` still works; absolute `{piezo_x}` mode still works.
- **Acceptance:** `bar_plans._grid_axes_named` can be deleted in favor of the backend (Workstream 6).

---

## Workstream 4 — Sample positioning from `Position` — `_core.goto_sample`
**Goal:** confirm/repair that `goto_sample` moves from `runnable_position()` (nominal/refined
`Position`), not legacy flat `piezo_*`. **GUI contract** (coordinate with DOC plan + SAMPLE_SYSTEM).

### 4a. Code — `src/smi_plans/_core.py`
- Audit `goto_sample`: if it uses `sample.piezo_moves()`/flat fields, switch to
  `runnable_position()` → `position_moves(...)` (add a `position_moves` helper mirroring
  `bar_plans._goto_xz_stage`/`holder_bar.position_moves` if missing).
- Support excluding alignment-owned axes (piezo y/th) for grazing (a `skip=` set or a variant), as
  `bar_plans._goto_xz_stage` does.
- Honor the `Position.frame` (`holder`/`lab`) — match exactly what the GUI writes (SAMPLE_SYSTEM D1/D21).

### 4b. Tests
- Sim: a `Sample` with ONLY a `nominal` Position (flat fields None) still moves — the exact
  GUI/spreadsheet case that broke on the floor.
- Sim: refined overrides nominal (`runnable_position()` precedence).
- **Acceptance:** ledger row "Position-based positioning" flips to backend-done + sim-tested.

---

## Workstream 5 — Redis holder→SampleList bridge (OPTIONAL, dict-replaceable)
**Goal:** the one genuinely new capability — load a holder's samples from the store and run a `*_bar`
from a holder name, with alignment persisted. Built on the typed model + `SampleStore`, **not** ad-hoc
Redis. Promotes `holder_bar.py`.

### 5a. Code — new `src/smi_plans/_holder.py` (loader) + `_core` (move/align helpers)
- `load_holder(holder_name, *, store) -> SampleList` (store is any `MutableMapping`-backed
  `SampleStore`; Redis only via `SampleStore.from_redis`, lazy import, `[beamline]` extra).
- Alignment helpers: `get_aligned`/`save_aligned`/`needs_alignment` round-tripping through
  `SampleStore.append_alignment`/`update_refined`. `goto_runnable`/`sample_center` (or reuse
  Workstream 4's helpers).
- **HARD CONSTRAINTS:** no Redis import at package import; importing/running off-beamline must not
  require Redis or the secret; using the bridge without a configured backend fails gracefully with a
  clear message (not an import error).

### 5b. Tests (pure, no hardware/Redis/secret)
- `test_store.py`-style: round-trip holder→SampleList, alignment get/save, needs_alignment, all
  against a **dict** backend (the primary tested path).
- **Acceptance:** runs in CI with no Redis; `redis` not a runtime dependency.

---

## Workstream 6 — Re-thin the user scripts (after 1–5)
**Goal:** `bar_plans.py` becomes ~5-line wrappers over `technique_*` bars + the holder bridge; the
field idioms now live in the backend.
- Rewrite each `bar_plans.py` plan: `load_holder(...)` → call `technique_A.nexafs_bar` /
  `technique_B.giwaxs_bar` / `technique_E.transmission_bar`. Energies/arcs/grid/exposure as kwargs
  (and, once Workstream 7 lands, the list params can be **named lists**, e.g. `energies="Fe_K_XANES"`).
- Delete `bar_plans._grid_axes_named` (superseded by Workstream 3) and the local
  `move_energy_step`/`reliable_energy_axis` (superseded by Workstream 1).
- Reduce `holder_bar.py` to a thin re-export of the backend bridge, or delete it.
- **Acceptance:** `bar_plans.py` contains intent only (which holder/energies/arcs/exposure), zero
  operational idioms; a sim run of each rewritten plan produces a well-formed run with correct tokens.

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
