import logging
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models.superlink import Superlink

logger = logging.getLogger(__name__)

superlink_bp = Blueprint('superlink', __name__, url_prefix='/superlink')


def _gerar_token():
    return secrets.token_urlsafe(24)


@superlink_bp.route('', methods=['POST'])
@jwt_required()
def criar_superlink():
    try:
        user_id = get_jwt_identity()
        data = request.get_json() or {}

        titulo = (data.get('titulo') or '').strip()
        itens = data.get('itens', [])

        if not titulo:
            return jsonify({'erro': 'titulo obrigatório'}), 400
        if not itens or not isinstance(itens, list):
            return jsonify({'erro': 'itens deve ser lista não vazia'}), 400

        for item in itens:
            descricao = (item.get('descricao') or '').strip()
            valor = item.get('valor')
            forma = (item.get('forma') or '').strip()
            if not descricao or valor is None or not forma:
                return jsonify({'erro': 'cada item precisa de descricao, valor e forma'}), 400
            if forma == 'pix' and not (item.get('pix_chave') or '').strip():
                return jsonify({'erro': f'item "{descricao}": forma=pix exige pix_chave'}), 400
            if forma == 'boleto' and not (item.get('codigo_barras') or '').strip():
                return jsonify({'erro': f'item "{descricao}": forma=boleto exige codigo_barras'}), 400

        valor_total = sum(float(i['valor']) for i in itens)

        for _ in range(5):
            token = _gerar_token()
            if not Superlink.query.filter_by(token=token).first():
                break

        agora = datetime.utcnow()
        sl = Superlink(
            token=token,
            grupo_id=int(user_id),
            titulo=titulo,
            itens=itens,
            valor_total=valor_total,
            criado_em=agora,
            expira_em=agora + timedelta(days=7),
        )
        db.session.add(sl)
        db.session.commit()

        return jsonify({'token': token, 'url': f'https://obraly.uk/pagar/{token}'}), 201

    except Exception as e:
        logger.exception("Erro em POST /superlink")
        return jsonify({'erro': 'Erro ao criar superlink', 'detalhe': str(e)}), 500


@superlink_bp.route('/<token>', methods=['GET'])
def obter_superlink(token):
    try:
        sl = Superlink.query.filter_by(token=token).first()
        if not sl:
            return jsonify({'erro': 'Link não encontrado'}), 404
        if sl.is_expirado():
            return jsonify({'erro': 'Link expirado'}), 410
        return jsonify(sl.to_dict_publico()), 200

    except Exception as e:
        logger.exception("Erro em GET /superlink/<token>")
        return jsonify({'erro': 'Erro ao buscar superlink', 'detalhe': str(e)}), 500
