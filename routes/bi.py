import logging
from datetime import date, timedelta
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from models.obra import Obra
from models.lancamento import Lancamento
from models.parcela_individual import ParcelaIndividual
from models.pagamento_parcelado import PagamentoParcelado
from models.pagamento_servico import PagamentoServico
from models.servico import Servico
from services import get_current_user

logger = logging.getLogger(__name__)

bi_bp = Blueprint('bi', __name__, url_prefix='/bi')


@bi_bp.route('/vencimentos', methods=['GET'])
@jwt_required()
def bi_vencimentos():
    """Retorna todos os vencimentos para o calendário do BI"""
    try:
        user = get_current_user()
        if user.role == 'master':
            obras_ids = [o.id for o in Obra.query.all()]
        else:
            obras_ids = [o.id for o in user.obras]

        hoje = date.today()

        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id.in_(obras_ids),
            ParcelaIndividual.status.in_(['Previsto', 'Pendente'])
        ).all()

        vencimentos = []
        for p in parcelas:
            pag = p.pagamento_parcelado
            obra = Obra.query.get(pag.obra_id) if pag else None
            vencimentos.append({
                'id': p.id,
                'tipo': 'parcela',
                'data': p.data_vencimento.isoformat() if p.data_vencimento else None,
                'valor': p.valor_parcela,
                'descricao': f"{pag.descricao} ({p.numero_parcela}/{pag.numero_parcelas})" if pag else f"Parcela {p.numero_parcela}",
                'obra_id': pag.obra_id if pag else None,
                'obra_nome': obra.nome if obra else 'N/A',
                'fornecedor': pag.fornecedor if pag else None,
                'status': 'vencido' if p.data_vencimento and p.data_vencimento < hoje else ('hoje' if p.data_vencimento == hoje else 'futuro'),
                'is_entrada': p.numero_parcela == 0
            })

        lancamentos_futuros = Lancamento.query.filter(
            Lancamento.obra_id.in_(obras_ids),
            Lancamento.status == 'A Pagar',
            Lancamento.data_vencimento != None
        ).all()

        for l in lancamentos_futuros:
            obra = Obra.query.get(l.obra_id)
            vencimentos.append({
                'id': l.id,
                'tipo': 'lancamento',
                'data': l.data_vencimento.isoformat() if l.data_vencimento else None,
                'valor': l.valor_total or 0,
                'descricao': l.descricao,
                'obra_id': l.obra_id,
                'obra_nome': obra.nome if obra else 'N/A',
                'fornecedor': l.fornecedor,
                'status': 'vencido' if l.data_vencimento and l.data_vencimento < hoje else ('hoje' if l.data_vencimento == hoje else 'futuro'),
                'is_entrada': False
            })

        vencimentos.sort(key=lambda x: x['data'] or '9999-99-99')

        vencidos = [v for v in vencimentos if v['status'] == 'vencido']
        hoje_list = [v for v in vencimentos if v['status'] == 'hoje']
        semana = [v for v in vencimentos if v['data'] and hoje <= date.fromisoformat(v['data']) <= hoje + timedelta(days=7)]
        mes = [v for v in vencimentos if v['data'] and hoje <= date.fromisoformat(v['data']) <= hoje + timedelta(days=30)]

        return jsonify({
            'vencimentos': vencimentos,
            'resumo': {
                'total': len(vencimentos),
                'vencidos': len(vencidos),
                'valor_vencido': sum(v['valor'] for v in vencidos),
                'hoje': len(hoje_list),
                'valor_hoje': sum(v['valor'] for v in hoje_list),
                'semana': len(semana),
                'valor_semana': sum(v['valor'] for v in semana),
                'mes': len(mes),
                'valor_mes': sum(v['valor'] for v in mes)
            }
        })
    except Exception as e:
        logger.exception(f"[BI] Erro ao buscar vencimentos: {e}")
        return jsonify({"erro": str(e)}), 500


