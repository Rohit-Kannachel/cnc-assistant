"""Write GRBL gcode (and a top-view toolpath preview) from toolpath moves.

GRBL notes:
  - Comments use parentheses, and must be plain ASCII with no nested brackets.
  - Coordinates are modal: an axis is only written when it changes.
  - G0 = rapid, G1 = feed. F is modal too, so it's only written when it changes.
  - Runs of short moves along a circle are written as G2/G3 arcs (I/J relative to the
    arc start), as Fusion does: much shorter files and smoother motion on GRBL.
"""

import math
import re
from datetime import datetime

from shapely.affinity import translate

from .setup import shift_moves
from .toolpath import THROUGH_EXTRA_MM

RAPID_RATE_MM_MIN = 1000  # rough 3018 rapid speed, only used for the time estimate
SPINDLE_DWELL_S = 3  # wait for the spindle to reach speed

_ASCII = {"×": "x", "—": "-", "–": "-", "→": "->", "Ø": "dia ", "°": "deg", "µ": "u", "²": "2", "³": "3"}


def _comment(text):
    for k, v in _ASCII.items():
        text = text.replace(k, v)
    text = text.replace("(", "[").replace(")", "]")
    text = re.sub(r"[^\x20-\x7E]", "?", text)
    return f"({text})"


def _num(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def write_gcode(result, *, job_name, tool, material, cut_depth_mm, safe_z_mm, planner="rules_engine", setup=None):
    """Return (gcode_text, stats) for a ToolpathResult.

    setup: a services.setup.WorkSetup. The moves (in the part frame) are shifted into the
    work frame it describes. Without one, X0 Y0 is the part's front-left corner and Z0
    the top of the stock.
    """
    if setup is not None:
        moves = shift_moves(result.moves, setup.dx, setup.dy, setup.dz)
        safe_z_mm += setup.dz
    else:
        moves = result.moves
    stats = _stats(moves)

    if setup is not None:
        setup_lines = [
            f"STOCK: {setup.stock_x:g} x {setup.stock_y:g} x {setup.stock_z:g} mm; part front-left corner at "
            f"{setup.part_x:g}, {setup.part_y:g} mm from the stock's front-left corner",
            f"ZERO: X0 Y0 = {setup.origin_xy_label}; Z0 = {setup.origin_z_label}",
        ]
    else:
        setup_lines = ["ZERO: X0 Y0 = front-left corner of the part; Z0 = top of the stock"]
    lines = [
        _comment(f"CNC Assistant - {job_name}"),
        _comment(f"Generated {datetime.now():%Y-%m-%d %H:%M} by the {planner.replace('_', ' ')}"),
        _comment(f"Tool: {tool.name} - dia {tool.cut_dia_mm:g} mm flat end mill"),
        _comment(f"Material: {material.name}"),
        *[_comment(t) for t in setup_lines],
        _comment(f"Toolpaths reach X{_num(stats['min_x'])} Y{_num(stats['min_y'])} to "
                 f"X{_num(stats['max_x'])} Y{_num(stats['max_y'])}, Z down to {_num(stats['min_z'])}"),
        _comment(f"Cut depth {cut_depth_mm:g} mm + {THROUGH_EXTRA_MM:g} mm break-through on through cuts"),
        _comment(f"Estimated time {stats['minutes']:.0f} min. Do an air cut first if unsure."),
    ]
    for w in (setup.warnings if setup is not None else []):
        lines.append(_comment(f"WARNING {w}"))
    for s in result.skipped:
        lines.append(_comment(f"SKIPPED {s}"))
    lines += [
        "G21 (mm)",
        "G90 (absolute)",
        "G17 (XY plane)",
        "G94 (feed per minute)",
    ]

    spindle_on = False
    rpm = None
    last = {"x": None, "y": None, "z": None}
    last_f = None

    i = 0
    while i < len(moves):
        m = moves[i]
        i += 1

        if m.kind == "feed" and spindle_on and None not in last.values():
            arc = _fit_arc(moves, i - 1, (last["x"], last["y"], last["z"]))
            if arc:
                end, centre, ccw, f, used = arc
                words = [f"X{_num(end[0])}", f"Y{_num(end[1])}"]
                if abs(end[2] - last["z"]) > 5e-4:
                    words.append(f"Z{_num(end[2])}")
                words += [f"I{_num(centre[0] - last['x'])}", f"J{_num(centre[1] - last['y'])}"]
                if f and f != last_f:
                    words.append(f"F{_num(f)}")
                    last_f = f
                lines.append(" ".join(["G3" if ccw else "G2"] + words))
                last.update(x=end[0], y=end[1], z=end[2])
                i += used - 1
                continue

        if m.kind == "comment":
            lines.append(_comment(m.text))
            if m.spindle and m.spindle != rpm:
                rpm = m.spindle
                if spindle_on:
                    lines.append(f"S{rpm}")
                else:
                    lines += [f"S{rpm} M3", f"G4 P{SPINDLE_DWELL_S} (spindle spin-up)"]
                    spindle_on = True
            continue

        if m.kind == "feed" and not spindle_on:
            raise ValueError("Feed move before the spindle was started")  # guard against bugs

        words = []
        for axis in ("x", "y", "z"):
            v = getattr(m, axis)
            if v is not None and (last[axis] is None or abs(v - last[axis]) > 5e-4):
                words.append(f"{axis.upper()}{_num(v)}")
                last[axis] = v
        if not words:
            continue
        # The motion word is written on every line (not left modal) so each line reads on its own.
        g = "G0" if m.kind == "rapid" else "G1"
        if m.kind == "feed" and m.f and m.f != last_f:
            words.append(f"F{_num(m.f)}")
            last_f = m.f
        lines.append(" ".join([g] + words))

    if last["z"] is None or last["z"] < safe_z_mm:
        lines.append(f"G0 Z{_num(safe_z_mm)}")
    lines += [
        "M5 (spindle off)",
        "G0 X0 Y0",
        "M30",
    ]
    stats["lines"] = len(lines)
    return "\n".join(lines) + "\n", stats


# ---------------------------------------------------------------------------
# Arc fitting: runs of short feed moves on a common circle become one G2/G3
# ---------------------------------------------------------------------------

ARC_MIN_SEGMENTS = 3  # need at least this many line segments to bother
ARC_RADIUS_TOL = 0.004  # every point must be this close to the fitted circle (mm); GRBL allows 0.005
# The arc may bulge at most this far from each original straight segment (mm). STLs draw
# circles as flat facets that sit slightly inside the real (CAD) circle: a 144-segment
# circle of radius 75 is off by 0.018 mm. The arc follows the real circle, so allow that.
ARC_MAX_SAGITTA = 0.02
ARC_MAX_SWEEP = math.pi  # split arcs at 180 degrees, like Fusion does
ARC_MAX_RADIUS = 2000.0  # nearly-straight runs stay as lines


def _circle(p0, p1, p2):
    """Centre (x, y) and radius of the circle through three XY points, or None if collinear."""
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy), math.hypot(ax - ux, ay - uy)


