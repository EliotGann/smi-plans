# Named Lists Library — design & plan (shared scan inputs: edges, angles, temperatures, times)

> **Execution order:** see `ROADMAP.md`. Named lists is **Workstream 7 — an independent parallel
> track** in Phase 1 (pure, dict-backed, blocks nothing; none of its open questions affect other
> workstreams). Build it whenever convenient alongside the energy/token work.
>
> **Companion docs:** `SAMPLE_SYSTEM_PLAN.md` (the sibling store this mirrors), `IMPLEMENTATION_PLAN.md`
> (backend workstreams), `DOC_CORRECTIONS_PLAN.md` (GUI), `FIELD_LESSONS_BAR_PLANS.md`.
>
> **Status:** design + plan only — no code yet.
>
> **Motivation (from the field):** the Redis sample store proved to be an excellent GUI↔profile
> channel — the user references samples/holders **by name**, with zero copy-paste of coordinate
> lists. We want the **same pattern for the other big lists** in scans: energies (edges), incident
> angles, temperatures, exposure-time lists. Instead of the GUI building a list the user pastes into
> bluesky, the GUI **curates a named library of reusable lists** (Redis-backed, view/edit/add in the
> GUI) and plans **reference them by name** — exactly like sample positions. This makes the GUI's job
> "curate named inputs," not "generate copy-paste code," and makes calls read as intent:
> `nexafs_bar(holder="bar1", energies="Fe_K_XANES")`.

## Decisions (confirmed)
- **D1 — General typed library.** One library holds typed named lists of several **kinds**:
  `energy` (edge), `incidence` (angles), `temperature` (setpoints), `time` (exposure/period lists),
  and is **extensible** to new kinds without schema changes.
- **D2 — Same Redis db=2, new prefix, new facade.** Co-located with samples (one connection), under a
  distinct key prefix, via a new pure-Python `ListStore` facade mirroring `SampleStore`.
  **Dict-replaceable** (Redis lazy, `[beamline]` extra; tests/offline use a plain dict).
- **D3 — Name-or-list resolution in plans.** A plan parameter accepts EITHER a name (str → resolved
  from the store) OR an explicit sequence (used as-is). Backward compatible: power users pass a
  literal; the GUI passes a name.

---

## 1. Data model (pure Python, GUI-safe — lives in `_samples.py` or a new `_lists.py`)

A single typed dataclass, JSON round-trippable like `Sample`:

```python
@dataclass
class NamedList:
    name: str                      # unique within its kind, human-referenceable
    kind: str                      # "energy" | "incidence" | "temperature" | "time" | ...
    values: list[float] | None = None   # the explicit list (source of truth if set)
    # OPTIONAL generator spec (so the GUI's list-builder is reproducible/editable):
    spec: dict | None = None       # e.g. energy edge: {"edge": 7112, "pre":[-30,-2,5],
                                   #      "near":[-2,2,0.25], "post":[2,60,5]}
    units: str | None = None       # "eV" | "deg" | "C" | "s" (advisory)
    id: str = <uuid4>
    md: dict = {}                  # notes, provenance, created/edited, who
```

- **`values` is authoritative when present.** `spec` is the *recipe* the GUI used to build them
  (kept so the entry is editable in the GUI without losing intent). A resolver returns `values` if
  set, else **materializes** `values` from `spec` via the kind's builder (see §3).
- **Why store both:** mirrors how the sample model keeps `nominal` (intent) + a runnable cache. The
  GUI shows/edits the `spec` (e.g. edge + pre/near/post) and the materialized `values`; plans get
  `values`.
- **Kinds are open**: `kind` is a string; unknown kinds still store/resolve their explicit `values`
  (only the *generator* is kind-specific). Adding "rh" or "potential" later needs no schema change.

> Reuse opportunity: `technique_A.energy_grid(edge, pre, near, post)` already builds an edge array.
> The `energy` kind's `spec`→`values` builder should BE `energy_grid` (single source of truth), so
> the GUI edge-builder and the stored entry produce identical arrays.

