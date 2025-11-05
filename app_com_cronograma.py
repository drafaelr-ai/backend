# Forçando novo deploy com CRONOGRAMA DE COMPRAS - v1.0
import os
import traceback
import re
import zipfile
from flask import Flask, jsonify, request, make_response, send_file
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from urllib.parse import quote_plus
import datetime
from sqlalchemy import func, case
import io
import csv
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from sqlalchemy.orm import joinedload

# Imports de Autenticação
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity, JWTManager, verify_jwt_in_request, get_jwt
from functools import wraps

print("--- [LOG] Iniciando app.py (VERSÃO COM CRONOGRAMA DE COMPRAS v1.0) ---")

app = Flask(__name__)

# --- CONFIGURAÇÃO DE CORS ---
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
print(f"--- [LOG] CORS configurado ---")

# --- CONFIGURAÇÃO DO JWT ---
app.config["JWT_SECRET_KEY"] = os.environ.get('JWT_SECRET_KEY', 'sua-chave-secreta-muito-forte-aqui-mude-depois')
jwt = JWTManager(app)
print("--- [LOG] JWT Manager inicializado ---")

# --- CONFIGURAÇÃO DA CONEXÃO ---
DB_USER = "postgres.kwmuiviyqjcxawuiqkrl"
DB_HOST = "aws-1-sa-east-1.pooler.supabase.com"
DB_PORT = "6543"
DB_NAME = "postgres"

print("--- [LOG] Lendo variável de ambiente DB_PASSWORD... ---")
DB_PASSWORD = os.environ.get('DB_PASSWORD')

if not DB_PASSWORD:
    print("--- [ERRO CRÍTICO] Variável de ambiente DB_PASSWORD não foi encontrada! ---")
    raise ValueError("Variável de ambiente DB_PASSWORD não definida.")
else:
    print("--- [LOG] Variável DB_PASSWORD carregada com sucesso. ---")

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"
print(f"--- [LOG] String de conexão criada ---")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
    'pool_timeout': 20,
    'pool_size': 2,
    'max_overflow': 3,
    'connect_args': {
        'connect_timeout': 10,
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 5
    }
}

db = SQLAlchemy(app)
print("--- [LOG] SQLAlchemy inicializado ---")

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()
print("--- [LOG] Teardown de sessão configurado ---")


# --- TABELAS E MODELOS ---
user_obra_association = db.Table('user_obra_association',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('obra_id', db.Integer, db.ForeignKey('obra.id'), primary_key=True)
)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='comum')
    obras_permitidas = db.relationship('Obra', secondary=user_obra_association, lazy='subquery',
        backref=db.backref('usuarios_permitidos', lazy=True))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    def to_dict(self):
        return { "id": self.id, "username": self.username, "role": self.role }


