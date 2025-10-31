# Forçando novo deploy com correções 24/10
import os
import traceback  # Importado para log de erros detalhado
import re  # Importado para o CORS com regex
from flask import Flask, jsonify, request, make_response
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

# Imports de Autenticação
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity, JWTManager, verify_jwt_in_request, get_jwt
from functools import wraps

print("--- [LOG] Iniciando app.py (VERSÃO FINAL com KPIs Corrigidos) ---")

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
# ---------------------------------------------


# --- MODELOS DO BANCO DE DADOS (PRINCIPAIS) ---
class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))
    lancamentos = db.relationship('Lancamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    servicos = db.relationship('Servico', backref='obra', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return { "id": self.id, "nome": self.nome, "cliente": self.cliente }

class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    pix = db.Column(db.String(100))
    
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='lancamentos_vinculados', lazy=True)
    
    def to_dict(self):
        return {
            "id": self.id, "obra_id": self.obra_id, "tipo": self.tipo,
            "descricao": self.descricao, "valor": self.valor, "data": self.data.isoformat(),
            "status": self.status, "pix": self.pix,
            "servico_id": self.servico_id, 
            "servico_nome": self.servico.nome if self.servico else None
        }

class Servico(db.Model):
    __tablename__ = 'servico'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    responsavel = db.Column(db.String(150))
    valor_global_mao_de_obra = db.Column(db.Float, nullable=False, default=0.0) # Orçado
    pix = db.Column(db.String(100))
    pagamentos = db.relationship('PagamentoServico', backref='servico', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id, "obra_id": self.obra_id, "nome": self.nome,
            "responsavel": self.responsavel,
            "valor_global_mao_de_obra": self.valor_global_mao_de_obra,
            "pix": self.pix,
            "pagamentos": [p.to_dict() for p in self.pagamentos]
        }

