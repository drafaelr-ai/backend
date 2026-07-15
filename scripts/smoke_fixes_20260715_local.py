"""
Smoke test local dos fixes de 2026-07-15 — SQLite in-memory, sem banco real.

Cobre: (1) tipo_composicao='fornecimento' no orcamento_eng_item (calculo de
totais + card de Servico), (2) sync orcamento -> cronograma (limpeza de
orfaos sem tocar em item manual), (3) endpoint /cronograma/.../limpar
restrito a master/administrador.

Uso: cd backend && python scripts/smoke_fixes_20260715_local.py
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
from models import User, Obra
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from models.servico import Servico
from models.cronograma_obra import CronogramaObra
from routes.orcamento_eng import orcamento_eng_bp
from routes.cronograma import cronograma_bp

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(orcamento_eng_bp)
app.register_blueprint(cronograma_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'servico', 'pagamento_servico',
    'orcamento_eng_etapa', 'orcamento_eng_item', 'cronograma_obra',
    'cronograma_etapa', 'boleto', 'pagamento_parcelado_v2', 'parcela_individual',
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
    # orcamento_etapa_id nao esta declarada no model ORM (adicionada via auto_migration
    # em prod) -- replica isso aqui para o smoke exercitar o mesmo caminho de raw SQL.
    db.session.execute(db.text(
        "ALTER TABLE cronograma_obra ADD COLUMN orcamento_etapa_id INTEGER"
    ))
    db.session.commit()

    obra = Obra(nome='Obra Fixes Smoke')
    master = User(username='master_smoke2', role='master')
    master.set_password('x')
    operador = User(username='operador_smoke2', role='operador')
    operador.set_password('x')
    db.session.add_all([obra, master, operador])
    db.session.commit()
    obra_id = obra.id
    h_master = {'Authorization': f'Bearer {create_access_token(identity=str(master.id))}'}
    h_op = {'Authorization': f'Bearer {create_access_token(identity=str(operador.id))}'}

    with app.test_client() as c:
        print('\n=== fornecimento: calcular_totais() ===')
        etapa = OrcamentoEngEtapa(obra_id=obra_id, codigo='01', nome='LOCACOES', ordem=0)
        db.session.add(etapa)
        db.session.commit()

        r = c.post(f'/obras/{obra_id}/orcamento-eng/itens', headers=h_master, json={
            'etapa_id': etapa.id, 'descricao': 'Locacao de andaime', 'unidade': 'mes',
            'quantidade': 2, 'tipo_composicao': 'fornecimento', 'preco_unitario': 500,
        })
        check('POST item fornecimento -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        item_body = json.loads(r.data)
        check('total_fornecimento = 1000, total_mao_obra/material = 0',
              item_body['total_fornecimento'] == 1000 and item_body['total_mao_obra'] == 0
              and item_body['total_material'] == 0,
              f"got {item_body.get('total_fornecimento')}/{item_body.get('total_mao_obra')}/{item_body.get('total_material')}")

        servico = Servico.query.get(item_body['servico_id'])
        check('card Servico nasce com valor_global_material = 1000 (nao zerado)',
              servico is not None and servico.valor_global_material == 1000
              and servico.valor_global_mao_de_obra == 0,
              f'servico={servico.to_dict() if servico else None}')

        print('\n=== fornecimento: resumo do orcamento-eng inclui o bucket ===')
        # GET /orcamento-eng usa `= ANY(:ids)` (sintaxe so-Postgres) numa query de boletos
        # nao relacionada a este fix -- incompatibilidade pre-existente do SQLite do smoke,
        # nao do codigo alterado aqui. Testamos a agregacao diretamente via calcular_totais().
        totais_direto = OrcamentoEngItem.query.get(item_body['id']).calcular_totais()
        check('calcular_totais() bate com o resumo esperado',
              totais_direto['total_fornecimento'] == 1000 and totais_direto['total'] == 1000,
              f'got {totais_direto}')

        print('\n=== fornecimento: editar item recalcula sem zerar ===')
        r = c.put(f'/obras/{obra_id}/orcamento-eng/itens/{item_body["id"]}', headers=h_master, json={
            'preco_unitario': 600,
        })
        check('PUT item -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        db.session.refresh(servico)
        check('valor_global_material atualizado p/ 1200',
              servico.valor_global_material == 1200, f'got {servico.valor_global_material}')

        print('\n=== cronograma <-> orcamento: sync remove orfao, preserva manual ===')
        etapa2 = OrcamentoEngEtapa(obra_id=obra_id, codigo='02', nome='FUNDACAO', ordem=1)
        db.session.add(etapa2)
        db.session.commit()

        cron_vinculado = CronogramaObra(
            obra_id=obra_id, servico_nome='FUNDACAO', ordem=1,
            data_inicio=hoje, data_fim_prevista=hoje + timedelta(days=10),
            tipo_medicao='percentual', percentual_conclusao=0,
        )
        cron_manual = CronogramaObra(
            obra_id=obra_id, servico_nome='Servico manual sem vinculo', ordem=2,
            data_inicio=hoje, data_fim_prevista=hoje + timedelta(days=10),
            tipo_medicao='percentual', percentual_conclusao=0,
        )
        db.session.add_all([cron_vinculado, cron_manual])
        db.session.commit()
        db.session.execute(db.text(
            "UPDATE cronograma_obra SET orcamento_etapa_id = :eid WHERE id = :cid"
        ), {"eid": etapa2.id, "cid": cron_vinculado.id})
        db.session.commit()

        # Apaga a etapa de origem no orcamento -> cron_vinculado fica orfao
        db.session.delete(etapa2)
        db.session.commit()

        r = c.get(f'/obras/{obra_id}/cronograma/sincronizar-orcamento', headers=h_master)
        check('GET orfaos -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        orfaos_body = json.loads(r.data)
        check('detecta exatamente 1 orfao (o vinculado)',
              orfaos_body['total'] == 1 and orfaos_body['orfaos'][0]['id'] == cron_vinculado.id,
              f'got {orfaos_body}')

        r = c.post(f'/obras/{obra_id}/cronograma/sincronizar-orcamento', headers=h_master)
        check('POST sincronizar -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        check('removidos = 1', json.loads(r.data)['removidos'] == 1, f'{r.data}')

        restantes = CronogramaObra.query.filter_by(obra_id=obra_id).all()
        check('item orfao removido, item manual preservado',
              len(restantes) == 1 and restantes[0].id == cron_manual.id,
              f'got {[c.servico_nome for c in restantes]}')

        print('\n=== cronograma: limpar restrito a master/administrador ===')
        r = c.delete(f'/obras/{obra_id}/cronograma/limpar', headers=h_op)
        check('operador -> 403 ao tentar limpar', r.status_code == 403, f'{r.status_code}: {r.data[:300]}')

        r = c.delete(f'/obras/{obra_id}/cronograma/limpar', headers=h_master)
        check('master -> 200 ao limpar', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        check('cronograma vazio apos limpar',
              CronogramaObra.query.filter_by(obra_id=obra_id).count() == 0)

print(f'\n{"="*50}\nPASS: {len(PASS)}  FAIL: {len(FAIL)}')
if FAIL:
    print('Falhas:', FAIL)
    sys.exit(1)
sys.exit(0)