def _fit_arc(moves, start, pos):
    """Try to replace moves[start:start+k] (all feed moves) with one arc from `pos`.

    Returns (end_xyz, centre_xy, ccw, feed, k) or None. Z may change, but only at a
    constant rate per angle (a helix), which GRBL arcs support.
    """
    pts = [pos]
    feed = moves[start].f
    j = start
    while j < len(moves) and moves[j].kind == "feed" and moves[j].f in (feed, None):
        m = moves[j]
        prev = pts[-1]
        pts.append((m.x if m.x is not None else prev[0],
                    m.y if m.y is not None else prev[1],
                    m.z if m.z is not None else prev[2]))
        j += 1
        if len(pts) > 400:
            break
    if len(pts) < ARC_MIN_SEGMENTS + 1:
        return None

    fit = _circle(pts[0][:2], pts[1][:2], pts[2][:2])
    if not fit:
        return None
    (cx, cy), r = fit
    if r > ARC_MAX_RADIUS or r < 0.05:
        return None

    def angle(p):
        return math.atan2(p[1] - cy, p[0] - cx)

    cross = (pts[1][0] - pts[0][0]) * (pts[2][1] - pts[1][1]) - (pts[1][1] - pts[0][1]) * (pts[2][0] - pts[1][0])
    ccw = cross > 0
    sweep = 0.0
    z_rate = None
    best = 0
    for k in range(1, len(pts)):
        a, b = pts[k - 1], pts[k]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-6:  # a pure Z move (tab step): stop here
            break
        if abs(math.hypot(b[0] - cx, b[1] - cy) - r) > ARC_RADIUS_TOL:
            break
        step = (angle(b) - angle(a)) if ccw else (angle(a) - angle(b))
        step %= 2 * math.pi
        if step <= 0 or step > math.radians(30):
            break
        if r * (1 - math.cos(step / 2)) > ARC_MAX_SAGITTA:
            break
        if sweep + step > ARC_MAX_SWEEP + 1e-9:
            break
        rate = (b[2] - a[2]) / step
        if z_rate is None:
            z_rate = rate
        elif abs(rate - z_rate) * step > 0.001:
            break
        sweep += step
        best = k
    if best < ARC_MIN_SEGMENTS:
        return None

    # The first three points can be very close together, so their circle is only roughly
    # right. Refit through the first, middle and last points: the start and end are then
    # exactly the same distance from the centre (GRBL checks this: error 33). Shorten the
    # arc until every point in it sits on the refitted circle.
    while best >= ARC_MIN_SEGMENTS:
        refit = _circle(pts[0][:2], pts[best // 2][:2], pts[best][:2])
        if refit:
            (rx, ry), rr = refit
            if all(abs(math.hypot(p[0] - rx, p[1] - ry) - rr) <= ARC_RADIUS_TOL for p in pts[1:best]):
                return pts[best], (rx, ry), ccw, feed, best
        best -= 1
    return None


def _stats(moves):
    x = y = z = None
    feed_len = rapid_len = 0.0
    minutes = 0.0
    xs, ys, zs = [], [], []
    for m in moves:
        if m.kind == "comment":
            continue
        nx = m.x if m.x is not None else x
        ny = m.y if m.y is not None else y
        nz = m.z if m.z is not None else z
        if None not in (x, y, z, nx, ny, nz):
            d = ((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2) ** 0.5
            if m.kind == "feed":
                feed_len += d
                minutes += d / m.f if m.f else 0
            else:
                rapid_len += d
                minutes += d / RAPID_RATE_MM_MIN
        x, y, z = nx, ny, nz
        if x is not None:
            xs.append(x)
        if y is not None:
            ys.append(y)
        if z is not None:
            zs.append(z)
    return {
        "min_x": min(xs, default=0), "max_x": max(xs, default=0),
        "min_y": min(ys, default=0), "max_y": max(ys, default=0),
        "min_z": min(zs, default=0), "max_z": max(zs, default=0),
        "feed_mm": round(feed_len), "rapid_mm": round(rapid_len),
        "minutes": minutes,
    }


# ---------------------------------------------------------------------------
# Toolpath preview (top view)
# ---------------------------------------------------------------------------


def toolpath_svg(result, outline, height_mm, setup=None):
    """Top view of every cutting move, coloured by operation, over the part outline,
    in work coordinates (so the red dot is X0 Y0), with the stock drawn if `setup` is given.

    Moves repeated on several depth passes are drawn once.
    """
    moves = result.moves
    stock = None
    if setup is not None:
        moves = shift_moves(moves, setup.dx, setup.dy, setup.dz)
        outline = translate(outline, setup.dx, setup.dy)
        # Stock corners in the work frame: the stock's front-left is at part frame (-part_x, -part_y).
        sx0, sy0 = setup.dx - setup.part_x, setup.dy - setup.part_y
        stock = (sx0, sy0, sx0 + setup.stock_x, sy0 + setup.stock_y)
    moves = [m for m in moves if m.kind != "comment"]
    xs = [m.x for m in moves if m.x is not None]
    ys = [m.y for m in moves if m.y is not None]
    if not xs:
        return None
    if stock:
        xs += [stock[0], stock[2]]
        ys += [stock[1], stock[3]]
    minx, maxx = min(xs + [0]), max(xs + [0])
    miny, maxy = min(ys + [0]), max(ys + [0])
    pad = max(maxx - minx, maxy - miny) * 0.04
    top = maxy + pad  # flip Y for SVG

    def pt(x, y):
        return f"{x:.2f},{top - y:.2f}"

    segs_by_op, rapids = {}, set()
    x = y = None
    for m in moves:
        nx = m.x if m.x is not None else x
        ny = m.y if m.y is not None else y
        if None not in (x, y) and (nx, ny) != (x, y):
            seg = (round(x, 2), round(y, 2), round(nx, 2), round(ny, 2))
            if m.kind == "feed":
                segs_by_op.setdefault(m.op, set()).add(seg)
            else:
                rapids.add(seg)
        x, y = nx, ny

    parts = []
    if stock:
        x0, y0, x1, y1 = stock
        parts.append(f'<rect x="{x0:.2f}" y="{top - y1:.2f}" width="{x1 - x0:.2f}" height="{y1 - y0:.2f}" '
                     f'class="tp-stock"><title>Stock</title></rect>')
    for poly in getattr(outline, "geoms", [outline]):
        if not poly.is_empty:
            d = "M" + " L".join(pt(px, py) for px, py in poly.exterior.coords) + " Z"
            parts.append(f'<path d="{d}" class="tp-part"/>')
    for op, segs in sorted(segs_by_op.items()):
        d = " ".join(f"M{pt(a, b)} L{pt(c, e)}" for a, b, c, e in segs)
        parts.append(f'<path d="{d}" class="tp-cut tp-op{op % 8}"><title>Operation {op}</title></path>')
    if rapids:
        d = " ".join(f"M{pt(a, b)} L{pt(c, e)}" for a, b, c, e in rapids)
        parts.append(f'<path d="{d}" class="tp-rapid"><title>Rapid moves</title></path>')
    # Origin marker: where X0 Y0 is.
    parts.append(f'<circle cx="0" cy="{top:.2f}" r="{pad * 0.35:.2f}" class="tp-origin"><title>X0 Y0</title></circle>')

    view = f"{minx - pad:.2f} 0 {maxx - minx + 2 * pad:.2f} {maxy - miny + 2 * pad:.2f}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" class="toolpath-view" role="img" '
            f'aria-label="Toolpath preview">{"".join(parts)}</svg>')
