"""
Smoke test local do módulo Solicitações — sem banco real (SQLite in-memory).

O app.py do main roda a auto-migration no import (Postgres real), então este
smoke monta um mini-app só com o necessário: extensions + models +
solicitacoes_bp. Inclui a tabela notificacao para testar o sino.

Uso: cd backend && python scripts/smoke_solicitacoes_local.py
"""
import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401 — registra todos os models no metadata
from models import (
    User, Obra, Notificacao, PagamentoFuturo,
    SolicitacaoCompra, SolicitacaoCotacao,
)
from routes.solicitacoes import solicitacoes_bp

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(solicitacoes_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'notificacao', 'pagamento_futuro',
    'servico',  # FK de pagamento_futuro.servico_id
    'solicitacao_compra', 'solicitacao_item', 'solicitacao_cotacao', 'solicitacao_config',
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


def notifs(user_id, tipo=None):
    q = Notificacao.query.filter_by(usuario_destino_id=user_id)
    if tipo:
        q = q.filter_by(tipo=tipo)
    return q.all()


with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])

    obra1 = Obra(nome='Obra Smoke 1')
    obra2 = Obra(nome='Obra Smoke 2')
    obra_arq = Obra(nome='Obra Arquivada', arquivada=True)
    master = User(username='master_smoke', role='master', modulos_permitidos=['obras'])
    master.set_password('smoke123')
    solicitante = User(username='solicitante_smoke', role='comum')  # modulos None = todos
    solicitante.set_password('smoke123')
    solicitante.obras_permitidas.append(obra1)
    outro = User(username='outro_smoke', role='comum')
    outro.set_password('smoke123')
    outro.obras_permitidas.append(obra2)
    restrito = User(username='restrito_smoke', role='comum', modulos_permitidos=['obras'])
    restrito.set_password('smoke123')
    aprovador = User(username='aprovador_smoke', role='comum', modulos_permitidos=['solicitacoes'])
    aprovador.set_password('smoke123')
    aprovador.obras_permitidas.append(obra1)
    alertado = User(username='alertado_smoke', role='comum', modulos_permitidos=['solicitacoes'])
    alertado.set_password('smoke123')
    alertado.obras_permitidas.append(obra1)
    db.session.add_all([obra1, obra2, obra_arq, master, solicitante, outro,
                        restrito, aprovador, alertado])
    db.session.commit()
    obra1_id, obra2_id, obra_arq_id = obra1.id, obra2.id, obra_arq.id
    ids = {u.username: u.id for u in (master, solicitante, outro, restrito, aprovador, alertado)}

    def h(username):
        return {'Authorization': f'Bearer {create_access_token(identity=str(ids[username]))}'}

    h_master = h('master_smoke')
    h_sol = h('solicitante_smoke')
    h_outro = h('outro_smoke')
    h_restrito = h('restrito_smoke')
    h_aprov = h('aprovador_smoke')
    h_alert = h('alertado_smoke')

    with app.test_client() as c:
        print('\n=== gating por módulo ===')
        r = c.get('/solicitacoes', headers=h_master)
        check('master com lista restritiva -> 200 (bypass)', r.status_code == 200,
              f'got {r.status_code}: {r.data[:200]}')
        r = c.get('/solicitacoes', headers=h_restrito)
        check('comum com modulos=[obras] -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.get('/solicitacoes', headers=h_sol)
        check('comum com modulos=None (todos) -> 200', r.status_code == 200)
        r = c.get('/solicitacoes')
        check('GET /solicitacoes sem token -> 401', r.status_code == 401)

        print('\n=== config (master only) ===')
        r = c.get('/solicitacoes/config', headers=h_sol)
        check('GET config como comum -> 403', r.status_code == 403)
        r = c.get('/solicitacoes/config', headers=h_master)
        check('GET config sem linha -> 200 defaults', r.status_code == 200
              and json.loads(r.data)['limite_valor'] is None)
        r = c.put('/solicitacoes/config', json={'alertados_ids': [99999]}, headers=h_master)
        check('PUT config com user inexistente -> 400', r.status_code == 400)
        r = c.put('/solicitacoes/config', json={'alertados_ids': 'banana'}, headers=h_master)
        check('PUT config lista inválida -> 400', r.status_code == 400)
        r = c.put('/solicitacoes/config', json={
            'alertados_ids': [ids['alertado_smoke']],
            'aprovadores_ids': [ids['aprovador_smoke']],
            'limite_valor': None,
        }, headers=h_master)
        check('PUT config ok -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')

        print('\n=== criação de solicitação ===')
        r = c.post('/solicitacoes', json={}, headers=h_sol)
        check('POST sem obra -> 400', r.status_code == 400)
        r = c.post('/solicitacoes', json={'obra_id': 99999, 'itens': [{'descricao': 'x', 'quantidade': 1}]},
                   headers=h_sol)
        check('POST obra inexistente -> 400', r.status_code == 400)
        r = c.post('/solicitacoes', json={'obra_id': obra2_id, 'itens': [{'descricao': 'x', 'quantidade': 1}]},
                   headers=h_sol)
        check('POST obra sem acesso -> 403', r.status_code == 403)
        r = c.post('/solicitacoes', json={'obra_id': obra_arq_id, 'itens': [{'descricao': 'x', 'quantidade': 1}]},
                   headers=h_master)
        check('POST obra arquivada -> 400', r.status_code == 400)
        r = c.post('/solicitacoes', json={'obra_id': obra1_id, 'itens': []}, headers=h_sol)
        check('POST sem itens -> 400', r.status_code == 400)
        r = c.post('/solicitacoes', json={'obra_id': obra1_id, 'itens': [{'quantidade': 5}]}, headers=h_sol)
        check('POST item sem descrição -> 400', r.status_code == 400)
        r = c.post('/solicitacoes', json={'obra_id': obra1_id,
                                          'itens': [{'descricao': 'Cimento', 'quantidade': 0}]}, headers=h_sol)
        check('POST quantidade zero -> 400', r.status_code == 400)
        r = c.post('/solicitacoes', json={'obra_id': obra1_id, 'tipo': 'Banana',
                                          'itens': [{'descricao': 'Cimento', 'quantidade': 1}]}, headers=h_sol)
        check('POST tipo inválido -> 400', r.status_code == 400)

        r = c.post('/solicitacoes', json={
            'obra_id': obra1_id,
            'tipo': 'Material',
            'data_necessidade': (date.today() + timedelta(days=15)).isoformat(),
            'observacao': 'Urgente para a laje',
            'itens': [
                {'descricao': 'Cimento CP-II', 'quantidade': '50', 'unidade': 'sc'},
                {'descricao': 'Areia média', 'quantidade': 20, 'unidade': 'm³'},
            ],
        }, headers=h_sol)
        check('POST solicitação ok -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:300]}')
        sol = json.loads(r.data)
        sol_id = sol['id']
        check('solicitação: status Aberta', sol['status'] == 'Aberta')
        check('solicitação: data_criacao automática', bool(sol['data_criacao']))
        check('solicitação: token_publico gerado', len(sol.get('token_publico') or '') > 20)
        check('solicitação: 2 itens', len(sol['itens']) == 2)
        check('solicitação: quantidade string parseada', sol['itens'][0]['quantidade'] == 50.0)
        check('notif: alertado recebeu solicitacao_criada',
              len(notifs(ids['alertado_smoke'], 'solicitacao_criada')) == 1)
        check('notif: solicitante NÃO recebeu a própria',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_criada')) == 0)

        print('\n=== rota pública ===')
        token = sol['token_publico']
        r = c.get(f'/solicitacoes/publico/{token}')
        check('GET público sem JWT -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        pub = json.loads(r.data)
        check('público: obra_nome presente', pub['obra_nome'] == 'Obra Smoke 1')
        check('público: itens presentes', len(pub['itens']) == 2)
        check('público: NÃO expõe cotações', 'cotacoes' not in pub)
        r = c.get('/solicitacoes/publico/token-invalido-xyz')
        check('GET público token inválido -> 404', r.status_code == 404)

        print('\n=== visibilidade ===')
        r = c.get('/solicitacoes', headers=h_outro)
        check('outro (obra2) não vê solicitação da obra1', len(json.loads(r.data)) == 0)
        r = c.get(f'/solicitacoes/{sol_id}', headers=h_outro)
        check('detalhe de outra obra -> 403', r.status_code == 403)
        r = c.get('/solicitacoes', headers=h_sol)
        check('solicitante vê a própria', len(json.loads(r.data)) == 1)
        r = c.get(f'/solicitacoes/{sol_id}', headers=h_master)
        check('master vê detalhe', r.status_code == 200)
        det = json.loads(r.data)
        check('detalhe: pode_aprovar (master)', det['pode_aprovar'] is True)
        r = c.get(f'/solicitacoes/{sol_id}', headers=h_sol)
        check('detalhe: solicitante não é aprovador', json.loads(r.data)['pode_aprovar'] is False)

        print('\n=== cotações ===')
        r = c.post(f'/solicitacoes/{sol_id}/cotacoes', json={'valor_total': 100}, headers=h_alert)
        check('POST cotação sem fornecedor -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/cotacoes',
                   json={'fornecedor': 'F1', 'valor_total': 0}, headers=h_alert)
        check('POST cotação valor zero -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/cotacoes', json={
            'fornecedor': 'Depósito A', 'valor_total': '2.640,00',
            'condicao_pagamento': '30 dias', 'prazo_entrega': '5 dias úteis',
        }, headers=h_alert)
        check('POST cotação 1 -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:300]}')
        cot1 = json.loads(r.data)
        check('cotação: valor BR parseado', cot1['valor_total'] == 2640.0)
        check('cotação: criado_por_nome', cot1['criado_por_nome'] == 'alertado_smoke')
        r = c.get(f'/solicitacoes/{sol_id}', headers=h_master)
        check('status virou Em cotação', json.loads(r.data)['status'] == 'Em cotação')

        r = c.post(f'/solicitacoes/{sol_id}/cotacoes', json={
            'fornecedor': 'Depósito B', 'valor_total': 2400.0,
        }, headers=h_alert)
        check('POST cotação 2 -> 201', r.status_code == 201)
        cot2 = json.loads(r.data)

        r = c.delete(f'/solicitacoes/{sol_id}/cotacoes/{cot2["id"]}', headers=h_sol)
        check('DELETE cotação por não-autor -> 403', r.status_code == 403)

        print('\n=== aprovação — sem limite configurado (tudo exige aprovador) ===')
        r = c.post(f'/solicitacoes/{sol_id}/aprovar', json={'cotacao_id': cot2['id']}, headers=h_alert)
        check('não-aprovador sem limite -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.post(f'/solicitacoes/{sol_id}/aprovar', json={}, headers=h_aprov)
        check('aprovar sem cotacao_id -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/aprovar', json={'cotacao_id': 99999}, headers=h_aprov)
        check('aprovar cotação de outra solicitação -> 400', r.status_code == 400)

        print('\n=== enviar para aprovação ===')
        r = c.patch(f'/solicitacoes/{sol_id}/enviar-aprovacao', headers=h_alert)
        check('enviar-aprovacao -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        check('status Aguardando aprovação', json.loads(r.data)['status'] == 'Aguardando aprovação')
        check('notif: aprovador recebeu aguardando_aprovacao',
              len(notifs(ids['aprovador_smoke'], 'solicitacao_aguardando_aprovacao')) == 1)
        r = c.patch(f'/solicitacoes/{sol_id}/enviar-aprovacao', headers=h_alert)
        check('enviar-aprovacao repetido -> 400', r.status_code == 400)

        print('\n=== aprovação pelo aprovador ===')
        r = c.post(f'/solicitacoes/{sol_id}/aprovar', json={'cotacao_id': cot2['id']}, headers=h_aprov)
        check('aprovador aprova -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:400]}')
        aprovada = json.loads(r.data)
        check('status Aprovada', aprovada['status'] == 'Aprovada')
        check('cotacao_aprovada_id gravado', aprovada['cotacao_aprovada_id'] == cot2['id'])
        check('pagamento_futuro_id gravado', bool(aprovada['pagamento_futuro_id']))
        pf = PagamentoFuturo.query.get(aprovada['pagamento_futuro_id'])
        check('PagamentoFuturo criado na obra', pf is not None and pf.obra_id == obra1_id)
        check('PF: valor da cotação escolhida', pf.valor == 2400.0)
        check('PF: fornecedor da cotação', pf.fornecedor == 'Depósito B')
        check('PF: status Previsto', pf.status == 'Previsto')
        check('PF: tipo da solicitação', pf.tipo == 'Material')
        check('PF: vencimento = data_necessidade',
              pf.data_vencimento == date.today() + timedelta(days=15))
        check('PF: descrição referencia a solicitação', f'#{sol_id}' in pf.descricao)
        check('notif: solicitante recebeu aprovada',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_aprovada')) == 1)
        check('notif: alertado recebeu aprovada',
              len(notifs(ids['alertado_smoke'], 'solicitacao_aprovada')) == 1)

        print('\n=== anti-duplicação e pós-aprovação ===')
        r = c.post(f'/solicitacoes/{sol_id}/aprovar', json={'cotacao_id': cot1['id']}, headers=h_aprov)
        check('2º aprovar -> 400', r.status_code == 400)
        check('PagamentoFuturo único', PagamentoFuturo.query.count() == 1)
        r = c.patch(f'/solicitacoes/{sol_id}/cancelar', headers=h_master)
        check('cancelar aprovada -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/cotacoes',
                   json={'fornecedor': 'F3', 'valor_total': 10}, headers=h_alert)
        check('cotação em aprovada -> 400', r.status_code == 400)
        r = c.delete(f'/solicitacoes/{sol_id}/cotacoes/{cot1["id"]}', headers=h_master)
        check('DELETE cotação em aprovada -> 400', r.status_code == 400)

        print('\n=== efetivação direta (limite de valor) ===')
        r = c.put('/solicitacoes/config', json={
            'alertados_ids': [ids['alertado_smoke']],
            'aprovadores_ids': [ids['aprovador_smoke']],
            'limite_valor': '1.000,00',
        }, headers=h_master)
        check('PUT config limite 1000 -> 200', r.status_code == 200)
        check('limite parseado', json.loads(r.data)['limite_valor'] == 1000.0)

        r = c.post('/solicitacoes', json={
            'obra_id': obra1_id,
            'itens': [{'descricao': 'Pregos 17x21', 'quantidade': 10, 'unidade': 'kg'}],
        }, headers=h_sol)
        sol2 = json.loads(r.data)
        r = c.post(f'/solicitacoes/{sol2["id"]}/cotacoes',
                   json={'fornecedor': 'Ferragem X', 'valor_total': 800}, headers=h_alert)
        cot_barata = json.loads(r.data)
        r = c.post(f'/solicitacoes/{sol2["id"]}/cotacoes',
                   json={'fornecedor': 'Ferragem Y', 'valor_total': 5000}, headers=h_alert)
        cot_cara = json.loads(r.data)

        r = c.post(f'/solicitacoes/{sol2["id"]}/aprovar',
                   json={'cotacao_id': cot_cara['id']}, headers=h_alert)
        check('efetivar acima do limite -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.post(f'/solicitacoes/{sol2["id"]}/aprovar',
                   json={'cotacao_id': cot_barata['id']}, headers=h_alert)
        check('efetivar dentro do limite -> 200', r.status_code == 200,
              f'got {r.status_code}: {r.data[:300]}')
        sol2_aprovada = json.loads(r.data)
        pf2 = PagamentoFuturo.query.get(sol2_aprovada['pagamento_futuro_id'])
        check('PF2: vencimento hoje+7 (sem data_necessidade)',
              pf2.data_vencimento == date.today() + timedelta(days=7))

        print('\n=== rejeição ===')
        r = c.post('/solicitacoes', json={
            'obra_id': obra1_id,
            'itens': [{'descricao': 'Betoneira', 'quantidade': 1}],
        }, headers=h_sol)
        sol3 = json.loads(r.data)
        r = c.post(f'/solicitacoes/{sol3["id"]}/cotacoes',
                   json={'fornecedor': 'Locadora Z', 'valor_total': 900}, headers=h_alert)
        check('cotação sol3 -> 201', r.status_code == 201)
        r = c.post(f'/solicitacoes/{sol3["id"]}/rejeitar', json={'motivo': 'x'}, headers=h_sol)
        check('rejeitar por não-aprovador -> 403', r.status_code == 403)
        r = c.post(f'/solicitacoes/{sol3["id"]}/rejeitar', json={}, headers=h_aprov)
        check('rejeitar sem motivo -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol3["id"]}/rejeitar',
                   json={'motivo': 'Sem orçamento este mês'}, headers=h_aprov)
        check('rejeitar ok -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        check('status Rejeitada', json.loads(r.data)['status'] == 'Rejeitada')
        check('notif: solicitante recebeu rejeitada',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_rejeitada')) == 1)

        print('\n=== cancelamento ===')
        r = c.post('/solicitacoes', json={
            'obra_id': obra1_id,
            'itens': [{'descricao': 'Vergalhão 10mm', 'quantidade': 30, 'unidade': 'br'}],
        }, headers=h_sol)
        sol4 = json.loads(r.data)
        r = c.patch(f'/solicitacoes/{sol4["id"]}/cancelar', headers=h_alert)
        check('cancelar por terceiro -> 403', r.status_code == 403)
        r = c.patch(f'/solicitacoes/{sol4["id"]}/cancelar', headers=h_sol)
        check('cancelar pelo solicitante -> 200', r.status_code == 200)
        check('status Cancelada', json.loads(r.data)['status'] == 'Cancelada')
        r = c.post(f'/solicitacoes/{sol4["id"]}/cotacoes',
                   json={'fornecedor': 'F', 'valor_total': 10}, headers=h_alert)
        check('cotação em cancelada -> 400', r.status_code == 400)

        print('\n=== filtros da listagem ===')
        r = c.get('/solicitacoes?status=Aprovada', headers=h_master)
        check('filtro status=Aprovada', all(s['status'] == 'Aprovada' for s in json.loads(r.data))
              and len(json.loads(r.data)) == 2)
        r = c.get(f'/solicitacoes?obra_id={obra2_id}', headers=h_master)
        check('filtro obra_id sem resultados', len(json.loads(r.data)) == 0)

        rotas = [r for r in app.url_map.iter_rules() if str(r).startswith('/solicitacoes')]
        check('blueprint: >= 12 rotas /solicitacoes', len(rotas) >= 12, f'encontrado: {len(rotas)}')

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
