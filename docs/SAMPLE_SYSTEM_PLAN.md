# SMI Sample-Metadata System — Design Plan

> **Status:** design + early build. The **Redis db=2 store and its access seam are built and
> confirmed working in the profile** (commit `46ac465`); the typed `SampleStore` facade,
> dataclasses, loading plans, and history callback in this package are still to be implemented
> (this doc is their contract). This is the spec for a persistent sample / holder / scan-history
> system shared by the **beamline plans** (this package), the **profile collection** (acquisition
> + alignment), and the **GUIs** (sample bookmarks), peer to `STARTUP_RESTRUCTURE_PLAN.md` /
> `DEVICE_DEBT.md`.
>
> **One-line goal:** a user arrives with a spreadsheet of basic sample information and leaves
> with that same information *enriched* by everything the beamtime recorded — refined positions,
> the full list of scans run on each sample (with motor positions, scan type, energy,
> attenuation, exposure, and a result), and the run uids that tie into analysis. The sample
> *carries its own history*.
>
> **Note on proposal/project + queueserver (2026-06):** this doc's "future qserver worker"
> references still hold for the *shared-state bus* (db=2 is process-shared, so a worker and the
> terminal see the same samples). But **production queueserver at SMI is deferred** pending a
> facility-level **proposal/project metadata** solution (see `docs/QSERVER_WIRING.md` → "Deferred:
> proposal/project metadata" and `STARTUP_RESTRUCTURE_PLAN.md` §7.3 item 5). Reinforced design
> rule: **proposal/project/`data_session` are NOT sample facts** and must not be stored on the
> `Sample`/`SampleStore` record — one bar may be measured under different proposals; proposal is a
> session/queue concern seeded by the (future, facility-provided) worker source, while the sample
> record stays scoped to physical-sample facts (position, alignment, history).

---

## Implementation status (what exists today)

**BUILT & confirmed working (profile, commit `46ac465`):**
- **The Redis db=2 store** — `samplestore = RedisJSONDict(redis.Redis(host, db=2, ssl, port,
  password), 'swaxssamples')`, created in `startup/smibase/base.py`. This is the **shared-state
  bus**: it is **the** place sample/holder data lives, and the way every tool (plans, GUIs,
  future qserver) sees the same samples.
- **The profile-internal access seam** — `_context.configure(sample_store=samplestore)` +
  `_context.get_sample_store()` (`src/smi_beamline/devices/_context.py`), with a `{}` fallback
  off-beamline. This is how the **profile's own code** (plans, the history callback) reaches the
  store; it is **not** the door for external tools (see "Access model" below).
- Unit tests confirm the seam returns the store by reference and uses the
  `swaxssamples:sample:<id>` / `swaxssamples:magazine` key namespacing.

**NOT yet built (this package — the rest of this doc is the contract):**
- The typed **`SampleStore`** facade (`_store.py`), the **`Sample`/`Holder`/`Magazine`/`Position`/
  `AlignmentResult`/`ScanRecord`** model (extending `_samples.py`), the **loading plans**
  (`_loading.py`), the **history callback** (`history.py`), and the **alignment-code registry**.
- Today's `_samples.py` has only the pre-existing pure-python `Sample`/`SampleList` (no id /
  holder / history / Redis) that the technique plans use; it is **extended**, not replaced.

---

## 0. Decisions locked (from the design Q&A)

| # | Decision | Choice |
|---|---|---|
| D1 | Position model | **Nominal holder-relative coords = source of truth**, plus a **cached refined-absolute** that alignment updates. |
| D2 | Stored axes | Full set: piezo `x/y/z/th`, Huber `stage x/y/z/theta/chi/phi`, per-sample `incident_angles`. |
| D3 | Physical layout | A **magazine** holds multiple holders; **one measurement position**; load/unload **transfers** a holder between them. An automated transfer mechanism exists (under active development) — the package uses an **injected `transfer_fn` seam** (default = manual prompt), not hardcoded PVs. |
| D4 | Alignment scope | Schema supports **both** holder-fiducial *and* per-sample alignment; **only per-sample is wired now**. Holder-origin transform is a defined-but-optional field (future fiducial routine). |
| D5 | Alignment result | Refined positions + status + routine name + run uid(s) + key fit scalars. |
| D6 | Alignment identity | **Named alignment-code registry** (code-name → plan), so the GUI can offer them and results are uniformly keyed. |
| D7 | History granularity | **One entry per Bluesky run** + a **per-run irradiated-region (spot) summary**. |
| D8 | History storage | **Both:** compact append-only history in **Redis db=2** (fast for GUI/spreadsheet) **and** every run **tagged with `sample_id`/`holder_id` in `md`** so Tiled is the source of truth. |
| D9 | Run→sample link | Stamp `sample_id` / `holder_id` (and `holder_slot`) into `md` (start document). |
| D10 | Sample identity | **Stable unique id** (never changes) + **editable human name**. id = Redis key + md join-key; name = humans/filenames. |
| D11 | Load semantics | `load_sample` = **transfer-holder-if-needed → goto position → set active-sample context**; runs then inherit md + append history. Reversible `unload_*`. Intent in context, **not** `RE.md` mutation. |
| D12 | Active sample | **Persisted in db=2** (mirrored process-local) so the terminal *and* a future qserver worker agree, surviving restarts. `acquire()` auto-merges active sample md + auto-appends history. |
| D13 | Spot summary | **Auto-derived** from the scan axes — but this doc specifies the **contract** acquisition/alignment code must satisfy to provide it (§7). |
| D14 | Spreadsheet | **Relational multi-file CSV** both ways: `samples.csv` (one row per sample, keyed by id) + `scans.csv` (one row per scan, referencing `sample_id`). Extra/unknown md columns preserved verbatim. |
| D15 | Redis db / access | **db=2**, store `samplestore`, prefix `'swaxssamples'` — **built & confirmed** in the profile (`46ac465`). Redis is the **shared bus**: external tools (GUIs) connect **directly** via `SampleStore.from_redis(...)` (no profile import); the `_context` seam is profile-internal only. Boundary = host + secret-file perms (§1b). |
| D16 | Huber phi (was Q-Huber-phi) | The rotation axis is **`stage.phi`** (`prs` removed); plans repointed (§8.1). |
| D17 | Slot (was Q-slot) | A slot is an **encoded physical first-guess position** that seeds `nominal` (`slot_to_position`); **numeric index interim**, encoded-position target. |
| D18 | Attenuation (was Q-attenuation) | `ScanRecord.transmission`/`attenuation_factor` ← the new **`attenuation`** (`AttenuatorSet`) baseline values (computed, energy-aware). No new device debt. |
| D19 | History pruning (was Q-history-cap) | Grow per beamtime; **never auto-prune**; pruning is a **manual** `store.prune(...)` that **requires an export first**. |
| D20 | uid capture (was Q-uid-capture) | **Standalone RE subscription** (`SampleHistoryCallback`) writes `ScanRecord` on `stop` for any run with `md.sample_id`; **qserver-proven**, catches all plans. Plans only stamp md. |
| D21 | CSV frame (was Q-frames) | Imported coords default to **nominal/holder-relative**; `frame=lab`/`--absolute` to paste absolute stage numbers. |
| D22 | Sample/Holder id (was Q-id-scheme) | **`uuid4` hex** (opaque, stable); human `name`+`holder`+`slot` are the editable fields; user-supplied id honored as upsert key on import. |

