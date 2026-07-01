# Polygon / Region Scans — GUI Contract and Backend Plan

## Status

This documents the current `smi-acquire` polygon quick-script behavior and the recommended
`smi-plans` direction for absorbing polygon regions centrally. `smi-acquire` now includes the
former `swaxs-beam-image` on-axis viewer and a wizard that uses `smi-plans` as its backend.

The central `smi_plans` polygon-region API now has two layers:

- pure helpers: `polygon_grid_offsets(...)`, `region_grid_offsets(...)`, and `sample_region(...)`;
- acquisition helpers: `smi_plans._compose.polygon_region_run(...)` and
  `smi_plans._compose.polygon_region_bar(...)`.

The GUI can use the pure helpers without importing Bluesky or beamline devices, and can now generate
thin acquisition scripts against the backend helpers instead of emitting large explicit point lists.

## Current smi-acquire Behavior

The current Polygon mode has two related modes.

### Single Drawn Polygon

When no sample/bookmark is selected as a scan target:

- The user draws a polygon on the on-axis camera.
- The GUI immediately converts polygon vertices from pixels to motor coordinates.
- The polygon source of truth is therefore motor-space vertices, not screen pixels.
- The GUI generates a rectangular grid at `step_x` / `step_y`.
- It keeps only grid points that fall inside the polygon.
- The quick script emits an explicit `bp.list_scan(...)` over `x`, `y`, and `z` lists.

This is an ad-hoc one-off scan area. It is useful during alignment, but it is not yet a durable
sample fact.

### Polygon Replicated At Samples / Bookmarks

When one or more samples/bookmarks are selected as scan targets:

- The drawn polygon is treated as a shape template.
- The GUI computes the polygon-grid points in motor space.
- It computes the centroid of those grid points.
- It stores grid offsets as `(dx, dy)` relative to that centroid.
- For each selected sample/bookmark, the script reconstructs the grid as:

```python
xs = [sample_center_x + dx for dx in dxs]
ys = [sample_center_y + dy for dy in dys]
zs = [sample_z] * len(xs)
```

- The quick script currently emits explicit offset lists and `bp.list_scan(...)` calls.

This is the correct *geometry* for replication, but the wrong long-term API boundary: the polygon
shape and target samples should be referenced from the sample store / `smi-plans`, not pasted as
large literal lists.

## Why This Should Move Into smi-plans

Grid and line quick scripts are now easy to thin because they map cleanly onto existing backend
helpers:

- square/grid -> `spatial_grid_axes(..., center=(cx, cy))`
- line -> `motor_axis(...)` centered on `sample.runnable_position()`
- sample positioning -> `acquire_bar(...)` -> `goto_sample(...)`

Polygon is different because it is not just `nx`, `ny`, `dx`, `dy`; it is a clipped region. The
backend needs a first-class way to say:

> For this sample, scan a grid of offsets inside this named polygon region, centered on the
> sample's runnable position.

Once `smi-plans` owns that concept, `smi-acquire` can generate a thin script like:

```python
bar = load_holder("bar1")

yield from polygon_region_bar(
    bar, "roi1", dets,
    reads=[piezo],
    name_tokens=("x{x}", "y{y}"),
    scan_name="polygon_roi",
)
```

The backend helper would use each sample's `runnable_position()` so refined positions work
automatically.

## Transitional Schema Using Sample Metadata

Until `smi-plans` grows typed region dataclasses, the cleanest available storage location is
`Sample.md`. This is not ideal as a final model, but it is compatible with the current shared
Redis store and can be used by both GUI and plans without a schema migration.

Recommended transitional key:

```python
sample.md["scan_regions"] = [
    {
        "name": "roi1",
        "kind": "polygon",
        "axes": ["piezo_x", "piezo_y"],
        "frame": "sample_relative",
        "vertices": [
            [-250.0, -100.0],
            [ 250.0, -100.0],
            [ 300.0,  120.0],
            [-200.0,  160.0],
        ],
        "grid": {
            "step_x": 25.0,
            "step_y": 25.0,
            "snake": True,
            "max_points": 100000,
        },
        "md": {
            "source": "smi-acquire polygon tool",
        },
    }
]
```

Important details:

- `vertices` are offsets from the sample center, not absolute lab coordinates.
- The sample center is `sample.runnable_position()` at runtime.
- `axes` names are `Position` fields, not device names.
- For the on-axis view, the first supported axes should be `['piezo_x', 'piezo_y']`.
- If later needed, wide/top camera regions could use `['piezo_z', 'piezo_x']`.
- The helper should reject unknown axes explicitly.

This schema intentionally stores the polygon, not the expanded grid. The grid is derived at
runtime from the region plus current sample position. That keeps stored metadata compact and lets
changing `step_x` / `step_y` be a small edit.

