import logging
from functools import wraps
from flask import request, make_response, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required, get_jwt
from extensions import db
from models.user import User

logger = logging.getLogger(__name__)


def get_current_user():
    user_id_str = get_jwt_identity()
    if not user_id_str:
        return None
    return db.session.get(User, int(user_id_str))


MODULOS_VALIDOS = ('obras', 'admin', 'rh', 'frota', 'solicitacoes', 'almoxarifado')


def user_tem_modulo(user, modulo):
    """Acesso por módulo: APENAS master ignora a lista; modulos_permitidos
    None = todos (default de quem nunca foi configurado)."""
    if not user:
        return False
    if user.role == 'master':
        return True
    if user.modulos_permitidos is None:
        return True
    return modulo in user.modulos_permitidos


def user_has_access_to_obra(user, obra_id):
    if not user:
        return False
    if user.role in ('master', 'administrador'):
        return True
    obra_ids_permitidas = [obra.id for obra in user.obras_permitidas]
    return obra_id in obra_ids_permitidas


def check_permission(roles):
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            if request.method == 'OPTIONS':
                return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
            claims = get_jwt()
            user_role = claims.get('role')
            if user_role not in roles:
                return jsonify({"erro": "Acesso negado: permissão insuficiente."}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator
