import logging
from extensions import db
from models.notificacao import Notificacao
from models.user import User
from models.obra import Obra

logger = logging.getLogger(__name__)


def criar_notificacao(usuario_destino_id, tipo, titulo, mensagem=None, obra_id=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Cria uma nova notificação para um usuário"""
    try:
        notificacao = Notificacao(
            usuario_destino_id=usuario_destino_id,
            usuario_origem_id=usuario_origem_id,
            tipo=tipo,
            titulo=titulo,
            mensagem=mensagem,
            obra_id=obra_id,
            item_id=item_id,
            item_type=item_type
        )
        db.session.add(notificacao)
        db.session.commit()
        logger.info(f"--- [NOTIF] Notificação criada: {tipo} para usuário {usuario_destino_id} ---")
        return notificacao
    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] Falha ao criar notificação: {e} ---")
        return None


def notificar_masters(tipo, titulo, mensagem=None, obra_id=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Notifica todos os usuários master"""
    masters = User.query.filter_by(role='master').all()
    for master in masters:
        if master.id != usuario_origem_id:
            criar_notificacao(
                usuario_destino_id=master.id,
                tipo=tipo,
                titulo=titulo,
                mensagem=mensagem,
                obra_id=obra_id,
                item_id=item_id,
                item_type=item_type,
                usuario_origem_id=usuario_origem_id
            )


def notificar_operadores_obra(obra_id, tipo, titulo, mensagem=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Notifica todos os operadores (comum) com acesso a uma obra"""
    obra = Obra.query.get(obra_id)
    if not obra:
        return

    for user in obra.usuarios_permitidos:
        if user.role == 'comum' and user.id != usuario_origem_id:
            criar_notificacao(
                usuario_destino_id=user.id,
                tipo=tipo,
                titulo=titulo,
                mensagem=mensagem,
                obra_id=obra_id,
                item_id=item_id,
                item_type=item_type,
                usuario_origem_id=usuario_origem_id
            )


def notificar_administradores(tipo, titulo, mensagem=None, obra_id=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Notifica todos os usuários administradores"""
    admins = User.query.filter_by(role='administrador').all()
    for admin in admins:
        if admin.id != usuario_origem_id:
            criar_notificacao(
                usuario_destino_id=admin.id,
                tipo=tipo,
                titulo=titulo,
                mensagem=mensagem,
                obra_id=obra_id,
                item_id=item_id,
                item_type=item_type,
                usuario_origem_id=usuario_origem_id
            )
