import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from models_admin import Categoria

logger = logging.getLogger(__name__)

categorias_admin_bp = Blueprint('categorias_admin', __name__)


@categorias_admin_bp.route('/categorias', methods=['GET'])
@jwt_required()
def listar_categorias():
    tipo = request.args.get('tipo')  # despesa, receita ou None (todas)
    query = Categoria.query.filter_by(ativo=True)
    if tipo:
        query = query.filter_by(tipo=tipo)
    categorias = query.order_by(Categoria.tipo, Categoria.ordem).all()
    return jsonify([c.to_dict() for c in categorias])
