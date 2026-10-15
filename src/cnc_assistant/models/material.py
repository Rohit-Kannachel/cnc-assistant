from datetime import datetime, timezone

from ..extensions import db

# Category values match the rules engine's MaterialType (planner/rules_engine.py).
MATERIAL_CATEGORIES = {
    "acrylic": "Acrylic / plastic",
    "wood_soft": "Softwood / MDF",
    "wood_hard": "Hardwood",
    "foam": "Foam",
    "pcb": "PCB",
    "aluminium": "Aluminium",
}


class Material(db.Model):
    """A stock material with the feeds and speeds to cut it."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(30), nullable=False, default="acrylic")
    spindle_rpm = db.Column(db.Integer, nullable=False)
    feed_rate_mmpm = db.Column(db.Float, nullable=False)  # XY feed, mm/min
    plunge_rate_mmpm = db.Column(db.Float, nullable=False)  # Z feed, mm/min
    step_down_mm = db.Column(db.Float, nullable=False)  # depth per pass
    step_over_mm = db.Column(db.Float, nullable=False)  # lateral pass spacing
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Material {self.name}>"
