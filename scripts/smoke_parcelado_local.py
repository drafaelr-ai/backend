"""
Smoke test local do pagamento parcelado — SQLite in-memory, sem banco real.

Valida os 4 fixes: (1) parcelas_customizadas de boleto honradas (valores +
código de barras), (2) ajuste de centavos na última parcela, (3) edição
estrutural regenera parcelas em aberto preservando as pagas, (4) criação
já 'Pago' com entrada marca a entrada como paga e alinha o contador.

Uso: cd backend && python scripts/smoke_parcelado_local.py
"""
import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401
from models import User, Obra, PagamentoParcelado, ParcelaIndividual
from routes.cronograma import cronograma_bp
from routes.sid import sid_bp

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(cronograma_bp)
app.register_blueprint(sid_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'lancamento', 'servico',
    'pagamento_servico', 'pagamento_parcelado_v2', 'parcela_individual',
    'pagamento_futuro', 'boleto', 'orcamento_eng_etapa', 'orcamento_eng_item',
    'notificacao',
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
    obra = Obra(nome='Obra Parcelado Smoke')
    master = User(username='master_smoke', role='master')
    master.set_password('x')
    db.session.add_all([obra, master])
    db.session.commit()
    obra_id = obra.id
    h = {'Authorization': f'Bearer {create_access_token(identity=str(master.id))}'}

    with app.test_client() as c:
        def parcelas_de(pid):
            return (ParcelaIndividual.query.filter_by(pagamento_parcelado_id=pid)
                    .order_by(ParcelaIndividual.numero_parcela).all())

        print('\n=== fix 2: centavos (1000/3) ===')
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Centavos', 'valor': 1000, 'tipo': 'Material',
            'status': 'A Pagar', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 3,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
        })
        check('POST parcelado 1000/3 -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        pid = json.loads(r.data)['pagamento_parcelado']['id']
        ps = parcelas_de(pid)
        valores = [p.valor_parcela for p in ps]
        check('parcelas 333.33/333.33/333.34', valores == [333.33, 333.33, 333.34], f'got {valores}')
        check('soma fecha 1000.00', round(sum(valores), 2) == 1000.00)

        print('\n=== fix 1: parcelas_customizadas (boleto) ===')
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Boletos custom', 'valor': 900, 'tipo': 'Material',
            'status': 'A Pagar', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 2,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
            'meio_pagamento': 'Boleto',
            'parcelas_customizadas': [
                {'numero': 1, 'valor': '400.00', 'data_vencimento': hoje.isoformat(),
                 'codigo_barras': '11111111111111111111111111111111111111111111111'},
                {'numero': 2, 'valor': '550.00',
                 'data_vencimento': (hoje + timedelta(days=45)).isoformat(),
                 'codigo_barras': '22222222222222222222222222222222222222222222222'},
            ],
        })
        check('POST boletos custom -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        body = json.loads(r.data)['pagamento_parcelado']
        pid2 = body['id']
        ps2 = parcelas_de(pid2)
        check('valores customizados persistidos', [p.valor_parcela for p in ps2] == [400.00, 550.00],
              f'got {[p.valor_parcela for p in ps2]}')
        check('códigos de barras persistidos',
              [p.codigo_barras[:2] for p in ps2] == ['11', '22'],
              f'got {[p.codigo_barras for p in ps2]}')
        check('data customizada da 2ª parcela',
              ps2[1].data_vencimento == hoje + timedelta(days=45))
        check('valor_total ajustado p/ soma dos boletos (950)', body['valor_total'] == 950.0,
              f"got {body['valor_total']}")

        print('\n=== fix 4: criação Pago com entrada ===')
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Pago com entrada', 'valor': 1000, 'tipo': 'Material',
            'status': 'Pago', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 2,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
            'tem_entrada': True, 'valor_entrada': 200, 'percentual_entrada': 20,
            'data_entrada': hoje.isoformat(),
        })
        check('POST Pago com entrada -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        body = json.loads(r.data)['pagamento_parcelado']
        pid3 = body['id']
        ps3 = parcelas_de(pid3)
        check('3 linhas (entrada + 2 parcelas)', len(ps3) == 3, f'got {len(ps3)}')
        check('entrada nasce Paga com data', ps3[0].numero_parcela == 0
              and ps3[0].status == 'Pago' and ps3[0].data_pagamento is not None,
              f'got {ps3[0].status}/{ps3[0].data_pagamento}')
        check('todas pagas', all(p.status == 'Pago' for p in ps3))
        check('contador = 3 (linhas) e Concluído',
              body['parcelas_pagas'] == 3 and body['status'] == 'Concluído',
              f"got {body['parcelas_pagas']}/{body['status']}")
        check('parcelas 400+400 e entrada 200 fecham 1000',
              round(sum(p.valor_parcela for p in ps3), 2) == 1000.00)

        print('\n=== fix 3: edição regenera parcelas em aberto ===')
        # paga a 1ª parcela do parcelamento "Centavos" e edita o total
        p1 = parcelas_de(pid)[0]
        r = c.post(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}/parcelas/{p1.id}/pagar',
                   headers=h, json={'data_pagamento': hoje.isoformat()})
        check('pagar 1ª parcela -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:200]}')

        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'valor_total': 1200, 'numero_parcelas': 4})
        check('PUT estrutural -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        ps = parcelas_de(pid)
        pagas = [p for p in ps if p.status == 'Pago']
        abertas = [p for p in ps if p.status != 'Pago']
        check('parcela paga preservada (333.33)', len(pagas) == 1 and pagas[0].valor_parcela == 333.33)
        check('3 novas parcelas em aberto', len(abertas) == 3, f'got {len(abertas)}')
        check('restante 866.67 redistribuído com centavo na última',
              [p.valor_parcela for p in abertas] == [288.89, 288.89, 288.89],
              f'got {[p.valor_parcela for p in abertas]}')
        check('soma total fecha 1200', round(sum(p.valor_parcela for p in ps), 2) == 1200.00,
              f'got {round(sum(p.valor_parcela for p in ps), 2)}')
        pai = db.session.get(PagamentoParcelado, pid)
        check('contador recomputado = 1', pai.parcelas_pagas == 1)
        check('pai continua Ativo', pai.status == 'Ativo')

        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'numero_parcelas': 0})
        check('reduzir abaixo das pagas -> 400', r.status_code == 400, f'got {r.status_code}')

        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'valor_total': 100})
        check('valor_total < soma pagas -> 400', r.status_code == 400, f'got {r.status_code}')

        # status cru não é mais aceito (só Cancelado/Ativo)
        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'status': 'Concluído', 'parcelas_pagas': 99})
        pai = db.session.get(PagamentoParcelado, pid)
        check("status 'Concluído' cru ignorado", pai.status == 'Ativo', f'got {pai.status}')
        check('parcelas_pagas cru ignorado', pai.parcelas_pagas == 1, f'got {pai.parcelas_pagas}')

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
