# Field Lessons — backing real-scan fixes into the backend

> **Purpose:** capture what it actually took to make the `technique_*` bar plans run on real SMI
> hardware, so we can iterate the backend until it behaves like the field-validated user scripts.
> The source of truth is the working user code at
> `/home/xf12id/SWAXS_user_scripts/bar_plans.py` + `holder_bar.py`, which currently runs correct
> transmission / NEXAFS / GIWAXS energy scans on the beamline.
> **Status:** PLAN ONLY — no backend code changed yet. Scope agreed: plan now, implement later
> (not mid-beamtime).
>
> **Framing (important):** `bar_plans.py` is **not** a reinvention of `technique_A/B/E`. We *started*
> on those backend bars and had to change things because **several backend paths had never been run
> against real hardware**. So `bar_plans.py` is the *record of the corrections*. The job here is to
> fold those corrections back into the backend, one tested change at a time, then re-thin the user
> scripts onto the backend.

---

## TL;DR — what the field proved

1. **The backend energy stepper (`move_energy_fb` / `energy_axis`) was the origin of the first real
   error.** Real energy scans started on `technique_*`, which use `energy_axis` → `move_energy_fb`.
   That path failed on hardware. The working fix turned out to be **the plain `bps.mv(energy, E)`**
   path through the beamline `energy` pseudo-positioner (which moves bragg + DCM gap + IVU gap
   together and keeps the undulator gap on the flux peak). Validated live: 100+ small steps and even
   1516 µm harmonic-crossover gap jumps all complete `success=True`.

2. **`move_energy_fb`'s design is questionable on this hardware.** It deliberately turns DCM
   pitch/roll feedback OFF and **double-sets** energy ("command it twice to land"). On the live
   machine the plain device move is reliable *with feedback managed by the device itself*; the
   backend's manual feedback-off + double-set is both unnecessary and a likely source of the
   instability. **`move_energy_fb` was never validated against the real `energy` device + the
   `energy_move_preprocessor` that is installed by default.**

3. **The undulator gap is not modeled in the backend at all** — it's delegated to the `energy`
   device. That delegation is correct, but it means `move_energy_fb`'s sub-stepping logic is
   redundant with (and can fight) the device's own gap/harmonic handling.

4. **The `{energy_energy}` / scan-naming collision is real and must be handled in the bars.** When
   energy is both injected by the scan-naming preprocessor (for the `{energy_energy}` filename
   token) *and* read by the plan, `trigger_and_read` raises `Data keys ... collide`. The working
   rule: the plan reads energy **once** — let the naming preprocessor inject it; do **not** also put
   `energy` in `reads` and do **not** `record=True` on the energy axis. `technique_A.nexafs_run`
   encodes this in its `name_tokens` defaults, but the other bars do not, and the interaction was
   not tested.

5. **One run per `(sample, arc)`; arc is the OUTER loop.** Putting multiple WAXS arcs inside one run
   produced `UnresolvableForeignKeyError`. **UPDATE (resolved — see §3):** the root cause was
   identified as classic-ophyd **AreaDetector Resource/Datum ownership**, NOT a fundamental limit on
   concurrent open runs. A separate agent fixed `multi_sample_run` to stage detectors **per
   (sample, slow-position) point inside each run key** so each run owns its Resource, added a
   conservative `multi_sample_run_split` fallback, and added regression tests. So both topologies are
   now supported: one-run-per-(sample,arc) (the field-validated default) AND arc-economy
   (`giwaxs_bar_arc_economy`, now un-blocked).

6. **Samples from the GUI/Redis store coordinates in `nominal`/`refined` `Position`, not the legacy
   flat `piezo_*` fields.** Movement helpers must use `runnable_position()` / the Position, not
   `sample.piezo_moves()`. `goto_sample` in `_core` needs to be confirmed to do this.

7. **A Redis "holder name → run" bridge is missing from the backend.** The valuable, genuinely
   reusable thing in the user code is `holder_bar.py`: load a holder's samples from Redis db=2 and
   run a `*_bar` directly from a holder name, with alignment persisted back to Redis. The backend
   has `SampleStore.from_redis` + the `_samples` model but **no thin loader** that turns a holder
   name into a `SampleList` ready for `acquire_bar`.

