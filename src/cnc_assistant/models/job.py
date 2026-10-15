import json
from datetime import datetime, timezone

from ..extensions import db

JOB_STATUSES = ["pending", "analysed", "planned", "gcode_ready"]
STL_UNITS = ["mm", "inch"]


class Job(db.Model):
    """A part to machine. Filled in over Phases 2-4 (analysis, plan, gcode)."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    original_filename = db.Column(db.String(255))  # name the user uploaded, for display
    stl_filename = db.Column(db.String(255))  # UUID name stored in UPLOAD_DIR
    units = db.Column(db.String(10), default="mm")  # units the STL was exported in
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"))
    material_id = db.Column(db.Integer, db.ForeignKey("material.id"))

    # Stock setup (see services/setup.py). None = automatic default.
    cut_from_stock = db.Column(db.Boolean, default=True)  # add an outline cut (with tabs) to free the part
    stock_thickness_mm = db.Column(db.Float)  # None = same as the part's height
    stock_x_mm = db.Column(db.Float)  # None = part + margin
    stock_y_mm = db.Column(db.Float)
    part_offset_x_mm = db.Column(db.Float)  # part's front-left corner from the stock's; None = centred
    part_offset_y_mm = db.Column(db.Float)
    origin_xy = db.Column(db.String(30))  # where X0 Y0 is: a key of services.setup.ORIGIN_XY
    origin_z = db.Column(db.String(30))  # where Z0 is: a key of services.setup.ORIGIN_Z

    # Geometry facts (populated after analysis)
    dim_x_mm = db.Column(db.Float)
    dim_y_mm = db.Column(db.Float)
    dim_z_mm = db.Column(db.Float)
    is_flat = db.Column(db.Boolean)  # thinner than 8 mm
    is_watertight = db.Column(db.Boolean)
    analysis_json = db.Column(db.Text)  # full output of services.geometry.analyse_stl

    # AI output
    operation_plan = db.Column(db.Text)  # JSON string from the planner (planner/)
    gcode_filename = db.Column(db.String(255))  # .nc file stored in GCODE_DIR
    gcode_info_json = db.Column(db.Text)  # stats, skipped ops and toolpath preview for the job page

    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    tool = db.relationship("Tool")
    material = db.relationship("Material")

    @property
    def analysis(self):
        """The stored geometry analysis as a dict (or None before analysis)."""
        return json.loads(self.analysis_json) if self.analysis_json else None

    @property
    def gcode_info(self):
        return json.loads(self.gcode_info_json) if self.gcode_info_json else None

    @property
    def plan(self):
        """The stored operation plan as a dict (or None)."""
        return json.loads(self.operation_plan) if self.operation_plan else None

    def set_plan(self, plan):
        self.operation_plan = json.dumps(plan) if plan else None
        self.status = "planned" if plan else "analysed"

    def set_analysis(self, analysis):
        self.analysis_json = json.dumps(analysis)
        dims = analysis["dimensions"]
        self.dim_x_mm, self.dim_y_mm, self.dim_z_mm = dims["x"], dims["y"], dims["z"]
        self.is_flat = analysis["is_flat"]
        self.is_watertight = analysis["is_watertight"]
        self.status = "analysed"

    def __repr__(self):
        return f"<Job {self.name}>"
