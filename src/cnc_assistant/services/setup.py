"""Stock size, where the part sits in it, and the work origin (where X0 Y0 Z0 is).

Three coordinate frames:
  - part frame:  X/Y from the part's front-left corner, Z = 0 at the part's top.
                 Everything up to toolpath generation works in this frame.
  - stock frame: X/Y from the stock's front-left corner.
  - work frame:  what the G-code uses, i.e. what you zero on the machine.

"Front" is the side nearest you on the 3018 (lowest Y), "left" is lowest X.
The part's top is always level with the stock's top (there's no facing operation yet).

Converting the toolpaths is a plain shift: work = part + (dx, dy, dz).
"""

from dataclasses import asdict, dataclass, field, replace
from typing import List

ORIGIN_XY = {
    "stock_front_left": "Front-left corner of the stock",
    "stock_front_right": "Front-right corner of the stock",
    "stock_back_left": "Back-left corner of the stock",
    "stock_back_right": "Back-right corner of the stock",
    "stock_center": "Centre of the stock",
    "part_front_left": "Front-left corner of the part",
    "part_center": "Centre of the part",
}
ORIGIN_Z = {
    "stock_top": "Top of the stock",
    "stock_bottom": "Bottom of the stock (the spoilboard surface)",
}
DEFAULT_ORIGIN_XY = "stock_front_left"
DEFAULT_ORIGIN_Z = "stock_top"
DEFAULT_MARGIN_MM = 5.0  # automatic stock = part + this on every side


@dataclass
class WorkSetup:
    stock_x: float
    stock_y: float
    stock_z: float
    part_x: float  # part's front-left corner in the stock frame
    part_y: float
    part_w: float
    part_h: float
    part_z: float
    origin_xy: str
    origin_z: str
    auto_stock: bool
    centred: bool
    dx: float  # work = part frame + (dx, dy, dz)
    dy: float
    dz: float
    warnings: List[str] = field(default_factory=list)

    @property
    def origin_xy_label(self):
        return ORIGIN_XY[self.origin_xy]

    @property
    def origin_z_label(self):
        return ORIGIN_Z[self.origin_z]

    def to_dict(self):
        d = asdict(self)
        d["origin_xy_label"] = self.origin_xy_label
        d["origin_z_label"] = self.origin_z_label
        return d


def resolve_setup(job, tool_dia_mm=None, bed=(300.0, 180.0)):
    """Work out the full setup for a job from its (possibly blank) settings and analysis."""
    dims = job.analysis["dimensions"]
    w, h, part_z = dims["x"], dims["y"], dims["z"]

    auto_stock = job.stock_x_mm is None or job.stock_y_mm is None
    sx = job.stock_x_mm if job.stock_x_mm is not None else w + 2 * DEFAULT_MARGIN_MM
    sy = job.stock_y_mm if job.stock_y_mm is not None else h + 2 * DEFAULT_MARGIN_MM
    sz = job.stock_thickness_mm or part_z

    centred = job.part_offset_x_mm is None or job.part_offset_y_mm is None
    px = job.part_offset_x_mm if not centred else (sx - w) / 2
    py = job.part_offset_y_mm if not centred else (sy - h) / 2

    oxy = job.origin_xy if job.origin_xy in ORIGIN_XY else DEFAULT_ORIGIN_XY
    oz = job.origin_z if job.origin_z in ORIGIN_Z else DEFAULT_ORIGIN_Z
    ox, oy = {
        "stock_front_left": (0.0, 0.0),
        "stock_front_right": (sx, 0.0),
        "stock_back_left": (0.0, sy),
        "stock_back_right": (sx, sy),
        "stock_center": (sx / 2, sy / 2),
        "part_front_left": (px, py),
        "part_center": (px + w / 2, py + h / 2),
    }[oxy]

    setup = WorkSetup(
        stock_x=sx, stock_y=sy, stock_z=sz,
        part_x=px, part_y=py, part_w=w, part_h=h, part_z=part_z,
        origin_xy=oxy, origin_z=oz, auto_stock=auto_stock, centred=centred,
        dx=px - ox, dy=py - oy, dz=sz if oz == "stock_bottom" else 0.0,
    )
    setup.warnings = _check(setup, tool_dia_mm, bed, cut_from_stock=job.cut_from_stock is not False)
    return setup


