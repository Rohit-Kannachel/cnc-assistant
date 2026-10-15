"""Turn an operation plan into tool moves, using shapely for the 2D offsets.

Coordinates match the geometry analysis: X/Y from the part's lower-left corner,
Z = 0 at the top of the stock, cutting into negative Z.

Every cut is built from one primitive, `_cut_loop`: follow a closed loop, ramping
down from the depth already cut to the next depth, then go once more around at
full depth. Clearing an area is a set of nested loops (inside-out); a small loop
ramped down repeatedly is a helix. Tabs are spots on a profile loop (the outline,
or the inside wall of a big through hole/cutout) where Z is held up.

Safety rules the moves follow:
  - Rapids (G0) only happen at the safe height, or just above an area that is
    already cut, and never sideways into material.
  - The tool never plunges straight down into uncut material with an end mill;
    it ramps (or pecks, for a hole the same size as the tool).
  - Operations the plan flags as impossible (tool bigger than the hole) are skipped.
"""

import bisect
import math
from dataclasses import dataclass, field
from typing import List, Optional

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

RAMP_ANGLE_DEG = 3.0  # entry ramp slope (steepest allowed, for pockets and small helices)
HELIX_PITCH_MM = 0.4  # Z drop per turn when helically boring a hole (Fusion used 0.38-0.52 here)
HOVER_MM = 0.5  # how far above already-cut material the tool moves between loops
THROUGH_EXTRA_MM = 0.2  # cut this much below the stock for through features (Fusion used 0.2-0.35)
ROUGHING_ALLOWANCE_MM = 0.2  # wall stock left by roughing (matches the rules engine's note)
HOLE_MATCH_TOL_MM = 0.05  # a hole within this of the tool size is pecked straight down


class ToolpathError(ValueError):
    pass


@dataclass
class Move:
    kind: str  # "rapid" | "feed" | "comment"
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    f: Optional[float] = None
    op: int = 0  # operation number, for colouring the preview
    text: str = ""
    spindle: Optional[int] = None  # set on a comment move to change spindle speed


@dataclass
class ToolpathResult:
    moves: List[Move] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # human-readable reasons
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Move builder: tracks position so it can make safe travel decisions
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self, safe_z):
        self.safe_z = safe_z
        self.moves = []
        self.x = self.y = None
        self.z = None
        self.op = 0

    def comment(self, text, spindle=None):
        self.moves.append(Move("comment", text=text, op=self.op, spindle=spindle))

    def rapid(self, x=None, y=None, z=None):
        self.moves.append(Move("rapid", x, y, z, op=self.op))
        self._update(x, y, z)

    def feed(self, x=None, y=None, z=None, f=None):
        self.moves.append(Move("feed", x, y, z, f, op=self.op))
        self._update(x, y, z)

    def _update(self, x, y, z):
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z

    def at_xy(self, x, y, tol=1e-4):
        return self.x is not None and abs(self.x - x) < tol and abs(self.y - y) < tol

    def retract(self):
        if self.z is None or self.z < self.safe_z:
            self.rapid(z=self.safe_z)

    def travel(self, x, y, hover_z, reach=None):
        """Get the tool to (x, y, hover_z) without touching material.

        If the straight line there stays inside `reach` (the area the tool centre may
        occupy over already-cut material), move across at hover height; otherwise go
        up to the safe height first.
        """
        if self.at_xy(x, y):
            if self.z is None or self.z > hover_z:
                self.rapid(z=hover_z)
            return
        short_hop = (
            reach is not None
            and self.x is not None
            and self.z is not None
            and self.z <= hover_z + 1e-9
            and reach.covers(LineString([(self.x, self.y), (x, y)]))
        )
        if short_hop:
            self.rapid(z=hover_z)
            self.rapid(x, y)
        else:
            self.retract()
            self.rapid(x, y)
            self.rapid(z=hover_z)


# ---------------------------------------------------------------------------
# Loop following (ramps, helices, tabs)
# ---------------------------------------------------------------------------


class _Loop:
    """A closed path with arc-length lookup."""

    def __init__(self, coords):
        pts = [tuple(c[:2]) for c in coords]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        self.pts = pts
        self.cum = [0.0]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.hypot(x1 - x0, y1 - y0))
        self.length = self.cum[-1]

    @property
    def start(self):
        return self.pts[0]

    def point_at(self, s):
        """(x, y) at distance s along the loop (any s; wraps around)."""
        if self.length == 0:
            return self.pts[0]
        s %= self.length
        i = min(bisect.bisect_right(self.cum, s) - 1, len(self.pts) - 2)
        seg = self.cum[i + 1] - self.cum[i]
        t = (s - self.cum[i]) / seg if seg else 0.0
        (x0, y0), (x1, y1) = self.pts[i], self.pts[i + 1]
        return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)

    def stops(self, a, b, extra=()):
        """Arc-length positions from a to b (b may wrap past the loop length) where the
        path changes direction (vertices) or hits one of the `extra` breakpoints."""
        marks = {a, b}
        first, last = int(a // self.length), int(b // self.length) + 1
        for k in range(first, last + 1):
            base = k * self.length
            for c in list(self.cum) + list(extra):
                s = base + c
                if a < s < b:
                    marks.add(s)
        return sorted(marks)


def _cut_loop(b, loop, z_from, z_to, *, feed, plunge, ramp_len, start_s=0.0, reach=None, tabs=None):
    """Follow `loop` starting at arc position start_s: ramp from z_from down to z_to,
    then go once more all the way around at z_to (which also cleans up the ramp).

    tabs: optional (intervals, tab_z): along those arc-length intervals Z is held at
    tab_z or above.

    Returns the arc position where the tool stopped, so the next pass on the same
    loop can carry on from there without travelling.
    """
    intervals, tab_z = tabs if tabs else ([], None)

    def in_tab(s):
        if not loop.length:
            return False
        s %= loop.length
        return any(a <= s <= c for a, c in intervals)

    # Starting on a tab? Then don't go below the tab's top here.
    z_start = max(z_from, tab_z) if in_tab(start_s) else z_from

    x0, y0 = loop.point_at(start_s)
    if not (b.at_xy(x0, y0) and b.z is not None and b.z <= z_start + HOVER_MM + 1e-9):
        b.travel(x0, y0, z_start + HOVER_MM, reach)
    if b.z > z_start:
        b.feed(z=z_start, f=plunge)

    if loop.length < 1e-6:  # a single point: nothing to follow, just go down
        b.feed(z=z_to, f=plunge)
        return start_s

    ramp = ramp_len if z_to < z_from - 1e-9 else 0.0
    end_s = start_s + ramp + loop.length

    def z_at(s, inside_tab):
        d = s - start_s
        z = z_from + (z_to - z_from) * min(d / ramp, 1.0) if ramp else z_to
        return max(z, tab_z) if inside_tab else z

    breaks = [e for iv in intervals for e in iv]
    for s in loop.stops(start_s, end_s, breaks)[1:]:
        x, y = loop.point_at(s)
        before, after = in_tab(s - 1e-6), in_tab(s + 1e-6)
        z_before = z_at(s, before)
        b.feed(x, y, z_before, feed)
        if before != after:  # stepping onto or off a tab: change Z on the spot
            z_after = z_at(s, after)
            if abs(z_after - z_before) > 1e-9:
                b.feed(z=z_after, f=plunge)
    return end_s % loop.length


# ---------------------------------------------------------------------------
# Area helpers
# ---------------------------------------------------------------------------


def _polys(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


def _loops_of(geom, outside=False):
    """Closed loops of a polygon, oriented for climb milling with a clockwise (M3) spindle.

    Inside an area (pockets, holes) climb milling runs counter-clockwise around the
    wall; around an outside profile it runs clockwise.
    """
    loops = []
    for p in _polys(geom):
        p = orient(p, sign=-1.0 if outside else 1.0)
        loops.append(_Loop(p.exterior.coords))
        loops.extend(_Loop(r.coords) for r in p.interiors)
    return loops


def _clearing_loops(region, tool_r, stepover, allowance=0.0):
    """Nested offset loops that clear `region`, innermost first.

    The step between loops is capped at the tool radius so nothing is left
    standing in the middle of the last (innermost) loop.
    """
    step = min(stepover, tool_r)
    rings = []
    current = region.buffer(-(tool_r + allowance))
    while not current.is_empty:
        rings.append(current)
        current = current.buffer(-step)
    loops = []
    for ring in reversed(rings):
        loops.extend(_loops_of(ring))
    return loops


def _depths(target, step_down):
    """Pass depths (negative Z) down to -target."""
    n = max(1, math.ceil(target / step_down - 1e-9))
    return [-min(k * step_down, target) for k in range(1, n + 1)]


def _ramp_len(step_down, helix_loop=None):
    """Path length over which to descend one step-down.

    Normally a RAMP_ANGLE_DEG ramp. For a helical bore, descend HELIX_PITCH_MM per turn
    of the loop, but never steeper than RAMP_ANGLE_DEG (matters for tiny holes).
    """
    ramp = step_down / math.tan(math.radians(RAMP_ANGLE_DEG))
    if helix_loop is not None and helix_loop.length > 0:
        ramp = max(ramp, helix_loop.length * step_down / HELIX_PITCH_MM)
    return ramp


# ---------------------------------------------------------------------------
# Operation generators
# ---------------------------------------------------------------------------


def _clear_area(b, region, target, op, tool_r, *, stepover, allowance=0.0, finish_only=False, helix=False):
    """Clear (or, with finish_only, just trace the walls of) `region` down to depth `target`.

    helix: bore-style entry, descending HELIX_PITCH_MM per turn (gentler than RAMP_ANGLE_DEG
    on bigger loops), as Fusion does for bores.
    """
    if finish_only:
        loops = _loops_of(region.buffer(-tool_r))
    else:
        loops = _clearing_loops(region, tool_r, stepover, allowance)
    if not loops:
        return False
    # Where the tool centre can move over already-cut floor without touching a wall.
    reach = region.buffer(-tool_r + 0.01)
    step = op["step_down_mm"]
    ramps = [_ramp_len(step, loop if helix else None) for loop in loops]
    resume = [0.0] * len(loops)  # each loop's next pass starts where its last one ended
    z_prev = 0.0
    for z in _depths(target, step):
        for i, loop in enumerate(loops):
            resume[i] = _cut_loop(b, loop, z_prev, z, feed=op["feed_rate_mmpm"], plunge=op["plunge_rate_mmpm"],
                                  ramp_len=ramps[i], start_s=resume[i], reach=reach)
        z_prev = z
    b.retract()
    return True


def _peck(b, x, y, target, op):
    """Peck straight down at (x, y), one step-down at a time, lifting out between pecks
    to clear chips. Only used for a hole the same size as the tool."""
    b.travel(x, y, HOVER_MM)
    last = 0.0
    for z in _depths(target, op["step_down_mm"]):
        b.rapid(z=last + 0.2)  # back down to just above the depth already drilled
        b.feed(z=z, f=op["plunge_rate_mmpm"])
        b.rapid(z=HOVER_MM)  # lift out to clear chips
        last = z
    b.retract()


def _feature_target(feature, cut_depth):
    """How deep to cut a feature: through features go through the stock plus a little."""
    if feature.get("through"):
        return cut_depth + THROUGH_EXTRA_MM
    return feature["depth"]


def _drilling(b, op, features, tool_r, cut_depth, result):
    for n in op["feature_refs"]:
        f = features[n - 1]
        target = _feature_target(f, cut_depth)
        hole = f["polygon"]
        dia = f.get("diameter") or 0
        if dia and abs(dia - 2 * tool_r) <= HOLE_MATCH_TOL_MM:
            b.comment(f"Feature {n}: peck drill dia {dia:g} to Z-{target:g}")
            c = hole.centroid
            _peck(b, c.x, c.y, target, op)
        else:
            b.comment(f"Feature {n}: helical bore dia {dia:g} to Z-{target:g}")
            if not _clear_area(b, hole, target, op, tool_r, stepover=tool_r, helix=True):
                result.skipped.append(f"Feature {n} (op {op['order']}): too small for the tool, not cut.")


def _area_features(op, features):
    for n in op["feature_refs"]:
        yield n, features[n - 1]


def _pocketing(b, op, features, tool_r, cut_depth, result, stepover):
    for n, f in _area_features(op, features):
        target = _feature_target(f, cut_depth)
        b.comment(f"Feature {n}: clear {f['type'].replace('_', ' ')} to Z-{target:g}")
        if not _clear_area(b, f["polygon"], target, op, tool_r, stepover=stepover):
            result.skipped.append(f"Feature {n} (op {op['order']}): too narrow for the tool, not cut.")


def _interior_area_features(features):
    """Pocket-like features (everything except holes), numbered from 1."""
    return [(n, f) for n, f in enumerate(features, start=1)
            if f["type"] in ("pocket", "step", "through_cutout")]


def _roughing(b, op, features, tool_r, cut_depth, result, profiled):
    # Through openings cut as an inside profile keep their middle: nothing to rough there.
    targets = [(n, f) for n, f in _interior_area_features(features) if n not in profiled]
    if not targets:
        b.comment("Nothing to rough: no pockets, steps or cutouts. Skipped.")
        result.notes.append(f"Op {op['order']} (roughing): no pockets, steps or cutouts to rough, so no moves.")
        return
    for n, f in targets:
        target = _feature_target(f, cut_depth)
        b.comment(f"Feature {n}: rough, leaving {ROUGHING_ALLOWANCE_MM:g} mm on the walls")
        _clear_area(b, f["polygon"], target, op, tool_r, stepover=op["step_over_mm"] or tool_r,
                    allowance=ROUGHING_ALLOWANCE_MM)


def _contour(b, op, features, tool_r, cut_depth, result, profiled):
    targets = [(n, f) for n, f in _interior_area_features(features) if n not in profiled]
    if not targets:
        b.comment("No interior walls to finish. Skipped.")
        result.notes.append(f"Op {op['order']} (contour): no pocket/step walls to finish, so no moves.")
        return
    for n, f in targets:
        b.comment(f"Feature {n}: finish walls")
        _clear_area(b, f["polygon"], _feature_target(f, cut_depth), op, tool_r,
                    stepover=tool_r, finish_only=True)


def _tab_spec(op, loop, target, count=None):
    """Tabs for a profile loop: `count` bridges evenly spaced along it (default: the op's),
    each op["tab_width_mm"] wide, standing op["tab_height_mm"] above the cut bottom."""
    n = count if count is not None else op.get("tab_count")
    if not (op.get("tabs") and n):
        return None
    # Path gap = bridge width + tool diameter (the tool eats a radius on each side).
    gap = op["tab_width_mm"] + 2 * op["_tool_r"]
    spacing = loop.length / n
    intervals = [((i + 0.5) * spacing - gap / 2, (i + 0.5) * spacing + gap / 2) for i in range(n)]
    return intervals, -(target - op["tab_height_mm"])


def _cut_profile(b, loop, target, op, *, tabs=None, ramp=None):
    """Follow one closed loop down to `target` pass by pass, carrying on from where each
    pass ended (no retracts), holding Z up over any tabs."""
    ramp = ramp if ramp is not None else _ramp_len(op["step_down_mm"])
    z_prev, s = 0.0, 0.0
    for z in _depths(target, op["step_down_mm"]):
        s = _cut_loop(b, loop, z_prev, z, feed=op["feed_rate_mmpm"], plunge=op["plunge_rate_mmpm"],
                      ramp_len=ramp, start_s=s, tabs=tabs)
        z_prev = z
    b.retract()


def _inside_profile(b, op, features, tool_r, cut_depth):
    """Cut round the inside wall of big through holes/cutouts, with tabs holding the middle."""
    target = cut_depth + THROUGH_EXTRA_MM
    for n in op["feature_refs"]:
        loops = _loops_of(features[n - 1]["polygon"].buffer(-tool_r))
        if not loops:
            continue
        b.comment(f"Feature {n}: inside profile to Z-{target:g} with {op.get('tab_count') or 0} tabs "
                  f"holding the middle")
        spacing = loops[0].length / op["tab_count"] if op.get("tab_count") else None
        for i, loop in enumerate(loops):
            # The main wall gets the op's tab count; any island walls get tabs at the same spacing.
            count = op.get("tab_count") if i == 0 or not spacing else max(2, round(loop.length / spacing))
            _cut_profile(b, loop, target, op, tabs=_tab_spec(op, loop, target, count),
                         ramp=_ramp_len(op["step_down_mm"], loop))


def _outline(b, op, outline, tool_r, cut_depth):
    target = cut_depth + THROUGH_EXTRA_MM
    for piece in _polys(outline):
        loop = _loops_of(Polygon(piece.exterior).buffer(tool_r), outside=True)[0]
        _cut_profile(b, loop, target, op, tabs=_tab_spec(op, loop, target))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_toolpaths(plan, geometry, *, tool_dia_mm, cut_depth_mm, safe_z_mm=5.0):
    """Moves for every operation in `plan` (the rules engine's dict output).

    geometry: services.geometry.part_geometry() output.
    cut_depth_mm: stock thickness (or part height), as a positive number.
    """
    tool_r = tool_dia_mm / 2
    features = geometry["features"]
    for op in plan["operations"]:
        for n in op.get("feature_refs") or []:
            if not 1 <= n <= len(features):
                raise ToolpathError("The plan refers to features that no longer match the STL. Re-analyse the job.")

    b = _Builder(safe_z_mm)
    result = ToolpathResult()
    b.rapid(z=safe_z_mm)
    # Features cut as inside profiles (the middle stays, held by tabs): roughing and
    # contour must leave them alone.
    profiled = {n for op in plan["operations"] if op["type"] == "profile" for n in op["feature_refs"]}

    for op in plan["operations"]:
        op = {**op, "_tool_r": tool_r}
        b.op = op["order"]
        b.comment(f"Op {op['order']}: {op['type'].upper()} - {op['description']}", spindle=op["spindle_rpm"])

        blocking = [w for w in op.get("warnings", []) if "bigger than the hole" in w]
        if blocking:
            b.comment("SKIPPED: " + blocking[0])
            result.skipped.append(f"Op {op['order']} ({op['type']}): {blocking[0]}")
            continue

        kind = op["type"]
        if kind == "drilling":
            _drilling(b, op, features, tool_r, cut_depth_mm, result)
        elif kind in ("pocketing", "trace"):
            stepover = op["step_over_mm"] or tool_r
            _pocketing(b, op, features, tool_r, cut_depth_mm, result, stepover)
        elif kind == "roughing":
            _roughing(b, op, features, tool_r, cut_depth_mm, result, profiled)
        elif kind == "contour":
            _contour(b, op, features, tool_r, cut_depth_mm, result, profiled)
        elif kind == "profile":
            _inside_profile(b, op, features, tool_r, cut_depth_mm)
        elif kind == "outline":
            _outline(b, op, geometry["outline"], tool_r, cut_depth_mm)
        else:
            b.comment(f"Unknown operation type {kind!r}: skipped")
            result.skipped.append(f"Op {op['order']}: unknown type {kind!r}.")
        b.retract()

    result.moves = b.moves
    return result