8. **`spatial_grid_axes` is a filename-token trap → caused a real `KeyError('x')`.** It builds
   `motor_axis("x", piezo.x, ...)` whose axis *name* is `"x"` but whose **recorded data key is
   `piezo_x`** (ophyd prefixes a component key with the parent device name). A user who writes the
   natural token `x{x}` references a key `x` that does not exist; the post-run file-naming/symlink
   workflow (`smi-workflows/linker.py`, `.format(**single_doc_data)`) then raises `KeyError('x')`
   **after the data is already taken**. The axis name and the data key must not be conflated. See
   the new skill `skills/naming-and-filename-tokens.md`. The user-side immediate fix records a
   `Signal(name="x")`/`Signal(name="y")` holding the *relative* grid offset (the
   `incidence_axis`/`{incident_angle}` pattern), so `{x}`/`{y}` resolve to a real key.

---

## The field-validated behaviors (what the backend must match)

These are the concrete behaviors in the working `bar_plans.py` that the backend currently does NOT
do (or does differently/untested). Each is a backend change item below.

### A. Energy stepping
- **Working:** `move_energy_step(E)` = `yield from bps.mv(energy, E)`. Nothing else. The device moves
  bragg + DCM gap + IVU gap together; the IVU gap stays on the flux peak at every step; harmonic
  changeovers handled by the device. Optional `settle` sleep after each step. Skip the move if
  already within `ENERGY_TOL_eV` (0.05 eV).
- **Backend now:** `move_energy_fb` does feedback-OFF → mv → settle → **double mv** → feedback-ON →
  `fb_settle`, and sub-steps any jump > `max_step` (50 eV). **Untested on hardware; feedback-off +
  double-set is the suspected original failure.**
- **History note:** the user code went through several *wrong* intermediate fixes before landing on
  the plain `bps.mv` — `abs_set(energy, wait=False)` + manual status poll + `st.set_finished()`
  (this itself produced a `FailedStatus`/`_finished(success=False)` because the RunEngine's
  per-status `done_callback` raises independently of any poll), then an IVU-freeze
  (`enableivu=False`), then a gap-accumulate scheme. **All of those were discarded.** The lesson is
  *do less*: let the device + its preprocessor do the move. Do not reintroduce manual status
  handling or gap freezing.

### B. Scan-naming / read-once
- **Working:** `name_tokens=["{energy_energy}eV", f"wa{arc:04.1f}", ...]`; energy/waxs are read ONCE
  by the naming preprocessor; the plan's `reads` exclude energy and waxs; the energy axis uses
  `record=False`.
- **Backend now:** `nexafs_run` gets this right via defaults; `giwaxs_run` / `transmission_run` were
  not exercised with the `{energy_energy}`/`{waxs_arc}` tokens against the live naming preprocessor.

### C. Run topology
- **Working:** arc is the outermost loop; ONE run per (sample, arc). SAXS detector dropped below an
  arc threshold (`ARC_SAXS_BLOCK_DEG = 15`) because it's occluded.
- **Backend now:** `giwaxs_bar` does one run per sample (arc inside); `giwaxs_bar_arc_economy` does
  arc-once via `multi_sample_run`. The `UnresolvableForeignKeyError` that forced one-run-per-arc was
  **not** seen in sim — it's a real-hardware/real-broker behavior. Need to confirm which topology is
  safe on the real document store.

### D. Sample positioning
- **Working:** position from `runnable_position()` (refined else nominal `Position`); for GIWAXS,
  move coarse x/z + full stage but NOT piezo y/th (those come from alignment); aligned th0 + y
  persisted to and reloaded from Redis.
- **Backend now:** `goto_sample` exists; needs confirming it reads the Position (not flat fields) and
  that alignment persistence round-trips through `SampleStore.append_alignment`/`update_refined`.

### E. Redis holder bridge
- **Working:** `holder_bar.py`: `load_holder_bar(holder_name)` → samples; `get_aligned`/`save_aligned`
  /`needs_alignment` against Redis db=2 (prefix `swaxssamples`); `goto_runnable`/`sample_center`.
