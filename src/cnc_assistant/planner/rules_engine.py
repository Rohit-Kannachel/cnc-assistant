"""
CNC Rules Engine
================
A pure rules-based operation planner for the Genmitsu 3018 Pro.
No ML, no external dependencies beyond Python standard library.

Usage (demo with the built-in test cases):
    python -m cnc_assistant.planner.rules_engine

Or import and use programmatically:
    from cnc_assistant.planner.rules_engine import plan_job, PartFeatures, Tool, Material

In the app, planner/adapter.py builds the inputs from the STL analysis and the
tool/material libraries, so this module stays independent of Flask and the DB.

Changes made when integrating into the app are marked "CHANGED:".
"""

import json
import math
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OperationType(str, Enum):
    DRILLING  = "drilling"
    ROUGHING  = "roughing"
    POCKETING = "pocketing"
    TRACE     = "trace"
    CONTOUR   = "contour"
    OUTLINE   = "outline"


class MaterialType(str, Enum):
    ACRYLIC   = "acrylic"
    WOOD_SOFT = "wood_soft"   # pine, MDF
    WOOD_HARD = "wood_hard"   # oak, maple
    FOAM      = "foam"
    PCB       = "pcb"
    ALUMINIUM = "aluminium"


class ToolType(str, Enum):
    FLAT_END_MILL = "flat_end_mill"
    BALL_END_MILL = "ball_end_mill"
    V_BIT         = "v_bit"
    DRILL_BIT     = "drill_bit"


# ---------------------------------------------------------------------------
# Input data classes
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    tool_type: ToolType
    diameter_mm: float
    flutes: int
    material: str = "carbide"           # carbide | hss
    # CHANGED: Optional. The tool library doesn't require it, and assuming 15mm
    # would produce wrong "can reach" answers. None = unknown, depth not checked.
    flute_length_mm: Optional[float] = None

    @property
    def radius_mm(self):
        return self.diameter_mm / 2


@dataclass
class Material:
    name: str
    material_type: MaterialType
    # If not provided, the engine calculates these from material type + tool
    spindle_rpm: Optional[int] = None
    feed_rate_mmpm: Optional[float] = None
    plunge_rate_mmpm: Optional[float] = None
    step_down_mm: Optional[float] = None
    step_over_mm: Optional[float] = None


@dataclass
class Hole:
    diameter_mm: float
    depth_mm: float
    count: int = 1
    # CHANGED: which features (1-based numbers from the STL analysis) this covers,
    # so the plan can point at them and toolpath generation can find their positions.
    feature_refs: List[int] = field(default_factory=list)


@dataclass
class Pocket:
    width_mm: float
    height_mm: float
    depth_mm: float
    count: int = 1
    feature_refs: List[int] = field(default_factory=list)  # CHANGED: see Hole

    @property
    def area_mm2(self):
        return self.width_mm * self.height_mm


@dataclass
class PartFeatures:
    """
    Geometry facts about the part. In the app these come from the trimesh
    analysis of the STL (see planner/adapter.py).
    """
    # Overall dimensions
    dim_x_mm: float
    dim_y_mm: float
    dim_z_mm: float                     # total part thickness / height

    # Features present
    holes: List[Hole] = field(default_factory=list)
    pockets: List[Pocket] = field(default_factory=list)
    has_engraving: bool = False         # trace operation needed
    has_curved_walls: bool = False      # contour finish needed
    cut_from_stock: bool = True         # needs outline cut to free part

    # Stock info
    stock_thickness_mm: Optional[float] = None  # if None, assumed = dim_z_mm

    # CHANGED: the real outline length from the STL analysis, when known.
    outline_perimeter_mm: Optional[float] = None

    @property
    def is_flat(self) -> bool:
        """Part height under 8mm — almost certainly 2.5D."""
        return self.dim_z_mm <= 8.0

    @property
    def perimeter_mm(self) -> float:
        """Perimeter for tab count calculation: the real outline length if known,
        otherwise a rough estimate from the bounding box."""
        if self.outline_perimeter_mm:  # CHANGED: prefer the measured outline
            return self.outline_perimeter_mm
        return 2 * (self.dim_x_mm + self.dim_y_mm)

    @property
    def has_holes(self) -> bool:
        return len(self.holes) > 0

    @property
    def has_pockets(self) -> bool:
        return len(self.pockets) > 0

    @property
    def cutting_depth_mm(self) -> float:
        return self.stock_thickness_mm or self.dim_z_mm


