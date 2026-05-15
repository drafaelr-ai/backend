import logging

from flask import Blueprint, jsonify, request, make_response
from flask_jwt_extended import create_access_token, jwt_required

from extensions_admin import db
from models_admin import Usuario
from services_admin import get_current_user

logger = logging.getLogger(__name__)

auth_admin_bp = Blueprint('auth_admin', __name__)


@auth_admin_bp.route('/login', methods=['POST', 'OPTIONS'])
def login():
    if request.method == 'OPTIONS':
        return make_response(jsonify({}), 200)
    try:
        dados = request.get_json(silent=True)
        if not dados:
            return jsonify({'erro': 'JSON inválido ou ausente'}), 400
        username = dados.get('username', '').strip()
        password = dados.get('password', '')

        if not username or not password:
            return jsonify({'erro': 'Usuário e senha são obrigatórios'}), 400

        usuario = Usuario.query.filter_by(username=username).first()

        if not usuario or not usuario.check_password(password):
            return jsonify({'erro': 'Usuário ou senha inválidos'}), 401

        if not usuario.ativo:
            return jsonify({'erro': 'Usuário inativo'}), 403

        access_token = create_access_token(identity=str(usuario.id))
        logger.info(f"Login: {username}")
        return jsonify({'access_token': access_token, 'user': usuario.to_dict()})

    except Exception as e:
        logger.exception("Erro no login")
        return jsonify({'erro': 'Erro interno no servidor'}), 500


@auth_admin_bp.route('/register', methods=['POST', 'OPTIONS'])
def register():
    if request.method == 'OPTIONS':
        return make_response(jsonify({}), 200)
    try:
        dados = request.get_json(silent=True)

        username = dados.get('username', '').strip()
        password = dados.get('password', '')
        nome = dados.get('nome', '').strip()
        email = dados.get('email', '').strip() or None

        if not username or not password or not nome:
            return jsonify({'erro': 'Username, senha e nome são obrigatórios'}), 400

        if len(password) < 6:
            return jsonify({'erro': 'Senha deve ter pelo menos 6 caracteres'}), 400

        if Usuario.query.filter_by(username=username).first():
            return jsonify({'erro': 'Username já está em uso'}), 400

        if email and Usuario.query.filter_by(email=email).first():
            return jsonify({'erro': 'Email já está em uso'}), 400

        usuario = Usuario(username=username, nome=nome, email=email, role='operador')
        usuario.set_password(password)
        db.session.add(usuario)
        db.session.commit()
        logger.info(f"Novo usuário registrado: {username}")

        return jsonify({'message': 'Usuário criado com sucesso', 'user': usuario.to_dict()}), 201

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro no registro")
        return jsonify({'erro': 'Erro interno no servidor'}), 500


@auth_admin_bp.route('/me', methods=['GET'])
@jwt_required()
def get_me():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Usuário não encontrado'}), 404
    return jsonify(user.to_dict())
