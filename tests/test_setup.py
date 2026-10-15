"""Stock size, part placement and work origin: the maths, the checks, and a simulated
cut of a shifted program."""

from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from cnc_assistant.planner.adapter import plan_for_job
from cnc_assistant.services.gcode import toolpath_svg, write_gcode
from cnc_assistant.services.geometry import analyse_stl, part_geometry
from cnc_assistant.services.setup import ORIGIN_XY, resolve_setup, setup_svg
from cnc_assistant.services.toolpath import THROUGH_EXTRA_MM, build_toolpaths

from gcode_sim import Sim
from test_gcode import ACRYLIC, SAMPLE, STEP_DOWN, TOOL, part_top

PART = {"dimensions": {"x": 80.0, "y": 50.0, "z": 6.0}}


def job(**kw):
    fields = dict(analysis=PART, stock_x_mm=None, stock_y_mm=None, stock_thickness_mm=None,
                  part_offset_x_mm=None, part_offset_y_mm=None, origin_xy=None, origin_z=None,
                  cut_from_stock=True)
    fields.update(kw)
    return NS(**fields)


def test_defaults_are_auto_stock_centred_front_left_top():
    s = resolve_setup(job(), tool_dia_mm=3.175)
    assert (s.stock_x, s.stock_y, s.stock_z) == (90.0, 60.0, 6.0)
    assert (s.part_x, s.part_y) == (5.0, 5.0)
    assert (s.origin_xy, s.origin_z) == ("stock_front_left", "stock_top")
    assert (s.dx, s.dy, s.dz) == (5.0, 5.0, 0.0)
    assert s.warnings == []


@pytest.mark.parametrize("origin, expected", [
    ("stock_front_left", (10.0, 20.0)),
    ("stock_front_right", (10.0 - 120.0, 20.0)),
    ("stock_back_left", (10.0, 20.0 - 100.0)),
    ("stock_back_right", (10.0 - 120.0, 20.0 - 100.0)),
    ("stock_center", (10.0 - 60.0, 20.0 - 50.0)),
    ("part_front_left", (0.0, 0.0)),
    ("part_center", (-40.0, -25.0)),
])
def test_each_origin(origin, expected):
    s = resolve_setup(job(stock_x_mm=120.0, stock_y_mm=100.0, part_offset_x_mm=10.0, part_offset_y_mm=20.0,
                          origin_xy=origin), tool_dia_mm=3.175)
    assert (s.dx, s.dy) == pytest.approx(expected)
    assert set(ORIGIN_XY) >= {origin}


def test_z_origin_at_stock_bottom_lifts_everything_by_the_stock_thickness():
    s = resolve_setup(job(stock_thickness_mm=8.0, origin_z="stock_bottom"), tool_dia_mm=3.175)
    assert s.dz == 8.0


def test_warnings():
    off = resolve_setup(job(stock_x_mm=85.0, stock_y_mm=60.0, part_offset_x_mm=10.0, part_offset_y_mm=5.0))
    assert any("runs off" in w for w in off.warnings)
    tight = resolve_setup(job(stock_x_mm=82.0, stock_y_mm=52.0), tool_dia_mm=3.175)
    assert any("little left to clamp" in w for w in tight.warnings)
    big = resolve_setup(job(stock_x_mm=320.0, stock_y_mm=60.0))
    assert any("bigger than the" in w for w in big.warnings)
    thick = resolve_setup(job(stock_thickness_mm=8.0))
    assert any("thicker than the part" in w for w in thick.warnings)
    thin = resolve_setup(job(stock_thickness_mm=4.0))
    assert any("thinner than the part" in w for w in thin.warnings)


def test_setup_diagram():
    svg = setup_svg(resolve_setup(job()))
    assert svg.startswith("<svg") and "setup-stock" in svg and "setup-origin" in svg


def test_shifted_program_cuts_the_same_part_in_the_right_place():
    """Sample plate in 100 x 70 x 6 stock, part at (12, 8), zeroed at the stock centre and the
    spoilboard: every move moves by the same offset, and the cut still checks out."""
    analysis = analyse_stl(SAMPLE, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    j = job(analysis=analysis, stock_x_mm=100.0, stock_y_mm=70.0, part_offset_x_mm=12.0, part_offset_y_mm=8.0,
            origin_xy="stock_center", origin_z="stock_bottom")
    setup = resolve_setup(j, tool_dia_mm=TOOL.cut_dia_mm)
    assert (setup.dx, setup.dy, setup.dz) == (12.0 - 50.0, 8.0 - 35.0, 6.0)

    plan_job = NS(name="Sample", analysis=analysis, cut_from_stock=True, stock_thickness_mm=None,
                  tool=TOOL, material=ACRYLIC)
    geom = part_geometry(SAMPLE, step_down_mm=STEP_DOWN, tool_dia_mm=TOOL.cut_dia_mm)
    result = build_toolpaths(plan_for_job(plan_job), geom, tool_dia_mm=TOOL.cut_dia_mm, cut_depth_mm=6.0)
    text, stats = write_gcode(result, job_name="Sample", tool=TOOL, material=ACRYLIC, cut_depth_mm=6.0,
                              safe_z_mm=5.0, setup=setup)

    assert "X0 Y0 = Centre of the stock" in text and "Z0 = Bottom of the stock" in text
    assert stats["min_z"] == pytest.approx(-THROUGH_EXTRA_MM)  # through cuts end just below the stock bottom (Z0)
    assert stats["max_z"] == pytest.approx(6.0 + 5.0)  # safe height is still 5 mm above the stock top
    assert stats["min_x"] == pytest.approx(setup.dx - TOOL.cut_dia_mm / 2, abs=0.01)

    # Simulate in the work frame: stock top at Z6, stock spans -50..50 x -35..35.
    s = Sim((-56, 56), (-41, 41), TOOL.cut_dia_mm, top=6.0)
    s.run(text)
    assert s.crashes == [] and s.feed_with_spindle_off == 0
    assert s.max_plunge <= STEP_DOWN + 0.05
    # Same checks as the unshifted test, at shifted positions.
    at = lambda x, y: s.at(x + setup.dx, y + setup.dy)  # noqa: E731  (part frame -> work frame)
    assert at(28, 22) == pytest.approx(6.0 - 2.0, abs=0.01)  # pocket floor
    assert at(6, 6) == pytest.approx(-THROUGH_EXTRA_MM, abs=0.01)  # Ø4 hole, through
    assert at(12, 22) == 6.0  # top surface untouched
    # No gouges: compare against the part shifted into the work frame.
    xs, ys = s.xs() - setup.dx, s.ys() - setup.dy
    top = part_top(geom, xs, ys) + 6.0
    assert (top - s.height)[np.isfinite(top)].max() < 0.02

    svg = toolpath_svg(result, geom["outline"], 6.0, setup=setup)
    assert "tp-stock" in svg
