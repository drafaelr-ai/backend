"""
Smoke test local para blueprints do admin (Batch D + E + F).
Testa 9 blueprints sem banco real — usa SQLite in-memory.

Uso: cd backend && python scripts/smoke_admin_blueprints_local.py
"""
import os
import sys
import json
from datetime import date

os.environ.setdefault('JWT_SECRET_KEY_ADMIN', 'smoke-test-secret')
os.environ.setdefault('DATABASE_URL_ADMIN', 'sqlite:///:memory:')

# Usa o factory — smoke real do app_admin_new
from app_admin import create_app
from config_admin import DevelopmentConfig
from extensions_admin import db
from models_admin import Usuario, Categoria
from services_admin import criar_categorias_padrao

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
    criar_categorias_padrao()

    admin = Usuario(username='admin_smoke', nome='Admin', email='admin@smoke.test', role='admin')
    admin.set_password('smoke123')
    operador = Usuario(username='op_smoke', nome='Operador', role='operador')
    operador.set_password('smoke123')
    db.session.add_all([admin, operador])
    db.session.commit()

    with app.test_client() as c:
        print('\n=== health_bp ===')
        r = c.get('/')
        check('GET / -> 200', r.status_code == 200)
        check('GET / body has status=online', json.loads(r.data).get('status') == 'online')

        r = c.get('/health')
        check('GET /health -> 200', r.status_code == 200)
        check('GET /health body has module=admin', json.loads(r.data).get('module') == 'admin')

        print('\n=== auth_admin_bp ===')
        r = c.post('/login', json={'username': 'admin_smoke', 'password': 'smoke123'})
        check('POST /login admin -> 200', r.status_code == 200)
        token_admin = json.loads(r.data).get('access_token', '')
        check('POST /login retorna access_token', bool(token_admin))

        r = c.post('/login', json={'username': 'op_smoke', 'password': 'smoke123'})
        check('POST /login operador -> 200', r.status_code == 200)
        token_op = json.loads(r.data).get('access_token', '')

        r = c.post('/login', json={'username': 'admin_smoke', 'password': 'errada'})
        check('POST /login senha errada -> 401', r.status_code == 401)

        r = c.get('/me', headers={'Authorization': f'Bearer {token_admin}'})
        check('GET /me com token -> 200', r.status_code == 200)
        check('GET /me retorna username correto', json.loads(r.data).get('username') == 'admin_smoke')

        r = c.get('/me')
        check('GET /me sem token -> 401', r.status_code == 401)

        print('\n=== usuarios_admin_bp ===')
        headers_admin = {'Authorization': f'Bearer {token_admin}'}
        headers_op = {'Authorization': f'Bearer {token_op}'}

        r = c.get('/usuarios', headers=headers_admin)
        check('GET /usuarios admin -> 200', r.status_code == 200)

        r = c.get('/usuarios', headers=headers_op)
        check('GET /usuarios operador -> 403', r.status_code == 403)

        r = c.post('/usuarios', json={
            'username': 'novo_smoke', 'password': 'senha123', 'nome': 'Novo'
        }, headers=headers_admin)
        check('POST /usuarios admin -> 201', r.status_code == 201)
        novo_id = json.loads(r.data).get('user', {}).get('id')

        r = c.put(f'/usuarios/{novo_id}', json={'nome': 'Novo Editado'}, headers=headers_admin)
        check('PUT /usuarios/<id> admin -> 200', r.status_code == 200)

        r = c.post(f'/usuarios/{novo_id}/reset-senha',
                   json={'nova_senha': 'novaSenha123'}, headers=headers_admin)
        check('POST /reset-senha -> 200', r.status_code == 200)

        r = c.delete(f'/usuarios/{novo_id}', headers=headers_admin)
        check('DELETE /usuarios/<id> admin -> 200', r.status_code == 200)

        print('\n=== categorias_admin_bp ===')
        r = c.get('/categorias', headers=headers_admin)
        check('GET /categorias com token -> 200', r.status_code == 200)
        cats = json.loads(r.data)
        check('GET /categorias retorna lista nao-vazia', len(cats) > 0)

        r = c.get('/categorias')
        check('GET /categorias sem token -> 401', r.status_code == 401)

        print('\n=== imoveis_admin_bp ===')
        r = c.get('/imoveis', headers=headers_admin)
        check('GET /imoveis sem dados -> 200', r.status_code == 200)
        check('GET /imoveis retorna lista', isinstance(json.loads(r.data), list))

        r = c.get('/imoveis')
        check('GET /imoveis sem token -> 401', r.status_code == 401)

        r = c.post('/imoveis', json={
            'nome': 'Apto Smoke', 'tipo': 'apartamento',
            'status': 'proprio', 'cidade': 'SP', 'estado': 'SP'
        }, headers=headers_admin)
        check('POST /imoveis admin -> 201', r.status_code == 201)
        imovel_id = json.loads(r.data).get('id')
        check('POST /imoveis retorna id', bool(imovel_id))

        r = c.post('/imoveis', json={
            'nome': 'Apto Op', 'tipo': 'apartamento', 'status': 'alugado'
        }, headers=headers_op)
        check('POST /imoveis operador -> 201', r.status_code == 201)
        imovel_op_id = json.loads(r.data).get('id')

        r = c.get(f'/imoveis/{imovel_id}', headers=headers_admin)
        check('GET /imoveis/<id> admin -> 200', r.status_code == 200)

        r = c.get(f'/imoveis/{imovel_id}', headers=headers_op)
        check('GET /imoveis/<id> operador imovel alheio -> 403', r.status_code == 403)

        r = c.get(f'/imoveis/{imovel_op_id}', headers=headers_op)
        check('GET /imoveis/<id> operador imovel proprio -> 200', r.status_code == 200)

        r = c.put(f'/imoveis/{imovel_id}', json={'nome': 'Apto Smoke Editado'}, headers=headers_admin)
        check('PUT /imoveis/<id> admin -> 200', r.status_code == 200)

        r = c.put(f'/imoveis/{imovel_id}', json={'nome': 'Tentativa'}, headers=headers_op)
        check('PUT /imoveis/<id> operador imovel alheio -> 403', r.status_code == 403)

        print('\n=== lancamentos_admin_bp ===')
        cat_id = cats[0]['id'] if cats else None
        hoje_iso = date.today().isoformat()

        r = c.get('/lancamentos', headers=headers_admin)
        check('GET /lancamentos admin -> 200', r.status_code == 200)

        r = c.get('/lancamentos')
        check('GET /lancamentos sem token -> 401', r.status_code == 401)

        r = c.post('/lancamentos', json={
            'imovel_id': imovel_id,
            'categoria_id': cat_id,
            'descricao': 'Aluguel Smoke',
            'valor': 1500.00,
            'tipo': 'receita',
            'status': 'pago',
            'data_lancamento': hoje_iso,
        }, headers=headers_admin)
        check('POST /lancamentos admin -> 201', r.status_code == 201)
        lanc_id = json.loads(r.data).get('id')
        check('POST /lancamentos retorna id', bool(lanc_id))

        r = c.post('/lancamentos', json={
            'imovel_id': imovel_id,
            'categoria_id': cat_id,
            'descricao': 'Conta Smoke',
            'valor': 200.00,
            'tipo': 'despesa',
            'status': 'pendente',
            'data_lancamento': hoje_iso,
            'data_vencimento': hoje_iso,
        }, headers=headers_op)
        check('POST /lancamentos operador imovel alheio -> 403', r.status_code == 403)

        r = c.post('/lancamentos', json={
            'imovel_id': imovel_op_id,
            'categoria_id': cat_id,
            'descricao': 'Conta Op Smoke',
            'valor': 200.00,
            'tipo': 'despesa',
            'status': 'pendente',
            'data_lancamento': hoje_iso,
            'data_vencimento': hoje_iso,
        }, headers=headers_op)
        check('POST /lancamentos operador imovel proprio -> 201', r.status_code == 201)
        lanc_op_id = json.loads(r.data).get('id')

        r = c.put(f'/lancamentos/{lanc_id}', json={'descricao': 'Aluguel Editado'}, headers=headers_admin)
        check('PUT /lancamentos/<id> admin -> 200', r.status_code == 200)

        r = c.put(f'/lancamentos/{lanc_id}', json={'descricao': 'Tentativa'}, headers=headers_op)
        check('PUT /lancamentos/<id> operador lancamento alheio -> 403', r.status_code == 403)

        r = c.post(f'/lancamentos/{lanc_op_id}/pagar', json={
            'data_pagamento': hoje_iso
        }, headers=headers_op)
        check('POST /lancamentos/<id>/pagar operador proprio -> 200', r.status_code == 200)
        check('pagar: status=pago', json.loads(r.data).get('status') == 'pago')

        r = c.post(f'/lancamentos/{lanc_id}/pagar', json={}, headers=headers_op)
        check('POST /lancamentos/<id>/pagar operador alheio -> 403', r.status_code == 403)

        r = c.get('/alertas-vencimento', headers=headers_admin)
        check('GET /alertas-vencimento admin -> 200', r.status_code == 200)
        data_alertas = json.loads(r.data)
        check('alertas-vencimento tem chave vencidos', 'vencidos' in data_alertas)
        check('alertas-vencimento tem chave a_vencer', 'a_vencer' in data_alertas)

        r = c.get('/alertas-vencimento')
        check('GET /alertas-vencimento sem token -> 401', r.status_code == 401)

        print('\n=== dashboard_admin_bp ===')
        r = c.get('/dashboard', headers=headers_admin)
        check('GET /dashboard admin -> 200', r.status_code == 200)
        dash = json.loads(r.data)
        check('dashboard tem chave resumo', 'resumo' in dash)
        check('dashboard tem chave alertas', 'alertas' in dash)
        check('dashboard tem chave despesas_por_categoria', 'despesas_por_categoria' in dash)
        check('dashboard tem chave despesas_por_imovel', 'despesas_por_imovel' in dash)
        check('dashboard tem chave ultimos_lancamentos', 'ultimos_lancamentos' in dash)

        r = c.get('/dashboard', headers=headers_op)
        check('GET /dashboard operador -> 200', r.status_code == 200)
        dash_op = json.loads(r.data)
        check('dashboard operador: total_imoveis >= 0', dash_op['resumo']['total_imoveis'] >= 0)

        r = c.get('/dashboard')
        check('GET /dashboard sem token -> 401', r.status_code == 401)

        r = c.get('/dashboard?mes=1&ano=2025', headers=headers_admin)
        check('GET /dashboard com mes/ano -> 200', r.status_code == 200)
        check('dashboard periodo correto', json.loads(r.data)['periodo'] == {'mes': 1, 'ano': 2025})

        print('\n=== importar_obra_bp ===')
        r = c.post('/importar-obra', json={
            'obra_id': 42,
            'nome': 'Casa Importada Smoke',
            'tipo': 'casa',
            'cidade': 'Curitiba',
            'estado': 'PR',
            'custo_total': 350000.00,
        }, headers=headers_admin)
        check('POST /importar-obra admin -> 201', r.status_code == 201)
        imovel_importado = json.loads(r.data)
        check('importar-obra retorna imovel.id', bool(imovel_importado.get('imovel', {}).get('id')))
        check('importar-obra status=proprio', imovel_importado['imovel']['status'] == 'proprio')

        r = c.post('/importar-obra', json={'obra_id': 42, 'nome': 'Duplicata'}, headers=headers_admin)
        check('POST /importar-obra duplicado -> 400', r.status_code == 400)

        r = c.post('/importar-obra', json={'nome': 'Sem token'})
        check('POST /importar-obra sem token -> 401', r.status_code == 401)

        r = c.post('/importar-obra', json={
            'nome': 'Casa Op Smoke', 'tipo': 'casa', 'custo_total': 100000.00
        }, headers=headers_op)
        check('POST /importar-obra operador -> 201', r.status_code == 201)

        print('\n=== boletos_admin_bp ===')
        r = c.get(f'/imoveis/{imovel_id}/boletos', headers=headers_admin)
        check('GET /imoveis/<id>/boletos admin -> 200', r.status_code == 200)
        check('boletos retorna lista', isinstance(json.loads(r.data), list))

        r = c.get(f'/imoveis/{imovel_id}/boletos')
        check('GET /imoveis/<id>/boletos sem token -> 401', r.status_code == 401)

        r = c.get(f'/imoveis/{imovel_id}/boletos', headers=headers_op)
        check('GET /imoveis/<id>/boletos operador alheio -> 403', r.status_code == 403)

        r = c.post(f'/imoveis/{imovel_id}/boletos', json={
            'descricao': 'Boleto Smoke',
            'valor': 500.00,
            'data_vencimento': hoje_iso,
            'beneficiario': 'Condominio Smoke',
        }, headers=headers_admin)
        check('POST /imoveis/<id>/boletos admin -> 201', r.status_code == 201)
        boleto_id = json.loads(r.data).get('id')
        check('boleto retorna id', bool(boleto_id))

        r = c.post(f'/imoveis/{imovel_id}/boletos', json={
            'descricao': 'Boleto sem valor',
            'data_vencimento': hoje_iso,
        }, headers=headers_admin)
        check('POST /imoveis/<id>/boletos sem valor -> 400', r.status_code == 400)

        r = c.post(f'/imoveis/{imovel_op_id}/boletos', json={
            'descricao': 'Boleto Op Smoke',
            'valor': 200.00,
            'data_vencimento': hoje_iso,
        }, headers=headers_op)
        check('POST /imoveis/<id>/boletos operador proprio -> 201', r.status_code == 201)
        boleto_op_id = json.loads(r.data).get('id')

        r = c.get(f'/imoveis/{imovel_id}/boletos/resumo', headers=headers_admin)
        check('GET /imoveis/<id>/boletos/resumo admin -> 200', r.status_code == 200)
        resumo = json.loads(r.data)
        check('resumo tem total_pendente', 'total_pendente' in resumo)
        check('resumo tem total_pago', 'total_pago' in resumo)

        r = c.put(f'/imoveis/{imovel_id}/boletos/{boleto_id}', json={
            'descricao': 'Boleto Editado'
        }, headers=headers_admin)
        check('PUT /imoveis/<id>/boletos/<id> admin -> 200', r.status_code == 200)

        r = c.put(f'/imoveis/{imovel_id}/boletos/{boleto_id}', json={
            'descricao': 'Tentativa Op'
        }, headers=headers_op)
        check('PUT /imoveis/<id>/boletos/<id> operador alheio -> 403', r.status_code == 403)

        r = c.post(f'/imoveis/{imovel_op_id}/boletos/{boleto_op_id}/pagar', json={
            'data_pagamento': hoje_iso
        }, headers=headers_op)
        check('POST /boletos/<id>/pagar operador proprio -> 200', r.status_code == 200)
        check('boleto pago: status=Pago', json.loads(r.data).get('status') == 'Pago')

        r = c.post(f'/imoveis/{imovel_id}/boletos/{boleto_id}/pagar', json={}, headers=headers_op)
        check('POST /boletos/<id>/pagar operador alheio -> 403', r.status_code == 403)

        r = c.get(f'/imoveis/{imovel_id}/boletos/{boleto_id}/arquivo', headers=headers_admin)
        check('GET /boletos/<id>/arquivo sem pdf -> 404', r.status_code == 404)

        r = c.post(f'/imoveis/{imovel_id}/boletos/extrair-pdf', json={
            'arquivo_base64': ''
        }, headers=headers_admin)
        check('POST /boletos/extrair-pdf sem pdf -> 400', r.status_code == 400)

        # DELETE ao final
        r = c.delete(f'/imoveis/{imovel_id}/boletos/{boleto_id}', headers=headers_admin)
        check('DELETE /imoveis/<id>/boletos/<id> admin -> 200', r.status_code == 200)

        r = c.delete(f'/imoveis/{imovel_id}/boletos/{boleto_id}', headers=headers_op)
        check('DELETE /imoveis/<id>/boletos/<id> operador alheio -> 404', r.status_code in (403, 404))

        r = c.delete(f'/lancamentos/{lanc_id}', headers=headers_admin)
        check('DELETE /lancamentos/<id> admin -> 200', r.status_code == 200)

        r = c.delete(f'/imoveis/{imovel_id}', headers=headers_admin)
        check('DELETE /imoveis/<id> admin -> 200', r.status_code == 200)

        # Verifica contagem de rotas do factory
        print('\n=== factory route count ===')
        rotas = list(app.url_map.iter_rules())
        check('factory: >= 35 rotas registradas', len(rotas) >= 35,
              f'encontrado: {len(rotas)}')

print(f'\n{"="*40}')
print(f'PASS: {len(PASS)}  FAIL: {len(FAIL)}')
if FAIL:
    print('FALHAS:')
    for f in FAIL:
        print(f'  - {f}')
    sys.exit(1)
else:
    print('Todos os cenarios passaram.')
    sys.exit(0)
