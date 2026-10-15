"""Build samples/sample_plate.stl: an 80 x 50 x 6 mm plate with one of each feature type.

Run with:  python scripts/make_sample_stl.py   (needs the dev extras: pip install -e ".[dev]")

Features (X/Y from the plate's lower-left corner, depths from the top):
  - 4 x Ø4 through holes near the corners (mounting holes)
  - 1 x Ø2 through hole at (40, 40), deliberately too small for a 3.175 mm bit
  - 24 x 14 mm pocket, 2 mm deep, centred at (28, 22)
  - counterbore at (60, 22): Ø10 x 2 mm deep, then Ø5 through
  - rebate (step) along the top edge: 6 mm wide, 3 mm deep
"""

from pathlib import Path

import trimesh
from shapely.geometry import Point, box

T = 6.0  # plate thickness


def prism(shape, z_bottom, z_top):
    """Extrude a shapely shape between two Z heights."""
    mesh = trimesh.creation.extrude_polygon(shape, z_top - z_bottom)
    mesh.apply_translation([0, 0, z_bottom])
    return mesh


def main():
    plate = prism(box(0, 0, 80, 50).buffer(-4).buffer(4), 0, T)  # rounded corners

    cutters = [prism(Point(x, y).buffer(2, quad_segs=16), -1, T + 1) for x, y in [(6, 6), (74, 6), (6, 38), (74, 38)]]
    cutters.append(prism(Point(40, 40).buffer(1, quad_segs=16), -1, T + 1))
    cutters.append(prism(box(16, 15, 40, 29).buffer(-2).buffer(2), T - 2, T + 1))
    cutters.append(prism(Point(60, 22).buffer(5, quad_segs=16), T - 2, T + 1))
    cutters.append(prism(Point(60, 22).buffer(2.5, quad_segs=16), -1, T + 1))
    cutters.append(prism(box(-1, 44, 81, 51), T - 3, T + 1))

    part = trimesh.boolean.difference([plate, *cutters], engine="manifold")

    out = Path(__file__).resolve().parent.parent / "samples" / "sample_plate.stl"
    out.parent.mkdir(exist_ok=True)
    part.export(out)
    print(f"Wrote {out} ({len(part.faces)} faces, watertight={part.is_watertight})")


if __name__ == "__main__":
    main()
