"""Blueprint /home — agregados para a tela inicial (seletor de módulos) e
para a home do módulo Obras. Todas as rotas exigem JWT.

/home/alertas junta pendências de pagamento (vencidas ou vencendo em breve)
das fontes do banco MAIN (módulo Obras) e do banco ADMIN (patrimônio, leitura
read-only via admin_read_service), respeitando os módulos permitidos do
usuário e o scoping por obra. Erros de validação sempre 400, nunca 422.
"""
import logging
from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from calendar import monthrange

from extensions import db
from models.obra import Obra
from models.lancamento import Lancamento
from models.boleto import Boleto
from models.parcela_individual import ParcelaIndividual
from models.pagamento_parcelado import PagamentoParcelado
from models.pagamento_futuro import PagamentoFuturo
from models.pagamento_servico import PagamentoServico
from models.servico import Servico
from services import admin_read_service
from services import get_current_user, user_tem_modulo

logger = logging.getLogger(__name__)

home_bp = Blueprint('home', __name__, url_prefix='/home')

DIAS_A_VENCER_DEFAULT = 3


def _obras_visiveis(user):
    """Map {id: nome} das obras que o usuário enxerga (não arquivadas)."""
    query = Obra.query.filter(Obra.arquivada.isnot(True))
    if user.role not in ('master', 'administrador'):
        ids = [o.id for o in user.obras_permitidas]
        query = query.filter(Obra.id.in_(ids))
    return {o.id: o.nome for o in query.all()}


def _situacao(venc, hoje):
    if venc < hoje:
        return 'vencido'
    if venc == hoje:
        return 'vence_hoje'
    return 'a_vencer'


def _item(modulo, origem, descricao, valor, venc, hoje, origem_id=None):
    return {
        'modulo': modulo,
        'origem': origem,
        'origem_id': origem_id,
        'descricao': descricao,
        'valor': round(float(valor or 0), 2),
        'data_vencimento': venc.isoformat(),
        'situacao': _situacao(venc, hoje),
        'dias': (venc - hoje).days,
    }


def _pendencias_obras(user, corte, hoje):
    obras = _obras_visiveis(user)
    if not obras:
        return []
    ids = list(obras.keys())
    itens = []

    # Lançamentos a pagar
    lancs = (Lancamento.query
             .filter(Lancamento.obra_id.in_(ids),
                     Lancamento.status == 'A Pagar',
                     Lancamento.data_vencimento.isnot(None),
                     Lancamento.data_vencimento <= corte)
             .all())
    for l in lancs:
        restante = max((l.valor_total or 0) - (l.valor_pago or 0), 0) or (l.valor_total or 0)
        desc = l.descricao + (f' — {l.fornecedor}' if l.fornecedor else '')
        itens.append(_item('obras', obras.get(l.obra_id), desc, restante,
                           l.data_vencimento, hoje, l.obra_id))

    # Parcelas de pagamento parcelado (inclui parcelas de boleto a vencer)
    parcelas = (db.session.query(ParcelaIndividual, PagamentoParcelado)
                .join(PagamentoParcelado,
                      ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id)
                .filter(PagamentoParcelado.obra_id.in_(ids),
                        ParcelaIndividual.status.in_(['Previsto', 'Pendente']),
                        ParcelaIndividual.data_vencimento <= corte)
                .all())
    for p, pp in parcelas:
        desc = f'{pp.descricao} — parcela {p.numero_parcela}/{pp.numero_parcelas}'
        itens.append(_item('obras', obras.get(pp.obra_id), desc, p.valor_parcela,
                           p.data_vencimento, hoje, pp.obra_id))

    # Boletos (Vencido explícito, ou Pendente com vencimento no corte)
    boletos = (Boleto.query
               .filter(Boleto.obra_id.in_(ids),
                       Boleto.status != 'Pago',
                       Boleto.data_vencimento <= corte)
               .all())
    for b in boletos:
        desc = 'Boleto ' + (b.descricao or b.beneficiario or 's/ descrição')
        itens.append(_item('obras', obras.get(b.obra_id), desc, b.valor,
                           b.data_vencimento, hoje, b.obra_id))

    # Pagamentos futuros (cronograma)
    futuros = (PagamentoFuturo.query
               .filter(PagamentoFuturo.obra_id.in_(ids),
                       PagamentoFuturo.status == 'Previsto',
                       PagamentoFuturo.data_vencimento <= corte)
               .all())
    for f in futuros:
        desc = f.descricao + (f' — {f.fornecedor}' if f.fornecedor else '')
        itens.append(_item('obras', obras.get(f.obra_id), desc, f.valor,
                           f.data_vencimento, hoje, f.obra_id))

    return itens


