"""
Smoke test local do blueprint /home — SQLite in-memory, sem banco real.

Cobre /home/alertas (fontes main, gating por módulo, degradação do admin) e
/home/obras (gastos MO/material do mês, previsão a pagar, por-obra).

Uso: cd backend && python scripts/smoke_home_local.py
"""
import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop('DATABASE_URL_ADMIN', None)  # degradação graciosa do lado admin

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401
from models import (User, Obra, Lancamento, Boleto, ParcelaIndividual,
                    PagamentoParcelado, PagamentoFuturo, Servico, PagamentoServico)
from routes.home import home_bp

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(home_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'lancamento', 'boleto',
    'parcela_individual', 'pagamento_parcelado_v2', 'pagamento_futuro',
    'servico', 'pagamento_servico',
]

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


hoje = date.today()

with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])

    obra1 = Obra(nome='Obra Smoke 1')
    obra2 = Obra(nome='Obra Smoke 2')
    obra3_concluida = Obra(nome='Obra Smoke 3 (concluida)', concluida=True)
    master = User(username='master_smoke', role='master')
    master.set_password('x')
    sem_obras = User(username='sem_obras_smoke', role='comum', modulos_permitidos=['rh'])
    sem_obras.set_password('x')
    db.session.add_all([obra1, obra2, obra3_concluida, master, sem_obras])
    db.session.commit()

    # ---- pendências ----
    db.session.add_all([
        # lançamento vencido (restante 300)
        Lancamento(obra_id=obra1.id, tipo='Material', descricao='Cimento CP-II',
                   valor_total=500, valor_pago=200, data=hoje - timedelta(days=10),
                   data_vencimento=hoje - timedelta(days=3), status='A Pagar',
                   fornecedor='Concreteira Sul'),
        # lançamento a vencer fora da janela default (5 dias)
        Lancamento(obra_id=obra1.id, tipo='Material', descricao='Areia',
                   valor_total=100, valor_pago=0, data=hoje,
                   data_vencimento=hoje + timedelta(days=5), status='A Pagar'),
        # boleto pendente vence hoje
        Boleto(obra_id=obra2.id, descricao='Cerâmica Norte', valor=3180,
               data_vencimento=hoje, status='Pendente'),
        # pagamento futuro vencido
        PagamentoFuturo(obra_id=obra1.id, descricao='Elétrica 2ª etapa', valor=8900,
                        data_vencimento=hoje - timedelta(days=1), status='Previsto'),
        # lançamento vencido de obra CONCLUÍDA — não deve virar alerta (fixture de regressão)
        Lancamento(obra_id=obra3_concluida.id, tipo='Material', descricao='Ghost pendencia concluida',
                   valor_total=999, valor_pago=0, data=hoje - timedelta(days=20),
                   data_vencimento=hoje - timedelta(days=5), status='A Pagar'),
    ])
    pp = PagamentoParcelado(obra_id=obra1.id, descricao='Esquadrias', segmento='Material',
                            valor_total=6000, numero_parcelas=3, valor_parcela=2000,
                            data_primeira_parcela=hoje - timedelta(days=40))
    db.session.add(pp)
    db.session.flush()
    db.session.add_all([
        ParcelaIndividual(pagamento_parcelado_id=pp.id, numero_parcela=1, valor_parcela=2000,
                          data_vencimento=hoje - timedelta(days=2), status='Pendente'),
        ParcelaIndividual(pagamento_parcelado_id=pp.id, numero_parcela=2, valor_parcela=2000,
                          data_vencimento=hoje + timedelta(days=20), status='Previsto'),
        # parcela paga neste mês (material via segmento)
        ParcelaIndividual(pagamento_parcelado_id=pp.id, numero_parcela=0, valor_parcela=2000,
                          data_vencimento=hoje - timedelta(days=30), status='Pago',
                          data_pagamento=hoje),
    ])

    # ---- gastos do mês ----
    db.session.add(Lancamento(obra_id=obra1.id, tipo='Mão de Obra', descricao='Pedreiro',
                              valor_total=1500, valor_pago=1500, data=hoje, status='Pago'))
    db.session.add(Lancamento(obra_id=obra2.id, tipo='Material', descricao='Tijolos',
                              valor_total=800, valor_pago=800, data=hoje, status='Pago'))
    db.session.add(Lancamento(obra_id=obra2.id, tipo='Despesa', descricao='Taxa cartório',
                              valor_total=400, valor_pago=400, data=hoje, status='Pago'))
    db.session.add(Lancamento(obra_id=obra1.id, tipo='Equipamentos', descricao='Aluguel andaime',
                              valor_total=600, valor_pago=600, data=hoje, status='Pago'))
    db.session.add(Lancamento(obra_id=obra2.id, tipo='Serviço', descricao='Empreita pintura',
                              valor_total=300, valor_pago=300, data=hoje, status='Pago'))
    # lançamento-ESPELHO de parcela paga: NÃO deve somar (a parcela já conta)
    db.session.add(Lancamento(obra_id=obra1.id, tipo='Despesa', descricao='Esquadrias (Parcela 0/3)',
                              valor_total=2000, valor_pago=2000, data=hoje, status='Pago'))
    sv = Servico(obra_id=obra2.id, nome='Alvenaria')
    sv_equip = Servico(obra_id=obra1.id, nome='Locação de equipamentos')
    db.session.add_all([sv, sv_equip])
    db.session.flush()
    db.session.add(PagamentoServico(servico_id=sv.id, data=hoje, valor_total=2500,
                                    valor_pago=2500, status='Pago', tipo_pagamento='mao_de_obra'))
    db.session.add(PagamentoServico(servico_id=sv_equip.id, data=hoje, valor_total=700,
                                    valor_pago=700, status='Pago', tipo_pagamento='equipamento'))
    pp_equip = PagamentoParcelado(obra_id=obra1.id, descricao='Guindaste', segmento='Equipamento',
                                  valor_total=900, numero_parcelas=1, valor_parcela=900,
                                  data_primeira_parcela=hoje - timedelta(days=5))
    db.session.add(pp_equip)
    db.session.flush()
    db.session.add(ParcelaIndividual(pagamento_parcelado_id=pp_equip.id, numero_parcela=0,
                                     valor_parcela=900, data_vencimento=hoje - timedelta(days=5),
                                     status='Pago', data_pagamento=hoje))
    db.session.commit()

    h_master = {'Authorization': f'Bearer {create_access_token(identity=str(master.id))}'}
    h_sem = {'Authorization': f'Bearer {create_access_token(identity=str(sem_obras.id))}'}

    with app.test_client() as c:
        print('\n=== /home/alertas ===')
        r = c.get('/home/alertas', headers=h_master)
        check('GET /home/alertas -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:200]}')
        body = json.loads(r.data)
        pend = body['pendencias']
        # janela default 3 dias: lançamento vencido, boleto hoje, futuro vencido, parcela vencida = 4
        check('4 pendências na janela default', len(pend) == 4, f'got {len(pend)}: {[p["descricao"] for p in pend]}')
        check('vencidos primeiro', pend[0]['situacao'] == 'vencido')
        check('lançamento usa valor restante (300)',
              any(p['valor'] == 300 and 'Cimento' in p['descricao'] for p in pend))
        check('boleto vence_hoje presente',
              any(p['situacao'] == 'vence_hoje' and 'Cer' in p['descricao'] for p in pend))
        check('parcela com numeração',
              any('parcela 1/3' in p['descricao'] for p in pend))
        check('origem = nome da obra', all(p['origem'] in ('Obra Smoke 1', 'Obra Smoke 2') for p in pend))
        check('resumo obras: 3 vencidos', body['resumo']['obras']['vencidos'] == 3,
              f"got {body['resumo']['obras']}")
        check('admin sem env: resumo zerado + sem quebrar', body['resumo']['admin']['qtd'] == 0)
        check('obra concluída não gera pendência (sem ghost)',
              not any('Ghost' in p['descricao'] for p in pend), f'got {[p["descricao"] for p in pend]}')

        r = c.get('/home/alertas?dias=10', headers=h_master)
        check('janela 10 dias inclui Areia', any('Areia' in p['descricao']
              for p in json.loads(r.data)['pendencias']))

        r = c.get('/home/alertas', headers=h_sem)
        body = json.loads(r.data)
        check('user só com RH: zero pendências (sem obras/admin)',
              r.status_code == 200 and body['pendencias'] == [])

        r = c.get('/home/alertas?dias=banana', headers=h_master)
        check('dias inválido -> 400', r.status_code == 400)

        r = c.get('/home/alertas')
        check('sem token -> 401', r.status_code == 401)

        print('\n=== /home/obras ===')
        r = c.get('/home/obras', headers=h_master)
        check('GET /home/obras -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        body = json.loads(r.data)
        k = body['kpis']
        check('MO total = 1500 + 2500', k['mo_total'] == 4000.0, f"got {k['mo_total']}")
        check('Material total = 800 + 2000 (parcela paga)', k['material_total'] == 2800.0,
              f"got {k['material_total']}")
        check('Equipamento total = 600 (lancamento) + 700 (pag.serviço) + 900 (parcela)',
              k['equipamento_total'] == 2200.0, f"got {k['equipamento_total']}")
        check('Serviço total = 300', k['servico_total'] == 300.0, f"got {k['servico_total']}")
        check('Despesa total = 400 (espelho de parcela excluído)', k['despesa_total'] == 400.0,
              f"got {k['despesa_total']}")
        check('Saídas do mês = 9700 (espelho de parcela excluído)', k['saidas_mes'] == 9700.0, f"got {k['saidas_mes']}")
        # previsão até fim do mês: depende do dia — todos os 4 vencidos/hoje entram; Areia (+5d)
        # e parcela +20d entram se caírem dentro do mês. Valida coerência mínima:
        check('previsão >= soma dos vencidos+hoje (14380)', k['previsao_pagar']['total'] >= 14380,
              f"got {k['previsao_pagar']}")
        o1 = next(o for o in body['obras'] if o['nome'] == 'Obra Smoke 1')
        o2 = next(o for o in body['obras'] if o['nome'] == 'Obra Smoke 2')
        check('obra1: mo_total 1500', o1['mo_total'] == 1500.0)
        check('obra1: equipamento_total 2200 (lancamento + pag.serviço + parcela)',
              o1['equipamento_total'] == 2200.0, f"got {o1}")
        check('obra2: servico_total 300', o2['servico_total'] == 300.0, f"got {o2}")
        check('obra2: despesa_total 400', o2['despesa_total'] == 400.0, f"got {o2}")
        check('obra1: vencidos_qtd 3', o1['vencidos_qtd'] == 3, f"got {o1}")

        r = c.get('/home/obras?competencia=1999-01', headers=h_master)
        check('competência antiga: saídas 0', json.loads(r.data)['kpis']['saidas_mes'] == 0)
        r = c.get('/home/obras?competencia=xx', headers=h_master)
        check('competência inválida -> 400', r.status_code == 400)
        r = c.get('/home/obras', headers=h_sem)
        check('sem módulo obras -> 403', r.status_code == 403, f'got {r.status_code}')

print(f'\n{"=" * 40}')
print(f'PASS: {len(PASS)}  FAIL: {len(FAIL)}')
if FAIL:
    print('FALHAS:')
    for f in FAIL:
        print(f'  - {f}')
    sys.exit(1)
else:
    print('Todos os cenarios passaram.')
    sys.exit(0)
