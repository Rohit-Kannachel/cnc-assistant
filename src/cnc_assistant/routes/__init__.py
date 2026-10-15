from .jobs import bp as jobs_bp
from .main import bp as main_bp
from .materials import bp as materials_bp
from .tools import bp as tools_bp


def register_blueprints(app):
    app.register_blueprint(main_bp)
    app.register_blueprint(tools_bp)
    app.register_blueprint(materials_bp)
    app.register_blueprint(jobs_bp)
