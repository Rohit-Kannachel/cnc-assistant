from flask import Blueprint, current_app, render_template

from ..models import Job, Material, Tool

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return render_template(
        "index.html",
        tool_count=Tool.query.count(),
        material_count=Material.query.count(),
        job_count=Job.query.count(),
        machine=current_app.config["MACHINE_NAME"],
        bed=(current_app.config["BED_X_MM"], current_app.config["BED_Y_MM"]),
    )
