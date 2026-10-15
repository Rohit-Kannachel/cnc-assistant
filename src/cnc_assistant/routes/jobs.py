"""Jobs: upload an STL, analyse its geometry, view the results."""

import json
import re
import uuid
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_from_directory, url_for

from ..extensions import db
from ..models import Job, Material, Tool
from ..models.job import STL_UNITS
from ..planner.adapter import plan_for_job
from ..services.gcode import toolpath_svg, write_gcode
from ..services.geometry import GeometryError, analyse_stl, part_geometry
from ..services.setup import ORIGIN_XY, ORIGIN_Z, setup_svg
from ..services.setup import resolve_setup
from ..services.toolpath import build_toolpaths
from .forms import FormError, number, text

bp = Blueprint("jobs", __name__, url_prefix="/jobs")

NOT_STL_MESSAGE = "Only STL files are supported. Please export as STL from your CAD software."


def _upload_dir() -> Path:
    return current_app.config["UPLOAD_DIR"]


def _choice(form, name, model):
    """Look up the Tool/Material picked in a <select>. Raises FormError if missing."""
    obj_id = form.get(name, type=int)
    obj = db.session.get(model, obj_id) if obj_id else None
    if obj is None:
        raise FormError(f"Please choose a {model.__name__.lower()}.")
    return obj


def _analyse(job):
    """Run the geometry analysis for a job with its current tool/material/units."""
    analysis = analyse_stl(
        _upload_dir() / job.stl_filename,
        step_down_mm=job.material.step_down_mm,
        tool_dia_mm=job.tool.cut_dia_mm,
        tool_flute_length_mm=job.tool.flute_length_mm,
        bed_x_mm=current_app.config["BED_X_MM"],
        bed_y_mm=current_app.config["BED_Y_MM"],
        units=job.units,
    )
    job.set_analysis(analysis)
    _clear_gcode(job)  # any earlier gcode was based on the old analysis

    # Plan straight away: the rules engine is fast and deterministic.
    try:
        job.set_plan(plan_for_job(job))
    except Exception:  # a planner bug shouldn't lose the analysis
        current_app.logger.exception("Planning failed for job %s", job.name)
        job.set_plan(None)
        flash("The analysis worked but the operation planner failed. See the server log for details.", "error")
        return

    try:
        _generate_gcode(job)
    except Exception:
        current_app.logger.exception("G-code generation failed for job %s", job.name)
        flash("The plan worked but G-code generation failed. See the server log for details.", "error")


def _gcode_dir() -> Path:
    return current_app.config["GCODE_DIR"]


def _clear_gcode(job):
    if job.gcode_filename:
        try:
            (_gcode_dir() / job.gcode_filename).unlink(missing_ok=True)
        except OSError:
            pass  # e.g. locked on Windows; a leftover file is harmless
    job.gcode_filename = None
    job.gcode_info_json = None


def _generate_gcode(job):
    """Turn the job's plan into toolpaths and a GRBL .nc file."""
    cut_depth = job.stock_thickness_mm or job.analysis["dimensions"]["z"]
    safe_z = current_app.config["SAFE_Z_MM"]
    geom = part_geometry(
        _upload_dir() / job.stl_filename,
        step_down_mm=job.material.step_down_mm,
        tool_dia_mm=job.tool.cut_dia_mm,
        units=job.units,
    )
    result = build_toolpaths(job.plan, geom, tool_dia_mm=job.tool.cut_dia_mm, cut_depth_mm=cut_depth,
                             safe_z_mm=safe_z)
    setup = _setup(job)
    text, stats = write_gcode(result, job_name=job.name, tool=job.tool, material=job.material,
                              cut_depth_mm=cut_depth, safe_z_mm=safe_z, planner=job.plan["planner"],
                              setup=setup)

    filename = f"{uuid.uuid4().hex}.nc"
    (_gcode_dir() / filename).write_text(text, encoding="ascii")
    job.gcode_filename = filename
    job.gcode_info_json = json.dumps({
        "stats": stats,
        "cut_depth_mm": cut_depth,
        "safe_z_mm": safe_z + setup.dz,
        "setup": setup.to_dict(),
        "skipped": result.skipped,
        "notes": result.notes,
        "toolpath_svg": toolpath_svg(result, geom["outline"], geom["height"], setup=setup),
    })
    job.status = "gcode_ready"


