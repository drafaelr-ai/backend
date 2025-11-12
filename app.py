# ============================================================
# app.py - versão completa, corrigida e compatível com OBRALY
# ============================================================

import os
import datetime
import traceback
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, jwt_required

# ============================================================
# CONFIGURAÇÕES BÁSICAS
# ============================================================

app = Flask(__name__)
CORS(app, resources={r'/*': {'origins': ['https://www.obraly.uk']}}, supports_credentials=True)

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///controle_obra.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'segredo-local')

db = SQLAlchemy(app)
jwt = JWTManager(app)

# ============================================================
# MODELOS
# ============================================================

class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))


class Servico(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    obra = db.relationship('Obra', backref=db.backref('servicos', lazy=True))


class PagamentoServico(db.Model):
    __tablename__ = 'pagamento_servico'
    id = db.Column(db.Integer, primary_key=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    data = db.Column(db.Date, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=True)
    valor_total = db.Column(db.Float, nullable=False)
    valor_pago = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    tipo_pagamento = db.Column(db.String(50))
    fornecedor = db.Column(db.String(150))
    prioridade = db.Column(db.Integer, default=0)
    pix = db.Column(db.String(100))
    servico = db.relationship('Servico', backref=db.backref('pagamentos', lazy=True))


class PagamentoFuturo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    descricao = db.Column(db.String(255))
    valor = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='Previsto')
    fornecedor = db.Column(db.String(150))
    pix = db.Column(db.String(100))
    obra = db.relationship('Obra', backref=db.backref('pagamentos_futuros', lazy=True))


# ============================================================
# ROTA PRINCIPAL
# ============================================================

@app.route('/')
def home():
    return jsonify({'status': 'ok', 'mensagem': 'API Controle de Obras (OBRALY) ativa'}), 200


# ============================================================
# ROTA DE INSERÇÃO DE PAGAMENTO (BOTÃO AZUL)
# ============================================================

@app.route('/pagamentos', methods=['POST'])
@jwt_required(optional=True)
def criar_pagamento():
    try:
        dados = request.json
        data = datetime.date.fromisoformat(dados.get('data'))
        data_vencimento = datetime.date.fromisoformat(dados.get('data_vencimento')) if dados.get('data_vencimento') else None

        novo_pagamento = PagamentoServico(
            servico_id=dados.get('servico_id'),
            data=data,
            data_vencimento=data_vencimento,
            valor_total=dados.get('valor_total'),
            valor_pago=dados.get('valor_pago', 0.0),
            status=dados.get('status', 'A Pagar'),
            tipo_pagamento=dados.get('tipo_pagamento', 'geral'),
            fornecedor=dados.get('fornecedor'),
            prioridade=dados.get('prioridade', 0),
            pix=dados.get('pix')
        )
        db.session.add(novo_pagamento)
        db.session.commit()

        # Adiciona automaticamente no cronograma financeiro
        obra_id = None
        if novo_pagamento.servico:
            obra_id = novo_pagamento.servico.obra_id
        elif dados.get('obra_id'):
            obra_id = dados.get('obra_id')
        else:
            obra_existente = Obra.query.first()
            obra_id = obra_existente.id if obra_existente else None

        if obra_id:
            novo_cronograma = PagamentoFuturo(
                obra_id=obra_id,
                descricao=dados.get('descricao', 'Pagamento planejado'),
                valor=dados.get('valor_total'),
                data_vencimento=data_vencimento or data,
                status='Previsto' if novo_pagamento.status.lower() in ['a pagar', 'previsto'] else 'Pago',
                fornecedor=dados.get('fornecedor'),
                pix=dados.get('pix')
            )
            db.session.add(novo_cronograma)
            db.session.commit()

        return jsonify({'sucesso': True, 'mensagem': 'Pagamento criado e adicionado ao cronograma.'}), 201

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


# ============================================================
# ROTA DE SINCRONIZAÇÃO RETROATIVA
# ============================================================

@app.route('/admin/sincronizar_pagamentos_futuros', methods=['POST'])
def sincronizar_pagamentos_futuros():
    try:
        pagamentos = PagamentoServico.query.all()
        criados = 0
        for pag in pagamentos:
            data_venc = pag.data_vencimento or pag.data
            existe = PagamentoFuturo.query.filter_by(valor=pag.valor_total, data_vencimento=data_venc).first()
            if not existe:
                obra_id = None
                if pag.servico:
                    obra_id = pag.servico.obra_id
                else:
                    primeira = Obra.query.first()
                    obra_id = primeira.id if primeira else None

                if obra_id:
                    novo = PagamentoFuturo(
                        obra_id=obra_id,
                        descricao=f'Pagamento antigo {pag.id}',
                        valor=pag.valor_total,
                        data_vencimento=data_venc,
                        status='Previsto' if pag.status.lower() != 'pago' else 'Pago',
                        fornecedor=pag.fornecedor,
                        pix=pag.pix
                    )
                    db.session.add(novo)
                    criados += 1

        db.session.commit()
        return jsonify({'sincronizados': criados}), 200

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


# ============================================================
# EXECUÇÃO LOCAL / DEPLOY
# ============================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
