import os
import secrets
import logging

import jwt as pyjwt
from flask import Blueprint, jsonify, request, make_response
from flask_jwt_extended import create_access_token, jwt_required

from extensions_admin import db, limiter
from models_admin import Usuario
from services_admin import get_current_user

logger = logging.getLogger(__name__)

auth_admin_bp = Blueprint('auth_admin', __name__)


@auth_admin_bp.route('/sso', methods=['POST', 'OPTIONS'])
@limiter.limit("10 per minute", methods=["POST"])
def sso():
    """Login único central: troca um token do backend MAIN por um token_admin.

    O frontend, já logado no main, envia `Authorization: Bearer <token_main>`.
    Validamos a assinatura com JWT_SECRET_KEY_MAIN e autorizamos se o usuário
    tem o módulo 'admin' liberado (claim `modulos`: null = todos) ou é master.
    Usuário patrimonial é casado por username (auto-criado como 'admin' com
    senha aleatória se não existir — a autorização vem do master no main).

    Limitação conhecida: o claim `modulos` é lido do token, não do banco main —
    revogar o módulo 'admin' de alguém só bloqueia o SSO quando o token main
    dele expirar (até 7 dias). O POST /login continua como fallback de emergência.
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({}), 200)
    try:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'erro': 'Token principal ausente'}), 401
        token_main = auth_header[len('Bearer '):].strip()

        secret_main = os.environ.get('JWT_SECRET_KEY_MAIN')
        if not secret_main:
            logger.error("SSO: JWT_SECRET_KEY_MAIN não configurada")
            return jsonify({'erro': 'SSO não configurado no servidor'}), 503

        try:
            claims = pyjwt.decode(token_main, secret_main, algorithms=['HS256'])
        except pyjwt.ExpiredSignatureError:
            return jsonify({'erro': 'Token principal expirado'}), 401
        except pyjwt.InvalidTokenError:
            return jsonify({'erro': 'Token principal inválido'}), 401

        username = (claims.get('username') or '').strip()
        if not username:
            return jsonify({'erro': 'Token principal sem username'}), 401

        role = claims.get('role')
        modulos = claims.get('modulos')  # null = todos
        if role != 'master' and modulos is not None and 'admin' not in modulos:
            return jsonify({'erro': 'Você não tem permissão para o módulo Administração.'}), 403

        usuario = Usuario.query.filter_by(username=username).first()
        if usuario and not usuario.ativo:
            return jsonify({'erro': 'Usuário inativo no módulo Administração'}), 403
        if not usuario:
            # role='operador' (escopo restrito ao próprio usuário, igual ao /register) —
            # o SSO só valida acesso ao MÓDULO "admin" no app principal, não deve
            # conceder automaticamente visibilidade irrestrita sobre TODOS os
            # imóveis/lançamentos/boletos. Promoção pra 'admin' é manual, feita
            # por um admin existente no painel de usuários.
            usuario = Usuario(username=username, nome=username, role='operador')
            usuario.set_password(secrets.token_urlsafe(32))
            db.session.add(usuario)
            db.session.commit()
            logger.info(f"SSO: usuário patrimonial '{username}' auto-criado (role=operador)")

        access_token = create_access_token(identity=str(usuario.id))
        logger.info(f"SSO: login de '{username}' via token principal")
        return jsonify({'access_token': access_token, 'user': usuario.to_dict()})

    except Exception:
        db.session.rollback()
        logger.exception("Erro no SSO")
        return jsonify({'erro': 'Erro interno no servidor'}), 500


@auth_admin_bp.route('/login', methods=['POST', 'OPTIONS'])
@limiter.limit("10 per minute", methods=["POST"])
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
@jwt_required()
def register():
    if request.method == 'OPTIONS':
        return make_response(jsonify({}), 200)
    try:
        user = get_current_user()
        if not user or user.role != 'admin':
            return jsonify({'erro': 'Acesso negado'}), 403

        dados = request.get_json(silent=True)
        if not dados:
            return jsonify({'erro': 'JSON inválido ou ausente'}), 400

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