def _setup(job):
    """The job's stock and work origin, with defaults filled in (see services/setup.py)."""
    bed = (current_app.config["BED_X_MM"], current_app.config["BED_Y_MM"])
    return resolve_setup(job, tool_dia_mm=job.tool.cut_dia_mm if job.tool else None, bed=bed)


def _apply_stock_settings(job, form):
    """Read the cut-from-stock checkbox and optional stock thickness (new-job form)."""
    job.cut_from_stock = bool(form.get("cut_from_stock"))
    job.stock_thickness_mm = number(form, "stock_thickness_mm", required=False)


def _apply_setup_form(job, form):
    """Read the Stock & origin form. Blank sizes/offsets mean "automatic"."""
    _apply_stock_settings(job, form)
    job.stock_x_mm = number(form, "stock_x_mm", required=False)
    job.stock_y_mm = number(form, "stock_y_mm", required=False)
    if (job.stock_x_mm is None) != (job.stock_y_mm is None):
        raise FormError("Enter both stock X and Y, or leave both blank for automatic.")
    if form.get("placement") == "offset":
        job.part_offset_x_mm = number(form, "part_offset_x_mm", positive=False)
        job.part_offset_y_mm = number(form, "part_offset_y_mm", positive=False)
    else:
        job.part_offset_x_mm = job.part_offset_y_mm = None
    if form.get("origin_xy") not in ORIGIN_XY or form.get("origin_z") not in ORIGIN_Z:
        raise FormError("Please choose the work origin from the lists.")
    job.origin_xy = form["origin_xy"]
    job.origin_z = form["origin_z"]


def _render_new(form=None):
    return render_template(
        "jobs/new.html",
        form=form or {},
        tools=Tool.query.order_by(Tool.name).all(),
        materials=Material.query.order_by(Material.name).all(),
        units=STL_UNITS,
    )


@bp.route("/")
def index():
    jobs = Job.query.order_by(Job.created_at.desc()).all()
    return render_template("jobs/index.html", jobs=jobs)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "GET":
        return _render_new()

    upload = request.files.get("stl")
    stored_path = None
    try:
        if not upload or not upload.filename:
            raise FormError("Please choose an STL file to upload.")
        if not upload.filename.lower().endswith(".stl"):
            raise FormError(NOT_STL_MESSAGE)

        job = Job(
            name=text(request.form, "name", required=False) or Path(upload.filename).stem,
            original_filename=upload.filename,
            units=request.form.get("units") if request.form.get("units") in STL_UNITS else "mm",
            tool=_choice(request.form, "tool_id", Tool),
            material=_choice(request.form, "material_id", Material),
        )
        _apply_stock_settings(job, request.form)

        job.stl_filename = f"{uuid.uuid4().hex}.stl"
        stored_path = _upload_dir() / job.stl_filename
        upload.save(stored_path)
        _analyse(job)
    except (FormError, GeometryError) as e:
        db.session.rollback()
        if stored_path:
            stored_path.unlink(missing_ok=True)
        flash(str(e), "error")
        return _render_new(request.form)

    db.session.add(job)
    db.session.commit()
    done = "planned and generated G-code" if job.gcode_filename else ("planned" if job.plan else "analysed")
    flash(f"Uploaded {job.name}: analysed, {done}." if job.plan else f"Uploaded and analysed {job.name}.", "success")
    return redirect(url_for("jobs.detail", job_id=job.id))


GCODE_PREVIEW_LINES = 200