def _check(s, tool_dia, bed, cut_from_stock):
    w = []
    tol = 0.01
    if s.part_x < -tol or s.part_y < -tol or s.part_x + s.part_w > s.stock_x + tol or s.part_y + s.part_h > s.stock_y + tol:
        w.append(
            f"The part ({s.part_w:g} x {s.part_h:g} mm at {s.part_x:g}, {s.part_y:g}) runs off the "
            f"{s.stock_x:g} x {s.stock_y:g} mm stock. Make the stock bigger or move the part."
        )
    elif cut_from_stock and tool_dia:
        margin = min(s.part_x, s.part_y, s.stock_x - s.part_x - s.part_w, s.stock_y - s.part_y - s.part_h)
        if margin < tool_dia:
            w.append(
                f"Only {margin:.1f} mm of stock beside the part on one side: the outline cut there "
                f"(tool {tool_dia:g} mm) runs partly in air and there's little left to clamp."
            )
    if s.stock_x > bed[0] or s.stock_y > bed[1]:
        w.append(f"The stock ({s.stock_x:g} x {s.stock_y:g} mm) is bigger than the {bed[0]:g} x {bed[1]:g} mm bed.")
    if s.stock_z > s.part_z + tol:
        w.append(
            f"The stock ({s.stock_z:g} mm) is thicker than the part ({s.part_z:g} mm). The part's top is set "
            f"level with the stock's top and cuts go through the full stock, so the part comes out "
            f"{s.stock_z:g} mm thick. Facing to thickness isn't supported yet."
        )
    elif s.stock_z < s.part_z - tol:
        w.append(
            f"The stock ({s.stock_z:g} mm) is thinner than the part ({s.part_z:g} mm): anything deeper "
            f"than the stock will cut into the spoilboard."
        )
    return w


def shift_moves(moves, dx, dy, dz):
    """Toolpath moves moved from the part frame into the work frame."""
    out = []
    for m in moves:
        if m.kind == "comment":
            out.append(m)
            continue
        out.append(replace(
            m,
            x=None if m.x is None else m.x + dx,
            y=None if m.y is None else m.y + dy,
            z=None if m.z is None else m.z + dz,
        ))
    return out


def setup_svg(s):
    """Top-view sketch of the stock, the part in it, and the work origin with X/Y arrows."""
    pad = max(s.stock_x, s.stock_y) * 0.12
    top = s.stock_y  # flip Y for SVG
    ox, oy = s.part_x - s.dx, s.part_y - s.dy  # origin in the stock frame
    arrow = max(s.stock_x, s.stock_y) * 0.12
    font = max(s.stock_x, s.stock_y) * 0.045
    view = f"{-pad:.2f} {-pad:.2f} {s.stock_x + 2 * pad:.2f} {s.stock_y + 2 * pad:.2f}"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" class="setup-view" role="img" '
        f'aria-label="Stock, part and work origin">'
        f'<defs><marker id="arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" class="setup-axis-head"/></marker></defs>'
        f'<rect x="0" y="0" width="{s.stock_x:.2f}" height="{s.stock_y:.2f}" class="setup-stock"><title>Stock</title></rect>'
        f'<rect x="{s.part_x:.2f}" y="{top - s.part_y - s.part_h:.2f}" width="{s.part_w:.2f}" height="{s.part_h:.2f}" '
        f'class="setup-part"><title>Part (bounding box)</title></rect>'
        f'<line x1="{ox:.2f}" y1="{top - oy:.2f}" x2="{ox + arrow:.2f}" y2="{top - oy:.2f}" class="setup-axis" marker-end="url(#arrowhead)"/>'
        f'<line x1="{ox:.2f}" y1="{top - oy:.2f}" x2="{ox:.2f}" y2="{top - oy - arrow:.2f}" class="setup-axis" marker-end="url(#arrowhead)"/>'
        f'<text x="{ox + arrow + font * 0.3:.2f}" y="{top - oy + font * 0.35:.2f}" font-size="{font:.2f}" class="setup-label">X+</text>'
        f'<text x="{ox - font * 0.6:.2f}" y="{top - oy - arrow - font * 0.3:.2f}" font-size="{font:.2f}" class="setup-label">Y+</text>'
        f'<circle cx="{ox:.2f}" cy="{top - oy:.2f}" r="{font * 0.35:.2f}" class="setup-origin"><title>X0 Y0</title></circle>'
        f'<text x="{s.stock_x / 2:.2f}" y="{s.stock_y + pad * 0.6:.2f}" font-size="{font:.2f}" class="setup-label" '
        f'text-anchor="middle">front (you)</text>'
        f'</svg>'
    )