- **Backend now:** nothing turns a holder name into a runnable `SampleList`. This is the one piece to
  **add** (not just reconcile).

---

## Proposed backend changes (ordered; each independently testable)

> Principle: **every change is driven by a field-validated behavior above, and gets a sim test +,
> where the failure was hardware-only, a hardware checkout step.** Do NOT port user code verbatim;
> port the *behavior*, with tests.

### 1. Gut the energy stepper — `_compose.move_energy_fb` / `energy_axis`  **(highest priority)**  ✅ DECIDED: GUT IT
- **DECISION (confirmed):** gut the body. The profile `energy` device now behaves correctly
  (manages its own DCM feedback, IVU gap, and harmonic), so the extra backend machinery
  (feedback-off + double-set + sub-stepping) **is no longer needed and is the suspected cause of the
  original failure.** Remove it.
- Replace the body with the field-validated path: a plain `bps.mv(energy, E)` (the device + the
  default `energy_move_preprocessor` own feedback, gap, harmonic, and large-move sub-stepping). Keep
  `settle` and the "skip if within tol" guard.
- **Keep the name** `move_energy_fb` (gut-and-keep, not deprecate) so `technique_A/J/N` and
  `energy_axis` keep importing it; keep the public signature/`record_name` of `energy_axis`
  unchanged. The retained params (`max_step`, `fb_settle`, `double_set`) become **accepted-but-
  ignored no-ops** with a docstring note (so existing callers don't break); consider a one-time
  `DeprecationWarning` if any of them are passed non-default.
- **Fold in the gap learnings:** docstring section stating the gap/harmonic behavior is delegated to
  the `energy` device and validated (100+ steps; 1516 µm harmonic-crossover jumps all
  `success=True`), so nobody re-adds gap-freezing/accumulate/`set_finished` logic. Explicitly warn
  against the discarded approaches (see History note in §A).
- **Tests:** sim test that `energy_axis` emits exactly one energy `set` + one read per point and **no
  double-set** (assert the message count drops vs. the old behavior); message-purity already covered.
  **Hardware checkout:** a 100+ eV scan crossing a harmonic boundary (e.g. through ~7469 eV) with the
  `energy.ivugap.move` logger attached.

### 2. Make all energy/arc bars read-once AND naming-aware — `technique_B`, `technique_E`  ✅ DECIDED: YES
- Audit `giwaxs_run` / `transmission_run` / `*_bar` so that when `{energy_energy}` / `{waxs_arc}`
  tokens are used, energy/waxs are NOT also in `reads` and the relevant axes are `record=False`.
- **CRITICAL naming interaction (confirmed by user):** the scan-naming preprocessor uses
  `skip_if_tokens` — **when it sees a custom name that already contains `{tokens}` it does NOT apply
  the automatic name AND only injects reads for the token devices in that custom name** (it skips its
  default behavior). So the rule is two-sided:
  - If we rely on the preprocessor to inject the energy/waxs read (for `{energy_energy}`/`{waxs_arc}`),
    we must put those tokens in the custom name — and must NOT also read them in the plan (collision).
  - If a needed read would have been auto-injected by the *default* naming path but our custom name
    drops it, **we must recreate that read ourselves** (in the plan's `reads` or via an axis
    `record`), because the custom-name branch suppressed the auto path. i.e. custom naming must be a
    *superset* of what auto-naming would have recorded — nothing silently lost.
- **Centralize so it can't regress:** a single seam (likely inside `acquire`) that, given the final
  name's tokens + the plan's `reads`/axes, (a) asserts no device is read twice (token-injected AND in
  reads), and (b) asserts every device the *default* naming would have recorded is still recorded
  somewhere when a custom name is used. Make a collision OR a silent-drop a test failure, not a
  runtime surprise.
- **Tests:** sim test reproducing the `Data keys ... collide` first (custom `{energy_energy}` +
  energy-in-reads), then asserting the fix; and a sim test that a custom name does not *lose* a
  field the auto name would have recorded.

### 3. Multi-arc / multi-simultaneous-run topology — `technique_B`  ✅ DONE (committed `536ec39`, pushed; **beamline-tested + confirmed working**)
- **ROOT CAUSE (found & fixed):** the `UnresolvableForeignKeyError` was **NOT** a fundamental limit
  on concurrent open runs. It was classic-ophyd **AreaDetector Resource/Datum ownership**: staging a
  file-plugin detector once across N simultaneously-open runs sends the single `Resource` document to
  only the *first* run that reads it; the other runs emit `Datum`s referencing an unknown Resource →
  foreign-key error.
- **FIX (in the working tree — `_core.py`, `technique_B_grazing.py`, `PACKAGE_OVERVIEW.md`, new
  `tests/test_multi_sample_assets.py`):**
  - `multi_sample_run` now stages detectors **per (sample, slow-position) point, inside each run
    key** (`stage_wrapper` within `set_run_key_wrapper`), so each run owns its Resource/Datum. The
    slow axis still moves only once per slow position. `dets` may now be a callable
    `dets(sample, slow_value)` (arc-aware), and `reads` is a new kwarg.
  - New `multi_sample_run_split` — conservative fallback: slow axis outermost, but one **independent**
    run per (sample, slow-position) (no concurrent open runs at all).
  - `giwaxs_bar_arc_economy` rewritten to use per-point arc-aware dets (`_arc_dets_at`) and write each
    arc to its **own stream** (`arc0`, `arc20`, …) so low-angle WAXS-only vs high-angle SAXS+WAXS data
    don't share a sparse primary stream.
  - Regression test reproduces the bug with an `ExternalAssetDetector` emitting Resource/Datum docs
    and asserts every Event's datum references a Resource **owned by the same run**. (Verified
    passing against the source tree.)