# ---------------------------------------------------------------------------
# Output data classes
# ---------------------------------------------------------------------------

@dataclass
class Operation:
    order: int
    op_type: OperationType
    description: str
    spindle_rpm: int
    feed_rate_mmpm: float
    plunge_rate_mmpm: float
    step_down_mm: float
    step_over_mm: Optional[float]
    depth_mm: float
    entry_type: str                     # ramp | helical | plunge
    tabs: bool = False
    tab_count: Optional[int] = None
    tab_width_mm: float = 3.0
    tab_height_mm: float = 1.0
    warnings: List[str] = field(default_factory=list)
    notes: str = ""
    feature_refs: List[int] = field(default_factory=list)  # CHANGED: see Hole


@dataclass
class OperationPlan:
    part_name: str
    tool: Tool
    material: Material
    operations: List[Operation]
    global_warnings: List[str]
    summary: str

    def to_dict(self) -> dict:
        return {
            "part_name": self.part_name,
            "tool": {
                "name": self.tool.name,
                "diameter_mm": self.tool.diameter_mm,
                "flutes": self.tool.flutes,
                "type": self.tool.tool_type.value,
            },
            "material": self.material.name,
            "summary": self.summary,
            "global_warnings": self.global_warnings,
            "operations": [
                {
                    "order": op.order,
                    "type": op.op_type.value,
                    "description": op.description,
                    "spindle_rpm": op.spindle_rpm,
                    "feed_rate_mmpm": op.feed_rate_mmpm,
                    "plunge_rate_mmpm": op.plunge_rate_mmpm,
                    "step_down_mm": op.step_down_mm,
                    "step_over_mm": op.step_over_mm,
                    "depth_mm": op.depth_mm,
                    "entry_type": op.entry_type,
                    "tabs": op.tabs,
                    "tab_count": op.tab_count,
                    "tab_width_mm": op.tab_width_mm if op.tabs else None,
                    "tab_height_mm": op.tab_height_mm if op.tabs else None,
                    "notes": op.notes,
                    "warnings": op.warnings,
                    "feature_refs": op.feature_refs,  # CHANGED: see Hole
                }
                for op in self.operations
            ],
        }

    def pretty_print(self):
        print("=" * 60)
        print(f"  OPERATION PLAN: {self.part_name}")
        print("=" * 60)
        print(f"  Tool     : {self.tool.name} ({self.tool.diameter_mm}mm, {self.tool.flutes}-flute)")
        print(f"  Material : {self.material.name}")
        print(f"  Summary  : {self.summary}")

        if self.global_warnings:
            print()
            print("  WARNINGS:")
            for w in self.global_warnings:
                print(f"     - {w}")

        print()
        print(f"  {len(self.operations)} OPERATION(S):")
        print()

        for op in self.operations:
            print(f"  [{op.order}] {op.op_type.value.upper()}")
            print(f"      {op.description}")
            print(f"      RPM: {op.spindle_rpm}  |  Feed: {op.feed_rate_mmpm} mm/min  |  Plunge: {op.plunge_rate_mmpm} mm/min")
            step_over_str = f"{op.step_over_mm}mm" if op.step_over_mm is not None else "N/A"
            print(f"      Step down: {op.step_down_mm}mm  |  Step over: {step_over_str}  |  Depth: {op.depth_mm}mm")
            print(f"      Entry: {op.entry_type}")
            if op.tabs:
                print(f"      Tabs: {op.tab_count} tabs  ({op.tab_width_mm}mm wide x {op.tab_height_mm}mm tall)")
            if op.notes:
                print(f"      Note: {op.notes}")
            for w in op.warnings:
                print(f"      WARNING: {w}")
            print()

        print("=" * 60)