@home_bp.route('/alertas', methods=['GET'])
@jwt_required()
def alertas():
    """Pendências vencidas/a vencer dos módulos que o usuário acessa.

    ?dias=N controla a janela de "a vencer" (default 3, máx 60)."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 401
        try:
            dias = min(max(int(request.args.get('dias', DIAS_A_VENCER_DEFAULT)), 0), 60)
        except (TypeError, ValueError):
            return jsonify({"erro": "dias inválido"}), 400

        hoje = date.today()
        corte = hoje + timedelta(days=dias)
        pendencias = []
        aviso_admin = None

        if user_tem_modulo(user, 'obras'):
            pendencias.extend(_pendencias_obras(user, corte, hoje))

        if user_tem_modulo(user, 'admin'):
            itens_admin, aviso_admin = admin_read_service.listar_pendencias(corte)
            for it in itens_admin:
                venc = it['data_vencimento']
                pendencias.append(_item('admin', it['imovel_nome'], it['descricao'],
                                        it['valor'], venc, hoje, it['imovel_id']))

        # vencidos primeiro (mais antigos no topo), depois por vencimento
        pendencias.sort(key=lambda x: (x['situacao'] != 'vencido', x['data_vencimento']))

        def _resumo(mod):
            do_mod = [p for p in pendencias if p['modulo'] == mod]
            vencidos = [p for p in do_mod if p['situacao'] == 'vencido']
            return {
                'qtd': len(do_mod),
                'vencidos': len(vencidos),
                'valor_total': round(sum(p['valor'] for p in do_mod), 2),
                'valor_vencido': round(sum(p['valor'] for p in vencidos), 2),
            }

        return jsonify({
            'pendencias': pendencias[:30],
            'resumo': {'obras': _resumo('obras'), 'admin': _resumo('admin')},
            'aviso_admin': aviso_admin,
            'dias': dias,
        }), 200
    except Exception as e:
        logger.exception("Erro em GET /home/alertas")
        return jsonify({"erro": "Erro ao montar alertas", "detalhe": str(e)}), 500


def _classe_gasto(tipo):
    """Classifica o tipo do lançamento: 'mo', 'material' ou 'outros'.

    lancamento.tipo tem 5 valores em prod: Mão de Obra, Material, Despesa,
    Serviço, Equipamentos — Despesa/Serviço/Equipamentos contam nas saídas,
    mas não são MO nem material."""
    if tipo == 'Mão de Obra':
        return 'mo'
    if tipo == 'Material':
        return 'material'
    return 'outros'


@home_bp.route('/obras', methods=['GET'])
@jwt_required()
def home_obras():
    """Agregado da home do módulo Obras (?competencia=YYYY-MM, default mês atual).

    MO e material são TOTAIS ACUMULADOS da obra (pedido do usuário: "total
    gasto"); saídas e previsão a pagar são do mês. Fontes: lançamentos pagos,
    pagamentos de serviço e parcelas pagas (mesmas do /bi/historico-mensal).
    Previsão a pagar = tudo em aberto com vencimento até o fim do mês."""
    try:
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 401
        if not user_tem_modulo(user, 'obras'):
            return jsonify({"erro": "Acesso negado: você não tem permissão para o módulo Obras."}), 403

        competencia = request.args.get('competencia') or date.today().strftime('%Y-%m')
        try:
            ano, mes = int(competencia[:4]), int(competencia[5:7])
            inicio = date(ano, mes, 1)
            fim = date(ano, mes, monthrange(ano, mes)[1])
        except Exception:
            return jsonify({"erro": "competencia inválida (use YYYY-MM)"}), 400

        obras_map = _obras_visiveis(user)
        ids = list(obras_map.keys())
        hoje = date.today()

        por_obra = {oid: {'mo_total': 0.0, 'material_total': 0.0, 'vencidos_qtd': 0,
                          'vencidos_valor': 0.0} for oid in ids}
        mo_total = material_total = saidas_mes = 0.0

        def _acumula(obra_id, classe, valor, data_ref):
            nonlocal mo_total, material_total, saidas_mes
            if data_ref and inicio <= data_ref <= fim:
                saidas_mes += valor
            if classe == 'mo':
                mo_total += valor
                por_obra[obra_id]['mo_total'] += valor
            elif classe == 'material':
                material_total += valor
                por_obra[obra_id]['material_total'] += valor

        if ids:
            # Lançamentos pagos (todos; data_ref = data ou vencimento — regra do BI)
            lancs = (Lancamento.query
                     .filter(Lancamento.obra_id.in_(ids), Lancamento.status == 'Pago')
                     .all())
            for l in lancs:
                _acumula(l.obra_id, _classe_gasto(l.tipo),
                         l.valor_pago or l.valor_total or 0,
                         l.data or l.data_vencimento)

            # Pagamentos de serviço
            pagtos = (db.session.query(PagamentoServico, Servico.obra_id)
                      .join(Servico, PagamentoServico.servico_id == Servico.id)
                      .filter(Servico.obra_id.in_(ids),
                              PagamentoServico.valor_pago > 0)
                      .all())
            for ps, obra_id in pagtos:
                classe = 'mo' if ps.tipo_pagamento == 'mao_de_obra' else 'material'
                _acumula(obra_id, classe, ps.valor_pago or 0, ps.data)

            # Parcelas pagas (split pelo segmento do parcelamento)
            parcelas_pagas = (db.session.query(ParcelaIndividual, PagamentoParcelado)
                              .join(PagamentoParcelado,
                                    ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id)
                              .filter(PagamentoParcelado.obra_id.in_(ids),
                                      ParcelaIndividual.status == 'Pago')
                              .all())
            for p, pp in parcelas_pagas:
                classe = 'mo' if (pp.segmento or 'Material') == 'Mão de Obra' else 'material'
                _acumula(pp.obra_id, classe, p.valor_parcela or 0,
                         p.data_pagamento or p.data_vencimento)

        # Previsão a pagar: em aberto com vencimento até o fim do mês
        # (reusa as mesmas fontes do /home/alertas com corte = fim do mês)
        pendencias = _pendencias_obras(user, fim, hoje) if ids else []
        previsao_total = round(sum(p['valor'] for p in pendencias), 2)
        for p in pendencias:
            if p['situacao'] == 'vencido' and p['origem_id'] in por_obra:
                por_obra[p['origem_id']]['vencidos_qtd'] += 1
                por_obra[p['origem_id']]['vencidos_valor'] += p['valor']

        obras_out = []
        for oid, nome in obras_map.items():
            d = por_obra[oid]
            obras_out.append({
                'id': oid,
                'nome': nome,
                'mo_total': round(d['mo_total'], 2),
                'material_total': round(d['material_total'], 2),
                'vencidos_qtd': d['vencidos_qtd'],
                'vencidos_valor': round(d['vencidos_valor'], 2),
            })

        return jsonify({
            'competencia': competencia,
            'kpis': {
                'mo_total': round(mo_total, 2),
                'material_total': round(material_total, 2),
                'saidas_mes': round(saidas_mes, 2),
                'previsao_pagar': {'total': previsao_total, 'qtd': len(pendencias),
                                   'ate': fim.isoformat()},
            },
            'obras': obras_out,
        }), 200
    except Exception as e:
        logger.exception("Erro em GET /home/obras")
        return jsonify({"erro": "Erro ao montar home de obras", "detalhe": str(e)}), 500