- **Status now:** both topologies are supported. The field-validated default remains **one run per
  (sample, arc)**; **arc-economy is no longer blocked** — it is a sanctioned option again.
- **REMAINING follow-ups (do NOT skip):**
  - **Hardware checkout: DONE** — `multi_sample_run` / arc-economy has been run on the real beamline
    and confirmed working (the foreign-key error is gone end-to-end). Committed `536ec39`, pushed.
  - Decide whether `multi_sample_run_split` should be the default the GUI offers (simplest, no
    concurrent runs) vs full arc-economy.
  - Re-point the GUI doc + legacy advice: arc-economy is **available again** (not "experimental /
    blocked"), but recommend it only where arc travel dominates (see Doc/GUI plan).
  - `MULTIRUN_RESOURCE_DOC_PLAN.md` is **not needed**; this item is fully closed.
- Bake the arc-aware detector drop (`ARC_SAXS_BLOCK_DEG` / `arc_block_deg`) into giwaxs/transmission
  det selection — partly done in `_arc_dets_at`; confirm the transmission bars use the same.


### 4. Confirm sample positioning uses `Position` — `_core.goto_sample`  ✅ DECIDED: YES (GUI tie-ins)
- Verify `goto_sample` moves from `runnable_position()` (nominal/refined), not legacy flat fields,
  and that GIWAXS positioning can exclude piezo y/th (alignment-owned). Add a `position_moves`-style
  helper if missing.
- **GUI tie-in (confirmed):** the `nominal`/`refined` Position is exactly what the sample GUI writes;
  this item is the contract between the GUI's stored coordinates and what the plans actually move.
  Coordinate with the GUI builder skill/spec (`skills/smi-plans-gui-builder.md`,
  `SAMPLE_SYSTEM_PLAN.md`) so the field names + frame (`holder`/`lab`) the GUI writes match what
  `goto_sample`/`position_moves` read. Any rename or frame handling must be done on both sides.
- **Tests:** sim test with a Sample that has ONLY a `nominal` Position (flat fields None) — assert it
  still moves. (This is exactly the GUI/spreadsheet sample case that bit us.)

### 5. Add the Redis holder bridge — new thin backend entry  **(the one genuinely new piece)**  ✅ DECIDED: YES, OPTIONAL + DICT-REPLACEABLE
- Add a small loader (likely in `_store.py` or a new `_holder.py`): `holder_name` → `SampleList`
  ready for `acquire_bar`, plus alignment get/save that round-trips through the existing
  `SampleStore.append_alignment`/`update_refined`. Mirror `holder_bar.py`'s
  `load_holder_bar`/`get_aligned`/`save_aligned`/`needs_alignment`/`goto_runnable`/`sample_center`,
  but built on the typed model + `SampleStore`, not ad-hoc Redis access.
- **HARD CONSTRAINTS (confirmed by user):**
  - **Redis is LOCAL-ONLY / beamline-only** — it requires the local secret
    (`/etc/bluesky/redis.secret`), so it is only reachable at the beamline. The bridge must
    therefore be **strictly optional**: importing `smi-plans` and running plans off-beamline (CI,
    GUI dev, a laptop) must NOT require Redis or the secret.
  - **No hard dependency that can't be replaced with a dict.** The bridge operates on a
    `MutableMapping` backend (exactly like `SampleStore` already does); `from_redis` stays the only
    place `redis` is imported, and it imports lazily. **All logic and all tests must work against a
    plain `dict`/in-memory backend.** `redis` must NOT become a runtime install dependency (stays in
    the `[beamline]` extra at most).
  - Fail gracefully with a clear message if a holder bridge is used without a backend configured
    (don't import-error the whole package).
- **Tests:** pure `test_store.py`-style tests with a **dict** backend; no hardware, no Redis, no
  secret. The dict-backend path is the *primary* tested path, not a fallback.

### 6. Make filename tokens safe-by-construction — `_compose.spatial_grid_axes` / `motor_axis` / `acquire`  ✅ NEW (from KeyError('x'))
- **Root trap:** `spatial_grid_axes` builds `motor_axis("x", piezo.x, ...)` — axis name `"x"`, but the
  recorded data key is `piezo_x`. A `{x}` token then has no key → `KeyError('x')` in the downstream
  naming/symlink workflow, **after** data is taken.
- **Backend fixes (pick per review):**
  - `spatial_grid_axes` should record a relative-offset `Signal` named `x`/`y` (so `{x}`/`{y}`
    resolve to the *relative* grid offset, the `incidence_axis`/`{incident_angle}` pattern), OR take
    explicit absolute positions + center and record the offset. Either way the token key must exist.
  - In `acquire`: **validate `name_tokens` against the keys that will actually be recorded** (axis
    `record` Signal names + `<device>_<component>` for `reads`/`dets`) and raise a clear build-time
    error listing any token with no matching key — turn a post-run `KeyError` into an immediate,
    actionable failure. (Pairs with the §2 collision/superset checks — same seam.)
  - Document `COMMON_TOKENS` + the device→key prefixing rule prominently; the new skill
    `skills/naming-and-filename-tokens.md` is the reference.
- **Tests:** sim test asserting every `{token}` in a built `sample_name` is present in the emitted
  primary event's `data` keys (reproduce `KeyError('x')` with the old `spatial_grid_axes`, then
  assert the fix); a unit test of the `acquire` token-vs-recorded-keys validator.

### 7. Re-thin the user scripts (after 1–6 land)
- Rewrite `bar_plans.py` so each plan is ~5 lines: load holder via the backend bridge → call the
  corresponding `technique_*` bar. Goal: the user file carries only *intent* (which holder, which
  energies/arcs/exposure), zero operational idioms.
- Delete `holder_bar.py` once its behavior lives in the backend (or reduce it to a re-export).
- The local `_grid_axes_named` helper added to `bar_plans.py` (the KeyError('x') immediate fix) is
  superseded once `spatial_grid_axes` records the `{x}`/`{y}` Signals in the backend (item 6).

---

## Decisions log
**Resolved (this session):**
- **#1 `move_energy_fb` fate:** ✅ **GUT IT** (gut-and-keep the name; the profile `energy` device now
  handles feedback/gap/harmonic; retained params become no-ops).
