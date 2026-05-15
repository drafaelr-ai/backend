import logging
import os

from flask import Flask

from config_admin import DevelopmentConfig, ProductionConfig
from extensions_admin import db, jwt, cors, apply_cors_headers
from logging_setup import setup_logging
from auto_migration_admin import run_auto_migration_admin
from routes_admin import (
    health_bp,
    auth_admin_bp,
    usuarios_admin_bp,
    categorias_admin_bp,
    imoveis_admin_bp,
    lancamentos_admin_bp,
    dashboard_admin_bp,
    importar_obra_bp,
    boletos_admin_bp,
)

logger = logging.getLogger(__name__)


def create_app(config=None):
    app = Flask(__name__)

    if config is None:
        flask_env = os.environ.get('FLASK_ENV', 'production')
        cfg_class = DevelopmentConfig if flask_env == 'development' else ProductionConfig
        config = cfg_class.from_env()

    app.config.from_object(config)

    setup_logging()

    db.init_app(app)
    jwt.init_app(app)
    cors.init_app(app, resources={r'/*': {'origins': '*'}}, supports_credentials=False)

    app.after_request(apply_cors_headers)

    @app.route('/<path:any_path>', methods=['OPTIONS'])
    def handle_options(any_path):
        return '', 200

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_admin_bp)
    app.register_blueprint(usuarios_admin_bp)
    app.register_blueprint(categorias_admin_bp)
    app.register_blueprint(imoveis_admin_bp)
    app.register_blueprint(lancamentos_admin_bp)
    app.register_blueprint(dashboard_admin_bp)
    app.register_blueprint(importar_obra_bp)
    app.register_blueprint(boletos_admin_bp)

    logger.info(f"app_admin: {len(list(app.url_map.iter_rules()))} rotas registradas")

    with app.app_context():
        run_auto_migration_admin()

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)


# gunicorn entrypoint (Dockerfile.admin: gunicorn app_admin_new:app)
app = create_app()