@bp.route("/<int:job_id>")
def detail(job_id):
    job = db.get_or_404(Job, job_id)
    gcode_preview = None
    if job.gcode_filename and (_gcode_dir() / job.gcode_filename).exists():
        with open(_gcode_dir() / job.gcode_filename, encoding="ascii") as fh:
            gcode_preview = "".join(line for _, line in zip(range(GCODE_PREVIEW_LINES), fh))
    setup = _setup(job) if job.analysis else None
    return render_template(
        "jobs/detail.html",
        job=job,
        setup=setup,
        setup_diagram=setup_svg(setup) if setup else None,
        origin_xy_options=ORIGIN_XY,
        origin_z_options=ORIGIN_Z,
        a=job.analysis,
        plan=job.plan,
        gcode=job.gcode_info,
        gcode_preview=gcode_preview,
        gcode_preview_lines=GCODE_PREVIEW_LINES,
        tools=Tool.query.order_by(Tool.name).all(),
        materials=Material.query.order_by(Material.name).all(),
        units=STL_UNITS,
    )


@bp.route("/<int:job_id>/reanalyse", methods=["POST"])
def reanalyse(job_id):
    """Change the job's settings and run the analysis and planner again."""
    job = db.get_or_404(Job, job_id)
    try:
        job.tool = _choice(request.form, "tool_id", Tool)
        job.material = _choice(request.form, "material_id", Material)
        if request.form.get("units") in STL_UNITS:
            job.units = request.form["units"]
        _analyse(job)
    except (FormError, GeometryError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("jobs.detail", job_id=job.id))
    db.session.commit()
    flash("Re-analysed, re-planned and regenerated the G-code with the new settings.", "success")
    return redirect(url_for("jobs.detail", job_id=job.id))


@bp.route("/<int:job_id>/setup", methods=["POST"])
def update_setup(job_id):
    """Save the stock size, part position and work origin, then re-plan and regenerate."""
    job = db.get_or_404(Job, job_id)
    if not job.tool or not job.material:
        flash("Choose a tool and material (and click Re-analyse) first.", "error")
        return redirect(url_for("jobs.detail", job_id=job.id))
    try:
        _apply_setup_form(job, request.form)
        _analyse(job)  # stock thickness changes the cut depth, so re-plan as well
    except (FormError, GeometryError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("jobs.detail", job_id=job.id) + "#setup")
    db.session.commit()
    flash("Saved the stock and origin, and regenerated the G-code.", "success")
    return redirect(url_for("jobs.detail", job_id=job.id) + "#setup")


@bp.route("/<int:job_id>/stl")
def download_stl(job_id):
    job = db.get_or_404(Job, job_id)
    return send_from_directory(
        _upload_dir(), job.stl_filename, as_attachment=True, download_name=job.original_filename or "part.stl"
    )


@bp.route("/<int:job_id>/gcode")
def download_gcode(job_id):
    job = db.get_or_404(Job, job_id)
    if not job.gcode_filename or not (_gcode_dir() / job.gcode_filename).exists():
        flash("No G-code for this job yet. Click Re-analyse & plan to generate it.", "error")
        return redirect(url_for("jobs.detail", job_id=job.id))
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", job.name).strip("_") or "job"
    return send_from_directory(_gcode_dir(), job.gcode_filename, as_attachment=True,
                               download_name=f"{safe_name}.nc", mimetype="text/plain")


@bp.route("/<int:job_id>/delete", methods=["POST"])
def delete(job_id):
    job = db.get_or_404(Job, job_id)
    stl = job.stl_filename
    _clear_gcode(job)
    db.session.delete(job)
    db.session.commit()
    if stl:
        try:
            (_upload_dir() / stl).unlink(missing_ok=True)
        except OSError:
            pass  # e.g. locked on Windows; a leftover file is harmless
    flash(f"Deleted {job.name}.", "success")
    return redirect(url_for("jobs.index"))