## 2. Storage — `ListStore` facade (mirrors `SampleStore`, `_store.py`)

- New facade `ListStore(backend)` over any `MutableMapping`; `ListStore.from_redis(...)` opens the
  **same db=2** connection style as `SampleStore.from_redis` (lazy `redis`/`redis_json_dict`,
  `[beamline]` extra, local secret).
- **Key layout** (distinct prefix so it never collides with `swaxssamples`):
  - Option A (separate connection-prefix): a `RedisJSONDict(prefix="swaxslists")` →
    `swaxslists` + `list:<kind>:<name>` etc.
  - Option B (shared connection-prefix `swaxssamples`, namespaced dict-keys): `list:<kind>:<name>`.
  - **Recommend Option A** (own prefix `swaxslists`) so the two libraries are independently
    browsable/prunable and a sample export never drags lists along. Same physical db, clean logical
    split.
  - Keys: `list:<kind>:<id> -> NamedList.to_dict()`, `index:<kind>:name -> {name: id}`,
    `schema_version -> int`.
- CRUD mirrors `SampleStore`: `put_list`/`get_list(name, kind)`/`find_list`/`list_lists(kind=None)`/
  `delete_list`; `export_tables`/`export_csv`/`prune` (refuse without prior export, like samples).
- **Constraints (same as the holder bridge):** no Redis import at package import; importing/running
  off-beamline must not require Redis/secret; dict backend is the **primary tested path**.

## 3. Resolution — `resolve_list(value, *, kind, store=None)` (the name-or-list seam)

A single helper used by every plan/axis that takes a list:
```python
def resolve_list(value, *, kind, store=None):
    """value is a NamedList name (str) OR an explicit sequence.
    - sequence -> returned as a list (used as-is; no store needed).
    - str -> look up (kind, name) in store; return its values (materialized from spec if needed).
    - str with no store / not found -> clear error.
    """
```
- **No store needed for literals** — keeps ad-hoc use and tests store-free (D3 backward compat).
- Materialization for a `spec`-only entry calls the kind's builder (energy→`energy_grid`, others as
  added). Unknown kind + spec-only → error telling the user to provide explicit `values`.
- Plans stay message-pure: resolution is plain Python on the inputs *before* the plan yields messages
  (it does not touch hardware), so it can run at plan-build time.

## 4. How plans/bars consume it

Every list-bearing parameter accepts name-or-list and resolves internally:
- `nexafs_bar(holder, energies="Fe_K_XANES")` → `resolve_list(energies, kind="energy", store=...)`.
- `giwaxs_bar(holder, incident_angles="grazing_fine")` → `kind="incidence"`.
- `temperature_bar(holder, setpoints="anneal_ramp")` → `kind="temperature"`.
- exposure/time lists similarly (`kind="time"`).
- A literal still works unchanged: `nexafs_bar(holder, energies=[7090,7100,7110])`.

The store handle: plans get it the same way the holder bridge does — a `store=` kwarg defaulting to
the session's configured `ListStore` (or `None` → literals only). Keep store wiring identical to the
sample store so there's one connection story.

## 5. GUI integration (see DOC_CORRECTIONS_PLAN — GUI section)

- The GUI gains a **"Lists" panel** beside Samples/Holders: browse by kind, view/edit/add a
  `NamedList` (its `spec` builder UI — e.g. the existing edge/energy list builder — plus the
  materialized preview), persist to the `ListStore`.
- **The GUI stops emitting copy-paste lists as the primary output.** Generated calls reference lists
  **by name** (`energies="Fe_K_XANES"`), exactly as it now references samples/holders by name. A
  literal-list escape hatch can remain for one-offs, but the default is name-reference.
- The existing GUI energy/edge list-builder becomes the **editor for `energy`-kind entries** (writing
  `spec`+`values`), not a code generator.

## 6. Plan (workstreams — fold into IMPLEMENTATION_PLAN sequencing)

### NL-1 — Model + facade (pure, no hardware)
- Add `NamedList` to the pure model (`_samples.py` or new `_lists.py`); `ListStore` to `_store.py`
  (or new `_liststore.py`).
- Tests (`test_store.py`-style, **dict backend**): round-trip per kind; spec-only vs values-only;
  CRUD; export/prune. No Redis/secret in tests.

### NL-2 — Resolver + builders
- `resolve_list(value, *, kind, store=None)`; wire the `energy` kind's `spec`→`values` to
  `technique_A.energy_grid` (single source of truth). Stub builders for incidence/temperature/time
  (likely just `values` pass-through + simple linspace specs).
- Tests: literal passthrough (no store); name resolution (dict store); spec materialization;
  missing-name + no-store error messages.

### NL-3 — Plumb name-or-list into the bars
- `technique_A/B/C/E` (+ `recipes_combined`, `_qserver` `*_from_spec`) accept name-or-list for their
  list params and call `resolve_list`. Backward compatible (literals unchanged).
- Tests: sim run of a bar with `energies="<name>"` resolves and produces a well-formed run; literal
  still works.

### NL-4 — GUI (DOC plan)
- "Lists" panel + name-reference-first generation (see §5 / DOC_CORRECTIONS_PLAN).

### NL-5 — Profile/session wiring
- The session exposes a configured `ListStore` (db=2) the way it exposes the sample store; document
  in `SAMPLE_SYSTEM_PLAN` (or a short addition) that db=2 now hosts TWO logical stores
  (`swaxssamples`, `swaxslists`).

## 7. Decisions (resolved + implemented in NL-1/NL-2)
- **Prefix vs db:** ✅ db=2, own prefix **`swaxslists`**, **Option A** (own prefix -> independently
  browsable/prunable; a sample export never drags lists along).
- **One library vs per-kind:** ✅ a single `ListStore` with `kind` tags.
- **Name collisions across kinds:** ✅ names unique **within a kind** (index is `{kind: {name: id}}`);
  `"fine"` can be both an `incidence` and a `time` list. (Tested.)
- **Provenance:** ✅ `NamedList.md` carries free metadata (created/edited/by) -- cheap, GUI-useful.
- **Materialize-on-save vs on-resolve:** ✅ **store both** -- `values` is authoritative when set;
  `spec` kept for re-edit; `resolve_list`/`resolved_values()` materialize from `spec` only if
  `values` is absent. The `energy` kind's builder is numerically identical to
  `technique_A.energy_grid` (verified `allclose`), but pure-Python so the library is CI-testable.

**Still open (not blocking; for later):**
- **Validation/units:** strictness on `units`/ranges (e.g. energy within [2050, 24000] eV). Currently
  advisory (`units` stored, not enforced); add per-kind validation later.

## Status
- **NL-1 (model + `ListStore`)** ✅ DONE — `src/smi_plans/_lists.py` (`NamedList`, `ListStore`,
  pure spec builders); exported from the package top level; no Redis import at package import.
- **NL-2 (`resolve_list` + builders)** ✅ DONE — name-or-list seam; energy spec via the pure
  energy-grid builder (matches `energy_grid`).
- **NL-3 (plumb into bars)** ⬜ TODO — fold name-or-list into `technique_A/B/C/E` + `_qserver`
  `*_from_spec` (do alongside WS6 re-thin).
- **NL-4/NL-5 (GUI Lists panel + session wiring)** ⬜ TODO (DOC plan / GUI).
- Tests: `tests/test_lists.py` (14, pure dict backend). Full suite 130 passed.


## 8. Acceptance
- A `NamedList` of each kind round-trips through `ListStore` on a **dict** backend in CI (no Redis).
- `resolve_list` returns literals untouched (store-free) and resolves names from the store.
- A technique bar runs in sim with `energies="<name>"` and with a literal, identically.
- The `energy` kind materializes via `energy_grid` (bit-identical to calling it directly).
- GUI references lists by name (no copy-paste list as the default output); a Lists panel edits them.
- No Redis import at package import; `redis` not a runtime dependency.
