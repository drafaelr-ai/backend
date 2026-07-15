import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from extensions_admin import db
from models_admin import Imovel, Lancamento
from services_admin import get_current_user

logger = logging.getLogger(__name__)

imoveis_admin_bp = Blueprint('imoveis_admin', __name__)


@imoveis_admin_bp.route('/imoveis', methods=['GET'])
@jwt_required()
def listar_imoveis():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401

    if user.role == 'admin':
        imoveis = Imovel.query.filter_by(ativo=True).order_by(Imovel.nome).all()
    else:
        imoveis = Imovel.query.filter_by(usuario_id=user.id, ativo=True).order_by(Imovel.nome).all()

    resultado = []
    for imovel in imoveis:
        imovel_dict = imovel.to_dict()

        despesas = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id == imovel.id,
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado'
        ).scalar() or 0

        receitas = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id == imovel.id,
            Lancamento.tipo == 'receita',
            Lancamento.status != 'cancelado'
        ).scalar() or 0

        imovel_dict['total_despesas'] = float(despesas)
        imovel_dict['total_receitas'] = float(receitas)
        imovel_dict['saldo'] = float(receitas - despesas)
        imovel_dict['exibe_saldo'] = imovel.status != 'proprio'

        resultado.append(imovel_dict)

    return jsonify(resultado)


@imoveis_admin_bp.route('/imoveis', methods=['POST'])
@jwt_required()
def criar_imovel():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    try:
        dados = request.get_json(silent=True) or {}

        imovel = Imovel(
            usuario_id=user.id,
            nome=dados.get('nome'),
            tipo=dados.get('tipo', 'apartamento'),
            endereco=dados.get('endereco'),
            cidade=dados.get('cidade'),
            estado=dados.get('estado'),
            cep=dados.get('cep'),
            status=dados.get('status', 'proprio'),
            valor_aluguel=float(dados.get('valor_aluguel', 0)),
            valor_mercado=float(dados.get('valor_mercado', 0)),
            custo_construcao=float(dados.get('custo_construcao', 0)),
            observacoes=dados.get('observacoes')
        )

        db.session.add(imovel)
        db.session.commit()
        logger.info(f"Imovel criado: {imovel.nome} (user: {user.username})")
        return jsonify(imovel.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao criar imovel")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@imoveis_admin_bp.route('/imoveis/<int:imovel_id>', methods=['GET'])
@jwt_required()
def obter_imovel(imovel_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    imovel = Imovel.query.get_or_404(imovel_id)
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    return jsonify(imovel.to_dict())


@imoveis_admin_bp.route('/imoveis/<int:imovel_id>', methods=['PUT'])
@jwt_required()
def atualizar_imovel(imovel_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    imovel = Imovel.query.get_or_404(imovel_id)
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        dados = request.get_json(silent=True) or {}

        imovel.nome = dados.get('nome', imovel.nome)
        imovel.tipo = dados.get('tipo', imovel.tipo)
        imovel.endereco = dados.get('endereco', imovel.endereco)
        imovel.cidade = dados.get('cidade', imovel.cidade)
        imovel.estado = dados.get('estado', imovel.estado)
        imovel.cep = dados.get('cep', imovel.cep)
        imovel.status = dados.get('status', imovel.status)
        imovel.valor_aluguel = float(dados.get('valor_aluguel', imovel.valor_aluguel))
        imovel.valor_mercado = float(dados.get('valor_mercado', imovel.valor_mercado))
        imovel.custo_construcao = float(dados.get('custo_construcao', imovel.custo_construcao))
        imovel.observacoes = dados.get('observacoes', imovel.observacoes)

        db.session.commit()
        logger.info(f"Imovel atualizado: {imovel.nome}")
        return jsonify(imovel.to_dict())

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao atualizar imovel")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@imoveis_admin_bp.route('/imoveis/<int:imovel_id>', methods=['DELETE'])
@jwt_required()
def deletar_imovel(imovel_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    imovel = Imovel.query.get_or_404(imovel_id)
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        imovel.ativo = False
        db.session.commit()
        logger.info(f"Imovel desativado: {imovel.nome}")
        return jsonify({'message': 'Imovel removido com sucesso'})

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao deletar imovel")
        return jsonify({'erro': "Erro interno no servidor"}), 500
