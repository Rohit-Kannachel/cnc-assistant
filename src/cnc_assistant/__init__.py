"""CNC Assistant: a local Flask app for planning jobs on a Genmitsu 3018 Pro."""

from flask import Flask, flash, redirect, request, url_for

from .config import Config
from .extensions import db


def create_app(config_class=Config):
    """Application factory. Used by `flask --app cnc_assistant run` and by `cnc-assistant`."""
    app = Flask(__name__, instance_path=str(config_class.DATA_DIR))
    app.config.from_object(config_class)

    # Make sure the data folders exist before anything tries to write to them.
    for key in ("DATA_DIR", "UPLOAD_DIR", "GCODE_DIR", "TOOL_IMAGE_DIR"):
        folder = app.config[key]
        folder.mkdir(parents=True, exist_ok=True)

    db.init_app(app)

    from .routes import register_blueprints

    register_blueprints(app)

    @app.template_filter("mm")
    def format_mm(value, places=2):
        """Show a measurement with up to `places` decimals and no trailing zeros: 3.997 -> 4, 46.969 -> 46.97."""
        if value is None:
            return "—"
        return f"{round(float(value), places):g}"

    @app.errorhandler(413)
    def too_large(_error):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(f"That file is too large (limit {limit_mb} MB).", "error")
        return redirect(request.referrer or url_for("main.index"))

    # Create tables and seed defaults on first run.
    with app.app_context():
        from . import models  # noqa: F401  (registers the model classes with SQLAlchemy)
        from .db_upgrade import add_missing_columns, fix_legacy_values
        from .seed import seed_defaults

        db.create_all()
        add_missing_columns()
        fix_legacy_values()
        seed_defaults()

    return app
