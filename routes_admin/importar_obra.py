import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from extensions_admin import db
from models_admin import Imovel
from services_admin import get_current_user

logger = logging.getLogger(__name__)

importar_obra_bp = Blueprint('importar_obra_admin', __name__)


@importar_obra_bp.route('/importar-obra', methods=['POST'])
@jwt_required()
def importar_obra():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401

    try:
        dados = request.get_json(silent=True)

        obra_id = dados.get('obra_id')
        if obra_id:
            existente = Imovel.query.filter_by(obra_id_origem=obra_id).first()
            if existente:
                return jsonify({
                    'erro': 'Esta obra já foi importada',
                    'imovel_id': existente.id,
                    'imovel_nome': existente.nome
                }), 400

        imovel = Imovel(
            usuario_id=user.id,
            nome=dados.get('nome', 'Imóvel importado'),
            tipo=dados.get('tipo', 'apartamento'),
            endereco=dados.get('endereco'),
            cidade=dados.get('cidade'),
            estado=dados.get('estado'),
            cep=dados.get('cep'),
            status='proprio',
            valor_mercado=float(dados.get('valor_mercado', 0)),
            obra_id_origem=obra_id,
            custo_construcao=float(dados.get('custo_total', 0)),
            observacoes=f"Importado do módulo Obras em {datetime.now().strftime('%d/%m/%Y')}"
        )

        db.session.add(imovel)
        db.session.commit()

        logger.info(f"Obra importada como imóvel: {imovel.nome} (obra_id: {obra_id})")

        return jsonify({
            'message': 'Obra importada com sucesso',
            'imovel': imovel.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao importar obra")
        return jsonify({'erro': str(e)}), 500
