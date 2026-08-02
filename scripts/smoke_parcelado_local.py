"""
Regressão local do fluxo financeiro — SQLite in-memory, sem banco real.

Valida pagamento à vista e parcelado, entrada, boletos customizados, vínculo
com orçamento, baixa de parcela, consolidação sem duplicidade, histórico geral,
edição estrutural e isolamento de acesso entre obras.

Uso: cd backend && python scripts/smoke_parcelado_local.py
"""
import os
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401
from models import (
    Boleto,
    Lancamento,
    Obra,
    OrcamentoEngEtapa,
    OrcamentoEngItem,
    PagamentoFuturo,
    PagamentoParcelado,
    PagamentoServico,
    ParcelaIndividual,
    Servico,
    User,
)
from routes.cronograma import cronograma_bp
from routes.lancamentos import lancamentos_bp
from routes.obras import obras_bp
from routes.sid import sid_bp
from services.financeiro_service import calcular_totais_pagos_obra

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret-with-at-least-32-bytes'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(cronograma_bp)
app.register_blueprint(lancamentos_bp)
app.register_blueprint(obras_bp)
app.register_blueprint(sid_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'lancamento', 'servico',
    'pagamento_servico', 'pagamento_parcelado_v2', 'parcela_individual',
    'pagamento_futuro', 'boleto', 'orcamento_eng_etapa', 'orcamento_eng_item',
    'orcamento', 'anexo_orcamento', 'notificacao',
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


hoje = date.today()

with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])
    obra = Obra(nome='Obra Parcelado Smoke')
    outra_obra = Obra(nome='Outra obra isolada')
    master = User(username='master_smoke', role='master')
    master.set_password('x')
    sem_acesso = User(username='sem_acesso_smoke', role='comum')
    sem_acesso.set_password('x')
    db.session.add_all([obra, outra_obra, master, sem_acesso])
    db.session.flush()
    servico = Servico(obra_id=obra.id, nome='Serviço vinculado')
    outro_servico = Servico(obra_id=outra_obra.id, nome='Serviço de outra obra')
    etapa = OrcamentoEngEtapa(obra_id=obra.id, codigo='01', nome='Etapa teste', ordem=1)
    db.session.add_all([servico, outro_servico, etapa])
    db.session.flush()
    item_orcamento = OrcamentoEngItem(
        etapa_id=etapa.id,
        codigo='01.01',
        descricao='Item vinculado',
        unidade='un',
        quantidade=1,
        tipo_composicao='composto',
        preco_unitario=1000,
        servico_id=servico.id,
    )
    db.session.add(item_orcamento)
    outra_etapa = OrcamentoEngEtapa(obra_id=outra_obra.id, codigo='99', nome='Etapa isolada', ordem=1)
    db.session.add(outra_etapa)
    db.session.flush()
    outro_item = OrcamentoEngItem(
        etapa_id=outra_etapa.id,
        codigo='99.01',
        descricao='Item de outra obra',
        unidade='un',
        quantidade=1,
        tipo_composicao='composto',
        preco_unitario=500,
    )
    db.session.add(outro_item)
    db.session.commit()
    obra_id = obra.id
    h = {'Authorization': f'Bearer {create_access_token(identity=str(master.id), additional_claims={"role": master.role})}'}
    h_sem_acesso = {
        'Authorization': f'Bearer {create_access_token(identity=str(sem_acesso.id), additional_claims={"role": sem_acesso.role})}'
    }

    with app.test_client() as c:
        def parcelas_de(pid):
            return (ParcelaIndividual.query.filter_by(pagamento_parcelado_id=pid)
                    .order_by(ParcelaIndividual.numero_parcela).all())

        print('\n=== fix 2: centavos (1000/3) ===')
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Centavos', 'valor': 1000, 'tipo': 'Material',
            'status': 'A Pagar', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 3,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
        })
        check('POST parcelado 1000/3 -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        pid = json.loads(r.data)['pagamento_parcelado']['id']
        ps = parcelas_de(pid)
        valores = [p.valor_parcela for p in ps]
        check('parcelas 333.33/333.33/333.34', valores == [333.33, 333.33, 333.34], f'got {valores}')
        check('soma fecha 1000.00', round(sum(valores), 2) == 1000.00)

        print('\n=== fix 1: parcelas_customizadas (boleto) ===')
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Boletos custom', 'valor': 900, 'tipo': 'Material',
            'status': 'A Pagar', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 2,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
            'meio_pagamento': 'Boleto',
            'parcelas_customizadas': [
                {'numero': 1, 'valor': '400.00', 'data_vencimento': hoje.isoformat(),
                 'codigo_barras': '11111111111111111111111111111111111111111111111'},
                {'numero': 2, 'valor': '550.00',
                 'data_vencimento': (hoje + timedelta(days=45)).isoformat(),
                 'codigo_barras': '22222222222222222222222222222222222222222222222'},
            ],
        })
        check('POST boletos custom -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        body = json.loads(r.data)['pagamento_parcelado']
        pid2 = body['id']
        ps2 = parcelas_de(pid2)
        check('valores customizados persistidos', [p.valor_parcela for p in ps2] == [400.00, 550.00],
              f'got {[p.valor_parcela for p in ps2]}')
        check('códigos de barras persistidos',
              [p.codigo_barras[:2] for p in ps2] == ['11', '22'],
              f'got {[p.codigo_barras for p in ps2]}')
        check('data customizada da 2ª parcela',
              ps2[1].data_vencimento == hoje + timedelta(days=45))
        check('valor_total ajustado p/ soma dos boletos (950)', body['valor_total'] == 950.0,
              f"got {body['valor_total']}")

        print('\n=== fix 4: criação Pago com entrada ===')
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Pago com entrada', 'valor': 1000, 'tipo': 'Material',
            'status': 'Pago', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 2,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
            'tem_entrada': True, 'valor_entrada': 200, 'percentual_entrada': 20,
            'data_entrada': hoje.isoformat(),
        })
        check('POST Pago com entrada -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        body = json.loads(r.data)['pagamento_parcelado']
        pid3 = body['id']
        ps3 = parcelas_de(pid3)
        check('3 linhas (entrada + 2 parcelas)', len(ps3) == 3, f'got {len(ps3)}')
        check('entrada nasce Paga com data', ps3[0].numero_parcela == 0
              and ps3[0].status == 'Pago' and ps3[0].data_pagamento is not None,
              f'got {ps3[0].status}/{ps3[0].data_pagamento}')
        check('todas pagas', all(p.status == 'Pago' for p in ps3))
        check('contador = 3 (linhas) e Concluído',
              body['parcelas_pagas'] == 3 and body['status'] == 'Concluído',
              f"got {body['parcelas_pagas']}/{body['status']}")
        check('parcelas 400+400 e entrada 200 fecham 1000',
              round(sum(p.valor_parcela for p in ps3), 2) == 1000.00)

        print('\n=== rastreabilidade e fonte financeira única ===')
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', json={
            'descricao': 'Tentativa sem token', 'valor': 10, 'tipo': 'Material',
            'status': 'Pago', 'data': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
        })
        check('requisição sem autenticação é rejeitada -> 401', r.status_code == 401,
              f'{r.status_code}: {r.data[:300]}')

        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h_sem_acesso, json={
            'descricao': 'Tentativa sem acesso', 'valor': 10, 'tipo': 'Material',
            'status': 'Pago', 'data': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
        })
        check('usuário sem acesso à obra é rejeitado -> 403', r.status_code == 403,
              f'{r.status_code}: {r.data[:300]}')

        casos_invalidos = [
            ('descrição vazia', {
                'descricao': ' ', 'valor': 10, 'tipo': 'Material', 'status': 'Pago',
                'data': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
            }),
            ('valor zero', {
                'descricao': 'Valor inválido', 'valor': 0, 'tipo': 'Material', 'status': 'Pago',
                'data': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
            }),
            ('status inválido', {
                'descricao': 'Status inválido', 'valor': 10, 'tipo': 'Material', 'status': 'Cancelado',
                'data': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
            }),
            ('data inválida', {
                'descricao': 'Data inválida', 'valor': 10, 'tipo': 'Material', 'status': 'Pago',
                'data': 'não-é-data', 'tipo_forma_pagamento': 'avista',
            }),
            ('mais de 60 parcelas', {
                'descricao': 'Parcelamento inválido', 'valor': 610, 'tipo': 'Material',
                'status': 'A Pagar', 'data': hoje.isoformat(),
                'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 61,
                'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
            }),
            ('entrada igual ao total', {
                'descricao': 'Entrada inválida', 'valor': 100, 'tipo': 'Material',
                'status': 'A Pagar', 'data': hoje.isoformat(),
                'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 2,
                'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
                'tem_entrada': True, 'valor_entrada': 100, 'percentual_entrada': 100,
            }),
            ('serviço de outra obra', {
                'descricao': 'Vínculo cruzado', 'valor': 10, 'tipo': 'Material',
                'status': 'Pago', 'data': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
                'servico_id': outro_servico.id,
            }),
        ]
        for nome_caso, payload in casos_invalidos:
            r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json=payload)
            check(f'{nome_caso} é rejeitado -> 400', r.status_code == 400,
                  f'{r.status_code}: {r.data[:300]}')

        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Tentativa de vínculo cruzado', 'valor': 10, 'tipo': 'Material',
            'status': 'Pago', 'data': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
            'orcamento_item_id': outro_item.id,
        })
        check('item de outra obra é rejeitado -> 400', r.status_code == 400, f'{r.status_code}: {r.data[:300]}')

        r = c.post(f'/obras/{obra_id}/lancamentos', headers=h, json={
            'descricao': 'Agendado ligado ao orçamento', 'valor': 90, 'tipo': 'Material',
            'status': 'A Pagar', 'data': hoje.isoformat(),
            'data_vencimento': hoje.isoformat(), 'servico_id': servico.id,
            'orcamento_item_id': item_orcamento.id,
        })
        check('agendamento geral vinculado -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        futuro_geral = db.session.get(PagamentoFuturo, json.loads(r.data)['id'])
        check('agendamento geral conserva item, serviço e tipo',
              futuro_geral.orcamento_item_id == item_orcamento.id
              and futuro_geral.servico_id == servico.id
              and futuro_geral.tipo == 'Material')

        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Parcelado ligado ao orçamento', 'valor': 600, 'tipo': 'Material',
            'status': 'Pago', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 2,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
            'servico_id': servico.id, 'orcamento_item_id': item_orcamento.id,
        })
        check('parcelado pago e vinculado -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        parcelado_vinculado_id = json.loads(r.data)['pagamento_parcelado']['id']
        parcelado_vinculado = db.session.get(PagamentoParcelado, parcelado_vinculado_id)
        check('parcelamento conserva item do orçamento',
              parcelado_vinculado.orcamento_item_id == item_orcamento.id,
              parcelado_vinculado.orcamento_item_id)
        check('parcelas pagas não criam PagamentoServico duplicado',
              PagamentoServico.query.filter_by(servico_id=servico.id).count() == 0)

        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'À vista ligado ao orçamento', 'valor': 125, 'tipo': 'Mão de Obra',
            'status': 'Pago', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'avista', 'servico_id': servico.id,
            'orcamento_item_id': item_orcamento.id,
        })
        check('à vista pago e vinculado -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        pagamento_avista = PagamentoServico.query.filter_by(servico_id=servico.id).one()
        check('pagamento à vista conserva item do orçamento',
              pagamento_avista.orcamento_item_id == item_orcamento.id,
              pagamento_avista.orcamento_item_id)

        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Futuro ligado ao orçamento', 'valor': 75, 'tipo': 'Material',
            'status': 'A Pagar', 'data': hoje.isoformat(),
            'data_vencimento': hoje.isoformat(), 'tipo_forma_pagamento': 'avista',
            'servico_id': servico.id, 'orcamento_item_id': item_orcamento.id,
        })
        check('futuro vinculado -> 201', r.status_code == 201, f'{r.status_code}: {r.data[:300]}')
        futuro_id = json.loads(r.data)['id']
        check('futuro nasce com vínculo',
              db.session.get(PagamentoFuturo, futuro_id).orcamento_item_id == item_orcamento.id)

        r = c.post(
            f'/obras/{obra_id}/cronograma/marcar-multiplos-pagos',
            headers=h,
            json={'itens': [{'tipo': 'futuro', 'id': futuro_id}], 'data_pagamento': hoje.isoformat()},
        )
        check('baixa do futuro vinculado -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        pagamento_convertido = PagamentoServico.query.filter_by(
            servico_id=servico.id,
            valor_total=75,
        ).one()
        check('baixa conserva item do orçamento',
              pagamento_convertido.orcamento_item_id == item_orcamento.id,
              pagamento_convertido.orcamento_item_id)
        check('futuro convertido é removido sem perder o pagamento',
              db.session.get(PagamentoFuturo, futuro_id) is None)

        print('\n=== fluxo completo: parcela -> orçamento -> histórico geral ===')
        total_antes = calcular_totais_pagos_obra(obra_id)['total']
        r = c.post(f'/obras/{obra_id}/inserir-pagamento', headers=h, json={
            'descricao': 'Parcelado global consolidado', 'valor': 300, 'tipo': 'Material',
            'status': 'A Pagar', 'data': hoje.isoformat(),
            'tipo_forma_pagamento': 'parcelado', 'numero_parcelas': 3,
            'periodicidade': 'Mensal', 'data_primeira_parcela': hoje.isoformat(),
            'orcamento_item_id': item_orcamento.id,
        })
        check('novo parcelado consolidado -> 201', r.status_code == 201,
              f'{r.status_code}: {r.data[:300]}')
        consolidado_id = json.loads(r.data)['pagamento_parcelado']['id']
        consolidado = db.session.get(PagamentoParcelado, consolidado_id)
        check('parcelamento pertence à obra e ao item selecionados',
              consolidado.obra_id == obra_id and consolidado.orcamento_item_id == item_orcamento.id,
              consolidado.to_dict())

        primeira = parcelas_de(consolidado_id)[0]
        r = c.post(
            f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{consolidado_id}/parcelas/{primeira.id}/pagar',
            headers=h_sem_acesso,
            json={'data_pagamento': hoje.isoformat(), 'forma_pagamento': 'PIX'},
        )
        check('usuário sem acesso não pode baixar parcela -> 403', r.status_code == 403,
              f'{r.status_code}: {r.data[:300]}')

        r = c.post(
            f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{consolidado_id}/parcelas/{primeira.id}/pagar',
            headers=h,
            json={'data_pagamento': hoje.isoformat(), 'forma_pagamento': 'PIX'},
        )
        check('baixa da parcela consolidada -> 200', r.status_code == 200,
              f'{r.status_code}: {r.data[:300]}')
        db.session.refresh(primeira)
        check('parcela guarda status, data e forma de pagamento',
              primeira.status == 'Pago' and primeira.data_pagamento == hoje
              and primeira.forma_pagamento == 'PIX', primeira.to_dict())

        pago_no_item = sum(
            parcela.valor_parcela
            for parcela in ParcelaIndividual.query.join(PagamentoParcelado).filter(
                PagamentoParcelado.orcamento_item_id == item_orcamento.id,
                ParcelaIndividual.status == 'Pago',
            ).all()
            if parcela.pagamento_parcelado_id == consolidado_id
        )
        check('orçamento recebe exatamente a parcela paga (R$ 100)',
              pago_no_item == 100.0, f'got {pago_no_item}')
        total_depois = calcular_totais_pagos_obra(obra_id)['total']
        check('consolidado geral aumenta uma única vez em R$ 100',
              round(total_depois - total_antes, 2) == 100.0,
              f'antes={total_antes} depois={total_depois}')

        r = c.get(f'/obras/{obra_id}', headers=h)
        check('dashboard da obra responde após a baixa -> 200', r.status_code == 200,
              f'{r.status_code}: {r.data[:500]}')
        detalhe = json.loads(r.data)
        ocorrencias = [
            item for item in detalhe.get('historico_unificado', [])
            if item.get('pagamento_parcelado_id') == consolidado_id
            and item.get('parcela_id') == primeira.id
        ]
        check('histórico geral contém a parcela paga uma única vez',
              len(ocorrencias) == 1, f'got {ocorrencias}')
        check('histórico conserva obra, orçamento e valor da parcela',
              len(ocorrencias) == 1
              and ocorrencias[0]['orcamento_item_id'] == item_orcamento.id
              and ocorrencias[0]['valor_pago'] == 100.0,
              ocorrencias)
        espelhos = Lancamento.query.filter_by(obra_id=obra_id).filter(
            Lancamento.descricao.like('Parcelado global consolidado (Parcela %')
        ).all()
        check('lançamento-espelho não duplica o histórico nem o consolidado',
              len(espelhos) == 1
              and all(item.get('tipo_registro') != 'lancamento'
                      for item in detalhe.get('historico_unificado', [])
                      if item.get('descricao', '').startswith('Parcelado global consolidado (Parcela ')),
              [espelho.to_dict() for espelho in espelhos])

        print('\n=== fix 3: edição regenera parcelas em aberto ===')
        # paga a 1ª parcela do parcelamento "Centavos" e edita o total
        p1 = parcelas_de(pid)[0]
        r = c.post(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}/parcelas/{p1.id}/pagar',
                   headers=h, json={'data_pagamento': hoje.isoformat()})
        check('pagar 1ª parcela -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:200]}')

        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'valor_total': 1200, 'numero_parcelas': 4})
        check('PUT estrutural -> 200', r.status_code == 200, f'{r.status_code}: {r.data[:300]}')
        ps = parcelas_de(pid)
        pagas = [p for p in ps if p.status == 'Pago']
        abertas = [p for p in ps if p.status != 'Pago']
        check('parcela paga preservada (333.33)', len(pagas) == 1 and pagas[0].valor_parcela == 333.33)
        check('3 novas parcelas em aberto', len(abertas) == 3, f'got {len(abertas)}')
        check('restante 866.67 redistribuído com centavo na última',
              [p.valor_parcela for p in abertas] == [288.89, 288.89, 288.89],
              f'got {[p.valor_parcela for p in abertas]}')
        check('soma total fecha 1200', round(sum(p.valor_parcela for p in ps), 2) == 1200.00,
              f'got {round(sum(p.valor_parcela for p in ps), 2)}')
        pai = db.session.get(PagamentoParcelado, pid)
        check('contador recomputado = 1', pai.parcelas_pagas == 1)
        check('pai continua Ativo', pai.status == 'Ativo')

        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'numero_parcelas': 0})
        check('reduzir abaixo das pagas -> 400', r.status_code == 400, f'got {r.status_code}')

        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'valor_total': 100})
        check('valor_total < soma pagas -> 400', r.status_code == 400, f'got {r.status_code}')

        # status cru não é mais aceito (só Cancelado/Ativo)
        r = c.put(f'/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pid}',
                  headers=h, json={'status': 'Concluído', 'parcelas_pagas': 99})
        pai = db.session.get(PagamentoParcelado, pid)
        check("status 'Concluído' cru ignorado", pai.status == 'Ativo', f'got {pai.status}')
        check('parcelas_pagas cru ignorado', pai.parcelas_pagas == 1, f'got {pai.parcelas_pagas}')

        print('\n=== acesso rápido: vencido e a vencer no mês ===')
        r = c.get('/obras?mostrar_concluidas=true&incluir_arquivadas=true', headers=h)
        linha_base = next(item for item in json.loads(r.data) if item['id'] == obra_id)

        parcelado_resumo = PagamentoParcelado(
            obra_id=obra_id,
            descricao='Parcelas para resumo',
            valor_total=110,
            numero_parcelas=2,
            valor_parcela=55,
            data_primeira_parcela=hoje - timedelta(days=1),
            periodicidade='Mensal',
            parcelas_pagas=0,
            status='Ativo',
        )
        db.session.add(parcelado_resumo)
        db.session.flush()
        db.session.add_all([
            PagamentoFuturo(
                obra_id=obra_id, descricao='Futuro vencido do resumo', valor=10,
                data_vencimento=hoje - timedelta(days=1), status='Previsto',
            ),
            PagamentoFuturo(
                obra_id=obra_id, descricao='Futuro do mês', valor=20,
                data_vencimento=hoje, status='Previsto',
            ),
            Boleto(
                obra_id=obra_id, descricao='Boleto vencido do resumo', valor=30,
                data_vencimento=hoje - timedelta(days=1), status='Vencido',
            ),
            Boleto(
                obra_id=obra_id, descricao='Boleto do mês', valor=40,
                data_vencimento=hoje, status='Pendente',
            ),
            ParcelaIndividual(
                pagamento_parcelado_id=parcelado_resumo.id, numero_parcela=1,
                valor_parcela=50, data_vencimento=hoje - timedelta(days=1), status='Previsto',
            ),
            ParcelaIndividual(
                pagamento_parcelado_id=parcelado_resumo.id, numero_parcela=2,
                valor_parcela=60, data_vencimento=hoje, status='Previsto',
            ),
        ])
        db.session.commit()

        r = c.get('/obras?mostrar_concluidas=true&incluir_arquivadas=true', headers=h)
        linha_atual = next(item for item in json.loads(r.data) if item['id'] == obra_id)
        check('resumo vencido soma futuro, boleto e parcela',
              round(linha_atual['valor_vencido'] - linha_base['valor_vencido'], 2) == 90.0,
              {'base': linha_base, 'atual': linha_atual})
        check('resumo do mês soma futuro, boleto e parcela sem misturar vencidos',
              round(linha_atual['valor_a_vencer_mes'] - linha_base['valor_a_vencer_mes'], 2) == 120.0,
              {'base': linha_base, 'atual': linha_atual})

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
