"""Smoke test isolado para Almoxarifado e Ponto Eletrônico.

Executa em SQLite em memória e não acessa o banco de produção.
Uso: cd backend && python scripts/smoke_almoxarifado_ponto_local.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
import models  # noqa: F401  # Registra os modelos no metadata do SQLAlchemy.
from models.categoria_mo import CategoriaMO
from models.funcionario import Funcionario
from models.obra import Obra
from models.user import User
from routes.almoxarifado import almoxarifado_bp
from routes.rh import rh_bp


app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JWT_SECRET_KEY='smoke-almoxarifado-ponto-key-with-32-bytes',
    RATELIMIT_ENABLED=False,
)
db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)
app.register_blueprint(almoxarifado_bp)
app.register_blueprint(rh_bp)

_TABELAS = [
    'user', 'user_obra_association', 'obra', 'categoria_mo', 'funcionario',
    'almoxarifado_item', 'almoxarifado_movimentacao', 'ponto_marcacao',
]
_FALHAS = []


def check(nome, condicao, recebido):
    if condicao:
        print(f'  PASS  {nome} ({recebido})')
        return
    _FALHAS.append(nome)
    print(f'  FAIL  {nome} ({recebido})')


with app.app_context():
    tabelas = [db.metadata.tables[nome] for nome in _TABELAS]
    db.metadata.create_all(bind=db.engine, tables=tabelas)

    obra_a = Obra(nome='Obra A')
    obra_b = Obra(nome='Obra B')
    categoria = CategoriaMO(nome='Pedreiro')
    almox = User(username='almox_smoke', role='comum', modulos_permitidos=['almoxarifado'])
    almox.set_password('senha-smoke')
    almox.obras_permitidas.append(obra_a)
    rh = User(username='rh_smoke', role='comum', modulos_permitidos=['rh'])
    rh.set_password('senha-smoke')
    rh.obras_permitidas.append(obra_a)
    sem_modulo = User(username='sem_modulo_smoke', role='comum', modulos_permitidos=['obras'])
    sem_modulo.set_password('senha-smoke')
    sem_modulo.obras_permitidas.append(obra_a)
    db.session.add_all([obra_a, obra_b, categoria, almox, rh, sem_modulo])
    db.session.flush()

    funcionario_a = Funcionario(nome='Ana', categoria_id=categoria.id, obra_id=obra_a.id, salario=2000)
    funcionario_b = Funcionario(nome='Beto', categoria_id=categoria.id, obra_id=obra_b.id, salario=2000)
    db.session.add_all([funcionario_a, funcionario_b])
    db.session.commit()

    headers_almox = {'Authorization': f'Bearer {create_access_token(identity=str(almox.id))}'}
    headers_rh = {'Authorization': f'Bearer {create_access_token(identity=str(rh.id))}'}
    headers_sem_modulo = {'Authorization': f'Bearer {create_access_token(identity=str(sem_modulo.id))}'}

    with app.test_client() as client:
        response = client.get('/almoxarifado/dashboard')
        check('almox sem JWT retorna 401', response.status_code == 401, response.status_code)
        response = client.get('/almoxarifado/dashboard', headers=headers_sem_modulo)
        check('módulo não liberado retorna 403', response.status_code == 403, response.status_code)
        response = client.get('/rh/ponto/folha?competencia=2026-07&funcionario_id=1', headers=headers_almox)
        check('usuário almox não acessa ponto RH', response.status_code == 403, response.status_code)

        response = client.post('/almoxarifado/itens', headers=headers_almox, json={
            'codigo': 'CAP-01', 'nome': 'Capacete', 'categoria': 'epi', 'unidade': 'un', 'estoque_minimo': 2,
        })
        check('cadastro de item autorizado', response.status_code == 201, response.status_code)
        item_id = response.get_json()['id']
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': item_id, 'tipo': 'entrada', 'quantidade': 5,
        })
        check('entrada de estoque autorizada', response.status_code == 201, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': item_id, 'tipo': 'saida', 'quantidade': 1, 'obra_id': obra_b.id,
        })
        check('saída para obra fora do escopo retorna 403', response.status_code == 403, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': item_id, 'tipo': 'saida', 'quantidade': 1, 'funcionario_id': funcionario_b.id,
        })
        check('saída para funcionário fora do escopo retorna 403', response.status_code == 403, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': item_id, 'tipo': 'saida', 'quantidade': 2, 'funcionario_id': funcionario_a.id,
        })
        check('saída para funcionário autorizado', response.status_code == 201, response.status_code)
        saldo = client.get('/almoxarifado/itens', headers=headers_almox).get_json()[0]['estoque_atual']
        check('saldo histórico é consistente', saldo == 3, saldo)

        response = client.post('/rh/ponto/marcacoes', headers=headers_rh, json={
            'funcionario_id': funcionario_a.id, 'data_hora': '2026-07-22T08:00',
            'tipo': 'entrada', 'origem': 'manual',
        })
        check('batida manual autorizada no RH', response.status_code == 201, response.status_code)
        response = client.post('/rh/ponto/marcacoes', headers=headers_rh, json={
            'funcionario_id': funcionario_a.id, 'data_hora': '2026-07-22T17:00',
            'tipo': 'saida', 'origem': 'relogio', 'referencia_externa': 'controlid-42',
        })
        check('painel manual não pode forjar origem relógio', response.status_code == 403, response.status_code)
        response = client.get(
            f'/rh/ponto/folha?competencia=2026-07&funcionario_id={funcionario_a.id}', headers=headers_rh,
        )
        check('folha do colaborador autorizado', response.status_code == 200, response.status_code)
        response = client.get(
            f'/rh/ponto/folha?competencia=2026-07&funcionario_id={funcionario_b.id}', headers=headers_rh,
        )
        check('RH sem acesso à obra não lê folha externa', response.status_code == 403, response.status_code)

if _FALHAS:
    print(f'\nFalhas: {", ".join(_FALHAS)}')
    raise SystemExit(1)

print('\nTodos os cenários de Almoxarifado e Ponto passaram.')
