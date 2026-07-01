"""Tests for pure polygon/region helpers."""

import pytest

from smi_plans import Position, Sample, polygon_grid_offsets, region_grid_offsets, sample_region


def test_polygon_grid_offsets_square_strict_interior():
    pts, truncated = polygon_grid_offsets(
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        step_x=0.1,
        step_y=0.1,
    )
    assert not truncated
    assert 60 <= len(pts) <= 121
    assert all(0.0 < x < 1.0 and 0.0 < y < 1.0 for x, y in pts)


def test_polygon_grid_offsets_triangle():
    pts, truncated = polygon_grid_offsets(
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
        step_x=0.1,
        step_y=0.1,
    )
    assert not truncated
    assert pts
    for x, y in pts:
        assert x >= y - 1e-9


def test_polygon_grid_offsets_degenerate():
    assert polygon_grid_offsets([(0.0, 0.0), (1.0, 0.0)], step_x=0.1, step_y=0.1) == ([], False)
    assert polygon_grid_offsets(
        [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)], step_x=0.1, step_y=0.1) == ([], False)
    assert polygon_grid_offsets(
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)], step_x=0.0, step_y=0.1) == ([], False)


def test_polygon_grid_offsets_dense_bails_out():
    pts, truncated = polygon_grid_offsets(
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
        step_x=0.0001,
        step_y=0.0001,
        max_points=100,
    )
    assert truncated
    assert pts == []


def test_sample_region_from_md():
    sample = Sample(name="s1", md={"scan_regions": [
        {"name": "roi1", "kind": "polygon", "vertices": [[0, 0], [1, 0], [1, 1]]},
        {"name": "roi2", "kind": "polygon", "vertices": [[0, 0], [2, 0], [2, 2]]},
    ]})
    assert sample_region(sample, "roi2")["vertices"] == [[0, 0], [2, 0], [2, 2]]


def test_region_grid_offsets_reads_region_grid():
    region = {
        "name": "roi",
        "kind": "polygon",
        "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
        "grid": {"step_x": 0.5, "step_y": 0.5},
    }
    pts, truncated = region_grid_offsets(region)
    assert not truncated
    assert len(pts) == 9


def test_sample_region_missing():
    with pytest.raises(KeyError, match="roi"):
        sample_region(Sample(name="s1"), "roi")


def _sample_with_region():
    return Sample(
        name="s1",
        nominal=Position(frame="holder", piezo_x=10.0, piezo_y=20.0, piezo_z=5.0),
        md={"scan_regions": [{
            "name": "roi",
            "kind": "polygon",
            "axes": ["piezo_x", "piezo_y"],
            "vertices": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
            "grid": {"step_x": 0.5, "step_y": 0.5},
        }]},
    )


def test_polygon_region_run_records_correlated_points(sim, inject):
    C = inject("smi_plans._compose")
    sample = _sample_with_region()
    result = sim.run(C.polygon_region_run(
        sample,
        "roi",
        [sim.pil900KW],
        reads=[sim.piezo],
        geometry="transmission",
    ))

    sim.assert_one_run(result)
    assert sim.primary_events(result) == 9
    events = [doc for name, doc in result.docs if name == "event"]
    assert {"x", "y", "region_name", "piezo_x", "piezo_y"}.issubset(events[0]["data"])
    visited = {(ev["data"]["piezo_x"], ev["data"]["piezo_y"]) for ev in events}
    assert (10.0, 20.0) in visited
    assert all(9.0 < x < 11.0 and 19.0 < y < 21.0 for x, y in visited)


def test_polygon_region_run_rejects_empty_region(sim, inject):
    C = inject("smi_plans._compose")
    sample = Sample(
        name="s1",
        nominal=Position(frame="holder", piezo_x=10.0, piezo_y=20.0),
        md={"scan_regions": [{
            "name": "empty", "kind": "polygon",
            "vertices": [[0, 0], [1, 0], [2, 0]],
            "grid": {"step_x": 1.0, "step_y": 1.0},
        }]},
    )
    with pytest.raises(ValueError, match="contains no grid points"):
        sim.run(C.polygon_region_run(sample, "empty", [sim.pil900KW], reads=[sim.piezo]))
