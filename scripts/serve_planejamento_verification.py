"""Servidor local descartável para verificação visual do Planejamento.

Usa SQLite em memória e nunca acessa o banco real. Credenciais: teste / teste123.
"""
import os
import sys
from datetime import date, timedelta


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request
from flask_cors import CORS

from extensions import db, jwt, limiter
import models  # noqa: F401
from models import (
    CronogramaObra,
    Obra,
    OrcamentoEngEtapa,
    OrcamentoEngItem,
    PlanejamentoAtividade,
    PlanejamentoRestricao,
    User,
)
from routes.auth import auth_bp
from routes.planejamento import planejamento_bp
from services.auth_service import get_current_user, user_has_access_to_obra
from flask_jwt_extended import jwt_required


app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JWT_SECRET_KEY='planejamento-browser-verification-secret',
    RATELIMIT_ENABLED=False,
)
CORS(app, origins=['http://localhost:3000', 'http://127.0.0.1:3000'])
db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(planejamento_bp)

TABLES = [
    'user', 'user_obra_association', 'obra', 'orcamento_eng_etapa',
    'orcamento_eng_item', 'cronograma_obra', 'planejamento_atividade',
    'planejamento_apontamento', 'planejamento_restricao', 'planejamento_fechamento',
]


def allowed_work(work_id):
    user = get_current_user()
    return user and user_has_access_to_obra(user, work_id)


@app.after_request
def log_verification_response(response):
    print(f'{request.method} {request.path} -> {response.status_code}', flush=True)
    return response


@app.get('/notificacoes/count')
@jwt_required()
def notification_count():
    return jsonify({'count': 0})


@app.get('/notificacoes')
@jwt_required()
def notifications():
    return jsonify([])


@app.get('/me')
@jwt_required()
def current_profile():
    user = get_current_user()
    return jsonify(user.to_dict())


@app.get('/home/alertas')
@jwt_required()
def home_alerts():
    return jsonify({
        'pendencias': [],
        'resumo': {
            'obras': {'vencidos': 0, 'a_vencer': 0},
            'admin': {'vencidos': 0, 'a_vencer': 0},
        },
    })


@app.get('/obras')
@jwt_required()
def list_works():
    user = get_current_user()
    rows = Obra.query.order_by(Obra.id).all()
    accessible = [row for row in rows if user_has_access_to_obra(user, row.id)]
    return jsonify([{
        **row.to_dict(),
        'orcamento_total': 1850000 + index * 250000,
        'total_pago': 620000 + index * 125000,
        'liberado_pagamento': 80000 + index * 10000,
        'despesas_extras': 12000 + index * 2000,
        'valor_vencido': index * 12800,
        'valor_a_vencer_mes': 32000 + index * 11350,
    } for index, row in enumerate(accessible)])


@app.get('/obras/<int:work_id>')
@jwt_required()
def work_detail(work_id):
    if not allowed_work(work_id):
        return jsonify({'erro': 'Acesso negado.'}), 403
    work = db.session.get(Obra, work_id)
    return jsonify({
        'obra': work.to_dict(),
        'lancamentos': [],
        'servicos': [],
        'orcamentos': [],
        'historico_unificado': [],
        'sumarios': {
            'orcamento_total': 1850000,
            'valores_pagos': 620000,
            'liberado_pagamento': 80000,
            'despesas_extras': 12000,
        },
    })


@app.get('/cronograma/<int:work_id>')
@app.get('/obras/<int:work_id>/cronograma')
@jwt_required()
def schedules(work_id):
    if not allowed_work(work_id):
        return jsonify({'erro': 'Acesso negado.'}), 403
    rows = CronogramaObra.query.filter_by(obra_id=work_id).order_by(CronogramaObra.id).all()
    return jsonify([{
        'id': row.id,
        'obra_id': row.obra_id,
        'servico_nome': row.servico_nome,
        'data_inicio': row.data_inicio.isoformat(),
        'data_fim_prevista': row.data_fim_prevista.isoformat(),
        'percentual_conclusao': row.percentual_conclusao,
        'etapas': [],
    } for row in rows])


