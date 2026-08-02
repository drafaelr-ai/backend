"""Consolida os valores efetivamente pagos de uma obra.

Cada fonte financeira e somada uma unica vez. Parcelas sao canonicas em
``parcela_individual``; por isso os lancamentos-espelho criados por versoes
antigas do fluxo parcelado ficam fora do total.
"""

from sqlalchemy import func

from extensions import db
from models.boleto import Boleto
from models.lancamento import Lancamento
from models.pagamento_futuro import PagamentoFuturo
from models.pagamento_parcelado import PagamentoParcelado
from models.pagamento_servico import PagamentoServico
from models.parcela_individual import ParcelaIndividual
from models.servico import Servico


def calcular_totais_pagos_obra(obra_id):
    """Retorna a composicao canonica do total pago, sem duplicar parcelas."""
    lancamentos = db.session.query(func.sum(Lancamento.valor_pago)).filter(
        Lancamento.obra_id == obra_id,
        ~func.coalesce(Lancamento.descricao, '').like('%(Parcela %'),
    ).scalar()

    pagamentos_servico = db.session.query(func.sum(PagamentoServico.valor_pago)).join(
        Servico, PagamentoServico.servico_id == Servico.id
    ).filter(Servico.obra_id == obra_id).scalar()

    parcelas = db.session.query(func.sum(ParcelaIndividual.valor_parcela)).join(
        PagamentoParcelado,
        ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id,
    ).filter(
        PagamentoParcelado.obra_id == obra_id,
        ParcelaIndividual.status == 'Pago',
    ).scalar()

    boletos = db.session.query(func.sum(Boleto.valor)).filter(
        Boleto.obra_id == obra_id,
        Boleto.status == 'Pago',
    ).scalar()

    # O fluxo atual converte esses registros ao pagar. Dados antigos que
    # permaneceram como Pago continuam validos e nao podem sumir do historico.
    pagamentos_futuros_legados = db.session.query(func.sum(PagamentoFuturo.valor)).filter(
        PagamentoFuturo.obra_id == obra_id,
        PagamentoFuturo.status == 'Pago',
    ).scalar()

    fontes = {
        'lancamentos': float(lancamentos or 0),
        'pagamentos_servico': float(pagamentos_servico or 0),
        'parcelas': float(parcelas or 0),
        'boletos': float(boletos or 0),
        'pagamentos_futuros_legados': float(pagamentos_futuros_legados or 0),
    }
    return {**fontes, 'total': sum(fontes.values())}
