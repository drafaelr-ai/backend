import logging
import traceback

from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import create_access_token, jwt_required

from extensions import db, limiter
from models.user import User
from services.auth_service import get_current_user

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/register', methods=['POST', 'OPTIONS'])
@limiter.limit("5 per hour", methods=["POST"])
def register():
    logger.info("--- [LOG] Rota /register (POST) acessada ---")
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        dados = request.json
        username = dados.get('username')
        password = dados.get('password')
        # Segurança: role elevado (master/administrador) NUNCA pode ser definido
        # pelo próprio cliente em auto-registro público. Sempre força 'comum'.
        role = 'comum'
        if not username or not password:
            return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({"erro": "Nome de usuário já existe"}), 409
        novo_usuario = User(username=username, role=role)
        novo_usuario.set_password(password)
        db.session.add(novo_usuario)
        db.session.commit()
        logger.info(f"--- [LOG] Usuário '{username}' criado com role '{role}' ---")
        return jsonify(novo_usuario.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /register (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@auth_bp.route('/login', methods=['POST', 'OPTIONS'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    logger.info("Rota /login (POST) acessada")
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        dados = request.json
        username = dados.get('username')
        password = dados.get('password')
        if not username or not password:
            return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            identity = str(user.id)
            # Claim `modulos` é consumido pelo SSO do backend admin (null = todos).
            additional_claims = {
                "username": user.username,
                "role": user.role,
                "modulos": user.modulos_permitidos,
            }
            access_token = create_access_token(identity=identity, additional_claims=additional_claims)
            logger.info(f"Login bem-sucedido para '{username}'")
            return jsonify(access_token=access_token, user=user.to_dict())
        else:
            logger.warning(f"Falha no login para '{username}'")
            return jsonify({"erro": "Credenciais inválidas"}), 401
    except Exception as e:
        logger.exception("Erro em /login")
        return jsonify({"erro": str(e)}), 500


@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def me():
    """Dados frescos do usuário logado (o frontend refresca o storage no boot)."""
    try:
        user = get_current_user()
        if not user:
            # 401 → fetchWithAuth limpa o storage e volta ao login.
            return jsonify({"erro": "Usuário não existe mais"}), 401
        return jsonify(user.to_dict()), 200
    except Exception as e:
        logger.exception("Erro em GET /me")
        return jsonify({"erro": "Erro ao obter usuário", "detalhe": str(e)}), 500


@auth_bp.route('/', methods=['GET'])
def home():
    logger.info("--- [LOG] Rota / (home) acessada ---")
    return jsonify({"message": "Backend rodando com sucesso!", "status": "OK"}), 200
