# Forçando novo deploy com correções 24/10
import os
import traceback  # Importado para log de erros detalhado
import re  # Importado para o CORS com regex
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from urllib.parse import quote_plus
import datetime
from sqlalchemy import func
import io
import csv
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

# Imports de Autenticação
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity, JWTManager, verify_jwt_in_request, get_jwt
from functools import wraps

print("--- [LOG] Iniciando app.py ---")

app = Flask(__name__)

# --- CONFIGURAÇÃO DE CORS (Cross-Origin Resource Sharing) ---
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
print(f"--- [LOG] CORS configurado para permitir TODAS AS ORIGENS ('*') ---")
# -----------------------------------------------------------------

# --- CONFIGURAÇÃO DO JWT (JSON Web Token) ---
app.config["JWT_SECRET_KEY"] = os.environ.get('JWT_SECRET_KEY', 'sua-chave-secreta-muito-forte-aqui-mude-depois')
jwt = JWTManager(app)
print("--- [LOG] JWT Manager inicializado ---")
# ------------------------------------------------


# --- CONFIGURAÇÃO DA CONEXÃO (COM VARIÁVEIS DE AMBIENTE) ---
DB_USER = "postgres.kwmuiviyqjcxawuiqkrl"
DB_HOST = "aws-1-sa-east-1.pooler.supabase.com"
DB_PORT = "5432"
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
print(f"--- [LOG] String de conexão criada para usuário {DB_USER} (com sslmode=require) ---")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
    'pool_timeout': 30,
    'pool_size': 5,
    'max_overflow': 10
}
# --------------------------------------------------------------

db = SQLAlchemy(app)
print("--- [LOG] SQLAlchemy inicializado ---")


# --- TABELAS E MODELOS DE AUTENTICAÇÃO ---

# Tabela de associação Muitos-para-Muitos
user_obra_association = db.Table('user_obra_association',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('obra_id', db.Integer, db.ForeignKey('obra.id'), primary_key=True)
)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # Níveis: 'administrador', 'master', 'comum'
    role = db.Column(db.String(20), nullable=False, default='comum')

    obras_permitidas = db.relationship('Obra', secondary=user_obra_association, lazy='subquery',
        backref=db.backref('usuarios_permitidos', lazy=True))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role
        }
# ---------------------------------------------


# --- MODELOS DO BANCO DE DADOS (Existentes) ---
class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))
    lancamentos = db.relationship('Lancamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    empreitadas = db.relationship('Empreitada', backref='obra', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "cliente": self.cliente
        }

class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    pix = db.Column(db.String(100))
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "tipo": self.tipo,
            "descricao": self.descricao,
            "valor": self.valor,
            "data": self.data.isoformat(),
            "status": self.status,
            "pix": self.pix
        }

class Empreitada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    responsavel = db.Column(db.String(150))
    valor_global = db.Column(db.Float, nullable=False)
    pix = db.Column(db.String(100))
    pagamentos = db.relationship('PagamentoEmpreitada', backref='empreitada', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "nome": self.nome,
            "responsavel": self.responsavel,
            "valor_global": self.valor_global,
            "pix": self.pix,
            "pagamentos": [p.to_dict() for p in self.pagamentos]
        }

