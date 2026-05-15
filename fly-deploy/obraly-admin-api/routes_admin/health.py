import logging
from datetime import datetime

from flask import Blueprint, jsonify

from extensions_admin import db
from models_admin import Usuario, Categoria
from services_admin import criar_categorias_padrao

logger = logging.getLogger(__name__)

health_bp = Blueprint('health_admin', __name__)


@health_bp.route('/', methods=['GET'])
def index():
    return jsonify({
        'status': 'online',
        'modulo': 'Obraly Admin - Gestão Patrimonial',
        'versao': '1.0.0',
        'timestamp': datetime.utcnow().isoformat()
    })


@health_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'module': 'admin'})


@health_bp.route('/init-db', methods=['GET', 'POST'])
def init_db():
    try:
        db.create_all()
        criar_categorias_padrao()

        admin = Usuario.query.filter_by(username='admin').first()
        if not admin:
            admin = Usuario(
                username='admin',
                nome='Administrador',
                email='admin@obraly.uk',
                role='admin'
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            logger.info("Usuário admin criado (senha: admin123)")

        return jsonify({
            'status': 'success',
            'message': 'Banco de dados inicializado com sucesso',
            'categorias': Categoria.query.count(),
            'usuarios': Usuario.query.count()
        })
    except Exception as e:
        logger.exception("Erro ao inicializar DB")
        return jsonify({'status': 'error', 'message': str(e)}), 500
