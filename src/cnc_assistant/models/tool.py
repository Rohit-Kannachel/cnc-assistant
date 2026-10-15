from datetime import datetime, timezone

from ..extensions import db

TOOL_TYPES = ["flat_end_mill", "ball_end_mill", "v_bit", "drill"]
TOOL_MATERIALS = ["carbide", "hss", "cobalt"]


class Tool(db.Model):
    """A cutting tool in the user's tool library."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    tool_type = db.Column(db.String(30), nullable=False, default="flat_end_mill")
    shank_dia_mm = db.Column(db.Float, nullable=False)
    cut_dia_mm = db.Column(db.Float, nullable=False)
    flutes = db.Column(db.Integer, nullable=False, default=2)
    material = db.Column(db.String(30), nullable=False, default="carbide")
    flute_length_mm = db.Column(db.Float)  # usable cutting length
    total_length_mm = db.Column(db.Float)  # overall length = flute length + shank length
    image_filename = db.Column(db.String(255))  # photo stored in TOOL_IMAGE_DIR
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def shank_length_mm(self):
        """Length of the non-cutting part, if both lengths are known."""
        if self.total_length_mm is None or self.flute_length_mm is None:
            return None
        return round(self.total_length_mm - self.flute_length_mm, 3)

    def __repr__(self):
        return f"<Tool {self.name}>"