# ---------------------------------------------------------------------------
# Feed & Speed tables
# ---------------------------------------------------------------------------

# Base chip load per tooth (mm) by material type
# chip_load × RPM × flutes = feed rate
CHIP_LOAD = {
    MaterialType.FOAM:      0.05,
    MaterialType.ACRYLIC:   0.015,
    MaterialType.PCB:       0.010,
    MaterialType.WOOD_SOFT: 0.020,
    MaterialType.WOOD_HARD: 0.015,
    MaterialType.ALUMINIUM: 0.008,
}

# Recommended spindle RPM by material
SPINDLE_RPM = {
    MaterialType.FOAM:      18000,
    MaterialType.ACRYLIC:   10000,
    MaterialType.PCB:       12000,
    MaterialType.WOOD_SOFT: 15000,
    MaterialType.WOOD_HARD: 12000,
    MaterialType.ALUMINIUM: 8000,
}

# Step down as fraction of tool diameter, by material
STEP_DOWN_RATIO = {
    MaterialType.FOAM:      1.0,    # can be aggressive
    MaterialType.ACRYLIC:   0.10,   # conservative — melts easily
    MaterialType.PCB:       0.08,
    MaterialType.WOOD_SOFT: 0.25,
    MaterialType.WOOD_HARD: 0.15,
    MaterialType.ALUMINIUM: 0.05,   # very conservative on 3018
}

# Step over as fraction of tool diameter
STEP_OVER_RATIO = {
    "roughing":  0.50,
    "pocketing": 0.45,
    "contour":   0.15,
    "finishing": 0.10,
}

# Machine limits
MACHINE = {
    "bed_x_mm": 300,
    "bed_y_mm": 180,
    "max_z_travel_mm": 45,
    "max_feed_mmpm": 800,
    "max_spindle_rpm": 10000,   # typical 3018 spindle limit (some go to 12000)
}

# CHANGED: hole diameters from a mesh are never exactly equal to the tool's,
# so "matches the tool" uses this tolerance instead of ==.
DIAMETER_MATCH_TOL_MM = 0.05


# ---------------------------------------------------------------------------
# Parameter calculation helpers
# ---------------------------------------------------------------------------

def calc_feeds(tool: Tool, material: Material) -> dict:
    """
    Calculate feeds and speeds from tool + material.
    If material has explicit overrides, use those instead.
    """
    mat = material.material_type

    rpm = min(
        material.spindle_rpm or SPINDLE_RPM[mat],
        MACHINE["max_spindle_rpm"]
    )

    chip_load = CHIP_LOAD[mat]
    feed = material.feed_rate_mmpm or round(rpm * tool.flutes * chip_load, 1)
    feed = min(feed, MACHINE["max_feed_mmpm"])

    step_down = material.step_down_mm or round(tool.diameter_mm * STEP_DOWN_RATIO[mat], 2)
    plunge = material.plunge_rate_mmpm or round(feed * 0.25, 1)

    return {
        "rpm": rpm,
        "feed": feed,
        "plunge": plunge,
        "step_down": step_down,
        # CHANGED: pass the material's step-over through (it was never used before),
        # so your tuned value from the material library wins over the ratio table.
        "step_over": material.step_over_mm,
    }


def calc_step_over(tool: Tool, mode: str = "pocketing") -> float:
    ratio = STEP_OVER_RATIO.get(mode, 0.45)
    return round(tool.diameter_mm * ratio, 2)


# CHANGED: tab spacing/size from the Fusion 360 programs that worked on this machine
# and acrylic: 9 tabs on a ~260 mm outline (~29 mm apart), ~2.6 mm bridges, ~1 mm tall.
TAB_SPACING_MM = 28.0
TAB_WIDTH_MM = 2.5
TAB_MIN, TAB_MAX = 4, 12


