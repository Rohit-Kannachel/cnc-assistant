"""Material library (feeds & speeds): list, add, edit, delete."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..models import Material
from ..models.material import MATERIAL_CATEGORIES
from .forms import FormError, number, text

bp = Blueprint("materials", __name__, url_prefix="/materials")


def _apply_form(material, form):
    """Copy validated form values onto a Material. Raises FormError on bad input."""
    material.name = text(form, "name")
    material.category = text(form, "category")
    if material.category not in MATERIAL_CATEGORIES:
        raise FormError("Please choose a category from the list.")
    material.spindle_rpm = number(form, "spindle_rpm", cast=int)
    material.feed_rate_mmpm = number(form, "feed_rate_mmpm")
    material.plunge_rate_mmpm = number(form, "plunge_rate_mmpm")
    material.step_down_mm = number(form, "step_down_mm")
    material.step_over_mm = number(form, "step_over_mm")
    material.notes = text(form, "notes", required=False)


def _render_form(material, title):
    return render_template(
        "materials/form.html", material=material, title=title, categories=MATERIAL_CATEGORIES
    )


@bp.route("/")
def index():
    materials = Material.query.order_by(Material.name).all()
    return render_template("materials/index.html", materials=materials, categories=MATERIAL_CATEGORIES)


@bp.route("/new", methods=["GET", "POST"])
def new():
    material = Material()
    if request.method == "POST":
        try:
            _apply_form(material, request.form)
        except FormError as e:
            flash(str(e), "error")
            return _render_form(material, "Add material")
        db.session.add(material)
        db.session.commit()
        flash(f"Added {material.name}.", "success")
        return redirect(url_for("materials.index"))
    return _render_form(material, "Add material")


@bp.route("/<int:material_id>/edit", methods=["GET", "POST"])
def edit(material_id):
    material = db.get_or_404(Material, material_id)
    if request.method == "POST":
        try:
            _apply_form(material, request.form)
        except FormError as e:
            db.session.rollback()
            flash(str(e), "error")
            return _render_form(material, "Edit material")
        db.session.commit()
        flash(f"Saved {material.name}.", "success")
        return redirect(url_for("materials.index"))
    return _render_form(material, "Edit material")


@bp.route("/<int:material_id>/delete", methods=["POST"])
def delete(material_id):
    material = db.get_or_404(Material, material_id)
    db.session.delete(material)
    db.session.commit()
    flash(f"Deleted {material.name}.", "success")
    return redirect(url_for("materials.index"))
