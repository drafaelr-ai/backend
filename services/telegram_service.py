"""Envio de notificações pelo bot do Telegram (Bot API oficial, gratuita).

Sem webhook: o vínculo usa deep-link t.me/<bot>?start=<code> e a confirmação
lê o getUpdates do bot procurando o código — funciona atrás do Fly sem
configurar URL pública. O envio (sendMessage) é só saída HTTP.

Kill switch: sem TELEGRAM_BOT_TOKEN no ambiente, tudo aqui vira no-op e a
UI de vínculo nem aparece (GET /telegram/status → configurado: false).

REGRA: envio de notificação é SEMPRE best-effort em thread daemon — jamais
atrasa ou derruba a transação que gerou a notificação.
"""
import logging
import os
import threading

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 8  # segundos — só vale nas threads de envio e nas rotas de vínculo
_bot_username_cache = None

# Categorias de notificação que o usuário escolhe no sino (PUT /telegram/
# preferencias). telegram_vinculo.tipos = NULL → todas; [] → nenhuma.
CATEGORIAS = {
    'mencoes': 'Menções e comentários',
    'solicitacoes': 'Solicitações de compra',
    'boletos': 'Boletos e vencimentos',
    'financeiro': 'Pagamentos e orçamentos',
    'outros': 'Demais avisos',
}


def categoria_do_tipo(tipo):
    """Mapeia o `tipo` cru da notificação na categoria escolhível.

    Por prefixo de propósito: tipos novos (ex.: futuros solicitacao_*)
    caem na categoria certa sem precisar atualizar este mapa."""
    t = (tipo or '')
    if t in ('solicitacao_mencao', 'solicitacao_comentario'):
        return 'mencoes'
    if t.startswith('solicitacao'):
        return 'solicitacoes'
    if t.startswith('boleto'):
        return 'boletos'
    if t.startswith(('pagamento', 'orcamento', 'lancamento', 'parcela')):
        return 'financeiro'
    return 'outros'


def _token():
    return (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()


def configurado():
    return bool(_token())


def _api(metodo):
    return f'https://api.telegram.org/bot{_token()}/{metodo}'


def bot_username():
    """@ do bot (getMe) — cacheado no processo; None se não configurado/fora."""
    global _bot_username_cache
    if not configurado():
        return None
    if _bot_username_cache:
        return _bot_username_cache
    try:
        r = requests.get(_api('getMe'), timeout=_TIMEOUT)
        dados = r.json() if r.ok else {}
        _bot_username_cache = (dados.get('result') or {}).get('username')
    except Exception as e:
        logger.warning("Telegram: getMe falhou: %s", e)
    return _bot_username_cache


def enviar_sync(chat_id, texto):
    """Envio síncrono (usado na confirmação do vínculo). True se entregou."""
    if not configurado() or not chat_id:
        return False
    try:
        r = requests.post(_api('sendMessage'), json={
            'chat_id': chat_id,
            'text': texto[:4000],
        }, timeout=_TIMEOUT)
        return bool(r.ok and r.json().get('ok'))
    except Exception as e:
        logger.warning("Telegram: sendMessage falhou p/ chat %s: %s", chat_id, e)
        return False


def enviar_async(chat_id, texto):
    """Fire-and-forget em thread daemon — não usa DB nem app context."""
    if not configurado() or not chat_id:
        return
    threading.Thread(
        target=enviar_sync, args=(chat_id, texto), daemon=True,
    ).start()


def notificar_usuario(user_id, titulo, mensagem=None, tipo=None):
    """Espelha uma notificação do sino no Telegram, se o usuário vinculou
    e a categoria do tipo está nas preferências dele (NULL = todas).

    Chamado dentro de request context (após o commit da notificação) —
    a consulta ao vínculo é aqui; o HTTP sai em thread."""
    if not configurado() or not user_id:
        return
    from models.telegram_vinculo import TelegramVinculo
    vinculo = TelegramVinculo.query.filter_by(user_id=user_id).first()
    if not vinculo or not vinculo.chat_id:
        return
    if vinculo.tipos is not None and categoria_do_tipo(tipo) not in vinculo.tipos:
        return
    texto = titulo if not mensagem else f"{titulo}\n{mensagem}"
    enviar_async(vinculo.chat_id, texto)


def buscar_start_code(code):
    """Procura '/start <code>' nos updates recentes do bot.

    Retorna (chat_id, nome) ou None. Não confirma offset — updates ficam
    24h disponíveis, então confirmações concorrentes não se atropelam."""
    if not configurado() or not code:
        return None
    try:
        r = requests.get(_api('getUpdates'), params={'limit': 100}, timeout=_TIMEOUT)
        updates = (r.json() or {}).get('result', []) if r.ok else []
    except Exception as e:
        logger.warning("Telegram: getUpdates falhou: %s", e)
        return None
    esperado = f'/start {code}'
    # Do mais recente pro mais antigo — se o usuário deu Start 2x, vale o último.
    for upd in reversed(updates):
        msg = upd.get('message') or {}
        if (msg.get('text') or '').strip() != esperado:
            continue
        chat = msg.get('chat') or {}
        if not chat.get('id'):
            continue
        nome = (f"{chat.get('first_name') or ''} {chat.get('last_name') or ''}".strip()
                or chat.get('username') or 'Telegram')
        return str(chat['id']), nome[:120]
    return None
