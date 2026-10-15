"""STL geometry analysis with trimesh + shapely.

The part is moved into "machine" coordinates before anything is measured:
  - X/Y start at the part's lower-left corner (0, 0)
  - Z = 0 is the top of the part and everything is below it (depths are negative Z)
This matches how you'd zero the 3018 on the top-left/lower-left of the stock.

The main idea: for a 2.5D part every horizontal "floor" (the top surface, pocket bottoms,
step shoulders) sits at a fixed Z. Between two neighbouring floors the cross-section
doesn't change, so slicing once in the middle of each band describes that whole
level exactly. Holes and pockets are then read off those cross-sections.
"""

import math

import numpy as np
import trimesh
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

INCH_MM = 25.4
FLAT_PART_MAX_Z_MM = 8.0  # thinner than this counts as a "flat" part
NORMAL_TOL = 0.01  # how close a face normal must be to vertical/horizontal
AREA_TOL_MM2 = 0.01  # cross-sections differing by less than this are "the same"
SLOPED_AREA_FRACTION = 0.005  # ignore sloped faces below 0.5% of the surface area
MAX_PREVIEW_LEVELS = 40
MAX_FLOOR_LEVELS = 200
SIMPLIFY_MM = 0.002  # toolpath shapes may move at most this much when tidied (see part_geometry)


