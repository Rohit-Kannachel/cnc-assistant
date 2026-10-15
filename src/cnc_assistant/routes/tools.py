"""Tool library: list, add, edit, delete, and tool photos."""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for

from ..extensions import db
from ..models import Tool
from ..models.tool import TOOL_MATERIALS, TOOL_TYPES
from ..services.images import ImageError, delete_image, save_image
from .forms import FormError, number, text

bp = Blueprint("tools", __name__, url_prefix="/tools")


def _image_dir():
    return current_app.config["TOOL_IMAGE_DIR"]


def _apply_form(tool, form):
    """Copy validated form values onto a Tool. Raises FormError on bad input."""
    tool.name = text(form, "name")
    tool.tool_type = text(form, "tool_type")
    tool.shank_dia_mm = number(form, "shank_dia_mm")
    tool.cut_dia_mm = number(form, "cut_dia_mm")
    tool.flutes = number(form, "flutes", cast=int)
    tool.material = text(form, "material")
    tool.flute_length_mm = number(form, "flute_length_mm", required=False)
    tool.total_length_mm = number(form, "total_length_mm", required=False)
    tool.notes = text(form, "notes", required=False)

    if tool.flute_length_mm and tool.total_length_mm and tool.total_length_mm <= tool.flute_length_mm:
        raise FormError("Total length must be longer than the flute length (it includes the shank).")


def _apply_image(tool, files, form):
    """Handle the photo fields. Returns the old filename to delete after commit, or None."""
    upload = files.get("image")
    old = tool.image_filename
    if upload and upload.filename:
        tool.image_filename = save_image(upload, _image_dir())
        return old
    if form.get("remove_image") and old:
        tool.image_filename = None
        return old
    return None


def _save(tool, is_new):
    """Validate the submitted form and photo, then commit. Returns an error message or None."""
    try:
        _apply_form(tool, request.form)
        old_image = _apply_image(tool, request.files, request.form)
    except (FormError, ImageError) as e:
        if not is_new:
            db.session.rollback()
        return str(e)
    if is_new:
        db.session.add(tool)
    db.session.commit()
    delete_image(_image_dir(), old_image)  # only once the DB no longer points at it
    return None


def _render_form(tool, title):
    return render_template(
        "tools/form.html", tool=tool, title=title, tool_types=TOOL_TYPES, tool_materials=TOOL_MATERIALS
    )


@bp.route("/")
def index():
    tools = Tool.query.order_by(Tool.name).all()
    return render_template("tools/index.html", tools=tools)


@bp.route("/new", methods=["GET", "POST"])
def new():
    tool = Tool()
    if request.method == "POST":
        error = _save(tool, is_new=True)
        if error:
            flash(error, "error")
            return _render_form(tool, "Add tool")
        flash(f"Added {tool.name}.", "success")
        return redirect(url_for("tools.index"))
    return _render_form(tool, "Add tool")


@bp.route("/<int:tool_id>/edit", methods=["GET", "POST"])
def edit(tool_id):
    tool = db.get_or_404(Tool, tool_id)
    if request.method == "POST":
        error = _save(tool, is_new=False)
        if error:
            flash(error, "error")
            return _render_form(tool, "Edit tool")
        flash(f"Saved {tool.name}.", "success")
        return redirect(url_for("tools.index"))
    return _render_form(tool, "Edit tool")


@bp.route("/<int:tool_id>/delete", methods=["POST"])
def delete(tool_id):
    tool = db.get_or_404(Tool, tool_id)
    image = tool.image_filename
    db.session.delete(tool)
    db.session.commit()
    delete_image(_image_dir(), image)
    flash(f"Deleted {tool.name}.", "success")
    return redirect(url_for("tools.index"))


@bp.route("/<int:tool_id>/image")
def image(tool_id):
    tool = db.get_or_404(Tool, tool_id)
    if not tool.image_filename:
        abort(404)
    return send_from_directory(_image_dir(), tool.image_filename)
