"""Integração financeira de equipamentos locados do Almoxarifado.

Cada alocação em obra é um evento auditável. Os pagamentos futuros e os
lançamentos criados pela baixa guardam o id desse evento, evitando conciliação
por texto de descrição ou por valores aproximados.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select

from extensions import db
from models.almoxarifado_item import AlmoxarifadoItem
from models.almoxarifado_movimentacao import AlmoxarifadoMovimentacao
from models.lancamento import Lancamento
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from models.pagamento_futuro import PagamentoFuturo


_CENTAVOS = Decimal('0.01')
_DIAS_MES_COMERCIAL = Decimal('30')


def _moeda(valor):
    return Decimal(str(valor or 0)).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


def _adicionar_meses(data_base, meses):
    mes_total = (data_base.month - 1) + meses
    ano = data_base.year + (mes_total // 12)
    mes = (mes_total % 12) + 1
    dia = min(data_base.day, monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def parcelas_proporcionais(valor_mensal, quantidade, dias_locacao, primeiro_vencimento):
    """Gera parcelas de até 30 dias usando mês comercial.

    Ex.: R$ 1.000/mês, 45 dias, 1 equipamento = R$ 1.000 + R$ 500.
    """
    valor_mensal_total = _moeda(valor_mensal) * _moeda(quantidade)
    restantes = int(dias_locacao)
    indice = 0
    parcelas = []
    while restantes > 0:
        dias_parcela = min(restantes, 30)
        valor = (valor_mensal_total * Decimal(dias_parcela) / _DIAS_MES_COMERCIAL).quantize(
            _CENTAVOS, rounding=ROUND_HALF_UP,
        )
        parcelas.append({
            'dias': dias_parcela,
            'valor': float(valor),
            'vencimento': _adicionar_meses(primeiro_vencimento, indice),
        })
        restantes -= dias_parcela
        indice += 1
    return parcelas


def validar_item_orcamento_da_obra(obra_id, orcamento_item_id):
    """Retorna o item apenas se ele pertencer à obra informada."""
    if not orcamento_item_id:
        return None
    return (db.session.query(OrcamentoEngItem)
            .join(OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id)
            .filter(OrcamentoEngItem.id == orcamento_item_id, OrcamentoEngEtapa.obra_id == obra_id)
            .first())


def fornecedor_da_locacao(item_id):
    """Fornecedor da última entrada de locação para o mesmo equipamento."""
    movimento = (AlmoxarifadoMovimentacao.query
                 .filter(
                     AlmoxarifadoMovimentacao.item_id == item_id,
                     AlmoxarifadoMovimentacao.tipo == 'locacao_entrada',
                     AlmoxarifadoMovimentacao.fornecedor.isnot(None),
                 )
                 .order_by(AlmoxarifadoMovimentacao.id.desc())
                 .first())
    return movimento.fornecedor if movimento else None


def criar_pagamentos_locacao(movimentacao, item, dias_locacao, primeiro_vencimento, orcamento_item_id):
    """Agenda os vencimentos da alocação dentro da transação atual."""
    fornecedor = fornecedor_da_locacao(item.id)
    if not fornecedor:
        raise ValueError('Não há fornecedor de locação registrado para este equipamento')

    parcelas = parcelas_proporcionais(
        item.valor_locacao_mensal,
        movimentacao.quantidade,
        dias_locacao,
        primeiro_vencimento,
    )
    descricao_base = f'Locação de equipamento: {item.nome}'
    pagamentos = []
    for indice, parcela in enumerate(parcelas, start=1):
        descricao = descricao_base
        if len(parcelas) > 1:
            descricao = f'{descricao_base} ({indice}/{len(parcelas)})'
        pagamento = PagamentoFuturo(
            obra_id=movimentacao.obra_id,
            descricao=descricao,
            valor=parcela['valor'],
            data_vencimento=parcela['vencimento'],
            fornecedor=fornecedor,
            observacoes=(
                f'Almoxarifado: movimentação {movimentacao.id}; '
                f'{parcela["dias"]} dia(s) de locação.'
            ),
            status='Previsto',
            servico_id=None,
            tipo='Equipamentos',
            orcamento_item_id=orcamento_item_id,
            almoxarifado_movimentacao_id=movimentacao.id,
        )
        db.session.add(pagamento)
        pagamentos.append(pagamento)
    return pagamentos


def resumo_financeiro_locacoes():
    """Valores financeiros de locações já alocadas, por situação de baixa."""
    movimentacoes_locacao = (select(AlmoxarifadoMovimentacao.id)
                              .join(AlmoxarifadoItem, AlmoxarifadoMovimentacao.item_id == AlmoxarifadoItem.id)
                              .where(
                                  AlmoxarifadoMovimentacao.tipo == 'alocacao_obra',
                                  AlmoxarifadoItem.categoria == 'equipamento',
                                  AlmoxarifadoItem.modalidade == 'locacao',
                              ))

    pendente_futuro = (db.session.query(func.coalesce(func.sum(PagamentoFuturo.valor), 0))
                        .filter(
                            PagamentoFuturo.almoxarifado_movimentacao_id.in_(movimentacoes_locacao),
                            PagamentoFuturo.status == 'Previsto',
                        ).scalar())
    pago_futuro = (db.session.query(func.coalesce(func.sum(PagamentoFuturo.valor), 0))
                   .filter(
                       PagamentoFuturo.almoxarifado_movimentacao_id.in_(movimentacoes_locacao),
                       PagamentoFuturo.status == 'Pago',
                   ).scalar())
    pendente_lancamento = (db.session.query(func.coalesce(func.sum(Lancamento.valor_total), 0))
                           .filter(
                               Lancamento.almoxarifado_movimentacao_id.in_(movimentacoes_locacao),
                               Lancamento.status != 'Pago',
                           ).scalar())
    pago_lancamento = (db.session.query(func.coalesce(func.sum(Lancamento.valor_pago), 0))
                       .filter(
                           Lancamento.almoxarifado_movimentacao_id.in_(movimentacoes_locacao),
                           Lancamento.status == 'Pago',
                       ).scalar())

    pendente = float(pendente_futuro or 0) + float(pendente_lancamento or 0)
    pago = float(pago_futuro or 0) + float(pago_lancamento or 0)
    return {
        'locacoes_financeiro_pendente': round(pendente, 2),
        'locacoes_financeiro_pago': round(pago, 2),
        'locacoes_financeiro_total': round(pendente + pago, 2),
    }
