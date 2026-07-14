"""
Smoke test local de acessos por módulo — sem banco real (SQLite in-memory).

Cobre: /login (claim modulos + user dict), GET /me, PUT /admin/users/<id>/modulos,
restrições de role master (único master) e gating before_request do /rh.

Uso: cd backend && python scripts/smoke_modulos_local.py
"""
import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token
import jwt as pyjwt

from extensions import db, jwt, limiter
import models  # noqa: F401
from models import User, Obra
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.rh import rh_bp

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
app.config['RATELIMIT_ENABLED'] = False
db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(rh_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'categoria_mo', 'funcionario',
    'pagamento_salario', 'encargo', 'notificacao',
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

    master = User(username='admin_principal', role='master')
    master.set_password('smoke123')
    comum = User(username='comum_smoke', role='comum', modulos_permitidos=['obras', 'rh'])
    comum.set_password('smoke123')
    sem_rh = User(username='sem_rh_smoke', role='comum', modulos_permitidos=['obras'])
    sem_rh.set_password('smoke123')
    db.session.add_all([master, comum, sem_rh])
    db.session.commit()
    master_id, comum_id, sem_rh_id = master.id, comum.id, sem_rh.id

    with app.test_client() as c:
        print('\n=== /login (claim + user dict) ===')
        r = c.post('/login', json={'username': 'comum_smoke', 'password': 'smoke123'})
        check('POST /login -> 200', r.status_code == 200)
        body = json.loads(r.data)
        check('login: user.modulos_permitidos presente',
              body['user'].get('modulos_permitidos') == ['obras', 'rh'])
        claims = pyjwt.decode(body['access_token'], 'smoke-test-secret', algorithms=['HS256'])
        check('login: claim modulos no JWT', claims.get('modulos') == ['obras', 'rh'])
        check('login: claim username no JWT', claims.get('username') == 'comum_smoke')
        token_comum = body['access_token']

        # login case-insensitive + trim
        r = c.post('/login', json={'username': '  COMUM_smoke ', 'password': 'smoke123'})
        check('login case-insensitive + trim -> 200', r.status_code == 200, f'got {r.status_code}')

        r = c.post('/login', json={'username': 'admin_principal', 'password': 'smoke123'})
        body = json.loads(r.data)
        check('login master: claim modulos null (todos)',
              pyjwt.decode(body['access_token'], 'smoke-test-secret',
                           algorithms=['HS256']).get('modulos') is None)
        token_master = body['access_token']

        h_master = {'Authorization': f'Bearer {token_master}'}
        h_comum = {'Authorization': f'Bearer {token_comum}'}
        h_sem_rh = {'Authorization': f'Bearer {create_access_token(identity=str(sem_rh_id))}'}

        print('\n=== GET /me ===')
        r = c.get('/me', headers=h_comum)
        check('GET /me -> 200', r.status_code == 200)
        check('GET /me: modulos_permitidos', json.loads(r.data).get('modulos_permitidos') == ['obras', 'rh'])
        r = c.get('/me')
        check('GET /me sem token -> 401', r.status_code == 401)
        h_fantasma = {'Authorization': f'Bearer {create_access_token(identity="99999")}'}
        r = c.get('/me', headers=h_fantasma)
        check('GET /me user inexistente -> 401', r.status_code == 401, f'got {r.status_code}')

        print('\n=== PUT /admin/users/<id>/modulos ===')
        r = c.put(f'/admin/users/{sem_rh_id}/modulos', json={'modulos': ['obras', 'frota']}, headers=h_master)
        check('master define modulos -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        check('resposta traz user atualizado',
              json.loads(r.data)['user']['modulos_permitidos'] == ['frota', 'obras'])
        r = c.put(f'/admin/users/{sem_rh_id}/modulos', json={'modulos': None}, headers=h_master)
        check('master define null (todos) -> 200', r.status_code == 200)
        check('null persistido', json.loads(r.data)['user']['modulos_permitidos'] is None)
        r = c.put(f'/admin/users/{sem_rh_id}/modulos', json={'modulos': ['obras']}, headers=h_master)
        check('volta a restringir -> 200', r.status_code == 200)
        r = c.put(f'/admin/users/{sem_rh_id}/modulos', json={'modulos': ['xpto']}, headers=h_master)
        check('módulo inválido -> 400', r.status_code == 400)
        r = c.put(f'/admin/users/{sem_rh_id}/modulos', json={'modulos': 'rh'}, headers=h_master)
        check('tipo errado -> 400', r.status_code == 400)
        r = c.put(f'/admin/users/{master_id}/modulos', json={'modulos': ['obras']}, headers=h_master)
        check('alvo master -> 400', r.status_code == 400)
        r = c.put(f'/admin/users/{sem_rh_id}/modulos', json={'modulos': ['rh']}, headers=h_comum)
        check('comum chama -> 403', r.status_code == 403)

        print('\n=== PUT /me/senha ===')
        r = c.put('/me/senha', json={'senha_atual': 'smoke123', 'senha_nova': 'novaSenha456'}, headers=h_comum)
        check('trocar senha -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        r = c.post('/login', json={'username': 'comum_smoke', 'password': 'novaSenha456'})
        check('login com senha nova -> 200', r.status_code == 200)
        r = c.post('/login', json={'username': 'comum_smoke', 'password': 'smoke123'})
        check('login com senha antiga -> 401', r.status_code == 401)
        h_comum = {'Authorization': f'Bearer {json.loads(c.post("/login", json={"username": "comum_smoke", "password": "novaSenha456"}).data)["access_token"]}'}

        r = c.put('/me/senha', json={'senha_atual': 'errada', 'senha_nova': 'outraSenha789'}, headers=h_comum)
        check('senha atual errada -> 400', r.status_code == 400, f'got {r.status_code}')
        r = c.put('/me/senha', json={'senha_atual': 'novaSenha456', 'senha_nova': '123'}, headers=h_comum)
        check('senha nova curta -> 400', r.status_code == 400)
        r = c.put('/me/senha', json={'senha_atual': 'novaSenha456', 'senha_nova': 'novaSenha456'}, headers=h_comum)
        check('senha nova igual a atual -> 400', r.status_code == 400)
        r = c.put('/me/senha', json={'senha_atual': 'novaSenha456', 'senha_nova': 'maisUmaSenha000'})
        check('sem token -> 401', r.status_code == 401)

        print('\n=== único master ===')
        r = c.post('/admin/users', json={'username': 'novo_m', 'password': 'x12345', 'role': 'master'}, headers=h_master)
        check('criar user master -> 400', r.status_code == 400, f'got {r.status_code}')
        r = c.post('/admin/users', json={'username': 'novo_adm', 'password': 'x12345', 'role': 'administrador'}, headers=h_master)
        check('criar administrador -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:200]}')
        novo_adm_id = json.loads(r.data)['id']
        r = c.patch(f'/admin/users/{novo_adm_id}/role', json={'role': 'master'}, headers=h_master)
        check('promover a master -> 400', r.status_code == 400)
        r = c.patch(f'/admin/users/{novo_adm_id}/role', json={'role': 'comum'}, headers=h_master)
        check('rebaixar a comum -> 200', r.status_code == 200)
        r = c.patch(f'/admin/users/{master_id}/role', json={'role': 'comum'}, headers=h_master)
        check('alterar role do master -> 400', r.status_code == 400)
        r = c.delete(f'/admin/users/{master_id}', headers=h_master)
        check('excluir master -> 400/403', r.status_code in (400, 403))

        print('\n=== gating /rh ===')
        r = c.get('/rh/categorias', headers=h_comum)
        check('comum com rh -> 200', r.status_code == 200, f'got {r.status_code}')
        r = c.get('/rh/categorias', headers=h_sem_rh)
        check('comum sem rh -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.get('/rh/categorias', headers=h_master)
        check('master -> 200', r.status_code == 200)
        r = c.get('/rh/categorias')
        check('sem token -> 401', r.status_code == 401)

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
