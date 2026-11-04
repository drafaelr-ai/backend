"""Notificação push para o aplicativo nativo, via FCM HTTP v1.

Por que existe: o APK já registrava no FCM e mandava o token para
`POST /notificacoes/dispositivos` desde sempre — mas esse endpoint nunca
existiu no backend, e nada aqui jamais enviou um push. O sino dentro do app
e o Telegram eram os únicos canais que funcionavam de verdade.

Autenticação sem SDK: o FCM HTTP v1 quer um access token OAuth2 de service
account. Em vez de trazer `google-auth` (e com ele rsa/pyasn1/cachetools), o
JWT RS256 é assinado aqui com PyJWT + cryptography, que o Flask-JWT-Extended
já traz. Mesmo espírito do telegram_service, que fala HTTP cru.

Kill switch: sem FCM_CREDENTIALS_JSON no ambiente tudo vira no-op e
`configurado()` responde False. Deployar isto sem configurar nada não muda
comportamento nenhum.

REGRA, igual à do Telegram: envio é SEMPRE best-effort em thread daemon —
jamais atrasa nem derruba a transação que gerou a notificação.
"""
import json
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 8          # segundos, só nas threads de envio
_ESCOPO = 'https://www.googleapis.com/auth/firebase.messaging'
_URL_TOKEN = 'https://oauth2.googleapis.com/token'

# Access token do FCM, em cache até perto do vencimento. O lock evita que
# dois workers do gunicorn peçam token ao mesmo tempo no primeiro envio.
_cache_token = {'valor': None, 'expira_em': 0.0}
_lock_token = threading.Lock()

# Respostas do FCM que significam "este token morreu": o aparelho
# desinstalou, limpou os dados ou o token foi reemitido. A linha sai do
# banco para a tabela não virar um cemitério.
_MORTOS = ('UNREGISTERED', 'INVALID_ARGUMENT', 'SENDER_ID_MISMATCH')


def _credenciais():
    """Service account como dict, ou None se não houver.

    Aceita o JSON inteiro na variável (jeito do `fly secrets set`) ou um
    caminho de arquivo, para quem preferir montar como volume.
    """
    bruto = (os.environ.get('FCM_CREDENTIALS_JSON') or '').strip()
    if not bruto:
        return None
    try:
        if bruto.startswith('{'):
            return json.loads(bruto)
        if os.path.isfile(bruto):
            with open(bruto, encoding='utf-8') as f:
                return json.load(f)
        logger.error("push: FCM_CREDENTIALS_JSON não é JSON nem caminho de arquivo existente")
        return None
    except Exception as e:
        logger.error("push: FCM_CREDENTIALS_JSON inválido — %s", e)
        return None


def configurado():
    """True quando dá para enviar. Usado pelas rotas e pelos testes."""
    cred = _credenciais()
    return bool(cred and cred.get('project_id') and cred.get('private_key')
                and cred.get('client_email'))


def _access_token():
    """Access token do FCM, trocando o JWT da service account. None em erro."""
    agora = time.time()
    with _lock_token:
        if _cache_token['valor'] and agora < _cache_token['expira_em']:
            return _cache_token['valor']

        cred = _credenciais()
        if not cred:
            return None
        try:
            import jwt as _pyjwt
            emitido = int(agora)
            assercao = _pyjwt.encode(
                {
                    'iss': cred['client_email'],
                    'scope': _ESCOPO,
                    'aud': _URL_TOKEN,
                    'iat': emitido,
                    'exp': emitido + 3600,
                },
                cred['private_key'],
                algorithm='RS256',
            )
            r = requests.post(
                _URL_TOKEN,
                data={
                    'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                    'assertion': assercao,
                },
                timeout=_TIMEOUT,
            )
            if r.status_code != 200:
                logger.error("push: OAuth do FCM recusou (%s) — %s",
                             r.status_code, r.text[:200])
                return None
            dados = r.json()
            token = dados.get('access_token')
            if not token:
                logger.error("push: resposta de OAuth sem access_token")
                return None
            # 60s de folga para não usar um token que vence no voo.
            _cache_token['valor'] = token
            _cache_token['expira_em'] = agora + max(60, int(dados.get('expires_in', 3600))) - 60
            return token
        except Exception as e:
            logger.error("push: falha ao obter access token do FCM — %s", e)
            return None


def _modulo_e_caminho(tipo, obra_id=None):
    """Para onde o toque na notificação deve levar.

    O app lê `modulo` e `caminho` do data payload (ver
    frontend/src/pwa/nativePush.js). Casado por prefixo de propósito: um
    `tipo` novo cai no módulo certo sem mexer aqui.
    """
    t = (tipo or '')
    if t.startswith('solicitacao'):
        return 'solicitacoes', '/'
    if t.startswith('frota_'):
        return 'frota', '/'
    if t.startswith('boleto'):
        return 'admin', '/'
    if t.startswith('almoxarifado'):
        return 'almoxarifado', '/'
    if t.startswith(('ponto', 'funcionario', 'rh_')):
        return 'rh', '/'
    return 'obras', '/'