class GeometryError(ValueError):
    """Raised when a file can't be loaded as a usable mesh."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyse_stl(path, *, step_down_mm, tool_dia_mm=None, tool_flute_length_mm=None,
                bed_x_mm=300.0, bed_y_mm=180.0, units="mm"):
    """Load an STL and return a JSON-serialisable dict describing the part."""
    mesh = load_mesh(path, units)
    dims = mesh.extents  # (x, y, z) in mm
    height = float(dims[2])
    tool_radius = tool_dia_mm / 2 if tool_dia_mm else None

    surfaces = classify_surfaces(mesh)
    levels = slice_levels(mesh, surfaces["floor_depths"], step_down_mm if surfaces["has_sloped"] else None)
    # find_features attaches each feature's shapely polygon as "_polygon"; it isn't JSON, so strip it.
    features = [{k: v for k, v in f.items() if k != "_polygon"} for f in find_features(levels, tool_radius)]

    analysis = {
        "units_in_file": units,
        "dimensions": {"x": _r(dims[0]), "y": _r(dims[1]), "z": _r(height)},
        "volume_mm3": _r(mesh.volume) if mesh.is_watertight else None,
        "is_watertight": bool(mesh.is_watertight),
        "face_count": int(len(mesh.faces)),
        "is_flat": height < FLAT_PART_MAX_Z_MM,
        "is_2_5d": not surfaces["has_sloped"] and not surfaces["has_undercuts"],
        "has_sloped_surfaces": surfaces["has_sloped"],
        "has_undercuts": surfaces["has_undercuts"],
        "outline": _outline_summary(levels),
        "levels": [_level_summary(lv) for lv in levels],
        "features": features,
        "z_layers": _passes(height, step_down_mm),
        "step_down_mm": step_down_mm,
        "warnings": [],
    }
    analysis["warnings"] = _warnings(analysis, tool_dia_mm, tool_flute_length_mm, bed_x_mm, bed_y_mm)
    analysis["preview_svg"] = top_view_svg(levels, dims, features)
    return analysis


def part_geometry(path, *, step_down_mm, tool_dia_mm=None, units="mm"):
    """The shapes needed for toolpaths: the part outline and each feature's polygon.

    Uses exactly the same steps as analyse_stl, so feature N here is feature N on the
    job page (and in the operation plan's feature_refs).
    """
    mesh = load_mesh(path, units)
    surfaces = classify_surfaces(mesh)
    levels = slice_levels(mesh, surfaces["floor_depths"], step_down_mm if surfaces["has_sloped"] else None)
    features = find_features(levels, tool_dia_mm / 2 if tool_dia_mm else None)
    # Slicing a mesh leaves extra points part-way along straight edges (where the plane
    # crosses a facet's diagonal). Dropping points that sit on a straight line between
    # their neighbours (within SIMPLIFY_MM) keeps the shape and lets circles be written
    # as clean G2/G3 arcs.
    for f in features:
        f["polygon"] = f.pop("_polygon").simplify(SIMPLIFY_MM)
    return {
        "height": float(mesh.extents[2]),
        "outline": part_outline(levels).simplify(SIMPLIFY_MM),
        "features": features,
    }


def part_outline(levels):
    """The part's silhouette seen from above (outer boundaries only, holes filled in)."""
    return unary_union([Polygon(p.exterior) for lv in levels for p in _polygons(lv["geom"])])


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_mesh(path, units="mm"):
    """Load an STL as a single mesh in machine coordinates (see module docstring)."""
    try:
        mesh = trimesh.load(path, file_type="stl", force="mesh")
    except Exception as e:  # trimesh raises many different types for bad files
        raise GeometryError(f"Couldn't read the STL file ({e}).") from None
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise GeometryError("The STL file doesn't contain any triangles.")

    if units == "inch":
        mesh.apply_scale(INCH_MM)

    lo, hi = mesh.bounds
    mesh.apply_translation([-lo[0], -lo[1], -hi[2]])
    return mesh


# ---------------------------------------------------------------------------
# Surface classification: floors, slopes, undercuts
# ---------------------------------------------------------------------------


def classify_surfaces(mesh):
    nz = mesh.face_normals[:, 2]
    area = mesh.area_faces
    centre_z = mesh.triangles_center[:, 2]
    bottom_z = mesh.bounds[0][2]
    total = area.sum()

    up = nz > 1 - NORMAL_TOL  # top surface, pocket floors, shoulders
    wall = np.abs(nz) < NORMAL_TOL
    on_bottom = (nz < -(1 - NORMAL_TOL)) & (centre_z < bottom_z + 1e-3)
    sloped = ~(up | wall) & ~on_bottom & (nz > 0)
    undercut = (nz < -NORMAL_TOL) & ~on_bottom  # faces looking down, above the bottom

    # Every distinct floor depth (as positive depth below the top), plus the full height.
    floor_depths = {round(-float(z), 3) for z in centre_z[up]}
    floor_depths.add(round(-float(bottom_z), 3))
    floor_depths.discard(0.0)

    return {
        "floor_depths": sorted(floor_depths),
        "has_sloped": bool(area[sloped].sum() > SLOPED_AREA_FRACTION * total),
        "has_undercuts": bool(area[undercut].sum() > SLOPED_AREA_FRACTION * total),
    }


# ---------------------------------------------------------------------------
# Slicing into levels
# ---------------------------------------------------------------------------


def slice_levels(mesh, floor_depths, step_down_mm=None):
    """Return a list of levels, top first: {"top": d0, "bottom": d1, "geom": shapely geometry}.

    If the part has sloped surfaces the cross-section changes continuously, so the pass
    depths are added as extra boundaries to give a stepped approximation.
    """
    height = float(-mesh.bounds[0][2])
    bounds = set(floor_depths)
    if len(bounds) > MAX_FLOOR_LEVELS:
        # Organic/scanned meshes have flat facets at countless heights; use passes instead.
        bounds = {round(height, 3)}
        step_down_mm = step_down_mm or height / 20
    if step_down_mm:
        bounds.update(round(d, 3) for d in _pass_depths(height, step_down_mm))
    bounds = sorted(b for b in bounds if 0 < b <= height + 1e-6)

    tops = [0.0] + bounds[:-1]
    mids = [-(t + b) / 2 for t, b in zip(tops, bounds)]
    sections = mesh.section_multiplane(plane_origin=[0, 0, 0], plane_normal=[0, 0, 1], heights=mids)

    levels = []
    for top, bottom, section in zip(tops, bounds, sections):
        geom = _section_to_geom(section)
        if levels and levels[-1]["geom"].symmetric_difference(geom).area < AREA_TOL_MM2:
            levels[-1]["bottom"] = bottom  # same shape as the level above: extend it
        else:
            levels.append({"top": top, "bottom": bottom, "geom": geom})
    return levels


def _section_to_geom(section):
    if section is None:
        return MultiPolygon()
    polys = [p for p in section.polygons_full if p.is_valid and p.area > 1e-6]
    return unary_union(polys) if polys else MultiPolygon()


def _polygons(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


# ---------------------------------------------------------------------------
# Features: holes and pockets
# ---------------------------------------------------------------------------


def find_features(levels, tool_radius=None):
    """Find material that has to be removed inside the part's outline, level by level.

    At each level, "removed" = the part's outline seen from above minus the material at
    that level. Each separate piece of that is an opening: a hole, a pocket, or a step
    cut in from the edge. An opening is reported at the level where it first appears and
    followed down for as long as the *whole* opening stays open, so a counterbore is
    reported as a wide shallow hole plus a narrower deeper one.
    """
    if not levels:
        return []
    outline = part_outline(levels)
    removed =[outline.difference(lv["geom"]) for lv in levels]
    part_bottom = levels[-1]["bottom"]

    features = []
    for i, level in enumerate(levels):
        for opening in _polygons(removed[i]):
            if opening.area < AREA_TOL_MM2:
                continue
            # Same shape as an opening in the level above? Then it's that one continuing down.
            # (A smaller opening inside a bigger one above, like a counterbore's hole, is new.)
            if i > 0 and any(
                opening.symmetric_difference(above).area < AREA_TOL_MM2 for above in _polygons(removed[i - 1])
            ):
                continue

            depth = level["bottom"]
            for j in range(i + 1, len(levels)):
                if opening.difference(removed[j]).area > AREA_TOL_MM2:
                    break  # material appears inside the opening: its floor is here
                depth = levels[j]["bottom"]

            feature = _describe_opening(
                opening,
                start_depth=level["top"],
                depth=depth,
                through=depth >= part_bottom - 1e-6,
                at_edge=opening.exterior.distance(outline.boundary) < 0.01,
                tool_radius=tool_radius,
            )
            feature["through"] = depth >= part_bottom - 1e-6
            feature["_polygon"] = opening  # kept for toolpaths; stripped from the JSON analysis
            features.append(feature)
    # Reading order (top-to-bottom, left-to-right) so the numbers are easy to find on the preview.
    features.sort(key=lambda f: (round(-f["center"][1], 1), round(f["center"][0], 1)))
    return features


def _describe_opening(opening, *, start_depth, depth, through, at_edge, tool_radius):
    shape = Polygon(opening.exterior)  # ignore islands when judging the overall shape
    minx, miny, maxx, maxy = shape.bounds
    circularity = 4 * math.pi * shape.area / shape.length**2 if shape.length else 0
    # A round outer edge with an island inside (e.g. a ring groove) is not a hole.
    is_round = circularity > 0.9 and not at_edge and not opening.interiors
    if at_edge:
        kind = "step"  # open to the side, e.g. a shoulder or rebate
    elif is_round:
        kind = "through_hole" if through else "blind_hole"
    else:
        kind = "through_cutout" if through else "pocket"

    feature = {
        "type": kind,
        "center": [_r(shape.centroid.x), _r(shape.centroid.y)],
        "size": [_r(maxx - minx), _r(maxy - miny)],
        "area_mm2": _r(opening.area),
        "islands": len(opening.interiors),
        "start_depth": _r(start_depth),
        "depth": _r(depth),
        # Bounding-box width is exact for a tessellated circle (area-based would read slightly small).
        "diameter": _r((maxx - minx + maxy - miny) / 2) if is_round else None,
    }
    if tool_radius and not at_edge:
        # Shrinking the opening by the tool radius leaves the area the tool centre can reach.
        feature["fits_tool"] = not opening.buffer(-(tool_radius - 0.01)).is_empty
    return feature


# ---------------------------------------------------------------------------
# Summaries, passes, warnings
# ---------------------------------------------------------------------------


def _outline_summary(levels):
    if not levels:
        return None
    footprint = unary_union([lv["geom"] for lv in levels])
    polys = _polygons(footprint)
    return {
        "separate_pieces": len(polys),
        "area_mm2": _r(footprint.area),
        "perimeter_mm": _r(sum(p.exterior.length for p in _polygons(part_outline(levels)))),
    }


def _level_summary(level):
    polys = _polygons(level["geom"])
    return {
        "top_depth": _r(level["top"]),
        "bottom_depth": _r(level["bottom"]),
        "regions": len(polys),
        "openings": sum(len(p.interiors) for p in polys),
        "area_mm2": _r(level["geom"].area),
    }


def _pass_depths(height, step_down_mm):
    n = max(1, math.ceil(height / step_down_mm - 1e-9))
    return [min(k * step_down_mm, height) for k in range(1, n + 1)]


def _passes(height, step_down_mm):
    return [{"pass": i, "z": _r(-d)} for i, d in enumerate(_pass_depths(height, step_down_mm), start=1)]


def _warnings(a, tool_dia, flute_length, bed_x, bed_y):
    w = []
    d = a["dimensions"]

    if not a["is_watertight"]:
        w.append("Mesh is not watertight (has gaps). Results may be unreliable; try re-exporting the STL.")

    if d["x"] > bed_x or d["y"] > bed_y:
        if d["y"] <= bed_x and d["x"] <= bed_y:
            w.append(f"Part is {d['x']} x {d['y']} mm, which only fits the {bed_x:g} x {bed_y:g} mm bed if rotated 90°.")
        else:
            w.append(f"Part is {d['x']} x {d['y']} mm, larger than the {bed_x:g} x {bed_y:g} mm bed.")

    if max(d.values()) < 5:
        w.append("Part is under 5 mm in every direction. If it should be bigger, the STL may be in inches: re-upload with units set to inches.")

    if d["z"] > max(d["x"], d["y"]):
        w.append("Part is taller than it is wide. Check it's lying flat in the orientation you want to cut (Z up).")

    if a["has_undercuts"]:
        w.append("Part has undercuts (surfaces facing down). A 3-axis router cutting from the top can't reach them.")
    if a["has_sloped_surfaces"]:
        w.append("Part has sloped or curved surfaces. This app plans 2.5D cuts, so they'll be approximated as steps.")

    if flute_length and d["z"] > flute_length:
        w.append(f"Part is {d['z']} mm deep but the tool's flute length is only {flute_length:g} mm.")

    if tool_dia:
        for f in a["features"]:
            if f.get("fits_tool") is False:
                size = f"Ø{f['diameter']}" if f["diameter"] else f"{f['size'][0]} x {f['size'][1]}"
                w.append(
                    f"{f['type'].replace('_', ' ').capitalize()} at ({f['center'][0]}, {f['center'][1]}) is {size} mm, "
                    f"too small for the {tool_dia:g} mm tool."
                )
    return w


# ---------------------------------------------------------------------------
# Top-view SVG preview
# ---------------------------------------------------------------------------


def top_view_svg(levels, dims, features=()):
    """Draw the part from above, shading each level by depth (deeper = darker).

    Features get numbered markers matching their order in the feature list.
    """
    width, height = float(dims[0]), float(dims[1])
    if not levels or width <= 0 or height <= 0:
        return None

    pad = max(width, height) * 0.04
    view = f"{-pad:.3f} {-pad:.3f} {width + 2 * pad:.3f} {height + 2 * pad:.3f}"

    # Draw deepest first so shallower (higher) material is painted on top.
    shown = levels if len(levels) <= MAX_PREVIEW_LEVELS else levels[:: math.ceil(len(levels) / MAX_PREVIEW_LEVELS)]
    paths = []
    n = len(shown)
    for i, level in enumerate(reversed(shown)):
        shade = 0.3 + 0.45 * (i / max(n - 1, 1))  # 0.3 (deep) .. 0.75 (top); through = background
        d = _geom_to_path(level["geom"].simplify(0.02), height)
        if d:
            paths.append(
                f'<path d="{d}" fill-rule="evenodd" class="lvl" style="--shade:{shade:.2f}">'
                f'<title>{level["top"]:g} to {level["bottom"]:g} mm deep</title></path>'
            )

    r = max(width, height) * 0.016
    for num, f in enumerate(features, start=1):
        x, y = f["center"][0], height - f["center"][1]
        if min(f["size"]) < 4 * r:
            # Small feature: put the marker beside it (up-right) so it doesn't hide it.
            x += f["size"][0] / 2 + r * 0.8
            y -= f["size"][1] / 2 + r * 0.8
        paths.append(
            f'<g class="feat-marker"><circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}"/>'
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{r * 1.3:.2f}">{num}</text></g>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" class="top-view" '
        f'role="img" aria-label="Top view of the part">{"".join(paths)}</svg>'
    )


def _geom_to_path(geom, height):
    # SVG's Y axis points down, so flip Y to keep the part the right way up.
    parts = []
    for poly in _polygons(geom):
        for ring in [poly.exterior, *poly.interiors]:
            pts = " L".join(f"{x:.2f},{height - y:.2f}" for x, y in ring.coords)
            parts.append(f"M{pts} Z")
    return " ".join(parts)


def _r(value, places=3):
    return round(float(value), places)
