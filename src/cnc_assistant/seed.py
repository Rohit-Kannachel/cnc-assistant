"""Default library entries, added the first time the app starts with an empty database."""

from .extensions import db
from .models import Material, Tool


def seed_defaults():
    if Tool.query.first() is None:
        db.session.add(
            Tool(
                name="2-flute carbide 3.175mm",
                tool_type="flat_end_mill",
                shank_dia_mm=3.175,
                cut_dia_mm=3.175,
                flutes=2,
                material="carbide",
                notes=(
                    "Tungsten-carbide flat nose spiral end mill. "
                    "Usable on acrylic with conservative settings; a single-flute bit is better for acrylic."
                ),
            )
        )

    if Material.query.first() is None:
        db.session.add(
            Material(
                name="Acrylic / Plexiglass",
                category="acrylic",
                spindle_rpm=10000,
                feed_rate_mmpm=150,
                plunge_rate_mmpm=50,
                step_down_mm=0.5,
                step_over_mm=1.4,
                notes=(
                    "Proven on this machine with the 3.175mm 2-flute carbide (Fusion 360 programs, June): "
                    "10,000 RPM, 150 mm/min, plunge 50, 0.5 mm step-down. 1.0 mm step-down caused problems; "
                    "0.75 mm is the next thing to try. The scrap may be acrylic (Plexiglas is acrylic) or "
                    "polycarbonate (tougher, bends rather than snaps): these settings worked on it either way. "
                    "White powdery chips = good. Melting/re-welding = raise feed or lower RPM."
                ),
            )
        )

    db.session.commit()