class PagamentoServico(db.Model):
    __tablename__ = 'pagamento_servico'
    id = db.Column(db.Integer, primary_key=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pago')
    tipo_pagamento = db.Column(db.String(20), nullable=False) # 'mao_de_obra' ou 'material'
    
    def to_dict(self):
        return {
            "id": self.id, "data": self.data.isoformat(),
            "valor": self.valor, "status": self.status,
            "tipo_pagamento": self.tipo_pagamento
        }
# ----------------------------------------------------

# (Funções auxiliares e de permissão permanecem as mesmas)
# ... (Omitido por brevidade) ...
def formatar_real(valor):
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
def get_current_user():
    user_id_str = get_jwt_identity()
    if not user_id_str: return None
    user = db.session.get(User, int(user_id_str))
    return user
def user_has_access_to_obra(user, obra_id):
    if not user: return False
    if user.role == 'administrador': return True
    obra_ids_permitidas = [obra.id for obra in user.obras_permitidas]
    return obra_id in obra_ids_permitidas
def check_permission(roles):
    def decorator(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*args, **kwargs):
            if request.method == 'OPTIONS':
                return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
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
        print("--- [LOG] db.create_all() executado com sucesso. ---")
        return jsonify({"sucesso": "Tabelas criadas no banco de dados."}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/create_tables: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Falha ao criar tabelas.", "details": error_details}), 500
# ------------------------------------


# --- ROTAS DE AUTENTICAÇÃO (Públicas) ---
@app.route('/register', methods=['POST', 'OPTIONS'])
def register():
    print("--- [LOG] Rota /register (POST) acessada ---")
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
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

@app.route('/login', methods=['POST', 'OPTIONS'])
def login():
    """Rota para autenticar um usuário e retornar um token JWT"""
    print("--- [LOG] Rota /login (POST) acessada ---")
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        dados = request.json
        username = dados.get('username')
        password = dados.get('password')
        if not username or not password:
            return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            identity = str(user.id)
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

# --- INÍCIO DA ROTA MODIFICADA (NOVA LÓGICA DE CÁLCULO) ---
@app.route('/obras', methods=['GET', 'OPTIONS'])
@jwt_required() 
def get_obras():
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    print("--- [LOG] Rota /obras (GET) acessada (com KPIs de Orçamento Restante) ---")
    try:
        user = get_current_user() 
        if not user: return jsonify({"erro": "Usuário não encontrado"}), 404

        # 1. Lançamentos (Custo total e Custo pago)
        lancamentos_sum = db.session.query(
            Lancamento.obra_id,
            func.sum(Lancamento.valor).label('total_geral_lanc'),
            func.sum(case((Lancamento.status == 'Pago', Lancamento.valor), else_=0)).label('total_pago_lanc')
        ).group_by(Lancamento.obra_id).subquery()

        # 2. Orçamento de Mão de Obra (Custo total)
        servico_budget_sum = db.session.query(
            Servico.obra_id,
            func.sum(Servico.valor_global_mao_de_obra).label('total_budget_mo')
        ).group_by(Servico.obra_id).subquery()

        # 3. Pagamentos de Serviço (Custo pago e Custo de material)
        pagamentos_sum = db.session.query(
            Servico.obra_id,
            func.sum(case((PagamentoServico.status == 'Pago', PagamentoServico.valor), else_=0)).label('total_pago_pag'),
            func.sum(case((PagamentoServico.tipo_pagamento == 'material', PagamentoServico.valor), else_=0)).label('total_geral_material_pag')
        ).select_from(PagamentoServico) \
         .join(Servico, PagamentoServico.servico_id == Servico.id) \
         .group_by(Servico.obra_id) \
         .subquery()

        # 4. Query Principal
        obras_query = db.session.query(
            Obra,
            func.coalesce(lancamentos_sum.c.total_geral_lanc, 0).label('lanc_geral'),
            func.coalesce(lancamentos_sum.c.total_pago_lanc, 0).label('lanc_pago'),
            func.coalesce(servico_budget_sum.c.total_budget_mo, 0).label('serv_budget_mo'),
            func.coalesce(pagamentos_sum.c.total_pago_pag, 0).label('pag_pago'),
            func.coalesce(pagamentos_sum.c.total_geral_material_pag, 0).label('pag_material_geral')
        ).outerjoin(
            lancamentos_sum, Obra.id == lancamentos_sum.c.obra_id
        ).outerjoin(
            servico_budget_sum, Obra.id == servico_budget_sum.c.obra_id
        ).outerjoin(
            pagamentos_sum, Obra.id == pagamentos_sum.c.obra_id
        )

        # 5. Filtra permissões
        if user.role == 'administrador':
            obras_com_totais = obras_query.order_by(Obra.nome).all()
        else:
            obras_com_totais = obras_query.join(
                user_obra_association, Obra.id == user_obra_association.c.obra_id
            ).filter(
                user_obra_association.c.user_id == user.id
            ).order_by(Obra.nome).all()

        # 6. Formata a Saída (NOVA LÓGICA DE CÁLCULO)
        resultados = []
        for obra, lanc_geral, lanc_pago, serv_budget_mo, pag_pago, pag_material_geral in obras_com_totais:
            
            # Total_Projeto = (Todos Lançamentos) + (Orçamento Mão de Obra) + (Materiais de Pagamento Rápido)
            total_projeto_previsto = float(lanc_geral) + float(serv_budget_mo) + float(pag_material_geral)
            
            # Total_Pago = (Lançamentos Pagos) + (Pagamentos de Serviço Pagos)
            total_pago = float(lanc_pago) + float(pag_pago)
            
            # Total em Aberto = Total_Projeto_Previsto - Total_Pago
            total_a_pagar = total_projeto_previsto - total_pago
            
            resultados.append({
                "id": obra.id,
                "nome": obra.nome,
                "cliente": obra.cliente,
                "total_pago": total_pago, 
                "total_a_pagar": total_a_pagar 
            })
        
        return jsonify(resultados)

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA ROTA CORRIGIDA ---


@app.route('/obras', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador']) 
def add_obra():
    print("--- [LOG] Rota /obras (POST) acessada ---")
    try:
        dados = request.json
        nova_obra = Obra(nome=dados['nome'], cliente=dados.get('cliente'))
        db.session.add(nova_obra)
        db.session.commit()
        return jsonify(nova_obra.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- INÍCIO DA ROTA MODIFICADA (get_obra_detalhes) ---
@app.route('/obras/<int:obra_id>', methods=['GET', 'OPTIONS'])
@jwt_required() 
def get_obra_detalhes(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    print(f"--- [LOG] Rota /obras/{obra_id} (GET) acessada (Nova Lógica KPIs) ---")
    try:
        user = get_current_user()
        if not user: return jsonify({"erro": "Usuário não encontrado"}), 404
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        obra = Obra.query.get_or_404(obra_id)
        
        # --- Lógica de KPIs (MODIFICADA) ---
        
        # 1. Lançamentos (Total, Pago, A Pagar)
        sumarios_lancamentos = db.session.query(
            func.sum(Lancamento.valor).label('total_geral'),
            func.sum(case((Lancamento.status == 'Pago', Lancamento.valor), else_=0)).label('total_pago'),
            func.sum(case((Lancamento.status == 'A Pagar', Lancamento.valor), else_=0)).label('total_a_pagar')
        ).filter(Lancamento.obra_id == obra_id).first()
        total_lancamentos_geral = float(sumarios_lancamentos.total_geral or 0.0)
        total_lancamentos_pago = float(sumarios_lancamentos.total_pago or 0.0)
        total_lancamentos_apagar = float(sumarios_lancamentos.total_a_pagar or 0.0)

        # 2. Pagamentos de Serviço (Pago, A Pagar, e Total de Material)
        sumarios_servicos = db.session.query(
            func.sum(case((PagamentoServico.status == 'Pago', PagamentoServico.valor), else_=0)).label('total_pago'),
            func.sum(case((PagamentoServico.status == 'A Pagar', PagamentoServico.valor), else_=0)).label('total_a_pagar'),
            func.sum(case((PagamentoServico.tipo_pagamento == 'material', PagamentoServico.valor), else_=0)).label('total_material_geral')
        ).join(Servico).filter(Servico.obra_id == obra_id).first()
        total_servicos_pago = float(sumarios_servicos.total_pago or 0.0)
        total_servicos_apagar = float(sumarios_servicos.total_a_pagar or 0.0)
        total_servicos_material_geral = float(sumarios_servicos.total_material_geral or 0.0)

        # 3. Orçamento de Mão de Obra (Total)
        servico_budget_sum = db.session.query(
            func.sum(Servico.valor_global_mao_de_obra).label('total_budget_mo')
        ).filter(Servico.obra_id == obra_id).first()
        total_budget_mo = float(servico_budget_sum.total_budget_mo or 0.0)

        # 4. Cálculo dos KPIs Finais
        
        # KPI VERDE: Total Pago
        kpi_total_pago = total_lancamentos_pago + total_servicos_pago
        
        # NOVO KPI (LARANJA): Liberado para Pagamento (A Pagar)
        kpi_liberado_pagamento = total_lancamentos_apagar + total_servicos_apagar
        
        # KPI AZUL: Total Comprometido (Pago + A Pagar)
        kpi_total_geral_comprometido = kpi_total_pago + kpi_liberado_pagamento
        
        # Custo Total Previsto (Orçamento)
        # = (Todos Lançamentos) + (Orçamento MO) + (Pagamentos Rápidos de Material)
        kpi_total_previsto = total_lancamentos_geral + total_budget_mo + total_servicos_material_geral
        
        # KPI VERMELHO: Restante do Orçamento (Total em Aberto)
        # = (Custo Total Previsto) - (Total Pago)
        kpi_total_em_aberto_orcamento = kpi_total_previsto - kpi_total_pago

        # Sumário de Segmentos (Apenas Lançamentos Gerais)
        total_por_segmento = db.session.query(
            Lancamento.tipo,
            func.sum(Lancamento.valor)
        ).filter(
            Lancamento.obra_id == obra_id, 
            Lancamento.servico_id.is_(None) # Apenas não vinculados
        ).group_by(Lancamento.tipo).all()
        
        sumarios_dict = {
            "total_geral": kpi_total_geral_comprometido,       # AZUL
            "total_pago": kpi_total_pago,                    # VERDE
            "total_liberado_pagamento": kpi_liberado_pagamento, # NOVO (LARANJA)
            "total_em_aberto_orcamento": kpi_total_em_aberto_orcamento, # VERMELHO
            "total_por_segmento_geral": {tipo: float(valor or 0.0) for tipo, valor in total_por_segmento},
        }
        
        # --- HISTÓRICO UNIFICADO (Inalterado) ---
        historico_unificado = []
        
        todos_lancamentos = Lancamento.query.filter_by(obra_id=obra_id).options(
            db.joinedload(Lancamento.servico)
        ).all()
        
        for lanc in todos_lancamentos:
            descricao = lanc.descricao or "Sem descrição"
            if lanc.servico:
                descricao = f"{descricao} (Serviço: {lanc.servico.nome})"
            
            historico_unificado.append({
                "id": f"lanc-{lanc.id}", "tipo_registro": "lancamento", "data": lanc.data, 
                "descricao": descricao, "tipo": lanc.tipo, "valor": float(lanc.valor or 0.0),
                "status": lanc.status, "pix": lanc.pix, "lancamento_id": lanc.id
            })
        
        for serv in obra.servicos:
            for pag in serv.pagamentos:
                desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
                historico_unificado.append({
                    "id": f"serv-pag-{pag.id}", "tipo_registro": "pagamento_servico", "data": pag.data,
                    "descricao": f"Pag. {desc_tipo}: {serv.nome}", "tipo": "Serviço", "valor": float(pag.valor or 0.0),
                    "status": pag.status, "pix": serv.pix, "servico_id": serv.id,
                    "pagamento_id": pag.id,
                })
        
        historico_unificado.sort(key=lambda x: x['data'] if x['data'] else datetime.date(1900, 1, 1), reverse=True)
        for item in historico_unificado:
            if item['data']:
                item['data'] = item['data'].isoformat()
            
        # --- Cálculo dos totais de serviço ---
        servicos_com_totais = []
        for s in obra.servicos:
            serv_dict = s.to_dict()
            # Calcula totais de gastos vinculados (Pago + A Pagar)
            gastos_vinculados_mo = sum(
                float(l.valor or 0.0) for l in todos_lancamentos 
                if l.servico_id == s.id and l.tipo == 'Mão de Obra'
            )
            gastos_vinculados_mat = sum(
                float(l.valor or 0.0) for l in todos_lancamentos 
                if l.servico_id == s.id and l.tipo == 'Material'
            )
            serv_dict['total_gastos_vinculados_mo'] = gastos_vinculados_mo
            serv_dict['total_gastos_vinculados_mat'] = gastos_vinculados_mat
            servicos_com_totais.append(serv_dict)
        
        return jsonify({
            "obra": obra.to_dict(),
            "lancamentos": [l.to_dict() for l in todos_lancamentos],
            "servicos": servicos_com_totais,
            "historico_unificado": historico_unificado, 
            "sumarios": sumarios_dict
        })
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO GERAL] /obras/{obra_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA ROTA MODIFICADA ---

@app.route('/obras/<int:obra_id>', methods=['DELETE', 'OPTIONS'])
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

# --- Rotas de Lançamento (Geral) ---
@app.route('/obras/<int:obra_id>/lancamentos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def add_lancamento(obra_id):
    print("--- [LOG] Rota /obras/{obra_id}/lancamentos (POST) acessada ---")
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
            pix=dados.get('pix'),
            servico_id=dados.get('servico_id')
        )
        db.session.add(novo_lancamento)
        db.session.commit()
        return jsonify(novo_lancamento.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/lancamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>/pago', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def marcar_como_pago(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id}/pago (PATCH) acessada ---")
    try:
        user = get_current_user()
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if lancamento.status == 'Pago':
            lancamento.status = 'A Pagar'
        else:
            lancamento.status = 'Pago'
        
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id}/pago (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['PUT', 'OPTIONS'])
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
        lancamento.pix = dados.get('pix')
        lancamento.servico_id = dados.get('servico_id')
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE', 'OPTIONS'])
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


# --- ROTAS DE SERVIÇO (Atualizadas) ---

@app.route('/obras/<int:obra_id>/servicos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def add_servico(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id}/servicos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.json
        novo_servico = Servico(
            obra_id=obra_id,
            nome=dados['nome'],
            responsavel=dados['responsavel'],
            valor_global_mao_de_obra=float(dados.get('valor_global_mao_de_obra', 0.0)),
            pix=dados.get('pix')
        )
        db.session.add(novo_servico)
        db.session.commit()
        return jsonify(novo_servico.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/servicos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/servicos/<int:servico_id>', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def editar_servico(servico_id):
    print(f"--- [LOG] Rota /servicos/{servico_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        servico = Servico.query.get_or_404(servico_id)
        
        if not user_has_access_to_obra(user, servico.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        dados = request.json
        servico.nome = dados.get('nome', servico.nome)
        servico.responsavel = dados.get('responsavel', servico.responsavel)
        servico.valor_global_mao_de_obra = float(dados.get('valor_global_mao_de_obra', servico.valor_global_mao_de_obra))
        servico.pix = dados.get('pix', servico.pix)
        db.session.commit()
        return jsonify(servico.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/{servico_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/servicos/<int:servico_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador']) 
def deletar_servico(servico_id):
    print(f"--- [LOG] Rota /servicos/{servico_id} (DELETE) acessada ---")
    try:
        servico = Servico.query.get_or_404(servico_id)
        db.session.delete(servico)
        db.session.commit()
        return jsonify({"sucesso": "Serviço deletado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/{servico_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/servicos/<int:servico_id>/pagamentos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def add_pagamento_servico(servico_id):
    print(f"--- [LOG] Rota /servicos/{servico_id}/pagamentos (POST) acessada ---")
    try:
        user = get_current_user()
        servico = Servico.query.get_or_404(servico_id)

        if not user_has_access_to_obra(user, servico.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        dados = request.json
        
        tipo_pagamento = dados.get('tipo_pagamento')
        if tipo_pagamento not in ['mao_de_obra', 'material']:
            return jsonify({"erro": "O 'tipo_pagamento' é obrigatório e deve ser 'mao_de_obra' ou 'material'"}), 400
            
        novo_pagamento = PagamentoServico(
            servico_id=servico_id,
            data=datetime.date.fromisoformat(dados['data']),
            valor=float(dados['valor']),
            status=dados.get('status', 'Pago'),
            tipo_pagamento=tipo_pagamento
        )
        db.session.add(novo_pagamento)
        db.session.commit()
        servico_atualizado = Servico.query.get(servico_id)
        return jsonify(servico_atualizado.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/{servico_id}/pagamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/servicos/<int:servico_id>/pagamentos/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador']) 
def deletar_pagamento_servico(servico_id, pagamento_id):
    print(f"--- [LOG] Rota /servicos/{servico_id}/pagamentos/{pagamento_id} (DELETE) acessada ---")
    try:
        pagamento = PagamentoServico.query.filter_by(
            id=pagamento_id, 
            servico_id=servico_id
        ).first_or_404()
        
        db.session.delete(pagamento)
        db.session.commit()
        return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/.../pagamentos (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/servicos/pagamentos/<int:pagamento_id>/status', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def toggle_pagamento_servico_status(pagamento_id):
    print(f"--- [LOG] Rota /servicos/pagamentos/{pagamento_id}/status (PATCH) acessada ---")
    try:
        user = get_current_user()
        pagamento = PagamentoServico.query.get_or_404(pagamento_id)
        servico = Servico.query.get(pagamento.servico_id)
        
        if not user_has_access_to_obra(user, servico.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if pagamento.status == 'Pago':
            pagamento.status = 'A Pagar'
        else:
            pagamento.status = 'Pago'
            
        db.session.commit()
        return jsonify(pagamento.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/pagamentos/.../status (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# ---------------------------------------------------


# --- ROTAS DE EXPORTAÇÃO (PROTEGIDAS) ---
@app.route('/obras/<int:obra_id>/export/csv', methods=['GET', 'OPTIONS'])
@jwt_required() 
def export_csv(obra_id):
    if request.method == 'OPTIONS': return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
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
        cw.writerow(['Data', 'Descricao', 'Tipo', 'Valor', 'Status', 'PIX', 'ServicoID'])
        for item in items:
            cw.writerow([
                item.data.isoformat(), item.descricao, item.tipo,
                item.valor, item.status, item.pix, item.servico_id
            ])
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename=relatorio_obra_{obra_id}.csv"
        output.headers["Content-type"] = "text/csv"
        return output
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /export/csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/export/pdf_pendentes', methods=['GET', 'OPTIONS'])
@jwt_required() 
def export_pdf_pendentes(obra_id):
    if request.method == 'OPTIONS': return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    print(f"--- [LOG] Rota /export/pdf_pendentes (GET) para obra_id={obra_id} ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        obra = Obra.query.get_or_404(obra_id)
        
        # --- MUDANÇA: PDF agora inclui TODOS os itens a pagar ---
        
        # 1. Lançamentos a pagar
        lancamentos_apagar = Lancamento.query.filter_by(obra_id=obra.id, status='A Pagar').all()
        
        # 2. Pagamentos de Serviços a pagar
        pagamentos_servico_apagar = PagamentoServico.query.join(Servico).filter(
            Servico.obra_id == obra.id,
            PagamentoServico.status == 'A Pagar'
        ).all()
        
        items = []
        for lanc in lancamentos_apagar:
            desc = lanc.descricao
            if lanc.servico:
                desc = f"{lanc.descricao} (Serviço: {lanc.servico.nome})"
            items.append({
                "data": lanc.data, "tipo": lanc.tipo, "descricao": desc,
                "valor": lanc.valor, "pix": lanc.pix
            })
            
        for pag in pagamentos_servico_apagar:
            desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
            items.append({
                "data": pag.data, "tipo": "Serviço", 
                "descricao": f"Pag. {desc_tipo}: {pag.servico.nome}",
                "valor": pag.valor, "pix": pag.servico.pix
            })
            
        items.sort(key=lambda x: x['data'] if x['data'] else datetime.date(1900, 1, 1))

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm, leftMargin=2*cm, rightMargin=2*cm)
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
                    item['data'].strftime('%d/%m/%Y'), item['tipo'][:15] if item['tipo'] else 'N/A',
                    item['descricao'][:35] if item['descricao'] else 'N/A', formatar_real(item['valor']),
                    (item['pix'] or 'Nao informado')[:20]
                ])
                total_pendente += item['valor']
            data.append(['', '', 'TOTAL A PAGAR', formatar_real(total_pendente), ''])
            
            table = Table(data, colWidths=[3*cm, 3*cm, 6*cm, 3*cm, 4*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#007bff')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesoke), ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12), ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white), ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'), ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'), ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#dc3545')), # <-- Mudado para Vermelho
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('FONTSIZE', (0, -1), (-1, -1), 11),
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
        return jsonify({ "erro": "Erro ao gerar PDF", "mensagem": str(e), "obra_id": obra_id, "details": error_details }), 500
        
# --- ROTAS DE ADMINISTRAÇÃO DE USUÁRIOS ---
@app.route('/admin/users', methods=['GET', 'OPTIONS'])
@check_permission(roles=['administrador'])
def get_all_users():
    print("--- [LOG] Rota /admin/users (GET) acessada ---")
    try:
        current_user = get_current_user()
        users = User.query.filter(User.id != current_user.id).order_by(User.username).all()
        return jsonify([user.to_dict() for user in users]), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/admin/users', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador'])
def create_user():
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

@app.route('/admin/users/<int:user_id>/permissions', methods=['GET', 'OPTIONS'])
@check_permission(roles=['administrador'])
def get_user_permissions(user_id):
    print(f"--- [LOG] Rota /admin/users/{user_id}/permissions (GET) acessada ---")
    try:
        user = User.query.get_or_404(user_id)
        obra_ids = [obra.id for obra in user.obras_permitidas]
        return jsonify({"user_id": user.id, "obra_ids": obra_ids}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users/{user_id}/permissions (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/admin/users/<int:user_id>/permissions', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['administrador'])
def set_user_permissions(user_id):
    print(f"--- [LOG] Rota /admin/users/{user_id}/permissions (PUT) acessada ---")
    try:
        user = User.query.get_or_404(user_id)
        dados = request.json
        obra_ids_para_permitir = dados.get('obra_ids', [])
        obras_permitidas = Obra.query.filter(Obra.id.in_(obra_ids_para_permitir)).all()
        user.obras_permitidas = obras_permitidas
        db.session.commit()
        print(f"--- [LOG] Permissões atualizadas para user_id={user_id} ---")
        return jsonify({"sucesso": f"Permissões atualizadas para {user.username}"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users/{user_id}/permissions (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# ---------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"--- [LOG] Iniciando servidor Flask na porta {port} ---")
    app.run(host='0.0.0.0', port=port, debug=True)