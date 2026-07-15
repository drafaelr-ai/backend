import logging
from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import func, extract

from extensions_admin import db
from models_admin import Imovel, Lancamento, Categoria
from services_admin import get_current_user

logger = logging.getLogger(__name__)

dashboard_admin_bp = Blueprint('dashboard_admin', __name__)


@dashboard_admin_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Nao autorizado'}), 401
    try:
        mes = request.args.get('mes', type=int, default=date.today().month)
        ano = request.args.get('ano', type=int, default=date.today().year)

        if user.role == 'admin':
            imoveis_ids = [i.id for i in Imovel.query.filter_by(ativo=True).all()]
        else:
            imoveis_ids = [i.id for i in Imovel.query.filter_by(usuario_id=user.id, ativo=True).all()]

        total_imoveis = len(imoveis_ids)

        despesas_mes = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).scalar() or 0

        receitas_mes = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'receita',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).scalar() or 0

        pendentes = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.status == 'pendente',
            Lancamento.tipo == 'despesa'
        ).scalar() or 0

        despesas_por_categoria = db.session.query(
            Categoria.nome,
            Categoria.icone,
            Categoria.cor,
            func.sum(Lancamento.valor).label('total')
        ).join(Lancamento).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).group_by(Categoria.id).order_by(func.sum(Lancamento.valor).desc()).all()

        despesas_por_imovel = db.session.query(
            Imovel.nome,
            func.sum(Lancamento.valor).label('total')
        ).join(Lancamento).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).group_by(Imovel.id).order_by(func.sum(Lancamento.valor).desc()).all()

        ultimos_lancamentos = Lancamento.query.filter(
            Lancamento.imovel_id.in_(imoveis_ids)
        ).order_by(Lancamento.created_at.desc()).limit(10).all()

        hoje = date.today()
        data_limite = hoje + timedelta(days=7)

        lancamentos_alerta = Lancamento.query.filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.status == 'pendente',
            Lancamento.data_vencimento.isnot(None),
            Lancamento.data_vencimento <= data_limite
        ).order_by(Lancamento.data_vencimento.asc()).all()

        alertas_vencidos = []
        alertas_a_vencer = []

        for lanc in lancamentos_alerta:
            lanc_dict = lanc.to_dict()
            dias = (lanc.data_vencimento - hoje).days
            lanc_dict['dias_para_vencer'] = dias
            if dias < 0:
                lanc_dict['status_alerta'] = 'vencido'
                alertas_vencidos.append(lanc_dict)
            else:
                lanc_dict['status_alerta'] = 'a_vencer'
                alertas_a_vencer.append(lanc_dict)

        return jsonify({
            'periodo': {'mes': mes, 'ano': ano},
            'resumo': {
                'total_imoveis': total_imoveis,
                'despesas_mes': float(despesas_mes),
                'receitas_mes': float(receitas_mes),
                'saldo_mes': float(receitas_mes - despesas_mes),
                'pendentes': float(pendentes)
            },
            'alertas': {
                'vencidos': alertas_vencidos,
                'a_vencer': alertas_a_vencer,
                'total_vencido': sum(l['valor'] for l in alertas_vencidos),
                'total_a_vencer': sum(l['valor'] for l in alertas_a_vencer)
            },
            'despesas_por_categoria': [
                {'nome': d.nome, 'icone': d.icone, 'cor': d.cor, 'total': float(d.total)}
                for d in despesas_por_categoria
            ],
            'despesas_por_imovel': [
                {'nome': d.nome, 'total': float(d.total)}
                for d in despesas_por_imovel
            ],
            'ultimos_lancamentos': [l.to_dict() for l in ultimos_lancamentos]
        })

    except Exception as e:
        logger.exception("Erro no dashboard")
        return jsonify({'erro': "Erro interno no servidor"}), 500
