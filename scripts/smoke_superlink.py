"""Smoke test — Superlink routes (Main)

Testa: POST autenticado, GET público sem token, GET inexistente, POST sem token.
Zero FAIL = pode deployar.
"""
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# Carregar .env antes de qualquer import que precise de env vars
_env_path = os.path.join(BASE, '.env')
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                os.environ.setdefault(_k.strip(), _v.strip())

from app import create_app
from flask_jwt_extended import create_access_token

app = create_app()

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    status = 'PASS' if condition else 'FAIL'
    print(f"[{status}] {label}")
    if condition:
        PASS += 1
    else:
        FAIL += 1


token_pub = None

with app.app_context():
    with app.test_client() as c:
        # 1. POST autenticado por múltiplos usuários
        for uid in (1, 3, 12):
            jwt = create_access_token(identity=str(uid))
            r = c.post(
                '/superlink',
                headers={'Authorization': f'Bearer {jwt}'},
                json={
                    'titulo': f'Smoke uid={uid}',
                    'itens': [{'descricao': 'Item x', 'valor': 10.0,
                                'contexto': 'Obra Y', 'forma': 'pix',
                                'pix_chave': 'teste@obraly.com'}],
                },
            )
            check(f'POST /superlink uid={uid} → 201', r.status_code == 201)
            if r.status_code == 201:
                token_pub = (r.get_json() or {}).get('token')

        # 2. GET PÚBLICO sem Authorization (caso crítico)
        if token_pub:
            r = c.get(f'/superlink/{token_pub}')
            check('GET /superlink/<token> sem Authorization → 200', r.status_code == 200)
        else:
            check('GET público (token_pub não gerado, pulado)', False)

        # 3. GET token inexistente → 404
        r = c.get('/superlink/tokeninexistente123abc')
        check('GET /superlink/inexistente → 404', r.status_code == 404)

        # 4. POST sem token → 401 ou 422 (NÃO pode criar sem auth)
        r = c.post('/superlink', json={'titulo': 'x', 'itens': []})
        check('POST /superlink sem token → 401/422', r.status_code in (401, 422))

        # 5. POST com itens inválidos (forma=pix sem pix_chave) → 400
        jwt = create_access_token(identity='1')
        r = c.post(
            '/superlink',
            headers={'Authorization': f'Bearer {jwt}'},
            json={
                'titulo': 'Inválido',
                'itens': [{'descricao': 'x', 'valor': 5.0, 'forma': 'pix'}],
            },
        )
        check('POST com forma=pix sem pix_chave → 400', r.status_code == 400)

print(f"\n{'='*40}")
print(f"Resultado: {PASS} PASS  {FAIL} FAIL")
print('='*40)
sys.exit(0 if FAIL == 0 else 1)
