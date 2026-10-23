"""Entrada pública e enxuta dos callbacks do e-Frotas/SERPRO."""
import hmac
import logging
import os

from flask import Blueprint, jsonify, request

from extensions import limiter
from services.efrotas_import_service import processar_evento_efrotas

logger = logging.getLogger(__name__)

efrotas_bp = Blueprint('efrotas', __name__, url_prefix='/integracoes/efrotas')


def _configuracao_webhook():
    return (
        (os.environ.get('EFROTAS_WEBHOOK_TOKEN') or '').strip(),
        (os.environ.get('EFROTAS_WEBHOOK_HEADER') or 'X-Obraly-Webhook-Token').strip(),
    )


@efrotas_bp.before_request
def _autenticar_webhook():
    if request.method == 'OPTIONS':
        return None
    segredo, header = _configuracao_webhook()
    if not segredo:
        logger.error('e-Frotas: EFROTAS_WEBHOOK_TOKEN não configurado.')
        return jsonify({'erro': 'Webhook e-Frotas não configurado.'}), 503
    recebido = request.headers.get(header, '')
    if not recebido or not hmac.compare_digest(recebido, segredo):
        return jsonify({'erro': 'Credencial de integração inválida.'}), 401
    if not request.is_json:
        return jsonify({'erro': 'Content-Type application/json é obrigatório.'}), 415
    return None


@efrotas_bp.route('/webhook', methods=['POST'])
@limiter.limit('120 per minute')
def receber_evento():
    """Persiste e responde rapidamente; o mesmo rastreamento é idempotente."""
    try:
        resultado = processar_evento_efrotas(
            # A notificação alcança o condutor atualmente vinculado, mas a
            # responsabilidade não é preenchida automaticamente: o callback
            # pode chegar depois de uma troca de motorista.
            request.get_json(silent=True), notificar=True, atribuir_condutor=False,
        )
        situacao = resultado.get('situacao')
        status_http = 201 if situacao == 'importada' else 200
        return jsonify({'ok': True, **resultado}), status_http
    except Exception:
        logger.exception('e-Frotas: falha ao processar webhook')
        return jsonify({'erro': 'Erro interno ao processar evento e-Frotas.'}), 500
