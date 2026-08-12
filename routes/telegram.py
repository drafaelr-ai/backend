"""Vínculo do usuário com o bot do Telegram — espelho das notificações do sino.

Fluxo (sem webhook):
  1. POST /telegram/vincular  → gera link_code e devolve o deep-link t.me
  2. usuário abre o link e dá Start no bot (envia '/start <code>')
  3. POST /telegram/confirmar → acha o código no getUpdates, grava o chat_id
Sem TELEGRAM_BOT_TOKEN configurado, /status devolve configurado=false e a UI
não mostra nada (kill switch).
Erros de validação são 400 — nunca 422 (fetchWithAuth desloga em 401/422).
"""
import logging
import secrets
from datetime import datetime

from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models.telegram_vinculo import TelegramVinculo
from services import get_current_user
from services import telegram_service

logger = logging.getLogger(__name__)

telegram_bp = Blueprint('telegram', __name__, url_prefix='/telegram')


def _vinculo(user_id):
    return TelegramVinculo.query.filter_by(user_id=user_id).first()


@telegram_bp.route('/status', methods=['GET'])
@jwt_required()
def status():
    if not telegram_service.configurado():
        return jsonify({'configurado': False, 'vinculado': False}), 200
    user = get_current_user()
    v = _vinculo(user.id)
    return jsonify({
        'configurado': True,
        'bot': telegram_service.bot_username(),
        'vinculado': bool(v and v.chat_id),
        'chat_nome': v.chat_nome if v and v.chat_id else None,
    }), 200


@telegram_bp.route('/vincular', methods=['POST'])
@jwt_required()
def vincular():
    if not telegram_service.configurado():
        return jsonify({'erro': 'Bot do Telegram não configurado no servidor.'}), 400
    bot = telegram_service.bot_username()
    if not bot:
        return jsonify({'erro': 'Bot do Telegram inacessível no momento. Tente de novo.'}), 400

    user = get_current_user()
    code = secrets.token_urlsafe(9)  # cabe no start= (limite 64 chars, [A-Za-z0-9_-])
    try:
        v = _vinculo(user.id)
        if not v:
            v = TelegramVinculo(user_id=user.id)
            db.session.add(v)
        v.link_code = code
        v.atualizado_em = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Telegram: erro ao gerar código de vínculo: %s", e)
        return jsonify({'erro': 'Erro interno ao gerar o código de vínculo.'}), 500

    return jsonify({'link': f'https://t.me/{bot}?start={code}', 'bot': bot}), 200


@telegram_bp.route('/confirmar', methods=['POST'])
@jwt_required()
def confirmar():
    if not telegram_service.configurado():
        return jsonify({'erro': 'Bot do Telegram não configurado no servidor.'}), 400
    user = get_current_user()
    v = _vinculo(user.id)
    if not v or not v.link_code:
        return jsonify({'erro': 'Gere o link de vínculo primeiro.'}), 400

    achado = telegram_service.buscar_start_code(v.link_code)
    if not achado:
        return jsonify({'erro': 'Ainda não recebi seu Start no Telegram. '
                                'Abra o link do bot, toque em Iniciar e confirme de novo.'}), 400

    chat_id, nome = achado
    try:
        v.chat_id = chat_id
        v.chat_nome = nome
        v.link_code = None
        v.atualizado_em = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Telegram: erro ao salvar vínculo: %s", e)
        return jsonify({'erro': 'Erro interno ao salvar o vínculo.'}), 500

    telegram_service.enviar_sync(
        chat_id,
        f"✅ Telegram conectado ao Obraly, {user.username}! "
        "A partir de agora suas notificações também chegam por aqui.",
    )
    return jsonify({'vinculado': True, 'chat_nome': nome}), 200


@telegram_bp.route('/vincular', methods=['DELETE'])
@jwt_required()
def desvincular():
    user = get_current_user()
    v = _vinculo(user.id)
    if not v:
        return jsonify({'mensagem': 'Nada a desvincular.'}), 200
    try:
        db.session.delete(v)
        db.session.commit()
        return jsonify({'mensagem': 'Telegram desvinculado.'}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Telegram: erro ao desvincular: %s", e)
        return jsonify({'erro': 'Erro interno ao desvincular.'}), 500
