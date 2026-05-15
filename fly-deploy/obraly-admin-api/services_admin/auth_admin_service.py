import logging

from flask_jwt_extended import get_jwt_identity

from models_admin import Usuario

logger = logging.getLogger(__name__)


def get_current_user():
    """Retorna o usuário atual baseado no token JWT"""
    try:
        user_id = get_jwt_identity()
        if user_id:
            return Usuario.query.get(int(user_id))
    except Exception:
        pass
    return None