Related future extension: `POLYGON_REGIONS_PLAN.md` proposes how GUI-drawn polygon scan regions
could become sample/holder facts, first via `Sample.md['scan_regions']` and later as a typed
`ScanRegion` model. It is intentionally not part of the locked D1-D22 sample-position contract yet.

---

## 1. Where this lives & how it connects

```
            ┌──────────────────────────── Redis (xf12id2-smi-redis1, port 6380, ssl) ───────────┐
            │  db=0  RE.md           (nslsii: proposal / data_session / cycle)                   │
            │  db=1  mdsave          (RedisJSONDict 'swaxsmetadata' — beamline CONFIG/calib)     │
            │  db=2  samplestore     (RedisJSONDict 'swaxssamples' — SAMPLES/HOLDERS/STATE) ◄LIVE │
            └───────────────────────────────────────────────────────────────────────────────────┘
              ▲ read/write (via _context seam)       ▲                  ▲ read/write (DIRECT: SampleStore.from_redis)
              │  in-profile code only                │                  │  external tools — NO profile import
        ┌─────┴────────────────────┐   ┌─────────────┴──────────────┐   ┌────────┴─────────┐
        │ profile_collection       │   │ smi-plans (THIS package)   │   │ GUIs / tools     │
        │  • load/unload/transfer  │   │  • Sample/Holder model     │   │  • sample        │
        │  • alignment writes back │   │  • SampleStore facade      │   │    bookmarks     │
        │  • history callback      │   │  • acquire() md-stamp      │   │  • on-axis viewer│
        │    writes ScanRecords    │   │  (pure-python, GUI-safe)   │   │    (positions)   │
        └──────────────────────────┘   └────────────────────────────┘   └──────────────────┘
          uses SampleStore(_context     [the shared library both        own Redis conn;
           .get_sample_store())          sides import]                  own SampleStore
                       │                                    │                         │
                       └────────────────► Tiled / databroker ◄────────────────────────┘
                          every run stamped md.sample_id / md.holder_id (SOURCE OF TRUTH)
```

> Both the profile and the GUIs use the **same `smi_plans` `SampleStore` facade + key layout**;
> they differ only in **how they open the db=2 backend** — the profile reuses its injected
> connection (`_context.get_sample_store()`), external tools open their own (`from_redis`). Redis
> is the only thing they share at runtime (§1b).

**Module split (mirrors `_samples.py` being pure-Python / GUI-safe):**

- `smi_plans/_samples.py` — **extended** (pure python, no bluesky/ophyd/redis): `Sample`,
  `SampleList`, **`Holder`**, **`Magazine`**, **`Position`**, **`AlignmentResult`**,
  **`ScanRecord`**. Dataclasses + (de)serialization + the CSV round-trip. Importable in a GUI.
- `smi_plans/_store.py` — **new** (pure python; depends only on `redis`+`redis_json_dict`, both
  optional/lazy): the **`SampleStore`** facade — a thin typed layer over a `RedisJSONDict` on db=2
  (or any dict/JSON file for tests). CRUD for samples/holders, the active-sample pointer, history
  append. **No bluesky, no profile import.** Two constructors (§1b): `SampleStore.from_redis(...)`
  (the **external door** — GUIs connect directly) and `SampleStore(_context.get_sample_store())`
  (in-profile reuse of the already-open connection). The db=2 backend itself **already exists**
  in the profile (`samplestore`, `'swaxssamples'`).
- `smi_plans/_loading.py` — **new** (bluesky plans): `load_sample`, `unload_sample`,
  `load_holder`, `unload_holder` + the **transfer template hooks** (`transfer_to_measurement` /
  `transfer_to_magazine`). These are *message plans* and reuse `goto_sample`.
- `smi_plans/_compose.py` — **touched**: `acquire()` learns to (a) merge the **active sample**'s
  md, (b) stamp `sample_id`/`holder_id` into md, and (c) emit a `ScanRecord` to the store on run
  close (with the auto-derived spot summary).
- `smi_plans/alignment_codes.py` — **new** (registry, mostly names + thin wrappers): the named
  **alignment-code registry** (D6); the actual alignment plans stay in the profile collection.
- `smi_plans/history.py` — **new** (the `SampleHistoryCallback`): a RunEngine subscription that
  watches every run and writes a `ScanRecord` for any run stamped with `sample_id` (Q-uid-capture
  Method 3, §5b). The profile installs it once (`RE.subscribe(SampleHistoryCallback(store))`),
  mirroring the existing `tiled_inserter`. Pure-python over the document model + the `SampleStore`.

> **Why `SampleStore` is in this package, not just the profile:** keeping it pure-python (Redis
> connection injected, never imported at module top) means **every tool — the beamline plans, an
> external GUI, a future qserver worker — uses the exact same facade and key layout**, and the
> **test suite** can run the whole sample lifecycle against an in-memory `{}` store. The *only*
> difference between callers is **how they obtain the backend** (see the access model next).

---

## 1b. Access model — Redis is the shared bus; external tools connect directly

This is the load-bearing decision for how GUIs (and anything else) reach the samples.

**The store is reached over Redis, not by importing the profile.** Redis db=2 is deliberately the
**shared-state bus**: independent processes (the beamline IPython/QS session, a standalone GUI, an
analysis tool) all talk to the *same* db=2 store **without importing any beamline/profile code**.
A GUI needs only `pip install redis redis_json_dict` in its own environment — **no `smi_beamline`,
no profile, no EPICS, no RunEngine**.

There are therefore **two ways to get the backend**, and they are the only two:

| Caller | How it gets the `RedisJSONDict` backend |
|---|---|
| **External tools (GUIs, analysis)** — the primary case | **Connect directly** to db=2: `SampleStore.from_redis(host=…, port=6380, ssl=True, db=2, prefix='swaxssamples', password=<secret>)`. **Never imports the profile.** |
| **In-profile code** (the plans, the history callback) | Wrap the already-built store from the seam: `SampleStore(_context.get_sample_store())`. The seam exists **only** so the profile's own code shares the one connection it already opened — it is **not** an external API. |

Both yield a `SampleStore` over the **same Redis db=2** with the **same keys** (§3), so they
interoperate transparently: the GUI writes a sample, a plan loads it; a plan appends history, the
GUI shows it — live, across processes, with no shared Python imports.

```python
# smi_plans/_store.py  — the two constructors
class SampleStore:
    def __init__(self, backend):                 # backend: RedisJSONDict | dict | path-to-json
        self.backend = backend

    @classmethod
    def from_redis(cls, *, host="xf12id2-smi-redis1.nsls2.bnl.gov", port=6380, ssl=True,
                   db=2, prefix="swaxssamples", password=None, secret_path="/etc/bluesky/redis.secret"):
        """Open an INDEPENDENT db=2 connection — the door for external tools (no profile import).
        Reads the password from `secret_path` if `password` is None.  Requires only
        `redis` + `redis_json_dict` in the caller's env."""
        import redis
        from redis_json_dict import RedisJSONDict
        if password is None:
            with open(secret_path) as fh:
                password = fh.read().strip()
        client = redis.Redis(host, db=db, ssl=ssl, port=port, password=password)
        return cls(RedisJSONDict(client, prefix))
```

### The access boundary ("only from this workstation/environment") — how it is enforced

The restriction is enforced by **network locality + filesystem permissions on the secret**, *not*
by importing the profile (and not by Redis ACLs at the app layer). Verified on
`xf12id2-ws1.nsls2.bnl.local`:

- **Reachability:** the Redis host (`xf12id2-smi-redis1`, 10.65.21.44) resolves and **TCP 6380 is
  open** from the workstation — so a GUI on this host *can* connect.
- **Secret gating:** `/etc/bluesky/redis.secret` is `root:xf12id` mode `0640` — **readable only by
  the `xf12id` group, not world-readable**. A tool can connect **only if** it runs on a host that
  mounts this secret as a user in the `xf12id` group. That is the "this workstation/environment"
  boundary, and it is *by design for now*.

> **If a tool must run somewhere this secret/host isn't reachable**, that is the point to revisit
> (e.g. a dedicated shareable Redis, or a thin read/write service in front of db=2). The current
> instance is confirmed reachable + secret-gated from the beamline workstation, so **no change is
> needed for the on-site GUIs**; this is flagged only as the known boundary.

> **Note (not the external door):** the package keeps `SampleStore(dict)` / a JSON-file backend
> for **tests and fully-offline development** — that is a convenience for headless runs, *not* a
> way to reach the live samples. Live samples are always the db=2 connection above.

---

## 2. The data model (pure-python dataclasses)

All types are JSON-serializable (`to_dict`/`from_dict`), so they round-trip through Redis
(`RedisJSONDict` stores JSON via orjson — **sequences read back as lists**, so we keep arrays as
lists and never rely on tuples surviving) and through CSV.

### 2.1 `Position` — a named coordinate set

A `Position` is "where to put the motors". Used three ways: a sample's **nominal** position
(holder-relative), its **refined** position (absolute, alignment-updated), and the **as-run**
position captured in a `ScanRecord`.

```python
@dataclass
class Position:
    # frame: "holder" (relative to the holder origin) or "lab" (absolute machine coords)
    frame: str = "lab"
    # SmarAct fine stage
    piezo_x: Optional[float] = None
    piezo_y: Optional[float] = None
    piezo_z: Optional[float] = None
    piezo_th: Optional[float] = None
    # Huber coarse stage (post-hexapod swap; see §8)
    stage_x: Optional[float] = None
    stage_y: Optional[float] = None
    stage_z: Optional[float] = None
    stage_theta: Optional[float] = None
    stage_chi: Optional[float] = None
    stage_phi: Optional[float] = None
    # per-sample grazing angles to measure (relative to aligned zero)
    incident_angles: List[float] = field(default_factory=list)
```

> **Compatibility note:** today's `Sample` carries `piezo_*` and `hexa_*` (the old hexapod). The
> Huber swap renamed the coarse stage; the new field names are `stage_*`. `Position` provides a
> back-compat reader that accepts `hexa_*` aliases on input (so existing CSVs/JSON load), and the
> `goto`/`acquire` bridge maps `stage_*` → the live `stage` (Huber) device. See §8.

### 2.2 `AlignmentResult` — what an alignment produced (D5)

```python
@dataclass
class AlignmentResult:
    code: str                       # registry name, e.g. "gisaxs_hex" (D6)
    status: str                     # "ok" | "failed" | "skipped"
    when: float                     # epoch seconds
    refined: Position               # the absolute position alignment converged to
    params: Dict[str, Any] = field(default_factory=dict)   # inputs (angle, range, ...)
    fit: Dict[str, float] = field(default_factory=dict)    # key scalars: th_found, y_found,
                                                           #   peak, fwhm, i0, ...
    run_uids: List[str] = field(default_factory=list)      # the alignment scan(s) in Tiled
    notes: str = ""
```

### 2.3 `ScanRecord` — one entry in a sample's history (D7)

Compact, run-level, with an irradiated-region summary. The full data is always in Tiled via
`run_uid`; this is the convenience cache the GUI/spreadsheet read.

```python
@dataclass
class ScanRecord:
    run_uid: str                    # the Bluesky run (join into Tiled) — primary key
    scan_name: str                  # e.g. "giwaxs_tempramp_energy_5loc"
    scan_type: str                  # coarse class: "alignment" | "data" | "calibration"
    when: float                     # epoch seconds (run start)
    # --- the as-run conditions (recorded, not guessed) ---
    position: Position              # where the sample was (loaded/refined position used)
    energy_eV: Optional[float] = None
    transmission: Optional[float] = None    # net beam transmission 0-1 (attenuation.transmission, §7.3)
    attenuation_factor: Optional[float] = None  # 1/T >= 1 (attenuation.attenuation_factor); both from
                                            #   the new AttenuatorSet baseline values at this energy
    exposure_s: Optional[float] = None
    geometry: Optional[str] = None          # "reflection" | "transmission"
    detectors: List[str] = field(default_factory=list)
    # --- the dose map (D13 / §7) ---
    spots: "SpotSummary" = field(default_factory=lambda: SpotSummary())
    # --- analysis tie-in ---
    result: Dict[str, Any] = field(default_factory=dict)    # alignment result, or analysis hook
    md: Dict[str, Any] = field(default_factory=dict)        # extra intent snapshot
```

`SpotSummary` (the irradiated region — §7.2):

```python
@dataclass
class SpotSummary:
    kind: str = "none"              # "none" | "point" | "points" | "bbox" | "grid"
    points: List[List[float]] = field(default_factory=list)   # [[x,y], ...] in sample frame
    bbox: Optional[List[float]] = None    # [xmin, ymin, xmax, ymax]
    count: int = 0                  # number of distinct irradiated spots
    motor_x: Optional[str] = None   # which motor was x (e.g. "piezo_x")
    motor_y: Optional[str] = None
    units: str = "um"
```

### 2.4 `Sample` — extended (the central record)

Keeps today's ergonomics (`name`, `md`, `from_columns`/`from_csv`) but adds identity, holder
linkage, the nominal/refined position pair (D1), alignment results, and history.

```python
@dataclass
class Sample:
    id: str                         # STABLE unique id (uuid4 hex by default) — NEVER edited (D10)
    name: str                       # human, editable, NOT required unique-forever
    holder_id: Optional[str] = None # which holder this sample sits on
    slot: Optional[str] = None      # ENCODED physical first-guess position on the holder (Q-slot):
                                    #   an addressable place that *seeds* `nominal` (see below).
                                    #   Numeric index ("0","1",...) is the interim encoding; the
                                    #   target is a real encoded position the holder geometry maps
                                    #   to holder-frame coords.

    nominal: Position = ...          # holder-relative layout — SOURCE OF TRUTH (D1, frame="holder")
    refined: Optional[Position] = None  # absolute, set by alignment — the runnable cache (D1)

    incident_angles: List[float] = field(default_factory=list)   # convenience mirror of nominal
    md: Dict[str, Any] = field(default_factory=dict)             # free-form intent (unchanged role)

    alignments: List[AlignmentResult] = field(default_factory=list)   # newest-last
    history: List[ScanRecord] = field(default_factory=list)           # newest-last (D7)

    created: float = ...            # epoch
    updated: float = ...            # epoch (bumped on any write)

    # --- derived / convenience ---
    def runnable_position(self) -> Position:
        """refined if present else nominal-resolved-to-absolute (via the holder transform)."""
    def base_md(self) -> dict:
        """{'sample_id','sample_name','holder_id','slot', **self.md} — merged into run md."""
    def last_alignment(self, code=None) -> Optional[AlignmentResult]: ...
    def n_scans(self, scan_type=None) -> int: ...
```

