"""Regras de saldo e indicadores do Almoxarifado.

O saldo é sempre derivado do histórico. A centralização dessas regras evita
que o dashboard geral e o módulo de Almoxarifado calculem números diferentes.
"""
from sqlalchemy import case, func

from extensions import db
from models.almoxarifado_item import AlmoxarifadoItem
from models.almoxarifado_movimentacao import AlmoxarifadoMovimentacao


TIPOS_SAIDA_ESTOQUE = frozenset({'saida', 'locacao_saida', 'alocacao_obra'})
TIPOS_ENTRADA_ESTOQUE = frozenset({'entrada', 'locacao_entrada', 'devolucao_obra'})


def expressao_variacao_estoque():
    """Expressão SQL: entradas e devoluções somam; saídas/alocações subtraem.

    O ajuste continua aceitando sinal positivo ou negativo para preservar a
    compatibilidade com registros já existentes.
    """
    return case(
        (AlmoxarifadoMovimentacao.tipo.in_(TIPOS_SAIDA_ESTOQUE), -AlmoxarifadoMovimentacao.quantidade),
        else_=AlmoxarifadoMovimentacao.quantidade,
    )


def saldo_item(item_id):
    valor = (db.session.query(func.coalesce(func.sum(expressao_variacao_estoque()), 0))
             .filter(AlmoxarifadoMovimentacao.item_id == item_id)
             .scalar())
    return float(valor or 0)


def saldos_itens(item_ids):
    if not item_ids:
        return {}
    rows = (db.session.query(
        AlmoxarifadoMovimentacao.item_id,
        func.coalesce(func.sum(expressao_variacao_estoque()), 0),
    )
            .filter(AlmoxarifadoMovimentacao.item_id.in_(item_ids))
            .group_by(AlmoxarifadoMovimentacao.item_id)
            .all())
    return {item_id: float(saldo or 0) for item_id, saldo in rows}


def locacoes_ativas_itens(item_ids):
    """Quantidade de bens locados ainda sob responsabilidade da empresa."""
    if not item_ids:
        return {}
    expressao = case(
        (AlmoxarifadoMovimentacao.tipo == 'locacao_entrada', AlmoxarifadoMovimentacao.quantidade),
        (AlmoxarifadoMovimentacao.tipo == 'locacao_saida', -AlmoxarifadoMovimentacao.quantidade),
        else_=0,
    )
    rows = (db.session.query(
        AlmoxarifadoMovimentacao.item_id,
        func.coalesce(func.sum(expressao), 0),
    )
            .filter(AlmoxarifadoMovimentacao.item_id.in_(item_ids))
            .group_by(AlmoxarifadoMovimentacao.item_id)
            .all())
    return {item_id: float(quantidade or 0) for item_id, quantidade in rows}


def resumo_estoque(itens=None):
    """Indicadores operacionais usados no módulo e no dashboard principal."""
    itens = itens if itens is not None else AlmoxarifadoItem.query.filter(
        AlmoxarifadoItem.ativo.is_(True),
    ).all()
    saldos = saldos_itens([item.id for item in itens])
    locacoes = locacoes_ativas_itens([item.id for item in itens])

    quantidade_estoque = 0.0
    valor_estoque = 0.0
    equipamentos_estoque = 0.0
    valor_equipamentos = 0.0
    locacoes_ativas = 0.0
    valor_locacao_mensal = 0.0
    itens_com_estoque = 0

    for item in itens:
        saldo = max(saldos.get(item.id, 0), 0)
        valor_unitario = float(item.valor_unitario or 0)
        quantidade_estoque += saldo
        valor_estoque += saldo * valor_unitario
        if saldo > 0:
            itens_com_estoque += 1
        if item.categoria == 'equipamento':
            equipamentos_estoque += saldo
            valor_equipamentos += saldo * valor_unitario
        if item.modalidade == 'locacao':
            quantidade_locada = max(locacoes.get(item.id, 0), 0)
            locacoes_ativas += quantidade_locada
            valor_locacao_mensal += quantidade_locada * float(item.valor_locacao_mensal or 0)

    return {
        'quantidade_estoque': round(quantidade_estoque, 2),
        'itens_com_estoque': itens_com_estoque,
        'valor_estoque': round(valor_estoque, 2),
        'equipamentos_estoque': round(equipamentos_estoque, 2),
        'valor_equipamentos': round(valor_equipamentos, 2),
        'locacoes_ativas': round(locacoes_ativas, 2),
        'valor_locacao_mensal': round(valor_locacao_mensal, 2),
        'saldos': saldos,
        'locacoes_por_item': locacoes,
    }