def calc_tab_count(perimeter_mm: float) -> int:
    """
    Rule: one tab per ~28mm of outline, minimum 4, maximum 12.
    (CHANGED: was one per ~50mm, maximum 8.)
    """
    count = round(perimeter_mm / TAB_SPACING_MM)
    return max(TAB_MIN, min(TAB_MAX, count))


def calc_passes(depth_mm: float, step_down_mm: float) -> int:
    return math.ceil(depth_mm / step_down_mm)


def needs_roughing(part: PartFeatures, tool: Tool, material: Material) -> bool:
    """
    Roughing is worthwhile when:
    - Part or pocket is deep (> 4× step down in a single pocket)
    - Material is hard enough that aggressive bulk removal is separate from finishing
    - Not foam or very soft materials (just go straight to finish)
    """
    if material.material_type == MaterialType.FOAM:
        return False
    feeds = calc_feeds(tool, material)
    step_down = feeds["step_down"]

    if part.has_pockets:
        deepest = max((p.depth_mm for p in part.pockets), default=0)
        if deepest > step_down * 4:
            return True

    if part.dim_z_mm > step_down * 6 and part.cut_from_stock:
        return True

    return False


def tool_can_reach(depth_mm: float, tool: Tool) -> bool:
    if tool.flute_length_mm is None:  # CHANGED: unknown flute length -> don't warn
        return True
    return depth_mm <= tool.flute_length_mm


# ---------------------------------------------------------------------------
# Global warnings
# ---------------------------------------------------------------------------

def check_global_warnings(part: PartFeatures, tool: Tool) -> List[str]:
    warnings = []

    if part.dim_x_mm > MACHINE["bed_x_mm"]:
        warnings.append(
            f"Part X dimension ({part.dim_x_mm}mm) exceeds machine bed ({MACHINE['bed_x_mm']}mm). "
            "Part will not fit."
        )
    if part.dim_y_mm > MACHINE["bed_y_mm"]:
        warnings.append(
            f"Part Y dimension ({part.dim_y_mm}mm) exceeds machine bed ({MACHINE['bed_y_mm']}mm). "
            "Part will not fit."
        )

    if part.cutting_depth_mm > MACHINE["max_z_travel_mm"]:
        warnings.append(
            f"Cutting depth ({part.cutting_depth_mm}mm) exceeds Z travel ({MACHINE['max_z_travel_mm']}mm)."
        )

    if not tool_can_reach(part.cutting_depth_mm, tool):
        warnings.append(
            f"Cutting depth ({part.cutting_depth_mm}mm) exceeds tool flute length "
            f"({tool.flute_length_mm}mm). Shank may contact stock — use a longer tool."
        )

    for hole in part.holes:
        # CHANGED: tolerance-based comparison (see DIAMETER_MATCH_TOL_MM)
        if hole.diameter_mm < tool.diameter_mm - DIAMETER_MATCH_TOL_MM:
            warnings.append(
                f"Hole diameter {hole.diameter_mm}mm is smaller than tool diameter "
                f"{tool.diameter_mm}mm. Cannot machine this hole with current tool."
            )
        elif abs(hole.diameter_mm - tool.diameter_mm) <= DIAMETER_MATCH_TOL_MM:
            warnings.append(
                f"Hole diameter exactly matches tool diameter ({hole.diameter_mm}mm). "
                "Use a drill bit of matching size for a clean hole rather than the end mill."
            )

    for pocket in part.pockets:
        # CHANGED: check the narrower side (width/height orientation is arbitrary)
        narrow = min(pocket.width_mm, pocket.height_mm)
        if narrow < tool.diameter_mm * 1.5:
            warnings.append(
                f"Pocket width {narrow}mm is very narrow for a "
                f"{tool.diameter_mm}mm tool. Pocket may not be fully cleared."
            )

    # CHANGED: removed an empty placeholder check here.

    return warnings


# ---------------------------------------------------------------------------
# Operation builders
# ---------------------------------------------------------------------------

