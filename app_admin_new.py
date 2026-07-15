import logging
import os

from flask import Flask

from auto_migration_admin import run_auto_migration_admin
from config_admin import DevelopmentConfig, ProductionConfig
from extensions_admin import db, jwt, cors, limiter, apply_cors_headers, ALLOWED_ORIGINS
from logging_setup import setup_logging
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
    superlink_admin_bp,
)

logger = logging.getLogger(__name__)


def _run_migrations():
    from sqlalchemy import text
    migrations = [
        "ALTER TABLE admin_boleto ADD COLUMN IF NOT EXISTS orcamento_item_id INTEGER;",
        "ALTER TABLE admin_boleto ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP;",
    ]
    try:
        with db.engine.connect() as conn:
            for sql in migrations:
                conn.execute(text(sql))
            conn.commit()
        logger.info("_run_migrations: OK")
    except Exception:
        logger.exception("_run_migrations: falhou (tabela pode não existir ainda)")


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
    limiter.init_app(app)
    cors.init_app(app, resources={r'/*': {'origins': ALLOWED_ORIGINS}}, supports_credentials=False)

    with app.app_context():
        _run_migrations()
        run_auto_migration_admin()

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
    app.register_blueprint(superlink_admin_bp)

    logger.info(f"app_admin: {len(list(app.url_map.iter_rules()))} rotas registradas")

    return app


if __name__ == '__main__':
    app = create_app()
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)


# NOTA: o entrypoint real usado em produção é app_admin.py (Dockerfile.admin
# e fly-deploy/obraly-admin-api/Dockerfile usam "gunicorn app_admin:app").
# Este módulo é mantido sincronizado como factory alternativa, mas não é
# referenciado por nenhum CMD/Dockerfile atual.
app = create_app()