@bi_bp.route('/historico-mensal', methods=['GET'])
@jwt_required()
def bi_historico_mensal():
    """Retorna histórico de pagamentos agrupado por mês"""
    try:
        user = get_current_user()
        if user.role == 'master':
            obras_ids = [o.id for o in Obra.query.all()]
        else:
            obras_ids = [o.id for o in user.obras]

        logger.info(f"[BI HISTORICO] Buscando para {len(obras_ids)} obras")

        lancamentos = Lancamento.query.filter(
            Lancamento.obra_id.in_(obras_ids),
            Lancamento.status == 'Pago'
        ).all()
        logger.info(f"[BI HISTORICO] Lançamentos pagos: {len(lancamentos)}")

        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id.in_(obras_ids),
            ParcelaIndividual.status == 'Pago',
            PagamentoParcelado.servico_id.isnot(None)
        ).all()
        logger.info(f"[BI HISTORICO] Parcelas pagas (apenas com servico): {len(parcelas)}")

        pagamentos_servico = PagamentoServico.query.join(Servico).filter(
            Servico.obra_id.in_(obras_ids),
            PagamentoServico.valor_pago > 0
        ).all()
        logger.info(f"[BI HISTORICO] Pagamentos de serviço: {len(pagamentos_servico)}")

        meses = {}
        for l in lancamentos:
            data_ref = l.data or l.data_vencimento
            if data_ref:
                mes_key = data_ref.strftime('%Y-%m')
                if mes_key not in meses:
                    meses[mes_key] = {'mes': mes_key, 'total': 0, 'qtd': 0, 'mao_obra': 0, 'material': 0}
                valor = l.valor_pago or l.valor_total or 0
                meses[mes_key]['total'] += valor
                meses[mes_key]['qtd'] += 1
                if l.tipo == 'Mão de Obra':
                    meses[mes_key]['mao_obra'] += valor
                else:
                    meses[mes_key]['material'] += valor

        for p in parcelas:
            data_ref = p.data_pagamento or p.data_vencimento
            if data_ref:
                mes_key = data_ref.strftime('%Y-%m')
                if mes_key not in meses:
                    meses[mes_key] = {'mes': mes_key, 'total': 0, 'qtd': 0, 'mao_obra': 0, 'material': 0}
                meses[mes_key]['total'] += p.valor_parcela or 0
                meses[mes_key]['qtd'] += 1

        for ps in pagamentos_servico:
            if ps.data_pagamento:
                mes_key = ps.data_pagamento.strftime('%Y-%m')
                if mes_key not in meses:
                    meses[mes_key] = {'mes': mes_key, 'total': 0, 'qtd': 0, 'mao_obra': 0, 'material': 0}
                meses[mes_key]['total'] += ps.valor_pago or 0
                meses[mes_key]['qtd'] += 1
                if ps.tipo_pagamento == 'mao_de_obra':
                    meses[mes_key]['mao_obra'] += ps.valor_pago or 0
                else:
                    meses[mes_key]['material'] += ps.valor_pago or 0

        logger.info(f"[BI HISTORICO] Total de meses encontrados: {len(meses)}")

        historico = sorted(meses.values(), key=lambda x: x['mes'])
        meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        for h in historico:
            ano, mes = h['mes'].split('-')
            h['mes_nome'] = f"{meses_nomes[int(mes)-1]}/{ano[2:]}"
            h['ano'] = int(ano)
            h['mes_num'] = int(mes)

        total_geral = sum(h['total'] for h in historico)
        media_mensal = total_geral / len(historico) if historico else 0
        melhor_mes = max(historico, key=lambda x: x['total']) if historico else None
        pior_mes = min(historico, key=lambda x: x['total']) if historico else None

        return jsonify({
            'historico': historico,
            'resumo': {
                'total_geral': total_geral,
                'media_mensal': media_mensal,
                'melhor_mes': melhor_mes,
                'pior_mes': pior_mes,
                'total_meses': len(historico)
            }
        })
    except Exception as e:
        logger.exception(f"[BI] Erro ao buscar histórico mensal: {e}")
        return jsonify({"erro": str(e)}), 500


@bi_bp.route('/projecao', methods=['GET'])
@jwt_required()
def bi_projecao():
    """Retorna projeção de gastos futuros baseado em parcelas e vencimentos"""
    try:
        user = get_current_user()
        if user.role == 'master':
            obras_ids = [o.id for o in Obra.query.all()]
        else:
            obras_ids = [o.id for o in user.obras]

        hoje = date.today()

        parcelas_futuras = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id.in_(obras_ids),
            ParcelaIndividual.status.in_(['Previsto', 'Pendente']),
            ParcelaIndividual.data_vencimento >= hoje
        ).all()

        projecao = {}
        for p in parcelas_futuras:
            if p.data_vencimento:
                mes_key = p.data_vencimento.strftime('%Y-%m')
                if mes_key not in projecao:
                    projecao[mes_key] = {'mes': mes_key, 'valor': 0, 'qtd': 0}
                projecao[mes_key]['valor'] += p.valor_parcela or 0
                projecao[mes_key]['qtd'] += 1

        projecao_lista = sorted(projecao.values(), key=lambda x: x['mes'])
        meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        for p in projecao_lista:
            ano, mes = p['mes'].split('-')
            p['mes_nome'] = f"{meses_nomes[int(mes)-1]}/{ano[2:]}"

        return jsonify({
            'projecao': projecao_lista,
            'total_projetado': sum(p['valor'] for p in projecao_lista),
            'total_parcelas': sum(p['qtd'] for p in projecao_lista)
        })
    except Exception as e:
        logger.exception(f"[BI] Erro ao buscar projeção: {e}")
        return jsonify({"erro": str(e)}), 500
