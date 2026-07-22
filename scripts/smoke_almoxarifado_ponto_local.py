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
from models.lancamento import Lancamento
from models.obra import Obra
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from models.pagamento_futuro import PagamentoFuturo
from models.user import User
from routes.almoxarifado import almoxarifado_bp
from routes.cronograma import cronograma_bp
from routes.home import home_bp
from routes.orcamento_eng import orcamento_eng_bp
from routes.rh import rh_bp
from routes.sid import sid_bp


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
app.register_blueprint(cronograma_bp)
app.register_blueprint(home_bp)
app.register_blueprint(orcamento_eng_bp)
app.register_blueprint(rh_bp)
app.register_blueprint(sid_bp)

_TABELAS = [
    'user', 'user_obra_association', 'obra', 'categoria_mo', 'funcionario',
    'almoxarifado_item', 'almoxarifado_movimentacao', 'ponto_marcacao',
    'lancamento', 'boleto', 'parcela_individual', 'pagamento_parcelado_v2',
    'pagamento_futuro', 'pagamento_servico', 'servico',
    'orcamento_eng_etapa', 'orcamento_eng_item',
    'cronograma_obra', 'cronograma_etapa',
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
    almox = User(username='almox_smoke', role='comum', modulos_permitidos=['almoxarifado', 'obras'])
    almox.set_password('senha-smoke')
    almox.obras_permitidas.append(obra_a)
    rh = User(username='rh_smoke', role='comum', modulos_permitidos=['rh'])
    rh.set_password('senha-smoke')
    rh.obras_permitidas.append(obra_a)
    sem_modulo = User(username='sem_modulo_smoke', role='comum', modulos_permitidos=['obras'])
    sem_modulo.set_password('senha-smoke')
    sem_modulo.obras_permitidas.append(obra_a)
    financeiro = User(username='financeiro_smoke', role='administrador', modulos_permitidos=['almoxarifado', 'obras'])
    financeiro.set_password('senha-smoke')
    db.session.add_all([obra_a, obra_b, categoria, almox, rh, sem_modulo, financeiro])
    db.session.flush()

    funcionario_a = Funcionario(nome='Ana', categoria_id=categoria.id, obra_id=obra_a.id, salario=2000)
    funcionario_b = Funcionario(nome='Beto', categoria_id=categoria.id, obra_id=obra_b.id, salario=2000)
    etapa_locacao = OrcamentoEngEtapa(obra_id=obra_a.id, codigo='01', nome='Locacoes', ordem=1)
    db.session.add_all([funcionario_a, funcionario_b, etapa_locacao])
    db.session.flush()
    item_orcamento_locacao = OrcamentoEngItem(
        etapa_id=etapa_locacao.id, codigo='01.01', descricao='Locacao de equipamentos',
        unidade='mes', quantidade=1, tipo_composicao='fornecimento', preco_unitario=1000,
    )
    db.session.add(item_orcamento_locacao)
    db.session.commit()

    headers_almox = {'Authorization': f'Bearer {create_access_token(identity=str(almox.id))}'}
    headers_rh = {'Authorization': f'Bearer {create_access_token(identity=str(rh.id))}'}
    headers_sem_modulo = {'Authorization': f'Bearer {create_access_token(identity=str(sem_modulo.id))}'}
    headers_financeiro = {'Authorization': f'Bearer {create_access_token(identity=str(financeiro.id), additional_claims={'role': 'administrador'})}'}

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
        item = response.get_json()
        item_id = item['id']
        check('codigo de EPI e gerado automaticamente', item['codigo'].startswith('EP-') and item['codigo'] != 'CAP-01', item['codigo'])
        response = client.put(f'/almoxarifado/itens/{item_id}', headers=headers_almox, json={'codigo': 'ALTERADO'})
        check('codigo automatico nao pode ser adulterado', response.status_code == 200 and response.get_json()['codigo'] == item['codigo'], response.get_json().get('codigo'))
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

        response = client.post('/almoxarifado/itens', headers=headers_almox, json={
            'codigo': 'UNI-01', 'nome': 'Camisa uniforme', 'categoria': 'fardamento', 'unidade': 'un',
        })
        check('cadastro de fardamento autorizado', response.status_code == 201, response.status_code)
        fardamento_id = response.get_json()['id']
        client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': fardamento_id, 'tipo': 'entrada', 'quantidade': 3,
        })
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': fardamento_id, 'tipo': 'saida', 'quantidade': 1,
        })
        check('fardamento sem colaborador é bloqueado', response.status_code == 400, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': fardamento_id, 'tipo': 'saida', 'quantidade': 1, 'funcionario_id': funcionario_a.id,
        })
        check('entrega de fardamento é saída definitiva', response.status_code == 201, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': fardamento_id, 'tipo': 'devolucao_obra', 'quantidade': 1, 'obra_id': obra_a.id,
        })
        check('fardamento não aceita retorno ao estoque', response.status_code == 400, response.status_code)

        response = client.post('/almoxarifado/itens', headers=headers_almox, json={
            'codigo': 'MANUAL-01', 'nome': 'Enxada', 'categoria': 'ferramenta', 'unidade': 'un', 'tamanho': 'M',
        })
        enxada = response.get_json() if response.status_code == 201 else {}
        check('ferramenta recebe prefixo automatico', response.status_code == 201 and enxada.get('codigo', '').startswith('FR-'), enxada.get('codigo'))
        check('ferramenta nao grava tamanho ou grade', enxada.get('tamanho') is None, enxada.get('tamanho'))

        response = client.post('/almoxarifado/itens', headers=headers_almox, json={
            'codigo': 'LOC-INVALIDO', 'nome': 'Equipamento sem tarifa', 'categoria': 'equipamento', 'unidade': 'un',
            'modalidade': 'locacao', 'valor_locacao_mensal': 0,
        })
        check('equipamento locado exige valor mensal positivo', response.status_code == 400, response.status_code)
        response = client.post('/almoxarifado/itens', headers=headers_almox, json={
            'codigo': 'LOC-01', 'nome': 'Betoneira locada', 'categoria': 'equipamento', 'unidade': 'un',
            'modalidade': 'locacao', 'valor_unitario': 1500, 'valor_locacao_mensal': 250,
        })
        check('cadastro de equipamento locado autorizado', response.status_code == 201, response.status_code)
        locacao_id = response.get_json()['id']
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': locacao_id, 'tipo': 'locacao_entrada', 'quantidade': 2,
        })
        check('locação sem fornecedor é bloqueada', response.status_code == 400, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': locacao_id, 'tipo': 'locacao_entrada', 'quantidade': 2, 'fornecedor': 'Locadora Smoke',
        })
        check('entrada de equipamento locado autorizada', response.status_code == 201, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_almox, json={
            'item_id': locacao_id, 'tipo': 'alocacao_obra', 'quantidade': 1, 'obra_id': obra_a.id,
            'dias_locacao': 45, 'data_vencimento': '2026-08-10',
            'orcamento_item_id': item_orcamento_locacao.id,
        })
        check('perfil operacional nao cria compromisso financeiro', response.status_code == 403, response.status_code)
        response = client.post('/almoxarifado/movimentacoes', headers=headers_financeiro, json={
            'item_id': locacao_id, 'tipo': 'alocacao_obra', 'quantidade': 1, 'obra_id': obra_a.id,
            'dias_locacao': 45, 'data_vencimento': '2026-08-10',
            'orcamento_item_id': item_orcamento_locacao.id,
        })
        check('equipamento locado pode ser alocado à obra', response.status_code == 201, response.status_code)
        alocacao = response.get_json()
        pagamentos_locacao = alocacao.get('financeiro', [])
        check('locacao gera parcelas financeiras proporcionais', len(pagamentos_locacao) == 2, len(pagamentos_locacao))
        check('locacao gera valor de 45 dias', round(sum(p['valor'] for p in pagamentos_locacao), 2) == 375, pagamentos_locacao)
        check('primeiro vencimento respeita a data informada', pagamentos_locacao[0]['data_vencimento'] == '2026-08-10', pagamentos_locacao[0]['data_vencimento'] if pagamentos_locacao else None)
        response = client.get(
            f'/sid/cronograma-financeiro/{obra_a.id}/pagamentos-futuros', headers=headers_financeiro,
        )
        financeiros_obra = response.get_json() if response.status_code == 200 else []
        ids_locacao = {pagamento['id'] for pagamento in pagamentos_locacao}
        ids_no_financeiro = {pagamento.get('id') for pagamento in financeiros_obra}
        check(
            'locacao aparece no financeiro da obra',
            response.status_code == 200 and ids_locacao.issubset(ids_no_financeiro),
            {'status': response.status_code, 'ids': ids_no_financeiro},
        )

        primeiro_pagamento_id = pagamentos_locacao[0]['id']
        response = client.post(
            f'/obras/{obra_a.id}/cronograma/marcar-multiplos-pagos', headers=headers_financeiro,
            json={'itens': [{'tipo': 'futuro', 'id': primeiro_pagamento_id}], 'data_pagamento': '2026-08-10'},
        )
        check('baixa no financeiro da obra funciona', response.status_code == 200 and response.get_json()['resultados'][0]['status'] == 'success', response.status_code)
        lancamento_locacao = Lancamento.query.filter_by(
            almoxarifado_movimentacao_id=alocacao['movimentacao']['id'], status='Pago',
        ).first()
        check('baixa conserva vinculo ao item de orcamento', lancamento_locacao and lancamento_locacao.orcamento_item_id == item_orcamento_locacao.id, lancamento_locacao.orcamento_item_id if lancamento_locacao else None)
        response = client.get(
            f'/obras/{obra_a.id}/orcamento-eng/itens/{item_orcamento_locacao.id}/pagamentos', headers=headers_financeiro,
        )
        pagamentos_orcamento = response.get_json() if response.status_code == 200 else {}
        check('baixa aparece como pago no orcamento', pagamentos_orcamento.get('total') == 250, pagamentos_orcamento.get('total'))

        resumo = client.get('/almoxarifado/dashboard', headers=headers_financeiro).get_json()['resumo']
        check('resumo do almox mostra locacao pendente', resumo['locacoes_financeiro_pendente'] == 125, resumo['locacoes_financeiro_pendente'])
        check('resumo do almox mostra locacao paga', resumo['locacoes_financeiro_pago'] == 250, resumo['locacoes_financeiro_pago'])
        response = client.get('/home/obras', headers=headers_almox)
        home_operacional = response.get_json().get('operacional', {}) if response.status_code == 200 else {}
        check('dashboard principal recebe resumo operacional', response.status_code == 200 and home_operacional.get('disponivel'), response.status_code)
        check('dashboard principal recebe valor de locacao', home_operacional.get('valor_locacao_mensal') == 500, home_operacional.get('valor_locacao_mensal'))
        response = client.get('/home/obras', headers=headers_sem_modulo)
        check('dashboard sem Almox nao recebe resumo de estoque', response.status_code == 200 and response.get_json().get('operacional') == {'disponivel': False}, response.status_code)
        check('locação ativa preserva quantidade fora do estoque', resumo['locacoes_ativas'] == 2, resumo['locacoes_ativas'])
        check('valor mensal de locação é calculado', resumo['valor_locacao_mensal'] == 500, resumo['valor_locacao_mensal'])
        check('equipamento alocado reduz o estoque disponível', resumo['equipamentos_estoque'] == 1, resumo['equipamentos_estoque'])

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
