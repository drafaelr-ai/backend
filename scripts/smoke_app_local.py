"""
Smoke test local da MONTAGEM do app principal — a única suíte que importa
app.py de verdade.

Por que existe: todos os outros smokes montam um Flask próprio com os
blueprints que querem testar. Nenhum importa app.py. O resultado é que o
registro de blueprints, as camadas de CORS, o catch-all de OPTIONS e as
rotas de health nunca eram exercitados por teste nenhum — e foi exatamente
aí que doeu o Incidente 3 (import órfão em routes/obras.py: o Fly dizia
"2/2 machines healthy" e toda requisição dava 500).

O que se testa aqui: que app.py importa e monta, que /health responde 200
sem token, que /health/db reporta o banco, que rota de módulo continua
exigindo JWT e que todos os blueprints esperados estão registrados.

Banco e migração: auto_migration roda psycopg2 direto no Postgres no import
de app.py, então aqui ele é substituído por stub e o banco é SQLite em
memória. O que se valida é a montagem do app, não a DDL — DDL quem pega é
o health check do Fly no boot.

Uso: cd backend && python scripts/smoke_app_local.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('JWT_SECRET_KEY', 'smoke-app-secret')
os.environ.setdefault('DB_PASSWORD', 'smoke-app-dummy')

# Stub antes de importar app.py: run_auto_migration() roda no nível do módulo
# e conectaria no Postgres de produção.
_stub = types.ModuleType('auto_migration')
_stub.run_auto_migration = lambda: None
sys.modules['auto_migration'] = _stub

import config  # noqa: E402

config._build_database_url = lambda: 'sqlite:///:memory:'
# As connect_args do Config são de psycopg2 e o SQLite não as aceita.
config.Config.SQLALCHEMY_ENGINE_OPTIONS = {}

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


print('\n=== import e montagem do app ===')
import app as app_module  # noqa: E402

flask_app = app_module.app
check('app.py importa e expõe o objeto app', flask_app is not None)

rotas = {str(r.rule) for r in flask_app.url_map.iter_rules()}
check('rota /health registrada', '/health' in rotas)
check('rota /health/db registrada', '/health/db' in rotas)

blueprints_esperados = {
    'frota', 'solicitacoes', 'notificacoes', 'diario', 'obras',
}
registrados = set(flask_app.blueprints)
faltando = blueprints_esperados - registrados
check('blueprints principais registrados', not faltando, f'faltando: {faltando}')
print(f'  ({len(registrados)} blueprints, {len(rotas)} rotas)')

with flask_app.test_client() as c:
    print('\n=== health: liveness não toca o banco ===')
    r = c.get('/health')
    corpo = r.get_json() or {}
    check('GET /health sem token -> 200', r.status_code == 200,
          f'got {r.status_code}: {r.data[:160]}')
    check('corpo do health diz que está de pé', corpo.get('status') == 'healthy',
          f'got {corpo}')

    # É esta rota que o Fly usa como health check. Se ela passar a depender do
    # banco, uma instabilidade do Supabase tira a máquina da rotação do proxy
    # e derruba a API inteira por um problema que não é da aplicação.
    r = c.get('/health')
    check('/health responde igual na segunda chamada (sem rate limit)',
          r.status_code == 200, f'got {r.status_code}')

    print('\n=== health/db: readiness ===')
    r = c.get('/health/db')
    corpo = r.get_json() or {}
    check('GET /health/db -> 200 com banco de pé', r.status_code == 200,
          f'got {r.status_code}: {r.data[:160]}')
    check('corpo do health/db reporta o banco', corpo.get('db') == 'ok', f'got {corpo}')
    check('health/db não vaza string de conexão',
          'postgres' not in r.get_data(as_text=True).lower()
          and 'password' not in r.get_data(as_text=True).lower(),
          r.get_data(as_text=True)[:200])

    print('\n=== health não abriu o resto do app ===')
    r = c.get('/frota/veiculos')
    check('rota de módulo sem token continua barrada',
          r.status_code in (401, 422), f'got {r.status_code}')
    r = c.get('/obras')
    check('/obras sem token continua barrada',
          r.status_code in (401, 422), f'got {r.status_code}')

    # Documenta o comportamento que confunde o diagnóstico em produção: o
    # catch-all de OPTIONS faz caminho inexistente responder 405, não 404.
    # É por isso que o passo pós-deploy compara com 200 explicitamente.
    r = c.post('/rota-que-nao-existe')
    check('caminho inexistente responde 405 (catch-all de OPTIONS), não 404',
          r.status_code == 405, f'got {r.status_code}')

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
