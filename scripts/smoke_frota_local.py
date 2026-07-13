"""
Smoke test local do módulo Frota — sem banco real (SQLite in-memory).

O app.py do main roda a auto-migration no import (Postgres real), então este
smoke monta um mini-app só com o necessário: extensions + models + frota_bp.
Cria apenas as tabelas usadas (evita JSONB de convencao_valor, que não compila
em SQLite).

Uso: cd backend && python scripts/smoke_frota_local.py
"""
import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.pop('DATABASE_URL_ADMIN', None)  # testa a degradação graciosa

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401 — registra todos os models no metadata
from models import User, Obra, FrotaVeiculo
from routes.frota import frota_bp

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(frota_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'categoria_mo', 'funcionario',
    'frota_condutor', 'frota_veiculo', 'frota_movimentacao', 'frota_documento',
    'frota_manutencao', 'frota_abastecimento', 'frota_multa',
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


with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])

    obra1 = Obra(nome='Obra Smoke 1')
    obra2 = Obra(nome='Obra Smoke 2')
    master = User(username='master_smoke', role='master')
    master.set_password('smoke123')
    comum = User(username='comum_smoke', role='comum')
    comum.set_password('smoke123')
    comum.obras_permitidas.append(obra1)
    db.session.add_all([obra1, obra2, master, comum])
    db.session.commit()
    obra1_id, obra2_id = obra1.id, obra2.id

    h_master = {'Authorization': f'Bearer {create_access_token(identity=str(master.id))}'}
    h_comum = {'Authorization': f'Bearer {create_access_token(identity=str(comum.id))}'}

    with app.test_client() as c:
        print('\n=== GETs básicos (master) ===')
        for rota in ('/frota/veiculos', '/frota/condutores', '/frota/manutencoes',
                     '/frota/abastecimentos', '/frota/multas', '/frota/dashboard'):
            r = c.get(rota, headers=h_master)
            check(f'GET {rota} -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')

        r = c.get('/frota/veiculos')
        check('GET /frota/veiculos sem token -> 401', r.status_code == 401)

        print('\n=== imoveis-admin (sem DATABASE_URL_ADMIN) ===')
        r = c.get('/frota/imoveis-admin', headers=h_master)
        check('GET /frota/imoveis-admin -> 200', r.status_code == 200)
        body = json.loads(r.data)
        check('imoveis-admin: lista vazia + aviso', body['imoveis'] == [] and bool(body['aviso']))

        print('\n=== condutores ===')
        r = c.post('/frota/condutores', json={
            'nome': 'João Smoke', 'cnh_numero': '123456', 'cnh_categoria': 'ab',
            'cnh_validade': (date.today() + timedelta(days=10)).isoformat(),
        }, headers=h_master)
        check('POST /frota/condutores -> 201', r.status_code == 201)
        condutor = json.loads(r.data)
        check('condutor: cnh_categoria uppercase', condutor['cnh_categoria'] == 'AB')
        check('condutor: cnh_status a_vencer', condutor['cnh_status'] == 'a_vencer')
        condutor_id = condutor['id']

        r = c.post('/frota/condutores', json={}, headers=h_master)
        check('POST /frota/condutores sem nome -> 400', r.status_code == 400)

        print('\n=== veículos ===')
        r = c.post('/frota/veiculos', json={
            'placa': 'abc-1d23', 'modelo': 'Hilux', 'marca': 'Toyota', 'tipo': 'caminhonete',
            'destino_tipo': 'obra', 'obra_id': obra2_id, 'km_atual': 50000,
        }, headers=h_master)
        check('POST /frota/veiculos (local inicial obra2) -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:300]}')
        veic = json.loads(r.data)
        check('veiculo: placa normalizada', veic['placa'] == 'ABC1D23')
        check('veiculo: local_tipo=obra', veic['local_tipo'] == 'obra')
        check('veiculo: obra_nome preenchido', veic['obra_nome'] == 'Obra Smoke 2')
        veic_id = veic['id']

        r = c.post('/frota/veiculos', json={'placa': 'ABC1D23', 'modelo': 'Clone'}, headers=h_master)
        check('POST placa duplicada -> 400', r.status_code == 400, f'got {r.status_code}')

        r = c.post('/frota/veiculos', json={'placa': 'XYZ9Z99'}, headers=h_master)
        check('POST sem modelo -> 400', r.status_code == 400)

        r = c.get('/frota/veiculos', headers=h_comum)
        check('comum não vê veículo da obra2', len(json.loads(r.data)) == 0)

        r = c.get(f'/frota/veiculos/{veic_id}', headers=h_comum)
        check('GET item obra2 como comum -> 403', r.status_code == 403)

        print('\n=== movimentações ===')
        r = c.post(f'/frota/veiculos/{veic_id}/movimentacoes', json={
            'destino_tipo': 'imovel', 'imovel_id': 7, 'imovel_nome': 'Apto Centro',
        }, headers=h_master)
        check('POST movimentação p/ imóvel -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:300]}')
        body = json.loads(r.data)
        check('veiculo atualizado: local_tipo=imovel', body['veiculo']['local_tipo'] == 'imovel')
        check('veiculo atualizado: imovel_nome snapshot', body['veiculo']['imovel_nome'] == 'Apto Centro')
        check('veiculo atualizado: obra_id limpo', body['veiculo']['obra_id'] is None)

        r = c.get(f'/frota/veiculos/{veic_id}/movimentacoes', headers=h_master)
        movs = json.loads(r.data)
        check('histórico tem 2 movimentações (inicial + imóvel)', len(movs) == 2,
              f'got {len(movs)}')

        r = c.get('/frota/veiculos', headers=h_comum)
        check('comum vê veículo em imóvel (sem obra)', len(json.loads(r.data)) == 1)

        r = c.post(f'/frota/veiculos/{veic_id}/movimentacoes', json={
            'destino_tipo': 'obra', 'obra_id': obra2_id,
        }, headers=h_comum)
        check('comum mover p/ obra sem acesso -> 403', r.status_code == 403)

        r = c.post(f'/frota/veiculos/{veic_id}/movimentacoes', json={
            'destino_tipo': 'obra', 'obra_id': obra1_id,
        }, headers=h_comum)
        check('comum mover p/ obra permitida -> 201', r.status_code == 201)

        r = c.post(f'/frota/veiculos/{veic_id}/movimentacoes', json={
            'destino_tipo': 'banana',
        }, headers=h_master)
        check('destino_tipo inválido -> 400', r.status_code == 400)

        print('\n=== condutor no veículo ===')
        r = c.patch(f'/frota/veiculos/{veic_id}/condutor',
                    json={'condutor_id': condutor_id}, headers=h_master)
        check('PATCH condutor -> 200', r.status_code == 200)
        check('condutor_atual_nome', json.loads(r.data)['condutor_atual_nome'] == 'João Smoke')

        r = c.delete(f'/frota/condutores/{condutor_id}', headers=h_master)
        check('DELETE condutor em uso -> 400', r.status_code == 400)

        print('\n=== documentos ===')
        r = c.post(f'/frota/veiculos/{veic_id}/documentos', json={
            'tipo': 'crlv',
            'data_vencimento': (date.today() - timedelta(days=5)).isoformat(),
        }, headers=h_master)
        check('POST documento -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:200]}')
        doc = json.loads(r.data)
        check('documento vencido: status=vencido', doc['status'] == 'vencido')
        doc_id = doc['id']

        r = c.post(f'/frota/veiculos/{veic_id}/documentos', json={'tipo': 'nota'}, headers=h_master)
        check('POST documento tipo inválido -> 400', r.status_code == 400)

        print('\n=== manutenções ===')
        r = c.post('/frota/manutencoes', json={
            'veiculo_id': veic_id, 'tipo': 'corretiva', 'data': date.today().isoformat(),
            'custo': '1.250,00', 'km': 51000, 'oficina': 'Oficina Smoke',
        }, headers=h_master)
        check('POST manutenção -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:300]}')
        manut = json.loads(r.data)
        check('manutenção: custo BR parseado', manut['custo'] == 1250.0)
        check('manutenção: snapshot local_tipo=obra', manut['local_tipo'] == 'obra',
              f"got {manut['local_tipo']}")
        check('manutenção: snapshot local_nome', manut['local_nome'] == 'Obra Smoke 1')

        r = c.get(f'/frota/veiculos/{veic_id}', headers=h_master)
        check('km_atual atualizado p/ 51000', json.loads(r.data)['km_atual'] == 51000)

        print('\n=== abastecimentos ===')
        r = c.post('/frota/abastecimentos', json={
            'veiculo_id': veic_id, 'data': date.today().isoformat(),
            'valor': 350.5, 'litros': 60, 'km': 51200, 'condutor_id': condutor_id,
        }, headers=h_master)
        check('POST abastecimento -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:300]}')
        check('abastecimento: condutor_nome', json.loads(r.data)['condutor_nome'] == 'João Smoke')

        print('\n=== multas ===')
        r = c.post('/frota/multas', json={
            'veiculo_id': veic_id, 'data_infracao': date.today().isoformat(),
            'valor': 195.23, 'pontos': 5, 'condutor_id': condutor_id,
        }, headers=h_master)
        check('POST multa -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:300]}')
        multa_id = json.loads(r.data)['id']

        r = c.put(f'/frota/multas/{multa_id}', json={
            'status_pagamento': 'paga', 'data_pagamento': date.today().isoformat(),
        }, headers=h_master)
        check('PUT multa paga -> 200', r.status_code == 200)
        check('multa: status=paga', json.loads(r.data)['status_pagamento'] == 'paga')

        print('\n=== dashboard ===')
        r = c.get('/frota/dashboard', headers=h_master)
        check('GET dashboard -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        dash = json.loads(r.data)
        check('dashboard: veiculos_ativos=1', dash['veiculos_ativos'] == 1)
        check('dashboard: custo manutenções', dash['custo_mes']['manutencoes'] == 1250.0)
        check('dashboard: custo abastecimentos', dash['custo_mes']['abastecimentos'] == 350.5)
        check('dashboard: multas pagas', dash['custo_mes']['multas_pagas'] == 195.23)
        check('dashboard: total soma', dash['custo_mes']['total'] == round(1250.0 + 350.5 + 195.23, 2))
        check('dashboard: doc vencido listado', len(dash['documentos_vencidos']) == 1)
        check('dashboard: CNH a vencer listada', len(dash['cnhs_a_vencer']) == 1)
        check('dashboard: custo_por_local não-vazio', len(dash['custo_por_local']) >= 1)

        r = c.get('/frota/dashboard?competencia=1999-01', headers=h_master)
        check('dashboard competência antiga: total 0', json.loads(r.data)['custo_mes']['total'] == 0)

        r = c.get('/frota/dashboard?competencia=banana', headers=h_master)
        check('dashboard competência inválida -> 400', r.status_code == 400)

        print('\n=== edição e remoção ===')
        r = c.put(f'/frota/veiculos/{veic_id}', json={'cor': 'Prata', 'status': 'em_manutencao'},
                  headers=h_master)
        check('PUT veículo -> 200', r.status_code == 200)
        check('veículo: status em_manutencao', json.loads(r.data)['status'] == 'em_manutencao')

        r = c.delete(f'/frota/documentos/{doc_id}', headers=h_master)
        check('DELETE documento -> 200', r.status_code == 200)

        r = c.patch(f'/frota/veiculos/{veic_id}/condutor', json={'condutor_id': None},
                    headers=h_master)
        check('PATCH remove condutor -> 200', r.status_code == 200)

        r = c.delete(f'/frota/condutores/{condutor_id}', headers=h_master)
        check('DELETE condutor liberado -> 200', r.status_code == 200)

        r = c.delete(f'/frota/veiculos/{veic_id}', headers=h_master)
        check('DELETE veículo (soft) -> 200', r.status_code == 200)
        r = c.get(f'/frota/veiculos/{veic_id}', headers=h_master)
        check('veículo inativo após delete', json.loads(r.data)['status'] == 'inativo')

        with app.app_context():
            pass
        rotas_frota = [r for r in app.url_map.iter_rules() if str(r).startswith('/frota')]
        check('blueprint: >= 25 rotas /frota', len(rotas_frota) >= 25,
              f'encontrado: {len(rotas_frota)}')

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