def build_drilling(order: int, hole: Hole, tool: Tool, feeds: dict) -> Operation:
    op_warnings = []
    entry = "plunge"  # drilling is always a straight plunge

    if hole.diameter_mm > tool.diameter_mm + DIAMETER_MATCH_TOL_MM:
        # Hole larger than tool — needs helical boring, not straight drill
        entry = "helical"
        note = f"Hole ({hole.diameter_mm}mm) larger than tool ({tool.diameter_mm}mm) — use helical boring"
    else:
        note = f"Straight plunge drill — {hole.count}× hole(s) at {hole.diameter_mm}mm dia"

    # CHANGED: flag the operation itself when the tool can't make the hole
    # (previously only a global warning, and the op looked normal).
    if hole.diameter_mm < tool.diameter_mm - DIAMETER_MATCH_TOL_MM:
        op_warnings.append(
            f"Tool ({tool.diameter_mm}mm) is bigger than the hole — skip this op or use a smaller bit / drill."
        )

    # Straight plunge drilling uses a slower feed than milling.
    # CHANGED: helical boring is side milling, so it uses the normal milling feed
    # (Fusion bores at the full cutting feed on this machine).
    if entry == "helical":
        drill_feed = feeds["feed"]
    else:
        drill_feed = min(feeds["plunge"] * 1.5, feeds["feed"] * 0.4)

    return Operation(
        order=order,
        op_type=OperationType.DRILLING,
        description=f"Drill {hole.count}× hole(s) — {hole.diameter_mm}mm diameter × {hole.depth_mm}mm deep",
        spindle_rpm=feeds["rpm"],
        feed_rate_mmpm=round(drill_feed, 1),
        plunge_rate_mmpm=feeds["plunge"],
        step_down_mm=feeds["step_down"],
        step_over_mm=None,
        depth_mm=hole.depth_mm,
        entry_type=entry,
        notes=note,
        warnings=op_warnings,
        feature_refs=hole.feature_refs,
    )


def build_roughing(order: int, part: PartFeatures, tool: Tool, feeds: dict) -> Operation:
    # Roughing uses more aggressive step over, slightly higher feed
    step_over = calc_step_over(tool, "roughing")
    rough_feed = round(feeds["feed"] * 0.85, 1)   # slightly slower for bulk removal

    return Operation(
        order=order,
        op_type=OperationType.ROUGHING,
        description="Rough out bulk material — leave 0.2mm on walls for finish pass",
        spindle_rpm=feeds["rpm"],
        feed_rate_mmpm=rough_feed,
        plunge_rate_mmpm=feeds["plunge"],
        step_down_mm=feeds["step_down"],
        step_over_mm=step_over,
        depth_mm=part.cutting_depth_mm,
        entry_type="ramp",
        notes="Leave 0.2mm stock on all walls. Finish pass follows.",
    )


def build_pocketing(order: int, pocket: Pocket, tool: Tool, feeds: dict,
                    pocket_index: int = 1) -> Operation:
    # CHANGED: prefer the material library's step-over; fall back to the ratio table.
    step_over = feeds.get("step_over") or calc_step_over(tool, "pocketing")
    passes = calc_passes(pocket.depth_mm, feeds["step_down"])
    count = f"{pocket.count}× " if pocket.count > 1 else ""  # CHANGED: show grouped pockets

    return Operation(
        order=order,
        op_type=OperationType.POCKETING,
        description=(
            f"Clear {count}pocket {pocket_index} — {pocket.width_mm}×{pocket.height_mm}mm "
            f"× {pocket.depth_mm}mm deep ({passes} passes)"
        ),
        spindle_rpm=feeds["rpm"],
        feed_rate_mmpm=feeds["feed"],
        plunge_rate_mmpm=feeds["plunge"],
        step_down_mm=feeds["step_down"],
        step_over_mm=step_over,
        depth_mm=pocket.depth_mm,
        entry_type="ramp",
        notes=f"Step over {step_over}mm ({round(step_over/tool.diameter_mm*100)}% of tool dia)",
        feature_refs=pocket.feature_refs,
    )


