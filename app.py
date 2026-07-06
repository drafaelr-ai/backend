"""Obraly API — Flask application factory.

Architecture:
    extensions.py      shared SQLAlchemy, JWT, CORS, Limiter instances
    config.py          environment-based configuration
    models/            25 SQLAlchemy models
    services/          7 reusable helpers (auth, notifications, utils)
    routes/            13 blueprints organised by domain
    auto_migration.py  idempotent startup schema migrations (psycopg2 direct)
    app.py             factory function + global CORS/OPTIONS handlers
"""
import os
import logging

from flask import Flask, request

from logging_setup import setup_logging
from extensions import db, jwt, cors, limiter
from config import Config, _build_database_url, _DB_USER
from auto_migration import run_auto_migration

# Models — imported so SQLAlchemy discovers them before any db operation.
from models.servico_base import ServicoBase           # noqa: F401
from models.user import User, user_obra_association   # noqa: F401
from models.obra import Obra                          # noqa: F401
from models.servico import Servico                    # noqa: F401
from models.notificacao import Notificacao            # noqa: F401
from models.pagamento_servico import PagamentoServico # noqa: F401
from models.lancamento import Lancamento              # noqa: F401
from models.orcamento import Orcamento                # noqa: F401
from models.nota_fiscal import NotaFiscal             # noqa: F401
from models.diario_obra import DiarioObra             # noqa: F401
from models.diario_imagem import DiarioImagem         # noqa: F401
from models.anexo_orcamento import AnexoOrcamento     # noqa: F401
from models.caixa_obra import CaixaObra               # noqa: F401
from models.servico_usuario import ServicoUsuario     # noqa: F401
from models.orcamento_eng_etapa import OrcamentoEngEtapa  # noqa: F401
from models.orcamento_eng_item import OrcamentoEngItem    # noqa: F401
from models.movimentacao_caixa import MovimentacaoCaixa   # noqa: F401
from models.fechamento_caixa import FechamentoCaixa   # noqa: F401
from models.pagamento_futuro import PagamentoFuturo   # noqa: F401
from models.boleto import Boleto                      # noqa: F401
from models.parcela_individual import ParcelaIndividual   # noqa: F401
from models.pagamento_parcelado import PagamentoParcelado # noqa: F401
from models.cronograma_etapa import CronogramaEtapa   # noqa: F401
from models.cronograma_obra import CronogramaObra     # noqa: F401
from models.agenda_demanda import AgendaDemanda       # noqa: F401
from models.superlink import Superlink                # noqa: F401
# Módulo Pessoal / RH
from models.categoria_mo import CategoriaMO           # noqa: F401
from models.convencao_coletiva import ConvencaoColetiva  # noqa: F401
from models.convencao_valor import ConvencaoValor     # noqa: F401
from models.funcionario import Funcionario            # noqa: F401
from models.pagamento_salario import PagamentoSalario # noqa: F401
from models.encargo import Encargo                    # noqa: F401

from routes import (
    notificacoes_bp, bi_bp, diario_bp, auth_bp, admin_bp, sid_bp,
    caixa_bp, servicos_bp, boletos_bp, lancamentos_bp,
    cronograma_bp, orcamento_eng_bp, obras_bp, superlink_bp,
    rh_bp,
)

setup_logging()
logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = [
    'https://obraly.uk',
    'https://www.obraly.uk',
    'http://localhost:3000',
    'http://localhost:3001',
    'https://localhost',       # Capacitor Android (androidScheme: https)
    'capacitor://localhost',   # Capacitor Android (scheme padrão)
    'ionic://localhost',       # fallback Ionic/Capacitor
]


def apply_cors_headers(response):
    """Camada 2 CORS — garante headers em toda resposta, independente do flask-cors."""
    origin = request.headers.get('Origin', '')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response


def create_app(config_class=Config):
    """Create and configure the Flask application.

    Initialises extensions, applies 2-layer CORS, registers all 13 blueprints,
    and wires the session teardown handler.
    """
    app = Flask(__name__)
    app.config.from_object(config_class)

    jwt_secret = os.environ.get('JWT_SECRET_KEY')
    if not jwt_secret:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable is required. "
            "Set it in .env (dev) or in the deploy provider (prod)."
        )
    app.config['JWT_SECRET_KEY'] = jwt_secret
    logger.info("JWT configurado: access=7d")

    logger.info("--- [LOG] Lendo variável de ambiente DB_PASSWORD... ---")
    db_password = os.environ.get('DB_PASSWORD')
    if not db_password:
        logger.error("--- [ERRO CRÍTICO] DB_PASSWORD não encontrada! ---")
        raise ValueError("Variável de ambiente DB_PASSWORD não definida.")
    logger.info("--- [LOG] Variável DB_PASSWORD carregada com sucesso. ---")

    database_url = _build_database_url()
    logger.info(f"--- [LOG] String de conexão criada para usuário {_DB_USER} ---")
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url

    db.init_app(app)
    logger.info("--- [LOG] SQLAlchemy inicializado ---")
    jwt.init_app(app)
    limiter.init_app(app)

    # === CAMADA 1 — flask-cors ===
    cors.init_app(app, resources={r'/*': {'origins': ALLOWED_ORIGINS}}, supports_credentials=False)
    logger.info(f"CORS configurado para origens: {ALLOWED_ORIGINS}")

    # === CAMADA 2 — after_request ===
    app.after_request(apply_cors_headers)

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()
    logger.info("--- [LOG] Teardown de sessão configurado ---")

    app.register_blueprint(notificacoes_bp)
    app.register_blueprint(bi_bp)
    app.register_blueprint(diario_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(sid_bp)
    app.register_blueprint(caixa_bp)
    app.register_blueprint(servicos_bp)
    app.register_blueprint(boletos_bp)
    app.register_blueprint(lancamentos_bp)
    app.register_blueprint(cronograma_bp)
    app.register_blueprint(orcamento_eng_bp)
    app.register_blueprint(obras_bp)
    app.register_blueprint(superlink_bp)
    app.register_blueprint(rh_bp)

    return app


# Run startup migrations, then create the global app instance.
logger.info("--- [LOG] Executando auto-migration antes de iniciar o app ---")
run_auto_migration()
logger.info("--- [LOG] Auto-migration concluída, iniciando app ---\n")

app = create_app()


# === CAMADA 3 — catch-all OPTIONS (handles preflights for all routes) ===
@app.route('/<path:any_path>', methods=['OPTIONS'])
def global_options(any_path):
    return ('', 200)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"--- [LOG] Iniciando servidor Flask na porta {port} ---")
    app.run(host='0.0.0.0', port=port, debug=True)
