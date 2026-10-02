"""Pure correlated fresh-spot paths; one XY pair per exposure, not nested axes."""

import math


def fresh_spot_grid(count, *, dx=100.0, dy=10.0, rows=None, anchor="center",
                    block=0, shift=(0.0, 0.0)):
    """Return sample-relative snake offsets in motor units (normally micrometers).

    ``rows=None`` fits a physically near-square footprint (not equal X/Y counts).
    Explicit rows gives fixed column length: 20 Y moves means rows=21.
    Anchors: center (bounding-box center); top_middle (X centered, Y >= 0);
    top_left (X,Y >= 0). 'Top' is a naming convention: positive Y is explicit.
    block advances by full grid width + one X step, retaining the original origin
    for recovery. shift adds an explicit XY translation, e.g. a motor-limit margin.
    No persistent dose history and no motor/sample-footprint checks are implied.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    if any(not math.isfinite(v) or v <= 0 for v in (dx, dy)):
        raise ValueError("dx/dy must be finite and positive")
    if not isinstance(block, int) or block < 0:
        raise ValueError("block must be a nonnegative integer")
    if anchor not in ("center", "top_middle", "top_left"):
        raise ValueError("Unknown anchor")
    if len(shift) != 2 or not all(math.isfinite(v) for v in shift):
        raise ValueError("shift must be a finite XY pair")
    if rows is None:
        nx, ny = min(((x, math.ceil(count / x)) for x in range(1, count + 1)),
                     key=lambda shape: (abs((shape[0] - 1) * dx - (shape[1] - 1) * dy),
                                        shape[0] * shape[1] - count))
    else:
        if not isinstance(rows, int) or rows < 1:
            raise ValueError("rows must be a positive integer")
        ny = min(rows, count)
        nx = math.ceil(count / ny)
    points = []
    for i in range(count):
        column, row = divmod(i, ny)
        points.append((column * dx, (row if column % 2 == 0 else ny - 1 - row) * dy))
    x_center = (nx - 1) * dx / 2 if anchor != "top_left" else 0
    y_center = (ny - 1) * dy / 2 if anchor == "center" else 0
    return [(x - x_center + block * nx * dx + shift[0], y - y_center + shift[1]) for x, y in points]