def build_trace(order: int, part: PartFeatures, tool: Tool, feeds: dict,
                feature_refs: Optional[List[int]] = None) -> Operation:
    # Trace / engraving — shallow, slow, precise
    trace_feed = round(feeds["feed"] * 0.5, 1)
    trace_step = min(feeds["step_down"] * 0.5, 0.1)

    return Operation(
        order=order,
        op_type=OperationType.TRACE,
        description="Trace engraving lines / score marks",
        spindle_rpm=feeds["rpm"],
        feed_rate_mmpm=trace_feed,
        plunge_rate_mmpm=round(feeds["plunge"] * 0.5, 1),
        step_down_mm=trace_step,
        step_over_mm=None,
        depth_mm=0.2,    # shallow — just scoring the surface
        entry_type="ramp",
        notes="Shallow trace pass — adjust depth for desired engraving depth",
        feature_refs=feature_refs or [],
    )


def build_contour(order: int, part: PartFeatures, tool: Tool, feeds: dict) -> Operation:
    # Contour / wall finishing — light step over, full depth
    step_over = calc_step_over(tool, "finishing")
    finish_feed = round(feeds["feed"] * 0.7, 1)  # slower for clean finish

    return Operation(
        order=order,
        op_type=OperationType.CONTOUR,
        description="Finish contour pass — clean walls to final dimension",
        spindle_rpm=feeds["rpm"],
        feed_rate_mmpm=finish_feed,
        plunge_rate_mmpm=feeds["plunge"],
        step_down_mm=feeds["step_down"],
        step_over_mm=step_over,
        depth_mm=part.cutting_depth_mm,
        entry_type="ramp",
        notes="Light finishing pass — climb cut for best surface finish on acrylic",
    )


def build_outline(order: int, part: PartFeatures, tool: Tool, feeds: dict) -> Operation:
    tab_count = calc_tab_count(part.perimeter_mm)
    passes = calc_passes(part.cutting_depth_mm, feeds["step_down"])

    # CHANGED: full cutting feed (was 0.8x); Fusion contours at the full feed worked well.
    outline_feed = feeds["feed"]

    return Operation(
        order=order,
        op_type=OperationType.OUTLINE,
        description=(
            f"Profile outline — cut part free from stock "
            f"({passes} passes × {feeds['step_down']}mm)"
        ),
        spindle_rpm=feeds["rpm"],
        feed_rate_mmpm=outline_feed,
        plunge_rate_mmpm=feeds["plunge"],
        step_down_mm=feeds["step_down"],
        step_over_mm=None,
        depth_mm=part.cutting_depth_mm,
        entry_type="ramp",
        tabs=True,
        tab_count=tab_count,
        tab_width_mm=TAB_WIDTH_MM,
        tab_height_mm=min(1.0, part.cutting_depth_mm * 0.25),
        notes=f"Always last. {tab_count} tabs to hold part. Break tabs and sand after.",
    )


# ---------------------------------------------------------------------------
# Main rules engine
# ---------------------------------------------------------------------------

