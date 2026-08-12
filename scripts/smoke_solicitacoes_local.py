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
import io
from datetime import date, timedelta

from openpyxl import Workbook

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
    'solicitacao_comentario', 'solicitacao_entrega',
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

        print('\n=== leitura e exportação de pedido ===')
        r = c.post('/solicitacoes/ler-pedido', headers=h_sol)
        check('ler pedido sem arquivo -> 400', r.status_code == 400)
        r = c.post('/solicitacoes/ler-pedido', data={
            'arquivo': (io.BytesIO(b'nao-e-planilha'), 'pedido.txt'),
        }, headers=h_sol, content_type='multipart/form-data')
        check('ler pedido com formato inválido -> 400', r.status_code == 400)
        r = c.post('/solicitacoes/ler-pedido', data={
            'arquivo': (io.BytesIO(b'PK\x03\x04arquivo-corrompido'), 'pedido.xlsx'),
        }, headers=h_sol, content_type='multipart/form-data')
        check('ler Excel corrompido -> 400 seguro', r.status_code == 400)

        wb_pedido = Workbook()
        ws_pedido = wb_pedido.active
        ws_pedido.append(['COBERTURA — 2 ITENS'])
        ws_pedido.append(['Sistema', 'Categoria', 'Material', 'Especificação', 'Quantidade', 'Unidade'])
        ws_pedido.append(['Ventilação', 'PVC Esgoto', 'Terminal de ventilação', '50 mm', 3, 'pç'])
        ws_pedido.append(['Ventilação', 'PVC Esgoto', 'Tubo rígido c/ ponta lisa', '50 mm - 2"', 10.51, 'm'])
        ws_pedido.append(['SUPERIOR — 1 ITEM'])
        ws_pedido.append(['Sistema', 'Categoria', 'Material', 'Especificação', 'Quantidade', 'Unidade'])
        ws_pedido.append(['Esgoto', 'PVC Acessórios', 'Caixa sifonada', '150 x 150 x 50 mm', 3, 'pç'])
        pedido_xlsx = io.BytesIO()
        wb_pedido.save(pedido_xlsx)
        r = c.post('/solicitacoes/ler-pedido', data={
            'arquivo': (io.BytesIO(pedido_xlsx.getvalue()), 'pedido_materiais.xlsx'),
        }, headers=h_sol, content_type='multipart/form-data')
        pedido_lido = json.loads(r.data) if r.status_code == 200 else {}
        check('ler Excel estruturado -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        check('Excel: ignora seção e importa 3 materiais', len(pedido_lido.get('itens', [])) == 3)
        check('Excel: material vira descrição', pedido_lido.get('itens', [{}])[0].get('descricao') == 'Terminal de ventilação')
        check('Excel: categoria e especificação preservadas na observação',
              'Categoria: PVC Esgoto' in pedido_lido.get('itens', [{}])[0].get('observacao', '')
              and 'Especificação: 50 mm' in pedido_lido.get('itens', [{}])[0].get('observacao', ''))
        check('Excel: quantidade decimal preservada', pedido_lido.get('itens', [{}, {}])[1].get('quantidade') == 10.51)

        r = c.get(f'/solicitacoes/{sol_id}/exportar.xlsx', headers=h_sol)
        xlsx_exportado = r.data
        check('exportar solicitação em Excel -> 200', r.status_code == 200 and xlsx_exportado.startswith(b'PK'))
        check('Excel exportado força download', 'attachment' in (r.headers.get('Content-Disposition') or ''))
        r = c.post('/solicitacoes/ler-pedido', data={
            'arquivo': (io.BytesIO(xlsx_exportado), 'solicitacao_exportada.xlsx'),
        }, headers=h_sol, content_type='multipart/form-data')
        check('Excel exportado pode ser lido novamente',
              r.status_code == 200 and len(json.loads(r.data).get('itens', [])) == 2,
              f'got {r.status_code}: {r.data[:300]}')

        r = c.get(f'/solicitacoes/{sol_id}/exportar.pdf', headers=h_sol)
        pdf_exportado = r.data
        check('exportar solicitação em PDF -> 200', r.status_code == 200 and pdf_exportado.startswith(b'%PDF'))
        r = c.post('/solicitacoes/ler-pedido', data={
            'arquivo': (io.BytesIO(pdf_exportado), 'solicitacao_exportada.pdf'),
        }, headers=h_sol, content_type='multipart/form-data')
        check('PDF exportado pode ser lido novamente',
              r.status_code == 200 and len(json.loads(r.data).get('itens', [])) == 2,
              f'got {r.status_code}: {r.data[:300]}')
        r = c.get(f'/solicitacoes/{sol_id}/exportar.pdf', headers=h_outro)
        check('exportação respeita visibilidade da obra -> 403', r.status_code == 403)
        r = c.get(f'/solicitacoes/{sol_id}/exportar.xlsx')
        check('exportação sem token -> 401', r.status_code == 401)

        print('\n=== edição de solicitação aberta ===')
        edit_payload = {
            'obra_id': obra1_id,
            'tipo': 'Material',
            'data_necessidade': (date.today() + timedelta(days=20)).isoformat(),
            'observacao': 'Entrega ajustada após conferência',
            'itens': [
                {'descricao': 'Cimento CP-III', 'quantidade': '60', 'unidade': 'sc',
                 'observacao': 'Preferência por lote único'},
                {'descricao': 'Areia média', 'quantidade': 20, 'unidade': 'm³'},
            ],
        }
        r = c.patch(f'/solicitacoes/{sol_id}', json=edit_payload, headers=h_alert)
        check('PATCH por não-solicitante -> 403', r.status_code == 403)
        r = c.patch(f'/solicitacoes/{sol_id}', json={**edit_payload, 'itens': []}, headers=h_sol)
        check('PATCH sem itens -> 400', r.status_code == 400)
        r = c.patch(f'/solicitacoes/{sol_id}', json=edit_payload, headers=h_sol)
        check('PATCH pelo solicitante -> 200', r.status_code == 200,
              f'got {r.status_code}: {r.data[:300]}')
        editada = json.loads(r.data)
        check('edição substituiu itens', len(editada['itens']) == 2
              and editada['itens'][0]['descricao'] == 'Cimento CP-III'
              and editada['itens'][0]['quantidade'] == 60.0)
        check('edição preservou autoria e criação', editada['solicitante_id'] == ids['solicitante_smoke']
              and editada['data_criacao'] == sol['data_criacao'])
        check('edição notificou responsável por preços',
              len(notifs(ids['alertado_smoke'], 'solicitacao_editada')) == 1)
        r = c.patch(f'/solicitacoes/{sol_id}', json={**edit_payload, 'observacao': 'Revisada pelo master'},
                    headers=h_master)
        check('PATCH pelo master -> 200', r.status_code == 200)
        r = c.get(f'/solicitacoes/{sol_id}', headers=h_sol)
        check('detalhe aberto informa pode_editar', json.loads(r.data).get('pode_editar') is True)

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
        detalhe_cotacao = json.loads(r.data)
        check('status virou Em cotação', detalhe_cotacao['status'] == 'Em cotação')
        check('com cotação não pode mais editar', detalhe_cotacao.get('pode_editar') is False)
        r = c.patch(f'/solicitacoes/{sol_id}', json=edit_payload, headers=h_master)
        check('PATCH após cotação -> 400', r.status_code == 400)

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
              pf.data_vencimento == date.today() + timedelta(days=20))
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

        print('\n=== atendimento (baixa do comprador) ===')
        r = c.patch(f'/solicitacoes/{sol3["id"]}/atender', headers=h_alert)
        check('atender rejeitada -> 400', r.status_code == 400, f'got {r.status_code}')
        r = c.patch(f'/solicitacoes/{sol_id}/atender', headers=h_outro)
        check('atender sem acesso à obra -> 403', r.status_code == 403)
        r = c.patch(f'/solicitacoes/{sol_id}/atender', headers=h_sol)
        check('atender por não-comprador -> 403', r.status_code == 403, f'got {r.status_code}')

        r = c.patch(f'/solicitacoes/{sol_id}/atender',
                    json={'data_atendimento': (date.today() + timedelta(days=1)).isoformat()},
                    headers=h_alert)
        check('atender com data futura -> 400', r.status_code == 400)

        r = c.get(f'/solicitacoes/{sol_id}', headers=h_alert)
        det_alert = json.loads(r.data)
        check('detalhe: pode_atender p/ comprador', det_alert['pode_atender'] is True)
        check('detalhe: pode_reabrir False antes da baixa', det_alert['pode_reabrir'] is False)
        r = c.get(f'/solicitacoes/{sol_id}', headers=h_sol)
        check('detalhe: pode_atender False p/ solicitante',
              json.loads(r.data)['pode_atender'] is False)

        r = c.patch(f'/solicitacoes/{sol_id}/atender',
                    json={
                        'data_atendimento': date.today().isoformat(),
                        'observacao': 'NF 4521 — entregue no canteiro',
                    }, headers=h_alert)
        check('comprador atende -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        atendida = json.loads(r.data)
        check('status Atendida', atendida['status'] == 'Atendida')
        check('atendida_por_nome gravado', atendida['atendida_por_nome'] == 'alertado_smoke')
        check('data_atendimento escolhida gravada',
              atendida['data_atendimento'][:10] == date.today().isoformat())
        check('observação do atendimento gravada',
              atendida['observacao_atendimento'] == 'NF 4521 — entregue no canteiro')
        check('valor_aprovado no payload', atendida['valor_aprovado'] == 2400.0)
        check('fornecedor_aprovado no payload', atendida['fornecedor_aprovado'] == 'Depósito B')
        check('notif: solicitante recebeu atendida',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_atendida')) == 1)
        pf_baixa = PagamentoFuturo.query.get(atendida['pagamento_futuro_id'])
        check('baixa não mexe na conta a pagar', pf_baixa is not None and pf_baixa.status == 'Previsto')
        r = c.patch(f'/solicitacoes/{sol_id}/atender', headers=h_alert)
        check('atender 2x -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/cotacoes',
                   json={'fornecedor': 'F4', 'valor_total': 10}, headers=h_alert)
        check('cotação em atendida -> 400', r.status_code == 400)
        r = c.patch(f'/solicitacoes/{sol_id}/cancelar', headers=h_master)
        check('cancelar atendida -> 400', r.status_code == 400)

        print('\n=== sai da lista de compras, entra no histórico ===')
        r = c.get('/solicitacoes', headers=h_master)
        lista_ativa = json.loads(r.data)
        check('atendida fora da lista de compras', all(x['id'] != sol_id for x in lista_ativa))
        check('demais solicitações seguem na lista', len(lista_ativa) == 3, f'got {len(lista_ativa)}')
        r = c.get('/solicitacoes?historico=true', headers=h_master)
        hist = json.loads(r.data)
        check('histórico traz só a atendida', len(hist) == 1 and hist[0]['id'] == sol_id,
              f'got {len(hist)}')
        r = c.get('/solicitacoes?status=Atendida', headers=h_master)
        check('filtro status=Atendida explícito', len(json.loads(r.data)) == 1)
        r = c.get('/solicitacoes?historico=true', headers=h_outro)
        check('histórico respeita visibilidade', len(json.loads(r.data)) == 0)
        r = c.get('/solicitacoes', headers=h_alert)
        check('lista expõe pode_atender p/ comprador',
              any(x['pode_atender'] for x in json.loads(r.data)))
        r = c.get('/solicitacoes', headers=h_sol)
        check('lista não expõe pode_atender p/ solicitante',
              not any(x['pode_atender'] for x in json.loads(r.data)))

        print('\n=== reabrir (desfaz a baixa) ===')
        r = c.patch(f'/solicitacoes/{sol_id}/reabrir', headers=h_aprov)
        check('reabrir por quem não deu a baixa -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.patch(f'/solicitacoes/{sol2["id"]}/reabrir', headers=h_master)
        check('reabrir não-atendida -> 400', r.status_code == 400)
        r = c.patch(f'/solicitacoes/{sol_id}/reabrir', headers=h_alert)
        check('comprador reabre -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        reaberta = json.loads(r.data)
        check('volta para Aprovada', reaberta['status'] == 'Aprovada')
        check('campos de atendimento limpos',
              reaberta['data_atendimento'] is None and reaberta['atendida_por_nome'] is None)
        r = c.get('/solicitacoes?historico=true', headers=h_master)
        check('histórico vazio após reabrir', len(json.loads(r.data)) == 0)
        r = c.get('/solicitacoes', headers=h_master)
        check('reaberta volta para a lista de compras',
              any(x['id'] == sol_id for x in json.loads(r.data)))

        print('\n=== devolver para cotação (desfaz a aprovação) ===')
        # sol_id está Aprovada (reaberta acima), com o PF 'Previsto' intacto.
        r = c.patch(f'/solicitacoes/{sol_id}/devolver', headers=h_sol)
        check('devolver por não-aprovador -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.patch(f'/solicitacoes/{sol3["id"]}/devolver', headers=h_aprov)
        check('devolver rejeitada -> 400', r.status_code == 400)

        pf_movida = PagamentoFuturo.query.get(sol2_aprovada['pagamento_futuro_id'])
        pf_movida.status = 'Pago'
        db.session.commit()
        r = c.patch(f'/solicitacoes/{sol2["id"]}/devolver', headers=h_aprov)
        check('devolver com PF já movimentado -> 400', r.status_code == 400, f'got {r.status_code}')
        pf_movida.status = 'Previsto'
        db.session.commit()

        pfs_antes = PagamentoFuturo.query.count()
        r = c.patch(f'/solicitacoes/{sol_id}/devolver', headers=h_aprov)
        check('aprovador devolve -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        devolvida = json.loads(r.data)
        check('devolvida: status Em cotação', devolvida['status'] == 'Em cotação')
        check('devolvida: decisão limpa', devolvida['cotacao_aprovada_id'] is None
              and devolvida['pagamento_futuro_id'] is None
              and devolvida['aprovador_nome'] is None)
        check('devolvida: PF Previsto removido', PagamentoFuturo.query.count() == pfs_antes - 1)
        check('notif: solicitante avisado da devolução',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_devolvida')) == 1)
        r = c.patch(f'/solicitacoes/{sol_id}/devolver', headers=h_aprov)
        check('devolver 2x -> 400 (não está aprovada)', r.status_code == 400)

        r = c.post(f'/solicitacoes/{sol_id}/aprovar', json={'cotacao_id': cot2['id']}, headers=h_aprov)
        check('re-aprovar depois de devolver -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        check('re-aprovação recria o PF', PagamentoFuturo.query.count() == pfs_antes)

        print('\n=== comentários (@menção) ===')
        r = c.get('/solicitacoes/usuarios-mencao', headers=h_restrito)
        check('usuarios-mencao sem módulo -> 403', r.status_code == 403)
        r = c.get('/solicitacoes/usuarios-mencao', headers=h_sol)
        check('usuarios-mencao como comum -> 200', r.status_code == 200, f'got {r.status_code}')
        mencionaveis = json.loads(r.data)
        check('mencionáveis: só usuários do módulo',
              all(u['username'] != 'restrito_smoke' for u in mencionaveis)
              and any(u['username'] == 'alertado_smoke' for u in mencionaveis))
        check('mencionáveis: só id/username/role',
              all(set(u.keys()) == {'id', 'username', 'role'} for u in mencionaveis))

        r = c.post(f'/solicitacoes/{sol_id}/comentarios', json={'texto': '  '}, headers=h_aprov)
        check('comentário vazio -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/comentarios', json={'texto': 'x' * 1001}, headers=h_aprov)
        check('comentário longo demais -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/comentarios', json={
            'texto': '@alertado_smoke consegue adiantar a entrega?',
            'mencionados_ids': [ids['alertado_smoke'], 99999],
        }, headers=h_aprov)
        check('POST comentário -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:300]}')
        com1 = json.loads(r.data)
        check('comentário: id inexistente descartado da menção',
              com1['mencionados_ids'] == [ids['alertado_smoke']])
        check('notif: mencionado recebeu solicitacao_mencao',
              len(notifs(ids['alertado_smoke'], 'solicitacao_mencao')) == 1)
        check('notif: solicitante avisado do comentário de terceiro',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_comentario')) == 1)

        r = c.post(f'/solicitacoes/{sol_id}/comentarios', json={
            'texto': '@solicitante_smoke pode confirmar a medida?',
        }, headers=h_alert)
        check('menção digitada à mão (sem ids) -> 201', r.status_code == 201)
        com2 = json.loads(r.data)
        check('menção resolvida pelo texto',
              com2['mencionados_ids'] == [ids['solicitante_smoke']])
        check('notif: mencionado no texto recebeu mencao',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_mencao')) == 1)
        check('notif: mencionado não é avisado em dobro',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_comentario')) == 1)

        r = c.get(f'/solicitacoes/{sol_id}/comentarios', headers=h_sol)
        check('GET comentários -> 200 com 2', r.status_code == 200 and len(json.loads(r.data)) == 2)
        check('comentário: autor_nome presente',
              json.loads(r.data)[0]['autor_nome'] == 'aprovador_smoke')
        r = c.get(f'/solicitacoes/{sol_id}/comentarios', headers=h_outro)
        check('GET comentários sem acesso à obra -> 403', r.status_code == 403)

        r = c.delete(f'/solicitacoes/{sol_id}/comentarios/{com1["id"]}', headers=h_sol)
        check('DELETE comentário por não-autor -> 403', r.status_code == 403)
        r = c.delete(f'/solicitacoes/{sol_id}/comentarios/{com1["id"]}', headers=h_aprov)
        check('DELETE pelo autor -> 200', r.status_code == 200)
        r = c.delete(f'/solicitacoes/{sol_id}/comentarios/{com2["id"]}', headers=h_master)
        check('DELETE pelo master -> 200', r.status_code == 200)
        r = c.get(f'/solicitacoes/{sol_id}/comentarios', headers=h_master)
        check('conversa vazia após remoções', len(json.loads(r.data)) == 0)

        print('\n=== superlink de entrega (motorista) ===')
        # sol_id voltou a ficar Aprovada na seção do devolver (re-aprovada).
        r = c.post(f'/solicitacoes/{sol_id}/entrega', headers=h_sol)
        check('gerar entrega por não-comprador -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.post(f'/solicitacoes/{sol3["id"]}/entrega', headers=h_alert)
        check('gerar entrega em rejeitada -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/{sol_id}/entrega', headers=h_alert)
        check('comprador gera o link -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        token_entrega_1 = json.loads(r.data)['token']

        r = c.post(f'/solicitacoes/{sol_id}/entrega', headers=h_alert)
        token_entrega = json.loads(r.data)['token']
        check('regenerar troca o token', token_entrega != token_entrega_1)
        r = c.get(f'/solicitacoes/entrega/{token_entrega_1}')
        check('token antigo morre -> 404', r.status_code == 404)

        r = c.get(f'/solicitacoes/entrega/{token_entrega}')
        check('GET entrega sem JWT -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        pub_ent = json.loads(r.data)
        check('entrega: fornecedor da cotação escolhida', pub_ent['fornecedor'] == 'Depósito B')
        check('entrega: itens presentes', len(pub_ent['itens']) == 2)
        check('entrega: NUNCA expõe valores',
              'valor' not in json.dumps(pub_ent).lower() and 'cotacoes' not in pub_ent)

        r = c.get(f'/solicitacoes/entrega/{token_entrega}/pedido.pdf')
        check('PDF público do pedido -> 200', r.status_code == 200 and r.data.startswith(b'%PDF'))

        # Lista GRANDE: 60 itens (um com descrição de 300 chars) — o link e o
        # PDF precisam aguentar sem estourar.
        r = c.post('/solicitacoes', json={
            'obra_id': obra1_id,
            'itens': [{'descricao': (f'Item volumoso {n} ' + 'x' * 280)[:300],
                       'quantidade': n + 1, 'unidade': 'un'} for n in range(60)],
        }, headers=h_sol)
        sol_grande = json.loads(r.data)
        r = c.post(f'/solicitacoes/{sol_grande["id"]}/cotacoes',
                   json={'fornecedor': 'Atacadão', 'valor_total': 900}, headers=h_alert)
        cot_grande = json.loads(r.data)
        c.post(f'/solicitacoes/{sol_grande["id"]}/aprovar',
               json={'cotacao_id': cot_grande['id']}, headers=h_alert)
        r = c.post(f'/solicitacoes/{sol_grande["id"]}/entrega', headers=h_alert)
        token_grande = json.loads(r.data)['token']
        r = c.get(f'/solicitacoes/entrega/{token_grande}')
        check('entrega com 60 itens -> 200 completa', r.status_code == 200
              and len(json.loads(r.data)['itens']) == 60)
        r = c.get(f'/solicitacoes/entrega/{token_grande}/pedido.pdf')
        check('PDF com 60 itens longos -> 200', r.status_code == 200 and r.data.startswith(b'%PDF'),
              f'got {r.status_code}')

        print('\n=== entrega: mensagem do motorista e confirmação ===')
        r = c.post(f'/solicitacoes/entrega/{token_entrega}/mensagem', json={})
        check('mensagem vazia -> 400', r.status_code == 400)
        r = c.post(f'/solicitacoes/entrega/{token_entrega}/mensagem',
                   json={'texto': 'Caminhão não entra na rua — descarrego na esquina?'})
        check('mensagem do motorista -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:200]}')
        r = c.get(f'/solicitacoes/{sol_id}/comentarios', headers=h_alert)
        com_mot = json.loads(r.data)
        check('mensagem vira comentário na conversa', len(com_mot) == 1
              and com_mot[0]['autor_nome'] == 'Motorista (entrega)'
              and 'esquina' in com_mot[0]['texto'])
        check('notif: comprador avisado da mensagem',
              len(notifs(ids['alertado_smoke'], 'solicitacao_mensagem_motorista')) == 1)
        check('notif: solicitante avisado da mensagem',
              len(notifs(ids['solicitante_smoke'], 'solicitacao_mensagem_motorista')) == 1)

        r = c.post(f'/solicitacoes/entrega/{token_entrega}/confirmar',
                   json={'observacao': 'Entregue no portão, NF 4521'})
        check('confirmar entrega -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:200]}')
        check('confirmação registrada', bool(json.loads(r.data)['entregue_em']))
        r = c.post(f'/solicitacoes/entrega/{token_entrega}/confirmar', json={})
        check('confirmar 2x -> 400', r.status_code == 400)
        check('notif: comprador avisado da entrega',
              len(notifs(ids['alertado_smoke'], 'solicitacao_entregue')) == 1)
        r = c.get(f'/solicitacoes/{sol_id}', headers=h_alert)
        det_ent = json.loads(r.data)
        check('detalhe expõe a entrega confirmada',
              det_ent['entrega'] and det_ent['entrega']['entregue_em']
              and det_ent['entrega']['observacao_entrega'] == 'Entregue no portão, NF 4521')
        check('baixa oficial segue com o comprador', det_ent['status'] == 'Aprovada'
              and det_ent['pode_atender'] is True)

        r = c.patch(f'/solicitacoes/{sol_grande["id"]}/devolver', headers=h_aprov)
        check('devolver mata o link de entrega', r.status_code == 200)
        r = c.get(f'/solicitacoes/entrega/{token_grande}')
        check('link de entrega devolvida -> 404', r.status_code == 404)

        rotas = [r for r in app.url_map.iter_rules() if str(r).startswith('/solicitacoes')]
        check('blueprint: >= 23 rotas /solicitacoes', len(rotas) >= 23, f'encontrado: {len(rotas)}')

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
