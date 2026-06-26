# Streams & the file-naming workflow — what changed, and what `smi-workflows` needs

> **Audience:** whoever maintains the `smi-workflows` repo (https://github.com/NSLS2/smi-workflows)
> and the `smi-plans` backend. Written from `smi-plans` because the workflow repo is not on the
> beamline box; apply the workflow-side changes there.
>
> **TL;DR:** the arc-economy GIWAXS path now writes **per-arc event streams** (`arc0`, `arc20`, …)
> instead of a single `primary` stream. The current `linker.py` is **stream-name-agnostic** — it
> processes events from *any* stream that carries an external (AreaDetector) data key — so per-arc
> streams do NOT need to be "registered" anywhere, and they will be picked up. **BUT** there is a
> real naming gap: arc-economy runs carry a **bare** `sample_name` (just the sample name, no
> `{token}` template), so the symlinked filenames **lose the WAXS arc and incident angle**. That is
> a `smi-plans`-side fix (give the arc-economy run a templated `sample_name`), described below.

---

## 1. What streams `smi-plans` produces

| Producer | Stream name(s) | Notes |
|---|---|---|
| `acquire` / `one_sample_run` / `nexafs_run` / `transmission_run` / `giwaxs_run` / `giwaxs_bar` | **`primary`** (default `trigger_and_read`, no `name=`) | the normal case; unchanged |
| **`giwaxs_bar_arc_economy`** (via `multi_sample_run`) | **`arc0`, `arc20`, `arcm1p5`, …** — one stream per WAXS arc | NEW. `technique_B._arc_stream_name(arc)`: `20 -> "arc20"`, `-1.5 -> "arcm1p5"`. Created implicitly by `trigger_and_read(..., name=stream)` (no explicit `declare_stream`). |
| `declare_saxs_waxs_streams` (opt-in helper) | `saxs`, `waxs` | advisory; only if a plan calls it. Not used by default. |
| `baseline_wrapper` | `baseline` | constants at run open/close (existing behavior) |

**Why per-arc streams:** low-arc data is WAXS-only (`pil900KW`) and high-arc data is SAXS+WAXS
(`pil2M`+`pil900KW`); putting them in one `primary` stream makes a sparse, awkward descriptor
(different data keys per arc). Separate streams keep each arc's descriptor clean. (Committed
`536ec39`, beamline-tested.)

> Each arc-economy event carries: the detector image key(s) for that arc, plus `energy_energy`,
> `waxs_arc`, `xbpm2_sumX`, `xbpm3_sumX`, and `incident_angle`. So the *data* needed for naming IS in
> the stream events — the gap is purely that the run's `sample_name` template doesn't reference them
> (see §3).

## 2. How the CURRENT `linker.py` consumes runs (relevant facts)

From `NSLS2/smi-workflows:linker.py` `get_symlink_pairs(...)`:

- It reads **one run** (`get_run(ref)`) and iterates **all** its documents.
- `start` → builds the filename template from the **start doc**:
  ```python
  target_template = f"{{det_name}}/{doc['sample_name']}_id{doc['scan_id']}_{{N:06d}}_{{det_type}}.tif"
  target_path     = /nsls2/data/smi/proposals/{cycle}/{data_session}/projects/{project_name}/user_data
  ```
- `descriptor` → `target_keys` = every data key whose describe has `"external"` (the AD image keys,
  e.g. `pil2M_image`, `pil900KW_image`). **This loops over ALL descriptors — it does NOT filter on
  `stream name == "primary"`.**
- `event` → for each event (in **any** stream), `single_doc_data = {k: data[k][0]}`, and for each
  external key present it does
  `target_template.format(det_name=…, N=…, det_type=…, **single_doc_data).format(**single_doc_data)`
  and symlinks the AD-written `.tif` to that destination.

**Consequences for per-arc streams:**
1. ✅ **Stream name is irrelevant to the linker.** Events in `arc0`/`arc20` are processed exactly
   like `primary` events, as long as they carry an external image key (they do). **No registration
   needed.**
2. ⚠️ **The filename only contains what `sample_name` (start doc) templates.** A `{token}` in
   `sample_name` is filled from **that event's** `single_doc_data`. So a token must be a data key
   present in the stream's events (this is the same `{x}` vs `{piezo_x}` / `KeyError` rule we already
   hardened in `acquire`). The linker has **no per-stream logic** beyond that.
3. ℹ️ Note the **double `.format(**single_doc_data)`** at the end: a token whose *value* is itself a
   string containing `{...}` would be re-expanded. Not relevant to the numeric arc/energy tokens, but
   worth knowing.

## 3. THE GAP: arc-economy runs have a bare `sample_name` (no arc/angle in the filename)

- `giwaxs_run`/`giwaxs_bar` pass
  `name_tokens=("ai{incident_angle}", "wa{waxs_arc}", "bpm{xbpm2_sumX}")` → `acquire` builds a
  **templated** `sample_name` like `S1_ai{incident_angle}_wa{waxs_arc}_bpm{xbpm2_sumX}_` → the linker
  fills those per frame → **correct per-arc/angle filenames.**
