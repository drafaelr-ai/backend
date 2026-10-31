"""
Smoke test local da recarga de saldo nos postos — sem banco real
(SQLite in-memory) e sem rede.

O que se testa aqui: o saldo derivado (recargas - abastecimentos), as
validações do valor, quem pode lançar e excluir, o extrato na linha do
tempo e o casamento dos abastecimentos antigos que só tinham o nome do
posto digitado (sem posto_id).

Uso: cd backend && python scripts/smoke_recarga_posto_local.py
"""
import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
import models  # noqa: F401 — registra todos os models no metadata
from models import User, Obra, FrotaVeiculo, FrotaAbastecimento
from models import FrotaPostoCombustivel, FrotaPostoRecarga
from routes.frota import frota_bp

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
app.config['RATELIMIT_ENABLED'] = False
db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)
app.register_blueprint(frota_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'categoria_mo', 'funcionario',
    'frota_condutor', 'frota_veiculo', 'frota_movimentacao', 'frota_documento',
    'frota_manutencao', 'frota_posto_combustivel', 'frota_posto_recarga',
    'frota_abastecimento', 'frota_multa', 'frota_abastecimento_solicitacao',
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


def saldo_de(dados, nome):
    for posto in dados['postos']:
        if posto['posto_nome'] == nome:
            return posto
    return None


with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])

    obra = Obra(nome='Obra Recarga')
    master = User(username='master_recarga', role='master', modulos_permitidos=['obras'])
    master.set_password('smoke123')
    admin = User(username='admin_recarga', role='administrador',
                 modulos_permitidos=['frota'])
    admin.set_password('smoke123')
    comum = User(username='comum_recarga', role='comum', modulos_permitidos=['frota'])
    comum.set_password('smoke123')
    db.session.add_all([obra, master, admin, comum])
    db.session.commit()

    veiculo = FrotaVeiculo(placa='RCG1A23', modelo='Hilux', tipo='caminhonete',
                           combustivel='diesel', km_atual=50000,
                           local_tipo='obra', obra_id=obra.id)
    db.session.add(veiculo)
    db.session.commit()
    veic_id = veiculo.id

    h_master = {'Authorization': f'Bearer {create_access_token(identity=str(master.id))}'}
    h_admin = {'Authorization': f'Bearer {create_access_token(identity=str(admin.id))}'}
    h_comum = {'Authorization': f'Bearer {create_access_token(identity=str(comum.id))}'}

    hoje = date.today()
    ontem = hoje - timedelta(days=1)
    semana = hoje - timedelta(days=7)

    with app.test_client() as c:
        print('\n=== saldo sem posto cadastrado ===')
        r = c.get('/frota/postos-combustivel/saldos', headers=h_master)
        dados = json.loads(r.data)
        check('GET saldos sem postos -> 200', r.status_code == 200,
              f'got {r.status_code}: {r.data[:200]}')
        check('saldos vazio devolve lista e resumo zerados',
              dados['postos'] == [] and dados['resumo']['saldo'] == 0.0)

        print('\n=== cadastro dos postos ===')
        r = c.post('/frota/postos-combustivel', json={'nome': 'Posto Ipiranga Centro'},
                   headers=h_master)
        posto_a = json.loads(r.data)
        r = c.post('/frota/postos-combustivel', json={'nome': 'Posto Shell BR-101'},
                   headers=h_master)
        posto_b = json.loads(r.data)
        check('dois postos cadastrados', bool(posto_a.get('id') and posto_b.get('id')))

        r = c.get('/frota/postos-combustivel/saldos', headers=h_master)
        dados = json.loads(r.data)
        check('posto sem movimento aparece com saldo 0',
              saldo_de(dados, 'Posto Ipiranga Centro')['saldo'] == 0.0)

        print('\n=== quem pode lançar recarga ===')
        r = c.post(f"/frota/postos-combustivel/{posto_a['id']}/recargas",
                   json={'valor': '100,00'}, headers=h_comum)
        check('comum não lança recarga -> 403', r.status_code == 403,
              f'got {r.status_code}')
        r = c.post(f"/frota/postos-combustivel/{posto_a['id']}/recargas",
                   json={'valor': '1.250,50', 'data': semana.isoformat(),
                         'observacao': '  Depósito   em   dinheiro  '},
                   headers=h_admin)
        check('administrador lança recarga -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:200]}')
        recarga = json.loads(r.data)
        check('valor em formato BR é convertido', recarga['valor'] == 1250.5,
              f"got {recarga.get('valor')}")
        check('observação normaliza espaços',
              recarga['observacao'] == 'Depósito em dinheiro',
              f"got {recarga.get('observacao')!r}")
        check('recarga registra quem lançou',
              recarga['criado_por_nome'] == 'admin_recarga')

        print('\n=== validações do valor ===')
        for rotulo, corpo in [
            ('valor ausente', {}),
            ('valor vazio', {'valor': ''}),
            ('valor zero', {'valor': 0}),
            ('valor negativo', {'valor': '-50'}),
            ('valor não numérico', {'valor': 'abc'}),
            ('valor acima do teto', {'valor': 99_000_000}),
        ]:
            r = c.post(f"/frota/postos-combustivel/{posto_a['id']}/recargas",
                       json=corpo, headers=h_master)
            check(f'{rotulo} -> 400', r.status_code == 400, f'got {r.status_code}')

        r = c.post('/frota/postos-combustivel/999999/recargas',
                   json={'valor': 100}, headers=h_master)
        check('posto inexistente -> 404', r.status_code == 404, f'got {r.status_code}')

        print('\n=== saldo após recargas e abastecimentos ===')
        r = c.post(f"/frota/postos-combustivel/{posto_a['id']}/recargas",
                   json={'valor': 500, 'data': ontem.isoformat()}, headers=h_master)
        check('segunda recarga -> 201', r.status_code == 201)

        r = c.post('/frota/abastecimentos', json={
            'veiculo_id': veic_id, 'data': hoje.isoformat(), 'valor': '300,00',
            'litros': 50, 'km': 50500, 'posto_id': posto_a['id'],
        }, headers=h_master)
        check('abastecimento no posto A -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:200]}')

        r = c.get('/frota/postos-combustivel/saldos', headers=h_master)
        dados = json.loads(r.data)
        posto_a_saldo = saldo_de(dados, 'Posto Ipiranga Centro')
        check('total recarregado soma as duas recargas',
              posto_a_saldo['total_recarregado'] == 1750.5,
              f"got {posto_a_saldo['total_recarregado']}")
        check('total consumido vem do abastecimento',
              posto_a_saldo['total_consumido'] == 300.0,
              f"got {posto_a_saldo['total_consumido']}")
        check('saldo = recargas - abastecimentos',
              posto_a_saldo['saldo'] == 1450.5, f"got {posto_a_saldo['saldo']}")
        check('contadores de recargas e abastecimentos',
              posto_a_saldo['recargas'] == 2 and posto_a_saldo['abastecimentos'] == 1)
        check('última recarga é a data mais recente informada',
              posto_a_saldo['ultima_recarga'] == ontem.isoformat())
        check('resumo soma todos os postos',
              dados['resumo']['saldo'] == 1450.5,
              f"got {dados['resumo']['saldo']}")

        print('\n=== abastecimento antigo, só com o nome digitado ===')
        legado = FrotaAbastecimento(
            veiculo_id=veic_id, data=hoje, valor=200, litros=30,
            posto='  posto   IPIRANGA   centro ', posto_id=None,
        )
        db.session.add(legado)
        db.session.commit()
        legado_id = legado.id
        r = c.get('/frota/postos-combustivel/saldos', headers=h_master)
        posto_a_saldo = saldo_de(json.loads(r.data), 'Posto Ipiranga Centro')
        check('registro antigo sem posto_id entra no consumo pelo nome',
              posto_a_saldo['total_consumido'] == 500.0,
              f"got {posto_a_saldo['total_consumido']}")
        check('saldo desconta o registro antigo', posto_a_saldo['saldo'] == 1250.5,
              f"got {posto_a_saldo['saldo']}")

        print('\n=== saldo negativo ===')
        r = c.post('/frota/abastecimentos', json={
            'veiculo_id': veic_id, 'data': hoje.isoformat(), 'valor': 400,
            'litros': 60, 'posto_id': posto_b['id'],
        }, headers=h_master)
        check('abastecimento no posto B (sem recarga) -> 201', r.status_code == 201)
        r = c.get('/frota/postos-combustivel/saldos', headers=h_master)
        dados = json.loads(r.data)
        check('posto sem recarga fica com saldo negativo',
              saldo_de(dados, 'Posto Shell BR-101')['saldo'] == -400.0)
        check('resumo conta os postos negativos',
              dados['resumo']['postos_negativos'] == 1)
        check('ordenação mostra o pior saldo primeiro',
              dados['postos'][0]['posto_nome'] == 'Posto Shell BR-101')

        print('\n=== extrato do posto ===')
        r = c.get(f"/frota/postos-combustivel/{posto_a['id']}/extrato", headers=h_master)
        extrato = json.loads(r.data)
        check('GET extrato -> 200', r.status_code == 200, f'got {r.status_code}')
        tipos = [m['tipo'] for m in extrato['movimentos']]
        check('extrato traz recargas e abastecimentos', len(tipos) == 4,
              f'got {tipos}')
        check('extrato vem do mais recente para o mais antigo',
              extrato['movimentos'][-1]['data'] == semana.isoformat())
        check('abastecimento entra como valor negativo',
              all(m['valor'] < 0 for m in extrato['movimentos'] if m['tipo'] == 'abastecimento'))
        check('saldo acumulado do movimento mais antigo é a primeira recarga',
              extrato['movimentos'][-1]['saldo_acumulado'] == 1250.5)
        check('saldo acumulado final bate com o saldo do posto',
              extrato['movimentos'][0]['saldo_acumulado'] == 1250.5
              and extrato['resumo']['saldo'] == 1250.5,
              f"got {extrato['movimentos'][0]['saldo_acumulado']}")
        check('extrato identifica a placa do veículo',
              any(m['veiculo_placa'] == 'RCG1A23' for m in extrato['movimentos']))

        r = c.get(f"/frota/postos-combustivel/{posto_a['id']}/extrato"
                  f'?de={hoje.isoformat()}', headers=h_master)
        extrato_hoje = json.loads(r.data)
        check('filtro de data recorta os movimentos',
              all(m['data'] == hoje.isoformat() for m in extrato_hoje['movimentos'])
              and len(extrato_hoje['movimentos']) == 2,
              f"got {[m['data'] for m in extrato_hoje['movimentos']]}")
        check('resumo do extrato continua sendo o saldo total do posto',
              extrato_hoje['resumo']['saldo'] == 1250.5)

        r = c.get('/frota/postos-combustivel/999999/extrato', headers=h_master)
        check('extrato de posto inexistente -> 404', r.status_code == 404)

        print('\n=== lista de recargas ===')
        r = c.get('/frota/postos-combustivel/recargas', headers=h_master)
        lista = json.loads(r.data)
        check('GET recargas -> 200', r.status_code == 200)
        check('lista traz as duas recargas com o nome do posto',
              len(lista) == 2
              and all(x['posto_nome'] == 'Posto Ipiranga Centro' for x in lista))
        check('lista ordenada da mais recente para a mais antiga',
              lista[0]['data'] == ontem.isoformat())
        r = c.get(f"/frota/postos-combustivel/recargas?de={ontem.isoformat()}",
                  headers=h_master)
        check('filtro de data na lista de recargas', len(json.loads(r.data)) == 1)
        r = c.get(f"/frota/postos-combustivel/recargas?posto_id={posto_b['id']}",
                  headers=h_master)
        check('filtro por posto na lista de recargas', json.loads(r.data) == [])

        print('\n=== exclusão de recarga ===')
        recarga_id = lista[0]['id']
        r = c.delete(f'/frota/postos-combustivel/recargas/{recarga_id}', headers=h_admin)
        check('administrador não exclui recarga -> 403', r.status_code == 403,
              f'got {r.status_code}')
        r = c.delete(f'/frota/postos-combustivel/recargas/{recarga_id}', headers=h_master)
        check('master exclui recarga -> 200', r.status_code == 200,
              f'got {r.status_code}: {r.data[:200]}')
        check('recarga some do banco',
              db.session.get(FrotaPostoRecarga, recarga_id) is None)
        r = c.delete('/frota/postos-combustivel/recargas/999999', headers=h_master)
        check('excluir recarga inexistente -> 404', r.status_code == 404)
        r = c.get('/frota/postos-combustivel/saldos', headers=h_master)
        check('saldo cai após excluir a recarga',
              saldo_de(json.loads(r.data), 'Posto Ipiranga Centro')['saldo'] == 750.5,
              f"got {saldo_de(json.loads(r.data), 'Posto Ipiranga Centro')['saldo']}")

        print('\n=== recarga morre com o posto (ON DELETE CASCADE no Postgres) ===')
        db.session.delete(db.session.get(FrotaAbastecimento, legado_id))
        db.session.commit()
        r = c.get('/frota/postos-combustivel/saldos', headers=h_master)
        check('remover abastecimento devolve o saldo',
              saldo_de(json.loads(r.data), 'Posto Ipiranga Centro')['saldo'] == 950.5,
              f"got {saldo_de(json.loads(r.data), 'Posto Ipiranga Centro')['saldo']}")

        print('\n=== gate do módulo ===')
        sem_modulo = User(username='sem_modulo_recarga', role='comum',
                          modulos_permitidos=['obras'])
        sem_modulo.set_password('smoke123')
        db.session.add(sem_modulo)
        db.session.commit()
        h_sem = {'Authorization':
                 f'Bearer {create_access_token(identity=str(sem_modulo.id))}'}
        r = c.get('/frota/postos-combustivel/saldos', headers=h_sem)
        check('sem o módulo Frota não vê saldo -> 403', r.status_code == 403,
              f'got {r.status_code}')
        r = c.get('/frota/postos-combustivel/saldos')
        check('sem JWT não vê saldo', r.status_code in (401, 422),
              f'got {r.status_code}')

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
