import os
from flask import Flask

from .models import init_db
from .routes import routes_web, routes_api

BASE_DIR = os.path.dirname(os.path.dirname(__file__))


def _ensure_dirs():
    for d in ['data', 'uploads', 'output', 'client_build']:
        os.makedirs(os.path.join(BASE_DIR, d), exist_ok=True)


def create_manage_app(listen_host='127.0.0.1', listen_port=8080):
    """Create the management web UI Flask app."""
    app = Flask(__name__)
    app.secret_key = os.urandom(24).hex()

    # Store listen address in config for EXE builder
    app.config['LISTEN_HOST'] = listen_host
    app.config['LISTEN_PORT'] = listen_port

    _ensure_dirs()

    with app.app_context():
        init_db()

    app.register_blueprint(routes_web, url_prefix='/fishfish')

    return app


def create_api_app():
    """Create the API listener Flask app (data collection endpoint only)."""
    app = Flask(__name__)

    _ensure_dirs()

    with app.app_context():
        init_db()

    app.register_blueprint(routes_api)

    return app