## Preferred Typed Model

The better long-term model is a typed region object in `smi_plans._samples`, e.g.:

```python
@dataclass
class ScanRegion:
    name: str
    kind: str = "polygon"
    axes: tuple[str, str] = ("piezo_x", "piezo_y")
    frame: str = "sample_relative"
    vertices: list[tuple[float, float]] = field(default_factory=list)
    grid: dict = field(default_factory=dict)
    md: dict = field(default_factory=dict)
```

Then `Sample` can eventually carry:

```python
regions: list[ScanRegion] = field(default_factory=list)
```

or a keyed mapping:

```python
regions: dict[str, ScanRegion] = field(default_factory=dict)
```

The typed model is preferable because regions become first-class sample facts like positions,
alignments, and scan history. It also enables validation at import/write time rather than at plan
construction time.

## Proposed smi-plans Helper API

### Pure Helpers

Pure helpers, importable by GUIs and tests:

```python
def polygon_grid_offsets(vertices, *, step_x, step_y, max_points=100000):
    """Return relative (dx, dy) grid offsets inside a polygon."""

def region_grid_offsets(region, *, step_x=None, step_y=None, max_points=None):
    """Return relative offsets using the region's grid block, with optional overrides."""

def sample_region(sample, name):
    """Return a region by name from sample.regions or sample.md['scan_regions']."""
```

Implemented in `smi_plans._regions` with a dependency-free point-in-polygon implementation. This
keeps the core package light even though `smi-acquire` / the former `swaxs-beam-image` may use
`shapely` internally for its own GUI geometry.

### Plan / Axis Helper

Add composable acquisition helpers in `_compose.py`:

```python
def polygon_region_run(sample, region_name, dets, *, reads=None, name_tokens=None, ...):
    """Acquire one sample's named polygon region as correlated (x, y) points."""

def polygon_region_bar(samples, region_name, dets, *, reads=None, ...):
    """Run polygon_region_run over a bar/list of samples."""
```

Implementation choice: polygon regions are a correlated list of `(x, y)` pairs, so the backend uses
a small point-list acquisition helper rather than pretending the points are independent `ScanAxis`
objects. Each event records relative Signals named `x` and `y`, plus a `region_name` Signal, and
the caller should include the parent motor device (usually `piezo`) in `reads` for absolute motor
provenance.

## Filename / Naming Contract

Polygon region scans should follow the newer token guidance:

- Do not bake `.position`, `.get()`, or formatted live motor reads into filenames.
- Record relative offsets as Signals named `x` and `y`, so `{x}` / `{y}` are valid tokens.
- Also record absolute motor positions (`{piezo_x}`, `{piezo_y}`, etc.) for provenance.
- Use `bar_name_tokens(...)` / `apply_name_prefix(...)` for user-customized naming.

Suggested default name tokens:

```python
name_tokens=("x{x}", "y{y}")
```

Optionally include a region token if the helper records one:

```python
name_tokens=("region{region_name}", "x{x}", "y{y}")
```

If `region_name` is desired in filenames, the helper must record a `Signal(name="region_name")`
or equivalent data key. Do not put `{region_name}` in a template unless it is actually recorded.

## Recommended Implementation Order

1. Add pure `ScanRegion` parsing helpers that read both future typed fields and the transitional
   `sample.md['scan_regions']` schema. **Done for `sample_region(...)`.**
2. Add tests for polygon-grid expansion and validation. **Done for `polygon_grid_offsets(...)`.**
3. Add a backend polygon-region acquisition helper that positions via `sample.runnable_position()`.
   **Done: `polygon_region_run(...)` / `polygon_region_bar(...)`.**
4. Update `smi-acquire` to store drawn polygons as `scan_regions` and emit thin
   `polygon_region_bar(...)` / `polygon_region_run(...)` scripts.
5. Only then consider migrating regions out of `md` into a typed `Sample.regions` field.

## Open Questions

- Should regions be per-sample only, or can a holder define reusable holder-level regions that
  samples inherit?
- Should region vertices be sample-relative offsets, holder-relative coordinates, or support both?
- Should polygon scans record a compact `ScanRecord.spots` summary as one polygon region or every
  individual grid point?
- Is `shapely` acceptable in `smi-plans` core, or should polygon point-inclusion be dependency-free?
  **Resolved for now: dependency-free in core.**

## Current GUI Guidance

Until the backend acquisition helper exists, `smi-acquire` should continue to:

- generate thin `polygon_region_bar(...)` / `polygon_region_run(...)` scripts once drawn polygons
  are stored as `sample.md['scan_regions']`,
- keep grid/line quick scripts thinned through `acquire_bar` helpers,
- keep explicit point-list fallback only for one-off polygons that are not saved into the sample
  store.