- **#2 read-once + naming:** ✅ **YES**, with the two-sided rule — custom naming must be a *superset*
  of auto-naming's recorded fields (recreate any read auto-naming would have injected; never read a
  token device twice).
- **#3 arc economy / multi-run:** ✅ **RESOLVED** (fixed by another agent) — root cause was
  AreaDetector Resource/Datum per-run ownership, not concurrent runs. `multi_sample_run` stages dets
  per-point inside each run key; `multi_sample_run_split` added as a fallback; regression test passes.
  Default stays one-run-per-(sample,arc); **arc-economy un-blocked**. **Beamline-tested + committed
  `536ec39` + pushed — fully closed.**
- **#4 positioning:** ✅ **YES** — and it's a **GUI contract** (coordinate field names/frame with the
  GUI + SAMPLE_SYSTEM_PLAN).
- **#5 holder bridge:** ✅ **YES, but OPTIONAL** — Redis is local/secret-gated; backend operates on a
  `MutableMapping`; `redis` stays a lazy, `[beamline]`-extra import; **dict backend is the primary
  tested path**; no un-dict-replaceable dependency.

**Still open (need a human call):**
- **Where the holder bridge lives:** extend `_store.py`, or a new `_holder.py` + `goto`/alignment
  helpers in `_core`. (Leaning `_holder.py` for the loader + `_core` for the move/align helpers.)
  *(Local to Workstream 5 — does not block other workstreams.)*

