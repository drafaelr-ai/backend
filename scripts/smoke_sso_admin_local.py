"""
Smoke test local do SSO do backend admin — sem banco real (SQLite in-memory).

Forja tokens do backend MAIN com PyJWT (assinados com JWT_SECRET_KEY_MAIN fake)
e valida a troca por token_admin via POST /sso.

Uso: cd backend && python scripts/smoke_sso_admin_local.py
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('JWT_SECRET_KEY_ADMIN', 'smoke-admin-secret')
os.environ.setdefault('DATABASE_URL_ADMIN', 'sqlite:///:memory:')
os.environ.setdefault('JWT_SECRET_KEY_MAIN', 'smoke-main-secret')

import jwt as pyjwt

from app_admin import create_app
from config_admin import DevelopmentConfig
from extensions_admin import db, limiter
from models_admin import Usuario

app = create_app(DevelopmentConfig.from_env())
# Este smoke dispara muitas chamadas /sso seguidas do mesmo IP de teste —
# desliga o rate limit (testado separadamente, ver smoke_rate_limit_admin_local.py)
# pra não confundir os dois. app.config['RATELIMIT_ENABLED'] só é lido em
# init_app(), que já rodou dentro de create_app() — setar aqui é tarde demais,
# por isso desligamos via o atributo runtime do próprio Limiter.
limiter.enabled = False

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


def token_main(username, role='comum', modulos='__omit__', secret='smoke-main-secret',
               expira_em=timedelta(hours=1)):
    agora = datetime.now(timezone.utc)
    payload = {
        'sub': '1',
        'username': username,
        'role': role,
        'iat': agora,
        'exp': agora + expira_em,
    }
    if modulos != '__omit__':
        payload['modulos'] = modulos
    return pyjwt.encode(payload, secret, algorithm='HS256')


with app.app_context():
    db.create_all()

    legado = Usuario(username='legado_smoke', nome='Legado', role='operador')
    legado.set_password('smoke123')
    inativo = Usuario(username='inativo_smoke', nome='Inativo', role='admin', ativo=False)
    inativo.set_password('smoke123')
    db.session.add_all([legado, inativo])
    db.session.commit()

    with app.test_client() as c:
        def sso(tok=None):
            headers = {'Authorization': f'Bearer {tok}'} if tok else {}
            return c.post('/sso', headers=headers)

        print('\n=== casos felizes ===')
        r = sso(token_main('admin_principal', role='master', modulos=None))
        check('master -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        body = json.loads(r.data)
        check('retorna access_token', bool(body.get('access_token')))
        # SSO auto-cria com role='operador' (escopo restrito) — promoção pra
        # 'admin' é manual, feita por um admin existente no painel de usuários.
        check('auto-criado com role operador', body['user']['role'] == 'operador')
        tok_admin = body['access_token']

        r = c.get('/me', headers={'Authorization': f'Bearer {tok_admin}'})
        check('token_admin funciona no GET /me', r.status_code == 200,
              f'got {r.status_code}')

        r = sso(token_main('admin_principal', role='master', modulos=None))
        check('2ª chamada -> 200 (não duplica)', r.status_code == 200)
        with app.app_context():
            n = Usuario.query.filter_by(username='admin_principal').count()
        check('admin_principal existe 1x', n == 1, f'got {n}')

        r = sso(token_main('user_admin_ok', role='comum', modulos=['admin', 'obras']))
        check("comum com modulos=['admin'] -> 200", r.status_code == 200, f'got {r.status_code}')

        r = sso(token_main('user_null_ok', role='comum', modulos=None))
        check('comum com modulos=null (todos) -> 200', r.status_code == 200)

        r = sso(token_main('legado_smoke', role='comum', modulos=['admin']))
        check('usuário legado casado por username -> 200', r.status_code == 200)
        check('legado mantém role operador', json.loads(r.data)['user']['role'] == 'operador')

        print('\n=== casos de recusa ===')
        r = sso(token_main('user_sem_admin', role='comum', modulos=['obras', 'rh']))
        check("comum com modulos=['obras'] -> 403", r.status_code == 403, f'got {r.status_code}')

        r = sso(token_main('user_adm_restrito', role='administrador', modulos=['obras']))
        check('administrador sem admin na lista -> 403', r.status_code == 403)

        r = sso(token_main('atacante', role='master', modulos=None, secret='chave-errada'))
        check('assinatura inválida -> 401', r.status_code == 401, f'got {r.status_code}')

        r = sso(token_main('expirado', role='master', modulos=None, expira_em=timedelta(hours=-1)))
        check('token expirado -> 401', r.status_code == 401)

        r = sso(token_main('', role='master', modulos=None))
        check('sem username -> 401', r.status_code == 401)

        r = sso()
        check('sem header -> 401', r.status_code == 401)

        r = sso(token_main('inativo_smoke', role='comum', modulos=['admin']))
        check('usuário patrimonial inativo -> 403', r.status_code == 403, f'got {r.status_code}')

        print('\n=== sem JWT_SECRET_KEY_MAIN ===')
        salvo = os.environ.pop('JWT_SECRET_KEY_MAIN')
        r = sso(token_main('qualquer', role='master', modulos=None, secret=salvo))
        check('env ausente -> 503', r.status_code == 503, f'got {r.status_code}')
        os.environ['JWT_SECRET_KEY_MAIN'] = salvo

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
