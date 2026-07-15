"""
Smoke test local do rate limit em /login e /sso do backend admin — sem
banco real (SQLite in-memory).

Uso: cd backend && python scripts/smoke_rate_limit_admin_local.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('JWT_SECRET_KEY_ADMIN', 'smoke-admin-secret')
os.environ.setdefault('DATABASE_URL_ADMIN', 'sqlite:///:memory:')

from app_admin import create_app
from config_admin import DevelopmentConfig
from extensions_admin import db

app = create_app(DevelopmentConfig.from_env())

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
    db.create_all()

    with app.test_client() as c:
        print('\n=== POST /login (limite: 10/min) ===')
        codes = [c.post('/login', json={'username': 'x', 'password': 'y'}).status_code
                 for _ in range(11)]
        check('primeiras 10 chamadas != 429', 429 not in codes[:10], f'codes={codes[:10]}')
        check('11ª chamada -> 429', codes[10] == 429, f'got {codes[10]}')

    with app.test_client() as c2:
        print('\n=== POST /sso (limite: 10/min, IP separado) ===')
        codes = [c2.post('/sso').status_code for _ in range(11)]
        check('primeiras 10 chamadas != 429', 429 not in codes[:10], f'codes={codes[:10]}')
        check('11ª chamada -> 429', codes[10] == 429, f'got {codes[10]}')

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
