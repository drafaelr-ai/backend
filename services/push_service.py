import base64
import json
import logging
import os
from threading import Lock

from extensions import db
from models.push_device import PushDevice

logger = logging.getLogger(__name__)

_firebase_lock = Lock()
_firebase_app = None


def _credential_info():
    raw = (os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON') or '').strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.loads(base64.b64decode(raw).decode('utf-8'))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            logger.error('FIREBASE_SERVICE_ACCOUNT_JSON possui formato inválido')
            return None


def _get_firebase_app():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    info = _credential_info()
    if not info:
        return None

    with _firebase_lock:
        if _firebase_app is not None:
            return _firebase_app
        try:
            import firebase_admin
            from firebase_admin import credentials

            try:
                _firebase_app = firebase_admin.get_app('obraly')
            except ValueError:
                _firebase_app = firebase_admin.initialize_app(
                    credentials.Certificate(info),
                    name='obraly',
                )
            return _firebase_app
        except Exception:
            logger.exception('Falha ao inicializar o Firebase Admin')
            return None


def _destino_notificacao(notificacao):
    tipo = (notificacao.tipo or '').lower()
    item_type = (notificacao.item_type or '').lower()
    obra_id = notificacao.obra_id

    if 'solicitacao' in tipo or item_type == 'solicitacao':
        return 'solicitacoes', '/'
    if item_type in {'planejamento', 'planejamento_atividade'}:
        caminho = f'/?obra={obra_id}&page=planejamento' if obra_id else '/'
        return 'planejamento', caminho
    if (
        item_type in {'lancamento', 'orcamento', 'pagamento', 'boleto'}
        or tipo.startswith(('pagamento_', 'orcamento_', 'boleto_'))
    ):
        caminho = f'/?obra={obra_id}&page=financeiro' if obra_id else '/'
        return 'financeiro', caminho
    caminho = f'/?obra={obra_id}' if obra_id else '/'
    return 'obras', caminho


def enviar_push_usuario(notificacao):
    """Envia o espelho nativo da notificação in-app; falhas não anulam o aviso."""
    app = _get_firebase_app()
    if app is None:
        logger.debug('Push nativo desativado: credencial Firebase ausente')
        return {'enviadas': 0, 'falhas': 0, 'desativado': True}

    devices = PushDevice.query.filter_by(
        user_id=notificacao.usuario_destino_id,
        ativo=True,
    ).all()
    if not devices:
        return {'enviadas': 0, 'falhas': 0}

    from firebase_admin import messaging

    modulo, caminho = _destino_notificacao(notificacao)
    data = {
        'notificacao_id': str(notificacao.id),
        'tipo': str(notificacao.tipo or ''),
        'obra_id': str(notificacao.obra_id or ''),
        'item_id': str(notificacao.item_id or ''),
        'item_type': str(notificacao.item_type or ''),
        'modulo': modulo,
        'caminho': caminho,
    }
    message = messaging.MulticastMessage(
        tokens=[device.token for device in devices],
        notification=messaging.Notification(
            title=notificacao.titulo,
            body=notificacao.mensagem or 'Você tem uma nova atualização no Obraly.',
        ),
        data=data,
        android=messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                channel_id='obraly_alertas',
                sound='default',
                tag=f'obraly-{notificacao.id}',
            ),
        ),
    )

    try:
        response = messaging.send_each_for_multicast(message, app=app)
    except Exception:
        logger.exception(
            'Falha ao enviar push para usuário %s',
            notificacao.usuario_destino_id,
        )
        return {'enviadas': 0, 'falhas': len(devices)}

    changed = False
    for device, result in zip(devices, response.responses):
        if result.success:
            continue
        error_name = type(result.exception).__name__.lower()
        error_text = str(result.exception).lower()
        if 'unregistered' in error_name or 'not registered' in error_text:
            device.ativo = False
            changed = True
    if changed:
        db.session.commit()

    logger.info(
        'Push nativo: %s enviada(s), %s falha(s), usuário %s',
        response.success_count,
        response.failure_count,
        notificacao.usuario_destino_id,
    )
    return {
        'enviadas': response.success_count,
        'falhas': response.failure_count,
    }