> **Back-compat:** `Sample(name=...)` with no `id` auto-mints a `uuid4` id; existing
> `SampleList.from_columns(...)` keeps working (it mints ids + a default holder). The old
> `piezo_*`/`hexa_*` kwargs still construct a `nominal` `Position`. So **every current call site
> and CSV keeps loading** — the new fields are additive.

> **Slot → nominal position (Q-slot).** A `slot` is an **encoded, addressable physical place** on
> the holder, and it is the **first guess** for the sample's `nominal` (holder-relative) position.
> A per-holder-kind function `slot_to_position(holder_kind, slot) -> Position(frame="holder")`
> seeds `nominal` from the slot encoding; the user (or an import) may then override individual
> coords, and alignment refines `refined` on top. **Interim:** `slot` is a numeric index
> (`"0","1",…`) and `slot_to_position` is a simple pitch×index for a 1-D bar; **target:** a real
> encoded position (row/col or engraved fiducial id) that the holder geometry maps to holder-frame
> coordinates — which is also what the future holder-fiducial transform (D4, §2.5) will refine.
> Designing for the encoded form now means the interim numeric index is just the trivial encoding;
> no schema change later.

### 2.5 `Holder` — a bar/plate carrying samples (D3)

```python
@dataclass
class Holder:
    id: str                         # stable unique id
    name: str                       # human ("bar_A", "echem_cell_1")
    kind: str = "bar"               # "bar" | "plate" | "cell" | ...
    # magazine state machine (D3, D11)
    state: str = "in_magazine"      # "in_magazine" | "loading" | "at_measurement" | "unloading"
    magazine_slot: Optional[str] = None    # where it lives when racked
    # the holder origin transform (D4): how holder-frame -> lab-frame.
    # Until the fiducial routine exists, this is identity/None and `refined` per sample is used.
    origin: Optional["HolderTransform"] = None
    sample_ids: List[str] = field(default_factory=list)   # members (ordered)
    md: Dict[str, Any] = field(default_factory=dict)
    created: float = ...
    updated: float = ...
```

`HolderTransform` (D4 — **defined now, fit later**):

```python
@dataclass
class HolderTransform:
    # Minimal rigid placement: an offset + in-plane rotation + height, fit from >=2 fiducials.
    # Enough to recompute every sample's absolute position from its holder-relative nominal.
    dx: float = 0.0; dy: float = 0.0; dz: float = 0.0
    theta: float = 0.0              # in-plane rotation (deg)
    fiducial_uids: List[str] = field(default_factory=list)   # alignment runs that fit this
    when: Optional[float] = None
    status: str = "unset"           # "unset" | "fit" | "stale"
    # apply(nominal: Position) -> Position  (holder-frame -> lab-frame); identity when unset.
```

> **Wiring status:** `HolderTransform.apply()` is implemented (pure math), but **no routine fits
> it yet** (D4). Today `runnable_position()` returns `refined` (per-sample alignment) when set,
> else falls back to `nominal` interpreted as absolute (current behavior). When the fiducial
> routine lands, it fills `origin` and `runnable_position()` prefers `origin.apply(nominal)` for
> un-individually-aligned samples. **No schema change required at that point** — that's the point
> of defining it now.

### 2.6 `Magazine` — the set of holders + the active pointer (D3, D12)

```python
@dataclass
class Magazine:
    holder_ids: List[str] = field(default_factory=list)
    measurement_holder_id: Optional[str] = None   # which holder is AT the beam (D3: one at a time)
    active_sample_id: Optional[str] = None         # the "loaded" sample (D11/D12)
    slots: Dict[str, Optional[str]] = field(default_factory=dict)   # magazine_slot -> holder_id
```

---

## 3. Redis db=2 layout (the `SampleStore`)

The db=2 store is **already stood up in the profile** (`startup/smibase/base.py`):
`samplestore = RedisJSONDict(redis.Redis(host, db=2, ssl, port), 'swaxssamples')`, injected into
the device seam via `_context.configure(sample_store=samplestore)` and reached by
`_context.get_sample_store()` (returns `{}` off-beamline). **External tools open their own
connection to the same db=2 via `SampleStore.from_redis(...)` (§1b) — they do not use the seam.**
Either way the keys are identical and **flat / namespaced** (RedisJSONDict stores one JSON blob
per top-level key; we keep each sample/holder as its own key so writes don't rewrite the whole
store):

```
swaxssamples:sample:<sample_id>     -> Sample.to_dict()
swaxssamples:holder:<holder_id>     -> Holder.to_dict()
swaxssamples:magazine               -> Magazine.to_dict()   (singleton: holders list + active ptr)
swaxssamples:index:name             -> { name: sample_id }  (optional human-name lookup cache)
swaxssamples:schema_version         -> int                  (migration guard)
```

> **Prefix `'swaxssamples'`** mirrors `mdsave`'s `'swaxsmetadata'` prefix on db=1 (and is the
> name already chosen in the live profile). The per-record-key choice (not one giant dict) keeps
> history appends O(one sample), important once `history` grows over a beamtime.

`SampleStore` API (pure-python facade; **the backend is a Redis db=2 connection** — opened
directly by external tools via `from_redis`, or reused from the seam in-profile; a plain dict /
JSON file is the tests/offline backend only):

```python
class SampleStore:
    def __init__(self, backend):                # backend: RedisJSONDict | dict | path-to-json
        ...
    @classmethod
    def from_redis(cls, *, host=..., port=6380, ssl=True, db=2, prefix="swaxssamples",
                   password=None, secret_path="/etc/bluesky/redis.secret") -> "SampleStore":
        ...                                     # the EXTERNAL door (GUIs); no profile import (§1b)
    # samples
    def put_sample(self, s: Sample) -> None
    def get_sample(self, sample_id: str) -> Sample
    def find_sample(self, name: str) -> Optional[Sample]     # by human name (may be ambiguous)
    def list_samples(self, holder_id=None) -> List[Sample]
    def delete_sample(self, sample_id: str) -> None
    # holders / magazine
    def put_holder(self, h: Holder) -> None
    def get_holder(self, holder_id: str) -> Holder
    def list_holders(self) -> List[Holder]
    def magazine(self) -> Magazine
    def set_measurement_holder(self, holder_id: Optional[str]) -> None
    # the active ("loaded") sample (D12)
    def set_active_sample(self, sample_id: Optional[str]) -> None
    def get_active_sample(self) -> Optional[Sample]
    # history (append-only; the hot path) (D7/D8)
    def append_scan_record(self, sample_id: str, rec: ScanRecord) -> None
    def append_alignment(self, sample_id: str, res: AlignmentResult) -> None
    def update_refined(self, sample_id: str, pos: Position) -> None
    # bulk / spreadsheet (D14)
    def import_samples(self, samples: List[Sample], holder: Holder) -> None
    def export_tables(self) -> tuple[list[dict], list[dict]]   # (samples_rows, scans_rows)
    # lifecycle / pruning (Q-history-cap) — MANUAL, never automatic
    def export_csv(self, dir_path: str) -> tuple[str, str]     # write samples_out.csv + scans_out.csv
    def prune(self, *, sample_ids=None, holders=None, require_export=True) -> dict
        # Deliberate, operator-invoked clear-out of samples/holders (e.g. end of a campaign).
        # Refuses (require_export=True) unless an export has been written first, so history is
        # never lost.  NEVER called automatically — see the growth note below.
```