- `giwaxs_bar_arc_economy` goes through `multi_sample_run`, whose per-run md is just
  `{scan_name, geometry, md, sample.base_md()}`. `base_md()['sample_name']` is the **bare sample
  name** (`s.name`). So `target_template` becomes `{det_name}/S1_id1234_{N:06d}_{det_type}.tif` — with
  **no `{waxs_arc}` / `{incident_angle}`**. Frames don't collide (N is the global point index across
  all arcs/angles), but **the arc and angle are absent from the filename**, and the per-arc streams
  are not reflected in naming.

This is a **`smi-plans`-side** gap (the producer), not a workflow gap. Two ways to fix it:

### Fix A (DONE in `smi-plans`): `multi_sample_run` now takes a templated `sample_name`
- ✅ **Implemented.** `multi_sample_run` / `multi_sample_run_split` gained a `name_tokens=` param;
  when given, the per-run `sample_name` is `fname(sample.name, *name_tokens)` (a template) instead of
  the bare `base_md` name. `giwaxs_bar_arc_economy` now passes
  `["ai{incident_angle}", "wa{waxs_arc}", "bpm{xbpm2_sumX}"]` — the SAME tokens as `giwaxs_run`, so
  arc-economy filenames carry the arc + incident angle, matching the one-run-per-sample path. The
  arc-stream events record `incident_angle`/`waxs_arc`/`xbpm2_sumX`, so the linker can fill them.
- Test: `tests/test_multi_sample_assets.py::test_multi_sample_run_templated_sample_name_and_named_streams`
  asserts the start docs carry the templated `sample_name`, the per-arc named streams exist, and they
  carry the token data key. (Full suite: 137 passed.)
- **Hardware checkout still wanted:** confirm on a real arc-economy run that the symlinked filenames
  now include the arc/angle (the producer side is fixed + sim-tested; the live linker behavior is the
  `smi-workflows` side — see below).

### Fix B (workflow-side, only if you also want stream-aware behavior)
- The linker does not need stream awareness to *work*. If you want the workflow to, e.g., route
  `arc0` vs `arc20` to different analysis notebooks or subdirectories, you could branch on the
  **descriptor's stream name** (`doc['name']` on the descriptor) — but that is an enhancement, not
  required for correct symlinking.

## 4. Does anything need streams "registered" as primary-like? — NO

- The linker keys on **external descriptor keys**, not on a stream named `primary`. Per-arc streams
  are picked up automatically.
- There is **no place** in the current workflow where streams must be declared/registered to be
  consumed. (`declare_stream` in the plan is only about descriptor hygiene, not workflow discovery.)
- If a *future* consumer (a Tiled view, a suitcase exporter, a BestEffortCallback table) DOES assume
  `primary`, it would miss arc streams — but the **symlink linker does not.** Flag any such consumer
  separately if one exists.

## 5. Action items

### In `smi-workflows` (NSLS2/smi-workflows)
1. **Mark the legacy linker as legacy.** The team has confirmed the `linker.py`
   `target_template.format(**single_doc_data)` symlink flow is **not the current production path**
   (it is legacy). Add a header docstring / `# LEGACY:` marker to `linker.py` (and/or move it) so
   nobody assumes it is live. If it *is* still wired into a Prefect deployment, note which flow.
2. **Confirm the CURRENT consumer.** Document (in the workflow repo) what actually turns runs into
   on-disk files today — is it (a) the AreaDetector file plugin writing `.tif` per frame (which is
   stream-name-agnostic and needs nothing), plus (b) a current exporter/symlinker? Per-arc streams
   only matter to (b) if (b) filters on stream name; the legacy linker does not.
3. If a current consumer DOES filter on `stream == "primary"`, change it to iterate all streams
   carrying external keys (mirror the legacy linker's stream-agnostic loop), so `arc0`/`arc20` are
   included.

### In `smi-plans` (this repo)
4. ✅ **DONE (Fix A).** `multi_sample_run`/`_split` take `name_tokens=`; `giwaxs_bar_arc_economy`
   passes the GIWAXS tokens so arc-economy filenames carry arc + incident angle. Sim-tested. (Commit
   pending in this branch.) Remaining: a hardware checkout that the live symlink filenames include
   the arc/angle.

## 6. Quick reference — stream/naming contract for the workflow

- **Filename source:** the run **start** doc's `sample_name` (a template), filled per frame from that
  frame's **event** `data` keys. Path from `cycle` / `data_session` / `project_name` in the start doc.
- **Which streams carry images:** any stream whose descriptor has an `"external"` data key. Today:
  `primary` (normal plans) or `arc0`/`arc20`/… (arc-economy). The linker must process **all** of
  them (the legacy one already does).
- **Tokens must be real recorded keys** present in the carrying stream's events (`{energy_energy}`,
  `{waxs_arc}`, `{incident_angle}`, `{xbpm2_sumX}`, `{piezo_x}` — NOT `{energy}`/`{x}`). See
  `skills/naming-and-filename-tokens.md`.
