import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from extensions_admin import db
from models_admin import Usuario
from services_admin import get_current_user

logger = logging.getLogger(__name__)

usuarios_admin_bp = Blueprint('usuarios_admin', __name__)


@usuarios_admin_bp.route('/usuarios', methods=['GET'])
@jwt_required()
def listar_usuarios():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    usuarios = Usuario.query.filter_by(ativo=True).order_by(Usuario.nome).all()
    return jsonify([u.to_dict() for u in usuarios])


@usuarios_admin_bp.route('/usuarios', methods=['POST'])
@jwt_required()
def criar_usuario():
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    try:
        dados = request.get_json(silent=True)

        username = dados.get('username', '').strip()
        password = dados.get('password', '')
        nome = dados.get('nome', '').strip()
        email = dados.get('email', '').strip() or None
        role = dados.get('role', 'operador')

        if not username or not password or not nome:
            return jsonify({'erro': 'Username, senha e nome são obrigatórios'}), 400

        if len(password) < 6:
            return jsonify({'erro': 'Senha deve ter pelo menos 6 caracteres'}), 400

        if role not in ['admin', 'operador']:
            return jsonify({'erro': 'Role inválido. Use: admin ou operador'}), 400

        if Usuario.query.filter_by(username=username).first():
            return jsonify({'erro': 'Username já está em uso'}), 400

        if email and Usuario.query.filter_by(email=email).first():
            return jsonify({'erro': 'Email já está em uso'}), 400

        usuario = Usuario(username=username, nome=nome, email=email, role=role)
        usuario.set_password(password)
        db.session.add(usuario)
        db.session.commit()
        logger.info(f"Usuário criado por {user.username}: {username} ({role})")

        return jsonify({'message': 'Usuário criado com sucesso', 'user': usuario.to_dict()}), 201

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao criar usuário")
        return jsonify({'erro': str(e)}), 500


@usuarios_admin_bp.route('/usuarios/<int:usuario_id>', methods=['PUT'])
@jwt_required()
def atualizar_usuario(usuario_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    usuario = Usuario.query.get_or_404(usuario_id)
    try:
        dados = request.get_json(silent=True)

        if dados.get('nome'):
            usuario.nome = dados['nome'].strip()

        if dados.get('email'):
            existing = Usuario.query.filter(
                Usuario.email == dados['email'], Usuario.id != usuario_id
            ).first()
            if existing:
                return jsonify({'erro': 'Email já está em uso'}), 400
            usuario.email = dados['email'].strip()

        if dados.get('role') and dados['role'] in ['admin', 'operador']:
            usuario.role = dados['role']

        if dados.get('password') and len(dados['password']) >= 6:
            usuario.set_password(dados['password'])

        if 'ativo' in dados:
            usuario.ativo = dados['ativo']

        db.session.commit()
        return jsonify({'message': 'Usuário atualizado com sucesso', 'user': usuario.to_dict()})

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao atualizar usuário")
        return jsonify({'erro': str(e)}), 500


@usuarios_admin_bp.route('/usuarios/<int:usuario_id>', methods=['DELETE'])
@jwt_required()
def deletar_usuario(usuario_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403

    if user.id == usuario_id:
        return jsonify({'erro': 'Você não pode desativar seu próprio usuário'}), 400

    usuario = Usuario.query.get_or_404(usuario_id)
    try:
        usuario.ativo = False
        db.session.commit()
        logger.info(f"Usuário desativado: {usuario.username}")
        return jsonify({'message': 'Usuário desativado com sucesso'})

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao desativar usuário")
        return jsonify({'erro': str(e)}), 500


@usuarios_admin_bp.route('/usuarios/<int:usuario_id>/reset-senha', methods=['POST'])
@jwt_required()
def reset_senha_usuario(usuario_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    usuario = Usuario.query.get_or_404(usuario_id)
    try:
        dados = request.get_json(silent=True)
        nova_senha = dados.get('nova_senha', '')

        if len(nova_senha) < 6:
            return jsonify({'erro': 'Nova senha deve ter pelo menos 6 caracteres'}), 400

        usuario.set_password(nova_senha)
        db.session.commit()
        logger.info(f"Senha resetada para usuário: {usuario.username}")
        return jsonify({'message': 'Senha alterada com sucesso'})

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao resetar senha")
        return jsonify({'erro': str(e)}), 500
