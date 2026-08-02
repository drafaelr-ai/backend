"""Regressao local dos totais pagos, sem acessar o banco real.

Valida que cada fonte financeira entra uma unica vez e que o lancamento-espelho
de parcela nao duplica o valor canonico registrado em ``parcela_individual``.

Uso: cd backend && python scripts/smoke_financeiro_totais_local.py
"""
import os
import sys
from datetime import date


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

from extensions import db
import models  # noqa: F401 - registra o metadata
from models import (
    Boleto,
    Lancamento,
    Obra,
    PagamentoFuturo,
    PagamentoParcelado,
    PagamentoServico,
    ParcelaIndividual,
    Servico,
)
from services.financeiro_service import calcular_totais_pagos_obra


app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    TESTING=True,
)
db.init_app(app)

TABLES = [
    'obra',
    'servico',
    'lancamento',
    'pagamento_servico',
    'pagamento_parcelado_v2',
    'parcela_individual',
    'pagamento_futuro',
    'user',
    'boleto',
]


def check(label, condition, detail=''):
    if not condition:
        raise AssertionError(f'{label}: {detail}')
    print(f'  PASS  {label}')


with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[name] for name in TABLES])

    obra = Obra(nome='Obra de regressao financeira')
    outra_obra = Obra(nome='Obra isolada')
    db.session.add_all([obra, outra_obra])
    db.session.flush()

    servico = Servico(obra_id=obra.id, nome='Alvenaria')
    db.session.add(servico)
    db.session.flush()

    parcelado = PagamentoParcelado(
        obra_id=obra.id,
        descricao='Esquadrias',
        valor_total=40,
        numero_parcelas=1,
        valor_parcela=40,
        data_primeira_parcela=date.today(),
    )
    db.session.add(parcelado)
    db.session.flush()

    db.session.add_all([
        Lancamento(
            obra_id=obra.id,
            tipo='Material',
            descricao='Cimento',
            valor_total=100,
            valor_pago=100,
            data=date.today(),
            status='Pago',
        ),
        # Registro legado criado quando a parcela era baixada. A parcela abaixo
        # e a fonte canonica; este espelho nao pode entrar novamente no total.
        Lancamento(
            obra_id=obra.id,
            tipo='Material',
            descricao='Esquadrias (Parcela 1/1)',
            valor_total=40,
            valor_pago=40,
            data=date.today(),
            status='Pago',
        ),
        PagamentoServico(
            servico_id=servico.id,
            data=date.today(),
            valor_total=200,
            valor_pago=200,
            status='Pago',
            tipo_pagamento='mao_de_obra',
        ),
        ParcelaIndividual(
            pagamento_parcelado_id=parcelado.id,
            numero_parcela=1,
            valor_parcela=40,
            data_vencimento=date.today(),
            data_pagamento=date.today(),
            status='Pago',
        ),
        PagamentoFuturo(
            obra_id=obra.id,
            descricao='Registro legado pago',
            valor=30,
            data_vencimento=date.today(),
            status='Pago',
        ),
        Boleto(
            obra_id=obra.id,
            descricao='Boleto quitado',
            valor=50,
            data_vencimento=date.today(),
            data_pagamento=date.today(),
            status='Pago',
        ),
        Lancamento(
            obra_id=outra_obra.id,
            tipo='Material',
            descricao='Nao pode vazar entre obras',
            valor_total=999,
            valor_pago=999,
            data=date.today(),
            status='Pago',
        ),
    ])
    db.session.commit()

    totais = calcular_totais_pagos_obra(obra.id)
    check('lancamento normal entra uma vez', totais['lancamentos'] == 100, totais)
    check('pagamento de servico entra uma vez', totais['pagamentos_servico'] == 200, totais)
    check('parcela paga entra uma vez', totais['parcelas'] == 40, totais)
    check('boleto pago entra uma vez', totais['boletos'] == 50, totais)
    check(
        'pagamento futuro legado permanece no historico',
        totais['pagamentos_futuros_legados'] == 30,
        totais,
    )
    check('total canonico sem duplicidade e 420', totais['total'] == 420, totais)

    print('\n6/6 verificacoes financeiras passaram.')