def plan_job(
    part_name: str,
    part: PartFeatures,
    tool: Tool,
    material: Material,
    engraving_refs: Optional[List[int]] = None,  # CHANGED: features the trace op covers
) -> OperationPlan:
    """
    Apply the rules engine to produce a complete operation plan.
    """
    operations: List[Operation] = []
    global_warnings = check_global_warnings(part, tool)
    feeds = calc_feeds(tool, material)
    order = 1

    # --- RULE 1: Drilling first (always) ---
    if part.has_holes:
        for hole in part.holes:
            op = build_drilling(order, hole, tool, feeds)
            operations.append(op)
            order += 1

    # --- RULE 2: Roughing (if deep enough to warrant it) ---
    roughing = needs_roughing(part, tool, material)
    if roughing:
        op = build_roughing(order, part, tool, feeds)
        operations.append(op)
        order += 1

    # --- RULE 3: Pocketing (interior features) ---
    if part.has_pockets:
        for i, pocket in enumerate(part.pockets, 1):
            op = build_pocketing(order, pocket, tool, feeds, pocket_index=i)
            operations.append(op)
            order += 1

    # --- RULE 4: Trace / engraving ---
    if part.has_engraving:
        op = build_trace(order, part, tool, feeds, engraving_refs)
        operations.append(op)
        order += 1

    # --- RULE 5: Contour finish (if curved walls or after roughing) ---
    if part.has_curved_walls or (roughing and part.cut_from_stock):
        op = build_contour(order, part, tool, feeds)
        operations.append(op)
        order += 1

    # --- RULE 6: Outline (always last if cutting part free) ---
    if part.cut_from_stock:
        op = build_outline(order, part, tool, feeds)
        operations.append(op)
        order += 1

    # Build summary
    op_names = [op.op_type.value for op in operations]
    summary = f"{len(operations)} operations: {' → '.join(op_names)}"

    return OperationPlan(
        part_name=part_name,
        tool=tool,
        material=material,
        operations=operations,
        global_warnings=global_warnings,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Demo cases — run this module to see the engine in action.
# The same cases are checked automatically in tests/test_rules_engine.py.
# ---------------------------------------------------------------------------

def demo_inputs():
    """The original hand-written test parts, as (name, part, tool, material)."""
    my_tool = Tool(
        name="2-flute carbide flat end mill",
        tool_type=ToolType.FLAT_END_MILL,
        diameter_mm=3.175,
        flutes=2,
        material="carbide",
        flute_length_mm=17.0,
    )
    acrylic = Material(
        name="Acrylic / Plexiglass",
        material_type=MaterialType.ACRYLIC,
        spindle_rpm=10000,
        feed_rate_mmpm=200,
        plunge_rate_mmpm=40,
        step_down_mm=0.3,
        step_over_mm=1.5,
    )
    soft_wood = Material(name="Softwood / MDF", material_type=MaterialType.WOOD_SOFT)

    return [
        ("Simple Acrylic Plate",
         PartFeatures(dim_x_mm=60, dim_y_mm=40, dim_z_mm=3.0), my_tool, acrylic),
        ("Acrylic Bracket",
         PartFeatures(dim_x_mm=80, dim_y_mm=50, dim_z_mm=5.0,
                      holes=[Hole(diameter_mm=4.0, depth_mm=5.0, count=2)]), my_tool, acrylic),
        ("Enclosure Lid",
         PartFeatures(dim_x_mm=100, dim_y_mm=70, dim_z_mm=6.0,
                      holes=[Hole(diameter_mm=3.5, depth_mm=6.0, count=4)],
                      pockets=[Pocket(width_mm=60, height_mm=40, depth_mm=3.0)]), my_tool, acrylic),
        ("Engraved Name Plate",
         PartFeatures(dim_x_mm=120, dim_y_mm=40, dim_z_mm=3.0, has_engraving=True), my_tool, acrylic),
        ("Deep MDF Part",
         PartFeatures(dim_x_mm=80, dim_y_mm=60, dim_z_mm=18.0,
                      pockets=[Pocket(width_mm=40, height_mm=30, depth_mm=15.0)]), my_tool, soft_wood),
        ("Oversized Part",
         PartFeatures(dim_x_mm=350, dim_y_mm=200, dim_z_mm=5.0), my_tool, acrylic),
        ("Part With Tiny Hole",
         PartFeatures(dim_x_mm=50, dim_y_mm=50, dim_z_mm=3.0,
                      holes=[Hole(diameter_mm=2.0, depth_mm=3.0)]), my_tool, acrylic),
    ]


def run_tests():
    # CHANGED: Windows consoles can choke on characters like → and ×; replace instead of crashing.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    plans = []
    for name, part, tool, material in demo_inputs():
        print("\n" + "-" * 60)
        print(name)
        print("-" * 60)
        plan = plan_job(name, part, tool, material)
        plan.pretty_print()
        plans.append(plan)

    print("\nJSON OUTPUT (Enclosure Lid):")
    print(json.dumps(plans[2].to_dict(), indent=2))


if __name__ == "__main__":
    run_tests()