def _enviar_um(token_acesso, project_id, token_aparelho, titulo, corpo, dados):
    """Um POST para um aparelho. Devolve (ok, motivo_de_morte_ou_None)."""
    url = f'https://fcm.googleapis.com/v1/projects/{project_id}/messages:send'
    corpo_msg = {
        'message': {
            'token': token_aparelho,
            'notification': {'title': titulo, 'body': corpo or ''},
            # Tudo em string: o FCM recusa data payload com valor não-string.
            'data': {k: str(v) for k, v in (dados or {}).items() if v is not None},
            'android': {
                'priority': 'HIGH',
                # Mesmo id criado pelo app em nativePush.js. Sem casar, o
                # Android joga a notificação num canal padrão silencioso.
                'notification': {'channel_id': 'obraly_alertas'},
            },
            'apns': {'payload': {'aps': {'sound': 'default'}}},
        }
    }
    r = requests.post(
        url,
        headers={'Authorization': f'Bearer {token_acesso}',
                 'Content-Type': 'application/json'},
        json=corpo_msg,
        timeout=_TIMEOUT,
    )
    if r.status_code == 200:
        return True, None
    texto = r.text or ''
    for marca in _MORTOS:
        if marca in texto:
            return False, marca
    if r.status_code == 404:
        return False, 'UNREGISTERED'
    logger.warning("push: FCM recusou (%s) — %s", r.status_code, texto[:200])
    return False, None


def _despachar(app, usuario_id, titulo, mensagem, tipo, notificacao_id, obra_id):
    """Corpo da thread. Precisa do app context: abre sessão própria."""
    with app.app_context():
        from extensions import db
        from models.dispositivo_push import DispositivoPush
        try:
            aparelhos = DispositivoPush.query.filter_by(usuario_id=usuario_id).all()
            if not aparelhos:
                return

            token_acesso = _access_token()
            if not token_acesso:
                return
            project_id = (_credenciais() or {}).get('project_id')
            if not project_id:
                return

            modulo, caminho = _modulo_e_caminho(tipo, obra_id)
            dados = {
                'notificacao_id': notificacao_id,
                'tipo': tipo,
                'modulo': modulo,
                'caminho': caminho,
                'obra_id': obra_id,
            }

            mortos = []
            for ap in aparelhos:
                try:
                    ok, motivo = _enviar_um(token_acesso, project_id, ap.token,
                                            titulo, mensagem, dados)
                    if not ok and motivo:
                        mortos.append(ap.id)
                except Exception as e:
                    logger.warning("push: erro ao enviar para aparelho %s — %s", ap.id, e)

            if mortos:
                DispositivoPush.query.filter(DispositivoPush.id.in_(mortos)).delete(
                    synchronize_session=False)
                db.session.commit()
                logger.info("push: %d aparelho(s) sem registro removido(s)", len(mortos))
        except Exception as e:
            logger.warning("push: despacho falhou para usuário %s — %s", usuario_id, e)


def notificar_usuario(usuario_id, titulo, mensagem=None, tipo=None,
                      notificacao_id=None, obra_id=None):
    """Dispara o push em thread daemon. Não levanta, não bloqueia, não espera.

    Chamada de dentro de criar_notificacao, logo depois do commit do sino.
    """
    if not usuario_id or not configurado():
        return
    try:
        from flask import current_app
        app = current_app._get_current_object()
    except Exception:
        logger.debug("push: sem app context, nada enviado")
        return

    threading.Thread(
        target=_despachar,
        args=(app, usuario_id, titulo, mensagem, tipo, notificacao_id, obra_id),
        daemon=True,
    ).start()


def registrar_dispositivo(usuario_id, token, plataforma=None):
    """Cria ou reatribui o vínculo aparelho→usuário. Devolve o DispositivoPush.

    Reatribui em vez de duplicar: o FCM entrega o mesmo token quando outro
    usuário faz login no mesmo aparelho, e manter o vínculo antigo mandaria
    as notificações de um para o aparelho do outro.
    """
    from extensions import db
    from datetime import datetime
    from models.dispositivo_push import DispositivoPush

    token = (token or '').strip()
    if not token:
        raise ValueError('token vazio')

    existente = DispositivoPush.query.filter_by(token=token).first()
    if existente:
        existente.usuario_id = usuario_id
        existente.plataforma = plataforma or existente.plataforma
        existente.visto_em = datetime.utcnow()
        db.session.commit()
        return existente

    novo = DispositivoPush(usuario_id=usuario_id, token=token, plataforma=plataforma)
    db.session.add(novo)
    db.session.commit()
    return novo


def remover_dispositivo(usuario_id, token):
    """Desfaz o vínculo no logout. Só remove o que é do próprio usuário."""
    from extensions import db
    from models.dispositivo_push import DispositivoPush

    token = (token or '').strip()
    if not token:
        return 0
    n = DispositivoPush.query.filter_by(token=token, usuario_id=usuario_id).delete(
        synchronize_session=False)
    db.session.commit()
    return n
