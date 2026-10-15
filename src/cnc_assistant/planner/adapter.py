"""Turn the app's data (STL analysis + tool/material library rows) into rules-engine inputs.

Keeping this translation here means rules_engine.py stays a plain, standalone module
you can run and test on its own, and a model-based planner can later reuse the same
inputs/outputs.
"""

from collections import defaultdict
from datetime import datetime, timezone

from . import rules_engine as engine

# Library tool types -> engine tool types
TOOL_TYPES = {
    "flat_end_mill": engine.ToolType.FLAT_END_MILL,
    "ball_end_mill": engine.ToolType.BALL_END_MILL,
    "v_bit": engine.ToolType.V_BIT,
    "drill": engine.ToolType.DRILL_BIT,
}

# Shallow, non-through features are treated as engraving (trace op) rather than pockets.
ENGRAVING_MAX_DEPTH_MM = 0.5

HOLE_TYPES = {"through_hole", "blind_hole"}


def to_engine_tool(tool):
    return engine.Tool(
        name=tool.name,
        tool_type=TOOL_TYPES.get(tool.tool_type, engine.ToolType.FLAT_END_MILL),
        diameter_mm=tool.cut_dia_mm,
        flutes=tool.flutes,
        material=tool.material,
        flute_length_mm=tool.flute_length_mm,
    )


def to_engine_material(material):
    return engine.Material(
        name=material.name,
        material_type=engine.MaterialType(material.category),
        spindle_rpm=material.spindle_rpm,
        feed_rate_mmpm=material.feed_rate_mmpm,
        plunge_rate_mmpm=material.plunge_rate_mmpm,
        step_down_mm=material.step_down_mm,
        step_over_mm=material.step_over_mm,
    )


def to_part_features(analysis, cut_from_stock=True, stock_thickness_mm=None):
    """Build PartFeatures from the geometry analysis.

    Returns (part, engraving_refs). Identical holes/pockets are grouped with a count,
    and every group remembers which feature numbers (1-based, as shown on the job
    page) it covers.
    """
    holes = defaultdict(list)  # (diameter, depth) -> [feature numbers]
    pockets = defaultdict(list)  # (width, height, depth) -> [feature numbers]
    engraving = []

    for num, f in enumerate(analysis["features"], start=1):
        through = f["type"] in ("through_hole", "through_cutout")
        if not through and f["depth"] - f["start_depth"] <= ENGRAVING_MAX_DEPTH_MM:
            engraving.append(num)
        elif f["type"] in HOLE_TYPES:
            holes[(round(f["diameter"], 2), round(f["depth"], 2))].append(num)
        else:  # pocket, through_cutout, step
            pockets[(round(f["size"][0], 2), round(f["size"][1], 2), round(f["depth"], 2))].append(num)

    dims = analysis["dimensions"]
    part = engine.PartFeatures(
        dim_x_mm=dims["x"],
        dim_y_mm=dims["y"],
        dim_z_mm=dims["z"],
        holes=[engine.Hole(diameter_mm=d, depth_mm=z, count=len(refs), feature_refs=refs)
               for (d, z), refs in holes.items()],
        pockets=[engine.Pocket(width_mm=w, height_mm=h, depth_mm=z, count=len(refs), feature_refs=refs)
                 for (w, h, z), refs in pockets.items()],
        has_engraving=bool(engraving),
        has_curved_walls=analysis.get("has_sloped_surfaces", False),
        cut_from_stock=cut_from_stock,
        stock_thickness_mm=stock_thickness_mm,
        # Older analyses have no perimeter; the engine then estimates it from the bounding box.
        outline_perimeter_mm=(analysis.get("outline") or {}).get("perimeter_mm"),
    )
    return part, engraving


def plan_for_job(job):
    """Run the rules engine for a Job that has been analysed. Returns a JSON-able dict."""
    analysis = job.analysis
    part, engraving_refs = to_part_features(
        analysis,
        cut_from_stock=job.cut_from_stock is not False,  # None (older jobs) counts as True
        stock_thickness_mm=job.stock_thickness_mm,
    )
    plan = engine.plan_job(
        job.name,
        part,
        to_engine_tool(job.tool),
        to_engine_material(job.material),
        engraving_refs=engraving_refs,
    )
    result = plan.to_dict()
    result["planner"] = "rules_engine"
    result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result
