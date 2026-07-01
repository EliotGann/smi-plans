"""Pure helpers for GUI-drawn scan regions.

The current supported region is a sample-relative polygon stored in ``sample.md['scan_regions']``.
This module deliberately has no shapely/bluesky/ophyd dependency so it can be used by GUIs and tests.
"""


__all__ = [
    "polygon_grid_offsets",
    "region_grid_offsets",
    "sample_region",
]


def polygon_grid_offsets(vertices, *, step_x, step_y, max_points=100000, include_boundary=False):
    """Return relative ``(dx, dy)`` grid offsets inside a polygon.

    Parameters
    ----------
    vertices : sequence of ``(x, y)``
        Polygon vertices in order, in sample-relative motor units. At least three non-collinear
        vertices are required.
    step_x, step_y : float
        Grid spacing along x and y. Must be positive.
    max_points : int
        Hard cap on returned points. If the bounding box would generate more than ``5*max_points``
        candidates, return ``([], True)`` immediately. If generation reaches ``max_points``, return
        the partial list and ``True``.
    include_boundary : bool
        If true, include points lying exactly on polygon edges. Default false matches the current
        GUI behavior, which uses strict interior containment.

    Returns
    -------
    (points, truncated) : tuple
        ``points`` is a list of ``(dx, dy)`` tuples. ``truncated`` is true when generation was capped.
    """
    try:
        step_x = float(step_x)
        step_y = float(step_y)
    except (TypeError, ValueError):
        return [], False
    if step_x <= 0 or step_y <= 0:
        return [], False

    pts = _clean_vertices(vertices)
    if len(pts) < 3 or abs(_polygon_area(pts)) == 0:
        return [], False

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    estimate = ((xmax - xmin) / step_x + 1) * ((ymax - ymin) / step_y + 1)
    if estimate > 5 * int(max_points):
        return [], True

    out = []
    x_values = _axis_values(xmin, xmax, step_x)
    y_values = _axis_values(ymin, ymax, step_y)
    for x in x_values:
        for y in y_values:
            if _point_in_polygon(x, y, pts, include_boundary=include_boundary):
                out.append((float(x), float(y)))
                if len(out) >= int(max_points):
                    return out, True
    return out, False


def sample_region(sample, name):
    """Return a named scan region from a sample.

    Looks first for a future typed ``sample.regions`` collection, then falls back to the transitional
    ``sample.md['scan_regions']`` list. Raises ``KeyError`` if no matching region exists.
    """
    regions = getattr(sample, "regions", None)
    if isinstance(regions, dict):
        if name in regions:
            return regions[name]
    elif regions:
        for region in regions:
            if _region_get(region, "name") == name:
                return region

    md = getattr(sample, "md", {}) or {}
    for region in md.get("scan_regions", []) or []:
        if _region_get(region, "name") == name:
            return region
    raise KeyError("No scan region {!r} on sample {!r}".format(
        name, getattr(sample, "name", None)))


def region_grid_offsets(region, *, step_x=None, step_y=None, max_points=None,
                        include_boundary=None):
    """Return polygon grid offsets from a region dict/object.

    Reads the transitional region schema used by ``sample.md['scan_regions']``. Explicit keyword
    arguments override the region's ``grid`` settings.
    """
    kind = _region_get(region, "kind", "polygon")
    if kind != "polygon":
        raise ValueError("only polygon scan regions are supported, got {!r}".format(kind))
    vertices = _region_get(region, "vertices", [])
    grid = _region_get(region, "grid", {}) or {}
    step_x = grid.get("step_x") if step_x is None else step_x
    step_y = grid.get("step_y") if step_y is None else step_y
    max_points = grid.get("max_points", 100000) if max_points is None else max_points
    include_boundary = (grid.get("include_boundary", False)
                        if include_boundary is None else include_boundary)
    if step_x is None or step_y is None:
        raise ValueError("polygon region {!r} must define grid.step_x and grid.step_y".format(
            _region_get(region, "name", "")))
    return polygon_grid_offsets(
        vertices,
        step_x=step_x,
        step_y=step_y,
        max_points=max_points,
        include_boundary=include_boundary,
    )


def _clean_vertices(vertices):
    pts = []
    for item in vertices or []:
        if len(item) < 2:
            continue
        pts.append((float(item[0]), float(item[1])))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def _axis_values(start, stop, step):
    values = []
    i = 0
    # Include the upper edge as a candidate; strict containment will trim boundaries by default.
    limit = stop + step / 2.0
    value = start
    while value <= limit:
        values.append(value)
        i += 1
        value = start + i * step
    return values


def _polygon_area(vertices):
    area = 0.0
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return 0.5 * area


def _point_in_polygon(x, y, vertices, *, include_boundary=False):
    inside = False
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return bool(include_boundary)
        crosses = (y1 > y) != (y2 > y)
        if crosses:
            x_intersect = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x_intersect > x:
                inside = not inside
    return inside


def _point_on_segment(px, py, x1, y1, x2, y2, *, tol=1e-12):
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > tol:
        return False
    return (min(x1, x2) - tol <= px <= max(x1, x2) + tol and
            min(y1, y2) - tol <= py <= max(y1, y2) + tol)


def _region_get(region, key, default=None):
    if isinstance(region, dict):
        return region.get(key, default)
    return getattr(region, key, default)