@app.get('/obras/<int:work_id>/orcamento-eng/itens-lista')
@jwt_required()
def budget_dropdown(work_id):
    if not allowed_work(work_id):
        return jsonify({'erro': 'Acesso negado.'}), 403
    return jsonify([])


@app.get('/obras/<int:work_id>/notas-fiscais')
@jwt_required()
def invoices(work_id):
    if not allowed_work(work_id):
        return jsonify({'erro': 'Acesso negado.'}), 403
    return jsonify([])


with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[name] for name in TABLES])
    monday = date.today() - timedelta(days=date.today().weekday())
    works = [Obra(nome=name, cliente=client) for name, client in (
        ('Residencial Aurora', 'Incorporadora Horizonte'),
        ('Edifício Atlântico', 'Grupo Mar'),
        ('Centro Logístico Norte', 'Log Norte'),
        ('Reforma Hospital Central', 'Fundação Saúde'),
    )]
    user = User(username='teste', role='master', modulos_permitidos=['obras'])
    user.set_password('teste123')
    db.session.add_all([*works, user])
    db.session.flush()
    statuses = ('em_andamento', 'pronto', 'impedido', 'a_planejar')
    for index, work in enumerate(works):
        schedule = CronogramaObra(
            obra_id=work.id,
            servico_nome=f'Cronograma principal · {work.nome}',
            ordem=1,
            data_inicio=monday,
            data_fim_prevista=monday + timedelta(days=28),
        )
        stage = OrcamentoEngEtapa(obra_id=work.id, codigo='01', nome='Estrutura', ordem=1)
        db.session.add_all([schedule, stage])
        db.session.flush()
        item = OrcamentoEngItem(
            etapa_id=stage.id,
            codigo=f'01.0{index + 1}',
            descricao=f'Executar serviço orçado · {work.nome}',
            unidade='m2',
            quantidade=100 + index * 20,
            preco_unitario=80,
            tipo_composicao='composto',
        )
        db.session.add(item)
        db.session.flush()
        activity = PlanejamentoAtividade(
            obra_id=work.id,
            orcamento_item_id=item.id,
            cronograma_id=schedule.id,
            titulo=item.descricao,
            etapa_nome='Estrutura',
            origem='orcamento',
            status=statuses[index],
            responsavel=f'Encarregado {index + 1}',
            equipe=f'Equipe {chr(65 + index)}',
            data_inicio=None if index == 3 else monday + timedelta(days=index),
            data_fim=None if index == 3 else monday + timedelta(days=index + 3),
            quantidade_planejada=item.quantidade,
            quantidade_executada=25 if index in (0, 2) else 0,
            unidade=item.unidade,
            criado_por_user_id=user.id,
        )
        manual = PlanejamentoAtividade(
            obra_id=work.id,
            cronograma_id=schedule.id,
            titulo=f'Complemento ausente do orçamento · {work.nome}',
            etapa_nome='Complementos',
            origem='manual',
            status='pronto',
            responsavel=f'Mestre {index + 1}',
            data_inicio=monday + timedelta(days=2),
            data_fim=monday + timedelta(days=5),
            quantidade_planejada=8,
            unidade='un',
            criado_por_user_id=user.id,
        )
        db.session.add_all([activity, manual])
        db.session.flush()
        if index == 2:
            db.session.add(PlanejamentoRestricao(
                atividade_id=activity.id,
                tipo='material',
                descricao='Material aguardando entrega',
                responsavel='Suprimentos',
                data_limite=monday + timedelta(days=1),
                criada_por_user_id=user.id,
            ))
    db.session.commit()


if __name__ == '__main__':
    print('Planejamento verification API: http://127.0.0.1:5055')
    print('Login: teste / teste123')
    app.run(host='127.0.0.1', port=5055, debug=False, use_reloader=False)