**Resolved (this round):**
- **Project/proposal metadata:** ✅ **SESSION CONTEXT** — set once per session/visit (like the QS
  data-session), plans/bars read it; NOT a per-call kwarg. Matches SAMPLE_SYSTEM_PLAN ("proposal/
  project is a session concern, not a sample fact"). The GUI sets it once. Bars drop the `project=`
  kwarg in favor of reading session context (coordinate with the QS data-session plumbing). This
  touches bar signatures + GUI + QS — sequence in the ROADMAP.
- **Canonical spatial token:** ✅ **`{x}`/`{y}` relative-offset Signals** (see IMPLEMENTATION_PLAN
  §3a). Drives GUI + legacy wording.

## What is correctly user-configurable (do NOT bake in)
- Energies, arcs, incident angles, grid size/spacing, exposure time, fresh-spot step/direction,
  detector on/off. These stay as `*_run`/`*_bar` kwargs — the bars already expose them.

## Companion docs to create
- ~~`MULTIRUN_RESOURCE_DOC_PLAN.md`~~ — **NO LONGER NEEDED.** The `UnresolvableForeignKeyError` was
  root-caused (AreaDetector Resource/Datum per-run ownership) and fixed in `_core.multi_sample_run`
  (+ `multi_sample_run_split` fallback + regression test). Remaining work is a single hardware
  checkout line in the ledger, not a separate blocker doc.

## Verification ledger (fill in as we go)
| Behavior | Field-validated? | Sim test? | Backend done? | Notes |
|---|---|---|---|---|
| Energy step = plain `bps.mv(energy,E)` | YES (live, 100+ steps, harmonic xover) | no | no | §1 gut `move_energy_fb` |
| Gap tracks peak / harmonic xover OK | YES (live) | n/a (device) | n/a | delegated to profile `energy` |
| `{energy_energy}` read-once (no collision) | YES (live) | no | partial (`nexafs_run` only) | §2 |
| Custom name ⊇ auto-name recorded fields | needs confirming | no | no | §2 skip_if_tokens superset rule |
| One run per (sample, arc) | YES (live; avoided UnresolvableForeignKey) | no | no | §3 safe default |
| Multi-open-run w/ staging (arc economy) | **YES (beamline-confirmed)** | **YES** (`test_multi_sample_assets.py`) | **YES** (committed `536ec39`, pushed) | §3 DONE |
| Position-based positioning (nominal/refined) | YES (live) | YES (`test_positioning.py`) | YES (`goto_sample` reads runnable Position) | §4 GUI contract |
| Redis holder → SampleList bridge | YES (live, `holder_bar.py`) | no | no (missing) | §5 optional, dict-replaceable |
| Filename token = real recorded key (no KeyError) | YES (live: KeyError('x') hit + fixed user-side) | no | no | §6 fix `spatial_grid_axes` + validate in `acquire`; skill written |
| Filename token = real recorded key (no KeyError) | YES (live: KeyError('x') hit + fixed user-side) | no | no | §6 fix `spatial_grid_axes` + validate in `acquire`; skill written |