class PagamentoEmpreitada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empreitada_id = db.Column(db.Integer, db.ForeignKey('empreitada.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pago')
    
    def to_dict(self):
        return {
            "id": self.id,
            "data": self.data.isoformat(),
            "valor": self.valor,
            "status": self.status
        }
# ----------------------------------------------------

# --- FUNÇÕES AUXILIARES ---

def formatar_real(valor):
    """Formata valor para padrão brasileiro: R$ 9.915,00"""
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

# --- FUNÇÕES DE VERIFICAÇÃO DE PERMISSÃO ---

def get_current_user():
    """Busca o usuário (objeto SQLAlchemy) a partir do token JWT."""
    user_id_str = get_jwt_identity() # Agora é uma string
    if not user_id_str:
        return None
    user = db.session.get(User, int(user_id_str)) # Converte para int para buscar no DB
    return user

def user_has_access_to_obra(user, obra_id):
    """Verifica se o usuário tem permissão para acessar uma obra específica."""
    if user.role == 'administrador':
        return True # Admin pode ver tudo
    
    obra_ids_permitidas = [obra.id for obra in user.obras_permitidas]
    return obra_id in obra_ids_permitidas

def check_permission(roles):
    """Decorator para verificar se o usuário tem a role necessária."""
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            claims = get_jwt()
            user_role = claims.get('role')
            if user_role not in roles:
                return jsonify({"erro": "Acesso negado: permissão insuficiente."}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator

# --- ROTAS DA API ---

# --- ROTA DE ADMINISTRAÇÃO (Existente) ---
@app.route('/admin/create_tables', methods=['GET'])
def create_tables():
    print("--- [LOG] Rota /admin/create_tables (GET) acessada ---")
    try:
        with app.app_context():
            db.create_all()
        print("--- [LOG] db.create_all() executado com sucesso (incluindo tabelas de usuário). ---")
        return jsonify({"sucesso": "Tabelas criadas no banco de dados."}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/create_tables: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Falha ao criar tabelas.", "details": error_details}), 500
# ------------------------------------


# --- ROTAS DE AUTENTICAÇÃO (Públicas) ---

@app.route('/register', methods=['POST', 'OPTIONS']) # <-- CORREÇÃO AQUI
def register():
    print("--- [LOG] Rota /register (POST) acessada ---")
    try:
        dados = request.json
        username = dados.get('username')
        password = dados.get('password')
        role = dados.get('role', 'comum') 

        if not username or not password:
            return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({"erro": "Nome de usuário já existe"}), 409

        novo_usuario = User(username=username, role=role)
        novo_usuario.set_password(password)
        
        db.session.add(novo_usuario)
        db.session.commit()
        
        print(f"--- [LOG] Usuário '{username}' criado com role '{role}' ---")
        return jsonify(novo_usuario.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /register (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/login', methods=['POST', 'OPTIONS']) # <-- CORREÇÃO AQUI
def login():
    """Rota para autenticar um usuário e retornar um token JWT"""
    print("--- [LOG] Rota /login (POST) acessada ---")
    try:
        dados = request.json
        username = dados.get('username')
        password = dados.get('password')

        if not username or not password:
            return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            identity = str(user.id) # A identidade DEVE ser uma string
            additional_claims = {"username": user.username, "role": user.role}
            access_token = create_access_token(identity=identity, additional_claims=additional_claims)
            
            print(f"--- [LOG] Login bem-sucedido para '{username}' ---")
            return jsonify(access_token=access_token, user=user.to_dict())
        else:
            print(f"--- [LOG] Falha no login para '{username}' (usuário ou senha incorretos) ---")
            return jsonify({"erro": "Credenciais inválidas"}), 401
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /login (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# ------------------------------------

# --- ROTAS DE API (PROTEGIDAS) ---

@app.route('/', methods=['GET'])
def home():
    print("--- [LOG] Rota / (home) acessada ---")
    return jsonify({"message": "Backend rodando com sucesso!", "status": "OK"}), 200

@app.route('/obras', methods=['GET'])
@jwt_required() 
def get_obras():
    print("--- [LOG] Rota /obras (GET) acessada ---")
    try:
        user = get_current_user() 
        
        if user.role == 'administrador':
            obras = Obra.query.order_by(Obra.nome).all()
        else:
            obras = user.obras_permitidas
            
        return jsonify([obra.to_dict() for obra in obras])
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras', methods=['POST'])
@check_permission(roles=['administrador']) 
def add_obra():
    print("--- [LOG] Rota /obras (POST) acessada ---")
    try:
        dados = request.json
        nova_obra = Obra(
            nome=dados['nome'],
            cliente=dados.get('cliente')
        )
        db.session.add(nova_obra)
        db.session.commit()
        return jsonify(nova_obra.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>', methods=['GET'])
@jwt_required() 
def get_obra_detalhes(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id} (GET) acessada ---")
    try:
        user = get_current_user()
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        obra = Obra.query.get_or_404(obra_id)
        
        # ... (Sua lógica de sumários) ...
        sumarios_lancamentos = db.session.query(
            func.sum(Lancamento.valor).label('total_geral'),
            func.sum(db.case((Lancamento.status == 'Pago', Lancamento.valor), else_=0)).label('total_pago'),
            func.sum(db.case((Lancamento.status == 'A Pagar', Lancamento.valor), else_=0)).label('total_a_pagar')
        ).filter(Lancamento.obra_id == obra_id).first()
        sumarios_empreitadas = db.session.query(
            func.sum(PagamentoEmpreitada.valor).label('total_empreitadas_pago')
        ).join(Empreitada).filter(
            Empreitada.obra_id == obra_id,
            PagamentoEmpreitada.status == 'Pago'
        ).first()
        total_lancamentos = sumarios_lancamentos.total_geral or 0.0
        total_pago_lancamentos = sumarios_lancamentos.total_pago or 0.0
        total_pago_empreitadas = sumarios_empreitadas.total_empreitadas_pago or 0.0
        total_pago_geral = total_pago_lancamentos + total_pago_empreitadas
        total_empreitadas_global = db.session.query(
            func.sum(Empreitada.valor_global)
        ).filter(Empreitada.obra_id == obra_id).scalar() or 0.0
        total_geral = total_lancamentos + total_empreitadas_global
        total_a_pagar = total_geral - total_pago_geral
        total_por_segmento = db.session.query(
            Lancamento.tipo,
            func.sum(Lancamento.valor)
        ).filter(Lancamento.obra_id == obra_id).group_by(Lancamento.tipo).all()
        total_por_mes = db.session.query(
            func.to_char(Lancamento.data, 'MM/YYYY').label('mes_ano'),
            func.sum(Lancamento.valor)
        ).filter(Lancamento.obra_id == obra_id).group_by('mes_ano').all()
        sumarios_dict = {
            "total_geral": total_geral,
            "total_pago": total_pago_geral,
            "total_a_pagar": total_a_pagar,
            "total_por_segmento": {tipo: valor for tipo, valor in total_por_segmento},
            "total_por_mes": {mes: valor for mes, valor in total_por_mes}
        }
        
        historico_unificado = []
        for lanc in obra.lancamentos:
            historico_unificado.append({
                "id": f"lanc-{lanc.id}", "tipo_registro": "lancamento", "data": lanc.data.isoformat(),
                "descricao": lanc.descricao, "tipo": lanc.tipo, "valor": lanc.valor,
                "status": lanc.status, "pix": lanc.pix, "lancamento_id": lanc.id
            })
        for emp in obra.empreitadas:
            for pag in emp.pagamentos:
                historico_unificado.append({
                    "id": f"emp-pag-{pag.id}", "tipo_registro": "pagamento_empreitada", "data": pag.data.isoformat(),
                    "descricao": f"Empreitada: {emp.nome}", "tipo": "Empreitada", "valor": pag.valor,
                    "status": pag.status, "pix": emp.pix, "empreitada_id": emp.id,
                    "pagamento_id": pag.id, "empreitada_nome": emp.nome
                })
        historico_unificado.sort(key=lambda x: x['data'], reverse=True)
        
        return jsonify({
            "obra": obra.to_dict(),
            "lancamentos": sorted([l.to_dict() for l in obra.lancamentos], key=lambda x: x['data'], reverse=True),
            "empreitadas": [e.to_dict() for e in obra.empreitadas],
            "historico_unificado": historico_unificado,
            "sumarios": sumarios_dict
        })
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>', methods=['DELETE'])
@check_permission(roles=['administrador']) 
def deletar_obra(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id} (DELETE) acessada ---")
    try:
        obra = Obra.query.get_or_404(obra_id)
        db.session.delete(obra)
        db.session.commit()
        return jsonify({"sucesso": "Obra deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/lancamentos', methods=['POST'])
@check_permission(roles=['administrador', 'master']) 
def add_lancamento(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id}/lancamentos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.json
        novo_lancamento = Lancamento(
            obra_id=obra_id,
            tipo=dados['tipo'],
            descricao=dados['descricao'],
            valor=float(dados['valor']),
            data=datetime.date.fromisoformat(dados['data']),
            status=dados['status'],
            pix=dados['pix']
        )
        db.session.add(novo_lancamento)
        db.session.commit()
        return jsonify(novo_lancamento.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/lancamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>/pago', methods=['PATCH'])
@check_permission(roles=['administrador', 'master']) 
def marcar_como_pago(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id}/pago (PATCH) acessada ---")
    try:
        user = get_current_user()
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        lancamento.status = 'Pago'
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id}/pago (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['PUT'])
@check_permission(roles=['administrador', 'master']) 
def editar_lancamento(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.json
        lancamento.data = datetime.date.fromisoformat(dados['data'])
        lancamento.descricao = dados['descricao']
        lancamento.valor = float(dados['valor'])
        lancamento.tipo = dados['tipo']
        lancamento.status = dados['status']
        lancamento.pix = dados['pix']
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE'])
@check_permission(roles=['administrador']) 
def deletar_lancamento(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (DELETE) acessada ---")
    try:
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        db.session.delete(lancamento)
        db.session.commit()
        return jsonify({"sucesso": "Lançamento deletado"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/empreitadas', methods=['POST'])
@check_permission(roles=['administrador', 'master']) 
def add_empreitada(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id}/empreitadas (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.json
        nova_empreitada = Empreitada(
            obra_id=obra_id,
            nome=dados['nome'],
            responsavel=dados['responsavel'],
            valor_global=float(dados['valor_global']),
            pix=dados['pix']
        )
        db.session.add(nova_empreitada)
        db.session.commit()
        return jsonify(nova_empreitada.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/empreitadas (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>', methods=['PUT'])
@check_permission(roles=['administrador', 'master']) 
def editar_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        empreitada = Empreitada.query.get_or_404(empreitada_id)
        
        if not user_has_access_to_obra(user, empreitada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        dados = request.json
        empreitada.nome = dados.get('nome', empreitada.nome)
        empreitada.responsavel = dados.get('responsavel', empreitada.responsavel)
        empreitada.valor_global = float(dados.get('valor_global', empreitada.valor_global))
        empreitada.pix = dados.get('pix', empreitada.pix)
        db.session.commit()
        return jsonify(empreitada.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>', methods=['DELETE'])
@check_permission(roles=['administrador']) 
def deletar_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id} (DELETE) acessada ---")
    try:
        empreitada = Empreitada.query.get_or_404(empreitada_id)
        db.session.delete(empreitada)
        db.session.commit()
        return jsonify({"sucesso": "Empreitada deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>/pagamentos', methods=['POST'])
@check_permission(roles=['administrador', 'master']) 
def add_pagamento_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id}/pagamentos (POST) acessada ---")
    try:
        user = get_current_user()
        empreitada = Empreitada.query.get_or_404(empreitada_id)

        if not user_has_access_to_obra(user, empreitada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        dados = request.json
        novo_pagamento = PagamentoEmpreitada(
            empreitada_id=empreitada_id,
            data=datetime.date.fromisoformat(dados['data']),
            valor=float(dados['valor']),
            status=dados.get('status', 'Pago')
        )
        db.session.add(novo_pagamento)
        db.session.commit()
        empreitada_atualizada = Empreitada.query.get(empreitada_id)
        return jsonify(empreitada_atualizada.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id}/pagamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>/pagamentos/<int:pagamento_id>', methods=['DELETE'])
@check_permission(roles=['administrador']) 
def deletar_pagamento_empreitada(empreitada_id, pagamento_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id}/pagamentos/{pagamento_id} (DELETE) acessada ---")
    try:
        pagamento = PagamentoEmpreitada.query.filter_by(
            id=pagamento_id, 
            empreitada_id=empreitada_id
        ).first_or_404()
        
        db.session.delete(pagamento)
        db.session.commit()
        return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/.../pagamentos (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- ROTAS DE EXPORTAÇÃO (PROTEGIDAS) ---

@app.route('/obras/<int:obra_id>/export/csv', methods=['GET'])
@jwt_required() 
def export_csv(obra_id):
    print(f"--- [LOG] Rota /export/csv (GET) para obra_id={obra_id} ---")
    try:
        verify_jwt_in_request(optional=True) 
        user = get_current_user()
        
        if not user or not user_has_access_to_obra(user, obra_id):
           print(f"--- [AVISO] Tentativa de export CSV sem permissão ou token (obra_id={obra_id}) ---")
           pass

        obra = Obra.query.get_or_404(obra_id)
        items = obra.lancamentos
        
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['Data', 'Descricao', 'Tipo', 'Valor', 'Status', 'PIX'])
        
        for item in items:
            cw.writerow([
                item.data.isoformat(), item.descricao, item.tipo,
                item.valor, item.status, item.pix
            ])
        
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename=relatorio_obra_{obra.id}.csv"
        output.headers["Content-type"] = "text/csv"
        return output
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /export/csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/export/pdf_pendentes', methods=['GET'])
@jwt_required() 
def export_pdf_pendentes(obra_id):
    print(f"--- [LOG] Rota /export/pdf_pendentes (GET) para obra_id={obra_id} ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        obra = Obra.query.get_or_404(obra_id)
        items = Lancamento.query.filter_by(obra_id=obra.id, status='A Pagar').all()
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm,
            leftMargin=2*cm, rightMargin=2*cm
        )
        elements = []
        styles = getSampleStyleSheet()
        
        title_text = f"<b>Relatorio de Pagamentos Pendentes</b><br/><br/>Obra: {obra.nome}<br/>Cliente: {obra.cliente or 'N/A'}"
        title = Paragraph(title_text, styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 1*cm))
        
        if not items:
            elements.append(Paragraph("Nenhum pagamento pendente nesta obra.", styles['Normal']))
        else:
            data = [['Data', 'Tipo', 'Descricao', 'Valor', 'PIX']]
            total_pendente = 0
            
            for item in items:
                data.append([
                    item.data.strftime('%d/%m/%Y'),
                    item.tipo[:15] if item.tipo else 'N/A',
                    item.descricao[:35] if item.descricao else 'N/A',
                    formatar_real(item.valor),
                    (item.pix or 'Nao informado')[:20]
                ])
                total_pendente += item.valor
            
            data.append(['', '', 'TOTAL A PAGAR', formatar_real(total_pendente), ''])
            
            table = Table(data, colWidths=[3*cm, 3*cm, 6*cm, 3*cm, 4*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#007bff')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#28a745')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 11),
                ('ALIGN', (2, -1), (3, -1), 'RIGHT'),
            ]))
            
            elements.append(table)
        
        elements.append(Spacer(1, 1*cm))
        data_geracao = f"Gerado em: {datetime.datetime.now().strftime('%d/%m/%Y as %H:%M')}"
        elements.append(Paragraph(data_geracao, styles['Normal']))
        
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=pagamentos_pendentes_obra_{obra.id}.pdf'
        
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"=" * 80)
        print(f"ERRO ao gerar PDF para obra_id={obra_id}")
        print(f"Erro: {str(e)}")
        print(f"Traceback completo:")
        print(error_details)
        print(f"=" * 80)
        return jsonify({
            "erro": "Erro ao gerar PDF", "mensagem": str(e),
            "obra_id": obra_id, "details": error_details 
        }), 500
        
# --- NOVO: ROTAS DE ADMINISTRAÇÃO DE USUÁRIOS ---
# (Protegidas para 'administrador')

@app.route('/admin/users', methods=['GET'])
@check_permission(roles=['administrador'])
def get_all_users():
    """Retorna uma lista de todos os usuários (exceto o próprio admin)"""
    print("--- [LOG] Rota /admin/users (GET) acessada ---")
    try:
        current_user = get_current_user()
        # Pega todos os usuários que NÃO são o admin que está fazendo a requisição
        users = User.query.filter(User.id != current_user.id).order_by(User.username).all()
        return jsonify([user.to_dict() for user in users]), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/admin/users', methods=['POST'])
@check_permission(roles=['administrador'])
def create_user():
    """Cria um novo usuário (master ou comum)"""
    print("--- [LOG] Rota /admin/users (POST) acessada ---")
    try:
        dados = request.json
        username = dados.get('username')
        password = dados.get('password')
        role = dados.get('role', 'comum')

        if not username or not password:
            return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400
        
        if role not in ['master', 'comum']:
             return jsonify({"erro": "Role deve ser 'master' ou 'comum'"}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({"erro": "Nome de usuário já existe"}), 409

        novo_usuario = User(username=username, role=role)
        novo_usuario.set_password(password)
        
        db.session.add(novo_usuario)
        db.session.commit()
        
        print(f"--- [LOG] Admin criou usuário '{username}' com role '{role}' ---")
        return jsonify(novo_usuario.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/admin/users/<int:user_id>/permissions', methods=['GET'])
@check_permission(roles=['administrador'])
def get_user_permissions(user_id):
    """Retorna a lista de IDs de obras que um usuário pode acessar"""
    print(f"--- [LOG] Rota /admin/users/{user_id}/permissions (GET) acessada ---")
    try:
        user = User.query.get_or_404(user_id)
        obra_ids = [obra.id for obra in user.obras_permitidas]
        return jsonify({"user_id": user.id, "obra_ids": obra_ids}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users/{user_id}/permissions (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/admin/users/<int:user_id>/permissions', methods=['PUT'])
@check_permission(roles=['administrador'])
def set_user_permissions(user_id):
    """Define a lista de obras que um usuário pode acessar"""
    print(f"--- [LOG] Rota /admin/users/{user_id}/permissions (PUT) acessada ---")
    try:
        user = User.query.get_or_404(user_id)
        dados = request.json
        obra_ids_para_permitir = dados.get('obra_ids', []) # Espera um array de IDs: [1, 2, 5]

        # 1. Encontra os objetos Obra correspondentes aos IDs
        obras_permitidas = Obra.query.filter(Obra.id.in_(obra_ids_para_permitir)).all()
        
        # 2. Define a lista de permissões do usuário
        user.obras_permitidas = obras_permitidas
        
        db.session.commit()
        
        print(f"--- [LOG] Permissões atualizadas para user_id={user_id}. Obras permitidas: {obra_ids_para_permitir} ---")
        return jsonify({"sucesso": f"Permissões atualizadas para {user.username}"}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users/{user_id}/permissions (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# ---------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"--- [LOG] Iniciando servidor Flask na porta {port} (debug=True) ---")
    app.run(host='0.0.0.0', port=port, debug=True)