class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))
    lancamentos = db.relationship('Lancamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    servicos = db.relationship('Servico', backref='obra', lazy=True, cascade="all, delete-orphan")
    orcamentos = db.relationship('Orcamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    notas_fiscais = db.relationship('NotaFiscal', backref='obra', lazy=True, cascade="all, delete-orphan")
    compras_agendadas = db.relationship('CompraAgendada', backref='obra', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return { "id": self.id, "nome": self.nome, "cliente": self.cliente }


# <--- NOVO MODELO: CompraAgendada --->
class CompraAgendada(db.Model):
    __tablename__ = 'compra_agendada'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    item = db.Column(db.String(255), nullable=False)
    descricao = db.Column(db.Text)
    fornecedor_sugerido = db.Column(db.String(150))
    valor_estimado = db.Column(db.Float, nullable=False, default=0.0)
    data_prevista = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pendente')  # Pendente, Realizada, Atrasada, Cancelada
    categoria = db.Column(db.String(100))  # Material, Ferramenta, Serviço, etc.
    prioridade = db.Column(db.Integer, nullable=False, default=3)  # 1-5
    observacoes = db.Column(db.Text)
    lancamento_vinculado_id = db.Column(db.Integer, db.ForeignKey('lancamento.id'), nullable=True)
    data_realizacao = db.Column(db.Date)  # Quando foi efetivamente comprado
    valor_real = db.Column(db.Float)  # Valor real pago
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "item": self.item,
            "descricao": self.descricao,
            "fornecedor_sugerido": self.fornecedor_sugerido,
            "valor_estimado": self.valor_estimado,
            "data_prevista": self.data_prevista.isoformat() if self.data_prevista else None,
            "status": self.status,
            "categoria": self.categoria,
            "prioridade": self.prioridade,
            "observacoes": self.observacoes,
            "lancamento_vinculado_id": self.lancamento_vinculado_id,
            "data_realizacao": self.data_realizacao.isoformat() if self.data_realizacao else None,
            "valor_real": self.valor_real,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor_total = db.Column(db.Float, nullable=False)
    valor_pago = db.Column(db.Float, nullable=False, default=0.0)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    pix = db.Column(db.String(100))
    prioridade = db.Column(db.Integer, nullable=False, default=0)
    fornecedor = db.Column(db.String(150), nullable=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='lancamentos_vinculados', lazy=True)
    compras_agendadas = db.relationship('CompraAgendada', backref='lancamento_vinculado', lazy=True)
    
    def to_dict(self):
        return {
            "id": self.id, "obra_id": self.obra_id, "tipo": self.tipo,
            "descricao": self.descricao,
            "valor_total": self.valor_total,
            "valor_pago": self.valor_pago,
            "data": self.data.isoformat(),
            "status": self.status, "pix": self.pix,
            "prioridade": self.prioridade,
            "fornecedor": self.fornecedor,
            "servico_id": self.servico_id,
            "servico_nome": self.servico.nome if self.servico else None,
            "lancamento_id": self.id
        }


# (Mantenha os outros modelos: Servico, PagamentoServico, Orcamento, NotaFiscal conforme seu código original)
# Por brevidade, vou incluir apenas a estrutura básica. Você deve manter todos os modelos existentes.

class Servico(db.Model):
    __tablename__ = 'servico'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    responsavel = db.Column(db.String(150))
    valor_global_mao_de_obra = db.Column(db.Float, nullable=False, default=0.0)
    valor_global_material = db.Column(db.Float, nullable=False, default=0.0)
    pix = db.Column(db.String(100))
    pagamentos = db.relationship('PagamentoServico', backref='servico', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "nome": self.nome,
            "responsavel": self.responsavel,
            "valor_global_mao_de_obra": self.valor_global_mao_de_obra,
            "valor_global_material": self.valor_global_material,
            "pix": self.pix,
            "pagamentos": [p.to_dict() for p in self.pagamentos]
        }

class PagamentoServico(db.Model):
    __tablename__ = 'pagamento_servico'
    id = db.Column(db.Integer, primary_key=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=False)
    tipo_pagamento = db.Column(db.String(50), nullable=False)
    valor_total = db.Column(db.Float, nullable=False, default=0.0)
    valor_pago = db.Column(db.Float, nullable=False, default=0.0)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    prioridade = db.Column(db.Integer, nullable=False, default=0)
    observacao = db.Column(db.Text)
    
    def to_dict(self):
        return {
            "id": self.id,
            "servico_id": self.servico_id,
            "tipo_pagamento": self.tipo_pagamento,
            "valor_total": self.valor_total,
            "valor_pago": self.valor_pago,
            "data": self.data.isoformat(),
            "status": self.status,
            "prioridade": self.prioridade,
            "observacao": self.observacao
        }

class Orcamento(db.Model):
    __tablename__ = 'orcamento'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    fornecedor = db.Column(db.String(150))
    valor = db.Column(db.Float, nullable=False, default=0.0)
    tipo = db.Column(db.String(100))
    status = db.Column(db.String(20), nullable=False, default='Pendente')
    observacoes = db.Column(db.Text)
    data_criacao = db.Column(db.Date, default=datetime.date.today)
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "fornecedor": self.fornecedor,
            "valor": self.valor,
            "tipo": self.tipo,
            "status": self.status,
            "observacoes": self.observacoes,
            "data_criacao": self.data_criacao.isoformat() if self.data_criacao else None
        }

class NotaFiscal(db.Model):
    __tablename__ = 'nota_fiscal'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    lancamento_id = db.Column(db.Integer, db.ForeignKey('lancamento.id'), nullable=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    pagamento_servico_id = db.Column(db.Integer, db.ForeignKey('pagamento_servico.id'), nullable=True)
    numero_nota = db.Column(db.String(100))
    arquivo = db.Column(db.LargeBinary, nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    data_upload = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "lancamento_id": self.lancamento_id,
            "servico_id": self.servico_id,
            "pagamento_servico_id": self.pagamento_servico_id,
            "numero_nota": self.numero_nota,
            "nome_arquivo": self.nome_arquivo,
            "mime_type": self.mime_type,
            "data_upload": self.data_upload.isoformat() if self.data_upload else None
        }


# --- DECORADORES DE AUTENTICAÇÃO ---
def jwt_optional_with_role():
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            try:
                verify_jwt_in_request(optional=True)
                current_user_id = get_jwt_identity()
                if current_user_id:
                    user = User.query.get(current_user_id)
                    if user:
                        kwargs['user'] = user
                        return fn(*args, **kwargs)
            except:
                pass
            return fn(*args, **kwargs)
        return decorator
    return wrapper

def role_required(roles):
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            current_user_id = get_jwt_identity()
            user = User.query.get(current_user_id)
            if not user or user.role not in roles:
                return jsonify({"erro": "Acesso negado"}), 403
            return fn(*args, **kwargs)
        return decorator
    return wrapper


# --- ROTAS DE AUTENTICAÇÃO ---
@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            return jsonify({"erro": "Credenciais inválidas"}), 401
        
        access_token = create_access_token(identity=user.id)
        return jsonify({"access_token": access_token, "user": user.to_dict()}), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# --- ROTAS DE OBRAS ---
@app.route('/obras', methods=['GET'])
@jwt_required()
def get_obras():
    try:
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if user.role == 'master':
            obras = Obra.query.all()
        elif user.role == 'administrador':
            obras = user.obras_permitidas
        else:
            obras = user.obras_permitidas
        
        return jsonify([obra.to_dict() for obra in obras]), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# <--- NOVAS ROTAS: CRONOGRAMA DE COMPRAS --->

@app.route('/obras/<int:obra_id>/compras', methods=['GET'])
@jwt_required()
def get_compras_agendadas(obra_id):
    """Lista todas as compras agendadas de uma obra"""
    try:
        compras = CompraAgendada.query.filter_by(obra_id=obra_id).order_by(CompraAgendada.data_prevista.asc()).all()
        return jsonify([c.to_dict() for c in compras]), 200
    except Exception as e:
        print(f"--- [ERRO] GET /obras/{obra_id}/compras: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/compras', methods=['POST'])
@jwt_required()
def criar_compra_agendada(obra_id):
    """Cria uma nova compra agendada"""
    try:
        data = request.get_json()
        
        nova_compra = CompraAgendada(
            obra_id=obra_id,
            item=data['item'],
            descricao=data.get('descricao'),
            fornecedor_sugerido=data.get('fornecedor_sugerido'),
            valor_estimado=float(data.get('valor_estimado', 0)),
            data_prevista=datetime.datetime.strptime(data['data_prevista'], '%Y-%m-%d').date(),
            status=data.get('status', 'Pendente'),
            categoria=data.get('categoria'),
            prioridade=int(data.get('prioridade', 3)),
            observacoes=data.get('observacoes'),
            lancamento_vinculado_id=data.get('lancamento_vinculado_id')
        )
        
        db.session.add(nova_compra)
        db.session.commit()
        
        print(f"--- [LOG] Compra agendada criada: ID {nova_compra.id} ---")
        return jsonify(nova_compra.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] POST /obras/{obra_id}/compras: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/compras/<int:compra_id>', methods=['PUT'])
@jwt_required()
def atualizar_compra_agendada(obra_id, compra_id):
    """Atualiza uma compra agendada"""
    try:
        compra = CompraAgendada.query.filter_by(id=compra_id, obra_id=obra_id).first()
        if not compra:
            return jsonify({"erro": "Compra não encontrada"}), 404
        
        data = request.get_json()
        
        if 'item' in data:
            compra.item = data['item']
        if 'descricao' in data:
            compra.descricao = data['descricao']
        if 'fornecedor_sugerido' in data:
            compra.fornecedor_sugerido = data['fornecedor_sugerido']
        if 'valor_estimado' in data:
            compra.valor_estimado = float(data['valor_estimado'])
        if 'data_prevista' in data:
            compra.data_prevista = datetime.datetime.strptime(data['data_prevista'], '%Y-%m-%d').date()
        if 'status' in data:
            compra.status = data['status']
        if 'categoria' in data:
            compra.categoria = data['categoria']
        if 'prioridade' in data:
            compra.prioridade = int(data['prioridade'])
        if 'observacoes' in data:
            compra.observacoes = data['observacoes']
        if 'data_realizacao' in data and data['data_realizacao']:
            compra.data_realizacao = datetime.datetime.strptime(data['data_realizacao'], '%Y-%m-%d').date()
        if 'valor_real' in data:
            compra.valor_real = float(data['valor_real'])
        if 'lancamento_vinculado_id' in data:
            compra.lancamento_vinculado_id = data['lancamento_vinculado_id']
        
        db.session.commit()
        
        print(f"--- [LOG] Compra {compra_id} atualizada ---")
        return jsonify(compra.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] PUT /obras/{obra_id}/compras/{compra_id}: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/compras/<int:compra_id>', methods=['DELETE'])
@jwt_required()
def deletar_compra_agendada(obra_id, compra_id):
    """Deleta uma compra agendada"""
    try:
        compra = CompraAgendada.query.filter_by(id=compra_id, obra_id=obra_id).first()
        if not compra:
            return jsonify({"erro": "Compra não encontrada"}), 404
        
        db.session.delete(compra)
        db.session.commit()
        
        print(f"--- [LOG] Compra {compra_id} deletada ---")
        return jsonify({"mensagem": "Compra deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] DELETE /obras/{obra_id}/compras/{compra_id}: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/compras/<int:compra_id>/marcar-realizada', methods=['POST'])
@jwt_required()
def marcar_compra_realizada(obra_id, compra_id):
    """Marca uma compra como realizada"""
    try:
        compra = CompraAgendada.query.filter_by(id=compra_id, obra_id=obra_id).first()
        if not compra:
            return jsonify({"erro": "Compra não encontrada"}), 404
        
        data = request.get_json()
        
        compra.status = 'Realizada'
        compra.data_realizacao = datetime.date.today()
        if 'valor_real' in data:
            compra.valor_real = float(data['valor_real'])
        else:
            compra.valor_real = compra.valor_estimado
        
        db.session.commit()
        
        print(f"--- [LOG] Compra {compra_id} marcada como realizada ---")
        return jsonify(compra.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] POST /obras/{obra_id}/compras/{compra_id}/marcar-realizada: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/compras/alertas', methods=['GET'])
@jwt_required()
def get_alertas_compras(obra_id):
    """Retorna compras que estão próximas (próximos 7 dias) ou atrasadas"""
    try:
        hoje = datetime.date.today()
        data_limite = hoje + datetime.timedelta(days=7)
        
        compras_proximas = CompraAgendada.query.filter(
            CompraAgendada.obra_id == obra_id,
            CompraAgendada.status == 'Pendente',
            CompraAgendada.data_prevista.between(hoje, data_limite)
        ).order_by(CompraAgendada.data_prevista.asc()).all()
        
        compras_atrasadas = CompraAgendada.query.filter(
            CompraAgendada.obra_id == obra_id,
            CompraAgendada.status == 'Pendente',
            CompraAgendada.data_prevista < hoje
        ).order_by(CompraAgendada.data_prevista.asc()).all()
        
        return jsonify({
            "proximas": [c.to_dict() for c in compras_proximas],
            "atrasadas": [c.to_dict() for c in compras_atrasadas]
        }), 200
    except Exception as e:
        print(f"--- [ERRO] GET /obras/{obra_id}/compras/alertas: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500

# <--- FIM DAS ROTAS DE CRONOGRAMA DE COMPRAS --->


# Mantenha todas as outras rotas do seu sistema original aqui
# (Lançamentos, Serviços, Orçamentos, Notas Fiscais, Relatórios, etc.)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"--- [LOG] Iniciando servidor Flask na porta {port} ---")
    app.run(host='0.0.0.0', port=port, debug=True)