**History growth & pruning (Q-history-cap).** The per-sample `history` in db=2 is allowed to
**grow for the whole beamtime** — it is **not** capped or rotated automatically, and it is **never
pruned on a proposal switch** (a single user/holder set can span several proposals, so an
automatic proposal-triggered prune would wrongly discard live samples). Clearing the store is a
**deliberate human decision** (`store.prune(...)`), and by default that call **requires an export
first** (`export_csv`) so the enriched spreadsheet + the full record (already in Tiled) are
preserved before anything is removed. The GUI surfaces this as an explicit "Archive & clear
samples" action, never a silent background job.

**Concurrency:** terminal + qserver worker may both write (D12). RedisJSONDict is last-write-wins
per key. Because each sample is its own key and history is append-only, the realistic conflict
(both append a `ScanRecord` to the *same* sample at the *same* instant) is rare; if it matters we
add a small `WATCH`/MULTI optimistic retry in `append_scan_record` (noted, not built). The
**active-sample pointer** is a single small key — the last writer wins, which is the correct
semantics ("whatever was most recently loaded is active").

---

## 4. The loading lifecycle (plans) — D3, D11

All are **message plans** (`yield from`), reusing `goto_sample`. **The real transfer mechanism
(magazine ↔ measurement) is under active development in the profile collection** (Q-transfer), so
this package does **not** hardcode its PVs. Instead the transfer is an **injected seam**: the
loading plans call a `transfer_fn` provided by the profile; the package ships a safe default
(prompt the user) and a clean contract the real mechanism must satisfy. When the mechanism lands,
the profile registers its plan via the seam — **no change in this package**.

```python
# smi_plans/_loading.py  (sketch)

# The transfer seam: the profile injects its real transfer plans here when ready.
# Until then these default to a manual prompt.  Signature is fixed; bodies are swappable.
#   transfer_fn(holder, *, store, target, slot=None) -> plan
#     target in {"measurement", "magazine"}; must yield only messages; must leave the
#     store consistent (see contract below) and raise on failure (a safe halt).
_TRANSFER_FN = None   # set by smi_plans.configure_transfer(fn) from the profile bootstrap

def configure_transfer(fn):
    """Profile bootstrap calls this with the real, under-development transfer plan."""
    global _TRANSFER_FN; _TRANSFER_FN = fn

def transfer_to_measurement(holder, *, store, from_slot=None):
    """Bring `holder` from the magazine to the beam position (via the injected transfer_fn,
    else a manual prompt).
    CONTRACT (the real mechanism must guarantee, the default upholds): on success ->
      • holder.state == 'at_measurement'
      • magazine.measurement_holder_id == holder.id
      • the previously-mounted holder (if any) is 'in_magazine'
      • all of the above PERSISTED to the store before returning
    On any failure: raise (do not leave a half-loaded state); callers treat it as a halt."""
    ...

def transfer_to_magazine(holder, *, store, to_slot=None):
    """Return `holder` from the beam position to its magazine slot (reverse of the above).
    CONTRACT: on success holder.state=='in_magazine', magazine.measurement_holder_id is cleared,
    holder.magazine_slot==to_slot (or its prior slot), persisted; raise on failure."""
    ...

def load_holder(holder, *, store):
    """Ensure `holder` is the one at the measurement position (D3: one at a time).
    If another holder is mounted, transfer_to_magazine() it first; then
    transfer_to_measurement(holder).  No-op if `holder` is already at measurement."""
    ...

def load_sample(sample, *, store, goto=True, set_active=True):
    """The user-facing verb (D11):
      1. resolve sample (accept a Sample or an id/name; read from store),
      2. load_holder(sample.holder) if it isn't already at measurement,
      3. if goto: yield from goto_sample_position(sample.runnable_position()),
      4. if set_active: store.set_active_sample(sample.id)  (persisted, D12).
    After this, acquire()/alignment auto-inherit this sample's md and append to its history."""
    ...

def unload_sample(*, store, park=True):
    """Clear the active sample; optionally move to a safe park position. Reverse of load."""
    ...
```

