"""Teste funcional e de segurança do Planejamento em SQLite, sem banco real.

O cenário cria quatro obras, quatro cronogramas, itens de orçamento e atividades
complementares que não existem no orçamento. Depois percorre os fluxos públicos do
blueprint usando JWT e valida isolamento entre obras, regras e importações.

Uso: cd backend && python scripts/smoke_planejamento_local.py
"""
import io
import os
import sys
from datetime import date, timedelta


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
import models  # noqa: F401 - registra o metadata
from models import (
    CronogramaObra,
    Obra,
    OrcamentoEngEtapa,
    OrcamentoEngItem,
    PlanejamentoAtividade,
    PlanejamentoFechamento,
    PlanejamentoRestricao,
    User,
)
from routes.planejamento import planejamento_bp


app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JWT_SECRET_KEY='planejamento-smoke-secret-with-32-bytes',
    RATELIMIT_ENABLED=False,
    TESTING=True,
)
db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)
app.register_blueprint(planejamento_bp)

TABLES = [
    'user',
    'user_obra_association',
    'obra',
    'orcamento_eng_etapa',
    'orcamento_eng_item',
    'cronograma_obra',
    'planejamento_atividade',
    'planejamento_apontamento',
    'planejamento_restricao',
    'planejamento_fechamento',
]

passed = []
failed = []


def check(label, condition, detail=''):
    if condition:
        passed.append(label)
        print(f'  PASS  {label}')
    else:
        failed.append(label)
        print(f'  FAIL  {label}  {detail}')


def body(response):
    return response.get_json(silent=True)


def auth(user):
    token = create_access_token(identity=str(user.id))
    return {'Authorization': f'Bearer {token}'}


