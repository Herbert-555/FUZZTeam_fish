import os
from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader

from .extensions import get_extension
from .models import init_db
from .routes import routes_web, routes_api

BASE_DIR = os.path.dirname(os.path.dirname(__file__))


def _prepare_extension(app):
    ext = get_extension()
    if not ext:
        return None
    tpl_dir = ext.template_dir()
    if tpl_dir and os.path.isdir(tpl_dir):
        app.jinja_loader = ChoiceLoader([FileSystemLoader(tpl_dir), app.jinja_loader])

    @app.context_processor
    def _inject_extension():
        return {'bypass_enabled': True}

    return ext


def _disguise_headers(app):
    @app.after_request
    def _spoof_server(response):
        response.headers['Server'] = 'nginx'
        response.headers.pop('X-Powered-By', None)
        return response


def _ensure_dirs():
    for d in ['data', 'uploads', 'output', 'client_build']:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)


def create_manage_app(admin_path=''):
    """Create the management web UI Flask app."""
    app = Flask(__name__)
    app.secret_key = 'drill_console_secret_2026'

    app.config['ADMIN_PATH'] = admin_path

    _ensure_dirs()

    with app.app_context():
        init_db()

    prefix = f'/{admin_path}' if admin_path else '/fishfish'
    ext = _prepare_extension(app)
    app.register_blueprint(routes_web, url_prefix=prefix)
    if ext:
        ext.register_web(app, prefix)
    _disguise_headers(app)

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

    ext = _prepare_extension(app)
    app.register_blueprint(routes_api)
    if ext:
        ext.register_api(app)
    _disguise_headers(app)

    return app