`goto_sample_position(pos: Position)` is the `Position`-aware sibling of today's
`goto_sample(sample)` — it maps `piezo_*`→`piezo`, `stage_*`→`stage` (Huber), emitting one
`bps.mv`. (Today's `goto_sample` is retained and re-expressed on top of it.)

---

## 5. Acquisition integration (the auto md + auto history) — D8, D9, D12

The whole system is only useful if **running a normal `acquire()` "just works"** with a loaded
sample. Two additive behaviors in `acquire()` (and `acquire_bar`/`multi_sample_run`):

**(a) Inherit + stamp (start document).** When a sample is active (or passed via `sample=`):

```python
# inside acquire(), building run_md:
active = sample or store.get_active_sample()
if active is not None:
    md = merge_md(active.base_md(), md)     # sample md FIRST so explicit caller md wins
    # base_md() injects: sample_id, sample_name, holder_id, slot  -> the Tiled join keys (D9)
```

This is **pure md** — no `RE.md` mutation (Tenet 4). It composes with the existing
`sample_name_decorator` the profile uses for alignment scans (they agree: both put a
`sample_name` in scope; here it also carries the stable `sample_id`).

**(b) Append history (a standalone RE subscription) — Q-uid-capture = Method 3.** History is
**not** written by `acquire()` itself. Instead the profile installs **one callback on the
RunEngine at startup** — exactly like the existing `RE.subscribe(tiled_inserter.insert)` — that
watches every run and, for any run whose `start` doc carries a `sample_id`, writes a `ScanRecord`
on the matching `stop` doc:

```python
# smi_plans.history.SampleHistoryCallback(store)  — profile does RE.subscribe(SampleHistoryCallback(store))
#   on "start": if doc.get("sample_id"): remember {uid: (sample_id, scan_name, geometry, ...)}
#   on "descriptor"/"event"/"baseline": accumulate what the ScanRecord needs (energy, transmission,
#       attenuation_factor from the `attenuation` baseline (§7.3), the spot summary (§7.2))
#   on "stop":  rec = ScanRecord(run_uid=doc["run_start"], when=..., **accumulated);
#               store.append_scan_record(sample_id, rec)
```

**Why a subscription, not `acquire()`-internal (the decisive reason — tested):**
- It works under **qserver with no command line** — the subscription lives on the worker's
  RunEngine, so it fires even though the caller never sees `RE()`'s return value. *Verified* by
  simulating the qserver model (worker runs the plan, return value discarded; the `ScanRecord`
  was still written). This was the user's key requirement.
- It is the **same proven pattern** the profile already uses for Tiled (`tiled_inserter`).
- It is **decoupled from `acquire()`**, so it records **every** plan that stamps `sample_id` —
  `acquire()`, alignment scans, `cdsaxs_rock_run`, tomography — not only composed runs. (This is
  what makes "*all* scans run on a sample are recorded" true, per your requirement.)

**The only thing plans must do** is stamp `sample_id`/`holder_id` into `md` (start doc) — which
(a) above already does for `acquire()`, and which alignment/other plans get from the **active
sample** the same way (or via the existing `sample_name_decorator`, extended to also carry
`sample_id`). So the integration is: *plans stamp md; the subscription writes history*.

> The spot summary (§7.2) + as-run energy/transmission/exposure (§7.3) must be reconstructable
> **from the run's documents** (descriptors/events/baseline) so the callback can build the
> `ScanRecord` without the plan handing it anything extra. Where a value isn't naturally in the
> stream, `acquire()` may stuff a compact summary into `md` (e.g. `md["spots"]`) for the callback
> to read — still pure md, no side channel.

---

## 6. The spreadsheet round-trip (D14)

**In:** `samples.csv` (+ optional `holders.csv`). Columns map to `Sample`/`Position` fields;
**unknown columns fold into `md`** (today's `from_csv` rule, preserved). Minimum viable header:

```
holder, name, slot, piezo_x, piezo_y, piezo_z, piezo_th,
stage_x, stage_y, stage_z, stage_theta, stage_chi, stage_phi,
incident_angles, <any extra columns -> md>
```

- `id` is **optional on input** (auto-minted if absent); if present, it's an **upsert** key
  (lets a user round-trip: export, edit, re-import without losing identity/history links).
- `incident_angles` may be space/`;`-separated in one cell (today's behavior).
- coordinates are interpreted as the **nominal** (holder-relative) position by default; a
  `frame=lab` column or a `--absolute` flag treats them as absolute.

**Out (enriched):** two CSVs, joinable on `sample_id` — the relational shape you chose, which is
also exactly how analysis consumes it:

`samples_out.csv` (one row per sample = the input, **plus** collected columns):
```
sample_id, name, holder, slot,
  nominal_piezo_x, ..., nominal_stage_phi, incident_angles,        # the input, echoed
  refined_piezo_x, ..., refined_stage_phi,                          # alignment result (D5)
  last_alignment_code, last_alignment_status, last_alignment_when,
  n_alignments, n_data_scans, n_total_scans,
  first_scan_when, last_scan_when,
  last_energy_eV, <md.* columns...>                                 # md flattened back out
```

`scans_out.csv` (one row per `ScanRecord`, **referencing** the sample):
```
sample_id, name, run_uid, scan_name, scan_type, when,
  energy_eV, transmission, attenuation_factor, exposure_s, geometry, detectors,
  pos_piezo_x, ..., pos_stage_phi,
  spots_kind, spots_count, spots_bbox, spots_motor_x, spots_motor_y,
  result_*                                                          # flattened alignment/analysis result
```

This is `SampleStore.export_tables()` → two row-lists → `csv.DictWriter`. The nested history
(`history`/`alignments` lists) lives in db=2/Tiled; the CSVs are the flattened, human- and
pandas-friendly projection (`df = scans.merge(samples, on='sample_id')` reproduces the full
picture, and every row still has a `run_uid` to fetch raw data from Tiled).

> **Why multi-file beats one wide CSV:** a sample has *N* scans; a single flat sheet would either
> duplicate sample columns per scan or bury history in unparseable cells. Two normalized tables
> joined on `sample_id` is the clean relational representation — and mirrors the db=2 (compact) ↔
> Tiled (full) split exactly.

---

## 7. The irradiated-region (spot) contract — D13

The dose map ("which parts of the sample saw beam") is **auto-derived**, but only if the
acquisition/alignment code exposes the needed information. This section is the **standard those
code paths must meet** (so they can be refactored to it).

### 7.1 Principle

The composition layer already names its spatial axes (`spatial_grid_axes` builds
`motor_axis("x", piezo.x, [...])` / `("y", piezo.y, [...])`). The spot summary is computed from
**the spatial axes' visited values**, expressed in the **sample frame** (relative to the loaded
sample origin), not raw machine coordinates.

### 7.2 What acquisition code must provide

An `acquire()`/recipe that rasters MUST make its spatial sampling discoverable. The contract:

1. **Tag spatial axes.** A `ScanAxis` that moves the in-plane sample position carries a role
   marker so the summarizer can find it:
   - `ScanAxis(..., role="spatial_x")` / `role="spatial_y"` (new optional attribute, default
     `None`). `spatial_grid_axes` sets these automatically.
2. **Values are recoverable in the sample frame.** The summarizer converts each visited absolute
   value to sample-frame by subtracting the loaded sample's origin (`refined`/`nominal`
   `piezo_x/y`). If an axis uses relative offsets, it must record the **base** it offsets from
   (so absolute → sample-frame is well defined). `spatial_grid_axes(..., origin=...)` captures
   this.
3. **Single-spot default.** If there are **no** spatial axes, the summary is `kind="point"` at
   the loaded position (`count=1`). This is always correct and needs no cooperation.

From (1)+(2), `acquire()` builds the `SpotSummary` with no user effort:
`kind` ∈ {point, points, grid, bbox}, `points` = the cartesian product of the tagged axes'
sample-frame values, `bbox` = their extent, `count` = number of distinct spots, `motor_x/y` =
the tagged axes' device data-keys.

### 7.3 What must be recorded for the as-run conditions

`ScanRecord` wants `energy_eV`, `transmission`, `exposure_s`. The contract: these must be present
**as recorded fields** (Tenet 2), so the recorder reads them from the run, not via `.get()`:

- `energy_eV` ← the recorded `energy` readback (already in `reads` for most recipes; if absent,
  left `None`). Data-key conventionally `energy_energy`.
- `exposure_s` ← the exposure set by `det_exposure_time` (see §8 finding: this is also where
  `det_exposure_time` should expose its commanded value as a recordable signal so it lands in the
  stream rather than being passed around).
- `attenuation` ← **the transmission factor**, now sourced from the **new energy-aware
  `AttenuatorSet`** object (instance **`attenuation`**, added to the profile 2026-06-17). It
  exposes the net values as real Signals computed from the inserted foils + live energy via CXRO
  curves:
  - `attenuation.transmission` (0–1, `kind="normal"`) — use this for `ScanRecord.transmission`.
  - `attenuation.attenuation_factor` (1/T ≥ 1, `kind="hinted"`) and `attenuation.energy_eV`
    (the energy the factor was computed at) — also recorded; store the factor + energy alongside
    so the value is self-describing.
  - **`attenuation` is already in the scan baseline**, so transmission/factor/energy land at run
    start & close automatically — the recorder reads them from the **baseline stream**, no `.get()`.
  - **Freshness caveat (important):** these Signals are *computed* (not EPICS PVs). They refresh on
    `attenuation.read()/.trigger()` (i.e. when bluesky reads the **whole device** — which baseline
    does) but **not** on a bare `bps.rd(attenuation.transmission)` of the sub-signal. So: rely on
    the baseline capture (preferred), or `yield from bps.rd(attenuation)` / `attenuation.compute()`
    before reading the sub-signal. It is **energy-dependent** (clamped to 2000–25000 eV).
  - **No new device debt:** this supersedes the earlier "expose an aggregate transmission" ask —
    the object now exists (DEVICE_DEBT #7 resolved). The only remaining nicety is confirming
    `attenuation` is in the baseline of *this package's* runs (it is, profile-side).

> **Net:** the spot map, energy, exposure, and **attenuation** are all achievable from recorded
> fields — attenuation via the new `attenuation` (`AttenuatorSet`) baseline values, the spot map
> via small additive axis-role tags in the composition layer (§7.2). No new ophyd item is required
> for the history record.

---

## 8. Device-dependency reassessment (does this package still work after Phase 0–4?)

You asked specifically whether **smi-plans' plans still apply** given the profile-collection
device changes — i.e. whether the package depends on any device that was changed/removed. Result:
**the architecture is fine and the sample system fits cleanly, but the device-debt work surfaced
two pre-existing consistency gaps that are independent of the sample system and should be fixed.**
Both are already implied by the restructure plan; recording them here because they affect whether
the plans run as written.

### 8.1 FIXED — `prs` repointed to the Huber `stage.phi`

- **Where:** `technique_I_cdsaxs.py` (the rocking curve), `technique_K_tomography.py` (the
  rotation series / texture / sinogram), `technique_M_autonomous.py` (the alignment raster), plus
  `prs` in the `.. important::` device lists (`technique_J_xrr.py`, `recipes_combined.py`,
  `_compose.py`, `_core.py`, `__init__.py`).
- **Was:** these called the removed `prs` global (`bps.mv(prs, …)`, `rel_scan(prs, …)`,
  `inner_product_scan(…, prs, …)`, `grid_scan(…, prs, …)`) → would `NameError` on the live
  profile. Open item **C7**.
- **Fix (DONE):** all device references repointed to **`stage.phi`** (confirmed: the Huber
  `STG_pseudo` rotation axis, a settable/readable `PseudoSingle`, limits ±90°, records as
  `stage_phi`; `prs` is fully removed from the live profile). The `prs_range`/`prs_start`/
  `prs_stop` *parameter* names are kept for backward-compatible call signatures (they configure
  the `stage.phi` rock); docstrings updated, with a migration note in each affected module.
- **Verified:** the sim harness now models the Huber `stage` (`x/y/z/theta/chi/phi` + `.th/.ph/.ch`
  aliases) and defines **no** `prs`, mirroring the beamline; `technique_K` examples run end-to-end
  through a RunEngine (181/455/96 frames); new regression tests assert `stage_phi` is the recorded
  scanned axis and that no `prs` key ever appears (`tests/test_smoke.py`).

### 8.2 FIXED — `det_exposure_time` now driven as a plan (`yield from`)

- **Where:** ~37 call sites across `technique_A/B/C/D/E/F/G/H/I/J/K/L/M/N/O` + `recipes_combined`.
- **Was:** `det_exposure_time(t, t)` called without `yield from` — but Phase 2 (C5) made
  `det_exposure_time` a **plan**. Calling a generator function without `yield from` creates an
  unconsumed generator → exposures were **silently never set** (a message-purity + correctness
  bug, ironically against the package's own Tenet 5/6).
- **Fix (DONE):** every real call site now `yield from det_exposure_time(t, t)` (all are inside
  generator plans, so this is safe). The lone bare occurrence left is the **anti-pattern example
  in `technique_N`'s docstring** (intentional — it shows legacy bad code).
- **Verified:** the sim harness's `det_exposure_time` is itself a plan; a new guard test
  (`test_det_exposure_time_is_yielded_from`) replaces it with a spy and asserts it is *consumed*,
  and was confirmed to **fail** when a `yield from` is removed (so it is a true regression guard).
- **Follow-up (still open, §7.3):** `det_exposure_time` should additionally expose its commanded
  exposure as a recordable signal so `ScanRecord.exposure_s` can read it from the stream. Tracked
  as a DEVICE_DEBT item; not required for the call-convention fix above.

### 8.3 Consistent / no change needed (verified against the handoff)

- **`stage` (Huber `STG_pseudo`)** — keeps the name `stage`, exposes `x/y/z/theta/chi/phi` (+
  `.th/.ph/.ch` aliases). The package uses `stage`, `stage.y`, `stage.th` — **still valid**
  (alias-covered). `Position.stage_*` matches the Huber axis set.
- **`piezo` (SmarAct)** — unchanged; still on top of the Huber stage. `goto_sample` piezo path
  fine.
- **Attenuators** — `att1_*`/`att2_*` still accept `bps.mv(att, "insert"/"open"/0/1)`; Phase 3
  made `.set()` settle-safe but **preserved the idiom** (`technique_O`, the `att2_9.close_cmd`
  setups). **No change** for the plans. The new energy-aware aggregate **`attenuation`**
  (`AttenuatorSet`, 2026-06-17) additionally provides the `transmission`/`attenuation_factor` the
  history record reads from baseline (§7.3) — additive, not a break.
- **Pilatus / `pil2M_pos.z` (SDD baseline)** — Phase 0 fixed latent Pilatus bugs and Phase 3
  moved SAXS offsets into db=1 config; the *interface* (`pil2M_pos.z` as a baseline readable) is
  unchanged. Note the **casing open question** `pil2M_pos` vs `pil2m_pos` (plans use upper) — flag
  with staff (pre-existing, not new).
- **Linkam `LThermal` / humidity `readHumidity`** — still method/function-based; the package
  already routes them through `_devices.py` wrappers (DEVICE_DEBT #1/#2). **No change**; the
  sample system doesn't touch them.
- **`sample_id` / `get_scan_md` / `get_more_md`** — **deprecated-but-kept** (Phase 3, H5/H6). The
  package already **does not use** them (it uses `md={}` + `sample_name`). The **new sample
  system reinforces this**: `sample_id`/`holder_id` travel in `md` via `Sample.base_md()`, which
  is the sanctioned replacement for the deprecated `sample_id(...)` global-state call. ✔
- **`make_devices` factory / headless import / db=1 `_config.py`** — the sample system mirrors
  this: the `SampleStore` facade is pure-python and never imports anything at module top, so the
  package stays headless-importable and qserver-safe. **In-profile** it is built from the injected
  seam (`SampleStore(_context.get_sample_store())`), exactly like `_config.py` reads `mdsave`;
  **external tools** build it with their own connection (`from_redis`, §1b). The
  active-sample-in-Redis choice (D12) is the **same process-shared rationale** as the Redis-config
  workstream (terminal, worker, and GUI must agree).

**Conclusion:** the sample-metadata design is **consistent with the post-Phase-4 device layer**
and depends on nothing that was removed. The two pre-existing gaps it surfaced
(`prs`→`stage.phi`, `det_exposure_time` call convention) have now been **fixed in the package**
(§8.1, §8.2 — repointed + `yield from`, with regression tests; 32 passed). The transmission factor
the history record needs is **already provided** by the new `attenuation` (`AttenuatorSet`) object
(§7.3), so there is **no remaining new device-side ask** — the design is fully buildable against
the current profile.

---

## 9. GUI bookmark contract (summary; detail in the GUI builder skill)

The GUI is a **consumer of the db=2 store, reached over its OWN direct Redis connection** —
`SampleStore.from_redis(...)` (§1b). It **does not import the profile/`smi_beamline`**, does not
use `_context`, and does not talk to the RunEngine. It only needs `redis` + `redis_json_dict` (and
the package's pure-python `_samples`/`_store`) in its own environment, and to run where the db=2
host + secret are reachable (the workstation; §1b boundary). Specifically the GUI:

- **Constructs** `store = SampleStore.from_redis()` once at startup (reads the secret from
  `/etc/bluesky/redis.secret`); everything below is a method on that `store`.
- **Reads** holders/samples to render the bar/magazine and each sample's bookmark (nominal +
  refined position, last alignment, scan count, last energy) — all from its own `store`.
- **Writes** new/edited samples (a sample table), holder membership, slot labels, and free md —
  via `put_sample`/`put_holder`/`import_samples`. Identity is the stable `id` (D10), so renaming
  in the GUI never breaks history links. Writes are visible to the live beamline session
  **immediately** (same Redis), and vice-versa — that is the whole point of the shared bus.
- **Requests a load** by writing the desired active sample id (`store.set_active_sample(id)`) and
  asking the beamline to run `load_sample` — i.e. the GUI sets intent in the shared store; the
  **plan** (in the beamline/qserver process) performs motion/transfer. The active pointer in db=2
  is the shared hand-off; the GUI never moves motors.
- **Never** performs motion or any EPICS/RunEngine action.

> **Cross-process consistency:** because the GUI and the beamline are separate processes sharing
> one Redis db=2, writes are last-write-wins per key (§3 concurrency note). The GUI should re-read
> before showing/acting on volatile state (the active sample, holder `state`), since the beamline
> may have changed it. This is normal shared-store discipline, not a special case.

The full UI breakdown (which panels, how bookmarks render, the load button semantics, and how the
viewer overlays the dose map) is in `skills/smi-plans-gui-builder.md`.

---

## 10. Build phases (beamline-safe slices)

Following the proven Phase-by-Phase discipline (feature branch, tests after each, no push without
OK, surface hardware-semantics before changing them):

1. **Model + store (headless, pure-python).** Extend `_samples.py` (Sample/Holder/Magazine/
   Position/AlignmentResult/ScanRecord) + `_store.py` (`SampleStore` with **both** constructors:
   `from_redis(...)` for external/live and `SampleStore(backend)` for the seam/tests). Full
   back-compat (existing `from_columns`/`from_csv`/`to_dicts` keep passing). Tests: round-trip every
   type through `to_dict`/`from_dict` and through a `{}`-backed store; existing sample tests stay
   green. *Independently useful with zero hardware.*
2. **Redis db=2 link.** ✅ **Done in the profile** (commit `46ac465`) — `samplestore`
   (`RedisJSONDict` on db=2, prefix `'swaxssamples'`) in `startup/smibase/base.py`, injected via
   `_context.configure(sample_store=samplestore)` / `get_sample_store()`, with unit tests.
   Remaining (package side): the `SampleStore.from_redis(...)` external constructor + the in-profile
   `SampleStore(_context.get_sample_store())` wrapper, and a restart-persistence + cross-process
   read/write check (GUI process writes, beamline process reads, via two independent connections).
3. **Loading plans (templates).** `_loading.py` with `load_sample`/`unload_sample`/`load_holder`
   + transfer hooks. **The real transfer mechanism is under active development**, so the hooks stay
   as a *scaffold* (the contract in §4: state transitions + return guarantees), with the manual
   `pause_for_user` body as the interim and the real plan injected via `configure_transfer(...)`
   when the mechanism lands. Drive against the sim harness (no real transfer).
4. **md stamping + history subscription.** (a) `acquire()` merges the active sample's `base_md()`
   and stamps `sample_id`/`holder_id` (start doc). (b) `smi_plans/history.py`
   `SampleHistoryCallback(store)` — the RE subscription that writes a `ScanRecord` on `stop` for any
   run carrying `sample_id` (Method 3, §5b), building the `SpotSummary` (§7.2) + reading
   energy/`attenuation` from baseline (§7.3). Add the `role="spatial_*"` tags to
   `spatial_grid_axes`. Tests: drive a sim run through a `RunEngine` with the callback subscribed
   and assert a `ScanRecord` (with the right `run_uid`) lands in a `{}`-store — including a
   **qserver-style** test where `RE()`'s return is discarded (already prototyped).
5. **Spreadsheet round-trip.** `import_samples` from `samples.csv`; `export_tables` →
   `samples_out.csv` + `scans_out.csv`. Tests on a synthetic store.
6. **Device-gap fixes (parallel, package-side).** ✅ **Done** — `prs`→`stage.phi` (§8.1) and
   `yield from det_exposure_time` (§8.2), with regression tests (32 passed); `DEVICE_DEBT.md`
   updated. The aggregate **attenuation** readable the history record needs already exists
   (`attenuation`/`AttenuatorSet`), so no further device item is required.
7. **GUI bookmarks** (separate, later) — the GUI builds on `SampleStore` per §9; add
   `QueueServerExecutor`-style "load this sample" enqueue when a backend exists.

---

## 11. Open questions (to settle before/within implementation)

1. ~~**Q-Huber-phi:** exact attribute for the rotation axis — `stage.phi`?~~ **RESOLVED:** the
   axis is **`stage.phi`** (`STG_pseudo` `PseudoSingle`, limits ±90°, records as `stage_phi`,
   back-compat alias `stage.ph`; `prs` fully removed). Plans repointed (§8.1) and
   `Position.stage_phi` is the correct field name. *(Also closes restructure-plan Open Q #1.)*
2. ~~**Q-slot:** free label vs numeric index vs encoded position?~~ **RESOLVED:** a slot is an
   **encoded physical first-guess position** that seeds the sample's `nominal` (holder-relative)
   coords via `slot_to_position(holder_kind, slot)`. **Interim** encoding = a numeric index
   (`"0","1",…`) with a simple pitch×index mapping; **target** = a real encoded position (row/col
   / fiducial id) the holder geometry resolves — the same place the future holder-fiducial
   transform (D4) refines. See §2.4 "Slot → nominal position".
3. ~~**Q-transfer:** manual-first or automated?~~ **RESOLVED:** an automated transfer mechanism
   **exists and is under active development** in the profile. The package stays decoupled: the
   loading plans use an **injected `transfer_fn` seam** (`configure_transfer(fn)`, §4) with a fixed
   signature + a state/return **contract**; the default is a manual prompt, and the profile
   registers the real plan when ready. Concrete PVs live in the profile, not here.
4. ~~**Q-attenuation:** single transmission readback or compute from foils?~~ **RESOLVED:** the new
   energy-aware **`AttenuatorSet`** object (instance `attenuation`, added 2026-06-17) exposes
   `transmission` / `attenuation_factor` (computed from inserted foils + live energy via CXRO
   curves) and is **already in the scan baseline**. `ScanRecord` reads them from the baseline
   stream (§7.3). **No new device debt** (freshness caveat noted: the values refresh on a
   whole-device `read()`/baseline capture, not a bare sub-signal `bps.rd`).
5. ~~**Q-history-cap:** cap/rotate, or grow + prune on proposal switch?~~ **RESOLVED:** grow for
   the whole beamtime; **never** auto-prune (a user/holder set can span proposals). Pruning is a
   **deliberate operator action** (`store.prune(...)`) that **requires an export first**
   (`export_csv`) so nothing is lost; the GUI exposes it as "Archive & clear", never automatic. See
   §3 "History growth & pruning".
6. ~~**Q-uid-capture:** subscribe-in-plan vs RE() return?~~ **RESOLVED: Method 3** — a standalone
   **RunEngine subscription** (`smi_plans.history.SampleHistoryCallback`, installed once by the
   profile like `tiled_inserter`) writes the `ScanRecord` on `stop` for any run stamped with
   `sample_id`. **Verified to work under the qserver model** (worker runs the plan, `RE()`'s return
   discarded — the record was still written) and it captures *every* such plan (alignment, cdsaxs,
   tomography), not just `acquire()`. Plans only need to stamp `md.sample_id` (§5).
7. ~~**Q-frames:** CSV default frame?~~ **RESOLVED: nominal/holder-relative by default** (D1 source
   of truth), with an explicit `frame=lab` column / `--absolute` flag to paste absolute stage
   numbers. (§6.)
8. ~~**Q-id-scheme:** uuid4 vs readable?~~ **RESOLVED: `uuid4` hex** for `Sample.id`/`Holder.id`
   (opaque, stable across renames/slot moves/re-imports), with the human `name` + `holder`/`slot`
   as the friendly editable fields. On CSV import a user-supplied `id` is honored as the upsert key
   (so a returning spreadsheet keeps identity); absent → minted. (D10.)

**All open questions resolved.** Remaining items are implementation choices captured inline
(e.g. optimistic-retry on concurrent same-sample appends; the exact baseline fields the history
callback reads), to be settled during the phased build (§10).