with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[name] for name in TABLES])

    monday = date.today() - timedelta(days=date.today().weekday())
    works = [Obra(nome=f'Obra Planejada {index}', cliente=f'Cliente {index}') for index in range(1, 5)]
    master = User(username='planejamento_master', role='master', modulos_permitidos=['obras'])
    admin = User(username='planejamento_admin', role='administrador', modulos_permitidos=['obras'])
    common = User(username='planejamento_comum', role='comum', modulos_permitidos=['obras'])
    other = User(username='planejamento_outra_obra', role='comum', modulos_permitidos=['obras'])
    blocked = User(username='planejamento_sem_modulo', role='comum', modulos_permitidos=['frota'])
    for user in (master, admin, common, other, blocked):
        user.set_password('smoke123')
    common.obras_permitidas.append(works[0])
    other.obras_permitidas.append(works[1])
    blocked.obras_permitidas.append(works[0])
    db.session.add_all([*works, master, admin, common, other, blocked])
    db.session.flush()

    schedules = []
    budget_items = []
    for index, work in enumerate(works, start=1):
        schedule = CronogramaObra(
            obra_id=work.id,
            servico_nome=f'Cronograma integrado da obra {index}',
            ordem=1,
            data_inicio=monday,
            data_fim_prevista=monday + timedelta(days=27),
            tipo_medicao='empreitada',
        )
        stage = OrcamentoEngEtapa(
            obra_id=work.id,
            codigo=f'{index:02d}',
            nome=f'Etapa orçada {index}',
            ordem=1,
        )
        db.session.add_all([schedule, stage])
        db.session.flush()
        first = OrcamentoEngItem(
            etapa_id=stage.id,
            codigo=f'{index:02d}.01',
            descricao=f'Serviço orçado principal {index}',
            unidade='m2',
            quantidade=100 + index,
            preco_unitario=25,
            tipo_composicao='composto',
            ordem=1,
        )
        second = OrcamentoEngItem(
            etapa_id=stage.id,
            codigo=f'{index:02d}.02',
            descricao=f'Serviço orçado secundário {index}',
            unidade='un',
            quantidade=10 + index,
            preco_unitario=50,
            tipo_composicao='composto',
            ordem=2,
        )
        db.session.add_all([first, second])
        db.session.flush()
        schedules.append(schedule)
        budget_items.append((first, second))
    db.session.commit()

    h_master = auth(master)
    h_admin = auth(admin)
    h_common = auth(common)
    h_other = auth(other)
    h_blocked = auth(blocked)

    with app.test_client() as client:
        print('\n=== Autenticação e isolamento ===')
        response = client.get('/planejamento/painel')
        check('painel sem JWT -> 401', response.status_code == 401, response.status_code)
        response = client.get('/planejamento/painel', headers=h_blocked)
        check('usuário sem módulo Obras -> 403', response.status_code == 403, body(response))
        response = client.get(
            f'/obras/{works[1].id}/planejamento/atividades', headers=h_common
        )
        check('usuário comum não acessa outra obra -> 403', response.status_code == 403)
        response = client.post(
            f'/obras/{works[0].id}/planejamento/importar-orcamento',
            json={'item_ids': [budget_items[1][0].id]},
            headers=h_common,
        )
        check('item de orçamento de outra obra é rejeitado -> 400', response.status_code == 400)

        print('\n=== Quatro obras e quatro cronogramas completos ===')
        imported_activity_ids = []
        manual_activity_ids = []
        for index, (work, schedule, items) in enumerate(
            zip(works, schedules, budget_items), start=1
        ):
            response = client.get(
                f'/obras/{work.id}/planejamento/orcamento-disponivel', headers=h_master
            )
            available = body(response)
            check(
                f'obra {index}: lista os 2 itens disponíveis',
                response.status_code == 200
                and sum(len(stage['itens']) for stage in available['etapas']) == 2,
                available,
            )

            response = client.post(
                f'/obras/{work.id}/planejamento/importar-orcamento',
                json={
                    'item_ids': [items[0].id],
                    'padroes': {
                        'cronograma_id': schedule.id,
                        'data_inicio': monday.isoformat(),
                        'data_fim': (monday + timedelta(days=4)).isoformat(),
                        'responsavel': f'Encarregado {index}',
                        'equipe': f'Equipe {index}',
                        'prioridade': 'alta' if index == 1 else 'normal',
                    },
                },
                headers=h_master,
            )
            imported = body(response)
            check(
                f'obra {index}: importa atividade do orçamento e liga ao cronograma',
                response.status_code == 201
                and len(imported['criados']) == 1
                and imported['criados'][0]['origem'] == 'orcamento'
                and imported['criados'][0]['cronograma_id'] == schedule.id
                and imported['criados'][0]['orcamento_item_id'] == items[0].id,
                imported,
            )
            imported_activity_ids.append(imported['criados'][0]['id'])

            response = client.post(
                f'/obras/{work.id}/planejamento/atividades',
                json={
                    'titulo': f'Atividade necessária fora do orçamento {index}',
                    'etapa_nome': 'Complementos de campo',
                    'cronograma_id': schedule.id,
                    'data_inicio': (monday + timedelta(days=2)).isoformat(),
                    'data_fim': (monday + timedelta(days=6)).isoformat(),
                    'quantidade_planejada': 5 + index,
                    'unidade': 'un',
                    'responsavel': f'Mestre {index}',
                    'observacoes': 'Item identificado durante o planejamento e ausente no orçamento.',
                },
                headers=h_master,
            )
            manual = body(response)
            check(
                f'obra {index}: inclui item ausente no orçamento',
                response.status_code == 201
                and manual['origem'] == 'manual'
                and manual['orcamento_item_id'] is None
                and manual['cronograma_id'] == schedule.id,
                manual,
            )
            manual_activity_ids.append(manual['id'])

            response = client.post(
                f'/obras/{work.id}/planejamento/importar-orcamento',
                json={'item_ids': [items[0].id]},
                headers=h_master,
            )
            duplicate = body(response)
            check(
                f'obra {index}: reimportação não duplica item',
                response.status_code == 201
                and duplicate['criados'] == []
                and duplicate['ignorados'] == [items[0].id],
                duplicate,
            )

        check('foram criados exatamente 4 cronogramas', CronogramaObra.query.count() == 4)
        check('as 4 obras possuem planejamento', PlanejamentoAtividade.query.count() == 8)

        print('\n=== Consulta, edição e concorrência ===')
        activity_id = imported_activity_ids[0]
        response = client.get(f'/planejamento/atividades/{activity_id}', headers=h_common)
        activity = body(response)
        check('detalhe da atividade permitida -> 200', response.status_code == 200)
        response = client.put(
            f'/planejamento/atividades/{activity_id}',
            json={'versao': activity['versao'], 'responsavel': 'Novo responsável'},
            headers=h_common,
        )
        updated = body(response)
        check(
            'edição atualiza responsável e versão',
            response.status_code == 200
            and updated['responsavel'] == 'Novo responsável'
            and updated['versao'] > activity['versao'],
            updated,
        )
        response = client.put(
            f'/planejamento/atividades/{activity_id}',
            json={'versao': activity['versao'], 'equipe': 'Edição antiga'},
            headers=h_common,
        )
        check('versão concorrente antiga -> 409', response.status_code == 409, body(response))
        response = client.put(
            f'/planejamento/atividades/{activity_id}',
            json={'data_fim': (monday - timedelta(days=1)).isoformat()},
            headers=h_common,
        )
        check('edição com intervalo inválido -> 400', response.status_code == 400, body(response))
        response = client.get(
            f'/obras/{works[0].id}/planejamento/atividades?busca=%27%20OR%201%3D1%20--',
            headers=h_common,
        )
        check('busca maliciosa permanece parametrizada', response.status_code == 200 and body(response)['total'] == 0)

        print('\n=== Importação de planilha ===')
        csv_content = (
            'Atividade;Etapa;Quantidade;Unidade;Inicio;Fim;Responsavel\n'
            f'Checklist de qualidade;Qualidade;3;un;{monday.isoformat()};'
            f'{(monday + timedelta(days=1)).isoformat()};Engenheira de qualidade\n'
        ).encode('utf-8')
        response = client.post(
            f'/obras/{works[0].id}/planejamento/importar-planilha',
            data={'arquivo': (io.BytesIO(csv_content), 'planejamento.csv')},
            headers=h_common,
            content_type='multipart/form-data',
        )
        preview = body(response)
        check(
            'CSV gera prévia sem gravar',
            response.status_code == 200 and preview['valido'] and preview['total'] == 1,
            preview,
        )
        before_confirm = PlanejamentoAtividade.query.count()
        response = client.post(
            f'/obras/{works[0].id}/planejamento/importar-planilha',
            data={
                'confirmar': 'true',
                'arquivo': (io.BytesIO(csv_content), 'planejamento.csv'),
            },
            headers=h_common,
            content_type='multipart/form-data',
        )
        spreadsheet = body(response)
        check(
            'confirmação do CSV cria atividade de origem planilha',
            response.status_code == 201
            and spreadsheet['criados'][0]['origem'] == 'planilha'
            and PlanejamentoAtividade.query.count() == before_confirm + 1,
            spreadsheet,
        )
        response = client.post(
            f'/obras/{works[0].id}/planejamento/importar-planilha',
            data={'arquivo': (io.BytesIO(b'invalido'), 'arquivo.exe')},
            headers=h_common,
            content_type='multipart/form-data',
        )
        check('extensão de planilha não permitida -> 400', response.status_code == 400)
        too_many_rows = ('Atividade\n' + '\n'.join(f'Linha {index}' for index in range(501))).encode('utf-8')
        response = client.post(
            f'/obras/{works[0].id}/planejamento/importar-planilha',
            data={'arquivo': (io.BytesIO(too_many_rows), 'muitas-linhas.csv')},
            headers=h_common,
            content_type='multipart/form-data',
        )
        check('CSV acima de 500 atividades -> 400', response.status_code == 400)
        too_many_columns = (';'.join(['Atividade', *[f'Campo {index}' for index in range(30)]]) + '\n' + ';'.join(['Teste', *(['x'] * 30)])).encode('utf-8')
        response = client.post(
            f'/obras/{works[0].id}/planejamento/importar-planilha',
            data={'arquivo': (io.BytesIO(too_many_columns), 'muitas-colunas.csv')},
            headers=h_common,
            content_type='multipart/form-data',
        )
        check('CSV acima de 30 colunas -> 400', response.status_code == 400)
        response = client.post(
            f'/obras/{works[0].id}/planejamento/importar-planilha',
            data={'arquivo': (io.BytesIO(b'x' * (2 * 1024 * 1024 + 1)), 'grande.csv')},
            headers=h_common,
            content_type='multipart/form-data',
        )
        check('arquivo acima de 2 MB -> 400', response.status_code == 400)

        print('\n=== Produção e impedimentos ===')
        response = client.post(
            f'/planejamento/atividades/{activity_id}/apontamentos',
            json={'quantidade': 40, 'data_apontamento': monday.isoformat(), 'observacao': 'Produção do dia'},
            headers=h_common,
        )
        progressed = body(response)
        check(
            'apontamento positivo atualiza execução e andamento',
            response.status_code == 201
            and progressed['atividade']['quantidade_executada'] == 40
            and progressed['atividade']['status'] == 'em_andamento',
            progressed,
        )
        response = client.post(
            f'/planejamento/atividades/{activity_id}/apontamentos',
            json={'quantidade': 0},
            headers=h_common,
        )
        check('apontamento zero -> 400', response.status_code == 400)
        response = client.post(
            f'/planejamento/atividades/{imported_activity_ids[1]}/apontamentos',
            json={'quantidade': 1},
            headers=h_common,
        )
        check('usuário não aponta produção em outra obra -> 403', response.status_code == 403)
        response = client.post(
            f'/planejamento/atividades/{activity_id}/restricoes',
            json={'descricao': 'Sem material'},
            headers=h_common,
        )
        check('impedimento sem tipo -> 400', response.status_code == 400, body(response))
        response = client.post(
            f'/planejamento/atividades/{activity_id}/restricoes',
            json={
                'tipo': 'material',
                'descricao': 'Blocos ainda não entregues',
                'responsavel': 'Suprimentos',
                'data_limite': (monday + timedelta(days=1)).isoformat(),
            },
            headers=h_common,
        )
        restricted = body(response)
        restriction_id = restricted['restricao']['id']
        check(
            'novo impedimento bloqueia a atividade',
            response.status_code == 201 and restricted['atividade']['status'] == 'impedido',
            restricted,
        )
        response = client.patch(
            f'/planejamento/restricoes/{restriction_id}',
            json={'status': 'resolvida', 'observacoes': 'Material entregue'},
            headers=h_common,
        )
        resolved = body(response)
        check(
            'resolver impedimento restaura status automático',
            response.status_code == 200
            and resolved['restricao']['resolvida_em']
            and resolved['atividade']['status'] == 'em_andamento',
            resolved,
        )

        print('\n=== Fechamento semanal, remoção e painel global ===')
        response = client.post(
            f'/obras/{works[0].id}/planejamento/fechamentos',
            json={'semana_inicio': monday.isoformat()},
            headers=h_common,
        )
        check('usuário comum não fecha semana -> 403', response.status_code == 403)
        response = client.post(
            f'/obras/{works[0].id}/planejamento/fechamentos',
            json={
                'semana_inicio': monday.isoformat(),
                'motivos_nao_conclusao': {'material': 1},
                'aprendizado': 'Antecipar a compra de materiais críticos.',
            },
            headers=h_admin,
        )
        closing = body(response)
        check(
            'administrador fecha semana e calcula PPC',
            response.status_code == 201
            and closing['planejadas'] >= 2
            and 0 <= closing['ppc'] <= 100,
            closing,
        )
        response = client.get(
            f'/obras/{works[0].id}/planejamento/fechamentos', headers=h_common
        )
        check('histórico de fechamentos retorna a semana', response.status_code == 200 and len(body(response)) == 1)
        response = client.delete(
            f'/planejamento/atividades/{manual_activity_ids[0]}', headers=h_common
        )
        check('usuário comum não exclui atividade -> 403', response.status_code == 403)

        response = client.post(
            f'/obras/{works[0].id}/planejamento/atividades',
            json={'titulo': 'Atividade temporária para excluir'},
            headers=h_master,
        )
        temporary_id = body(response)['id']
        response = client.delete(
            f'/planejamento/atividades/{temporary_id}', headers=h_master
        )
        check('master exclui atividade -> 200', response.status_code == 200)

        response = client.get('/planejamento/painel', headers=h_common)
        common_panel = body(response)
        check(
            'painel comum contém somente a obra permitida',
            response.status_code == 200
            and len(common_panel['obras']) == 1
            and common_panel['obras'][0]['id'] == works[0].id
            and all(item['obra_id'] == works[0].id for item in common_panel['atividades']),
            common_panel,
        )
        response = client.get('/planejamento/painel', headers=h_master)
        master_panel = body(response)
        check(
            'painel master consolida as 4 obras',
            response.status_code == 200 and len(master_panel['obras']) == 4,
            master_panel,
        )
        check('fechamento foi persistido uma única vez', PlanejamentoFechamento.query.count() == 1)
        check('impedimento resolvido foi preservado no histórico', PlanejamentoRestricao.query.count() == 1)

print(f'\n{"=" * 52}')
print(f'PASS: {len(passed)}  FAIL: {len(failed)}')
if failed:
    print('Falhas:')
    for failure in failed:
        print(f'  - {failure}')
    sys.exit(1)
print('Todos os cenários do Planejamento passaram.')
