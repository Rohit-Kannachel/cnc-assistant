"""Generate gcode for the sample part and check it by simulating the cut.

These are the safety checks to run after any change to toolpath.py or gcode.py.
"""

import re
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
import shapely

from cnc_assistant.planner.adapter import plan_for_job
from cnc_assistant.services.gcode import toolpath_svg, write_gcode
from cnc_assistant.services.geometry import analyse_stl, part_geometry
from cnc_assistant.services.toolpath import THROUGH_EXTRA_MM, build_toolpaths

from gcode_sim import Sim

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_plate.stl"
TOOL = NS(name="2-flute 3.175", tool_type="flat_end_mill", cut_dia_mm=3.175, flutes=2,
          material="carbide", flute_length_mm=12.0)
# The proven settings from the Fusion 360 programs on this machine.
ACRYLIC = NS(name="Acrylic", category="acrylic", spindle_rpm=10000, feed_rate_mmpm=150,
             plunge_rate_mmpm=50, step_down_mm=0.5, step_over_mm=1.4)
STEP_DOWN = ACRYLIC.step_down_mm


@pytest.fixture(scope="module")
def generated():
    analysis = analyse_stl(SAMPLE, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    job = NS(name="Sample", analysis=analysis, cut_from_stock=True, stock_thickness_mm=None,
             tool=TOOL, material=ACRYLIC)
    plan = plan_for_job(job)
    geom = part_geometry(SAMPLE, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    result = build_toolpaths(plan, geom, tool_dia_mm=TOOL.cut_dia_mm, cut_depth_mm=6.0)
    text, stats = write_gcode(result, job_name="Sample", tool=TOOL, material=ACRYLIC,
                              cut_depth_mm=6.0, safe_z_mm=5.0)
    return NS(plan=plan, geom=geom, result=result, text=text, stats=stats)


@pytest.fixture(scope="module")
def sim(generated):
    s = Sim((-6, 86), (-6, 56), TOOL.cut_dia_mm, res=0.2)
    s.run(generated.text)
    return s


def part_top(geom, xs, ys, margin=0.05):
    """Top surface of the finished part on the grid (-inf where there's no part).

    Walls are pulled back by `margin` so grid cells sitting exactly on a wall don't
    count as gouges.
    """
    X, Y = np.meshgrid(xs, ys)
    top = np.full(X.shape, -np.inf)
    inside = shapely.contains_xy(geom["outline"].buffer(-margin), X, Y)
    top[inside] = 0.0
    for f in geom["features"]:
        region = shapely.contains_xy(f["polygon"].buffer(margin), X, Y)
        bottom = -np.inf if f["through"] else -f["depth"]
        top[region] = np.minimum(top[region], bottom)
    return top


def test_gcode_is_plain_grbl(generated):
    for line in generated.text.splitlines():
        assert line.isascii(), line
        code = re.sub(r"\(.*?\)", "", line).strip()
        assert "(" not in code and ")" not in code, line
        if code:
            assert re.fullmatch(r"([GMXYZIJFSP]-?\d+(\.\d+)?\s*)+", code), line
    lines = generated.text.splitlines()
    assert "S10000 M3" in lines and lines[-1] == "M30" and "M5 (spindle off)" in lines


def test_no_rapid_crashes_and_spindle_always_on(sim):
    assert sim.crashes == []
    assert sim.feed_with_spindle_off == 0


def test_no_straight_plunges_deeper_than_a_step(sim):
    assert sim.max_plunge <= STEP_DOWN + 0.05


def test_finished_part_is_not_gouged(generated, sim):
    top = part_top(generated.geom, sim.xs(), sim.ys())
    gouge = (top - sim.height)[np.isfinite(top)]
    assert gouge.max() < 0.02, f"cut {gouge.max():.3f} mm into the part"


def test_features_are_cut_to_depth(generated, sim):
    through = -(6.0 + THROUGH_EXTRA_MM)
    assert sim.at(28, 22) == pytest.approx(-2.0, abs=0.01)  # pocket
    assert sim.at(40, 47) == pytest.approx(-3.0, abs=0.01)  # rebate
    assert sim.at(63.5, 22) == pytest.approx(-2.0, abs=0.01)  # counterbore ring
    assert sim.at(60, 22) == pytest.approx(through, abs=0.01)  # hole under the counterbore
    for x, y in [(6, 6), (74, 6), (6, 38), (74, 38)]:
        assert sim.at(x, y) == pytest.approx(through, abs=0.01)  # Ø4 holes
    assert sim.at(40, 40) == 0.0  # Ø2 hole: skipped (tool too big)
    assert sim.at(12, 22) == 0.0  # top surface untouched


def test_part_is_cut_free_except_for_tabs(generated, sim):
    outline_op = generated.plan["operations"][-1]
    loop = generated.geom["outline"].buffer(TOOL.cut_dia_mm / 2).exterior
    samples = [loop.interpolate(d) for d in np.linspace(0, loop.length, 2000, endpoint=False)]
    held = [sim.at(p.x, p.y) > -(6.0 + THROUGH_EXTRA_MM) + 0.05 for p in samples]
    # Count separate runs of "material still there" around the loop: one per tab.
    runs = sum(1 for i in range(len(held)) if held[i] and not held[i - 1])
    assert runs == outline_op["tab_count"]
    tab_top = -(6.0 + THROUGH_EXTRA_MM) + outline_op["tab_height_mm"]
    assert max(sim.at(p.x, p.y) for p in samples) == pytest.approx(tab_top, abs=0.01)


def test_skipped_ops_are_reported(generated):
    assert any("bigger than the hole" in s for s in generated.result.skipped)
    assert "SKIPPED" in generated.text


def test_stats_and_preview(generated):
    s = generated.stats
    assert s["min_z"] == pytest.approx(-(6.0 + THROUGH_EXTRA_MM))
    assert s["min_x"] == pytest.approx(-TOOL.cut_dia_mm / 2, abs=0.01)
    assert s["minutes"] > 0
    svg = toolpath_svg(generated.result, generated.geom["outline"], 6.0)
    assert svg.startswith("<svg") and "tp-cut" in svg


def test_simulator_catches_a_rapid_into_material():
    s = Sim((-6, 20), (-6, 20), 3.175)
    s.run("S10000 M3\nG0 X5 Y5 Z5\nG0 Z-1\n")
    assert s.crashes


def test_tool_sized_hole_is_pecked_on_a_plain_plate(tmp_path):
    import trimesh
    from shapely.geometry import Point, box

    plate = box(0, 0, 30, 20).difference(Point(15, 10).buffer(3.175 / 2, quad_segs=16))
    stl = tmp_path / "plate.stl"
    trimesh.creation.extrude_polygon(plate, 3.0).export(stl)

    analysis = analyse_stl(stl, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    job = NS(name="Plate", analysis=analysis, cut_from_stock=True, stock_thickness_mm=None,
             tool=TOOL, material=ACRYLIC)
    plan = plan_for_job(job)
    geom = part_geometry(stl, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    result = build_toolpaths(plan, geom, tool_dia_mm=TOOL.cut_dia_mm, cut_depth_mm=3.0)
    text, _ = write_gcode(result, job_name="Plate", tool=TOOL, material=ACRYLIC, cut_depth_mm=3.0, safe_z_mm=5.0)
    assert "peck drill" in text

    s = Sim((-6, 36), (-6, 26), TOOL.cut_dia_mm)
    s.run(text)
    assert s.crashes == [] and s.feed_with_spindle_off == 0
    assert s.max_plunge <= STEP_DOWN + 0.05
    assert s.at(15, 10) == pytest.approx(-(3.0 + THROUGH_EXTRA_MM), abs=0.01)  # hole drilled through
    top = part_top(geom, s.xs(), s.ys())
    assert (top - s.height)[np.isfinite(top)].max() < 0.02  # no gouges


def test_circles_are_written_as_arcs(generated, sim):
    arc_lines = [l for l in generated.text.splitlines() if l.startswith(("G2 ", "G3 "))]
    assert len(arc_lines) > 50
    assert sim.arcs == len(arc_lines)  # and the simulator cut along every one of them
    assert generated.stats["lines"] < 15000  # was ~31,000 lines as straight segments


def lid_like_part(tmp_path):
    """Ø60 x 5 mm disc: a 4 mm ring groove (Ø40-48, 2 mm deep) and a Ø16 through hole."""
    import trimesh
    from shapely.geometry import Point

    disc = Point(30, 30).buffer(30, quad_segs=32)
    lower = disc.difference(Point(30, 30).buffer(8, quad_segs=32))  # z 0..3: has the Ø16 hole
    groove = Point(30, 30).buffer(24, quad_segs=32).difference(Point(30, 30).buffer(20, quad_segs=32))
    upper = lower.difference(groove)  # z 3..5: also has the groove
    meshes = [trimesh.creation.extrude_polygon(lower, 3.0)]
    for piece in upper.geoms:  # the groove splits the top layer into a rim and a hub
        m = trimesh.creation.extrude_polygon(piece, 2.0)
        m.apply_translation([0, 0, 3.0])
        meshes.append(m)
    stl = tmp_path / "lid.stl"
    trimesh.util.concatenate(meshes).export(stl)
    return stl


def test_lid_groove_is_a_pocket_and_big_through_hole_is_profiled(tmp_path):
    stl = lid_like_part(tmp_path)
    analysis = analyse_stl(stl, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    types = sorted(f["type"] for f in analysis["features"])
    assert types == ["pocket", "through_hole"]  # the ring groove is not a "Ø48 hole"

    job = NS(name="Lid", analysis=analysis, cut_from_stock=True, stock_thickness_mm=None, tool=TOOL, material=ACRYLIC)
    plan = plan_for_job(job)
    geom = part_geometry(stl, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    result = build_toolpaths(plan, geom, tool_dia_mm=TOOL.cut_dia_mm, cut_depth_mm=5.0)
    text, _ = write_gcode(result, job_name="Lid", tool=TOOL, material=ACRYLIC, cut_depth_mm=5.0, safe_z_mm=5.0)
    assert "inside profile" in text

    s = Sim((-6, 66), (-6, 66), TOOL.cut_dia_mm)
    s.run(text)
    assert s.crashes == [] and s.feed_with_spindle_off == 0
    assert s.max_plunge <= STEP_DOWN + 0.05
    top = part_top(geom, s.xs(), s.ys())
    assert (top - s.height)[np.isfinite(top)].max() < 0.02  # no gouges
    through = -(5.0 + THROUGH_EXTRA_MM)
    assert s.at(30 + 8 - 1.0, 30) == pytest.approx(through, abs=0.01)  # wall of the Ø16 hole cut through
    assert s.at(30, 30) == 0.0  # centre left alone: it drops out as a slug
    assert s.at(30 + 22, 30) == pytest.approx(-2.0, abs=0.01)  # groove floor


def test_bores_use_the_milling_feed(generated):
    drill_ops = [op for op in generated.plan["operations"] if op["type"] == "drilling" and op["entry_type"] == "helical"]
    assert drill_ops and all(op["feed_rate_mmpm"] == ACRYLIC.feed_rate_mmpm for op in drill_ops)
