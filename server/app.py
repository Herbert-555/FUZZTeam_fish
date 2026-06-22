import os
from flask import Flask

from .models import init_db
from .routes import routes_web, routes_api

BASE_DIR = os.path.dirname(os.path.dirname(__file__))


def _ensure_dirs():
    for d in ['data', 'uploads', 'output', 'client_build']:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)


def create_manage_app(admin_path=''):
    """Create the management web UI Flask app."""
    app = Flask(__name__)
    app.secret_key = 'fuzzteam_fish_secret_2026'

    app.config['ADMIN_PATH'] = admin_path

    _ensure_dirs()

    with app.app_context():
        init_db()

    prefix = f'/{admin_path}' if admin_path else '/fishfish'
    app.register_blueprint(routes_web, url_prefix=prefix)

    @app.context_processor
    def inject_admin_path():
        return {'admin_path': prefix}

    return app


def create_api_app():
    """Create the API listener Flask app (data collection endpoint only)."""
    app = Flask(__name__)

    _ensure_dirs()

    with app.app_context():
        init_db()

    app.register_blueprint(routes_api)

    return app
