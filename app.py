# ============================================================================
# VERSÃO CORRIGIDA - 18/NOV/2025 - SEM COLUNA SEGMENTO
# Esta versão REMOVE a definição de coluna segmento dos modelos
# para evitar erro "column segmento does not exist"
# ============================================================================
import os
import traceback  # Importado para log de erros detalhado
import re  # Importado para o CORS com regex
import zipfile  # Importado para criar ZIP de notas fiscais
from flask import Flask, jsonify, request, make_response, send_file
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from urllib.parse import quote_plus
from sqlalchemy import func, case
import io
import base64
import csv
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet
from sqlalchemy.orm import joinedload 
from datetime import datetime, date, timedelta
# Imports de Autenticação
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity, JWTManager, verify_jwt_in_request, get_jwt
from functools import wraps

print("--- [LOG] Iniciando app.py (VERSÃO COM DEBUG COMPLETO - KPIs v4) ---")
def run_auto_migration():
    """Executa migration automaticamente no startup"""
    print("=" * 70)
    print("🔧 AUTO-MIGRATION: Corrigindo estrutura do banco...")
    print("=" * 70)
    
    try:
        import psycopg2
        from urllib.parse import quote_plus
        
        db_password = os.environ.get('DB_PASSWORD')
        if not db_password:
            print("⚠️ DB_PASSWORD não encontrada, pulando migration")
            return
        
        encoded_password = quote_plus(db_password)
        url = f"postgresql://postgres.kwmuiviyqjcxawuiqkrl:{encoded_password}@aws-1-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '900s';")
        
        # 1. Verificar colunas em pagamento_futuro
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pagamento_futuro' AND column_name = 'servico_id';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE pagamento_futuro ADD COLUMN servico_id INTEGER;")
            print("✅ Coluna servico_id adicionada")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pagamento_futuro' AND column_name = 'tipo';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE pagamento_futuro ADD COLUMN tipo VARCHAR(50);")
            print("✅ Coluna tipo adicionada")
        # 2. Verificar coluna segmento em pagamento_parcelado_v2
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pagamento_parcelado_v2' AND column_name = 'segmento';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE pagamento_parcelado_v2 ADD COLUMN segmento VARCHAR(50) DEFAULT 'Material';")
            print("✅ Coluna segmento adicionada")
        # =================================================================
        # 3. CORREÇÃO DO ERRO DE FOREIGN KEY (CRÍTICO)
        # =================================================================
        print("🔄 Forçando recriação da tabela parcela_individual para corrigir FK...")
        
        # Dropamos a tabela para garantir que ela perca o vínculo com a tabela antiga (pagamento_parcelado)
        cur.execute("DROP TABLE IF EXISTS parcela_individual CASCADE;")
        
        # Recriamos apontando explicitamente para pagamento_parcelado_v2
        print("📝 Criando tabela parcela_individual correta...")
        cur.execute("""
            CREATE TABLE parcela_individual (
                id SERIAL PRIMARY KEY,
                pagamento_parcelado_id INTEGER NOT NULL,
                numero_parcela INTEGER NOT NULL,
                valor_parcela FLOAT NOT NULL,
                data_vencimento DATE NOT NULL,
                status VARCHAR(20) DEFAULT 'Previsto',
                data_pagamento DATE,
                forma_pagamento VARCHAR(50),
                observacao VARCHAR(255),
                CONSTRAINT fk_pagamento_parcelado_v2 
                    FOREIGN KEY(pagamento_parcelado_id) 
                    REFERENCES pagamento_parcelado_v2(id)
                    ON DELETE CASCADE
            );
        """)
        print("✅ Tabela parcela_individual recriada vinculada a pagamento_parcelado_v2!")
            
        conn.commit()
        cur.close()
        conn.close()
        print("🎉 AUTO-MIGRATION CONCLUÍDA!")
        
    except Exception as e:
        print(f"❌ Erro na auto-migration: {e}")
        traceback.print_exc()

# Executar migration automaticamente
print("\n--- [LOG] Executando auto-migration antes de iniciar o app ---")
run_auto_migration()
print("--- [LOG] Auto-migration concluída, iniciando app.py ---\n")

app = Flask(__name__)

# --- CORS global canônico ---
CORS(app, resources={r'/*': {'origins': '*'}}, supports_credentials=False)

# --- Reforço universal de cabeçalhos CORS em todas as respostas ---
@app.after_request
def apply_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response



# --- OPTIONS catch-all para QUALQUER rota (evita 404 em preflight) ---
@app.route('/<path:any_path>', methods=['OPTIONS'])
def global_options(any_path):
    return ('', 200)



# --- OPTIONS dedicado para /sid/... (compat) ---
@app.route('/sid/<path:any_path>', methods=['OPTIONS'])
def sid_options(any_path):
    return ('', 200)

# --- CONFIGURAÇÃO DE CORS (Cross-Origin Resource Sharing) ---  
print(f"--- [LOG] CORS configurado para permitir TODAS AS ORIGENS com métodos: GET, POST, PUT, DELETE, OPTIONS ---")
# -----------------------------------------------------------------

# --- CONFIGURAÇÃO DO JWT (JSON Web Token) ---
app.config["JWT_SECRET_KEY"] = os.environ.get('JWT_SECRET_KEY', 'sua-chave-secreta-muito-forte-aqui-mude-depois')
jwt = JWTManager(app)
print("--- [LOG] JWT Manager inicializado ---")
# ------------------------------------------------


# --- CONFIGURAÇÃO DA CONEXÃO (COM VARIÁVEIS DE AMBIENTE) ---
DB_USER = "postgres.kwmuiviyqjcxawuiqkrl"
DB_HOST = "aws-1-sa-east-1.pooler.supabase.com"
DB_PORT = "6543"  # Porta 6543 = Transaction mode (mais conexões permitidas)
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
    'pool_recycle': 280,  # Recicla conexões a cada 280 segundos (antes dos 300s do Supabase)
    'pool_timeout': 20,    # Timeout reduzido
    'pool_size': 2,        # Reduzido para 2 conexões permanentes
    'max_overflow': 3,     # Máximo de 3 conexões extras (total: 5)
    'connect_args': {
        'connect_timeout': 10,
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 5
    }
}
# --------------------------------------------------------------

db = SQLAlchemy(app)
print("--- [LOG] SQLAlchemy inicializado ---")

# --- GERENCIAMENTO AUTOMÁTICO DE CONEXÕES ---
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Fecha a sessão do banco após cada requisição para liberar conexões"""
    db.session.remove()
print("--- [LOG] Teardown de sessão configurado ---")
# ------------------------------------------------


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
    orcamentos = db.relationship('Orcamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    notas_fiscais = db.relationship('NotaFiscal', backref='obra', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return { "id": self.id, "nome": self.nome, "cliente": self.cliente }

class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    
    valor_total = db.Column(db.Float, nullable=False)
    valor_pago = db.Column(db.Float, nullable=False, default=0.0)
    
    data = db.Column(db.Date, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    pix = db.Column(db.String(100))
    prioridade = db.Column(db.Integer, nullable=False, default=0) 
    fornecedor = db.Column(db.String(150), nullable=True)
    
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='lancamentos_vinculados', lazy=True)
    
    def to_dict(self):
        # Trata segmento dinamicamente (não está no modelo)
        # Tenta pegar do objeto, mas sempre retorna Material se não existir
        segmento_value = 'Material'
        try:
            if hasattr(self, 'segmento') and self.segmento:
                segmento_value = self.segmento
        except:
            pass
        
        return {
            "id": self.id, "obra_id": self.obra_id, "tipo": self.tipo,
            "descricao": self.descricao, 
            "valor_total": self.valor_total, 
            "valor_pago": self.valor_pago, 
            "data": self.data.isoformat(),
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "status": self.status, "pix": self.pix,
            "prioridade": self.prioridade, 
            "fornecedor": self.fornecedor, 
            "servico_id": self.servico_id, 
            "servico_nome": self.servico.nome if self.servico else None,
            "segmento": segmento_value,
            "lancamento_id": self.id 
        }

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
            "id": self.id, "obra_id": self.obra_id, "nome": self.nome,
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
    data = db.Column(db.Date, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=True)
    
    valor_total = db.Column(db.Float, nullable=False)
    valor_pago = db.Column(db.Float, nullable=False, default=0.0)
    
    status = db.Column(db.String(20), nullable=False, default='Pago')
    tipo_pagamento = db.Column(db.String(20), nullable=False)
    forma_pagamento = db.Column(db.String(20), nullable=True)
    pix = db.Column(db.String(100), nullable=True)  # Chave PIX do pagamento
    prioridade = db.Column(db.Integer, nullable=False, default=0)
    fornecedor = db.Column(db.String(150), nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "data": self.data.isoformat(),
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "valor_total": self.valor_total, 
            "valor_pago": self.valor_pago, 
            "status": self.status,
            "tipo_pagamento": self.tipo_pagamento,
            "forma_pagamento": self.forma_pagamento,
            "pix": self.pix,
            "prioridade": self.prioridade,
            "fornecedor": self.fornecedor, 
            "pagamento_id": self.id 
        }

class Orcamento(db.Model):
    __tablename__ = 'orcamento'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    
    descricao = db.Column(db.String(255), nullable=False)
    fornecedor = db.Column(db.String(150), nullable=True)
    valor = db.Column(db.Float, nullable=False)
    dados_pagamento = db.Column(db.String(150), nullable=True)
    tipo = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pendente') 
    
    observacoes = db.Column(db.Text, nullable=True)
    
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='orcamentos_vinculados', lazy=True)
    
    anexos = db.relationship('AnexoOrcamento', backref='orcamento', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "fornecedor": self.fornecedor,
            "valor": self.valor,
            "dados_pagamento": self.dados_pagamento,
            "tipo": self.tipo,
            "status": self.status,
            "observacoes": self.observacoes, 
            "servico_id": self.servico_id,
            "servico_nome": self.servico.nome if self.servico else None,
            "anexos_count": len(self.anexos)
        }

class AnexoOrcamento(db.Model):
    __tablename__ = 'anexo_orcamento'
    id = db.Column(db.Integer, primary_key=True)
    orcamento_id = db.Column(db.Integer, db.ForeignKey('orcamento.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    mimetype = db.Column(db.String(100), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False) 

    def to_dict(self):
        return {
            "id": self.id,
            "orcamento_id": self.orcamento_id,
            "filename": self.filename,
            "mimetype": self.mimetype
        }

class NotaFiscal(db.Model):
    __tablename__ = 'nota_fiscal'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    
    filename = db.Column(db.String(255), nullable=False)
    mimetype = db.Column(db.String(100), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    
    item_id = db.Column(db.Integer, nullable=False)
    item_type = db.Column(db.String(50), nullable=False)
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "filename": self.filename,
            "mimetype": self.mimetype,
            "item_id": self.item_id,
            "item_type": self.item_type
        }

# --- MODELOS DO CRONOGRAMA FINANCEIRO ---
class PagamentoFuturo(db.Model):
    """Pagamentos únicos planejados para o futuro"""
    __tablename__ = 'pagamento_futuro'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Previsto')  # Previsto/Pago/Cancelado
    fornecedor = db.Column(db.String(150), nullable=True)
    pix = db.Column(db.String(100), nullable=True)  # Chave PIX para pagamento
    observacoes = db.Column(db.Text, nullable=True)
    
    # NOVOS CAMPOS: Para vincular pagamentos futuros a serviços
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    tipo = db.Column(db.String(50), nullable=True)  # 'Mão de Obra', 'Material', ou 'Despesa'
    
    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "valor": self.valor,
            "data_vencimento": self.data_vencimento.isoformat(),
            "status": self.status,
            "fornecedor": self.fornecedor,
            "pix": self.pix,
            "observacoes": self.observacoes,
            "servico_id": self.servico_id,
            "tipo": self.tipo
        }

class PagamentoParcelado(db.Model):
    """Pagamentos parcelados (ex: 1/10, 2/10, etc)"""
    __tablename__ = 'pagamento_parcelado_v2'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    fornecedor = db.Column(db.String(150), nullable=True)
    
    # Vínculo com serviço do cronograma (opcional)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    
    # Tipo de pagamento: Material ou Mão de Obra
    segmento = db.Column(db.String(50), nullable=True, default='Material')
    
    # Informações do parcelamento
    valor_total = db.Column(db.Float, nullable=False)
    numero_parcelas = db.Column(db.Integer, nullable=False)
    valor_parcela = db.Column(db.Float, nullable=False)
    data_primeira_parcela = db.Column(db.Date, nullable=False)
    periodicidade = db.Column(db.String(10), nullable=False, default='Mensal')  # Semanal ou Mensal
    
    # Controle de pagamentos
    parcelas_pagas = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default='Ativo')  # Ativo/Concluído/Cancelado
    observacoes = db.Column(db.Text, nullable=True)
    
    def to_dict(self):
        """Converte objeto para dicionário de forma segura sem dependências externas"""
        
        # Função auxiliar para somar meses sem usar dateutil
        def add_months_safe(source_date, months):
            import calendar
            month = source_date.month - 1 + months
            year = source_date.year + month // 12
            month = month % 12 + 1
            day = min(source_date.day, calendar.monthrange(year, month)[1])
            return date(year, month, day)

        # Calcular a próxima parcela pendente
        proxima_parcela_numero = self.parcelas_pagas + 1
        
        # Calcular a data da próxima parcela
        proxima_parcela_vencimento = None
        if proxima_parcela_numero <= self.numero_parcelas:
            try:
                if self.periodicidade == 'Semanal':
                    from datetime import timedelta
                    dias_incremento = (proxima_parcela_numero - 1) * 7
                    proxima_data = self.data_primeira_parcela + timedelta(days=dias_incremento)
                    proxima_parcela_vencimento = proxima_data.isoformat()
                else:  # Mensal
                    # CORREÇÃO: Usa função nativa em vez de dateutil
                    proxima_data = add_months_safe(self.data_primeira_parcela, (proxima_parcela_numero - 1))
                    proxima_parcela_vencimento = proxima_data.isoformat()
            except Exception as e:
                print(f"[AVISO] Erro ao calcular próxima parcela: {e}")
                proxima_parcela_vencimento = None
        
        # Buscar nome do serviço de forma segura
        servico_nome = None
        if self.servico_id:
            try:
                servico = Servico.query.get(self.servico_id)
                servico_nome = servico.nome if servico else None
            except Exception as e:
                print(f"[AVISO] Erro ao buscar serviço {self.servico_id}: {e}")
                servico_nome = None
        
        # Tratar segmento de forma defensiva
        try:
            segmento_value = self.segmento if hasattr(self, 'segmento') and self.segmento else 'Material'
        except:
            segmento_value = 'Material'
        
        # Montar dicionário de resposta
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "fornecedor": self.fornecedor,
            "segmento": segmento_value,
            "valor_total": self.valor_total,
            "numero_parcelas": self.numero_parcelas,
            "valor_parcela": self.valor_parcela,
            "data_primeira_parcela": self.data_primeira_parcela.isoformat() if self.data_primeira_parcela else None,
            "periodicidade": self.periodicidade,
            "parcelas_pagas": self.parcelas_pagas,
            "status": self.status,
            "observacoes": self.observacoes,
            "proxima_parcela_numero": proxima_parcela_numero if proxima_parcela_numero <= self.numero_parcelas else None,
            "proxima_parcela_vencimento": proxima_parcela_vencimento,
            "servico_id": self.servico_id,
            "servico_nome": servico_nome
        }
    
# ----------------------------------------------------
class ParcelaIndividual(db.Model):
    """Modelo para armazenar valores individuais de cada parcela"""
    __tablename__ = 'parcela_individual'
    
    id = db.Column(db.Integer, primary_key=True)
    pagamento_parcelado_id = db.Column(db.Integer, db.ForeignKey('pagamento_parcelado_v2.id'), nullable=False)
    numero_parcela = db.Column(db.Integer, nullable=False)  # 1, 2, 3...
    valor_parcela = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Previsto')  # Previsto, Pago
    data_pagamento = db.Column(db.Date, nullable=True)
    forma_pagamento = db.Column(db.String(50), nullable=True)  # PIX, Boleto, TED, Dinheiro, etc
    observacao = db.Column(db.String(255), nullable=True)
    
    pagamento_parcelado = db.relationship('PagamentoParcelado', backref='parcelas_individuais')
    
    def to_dict(self):
        return {
            "id": self.id,
            "pagamento_parcelado_id": self.pagamento_parcelado_id,
            "numero_parcela": self.numero_parcela,
            "valor_parcela": self.valor_parcela,
            "data_vencimento": self.data_vencimento.isoformat(),
            "status": self.status,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "forma_pagamento": self.forma_pagamento,
            "observacao": self.observacao
        }

# ===== MODELOS DO DIÁRIO DE OBRAS =====
# ==============================================================================
# MODELO DIARIOOBRA CORRETO - SUBSTITUA NO SEU app.py (linha ~431)
# ==============================================================================
# Encontre "class DiarioObra(db.Model):" no seu app.py
# Apague TODO o modelo (até antes do próximo @app.route ou próxima class)
# Cole este código no lugar

class DiarioObra(db.Model):
    __tablename__ = 'diario_obra'
    
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text, nullable=True)
    clima = db.Column(db.String(50), nullable=True)
    temperatura = db.Column(db.String(50), nullable=True)
    equipe_presente = db.Column(db.Text, nullable=True)
    atividades_realizadas = db.Column(db.Text, nullable=True)
    materiais_utilizados = db.Column(db.Text, nullable=True)
    equipamentos_utilizados = db.Column(db.Text, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    criado_por = db.Column(db.Integer, nullable=True)
    criado_em = db.Column(db.DateTime, default=func.now())
    atualizado_em = db.Column(db.DateTime, default=func.now(), onupdate=func.now())
    
    # Relacionamentos
    imagens = db.relationship('DiarioImagem', backref='entrada', lazy=True, cascade='all, delete-orphan')
    # criador = db.relationship('User', backref='entradas_diario', foreign_keys=[criado_por])
    
    def to_dict(self):
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'data': self.data.isoformat() if self.data else None,
            'titulo': self.titulo,
            'descricao': self.descricao,
            'clima': self.clima,
            'temperatura': self.temperatura,
            'equipe_presente': self.equipe_presente,
            'atividades_realizadas': self.atividades_realizadas,
            'materiais_utilizados': self.materiais_utilizados,
            'equipamentos_utilizados': self.equipamentos_utilizados,
            'observacoes': self.observacoes,
            'criado_por': self.criado_por,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'atualizado_em': self.atualizado_em.isoformat() if self.atualizado_em else None,
            'imagens': [img.to_dict() for img in self.imagens]
        }

class DiarioImagem(db.Model):
    """Imagens do diário de obras"""
    __tablename__ = 'diario_imagens'
    
    id = db.Column(db.Integer, primary_key=True)
    diario_id = db.Column(db.Integer, db.ForeignKey('diario_obra.id'), nullable=False)
    arquivo_nome = db.Column(db.String(255), nullable=False)
    arquivo_base64 = db.Column(db.Text, nullable=False)  # Armazena imagem em base64
    legenda = db.Column(db.String(500))
    ordem = db.Column(db.Integer, default=0)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'diario_id': self.diario_id,
            'arquivo_nome': self.arquivo_nome,
            'arquivo_base64': self.arquivo_base64,
            'legenda': self.legenda,
            'ordem': self.ordem,
            'criado_em': self.criado_em.strftime('%Y-%m-%d %H:%M:%S') if self.criado_em else None
        }

# (Funções auxiliares e de permissão permanecem as mesmas)
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
        print("--- [LOG] db.create_all() executado com sucesso. (Incluindo NotaFiscal e colunas de pag. parcial) ---")
        return jsonify({"sucesso": "Tabelas/colunas atualizadas no banco de dados."}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/create_tables: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Falha ao criar tabelas.", "details": error_details}), 500
# ------------------------------------


# --- ROTAS DE AUTENTICAÇÃO (Públicas) ---
@app.route('/register', methods=['POST', 'OPTIONS'])
def register():
    # ... (código inalterado) ...
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
    # ... (código inalterado) ...
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

# --- ROTA /obras (Tela inicial) ---
@app.route('/obras', methods=['GET', 'OPTIONS'])
@jwt_required() 
def get_obras():
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    print("--- [LOG] Rota /obras (GET) acessada (4 KPIs Completos) ---")
    try:
        user = get_current_user() 
        if not user: return jsonify({"erro": "Usuário não encontrado"}), 404

        # 1. Lançamentos (Custo total e Custo pago)
        lancamentos_sum = db.session.query(
            Lancamento.obra_id,
            func.sum(Lancamento.valor_total).label('total_geral_lanc'),
            func.sum(Lancamento.valor_pago).label('total_pago_lanc'),
            func.sum(
                case(
                    (Lancamento.valor_total > Lancamento.valor_pago, 
                     Lancamento.valor_total - Lancamento.valor_pago),
                    else_=0
                )
            ).label('total_pendente_lanc')
        ).group_by(Lancamento.obra_id).subquery()

        # 2. Orçamento de Mão de Obra E Material (Custo total)
        servico_budget_sum = db.session.query(
            Servico.obra_id,
            func.sum(Servico.valor_global_mao_de_obra).label('total_budget_mo'),
            func.sum(Servico.valor_global_material).label('total_budget_mat')
        ).group_by(Servico.obra_id).subquery()

        # 3. Pagamentos de Serviço (Custo pago e pendente)
        pagamentos_sum = db.session.query(
            Servico.obra_id,
            func.sum(PagamentoServico.valor_pago).label('total_pago_pag'),
            func.sum(
                case(
                    (PagamentoServico.valor_total > PagamentoServico.valor_pago,
                     PagamentoServico.valor_total - PagamentoServico.valor_pago),
                    else_=0
                )
            ).label('total_pendente_pag')
        ).select_from(PagamentoServico) \
         .join(Servico, PagamentoServico.servico_id == Servico.id) \
         .group_by(Servico.obra_id) \
         .subquery()
        
        # CORREÇÃO: 4. Pagamentos Futuros (Cronograma Financeiro)
        pagamentos_futuros_sum = db.session.query(
            PagamentoFuturo.obra_id,
            func.sum(PagamentoFuturo.valor).label('total_futuro')
        ).filter(
            PagamentoFuturo.status == 'Previsto'
        ).group_by(PagamentoFuturo.obra_id).subquery()
        
        # CORREÇÃO: 5. Parcelas Previstas (Cronograma Financeiro)
        parcelas_previstas_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(ParcelaIndividual.status == 'Previsto') \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()

        # 6. Query Principal
        obras_query = db.session.query(
            Obra,
            func.coalesce(lancamentos_sum.c.total_geral_lanc, 0).label('lanc_geral'),
            func.coalesce(lancamentos_sum.c.total_pago_lanc, 0).label('lanc_pago'),
            func.coalesce(lancamentos_sum.c.total_pendente_lanc, 0).label('lanc_pendente'),
            func.coalesce(servico_budget_sum.c.total_budget_mo, 0).label('serv_budget_mo'),
            func.coalesce(servico_budget_sum.c.total_budget_mat, 0).label('serv_budget_mat'),
            func.coalesce(pagamentos_sum.c.total_pago_pag, 0).label('pag_pago'),
            func.coalesce(pagamentos_sum.c.total_pendente_pag, 0).label('pag_pendente'),
            func.coalesce(pagamentos_futuros_sum.c.total_futuro, 0).label('futuro_previsto'),
            func.coalesce(parcelas_previstas_sum.c.total_parcelas, 0).label('parcelas_previstas')
        ).outerjoin(
            lancamentos_sum, Obra.id == lancamentos_sum.c.obra_id
        ).outerjoin(
            servico_budget_sum, Obra.id == servico_budget_sum.c.obra_id
        ).outerjoin(
            pagamentos_sum, Obra.id == pagamentos_sum.c.obra_id
        ).outerjoin(
            pagamentos_futuros_sum, Obra.id == pagamentos_futuros_sum.c.obra_id
        ).outerjoin(
            parcelas_previstas_sum, Obra.id == parcelas_previstas_sum.c.obra_id
        )

        # 7. Filtra permissões
        if user.role == 'administrador':
            obras_com_totais = obras_query.order_by(Obra.nome).all()
        else:
            obras_com_totais = obras_query.join(
                user_obra_association, Obra.id == user_obra_association.c.obra_id
            ).filter(
                user_obra_association.c.user_id == user.id
            ).order_by(Obra.nome).all()

        # 8. Formata a Saída com os 4 KPIs
        resultados = []
        for obra, lanc_geral, lanc_pago, lanc_pendente, serv_budget_mo, serv_budget_mat, pag_pago, pag_pendente, futuro_previsto, parcelas_previstas in obras_com_totais:
            
            # KPI 1: Orçamento Total (INCLUINDO Cronograma Financeiro)
            orcamento_total = float(lanc_geral) + float(serv_budget_mo) + float(serv_budget_mat) + float(futuro_previsto) + float(parcelas_previstas)
            
            # KPI 2: Total Pago (Valores Efetivados)
            total_pago = float(lanc_pago) + float(pag_pago)
            
            # KPI 3: Liberado para Pagamento (Fila) - CORREÇÃO: Incluindo Cronograma Financeiro
            liberado_pagamento = (
                float(lanc_pendente) + 
                float(pag_pendente) + 
                float(futuro_previsto) + 
                float(parcelas_previstas)
            )
            
            # KPI 4: Residual (Orçamento - Pago)
            residual = orcamento_total - total_pago
            
            resultados.append({
                "id": obra.id,
                "nome": obra.nome,
                "cliente": obra.cliente,
                "orcamento_total": orcamento_total,
                "total_pago": total_pago,
                "liberado_pagamento": liberado_pagamento,
                "residual": residual
            })
        
        return jsonify(resultados)

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA ROTA ---


@app.route('/obras', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def add_obra():
    # ... (código inalterado) ...
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

# --- ROTA /obras/<id> (Dashboard Interno) ---
@app.route('/obras/<int:obra_id>', methods=['GET', 'OPTIONS'])
@jwt_required() 
def get_obra_detalhes(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    print(f"--- [LOG] Rota /obras/{obra_id} (GET) acessada (Novos KPIs v3) ---")
    
    try:
        from sqlalchemy.orm import joinedload
        user = get_current_user()
        if not user: return jsonify({"erro": "Usuário não encontrado"}), 404
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        obra = Obra.query.get_or_404(obra_id)
        
        # --- Lógica de KPIs (ATUALIZADA - Corrigida) ---
        
        # Orçamentos de Serviços (MO + Material)
        servico_budget_sum = db.session.query(
            func.sum(Servico.valor_global_mao_de_obra).label('total_budget_mo'),
            func.sum(Servico.valor_global_material).label('total_budget_mat')
        ).filter(Servico.obra_id == obra_id).first()
        
        total_budget_mo = float(servico_budget_sum.total_budget_mo or 0.0)
        total_budget_mat = float(servico_budget_sum.total_budget_mat or 0.0)
        
        # Total de Lançamentos (valor_total, independente de status)
        total_lancamentos_query = db.session.query(
            func.sum(Lancamento.valor_total).label('total_lanc')
        ).filter(Lancamento.obra_id == obra_id).first()
        total_lancamentos = float(total_lancamentos_query.total_lanc or 0.0)
        
        # Valor pago dos lançamentos (soma de valor_pago)
        lancamentos_valor_pago = db.session.query(
            func.sum(Lancamento.valor_pago).label('valor_pago_lanc')
        ).filter(Lancamento.obra_id == obra_id).first()
        total_pago_lancamentos = float(lancamentos_valor_pago.valor_pago_lanc or 0.0)
        
        # Valor pago dos pagamentos de serviço (soma de valor_pago)
        pagamentos_servico_valor_pago = db.session.query(
            func.sum(PagamentoServico.valor_pago).label('valor_pago_serv')
        ).join(Servico).filter(
            Servico.obra_id == obra_id
        ).first()
        total_pago_servicos = float(pagamentos_servico_valor_pago.valor_pago_serv or 0.0)
        
        # CORREÇÃO: Calcular totais de Pagamentos Futuros e Parcelas ANTES do KPI
        # Pagamentos Futuros com status='Previsto'
        pagamentos_futuros_previstos = db.session.query(
            func.sum(PagamentoFuturo.valor).label('total_futuro')
        ).filter(
            PagamentoFuturo.obra_id == obra_id,
            PagamentoFuturo.status == 'Previsto'
        ).first()
        
        # Parcelas Individuais com status='Previsto'
        parcelas_previstas = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas')
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).first()
        
        total_futuros = float(pagamentos_futuros_previstos.total_futuro or 0.0)
        total_parcelas_previstas = float(parcelas_previstas.total_parcelas or 0.0)
        
        # Logs de DEBUG para rastreamento
        print(f"--- [DEBUG KPI] obra_id={obra_id} ---")
        print(f"--- [DEBUG KPI] total_lancamentos: R$ {total_lancamentos:.2f} ---")
        print(f"--- [DEBUG KPI] total_budget_mo: R$ {total_budget_mo:.2f} ---")
        print(f"--- [DEBUG KPI] total_budget_mat: R$ {total_budget_mat:.2f} ---")
        print(f"--- [DEBUG KPI] total_futuros (PagamentoFuturo): R$ {total_futuros:.2f} ---")
        print(f"--- [DEBUG KPI] total_parcelas_previstas: R$ {total_parcelas_previstas:.2f} ---")
        
        # KPI 1: ORÇAMENTO TOTAL (INCLUINDO Cronograma Financeiro)
        kpi_orcamento_total = total_lancamentos + total_budget_mo + total_budget_mat + total_futuros + total_parcelas_previstas
        print(f"--- [DEBUG KPI] ✅ ORÇAMENTO TOTAL = R$ {kpi_orcamento_total:.2f} ---")
        
        # KPI 2: VALORES EFETIVADOS/PAGOS (valor_pago de lançamentos + valor_pago de serviços)
        kpi_valores_pagos = total_pago_lancamentos + total_pago_servicos
        
        # KPI 3: VALOR RESIDUAL (Orçamento Total - Valores Pagos)
        kpi_residual = kpi_orcamento_total - kpi_valores_pagos
        
        # KPI 4: LIBERADO PARA PAGAMENTO (Valores pendentes = valor_total - valor_pago)
        # Lançamentos com saldo pendente (valor_total - valor_pago > 0)
        lancamentos_pendentes = db.session.query(
            func.sum(Lancamento.valor_total - Lancamento.valor_pago).label('total_pendente')
        ).filter(
            Lancamento.obra_id == obra_id,
            Lancamento.valor_total > Lancamento.valor_pago,
            Lancamento.status != 'A Pagar'  # NOVO: Exclui 'A Pagar' (agora usa PagamentoFuturo)
        ).first()
        
        # Pagamentos de Serviço com saldo pendente (valor_total - valor_pago > 0)
        pagamentos_servico_pendentes = db.session.query(
            func.sum(PagamentoServico.valor_total - PagamentoServico.valor_pago).label('total_pendente')
        ).join(Servico).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_total > PagamentoServico.valor_pago
        ).first()
        
        # Usar valores já calculados de Pagamentos Futuros e Parcelas
        kpi_liberado_pagamento = (
            float(lancamentos_pendentes.total_pendente or 0.0) + 
            float(pagamentos_servico_pendentes.total_pendente or 0.0) +
            total_futuros +
            total_parcelas_previstas
        )

        # Sumário de Segmentos (Apenas Lançamentos Gerais)
        total_por_segmento = db.session.query(
            Lancamento.tipo,
            func.sum(Lancamento.valor_total)
        ).filter(
            Lancamento.obra_id == obra_id, 
            Lancamento.servico_id.is_(None)
        ).group_by(Lancamento.tipo).all()
        
        # <--- Enviando os 4 KPIs corretos (ATUALIZADO) -->
        sumarios_dict = {
            "orcamento_total": kpi_orcamento_total,        # Card 1 - Orçamento Total (Vermelho)
            "valores_pagos": kpi_valores_pagos,            # Card 2 - Valores Pagos (Azul/Índigo)
            "residual": kpi_residual,                      # Card 3 - Residual (Laranja)
            "liberado_pagamento": kpi_liberado_pagamento,  # Card 4 - Liberado p/ Pagamento (Verde)
            
            # Mantendo este para o Gráfico
            "total_por_segmento_geral": {tipo: float(valor or 0.0) for tipo, valor in total_por_segmento},
        }
        
        # --- HISTÓRICO UNIFICADO ---
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
                "data_vencimento": lanc.data_vencimento,
                "descricao": descricao, "tipo": lanc.tipo, 
                "valor_total": float(lanc.valor_total or 0.0), 
                "valor_pago": float(lanc.valor_pago or 0.0), 
                "status": lanc.status, "pix": lanc.pix, "lancamento_id": lanc.id,
                "prioridade": lanc.prioridade,
                "fornecedor": lanc.fornecedor 
            })
        
        for serv in obra.servicos:
            for pag in serv.pagamentos:
                desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
                historico_unificado.append({
                    "id": f"serv-pag-{pag.id}", "tipo_registro": "pagamento_servico", "data": pag.data,
                    "data_vencimento": pag.data_vencimento,
                    "descricao": f"Pag. {desc_tipo}: {serv.nome}", "tipo": "Serviço", 
                    "valor_total": float(pag.valor_total or 0.0), 
                    "valor_pago": float(pag.valor_pago or 0.0), 
                    "status": pag.status, "pix": serv.pix, "servico_id": serv.id,
                    "pagamento_id": pag.id,
                    "prioridade": pag.prioridade,
                    "fornecedor": pag.fornecedor 
                })
        
        historico_unificado.sort(key=lambda x: x['data'] if x['data'] else datetime.date(1900, 1, 1), reverse=True)
        for item in historico_unificado:
            if item['data']:
                item['data'] = item['data'].isoformat()
            if item.get('data_vencimento'):
                item['data_vencimento'] = item['data_vencimento'].isoformat()
            
        # --- Cálculo dos totais de serviço ---
        servicos_com_totais = []
        for s in obra.servicos:
            serv_dict = s.to_dict()
            gastos_vinculados_mo = sum(
                float(l.valor_total or 0.0) for l in todos_lancamentos
                if l.servico_id == s.id and l.tipo == 'Mão de Obra'
            )
            gastos_vinculados_mat = sum(
                float(l.valor_total or 0.0) for l in todos_lancamentos 
                if l.servico_id == s.id and l.tipo == 'Material'
            )
            serv_dict['total_gastos_vinculados_mo'] = gastos_vinculados_mo
            serv_dict['total_gastos_vinculados_mat'] = gastos_vinculados_mat
            servicos_com_totais.append(serv_dict)
            
        # Busca orçamentos pendentes
        orcamentos_pendentes = Orcamento.query.filter_by(
            obra_id=obra_id, 
            status='Pendente'
        ).options(
            joinedload(Orcamento.anexos)
        ).order_by(Orcamento.id.desc()).all()
        
        
        return jsonify({
            "obra": obra.to_dict(),
            "lancamentos": [l.to_dict() for l in todos_lancamentos],
            "servicos": servicos_com_totais,
            "historico_unificado": historico_unificado, 
            "sumarios": sumarios_dict,
            "orcamentos": [o.to_dict() for o in orcamentos_pendentes] 
        })
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO GERAL] /obras/{obra_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA ROTA ---

@app.route('/obras/<int:obra_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def deletar_obra(obra_id):
    # ... (código inalterado) ...
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
    """
    LÓGICA CORRIGIDA:
    - Se status == 'A Pagar' → Cria PagamentoFuturo (aparece no cronograma)
    - Se status == 'Pago' → Cria Lançamento (vai direto pro histórico)
    """
    print("--- [LOG] Rota /obras/{obra_id}/lancamentos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.get_json()  # CORREÇÃO: Usar get_json() ao invés de request.json
        
        if not dados:
            return jsonify({"erro": "Dados inválidos ou ausentes"}), 400
        
        # Validar campos obrigatórios
        if 'valor' not in dados:
            return jsonify({"erro": "Campo 'valor' é obrigatório"}), 400
        if 'status' not in dados:
            return jsonify({"erro": "Campo 'status' é obrigatório"}), 400
        if 'descricao' not in dados:
            return jsonify({"erro": "Campo 'descricao' é obrigatório"}), 400
        
        valor_total = float(dados['valor'])
        status = dados['status']
        
        # PROCESSAR DATAS COM SEGURANÇA
        data_registro = None
        data_vencimento_obj = None
        
        try:
            # Tentar pegar data_vencimento primeiro
            if dados.get('data_vencimento'):
                data_vencimento_obj = date.fromisoformat(dados['data_vencimento'])
            
            # Se não tiver data_vencimento, tentar 'data'
            if not data_vencimento_obj and dados.get('data'):
                data_vencimento_obj = date.fromisoformat(dados['data'])
            
            # Se não tiver nenhuma, usar hoje
            if not data_vencimento_obj:
                data_vencimento_obj = date.today()
            
            # Para lançamentos, precisamos de data_registro
            if dados.get('data'):
                data_registro = date.fromisoformat(dados['data'])
            else:
                data_registro = date.today()
                
        except ValueError as e:
            return jsonify({"erro": f"Formato de data inválido: {str(e)}"}), 400
        
        print(f"--- [LOG] Status='{status}', Valor={valor_total}, Data Vencimento={data_vencimento_obj} ---")
        
        # LÓGICA PRINCIPAL: Se é "A Pagar", cria PagamentoFuturo
        if status == 'A Pagar':
            print(f"--- [LOG] Status='A Pagar' → Criando PagamentoFuturo ---")
            
            novo_pagamento_futuro = PagamentoFuturo(
                obra_id=obra_id,
                descricao=dados['descricao'],
                valor=valor_total,
                data_vencimento=data_vencimento_obj,
                fornecedor=dados.get('fornecedor'),
                pix=dados.get('pix'),
                observacoes=None,
                status='Previsto'
            )
            db.session.add(novo_pagamento_futuro)
            db.session.commit()
            
            print(f"--- [LOG] ✅ PagamentoFuturo criado: ID {novo_pagamento_futuro.id} ---")
            return jsonify(novo_pagamento_futuro.to_dict()), 201
        
        # Se status == 'Pago', cria Lançamento normalmente
        else:
            print(f"--- [LOG] Status='Pago' → Criando Lançamento ---")
            
            # Se é gasto avulso do histórico, força status="Pago"
            is_gasto_avulso_historico = dados.get('is_gasto_avulso_historico', False)
            if is_gasto_avulso_historico:
                status = 'Pago'
            
            valor_pago = valor_total if status == 'Pago' else 0.0
            
            novo_lancamento = Lancamento(
                obra_id=obra_id, 
                tipo=dados.get('tipo', 'Saída'), 
                descricao=dados['descricao'],
                valor_total=valor_total,
                valor_pago=valor_pago,
                data=data_registro,
                data_vencimento=data_vencimento_obj if dados.get('data_vencimento') else None,
                status=status, 
                pix=dados.get('pix'),
                prioridade=int(dados.get('prioridade', 0)),
                fornecedor=dados.get('fornecedor'), 
                servico_id=dados.get('servico_id')
            )
            db.session.add(novo_lancamento)
            db.session.commit()
            
            print(f"--- [LOG] ✅ Lançamento criado: ID {novo_lancamento.id} ---")
            return jsonify(novo_lancamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/lancamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>/pago', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def marcar_como_pago(lancamento_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id}/pago (PATCH) acessada ---")
    try:
        user = get_current_user()
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if lancamento.status == 'Pago':
            lancamento.status = 'A Pagar'
            lancamento.valor_pago = 0.0
        else:
            lancamento.status = 'Pago'
            lancamento.valor_pago = lancamento.valor_total
        
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
    # ... (código atualizado para valor_total/valor_pago) ...
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        dados = request.json
        lancamento.data = date.fromisoformat(dados['data'])
        lancamento.data_vencimento = date.fromisoformat(dados['data_vencimento']) if dados.get('data_vencimento') else None
        lancamento.descricao = dados['descricao']
        lancamento.valor_total = float(dados['valor_total']) 
        lancamento.valor_pago = float(dados.get('valor_pago', lancamento.valor_pago)) 
        lancamento.tipo = dados['tipo']
        lancamento.status = dados['status']
        lancamento.pix = dados.get('pix')
        lancamento.prioridade = int(dados.get('prioridade', lancamento.prioridade))
        lancamento.fornecedor = dados.get('fornecedor', lancamento.fornecedor) 
        lancamento.servico_id = dados.get('servico_id')
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_lancamento(lancamento_id):
    """
    Deleta um lançamento com regras específicas:
    - Lançamentos PAGOS só podem ser deletados por usuários MASTER
    - Lançamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
    """
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (DELETE) acessada ---")
    
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        # Buscar o lançamento
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        
        # Obter o papel do usuário
        claims = get_jwt()
        user_role = claims.get('role')
        
        # Verificar se o lançamento está PAGO (executado)
        is_pago = lancamento.status == 'Pago'
        
        # REGRA: Se está PAGO, apenas MASTER pode deletar
        if is_pago and user_role != 'master':
            print(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO por usuário {user_role} (não MASTER) ---")
            return jsonify({
                "erro": "Acesso negado: Apenas usuários MASTER podem excluir pagamentos já executados (PAGOS)."
            }), 403
        
        # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
        if not is_pago and user_role not in ['administrador', 'master']:
            print(f"--- [LOG] ❌ Tentativa de deletar lançamento por usuário {user_role} (sem permissão) ---")
            return jsonify({
                "erro": "Acesso negado: Permissão insuficiente para excluir este lançamento."
            }), 403
        
        # Se passou nas verificações, deletar o lançamento
        db.session.delete(lancamento)
        db.session.commit()
        
        print(f"--- [LOG] ✅ Lançamento {lancamento_id} (Status: {lancamento.status}) deletado com sucesso pelo usuário {user_role} ---")
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
    # ... (código inalterado) ...
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
            valor_global_material=float(dados.get('valor_global_material', 0.0)),
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

@app.route('/obras/<int:obra_id>/servicos-nomes', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_servicos_nomes(obra_id):
    """
    Retorna lista simplificada de serviços (id, nome) para uso em dropdowns
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        servicos = Servico.query.filter_by(obra_id=obra_id).order_by(Servico.nome).all()
        
        servicos_simples = [
            {
                'id': s.id,
                'nome': s.nome
            }
            for s in servicos
        ]
        
        return jsonify({'servicos': servicos_simples}), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/servicos-nomes (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@app.route('/servicos/<int:servico_id>', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def editar_servico(servico_id):
    # ... (código inalterado) ...
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
        servico.valor_global_material = float(dados.get('valor_global_material', servico.valor_global_material))
        servico.pix = dados.get('pix', servico.pix)
        db.session.commit()
        return jsonify(servico.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/{servico_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/servicos/<int:servico_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def deletar_servico(servico_id):
    # ... (código inalterado) ...
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

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @app.route('/servicos/<int:servico_id>/pagamentos', methods=['POST', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master']) 
# def add_pagamento_servico(servico_id):
#     # ... (código atualizado para valor_total/valor_pago) ...
#     print(f"--- [LOG] Rota /servicos/{servico_id}/pagamentos (POST) acessada ---")
#     try:
#         user = get_current_user()
#         servico = Servico.query.get_or_404(servico_id)
# 
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
# 
#         dados = request.json
#         
#         tipo_pagamento = dados.get('tipo_pagamento')
#         if tipo_pagamento not in ['mao_de_obra', 'material']:
#             return jsonify({"erro": "O 'tipo_pagamento' é obrigatório e deve ser 'mao_de_obra' ou 'material'"}), 400
#             
#         valor_total = float(dados['valor'])
#         status = dados.get('status', 'Pago')
#         valor_pago = valor_total if status == 'Pago' else 0.0
# 
#         novo_pagamento = PagamentoServico(
#             servico_id=servico_id,
#             data=date.fromisoformat(dados['data']),
#             data_vencimento=date.fromisoformat(dados['data_vencimento']) if dados.get('data_vencimento') else None,
#             valor_total=valor_total, 
#             valor_pago=valor_pago, 
#             status=status,
#             tipo_pagamento=tipo_pagamento,
#             forma_pagamento=dados.get('forma_pagamento'),
#             pix=dados.get('pix'),  # Chave PIX do pagamento
#             prioridade=int(dados.get('prioridade', 0)),
#             fornecedor=dados.get('fornecedor') 
#         )
#         db.session.add(novo_pagamento)
#         db.session.commit()
#         servico_atualizado = Servico.query.get(servico_id)
#         return jsonify(servico_atualizado.to_dict())
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /servicos/{servico_id}/pagamentos (POST): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e), "details": error_details}), 500
# ===============================================================================

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @app.route('/servicos/<int:servico_id>/pagamentos/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
# @jwt_required()
# def deletar_pagamento_servico(servico_id, pagamento_id):
#     """
#     Deleta um pagamento de serviço com regras específicas:
#     - Pagamentos PAGOS só podem ser deletados por usuários MASTER
#     - Pagamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
#     """
#     print(f"--- [LOG] Rota /servicos/{servico_id}/pagamentos/{pagamento_id} (DELETE) acessada ---")
#     
#     if request.method == 'OPTIONS':
#         return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
#     
#     try:
#         pagamento = PagamentoServico.query.filter_by(
#             id=pagamento_id, 
#             servico_id=servico_id
#         ).first_or_404()
#         
#         # Obter o papel do usuário
#         claims = get_jwt()
#         user_role = claims.get('role')
#         
#         # Verificar se o pagamento está PAGO (completamente executado)
#         is_pago = pagamento.valor_pago >= pagamento.valor_total
#         
#         # REGRA: Se está PAGO, apenas MASTER pode deletar
#         if is_pago and user_role != 'master':
#             print(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO de serviço por usuário {user_role} (não MASTER) ---")
#             return jsonify({
#                 "erro": "Acesso negado: Apenas usuários MASTER podem excluir pagamentos já executados (PAGOS)."
#             }), 403
#         
#         # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
#         if not is_pago and user_role not in ['administrador', 'master']:
#             print(f"--- [LOG] ❌ Tentativa de deletar pagamento de serviço por usuário {user_role} (sem permissão) ---")
#             return jsonify({
#                 "erro": "Acesso negado: Permissão insuficiente para excluir este pagamento."
#             }), 403
#         
#         db.session.delete(pagamento)
#         db.session.commit()
#         
#         print(f"--- [LOG] ✅ Pagamento de serviço {pagamento_id} deletado com sucesso pelo usuário {user_role} ---")
#         return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /servicos/.../pagamentos (DELETE): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e), "details": error_details}), 500
# ===============================================================================

# Rota alternativa para deletar pagamento de serviço (usada pelo histórico)
# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @app.route('/obras/<int:obra_id>/servicos/pagamentos/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
# @jwt_required()
# def deletar_pagamento_servico_alternativo(obra_id, pagamento_id):
#     """
#     Rota alternativa para deletar pagamento de serviço.
#     Busca o pagamento pelo ID e aplica as mesmas regras de segurança.
#     """
#     print(f"--- [LOG] Rota /obras/{obra_id}/servicos/pagamentos/{pagamento_id} (DELETE) acessada ---")
#     
#     if request.method == 'OPTIONS':
#         return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
#     
#     try:
#         # Buscar o pagamento pelo ID
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         
#         # Verificar se o pagamento pertence a um serviço da obra especificada
#         servico = Servico.query.get(pagamento.servico_id)
#         if not servico or servico.obra_id != obra_id:
#             return jsonify({"erro": "Pagamento não encontrado nesta obra"}), 404
#         
#         # Obter o papel do usuário
#         claims = get_jwt()
#         user_role = claims.get('role')
#         
#         # Verificar se o pagamento está PAGO (completamente executado)
#         is_pago = pagamento.valor_pago >= pagamento.valor_total
#         
#         # REGRA: Se está PAGO, apenas MASTER pode deletar
#         if is_pago and user_role != 'master':
#             print(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO de serviço por usuário {user_role} (não MASTER) ---")
#             return jsonify({
#                 "erro": "Acesso negado: Apenas usuários MASTER podem excluir pagamentos já executados (PAGOS)."
#             }), 403
#         
#         # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
#         if not is_pago and user_role not in ['administrador', 'master']:
#             print(f"--- [LOG] ❌ Tentativa de deletar pagamento de serviço por usuário {user_role} (sem permissão) ---")
#             return jsonify({
#                 "erro": "Acesso negado: Permissão insuficiente para excluir este pagamento."
#             }), 403
#         
#         db.session.delete(pagamento)
#         db.session.commit()
#         
#         print(f"--- [LOG] ✅ Pagamento de serviço {pagamento_id} deletado com sucesso pelo usuário {user_role} ---")
#         return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /obras/.../servicos/pagamentos (DELETE): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e), "details": error_details}), 500
# ===============================================================================

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @app.route('/servicos/pagamentos/<int:pagamento_id>/status', methods=['PATCH', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master'])
# def toggle_pagamento_servico_status(pagamento_id):
#     # ... (código atualizado para valor_total/valor_pago) ...
#     print(f"--- [LOG] Rota /servicos/pagamentos/{pagamento_id}/status (PATCH) acessada ---")
#     try:
#         user = get_current_user()
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         servico = Servico.query.get(pagamento.servico_id)
#         
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
#         
#         if pagamento.status == 'Pago':
#             pagamento.status = 'A Pagar'
#             pagamento.valor_pago = 0.0
#         else:
#             pagamento.status = 'Pago'
#             pagamento.valor_pago = pagamento.valor_total
#             
#         db.session.commit()
#         return jsonify(pagamento.to_dict()), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /servicos/pagamentos/.../status (PATCH): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e), "details": error_details}), 500
# ===============================================================================

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @app.route('/servicos/pagamentos/<int:pagamento_id>/prioridade', methods=['PATCH', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master'])
# def editar_pagamento_servico_prioridade(pagamento_id):
#     # ... (código inalterado) ...
#     print(f"--- [LOG] Rota /servicos/pagamentos/{pagamento_id}/prioridade (PATCH) acessada ---")
#     if request.method == 'OPTIONS': 
#         return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
#         
#     try:
#         user = get_current_user()
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         servico = Servico.query.get(pagamento.servico_id)
#         
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
#         
#         dados = request.json
#         nova_prioridade = dados.get('prioridade')
#         
#         if nova_prioridade is None or not isinstance(nova_prioridade, int):
#             return jsonify({"erro": "Prioridade inválida. Deve ser um número."}), 400
#             
#         pagamento.prioridade = int(nova_prioridade)
#         db.session.commit()
#         
#         return jsonify(pagamento.to_dict()), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /servicos/pagamentos/.../prioridade (PATCH): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e), "details": error_details}), 500
# ===============================================================================

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @app.route('/servicos/pagamentos/<int:pagamento_id>', methods=['PUT', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master'])
# def editar_pagamento_servico(pagamento_id):
#     """Edita um pagamento de serviço completo"""
#     print(f"--- [LOG] Rota /servicos/pagamentos/{pagamento_id} (PUT) acessada ---")
#     if request.method == 'OPTIONS':
#         return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
#     
#     try:
#         user = get_current_user()
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         servico = Servico.query.get(pagamento.servico_id)
#         
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
#         
#         dados = request.json
#         
#         # Atualizar campos se fornecidos
#         if 'data' in dados:
#             pagamento.data = date.fromisoformat(dados['data'])
#         if 'data_vencimento' in dados:
#             pagamento.data_vencimento = date.fromisoformat(dados['data_vencimento']) if dados['data_vencimento'] else None
#         if 'valor' in dados:
#             pagamento.valor_total = float(dados['valor'])
#             # Se status = Pago, atualizar valor_pago também
#             if pagamento.status == 'Pago':
#                 pagamento.valor_pago = pagamento.valor_total
#         if 'tipo_pagamento' in dados:
#             if dados['tipo_pagamento'] not in ['mao_de_obra', 'material']:
#                 return jsonify({"erro": "tipo_pagamento deve ser 'mao_de_obra' ou 'material'"}), 400
#             pagamento.tipo_pagamento = dados['tipo_pagamento']
#         if 'forma_pagamento' in dados:
#             pagamento.forma_pagamento = dados['forma_pagamento']
#         if 'pix' in dados:
#             pagamento.pix = dados['pix']
#         if 'fornecedor' in dados:
#             pagamento.fornecedor = dados['fornecedor']
#         if 'prioridade' in dados:
#             pagamento.prioridade = int(dados['prioridade'])
#         if 'status' in dados:
#             pagamento.status = dados['status']
#             # Ajustar valor_pago conforme status
#             if dados['status'] == 'Pago':
#                 pagamento.valor_pago = pagamento.valor_total
#             elif dados['status'] == 'A Pagar':
#                 pagamento.valor_pago = 0.0
#         
#         db.session.commit()
#         return jsonify(pagamento.to_dict()), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] PUT /servicos/pagamentos/{pagamento_id}: {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e), "details": error_details}), 500
# ===============================================================================
# ---------------------------------------------------


# --- NOVA ROTA PARA PAGAMENTO PARCIAL ---
@app.route('/pagamentos/<string:item_type>/<int:item_id>/pagar', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def pagar_item_parcial(item_type, item_id):
    """
    Registra um pagamento (parcial ou total) para um item de despesa.
    item_type pode ser 'lancamento' ou 'pagamento_servico'.
    """
    if request.method == 'OPTIONS': 
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        user = get_current_user()
        dados = request.json
        valor_a_pagar = float(dados.get('valor_a_pagar', 0))

        if valor_a_pagar <= 0:
            return jsonify({"erro": "O valor a pagar deve ser positivo."}), 400

        item = None
        
        # 1. Encontrar o item e verificar permissões
        if item_type == 'lancamento':
            item = Lancamento.query.get_or_404(item_id)
            if not user_has_access_to_obra(user, item.obra_id):
                return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        elif item_type == 'pagamento_servico':
            item = PagamentoServico.query.get_or_404(item_id)
            servico = Servico.query.get(item.servico_id)
            if not user_has_access_to_obra(user, servico.obra_id):
                return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        else:
            return jsonify({"erro": "Tipo de item inválido."}), 400

        # 2. Validar o pagamento
        valor_restante = item.valor_total - item.valor_pago
        if valor_a_pagar > (valor_restante + 0.01): # 0.01 de margem para floats
            return jsonify({"erro": f"O valor a pagar (R$ {valor_a_pagar:.2f}) é maior que o valor restante (R$ {valor_restante:.2f})."}), 400

        # 3. Atualizar o item
        item.valor_pago += valor_a_pagar
        
        # 4. Atualizar o status
        if (item.valor_total - item.valor_pago) < 0.01: # Se estiver totalmente pago
            item.status = 'Pago'
            item.valor_pago = item.valor_total # Garante valor exato
        else:
            item.status = 'A Pagar' 

        db.session.commit()
        return jsonify(item.to_dict()), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /pagamentos/.../pagar (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA NOVA ROTA ---


# --- ROTAS DE ORÇAMENTO (MODIFICADAS PARA ANEXOS) ---

@app.route('/obras/<int:obra_id>/orcamentos', methods=['GET', 'OPTIONS'])
@check_permission(roles=['administrador', 'master', 'comum'])
def get_orcamentos_obra(obra_id):
    """Lista todos os orçamentos de uma obra com seus anexos"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/orcamentos (GET) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        # Buscar obra para validar
        obra = Obra.query.get_or_404(obra_id)
        
        # Buscar todos os orçamentos da obra com eager loading dos anexos
        orcamentos = Orcamento.query.filter_by(obra_id=obra_id).options(
            joinedload(Orcamento.anexos),
            joinedload(Orcamento.servico)
        ).all()
        
        # Montar resposta com informações dos anexos
        orcamentos_data = []
        for orc in orcamentos:
            orc_dict = orc.to_dict()
            # Adicionar lista de anexos com detalhes
            orc_dict['anexos'] = [
                {
                    'id': anexo.id,
                    'filename': anexo.filename,
                    'mimetype': anexo.mimetype
                }
                for anexo in orc.anexos
            ]
            orcamentos_data.append(orc_dict)
        
        print(f"--- [LOG] {len(orcamentos_data)} orçamentos encontrados para obra {obra_id} ---")
        return jsonify(orcamentos_data), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/orcamentos (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/orcamentos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def add_orcamento(obra_id):
    # ... (código inalterado) ...
    print(f"--- [LOG] Rota /obras/{obra_id}/orcamentos (POST) acessada (com anexos) ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.form
        
        novo_orcamento = Orcamento(
            obra_id=obra_id,
            descricao=dados['descricao'],
            fornecedor=dados.get('fornecedor') or None,
            valor=float(dados.get('valor', 0)),
            dados_pagamento=dados.get('dados_pagamento') or None,
            tipo=dados['tipo'],
            status='Pendente',
            observacoes=dados.get('observacoes') or None, 
            servico_id=int(dados['servico_id']) if dados.get('servico_id') else None
        )
        db.session.add(novo_orcamento)
        db.session.commit() 

        files = request.files.getlist('anexos')
        for file in files:
            if file and file.filename:
                novo_anexo = AnexoOrcamento(
                    orcamento_id=novo_orcamento.id,
                    filename=file.filename,
                    mimetype=file.mimetype,
                    data=file.read()
                )
                db.session.add(novo_anexo)
        
        db.session.commit() 
        
        return jsonify(novo_orcamento.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/orcamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/orcamentos/<int:orcamento_id>', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def editar_orcamento(orcamento_id):
    # ... (código inalterado) ...
    print(f"--- [LOG] Rota /orcamentos/{orcamento_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        if orcamento.status != 'Pendente':
            return jsonify({"erro": "Não é possível editar um orçamento que já foi processado."}), 400

        dados = request.form
        
        orcamento.descricao = dados.get('descricao', orcamento.descricao)
        orcamento.fornecedor = dados.get('fornecedor') or None
        orcamento.valor = float(dados.get('valor', orcamento.valor))
        orcamento.dados_pagamento = dados.get('dados_pagamento') or None
        orcamento.tipo = dados.get('tipo', orcamento.tipo)
        orcamento.observacoes = dados.get('observacoes') or None
        orcamento.servico_id = int(dados['servico_id']) if dados.get('servico_id') else None
        
        db.session.commit()
        return jsonify(orcamento.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /orcamentos/{orcamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA ROTA ---

@app.route('/orcamentos/<int:orcamento_id>/aprovar', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def aprovar_orcamento(orcamento_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    print(f"--- [LOG] Rota /orcamentos/{orcamento_id}/aprovar (POST) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if orcamento.status != 'Pendente':
            return jsonify({"erro": "Este orçamento já foi processado."}), 400

        orcamento.status = 'Aprovado'
        
        desc_lancamento = f"{orcamento.descricao}"
        
        novo_lancamento = Lancamento(
            obra_id=orcamento.obra_id,
            tipo=orcamento.tipo,
            descricao=desc_lancamento,
            valor_total=orcamento.valor,
            valor_pago=0.0,
            data=date.today(), 
            status='A Pagar',
            pix=orcamento.dados_pagamento,
            prioridade=0,
            fornecedor=orcamento.fornecedor, 
            servico_id=orcamento.servico_id
        )
        
        db.session.add(novo_lancamento)
        db.session.commit()
        
        return jsonify({"sucesso": "Orçamento aprovado e movido para pendências", "lancamento": novo_lancamento.to_dict()}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /orcamentos/{orcamento_id}/aprovar (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/orcamentos/<int:orcamento_id>/converter_para_servico', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def converter_orcamento_para_servico(orcamento_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    print(f"--- [LOG] Rota /orcamentos/{orcamento_id}/converter_para_servico (POST) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if orcamento.status != 'Pendente':
            return jsonify({"erro": "Este orçamento já foi processado."}), 400
            
        dados = request.json
        destino_valor = dados.get('destino_valor') 
        
        if destino_valor not in ['orcamento_mo', 'pagamento_vinculado']:
            return jsonify({"erro": "Destino do valor inválido."}), 400

        orcamento.status = 'Aprovado'
        
        novo_servico = Servico(
            obra_id=orcamento.obra_id,
            nome=orcamento.descricao,
            responsavel=orcamento.fornecedor,
            pix=orcamento.dados_pagamento,
            valor_global_mao_de_obra=0.0,
            valor_global_material=0.0
        )
        
        if destino_valor == 'orcamento_mo':
            if orcamento.tipo == 'Mão de Obra':
                novo_servico.valor_global_mao_de_obra = orcamento.valor
            else:
                novo_servico.valor_global_material = orcamento.valor

            db.session.add(novo_servico)
            db.session.commit()
            return jsonify({"sucesso": "Orçamento aprovado e novo serviço criado", "servico": novo_servico.to_dict()}), 200

        else: 
            db.session.add(novo_servico)
            db.session.commit() 

            novo_lancamento = Lancamento(
                obra_id=orcamento.obra_id,
                tipo=orcamento.tipo,
                descricao=orcamento.descricao,
                valor_total=orcamento.valor,
                valor_pago=0.0,
                data=date.today(),
                status='A Pagar',
                pix=orcamento.dados_pagamento,
                prioridade=0,
                fornecedor=orcamento.fornecedor, 
                servico_id=novo_servico.id
            )
            db.session.add(novo_lancamento)
            db.session.commit()
            return jsonify({"sucesso": "Serviço criado e pendência gerada", "servico": novo_servico.to_dict(), "lancamento": novo_lancamento.to_dict()}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /orcamentos/{orcamento_id}/converter_para_servico (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/orcamentos/<int:orcamento_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def rejeitar_orcamento(orcamento_id):
    # <-- MUDANÇA: Mudar status para 'Rejeitado' em vez de deletar
    print(f"--- [LOG] Rota /orcamentos/{orcamento_id} (DELETE) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        # <-- MUDANÇA: Em vez de deletar, muda status para 'Rejeitado'
        orcamento.status = 'Rejeitado'
        db.session.commit()
        
        print(f"--- [LOG] Orçamento {orcamento_id} marcado como Rejeitado ---")
        return jsonify({"sucesso": "Orçamento rejeitado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /orcamentos/{orcamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# ---------------------------------------------------

# <--- MUDANÇA: Novas Rotas para Anexos ---
@app.route('/orcamentos/<int:orcamento_id>/anexos', methods=['GET', 'OPTIONS'])
@check_permission(roles=['administrador', 'master', 'comum'])
def get_orcamento_anexos(orcamento_id):
    # ... (código inalterado) ...
    print(f"--- [LOG] Rota /orcamentos/{orcamento_id}/anexos (GET) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        anexos = AnexoOrcamento.query.filter_by(orcamento_id=orcamento_id).all()
        return jsonify([anexo.to_dict() for anexo in anexos]), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /orcamentos/{orcamento_id}/anexos (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/orcamentos/<int:orcamento_id>/anexos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def add_anexos_orcamento(orcamento_id):
    # ... (código inalterado) ...
    print(f"--- [LOG] Rota /orcamentos/{orcamento_id}/anexos (POST) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        files = request.files.getlist('anexos')
        novos_anexos = []
        for file in files:
            if file and file.filename:
                novo_anexo = AnexoOrcamento(
                    orcamento_id=orcamento.id,
                    filename=file.filename,
                    mimetype=file.mimetype,
                    data=file.read()
                )
                db.session.add(novo_anexo)
                novos_anexos.append(novo_anexo)
        
        db.session.commit()
        
        return jsonify([anexo.to_dict() for anexo in novos_anexos]), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /orcamentos/{orcamento_id}/anexos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/anexos/<int:anexo_id>', methods=['GET', 'OPTIONS'])
@jwt_required()
def get_anexo_data(anexo_id):
    # ... (código inalterado) ...
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)

    print(f"--- [LOG] Rota /anexos/{anexo_id} (GET) acessada ---")
    try:
        user = get_current_user()
        anexo = AnexoOrcamento.query.get_or_404(anexo_id)
        orcamento = Orcamento.query.get(anexo.orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        return send_file(
            io.BytesIO(anexo.data),
            mimetype=anexo.mimetype,
            as_attachment=False, 
            download_name=anexo.filename 
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /anexos/{anexo_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/anexos/<int:anexo_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def delete_anexo(anexo_id):
    # ... (código inalterado) ...
    print(f"--- [LOG] Rota /anexos/{anexo_id} (DELETE) acessada ---")
    try:
        user = get_current_user()
        anexo = AnexoOrcamento.query.get_or_404(anexo_id)
        orcamento = Orcamento.query.get(anexo.orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        db.session.delete(anexo)
        db.session.commit()
        return jsonify({"sucesso": "Anexo deletado"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /anexos/{anexo_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# ---------------------------------------------------


# --- ROTAS DE EXPORTAÇÃO (PROTEGIDAS) ---
@app.route('/obras/<int:obra_id>/export/csv', methods=['GET', 'OPTIONS'])
@jwt_required() 
def export_csv(obra_id):
    # ... (código atualizado para valor_total/valor_pago) ...
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
        cw.writerow(['Data', 'Descricao', 'Tipo', 'ValorTotal', 'ValorPago', 'Status', 'PIX', 'ServicoID', 'Fornecedor'])
        for item in items:
            cw.writerow([
                item.data.isoformat(), item.descricao, item.tipo,
                item.valor_total, item.valor_pago, item.status, item.pix, item.servico_id,
                item.fornecedor
            ])
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename=relatorio_obra_{obra_id}.csv"
        output.headers["Content-type"] = "text/csv"
        return output
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /export/csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# MUDANÇA 4: Endpoint removido - Relatório de pendências substituído pelo Cronograma Financeiro
# @app.route('/obras/<int:obra_id>/export/pdf_pendentes', methods=['GET', 'OPTIONS'])
# @jwt_required() 
def export_pdf_pendentes_DESATIVADO(obra_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    if request.method == 'OPTIONS': return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    print(f"--- [LOG] Rota /export/pdf_pendentes (GET) para obra_id={obra_id} ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        obra = Obra.query.get_or_404(obra_id)
        
        lancamentos_apagar = Lancamento.query.filter(
            Lancamento.obra_id == obra.id, 
            Lancamento.valor_pago < Lancamento.valor_total
        ).all()
        
        pagamentos_servico_apagar = PagamentoServico.query.join(Servico).filter(
            Servico.obra_id == obra.id,
            PagamentoServico.valor_pago < PagamentoServico.valor_total
        ).all()
        
        items = []
        for lanc in lancamentos_apagar:
            desc = lanc.descricao
            if lanc.servico:
                desc = f"{desc} (Serviço: {lanc.servico.nome})"
            items.append({
                "data": lanc.data, "tipo": lanc.tipo, "descricao": desc,
                "valor": lanc.valor_total - lanc.valor_pago,
                "pix": lanc.pix,
                "prioridade": lanc.prioridade 
            })
            
        for pag in pagamentos_servico_apagar:
            desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
            items.append({
                "data": pag.data, "tipo": "Serviço", 
                "descricao": f"Pag. {desc_tipo}: {pag.servico.nome}",
                "valor": pag.valor_total - pag.valor_pago,
                "pix": pag.pix if pag.pix else '-',  # Usar PIX do pagamento
                "prioridade": pag.prioridade 
            })
            
        items.sort(key=lambda x: (-x.get('prioridade', 0), x['data'] if x['data'] else datetime.date(1900, 1, 1)))

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
            data = [['Prior.', 'Data', 'Tipo', 'Descricao', 'Valor Restante', 'PIX']]
            total_pendente = 0
            for item in items:
                data.append([
                    item.get('prioridade', 0), 
                    item['data'].strftime('%d/%m/%Y'), item['tipo'][:15] if item['tipo'] else 'N/A',
                    item['descricao'][:35] if item['descricao'] else 'N/A', 
                    formatar_real(item['valor']),
                    (item['pix'] or 'Nao informado')[:20]
                ])
                total_pendente += item['valor']
            
            data.append(['', '', '', '', 'TOTAL A PAGAR', formatar_real(total_pendente)])
            
            table = Table(data, colWidths=[1.5*cm, 2.5*cm, 3*cm, 5.5*cm, 3*cm, 3.5*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#007bff')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12), ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white), ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'), ('ALIGN', (4, 1), (4, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'), ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#dc3545')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('FONTSIZE', (0, -1), (-1, -1), 11),
                ('ALIGN', (3, -1), (4, -1), 'RIGHT'), 
            ]))
            elements.append(table)
        
        elements.append(Spacer(1, 1*cm))
        data_geracao = f"Gerado em: {datetime.now().strftime('%d/%m/%Y as %H:%M')}"
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
        
# MUDANÇA 4: Endpoint removido - Relatório de pendências substituído pelo Cronograma Financeiro
# @app.route('/export/pdf_pendentes_todas_obras', methods=['GET', 'OPTIONS'])
# @jwt_required() 
def export_pdf_pendentes_todas_obras_DESATIVADO():
    # ... (código atualizado para valor_total/valor_pago) ...
    if request.method == 'OPTIONS': 
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print("--- [LOG] Rota /export/pdf_pendentes_todas_obras (GET) acessada ---")
    
    try:
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 404
        
        prioridade_filtro = request.args.get('prioridade')
        print(f"--- [LOG] Filtro de prioridade recebido: {prioridade_filtro} ---")
        
        titulo_relatorio = "<b>Relatório de Pagamentos Pendentes - Todas as Obras</b>"
        if prioridade_filtro and prioridade_filtro != 'todas':
            titulo_relatorio = f"<b>Relatório de Pendências (Prioridade {prioridade_filtro}) - Todas as Obras</b>"
        
        
        if user.role == 'administrador':
            obras = Obra.query.order_by(Obra.nome).all()
        else:
            obras = user.obras_permitidas
        
        if not obras:
            return jsonify({"erro": "Nenhuma obra encontrada"}), 404
        
        obras_com_pendencias = []
        total_geral_pendente = 0.0
        
        for obra in obras:
            
            lancamentos_query = Lancamento.query.filter(
                Lancamento.obra_id == obra.id, 
                Lancamento.valor_pago < Lancamento.valor_total
            )
            
            pagamentos_query = PagamentoServico.query.join(Servico).filter(
                Servico.obra_id == obra.id,
                PagamentoServico.valor_pago < PagamentoServico.valor_total
            )

            if prioridade_filtro and prioridade_filtro != 'todas':
                try:
                    p_int = int(prioridade_filtro)
                    lancamentos_query = lancamentos_query.filter_by(prioridade=p_int)
                    pagamentos_query = pagamentos_query.filter_by(prioridade=p_int)
                except ValueError:
                    pass 
            
            lancamentos_apagar = lancamentos_query.all()
            pagamentos_servico_apagar = pagamentos_query.all()
            
            items = []
            
            for lanc in lancamentos_apagar:
                desc = lanc.descricao
                if lanc.servico:
                    desc = f"{desc} (Serviço: {lanc.servico.nome})"
                items.append({
                    "data": lanc.data, 
                    "tipo": lanc.tipo, 
                    "descricao": desc,
                    "valor": lanc.valor_total - lanc.valor_pago,
                    "pix": lanc.pix,
                    "prioridade": lanc.prioridade 
                })
            
            for pag in pagamentos_servico_apagar:
                desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
                items.append({
                    "data": pag.data, 
                    "tipo": "Serviço", 
                    "descricao": f"Pag. {desc_tipo}: {pag.servico.nome}",
                    "valor": pag.valor_total - pag.valor_pago,
                    "pix": pag.pix if pag.pix else '-',  # Usar PIX do pagamento
                    "prioridade": pag.prioridade
                })
            
            if items:
                items.sort(key=lambda x: (-x.get('prioridade', 0), x['data'] if x['data'] else datetime.date(1900, 1, 1)))
                total_obra = sum(item['valor'] for item in items)
                total_geral_pendente += total_obra
                
                obras_com_pendencias.append({
                    "obra": obra,
                    "items": items,
                    "total": total_obra
                })
        
        if not obras_com_pendencias:
            return jsonify({"mensagem": "Nenhuma pendência encontrada para este filtro"}), 200
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=A4, 
            topMargin=2*cm, 
            bottomMargin=2*cm, 
            leftMargin=2*cm, 
            rightMargin=2*cm
        )
        elements = []
        styles = getSampleStyleSheet()
        
        title_text = f"{titulo_relatorio}<br/><br/>Total de Obras com Pendências: {len(obras_com_pendencias)}"
        title = Paragraph(title_text, styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 0.8*cm))
        
        for idx, obra_data in enumerate(obras_com_pendencias):
            obra = obra_data['obra']
            items = obra_data['items']
            total_obra = obra_data['total']
            
            obra_header = f"<b>Obra: {obra.nome}</b>"
            if obra.cliente:
                obra_header += f" | Cliente: {obra.cliente}"
            obra_header += f" | Total: {formatar_real(total_obra)}"
            
            elements.append(Paragraph(obra_header, styles['Heading2']))
            elements.append(Spacer(1, 0.3*cm))
            
            data = [['Prior.', 'Data', 'Tipo', 'Descrição', 'Valor Restante', 'PIX']]
            
            for item in items:
                data.append([
                    item.get('prioridade', 0), 
                    item['data'].strftime('%d/%m/%Y') if item['data'] else 'N/A',
                    item['tipo'][:15] if item['tipo'] else 'N/A',
                    item['descricao'][:30] if item['descricao'] else 'N/A',
                    formatar_real(item['valor']),
                    (item['pix'] or 'Não informado')[:15]
                ])
            
            data.append(['', '', '', '', 'SUBTOTAL', formatar_real(total_obra)])
            
            table = Table(data, colWidths=[1.5*cm, 2.5*cm, 2.5*cm, 5*cm, 2.5*cm, 3*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (4, 1), (4, -1), 'RIGHT'), 
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#10b981')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 10),
                ('ALIGN', (3, -1), (4, -1), 'RIGHT'), 
            ]))
            elements.append(table)
            
            if idx < len(obras_com_pendencias) - 1:
                elements.append(Spacer(1, 0.8*cm))
        
        elements.append(Spacer(1, 1*cm))
        total_geral_text = f"<b>TOTAL GERAL A PAGAR: {formatar_real(total_geral_pendente)}</b>"
        total_geral_para = Paragraph(total_geral_text, styles['Heading1'])
        elements.append(total_geral_para)
        
        elements.append(Spacer(1, 0.5*cm))
        data_geracao = f"Gerado em: {datetime.now().strftime('%d/%m/%Y às %H:%M')}"
        elements.append(Paragraph(data_geracao, styles['Normal']))
        
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=pagamentos_pendentes_todas_obras.pdf'
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"=" * 80)
        print(f"ERRO ao gerar PDF de todas as obras")
        print(f"Erro: {str(e)}")
        print(f"Traceback completo:")
        print(error_details)
        print(f"=" * 80)
        return jsonify({
            "erro": "Erro ao gerar PDF", 
            "mensagem": str(e), 
            "details": error_details
        }), 500

# --- ROTAS DE ADMINISTRAÇÃO DE USUÁRIOS ---
@app.route('/admin/users', methods=['GET', 'OPTIONS'])
@check_permission(roles=['master'])
def get_all_users():
    # ... (código inalterado) ...
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
@check_permission(roles=['master'])
def create_user():
    """
    Cria um novo usuário no sistema.
    APENAS usuários MASTER podem criar novos usuários.
    """
    # ... (código inalterado) ...
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
@check_permission(roles=['master'])
def get_user_permissions(user_id):
    # ... (código inalterado) ...
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
@check_permission(roles=['master'])
def set_user_permissions(user_id):
    # ... (código inalterado) ...
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

# --- NOVA ROTA PARA DELETAR USUÁRIO ---
@app.route('/admin/users/<int:user_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['master'])
def delete_user(user_id):
    if request.method == 'OPTIONS': 
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)

    print(f"--- [LOG] Rota /admin/users/{user_id} (DELETE) acessada ---")
    try:
        current_user_id = int(get_jwt_identity())
        
        if user_id == current_user_id:
            return jsonify({"erro": "Você não pode excluir a si mesmo."}), 403

        user = User.query.get_or_404(user_id)
        
        if user.role == 'administrador':
            return jsonify({"erro": "Não é possível excluir outro administrador."}), 403

        db.session.delete(user)
        db.session.commit()
        
        print(f"--- [LOG] Usuário '{user.username}' (ID: {user_id}) foi deletado ---")
        return jsonify({"sucesso": f"Usuário {user.username} deletado com sucesso."}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users/{user_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA NOVA ROTA ---
# ---------------------------------------------------

# --- ROTAS DE NOTAS FISCAIS ---
@app.route('/obras/<int:obra_id>/notas-fiscais', methods=['POST', 'OPTIONS'])
@jwt_required()
def upload_nota_fiscal(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/notas-fiscais (POST) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        
        if 'file' not in request.files:
            return jsonify({"erro": "Nenhum arquivo enviado"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"erro": "Nome do arquivo vazio"}), 400
        
        item_id = request.form.get('item_id')
        item_type = request.form.get('item_type')
        
        if not item_id or not item_type:
            return jsonify({"erro": "item_id e item_type são obrigatórios"}), 400
        
        file_data = file.read()
        
        nota_fiscal = NotaFiscal(
            obra_id=obra_id,
            filename=file.filename,
            mimetype=file.mimetype,
            data=file_data,
            item_id=int(item_id),
            item_type=item_type
        )
        
        db.session.add(nota_fiscal)
        db.session.commit()
        
        print(f"--- [LOG] Nota fiscal '{file.filename}' anexada ao item {item_type}:{item_id} da obra {obra_id} ---")
        return jsonify(nota_fiscal.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/notas-fiscais (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/obras/<int:obra_id>/notas-fiscais', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_notas_fiscais(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/notas-fiscais (GET) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        notas = NotaFiscal.query.filter_by(obra_id=obra_id).all()
        return jsonify([nota.to_dict() for nota in notas]), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/notas-fiscais (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/notas-fiscais/<int:nf_id>', methods=['GET', 'OPTIONS'])
@jwt_required()
def download_nota_fiscal(nf_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /notas-fiscais/{nf_id} (GET) acessada ---")
    try:
        nota = NotaFiscal.query.get_or_404(nf_id)
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, nota.obra_id):
            return jsonify({"erro": "Acesso negado a esta nota fiscal."}), 403
        
        return send_file(
            io.BytesIO(nota.data),
            mimetype=nota.mimetype,
            as_attachment=True,
            download_name=nota.filename
        )
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /notas-fiscais/{nf_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/notas-fiscais/<int:nf_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_nota_fiscal(nf_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /notas-fiscais/{nf_id} (DELETE) acessada ---")
    try:
        nota = NotaFiscal.query.get_or_404(nf_id)
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, nota.obra_id):
            return jsonify({"erro": "Acesso negado a esta nota fiscal."}), 403
        
        if current_user.role not in ['administrador', 'master']:
            return jsonify({"erro": "Apenas administradores e masters podem excluir notas fiscais"}), 403
        
        db.session.delete(nota)
        db.session.commit()
        
        print(f"--- [LOG] Nota fiscal {nf_id} deletada ---")
        return jsonify({"sucesso": "Nota fiscal deletada com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /notas-fiscais/{nf_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DAS ROTAS DE NOTAS FISCAIS ---


# --- ROTAS DE RELATÓRIOS ---
@app.route('/obras/<int:obra_id>/notas-fiscais/export/zip', methods=['GET', 'OPTIONS'])
@jwt_required()
def export_notas_fiscais_zip(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/notas-fiscais/export/zip (GET) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        notas = NotaFiscal.query.filter_by(obra_id=obra_id).all()
        
        if not notas:
            return jsonify({"erro": "Nenhuma nota fiscal encontrada para esta obra"}), 404
        
        # Criar ZIP em memória
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for idx, nota in enumerate(notas, 1):
                # Nome do arquivo com prefixo para organização
                filename = f"{idx:03d}_{nota.filename}"
                zip_file.writestr(filename, nota.data)
        
        zip_buffer.seek(0)
        
        response = make_response(zip_buffer.read())
        response.headers['Content-Type'] = 'application/zip'
        response.headers['Content-Disposition'] = f'attachment; filename=notas_fiscais_{obra.nome.replace(" ", "_")}.zip'
        
        print(f"--- [LOG] ZIP com {len(notas)} notas fiscais gerado para obra {obra_id} ---")
        return response
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/notas-fiscais/export/zip (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/obras/<int:obra_id>/relatorio/resumo-completo', methods=['GET', 'OPTIONS'])
@jwt_required()
def relatorio_resumo_completo(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/relatorio/resumo-completo (GET) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        
        # Buscar todos os dados necessários
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).all()
        servicos = Servico.query.filter_by(obra_id=obra_id).options(joinedload(Servico.pagamentos)).all()
        orcamentos = Orcamento.query.filter_by(obra_id=obra_id).all()
        
        # CORREÇÃO: Buscar também PagamentoFuturo e Parcelas
        pagamentos_futuros = PagamentoFuturo.query.filter_by(obra_id=obra_id, status='Previsto').all()
        parcelas_previstas = db.session.query(ParcelaIndividual).join(
            PagamentoParcelado
        ).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).all()
        
        # Calcular sumários
        orcamento_total_lancamentos = sum((l.valor_total or 0) for l in lancamentos)
        
        orcamento_total_servicos = sum(
            (s.valor_global_mao_de_obra or 0) + (s.valor_global_material or 0)
            for s in servicos
        )
        
        # CORREÇÃO: Incluir pagamentos futuros e parcelas no orçamento total
        orcamento_total_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros)
        orcamento_total_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas)
        
        orcamento_total = orcamento_total_lancamentos + orcamento_total_servicos + orcamento_total_futuros + orcamento_total_parcelas
        
        valores_pagos_lancamentos = sum((l.valor_pago or 0) for l in lancamentos)
        valores_pagos_servicos = sum(
            sum((p.valor_pago or 0) for p in s.pagamentos)
            for s in servicos
        )
        valores_pagos = valores_pagos_lancamentos + valores_pagos_servicos
        
        # Criar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Título
        titulo = f"<b>RESUMO COMPLETO DA OBRA</b><br/>{obra.nome}"
        elements.append(Paragraph(titulo, styles['Title']))
        elements.append(Spacer(1, 0.5*cm))
        
        # Informações da Obra
        info_text = f"<b>Cliente:</b> {obra.cliente or 'N/A'}<br/>"
        info_text += f"<b>Data de Geração:</b> {datetime.now().strftime('%d/%m/%Y às %H:%M')}"
        elements.append(Paragraph(info_text, styles['Normal']))
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 1: RESUMO FINANCEIRO ===
        elements.append(Paragraph("<b>1. RESUMO FINANCEIRO</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        data_financeiro = [
            ['Indicador', 'Valor'],
            ['Orçamento Total', formatar_real(orcamento_total)],
            ['Valores Pagos', formatar_real(valores_pagos)],
            ['Percentual Executado', f"{(valores_pagos / orcamento_total * 100) if orcamento_total > 0 else 0:.1f}%"],
            ['Saldo Restante', formatar_real(orcamento_total - valores_pagos)]
        ]
        
        table_financeiro = Table(data_financeiro, colWidths=[8*cm, 8*cm])
        table_financeiro.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_financeiro)
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 2: SERVIÇOS ===
        elements.append(Paragraph("<b>2. SERVIÇOS (EMPREITADAS)</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        if servicos:
            for serv in servicos:
                elements.append(Paragraph(f"<b>{serv.nome}</b>", styles['Heading3']))
                
                valor_global_mo = serv.valor_global_mao_de_obra or 0
                valor_global_mat = serv.valor_global_material or 0
                valor_global_total = valor_global_mo + valor_global_mat
                
                pagamentos_mo = [p for p in serv.pagamentos if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_mat = [p for p in serv.pagamentos if p.tipo_pagamento == 'material']
                
                valor_pago_mo = sum((p.valor_pago or 0) for p in pagamentos_mo)
                valor_pago_mat = sum((p.valor_pago or 0) for p in pagamentos_mat)
                valor_pago_total = valor_pago_mo + valor_pago_mat
                
                percentual_mo = (valor_pago_mo / valor_global_mo * 100) if valor_global_mo > 0 else 0
                percentual_mat = (valor_pago_mat / valor_global_mat * 100) if valor_global_mat > 0 else 0
                percentual_total = (valor_pago_total / valor_global_total * 100) if valor_global_total > 0 else 0
                
                status = "✓ PAGO 100%" if percentual_total >= 99.9 else f"⏳ EM ANDAMENTO ({percentual_total:.1f}%)"
                
                data_servico = [
                    ['', 'Orçado', 'Pago', '% Executado'],
                    ['Mão de Obra', formatar_real(valor_global_mo), formatar_real(valor_pago_mo), f"{percentual_mo:.1f}%"],
                    ['Material', formatar_real(valor_global_mat), formatar_real(valor_pago_mat), f"{percentual_mat:.1f}%"],
                    ['TOTAL', formatar_real(valor_global_total), formatar_real(valor_pago_total), f"{percentual_total:.1f}%"]
                ]
                
                table_servico = Table(data_servico, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
                table_servico.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                    ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0f0f0')),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ]))
                elements.append(table_servico)
                elements.append(Paragraph(f"<b>Status:</b> {status}", styles['Normal']))
                elements.append(Spacer(1, 0.5*cm))
        else:
            elements.append(Paragraph("Nenhum serviço cadastrado.", styles['Normal']))
            elements.append(Spacer(1, 0.5*cm))
        
        # === SEÇÃO 3: PENDÊNCIAS VENCIDAS ===
        elements.append(Paragraph("<b>3. PENDÊNCIAS VENCIDAS ⚠️</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        hoje = date.today()
        
        pendencias_lanc_vencidas = []
        pendencias_lanc_a_pagar = []
        
        for l in lancamentos:
            if (l.valor_total or 0) > (l.valor_pago or 0):
                if l.data_vencimento and l.data_vencimento < hoje:
                    pendencias_lanc_vencidas.append(l)
                else:
                    pendencias_lanc_a_pagar.append(l)
        
        pendencias_serv_vencidas = []
        pendencias_serv_a_pagar = []
        
        for serv in servicos:
            for pag in serv.pagamentos:
                if (pag.valor_total or 0) > (pag.valor_pago or 0):
                    if pag.data_vencimento and pag.data_vencimento < hoje:
                        pendencias_serv_vencidas.append((serv.nome, pag))
                    else:
                        pendencias_serv_a_pagar.append((serv.nome, pag))
        
        total_vencido = 0
        
        if pendencias_lanc_vencidas or pendencias_serv_vencidas:
            data_vencidas = [['Descrição', 'Tipo', 'Valor Pendente']]
            
            for lanc in pendencias_lanc_vencidas:
                valor_pendente = (lanc.valor_total or 0) - (lanc.valor_pago or 0)
                total_vencido += valor_pendente
                data_vencidas.append([
                    lanc.descricao[:40],
                    lanc.tipo,
                    formatar_real(valor_pendente)
                ])
            
            for serv_nome, pag in pendencias_serv_vencidas:
                valor_pendente = (pag.valor_total or 0) - (pag.valor_pago or 0)
                total_vencido += valor_pendente
                tipo_pag_display = pag.tipo_pagamento.replace('_', ' ').title() if pag.tipo_pagamento else 'Serviço'
                data_vencidas.append([
                    f"{serv_nome} - {tipo_pag_display}"[:40],
                    "Serviço",
                    formatar_real(valor_pendente)
                ])
            
            data_vencidas.append(['', 'TOTAL VENCIDO ⚠️', formatar_real(total_vencido)])
            
            table_vencidas = Table(data_vencidas, colWidths=[9*cm, 3.5*cm, 3.5*cm])
            table_vencidas.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d32f2f')),  # Vermelho escuro
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#ffcdd2')),  # Vermelho claro
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d32f2f')),  # Linha total em vermelho
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ]))
            elements.append(table_vencidas)
        else:
            elements.append(Paragraph("✓ Nenhuma pendência vencida!", styles['Normal']))
        
        elements.append(Spacer(1, 0.5*cm))
        
        # === SEÇÃO 4: PENDÊNCIAS A PAGAR ===
        elements.append(Paragraph("<b>4. PENDÊNCIAS A PAGAR (No Prazo)</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        total_a_pagar = 0
        
        if pendencias_lanc_a_pagar or pendencias_serv_a_pagar:
            data_a_pagar = [['Descrição', 'Tipo', 'Valor Pendente']]
            
            for lanc in pendencias_lanc_a_pagar:
                valor_pendente = (lanc.valor_total or 0) - (lanc.valor_pago or 0)
                total_a_pagar += valor_pendente
                data_a_pagar.append([
                    lanc.descricao[:40],
                    lanc.tipo,
                    formatar_real(valor_pendente)
                ])
            
            for serv_nome, pag in pendencias_serv_a_pagar:
                valor_pendente = (pag.valor_total or 0) - (pag.valor_pago or 0)
                total_a_pagar += valor_pendente
                tipo_pag_display = pag.tipo_pagamento.replace('_', ' ').title() if pag.tipo_pagamento else 'Serviço'
                data_a_pagar.append([
                    f"{serv_nome} - {tipo_pag_display}"[:40],
                    "Serviço",
                    formatar_real(valor_pendente)
                ])
            
            data_a_pagar.append(['', 'TOTAL A PAGAR', formatar_real(total_a_pagar)])
            
            table_a_pagar = Table(data_a_pagar, colWidths=[9*cm, 3.5*cm, 3.5*cm])
            table_a_pagar.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2196f3')),  # Azul
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0f0f0')),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
            ]))
            elements.append(table_a_pagar)
        else:
            elements.append(Paragraph("✓ Nenhuma pendência a pagar no momento!", styles['Normal']))
        
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 5: ORÇAMENTOS ===
        elements.append(Paragraph("<b>5. ORÇAMENTOS</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        if orcamentos:
            # <-- MUDANÇA: Log de debug para verificar status
            print(f"--- [DEBUG] Total de orçamentos: {len(orcamentos)}")
            for orc in orcamentos:
                print(f"--- [DEBUG] Orçamento: {orc.descricao} | Status: '{orc.status}'")
            
            orcamentos_pendentes = [o for o in orcamentos if o.status == 'Pendente']
            orcamentos_aprovados = [o for o in orcamentos if o.status == 'Aprovado']
            orcamentos_rejeitados = [o for o in orcamentos if o.status == 'Rejeitado']
            
            print(f"--- [DEBUG] Pendentes: {len(orcamentos_pendentes)} | Aprovados: {len(orcamentos_aprovados)} | Rejeitados: {len(orcamentos_rejeitados)}")
            
            if orcamentos_pendentes:
                elements.append(Paragraph("<b>5.1. Orçamentos Pendentes de Aprovação</b>", styles['Heading3']))
                data_orc_pend = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_pendentes:
                    data_orc_pend.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_pend = Table(data_orc_pend, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_pend.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f59e0b')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_pend)
                elements.append(Spacer(1, 0.5*cm))
            
            if orcamentos_aprovados:
                elements.append(Paragraph("<b>5.2. Orçamentos Aprovados</b>", styles['Heading3']))
                data_orc_apr = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_aprovados:
                    data_orc_apr.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_apr = Table(data_orc_apr, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_apr.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_apr)
                elements.append(Spacer(1, 0.5*cm))
            
            # <-- NOVO: Seção de Orçamentos Rejeitados
            if orcamentos_rejeitados:
                elements.append(Paragraph("<b>5.3. Orçamentos Rejeitados (Histórico)</b>", styles['Heading3']))
                data_orc_rej = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_rejeitados:
                    data_orc_rej.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_rej = Table(data_orc_rej, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_rej.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_rej)
                elements.append(Spacer(1, 0.5*cm))
        else:
            elements.append(Paragraph("Nenhum orçamento cadastrado.", styles['Normal']))
        
        # Gerar PDF
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=resumo_completo_{obra.nome.replace(" ", "_")}.pdf'
        
        print(f"--- [LOG] Relatório completo gerado para obra {obra_id} ---")
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/relatorio/resumo-completo (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DAS ROTAS DE RELATÓRIOS ---


# ===========================
# ROTAS DO CRONOGRAMA FINANCEIRO
# ===========================

# --- PAGAMENTOS FUTUROS (Únicos) ---
@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-futuros', methods=['GET'])
@jwt_required()
def listar_pagamentos_futuros(obra_id):
    """Lista todos os pagamentos futuros de uma obra, incluindo pagamentos de serviços pendentes"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        print(f"--- [DEBUG] Buscando pagamentos futuros para obra_id={obra_id} ---")
        
        resultado = []
        
        # 1. Pagamentos Futuros (cadastrados pelo botão azul)
        pagamentos_futuros = PagamentoFuturo.query.filter_by(obra_id=obra_id).order_by(PagamentoFuturo.data_vencimento).all()
        print(f"--- [DEBUG] Encontrados {len(pagamentos_futuros)} PagamentoFuturo no banco ---")
        for p in pagamentos_futuros:
            print(f"--- [DEBUG] PagamentoFuturo ID {p.id}: {p.descricao}, Valor: R$ {p.valor:.2f}, Data: {p.data_vencimento} ---")
            resultado.append(p.to_dict())
        
        # 2. NOVO: Pagamentos de Serviços com saldo pendente
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        for servico in servicos:
            pagamentos_servico = PagamentoServico.query.filter_by(
                servico_id=servico.id
            ).filter(
                PagamentoServico.valor_pago < PagamentoServico.valor_total
            ).all()
            
            for pag_serv in pagamentos_servico:
                valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
                if valor_pendente > 0 and pag_serv.data_vencimento:
                    # Adicionar como se fosse um pagamento futuro
                    resultado.append({
                        'id': f'servico-{pag_serv.id}',  # ID especial para distinguir
                        'tipo_origem': 'servico',  # Flag para identificar origem
                        'pagamento_servico_id': pag_serv.id,
                        'servico_id': servico.id,
                        'servico_nome': servico.nome,
                        'descricao': f"{servico.nome} - {pag_serv.tipo_pagamento.replace('_', ' ').title()}",
                        'fornecedor': pag_serv.fornecedor,
                        'valor': valor_pendente,
                        'data_vencimento': pag_serv.data_vencimento.isoformat(),
                        'status': 'Previsto',
                        'periodicidade': None
                    })
        
        # Ordenar todos por data de vencimento
        resultado.sort(key=lambda x: x.get('data_vencimento', '9999-12-31'))
        
        print(f"--- [DEBUG] TOTAL FINAL: {len(resultado)} itens sendo retornados para o frontend ---")
        print(f"--- [DEBUG] Primeiros 3 itens: {resultado[:3] if len(resultado) > 0 else 'nenhum'} ---")
        
        return jsonify(resultado), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-futuros', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def criar_pagamento_futuro(obra_id):
    """Cria um novo pagamento futuro"""
    # OPTIONS é permitido sem JWT
    if request.method == 'OPTIONS':
        return '', 200
    
    # POST requer JWT
    try:
        print(f"--- [DEBUG] Iniciando criação de pagamento futuro na obra {obra_id} ---")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        print(f"--- [DEBUG] Dados recebidos: {data} ---")
        
        pix_value = data.get('pix')
        print(f"--- [DEBUG] Campo PIX recebido: '{pix_value}' (tipo: {type(pix_value)}) ---")
        
        novo_pagamento = PagamentoFuturo(
            obra_id=obra_id,
            descricao=data.get('descricao'),
            valor=float(data.get('valor', 0)),
            data_vencimento=datetime.strptime(data.get('data_vencimento'), '%Y-%m-%d').date(),
            fornecedor=data.get('fornecedor'),
            pix=pix_value,
            observacoes=data.get('observacoes'),
            status='Previsto'
        )
        
        print(f"--- [DEBUG] Objeto criado, tentando adicionar ao banco... ---")
        db.session.add(novo_pagamento)
        db.session.flush()  # Flush para obter o ID antes do commit
        print(f"--- [DEBUG] Flush OK, ID atribuído: {novo_pagamento.id} ---")
        db.session.commit()
        print(f"--- [DEBUG] Commit realizado! ---")
        
        # Verificar se foi salvo
        verificacao = PagamentoFuturo.query.get(novo_pagamento.id)
        if verificacao:
            print(f"--- [DEBUG] ✅ VERIFICAÇÃO: PagamentoFuturo ID {verificacao.id} encontrado no banco ---")
            print(f"--- [DEBUG] ✅ Descrição: {verificacao.descricao}, Valor: {verificacao.valor}, Data: {verificacao.data_vencimento} ---")
        else:
            print(f"--- [DEBUG] ❌ ERRO: PagamentoFuturo NÃO encontrado após commit! ---")
        
        print(f"--- [LOG] ✅ Pagamento futuro criado: ID {novo_pagamento.id} na obra {obra_id} com PIX: {novo_pagamento.pix} ---")
        return jsonify(novo_pagamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] ❌ POST /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/<int:pagamento_id>', methods=['PUT', 'OPTIONS'])
@jwt_required(optional=True)
def editar_pagamento_futuro(obra_id, pagamento_id):
    """Edita um pagamento futuro existente"""
    # OPTIONS é permitido sem JWT
    if request.method == 'OPTIONS':
        return '', 200
    
    # PUT requer JWT
    try:
        print(f"--- [DEBUG] Iniciando edição do pagamento {pagamento_id} da obra {obra_id} ---")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoFuturo, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        data = request.get_json()
        print(f"--- [DEBUG] Dados recebidos: {data} ---")
        
        if 'descricao' in data:
            pagamento.descricao = data['descricao']
        if 'valor' in data:
            pagamento.valor = float(data['valor'])
        if 'data_vencimento' in data:
            pagamento.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        if 'fornecedor' in data:
            pagamento.fornecedor = data['fornecedor']
        if 'pix' in data:
            print(f"--- [DEBUG] Salvando PIX: {data['pix']} ---")
            pagamento.pix = data['pix']
        if 'observacoes' in data:
            pagamento.observacoes = data['observacoes']
        if 'status' in data:
            pagamento.status = data['status']
        
        print(f"--- [DEBUG] Tentando commit no banco... ---")
        db.session.commit()
        
        print(f"--- [LOG] ✅ Pagamento futuro {pagamento_id} editado com sucesso na obra {obra_id} ---")
        return jsonify(pagamento.to_dict()), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] ❌ PUT /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/{pagamento_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/<int:pagamento_id>', methods=['DELETE'])
@jwt_required()
def deletar_pagamento_futuro(obra_id, pagamento_id):
    """Deleta um pagamento futuro"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoFuturo, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        db.session.delete(pagamento)
        db.session.commit()
        
        print(f"--- [LOG] Pagamento futuro {pagamento_id} deletado da obra {obra_id} ---")
        return jsonify({"mensagem": "Pagamento futuro deletado com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] DELETE /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/{pagamento_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/<int:pagamento_id>/marcar-pago', methods=['POST'])
@jwt_required()
def marcar_pagamento_futuro_pago(obra_id, pagamento_id):
    """Marca um pagamento futuro como pago e move para o histórico"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoFuturo, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        if pagamento.status == 'Pago':
            return jsonify({"mensagem": "Pagamento já está marcado como pago"}), 200
        
        # ===== NOVA LÓGICA: Move para o Histórico =====
        # 1. CRIAR o Lançamento no Histórico
        novo_lancamento = Lancamento(
            obra_id=pagamento.obra_id,
            tipo='Despesa',
            descricao=pagamento.descricao,
            valor_total=pagamento.valor,
            valor_pago=pagamento.valor,
            data=date.today(),
            data_vencimento=pagamento.data_vencimento,
            status='Pago',
            pix=pagamento.pix,
            prioridade=0,
            fornecedor=pagamento.fornecedor,
            servico_id=None
        )
        db.session.add(novo_lancamento)
        
        # 2. DELETE o PagamentoFuturo (remove do cronograma)
        db.session.delete(pagamento)
        
        # 3. Commit das alterações
        db.session.commit()
        
        print(f"--- [LOG] Pagamento futuro {pagamento_id} movido para o histórico (lancamento_id={novo_lancamento.id}) na obra {obra_id} ---")
        return jsonify({
            "mensagem": "Pagamento marcado como pago e movido para o histórico com sucesso",
            "lancamento_id": novo_lancamento.id
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] POST /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/{pagamento_id}/marcar-pago: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- PAGAMENTOS PARCELADOS ---
@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados', methods=['GET'])
@jwt_required()
def listar_pagamentos_parcelados(obra_id):
    """Lista todos os pagamentos parcelados de uma obra"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamentos = PagamentoParcelado.query.filter_by(obra_id=obra_id).order_by(PagamentoParcelado.data_primeira_parcela).all()
        return jsonify([p.to_dict() for p in pagamentos]), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados', methods=['POST'])
@jwt_required()
def criar_pagamento_parcelado(obra_id):
    """Cria um novo pagamento parcelado"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        
        valor_total = float(data.get('valor_total', 0))
        numero_parcelas = int(data.get('numero_parcelas', 1))
        valor_parcela = valor_total / numero_parcelas if numero_parcelas > 0 else 0
        periodicidade = data.get('periodicidade', 'Mensal')  # Semanal ou Mensal
        
        novo_pagamento = PagamentoParcelado(
            obra_id=obra_id,
            descricao=data.get('descricao'),
            fornecedor=data.get('fornecedor') or None,
            servico_id=data.get('servico_id') or None,  # Vínculo opcional com serviço (converte "" para None)
            # segmento será adicionado quando a coluna existir no banco
            valor_total=valor_total,
            numero_parcelas=numero_parcelas,
            valor_parcela=valor_parcela,
            data_primeira_parcela=datetime.strptime(data.get('data_primeira_parcela'), '%Y-%m-%d').date(),
            periodicidade=periodicidade,
            parcelas_pagas=0,
            status='Ativo',
            observacoes=data.get('observacoes') or None
        )
        
        db.session.add(novo_pagamento)
        db.session.commit()
        
        print(f"--- [LOG] Pagamento parcelado criado: ID {novo_pagamento.id} na obra {obra_id} ---")
        return jsonify(novo_pagamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] POST /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>', methods=['PUT'])
@jwt_required()
def editar_pagamento_parcelado(obra_id, pagamento_id):
    """Edita um pagamento parcelado existente"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        data = request.get_json()
        
        if 'descricao' in data:
            pagamento.descricao = data['descricao']
        if 'fornecedor' in data:
            pagamento.fornecedor = data['fornecedor']
        if 'observacoes' in data:
            pagamento.observacoes = data['observacoes']
        if 'parcelas_pagas' in data:
            pagamento.parcelas_pagas = int(data['parcelas_pagas'])
            # Atualiza status se todas as parcelas foram pagas
            if pagamento.parcelas_pagas >= pagamento.numero_parcelas:
                pagamento.status = 'Concluído'
        if 'status' in data:
            pagamento.status = data['status']
        
        # Recalcula valor_parcela se valor_total ou numero_parcelas mudarem
        if 'valor_total' in data or 'numero_parcelas' in data:
            if 'valor_total' in data:
                pagamento.valor_total = float(data['valor_total'])
            if 'numero_parcelas' in data:
                pagamento.numero_parcelas = int(data['numero_parcelas'])
            pagamento.valor_parcela = pagamento.valor_total / pagamento.numero_parcelas if pagamento.numero_parcelas > 0 else 0
        
        if 'data_primeira_parcela' in data:
            pagamento.data_primeira_parcela = datetime.strptime(data['data_primeira_parcela'], '%Y-%m-%d').date()
        
        db.session.commit()
        
        print(f"--- [LOG] Pagamento parcelado {pagamento_id} editado na obra {obra_id} ---")
        return jsonify(pagamento.to_dict()), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] PUT /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pagamento_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>', methods=['DELETE'])
@jwt_required()
def deletar_pagamento_parcelado(obra_id, pagamento_id):
    """Deleta um pagamento parcelado"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        db.session.delete(pagamento)
        db.session.commit()
        
        print(f"--- [LOG] Pagamento parcelado {pagamento_id} deletado da obra {obra_id} ---")
        return jsonify({"mensagem": "Pagamento parcelado deletado com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] DELETE /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pagamento_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- TABELA DE PREVISÕES (CÁLCULO) ---
@app.route('/sid/cronograma-financeiro/<int:obra_id>/previsoes', methods=['GET'])
@jwt_required()
def calcular_previsoes(obra_id):
    """Calcula a tabela de previsões mensais usando parcelas individuais e pagamentos de serviços"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        previsoes_por_mes = {}
        
        # 1. Pagamentos Futuros (Únicos)
        pagamentos_futuros = PagamentoFuturo.query.filter_by(
            obra_id=obra_id
        ).filter(
            PagamentoFuturo.status != 'Cancelado',
            PagamentoFuturo.status != 'Pago'
        ).all()
        
        for pag in pagamentos_futuros:
            mes_chave = pag.data_vencimento.strftime('%Y-%m')
            if mes_chave not in previsoes_por_mes:
                previsoes_por_mes[mes_chave] = 0
            previsoes_por_mes[mes_chave] += pag.valor
        
        # 2. Parcelas Individuais
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            PagamentoParcelado.status != 'Cancelado',
            ParcelaIndividual.status == 'Previsto'
        ).all()
        
        for parcela in parcelas:
            mes_chave = parcela.data_vencimento.strftime('%Y-%m')
            if mes_chave not in previsoes_por_mes:
                previsoes_por_mes[mes_chave] = 0
            previsoes_por_mes[mes_chave] += parcela.valor_parcela
        
        # 3. NOVO: Pagamentos de Serviços com status "A Pagar"
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        for servico in servicos:
            pagamentos_servico = PagamentoServico.query.filter_by(
                servico_id=servico.id
            ).filter(
                PagamentoServico.valor_pago < PagamentoServico.valor_total  # Tem saldo a pagar
            ).all()
            
            for pag_serv in pagamentos_servico:
                if pag_serv.data_vencimento:  # Se tem data de vencimento
                    valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
                    if valor_pendente > 0:
                        mes_chave = pag_serv.data_vencimento.strftime('%Y-%m')
                        if mes_chave not in previsoes_por_mes:
                            previsoes_por_mes[mes_chave] = 0
                        previsoes_por_mes[mes_chave] += valor_pendente
        
        previsoes_lista = []
        for mes_chave in sorted(previsoes_por_mes.keys()):
            ano, mes = mes_chave.split('-')
            meses_pt = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
            mes_nome = meses_pt[int(mes)]
            
            previsoes_lista.append({
                'mes_chave': mes_chave,
                'mes_nome': f"{mes_nome}/{ano}",
                'valor': round(previsoes_por_mes[mes_chave], 2)
            })
        
        print(f"--- [LOG] Previsões calculadas para obra {obra_id}: {len(previsoes_lista)} meses ---")
        return jsonify(previsoes_lista), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET previsões: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# ========================================
# ENDPOINTS: PARCELAS INDIVIDUAIS (NOVO!)
# ========================================

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def listar_parcelas_individuais(obra_id, pagamento_id):
    """
    Lista todas as parcelas individuais de um pagamento parcelado.
    Se as parcelas não existirem, gera automaticamente baseado na configuração do pagamento.
    """
    # Handler para OPTIONS (CORS preflight)
    if request.method == 'OPTIONS':
        return make_response('', 200)
    
    try:
        # Validações de acesso
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamento parcelado (usando db.session.get para compatibilidade)
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        # Buscar parcelas individuais existentes
        parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).order_by(ParcelaIndividual.numero_parcela).all()
        
        # Gerar parcelas automaticamente se não existirem
        if not parcelas:
            print(f"--- [LOG] Gerando parcelas para Pagamento ID {pagamento_id} ---")
            
            import calendar
            from datetime import timedelta

            # Função auxiliar local para cálculo preciso de meses
            def add_months_local(source_date, months):
                month = source_date.month - 1 + months
                year = source_date.year + month // 12
                month = month % 12 + 1
                day = min(source_date.day, calendar.monthrange(year, month)[1])
                return date(year, month, day)

            valor_parcela_padrao = pagamento.valor_parcela
            
            # Gerar cada parcela
            for i in range(pagamento.numero_parcelas):
                numero_parcela = i + 1
                
                # Ajustar valor da última parcela para fechar o total exato (evita dízimas)
                if numero_parcela == pagamento.numero_parcelas:
                    valor_parcelas_anteriores = valor_parcela_padrao * (pagamento.numero_parcelas - 1)
                    valor_desta_parcela = pagamento.valor_total - valor_parcelas_anteriores
                else:
                    valor_desta_parcela = valor_parcela_padrao
                
                # Calcular data de vencimento (Lógica corrigida)
                if pagamento.periodicidade == 'Semanal':
                    data_vencimento = pagamento.data_primeira_parcela + timedelta(days=7 * i)
                elif pagamento.periodicidade == 'Quinzenal':
                    data_vencimento = pagamento.data_primeira_parcela + timedelta(days=15 * i)
                else: # Mensal (Padrão)
                    data_vencimento = add_months_local(pagamento.data_primeira_parcela, i)
                
                # Determinar status inicial
                status = 'Pago' if i < pagamento.parcelas_pagas else 'Previsto'
                data_pagamento = data_vencimento if status == 'Pago' else None
                
                # Criar parcela
                parcela = ParcelaIndividual(
                    pagamento_parcelado_id=pagamento_id,
                    numero_parcela=numero_parcela,
                    valor_parcela=valor_desta_parcela,
                    data_vencimento=data_vencimento,
                    data_pagamento=data_pagamento,
                    status=status,
                    forma_pagamento=None,
                    observacao=None
                )
                db.session.add(parcela)
            
            db.session.commit()
            
            # Recarregar parcelas geradas
            parcelas = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=pagamento_id
            ).order_by(ParcelaIndividual.numero_parcela).all()
        
        return jsonify([p.to_dict() for p in parcelas]), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] listar_parcelas_individuais: {str(e)}\n{error_details} ---")
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas/<int:parcela_id>', methods=['PUT'])
@jwt_required()
def editar_parcela_individual(obra_id, pagamento_id, parcela_id):
    """Edita uma parcela individual (valor, data, observação)"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        parcela = db.session.get(ParcelaIndividual, parcela_id)
        if not parcela or parcela.pagamento_parcelado_id != pagamento_id:
            return jsonify({"erro": "Parcela não encontrada"}), 404
        
        data = request.get_json()
        
        # Atualiza os campos permitidos
        if 'valor_parcela' in data:
            parcela.valor_parcela = float(data['valor_parcela'])
        
        if 'data_vencimento' in data:
            parcela.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        
        if 'observacao' in data:
            parcela.observacao = data['observacao']
        
        if 'status' in data:
            parcela.status = data['status']
            if data['status'] == 'Pago' and 'data_pagamento' in data:
                parcela.data_pagamento = datetime.strptime(data['data_pagamento'], '%Y-%m-%d').date()
        
        db.session.commit()
        
        # Recalcula o valor_total do pagamento parcelado
        todas_parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        novo_valor_total = sum(p.valor_parcela for p in todas_parcelas)
        pagamento.valor_total = novo_valor_total
        
        # Atualiza parcelas_pagas
        parcelas_pagas_count = sum(1 for p in todas_parcelas if p.status == 'Pago')
        pagamento.parcelas_pagas = parcelas_pagas_count
        
        db.session.commit()
        
        print(f"--- [LOG] Parcela {parcela_id} editada ---")
        return jsonify(parcela.to_dict()), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] PUT parcela individual: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas/<int:parcela_id>/pagar', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def marcar_parcela_paga(obra_id, pagamento_id, parcela_id):
    """Marca uma parcela individual como paga e cria lançamento no histórico"""
    
    # Handler para OPTIONS (CORS preflight)
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response

    try:
        print(f"\n{'='*80}")
        print(f"💳 INÍCIO: marcar_parcela_paga")
        print(f"   obra_id={obra_id}, pagamento_id={pagamento_id}, parcela_id={parcela_id}")
        print(f"{'='*80}")
        
        # Validações de acesso
        current_user = get_current_user()
        print(f"   👤 Usuário: {current_user.username} (role: {current_user.role})")
        
        if not user_has_access_to_obra(current_user, obra_id):
            print(f"   ❌ Acesso negado à obra {obra_id}")
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamento parcelado
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            print(f"   ❌ Pagamento {pagamento_id} não encontrado ou não pertence à obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        print(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        print(f"      - servico_id: {pagamento.servico_id}")
        print(f"      - fornecedor: {pagamento.fornecedor}")
        
        # Buscar parcela
        parcela = db.session.get(ParcelaIndividual, parcela_id)
        if not parcela or parcela.pagamento_parcelado_id != pagamento_id:
            print(f"   ❌ Parcela {parcela_id} não encontrada ou não pertence ao pagamento {pagamento_id}")
            return jsonify({"erro": "Parcela não encontrada"}), 404
        
        if parcela.status == 'Pago':
            print(f"   ⚠️ Parcela {parcela_id} já estava paga")
            return jsonify({"mensagem": "Parcela já está marcada como paga"}), 200
        
        print(f"   ✅ Parcela encontrada: {parcela.numero_parcela}/{pagamento.numero_parcelas}")
        print(f"      - valor: R$ {parcela.valor_parcela}")
        
        # Processar dados
        data = request.get_json()
        
        # Marcar parcela como paga
        parcela.status = 'Pago'
        parcela.data_pagamento = datetime.strptime(
            data.get('data_pagamento', date.today().isoformat()), 
            '%Y-%m-%d'
        ).date()
        parcela.forma_pagamento = data.get('forma_pagamento', None)
        
        print(f"   ✅ Parcela marcada como paga em {parcela.data_pagamento}")
        
        # Criar lançamento no histórico
        descricao_lancamento = f"{pagamento.descricao} (Parcela {parcela.numero_parcela}/{pagamento.numero_parcelas})"
        
        # Tratamento seguro do segmento
        segmento_info = 'Material'
        if hasattr(pagamento, 'segmento') and pagamento.segmento:
            segmento_info = pagamento.segmento
        
        print(f"   📄 Criando lançamento: '{descricao_lancamento}'")
        print(f"      - segmento: {segmento_info}")
        
        novo_lancamento = Lancamento(
            obra_id=pagamento.obra_id,
            tipo='Despesa',
            descricao=descricao_lancamento,
            valor_total=parcela.valor_parcela,
            valor_pago=parcela.valor_parcela,
            data=parcela.data_pagamento,
            data_vencimento=parcela.data_vencimento,
            status='Pago',
            pix=None,
            prioridade=0,
            fornecedor=pagamento.fornecedor,
            servico_id=pagamento.servico_id
        )
        
        # Tenta atribuir segmento se o modelo suportar
        if hasattr(novo_lancamento, 'segmento'):
            novo_lancamento.segmento = segmento_info
            print(f"      - segmento atribuído ao lançamento")
        
        db.session.add(novo_lancamento)
        db.session.flush()
        
        print(f"   ✅ Lançamento criado com ID={novo_lancamento.id}")
        
        # Criar/atualizar PagamentoServico se houver vínculo
        if pagamento.servico_id:
            # ⭐ VALIDAR SE SERVIÇO EXISTE
            servico = db.session.get(Servico, pagamento.servico_id)
            if not servico:
                print(f"--- [AVISO] Serviço {pagamento.servico_id} não existe no banco! Continuando sem vincular ao serviço. ---")
                novo_lancamento.servico_id = None
            else:
                print(f"--- [LOG] Parcela vinculada ao serviço {pagamento.servico_id}, criando/atualizando PagamentoServico ---")
                
                # Determinar tipo de pagamento baseado no segmento do pagamento parcelado
                try:
                    if hasattr(pagamento, 'segmento') and pagamento.segmento:
                        # Converter "Mão de Obra" para "mao_de_obra" e "Material" para "material"
                        if pagamento.segmento == 'Mão de Obra':
                            tipo_pag = 'mao_de_obra'
                        else:
                            tipo_pag = 'material'
                        print(f"--- [LOG] Segmento detectado: {pagamento.segmento} -> tipo_pagamento: {tipo_pag} ---")
                    else:
                        tipo_pag = 'material'  # Padrão
                        print(f"--- [LOG] Segmento não encontrado, usando padrão: material ---")
                except Exception as seg_error:
                    tipo_pag = 'material'  # Fallback seguro
                    print(f"--- [LOG] Erro ao detectar segmento: {seg_error}, usando padrão: material ---")
                
                # Buscar PagamentoServico existente para este serviço e fornecedor
                pagamento_servico_existente = PagamentoServico.query.filter_by(
                    servico_id=pagamento.servico_id,
                    fornecedor=pagamento.fornecedor,
                    tipo_pagamento=tipo_pag
                ).first()
                
                if pagamento_servico_existente:
                    # Atualizar valor_pago do registro existente
                    pagamento_servico_existente.valor_pago += parcela.valor_parcela
                    print(f"--- [LOG] PagamentoServico ID={pagamento_servico_existente.id} atualizado. Novo valor_pago: {pagamento_servico_existente.valor_pago} ---")
                else:
                    # Criar novo registro
                    novo_pagamento_servico = PagamentoServico(
                        servico_id=pagamento.servico_id,
                        tipo_pagamento=tipo_pag,
                        valor_total=parcela.valor_parcela,
                        valor_pago=parcela.valor_parcela,
                        data=parcela.data_pagamento,
                        fornecedor=pagamento.fornecedor,
                        forma_pagamento=parcela.forma_pagamento,
                        prioridade=0
                    )
                    db.session.add(novo_pagamento_servico)
                    db.session.flush()
                    print(f"--- [LOG] Novo PagamentoServico criado com ID={novo_pagamento_servico.id}, tipo={tipo_pag} ---")
        
        # Atualizar contador de parcelas pagas
        todas_parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        parcelas_pagas_count = sum(1 for p in todas_parcelas if p.status == 'Pago')
        pagamento.parcelas_pagas = parcelas_pagas_count
        
        print(f"   📊 Total de parcelas pagas: {parcelas_pagas_count}/{pagamento.numero_parcelas}")
        
        # Se todas foram pagas, atualizar status
        if parcelas_pagas_count >= pagamento.numero_parcelas:
            pagamento.status = 'Concluído'
            print(f"   🎉 Pagamento marcado como Concluído")
        
        # Commit final
        db.session.commit()
        
        print(f"   ✅ SUCESSO: Parcela {parcela_id} paga e lançamento {novo_lancamento.id} criado")
        print(f"{'='*80}\n")
        
        return jsonify({
            "mensagem": "Parcela paga com sucesso",
            "parcela": parcela.to_dict(),
            "lancamento_id": novo_lancamento.id
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"\n{'='*80}")
        print(f"❌ ERRO FATAL em marcar_parcela_paga:")
        print(f"   {str(e)}")
        print(f"\nStack trace completo:")
        print(error_details)
        print(f"{'='*80}\n")
        return jsonify({"erro": str(e)}), 500
@app.route('/sid/cronograma-financeiro/<int:obra_id>/alertas-vencimento', methods=['GET'])
@jwt_required()
def obter_alertas_vencimento(obra_id):
    """
    Retorna um resumo dos pagamentos por categoria de vencimento:
    - Vencidos (atrasados)
    - Vence Hoje
    - Vence Amanhã
    - Vence em 7 dias
    - Futuros (mais de 7 dias)
    """
    try:
        print(f"--- [DEBUG] Iniciando obter_alertas_vencimento para obra {obra_id} ---")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        hoje = date.today()
        amanha = hoje + timedelta(days=1)
        em_7_dias = hoje + timedelta(days=7)
        
        print(f"--- [DEBUG] Hoje: {hoje}, Amanhã: {amanha}, Em 7 dias: {em_7_dias} ---")
        
        alertas = {
            "vencidos": {"quantidade": 0, "valor_total": 0, "itens": []},
            "vence_hoje": {"quantidade": 0, "valor_total": 0, "itens": []},
            "vence_amanha": {"quantidade": 0, "valor_total": 0, "itens": []},
            "vence_7_dias": {"quantidade": 0, "valor_total": 0, "itens": []},
            "futuros": {"quantidade": 0, "valor_total": 0, "itens": []}  # CORREÇÃO: Adicionado array "itens"
        }
        
        # 1. PAGAMENTOS FUTUROS
        pagamentos_futuros = PagamentoFuturo.query.filter_by(
            obra_id=obra_id
        ).filter(
            PagamentoFuturo.status == 'Previsto'
        ).all()
        
        print(f"--- [DEBUG] Encontrados {len(pagamentos_futuros)} PagamentoFuturo com status 'Previsto' ---")
        
        for pag in pagamentos_futuros:
            print(f"--- [DEBUG] PagamentoFuturo ID {pag.id}: {pag.descricao}, Valor: {pag.valor}, Vencimento: {pag.data_vencimento} ---")
            
            item = {
                "tipo": "Pagamento Futuro",
                "descricao": pag.descricao,
                "fornecedor": pag.fornecedor,
                "valor": pag.valor,
                "data_vencimento": pag.data_vencimento.isoformat(),
                "id": pag.id
            }
            
            if pag.data_vencimento < hoje:
                print(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCIDO ---")
                alertas["vencidos"]["quantidade"] += 1
                alertas["vencidos"]["valor_total"] += pag.valor
                alertas["vencidos"]["itens"].append(item)
            elif pag.data_vencimento == hoje:
                print(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCE HOJE ---")
                alertas["vence_hoje"]["quantidade"] += 1
                alertas["vence_hoje"]["valor_total"] += pag.valor
                alertas["vence_hoje"]["itens"].append(item)
            elif pag.data_vencimento == amanha:
                print(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCE AMANHÃ ---")
                alertas["vence_amanha"]["quantidade"] += 1
                alertas["vence_amanha"]["valor_total"] += pag.valor
                alertas["vence_amanha"]["itens"].append(item)
            elif pag.data_vencimento <= em_7_dias:
                print(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCE EM 7 DIAS ---")
                alertas["vence_7_dias"]["quantidade"] += 1
                alertas["vence_7_dias"]["valor_total"] += pag.valor
                alertas["vence_7_dias"]["itens"].append(item)
            else:
                print(f"--- [DEBUG] PagamentoFuturo {pag.id} → FUTURO (>7 dias) ---")
                alertas["futuros"]["quantidade"] += 1
                alertas["futuros"]["valor_total"] += pag.valor
                alertas["futuros"]["itens"].append(item)
        
        # 2. PARCELAS INDIVIDUAIS DE PAGAMENTOS PARCELADOS
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).all()
        
        for parcela in parcelas:
            pag = parcela.pagamento_parcelado
            item = {
                "tipo": "Parcela",
                "descricao": f"{pag.descricao} - Parcela {parcela.numero_parcela}/{pag.numero_parcelas}",
                "fornecedor": pag.fornecedor,
                "valor": parcela.valor_parcela,
                "data_vencimento": parcela.data_vencimento.isoformat(),
                "id": parcela.id,
                "pagamento_parcelado_id": pag.id
            }
            
            if parcela.data_vencimento < hoje:
                alertas["vencidos"]["quantidade"] += 1
                alertas["vencidos"]["valor_total"] += parcela.valor_parcela
                alertas["vencidos"]["itens"].append(item)
            elif parcela.data_vencimento == hoje:
                alertas["vence_hoje"]["quantidade"] += 1
                alertas["vence_hoje"]["valor_total"] += parcela.valor_parcela
                alertas["vence_hoje"]["itens"].append(item)
            elif parcela.data_vencimento == amanha:
                alertas["vence_amanha"]["quantidade"] += 1
                alertas["vence_amanha"]["valor_total"] += parcela.valor_parcela
                alertas["vence_amanha"]["itens"].append(item)
            elif parcela.data_vencimento <= em_7_dias:
                alertas["vence_7_dias"]["quantidade"] += 1
                alertas["vence_7_dias"]["valor_total"] += parcela.valor_parcela
                alertas["vence_7_dias"]["itens"].append(item)
            else:
                alertas["futuros"]["quantidade"] += 1
                alertas["futuros"]["valor_total"] += parcela.valor_parcela
                alertas["futuros"]["itens"].append(item)
        
        # 3. NOVO: PAGAMENTOS DE SERVIÇOS COM SALDO PENDENTE
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        for servico in servicos:
            pagamentos_servico = PagamentoServico.query.filter_by(
                servico_id=servico.id
            ).filter(
                PagamentoServico.valor_pago < PagamentoServico.valor_total
            ).all()
            
            for pag_serv in pagamentos_servico:
                valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
                if valor_pendente > 0 and pag_serv.data_vencimento:
                    item = {
                        "tipo": "Pagamento Serviço",
                        "descricao": f"{servico.nome} - {pag_serv.tipo_pagamento.replace('_', ' ').title()}",
                        "fornecedor": pag_serv.fornecedor,
                        "valor": valor_pendente,
                        "data_vencimento": pag_serv.data_vencimento.isoformat(),
                        "id": pag_serv.id,
                        "servico_id": servico.id
                    }
                    
                    if pag_serv.data_vencimento < hoje:
                        alertas["vencidos"]["quantidade"] += 1
                        alertas["vencidos"]["valor_total"] += valor_pendente
                        alertas["vencidos"]["itens"].append(item)
                    elif pag_serv.data_vencimento == hoje:
                        alertas["vence_hoje"]["quantidade"] += 1
                        alertas["vence_hoje"]["valor_total"] += valor_pendente
                        alertas["vence_hoje"]["itens"].append(item)
                    elif pag_serv.data_vencimento == amanha:
                        alertas["vence_amanha"]["quantidade"] += 1
                        alertas["vence_amanha"]["valor_total"] += valor_pendente
                        alertas["vence_amanha"]["itens"].append(item)
                    elif pag_serv.data_vencimento <= em_7_dias:
                        alertas["vence_7_dias"]["quantidade"] += 1
                        alertas["vence_7_dias"]["valor_total"] += valor_pendente
                        alertas["vence_7_dias"]["itens"].append(item)
                    else:
                        alertas["futuros"]["quantidade"] += 1
                        alertas["futuros"]["valor_total"] += valor_pendente
                        alertas["futuros"]["itens"].append(item)
        
        # Arredonda os valores
        for categoria in alertas.values():
            if 'valor_total' in categoria:
                categoria['valor_total'] = round(categoria['valor_total'], 2)
        
        print(f"--- [DEBUG] RESULTADO FINAL DOS ALERTAS ---")
        print(f"  Vencidos: {alertas['vencidos']['quantidade']} itens, Total: R$ {alertas['vencidos']['valor_total']}")
        print(f"  Vence Hoje: {alertas['vence_hoje']['quantidade']} itens, Total: R$ {alertas['vence_hoje']['valor_total']}")
        print(f"  Vence Amanhã: {alertas['vence_amanha']['quantidade']} itens, Total: R$ {alertas['vence_amanha']['valor_total']}")
        print(f"  Vence em 7 dias: {alertas['vence_7_dias']['quantidade']} itens, Total: R$ {alertas['vence_7_dias']['valor_total']}")
        print(f"  Futuros (>7 dias): {alertas['futuros']['quantidade']} itens, Total: R$ {alertas['futuros']['valor_total']}")
        print(f"--- [LOG] Alertas de vencimento calculados para obra {obra_id} ---")
        return jsonify(alertas), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET alertas vencimento: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# --- ENDPOINT PARA GERAR RELATÓRIO DO CRONOGRAMA FINANCEIRO (PDF) ---
@app.route('/obras/<int:obra_id>/relatorio-cronograma-pdf', methods=['GET'])
@jwt_required()
def gerar_relatorio_cronograma_pdf(obra_id):
    """Gera um relatório PDF do cronograma financeiro de uma obra"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        # Buscar dados do cronograma
        hoje = date.today()
        
        pagamentos_futuros = PagamentoFuturo.query.filter_by(
            obra_id=obra_id
        ).order_by(PagamentoFuturo.data_vencimento).all()
        
        # Separar pagamentos em vencidos e previstos
        pagamentos_vencidos = []
        pagamentos_previstos = []
        
        for pag in pagamentos_futuros:
            if pag.status == 'Previsto' and pag.data_vencimento < hoje:
                pagamentos_vencidos.append(pag)
            elif pag.status == 'Previsto':
                pagamentos_previstos.append(pag)
        
        # NOVO: Buscar também pagamentos de serviços pendentes
        pagamentos_servicos_pendentes = []
        pagamentos_servicos_vencidos = []
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        for servico in servicos:
            pagamentos_servico = PagamentoServico.query.filter_by(
                servico_id=servico.id
            ).filter(
                PagamentoServico.valor_pago < PagamentoServico.valor_total
            ).all()
            
            for pag_serv in pagamentos_servico:
                valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
                if valor_pendente > 0 and pag_serv.data_vencimento:
                    # Determinar descrição do tipo (mão de obra ou material)
                    tipo_desc = pag_serv.tipo_pagamento.replace('_', ' ').title() if pag_serv.tipo_pagamento else ''
                    
                    # Determinar forma de pagamento (PIX, Boleto, TED, etc)
                    forma_pag = pag_serv.forma_pagamento if pag_serv.forma_pagamento else None
                    
                    # Determinar PIX - agora o pagamento tem seu próprio campo PIX
                    pix_display = pag_serv.pix if pag_serv.pix else '-'
                    
                    # Montar descrição (removemos a forma da descrição já que terá coluna própria)
                    descricao_completa = f"{servico.nome} - {tipo_desc}"
                    
                    pag_dict = {
                        'descricao': descricao_completa,
                        'fornecedor': pag_serv.fornecedor,
                        'pix': pix_display,  # Incluir PIX/forma de pagamento
                        'valor': valor_pendente,
                        'data_vencimento': pag_serv.data_vencimento,
                        'tipo_pagamento': '-',
                        'status': 'Previsto' if pag_serv.data_vencimento >= hoje else 'Vencido'
                    }
                    
                    if pag_serv.data_vencimento < hoje:
                        pagamentos_servicos_vencidos.append(pag_dict)
                    else:
                        pagamentos_servicos_pendentes.append(pag_dict)
        
        pagamentos_parcelados = PagamentoParcelado.query.filter_by(
            obra_id=obra_id
        ).all()
        
        # Buscar parcelas de todos os pagamentos parcelados
        todas_parcelas = []
        for pag_parcelado in pagamentos_parcelados:
            parcelas = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=pag_parcelado.id
            ).order_by(ParcelaIndividual.numero_parcela).all()
            todas_parcelas.extend(parcelas)
        
        # Criar o PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        secao_numero = 0  # Contador para numeração dinâmica das seções
        
        # Título
        title_style = styles['Title']
        title = Paragraph(f"<b>Relatório do Cronograma Financeiro</b><br/>{obra.nome}", title_style)
        elements.append(title)
        elements.append(Spacer(1, 0.5*cm))
        
        # Informações da obra
        info_style = styles['Normal']
        info_text = f"<b>Cliente:</b> {obra.cliente or 'N/A'}<br/>"
        info_text += f"<b>Data do Relatório:</b> {date.today().strftime('%d/%m/%Y')}"
        elements.append(Paragraph(info_text, info_style))
        elements.append(Spacer(1, 0.5*cm))
        
        # Seção: RESUMO - Atenção Urgente (Vencidos + Próximos 7 dias)
        hoje = date.today()
        limite_7_dias = hoje + timedelta(days=7)
        
        # Separar pagamentos por urgência
        pagamentos_resumo = []  # Vencidos + próximos 7 dias
        pagamentos_futuros_normais = []  # Após 7 dias
        
        # Adicionar vencidos ao resumo
        for pag in pagamentos_vencidos:
            pagamentos_resumo.append({
                'descricao': pag.descricao,
                'fornecedor': pag.fornecedor if pag.fornecedor else '-',
                'pix': pag.pix if pag.pix else '-',  # Chave PIX do pagamento
                'valor': pag.valor,
                'vencimento': pag.data_vencimento,
                'status': 'Vencido',
                'urgente': True
            })
        
        # Adicionar serviços vencidos ao resumo
        for pag_serv in pagamentos_servicos_vencidos:
            pagamentos_resumo.append({
                'descricao': pag_serv['descricao'],
                'fornecedor': pag_serv['fornecedor'] if pag_serv['fornecedor'] else '-',
                'pix': pag_serv['pix'],  # PIX já está no dicionário
                'valor': pag_serv['valor'],
                'vencimento': pag_serv['data_vencimento'],
                'status': 'Vencido',
                'urgente': True
            })
        
        # Classificar pagamentos previstos (únicos)
        for pag in pagamentos_previstos:
            if pag.data_vencimento <= limite_7_dias:
                pagamentos_resumo.append({
                    'descricao': pag.descricao,
                    'fornecedor': pag.fornecedor if pag.fornecedor else '-',
                    'pix': pag.pix if pag.pix else '-',  # Chave PIX do pagamento
                    'valor': pag.valor,
                    'vencimento': pag.data_vencimento,
                    'status': 'Próximos 7 dias',
                    'urgente': True
                })
            else:
                pagamentos_futuros_normais.append({
                    'descricao': pag.descricao,
                    'fornecedor': pag.fornecedor if pag.fornecedor else '-',
                    'tipo_pagamento': '-',
                    'valor': pag.valor,
                    'vencimento': pag.data_vencimento,
                    'status': pag.status
                })
        
        # Classificar pagamentos de serviços pendentes
        for pag_serv in pagamentos_servicos_pendentes:
            if pag_serv['data_vencimento'] <= limite_7_dias:
                pagamentos_resumo.append({
                    'descricao': pag_serv['descricao'],
                    'fornecedor': pag_serv['fornecedor'] if pag_serv['fornecedor'] else '-',
                    'pix': pag_serv['pix'],  # PIX já está no dicionário
                    'valor': pag_serv['valor'],
                    'vencimento': pag_serv['data_vencimento'],
                    'status': 'Próximos 7 dias',
                    'urgente': True
                })
            else:
                pagamentos_futuros_normais.append({
                    'descricao': pag_serv['descricao'],
                    'fornecedor': pag_serv['fornecedor'] if pag_serv['fornecedor'] else '-',
                    'tipo_pagamento': pag_serv['tipo_pagamento'],
                    'valor': pag_serv['valor'],
                    'vencimento': pag_serv['data_vencimento'],
                    'status': pag_serv['status']
                })
        
        # Ordenar resumo por data (mais antigos primeiro)
        pagamentos_resumo.sort(key=lambda x: x['vencimento'])
        
        # Mostrar seção RESUMO se houver pagamentos urgentes
        if pagamentos_resumo:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. RESUMO - Atenção Urgente ⚠️</b><br/><font size=9>(Vencidos e próximos 7 dias)</font>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            data_resumo = [['Descrição', 'Fornecedor', 'PIX', 'Valor', 'Vencimento', 'Status']]
            
            for pag in pagamentos_resumo:
                # Definir cor do status
                status_text = pag['status']
                
                data_resumo.append([
                    pag['descricao'][:25],
                    pag['fornecedor'][:15],
                    pag['pix'][:20] if pag['pix'] != '-' else '-',  # Coluna PIX adicionada
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y'),
                    status_text
                ])
            
            # Ajustar larguras das colunas (agora são 6 colunas)
            table = Table(data_resumo, colWidths=[4.5*cm, 2.5*cm, 3*cm, 2.5*cm, 2.5*cm, 2*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff6f00')),  # Laranja escuro
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fff3e0')),  # Fundo laranja claro
            ]))
            elements.append(table)
            elements.append(Spacer(1, 0.5*cm))
        
        # Seção: Pagamentos Futuros (Após 7 dias)
        if pagamentos_futuros_normais:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. Pagamentos Futuros</b><br/><font size=9>(Após 7 dias)</font>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            data_futuros = [['Descrição', 'Fornecedor', 'Valor', 'Vencimento']]
            
            # Adicionar pagamentos futuros (após 7 dias)
            for pag in pagamentos_futuros_normais:
                data_futuros.append([
                    pag['descricao'][:30],
                    pag['fornecedor'][:18],
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y')
                ])
            
            # Ajustar larguras sem coluna Tipo e Status
            table = Table(data_futuros, colWidths=[7*cm, 4*cm, 3*cm, 3*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a90e2')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white])
            ]))
            elements.append(table)
            elements.append(Spacer(1, 0.5*cm))
        
        # Seção: Pagamentos Parcelados
        if pagamentos_parcelados:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. Pagamentos Parcelados</b>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            for pag_parcelado in pagamentos_parcelados:
                # Buscar parcelas deste pagamento para calcular o total
                parcelas = ParcelaIndividual.query.filter_by(
                    pagamento_parcelado_id=pag_parcelado.id
                ).order_by(ParcelaIndividual.numero_parcela).all()
                
                # Calcular valor total real de todas as parcelas
                valor_total_parcelas = sum(p.valor_parcela for p in parcelas)
                
                # Subtítulo do pagamento parcelado - mostra apenas o valor total
                sub_title = Paragraph(
                    f"<b>{pag_parcelado.descricao}</b> - Total: {formatar_real(valor_total_parcelas)} | Fornecedor: {pag_parcelado.fornecedor or '-'}",
                    styles['Heading3']
                )
                elements.append(sub_title)
                elements.append(Spacer(1, 0.2*cm))
                
                if parcelas:
                    data_parcelas = [['Parcela', 'Valor', 'Vencimento', 'Status', 'Tipo', 'Forma Pgto.', 'Pago em']]
                    
                    # Variável para controlar cores
                    row_colors = []
                    
                    for parcela in parcelas:
                        # Determinar se está vencida
                        status_display = parcela.status
                        if parcela.status == 'Previsto' and parcela.data_vencimento < hoje:
                            status_display = 'Vencido'
                            row_colors.append(colors.HexColor('#ffcdd2'))  # Vermelho claro
                        else:
                            row_colors.append(colors.whitesmoke if len(row_colors) % 2 == 0 else colors.white)
                        
                        # Determinar valor da coluna "Forma Pgto."
                        forma_pagamento_display = parcela.forma_pagamento if parcela.forma_pagamento else '-'
                        
                        # Determinar valor da coluna "Pago em"
                        pago_em_display = parcela.data_pagamento.strftime('%d/%m/%Y') if parcela.data_pagamento else '-'
                        
                        data_parcelas.append([
                            f"{parcela.numero_parcela}/{pag_parcelado.numero_parcelas}",
                            formatar_real(parcela.valor_parcela),
                            parcela.data_vencimento.strftime('%d/%m/%Y'),
                            status_display,
                            pag_parcelado.periodicidade or '-',  # Tipo = Periodicidade
                            forma_pagamento_display,  # Nova coluna
                            pago_em_display
                        ])
                    
                    table_parcelas = Table(data_parcelas, colWidths=[1.8*cm, 2.2*cm, 2.2*cm, 2*cm, 2*cm, 2.2*cm, 2.2*cm])
                    
                    style_list = [
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#5cb85c')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 9),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                        ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ]
                    
                    # Adicionar cores de fundo linha por linha
                    for i, color in enumerate(row_colors, start=1):
                        style_list.append(('BACKGROUND', (0, i), (-1, i), color))
                        if color == colors.HexColor('#ffcdd2'):  # Se for vencida
                            style_list.append(('TEXTCOLOR', (3, i), (3, i), colors.HexColor('#d32f2f')))  # Status em vermelho
                    
                    table_parcelas.setStyle(TableStyle(style_list))
                    elements.append(table_parcelas)
                    elements.append(Spacer(1, 0.3*cm))
        
        # Seção: Resumo Financeiro
        secao_numero += 1
        section_title = Paragraph(f"<b>{secao_numero}. Resumo Financeiro</b>", styles['Heading2'])
        elements.append(section_title)
        elements.append(Spacer(1, 0.3*cm))
        
        # Calcular totais
        total_futuros = sum(pag.valor for pag in pagamentos_previstos)
        total_vencidos_unicos = sum(pag.valor for pag in pagamentos_vencidos)
        
        # Adicionar pagamentos de serviços
        total_servicos_pendentes = sum(pag_serv['valor'] for pag_serv in pagamentos_servicos_pendentes)
        total_servicos_vencidos = sum(pag_serv['valor'] for pag_serv in pagamentos_servicos_vencidos)
        
        # Parcelas
        total_parcelados = sum(
            parcela.valor_parcela for parcela in todas_parcelas if parcela.status == 'Previsto' and parcela.data_vencimento >= hoje
        )
        total_parcelas_vencidas = sum(
            parcela.valor_parcela for parcela in todas_parcelas if parcela.status == 'Previsto' and parcela.data_vencimento < hoje
        )
        total_pago_parcelas = sum(
            parcela.valor_parcela for parcela in todas_parcelas if parcela.status == 'Pago'
        )
        
        total_geral_vencido = total_vencidos_unicos + total_servicos_vencidos + total_parcelas_vencidas
        total_geral_previsto = total_futuros + total_servicos_pendentes + total_parcelados
        total_geral = total_geral_vencido + total_geral_previsto
        
        resumo_data = [
            ['Descrição', 'Valor'],
            ['Total de Pagamentos Futuros (Previstos)', formatar_real(total_futuros)],
            ['Total de Pagamentos de Serviços (Previstos)', formatar_real(total_servicos_pendentes)],
            ['Total de Parcelas (Previstas)', formatar_real(total_parcelados)],
            ['', ''],  # Linha em branco
            ['Total de Pagamentos VENCIDOS (Únicos)', formatar_real(total_vencidos_unicos)],
            ['Total de Pagamentos de Serviços VENCIDOS', formatar_real(total_servicos_vencidos)],
            ['Total de Parcelas VENCIDAS', formatar_real(total_parcelas_vencidas)],
            ['', ''],  # Linha em branco
            ['Total de Parcelas PAGAS', formatar_real(total_pago_parcelas)],
            ['', ''],  # Linha em branco
            ['TOTAL VENCIDO ⚠️', formatar_real(total_geral_vencido)],
            ['TOTAL PREVISTO', formatar_real(total_geral_previsto)],
            ['TOTAL GERAL (A Pagar)', formatar_real(total_geral)]
        ]
        
        table_resumo = Table(resumo_data, colWidths=[12*cm, 5*cm])
        
        style_list = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff9800')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            # Destacar linha TOTAL VENCIDO em vermelho
            ('BACKGROUND', (0, 11), (-1, 11), colors.HexColor('#ffcdd2')),
            ('TEXTCOLOR', (0, 11), (-1, 11), colors.HexColor('#d32f2f')),
            ('FONTNAME', (0, 11), (-1, 11), 'Helvetica-Bold'),
            # Destacar linha TOTAL GERAL em laranja escuro
            ('BACKGROUND', (0, 13), (-1, 13), colors.HexColor('#ff9800')),
            ('TEXTCOLOR', (0, 13), (-1, 13), colors.whitesmoke),
            ('FONTNAME', (0, 13), (-1, 13), 'Helvetica-Bold'),
        ]
        
        table_resumo.setStyle(TableStyle(style_list))
        elements.append(table_resumo)
        
        # Construir o PDF
        doc.build(elements)
        buffer.seek(0)
        
        print(f"--- [LOG] PDF do cronograma gerado para obra {obra_id} ---")
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Cronograma_{obra.nome.replace(' ', '_')}_{date.today()}.pdf",
            mimetype='application/pdf'
        )
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] ao gerar PDF do cronograma: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT DE RELATÓRIO DO CRONOGRAMA ---


# --- ALIAS: ROTA ALTERNATIVA PARA PDF DO CRONOGRAMA (USADA PELO FRONTEND) ---
@app.route('/obras/<int:obra_id>/cronograma-financeiro/pdf', methods=['GET'])
@jwt_required()
def gerar_pdf_cronograma_financeiro_alias(obra_id):
    """Alias para rota de PDF do cronograma - usado pelo frontend"""
    return gerar_relatorio_cronograma_pdf(obra_id)

# --- NOVO ENDPOINT: BUSCAR PAGAMENTOS DE SERVIÇO PENDENTES ---
@app.route('/obras/<int:obra_id>/pagamentos-servico-pendentes', methods=['GET', 'OPTIONS'])
@jwt_required()
def get_pagamentos_servico_pendentes(obra_id):
    """
    Retorna todos os pagamentos de serviço com valor_pago < valor_total
    para exibir no Cronograma Financeiro
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        # Buscar pagamentos de serviço pendentes
        pagamentos_pendentes = db.session.query(PagamentoServico, Servico).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_total > PagamentoServico.valor_pago
        ).all()
        
        resultado = []
        for pagamento, servico in pagamentos_pendentes:
            descricao = pagamento.fornecedor or f"Pagamento - {servico.nome}"
            resultado.append({
                'id': pagamento.id,
                'servico_id': servico.id,
                'servico_nome': servico.nome,
                'descricao': descricao,
                'tipo_pagamento': 'Mão de Obra' if pagamento.tipo_pagamento == 'mao_de_obra' else 'Material',
                'valor_total': pagamento.valor_total,
                'valor_pago': pagamento.valor_pago,
                'valor_restante': pagamento.valor_total - pagamento.valor_pago,
                'data': pagamento.data.isoformat() if pagamento.data else None,
                'prioridade': pagamento.prioridade
            })
        
        return jsonify(resultado), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /pagamentos-servico-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: LISTAR LANÇAMENTOS COM SALDO PENDENTE ---
@app.route('/obras/<int:obra_id>/lancamentos-pendentes', methods=['GET'])
@jwt_required()
def listar_lancamentos_pendentes(obra_id):
    """
    Lista todos os lançamentos com saldo pendente (valor_total > valor_pago).
    Esses são os lançamentos "fantasmas" que contribuem para o KPI "Liberado p/ Pagamento"
    mas não aparecem mais no quadro de pendências (que foi removido).
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar lançamentos com saldo pendente
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).filter(
            Lancamento.valor_total > Lancamento.valor_pago
        ).order_by(Lancamento.data).all()
        
        resultado = []
        for lanc in lancamentos:
            valor_restante = lanc.valor_total - lanc.valor_pago
            resultado.append({
                'id': lanc.id,
                'tipo': lanc.tipo,
                'descricao': lanc.descricao,
                'fornecedor': lanc.fornecedor,
                'valor_total': lanc.valor_total,
                'valor_pago': lanc.valor_pago,
                'valor_restante': valor_restante,
                'data': lanc.data.isoformat() if lanc.data else None,
                'data_vencimento': lanc.data_vencimento.isoformat() if lanc.data_vencimento else None,
                'status': lanc.status,
                'prioridade': lanc.prioridade,
                'pix': lanc.pix,
                'servico_id': lanc.servico_id,
                'servico_nome': lanc.servico.nome if lanc.servico else None
            })
        
        total_pendente = sum(lanc.valor_total - lanc.valor_pago for lanc in lancamentos)
        
        print(f"--- [LOG] Encontrados {len(resultado)} lançamentos pendentes na obra {obra_id}. Total: R$ {total_pendente:.2f} ---")
        
        return jsonify({
            'lancamentos': resultado,
            'total_lancamentos': len(resultado),
            'total_pendente': round(total_pendente, 2)
        }), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: EXCLUIR LANÇAMENTO PENDENTE ---
@app.route('/obras/<int:obra_id>/lancamentos/<int:lancamento_id>/excluir-pendente', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_lancamento_pendente(obra_id, lancamento_id):
    """
    Exclui um lançamento com saldo pendente.
    Remove completamente do banco de dados.
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar o lançamento
        lancamento = Lancamento.query.filter_by(id=lancamento_id, obra_id=obra_id).first()
        if not lancamento:
            return jsonify({"erro": "Lançamento não encontrado"}), 404
        
        # Guardar info antes de excluir
        descricao = lancamento.descricao
        valor_restante = lancamento.valor_total - lancamento.valor_pago
        
        # Excluir o lançamento
        db.session.delete(lancamento)
        db.session.commit()
        
        print(f"--- [LOG] Lançamento {lancamento_id} excluído. Valor restante era: R$ {valor_restante:.2f} ---")
        
        return jsonify({
            "mensagem": "Lançamento excluído com sucesso",
            "lancamento_id": lancamento_id,
            "descricao": descricao,
            "valor_que_estava_pendente": valor_restante
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /excluir-pendente: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: EXCLUIR TODOS OS LANÇAMENTOS PENDENTES ---
@app.route('/obras/<int:obra_id>/lancamentos/excluir-todos-pendentes', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_todos_lancamentos_pendentes(obra_id):
    """
    Exclui TODOS os lançamentos pendentes de uma obra de uma vez.
    Remove completamente do banco de dados - limpa os valores "fantasmas".
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar todos os lançamentos com saldo pendente
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).filter(
            Lancamento.valor_total > Lancamento.valor_pago
        ).all()
        
        if not lancamentos:
            return jsonify({"mensagem": "Nenhum lançamento pendente encontrado"}), 200
        
        excluidos = []
        valor_total_removido = 0
        
        for lancamento in lancamentos:
            valor_restante = lancamento.valor_total - lancamento.valor_pago
            
            excluidos.append({
                'lancamento_id': lancamento.id,
                'descricao': lancamento.descricao,
                'valor_pendente_removido': valor_restante
            })
            valor_total_removido += valor_restante
            
            # Excluir do banco
            db.session.delete(lancamento)
        
        db.session.commit()
        
        print(f"--- [LOG] {len(excluidos)} lançamentos pendentes excluídos. Total removido: R$ {valor_total_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"{len(excluidos)} lançamentos pendentes excluídos com sucesso",
            "quantidade_excluida": len(excluidos),
            "valor_total_removido": round(valor_total_removido, 2),
            "lancamentos_excluidos": excluidos
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /excluir-todos-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT GLOBAL: EXCLUIR PENDENTES DE TODAS AS OBRAS ---
@app.route('/lancamentos/excluir-todos-pendentes-global', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_todos_lancamentos_pendentes_global():
    """
    Exclui TODOS os lançamentos pendentes de TODAS as obras acessíveis pelo usuário.
    
    Administrador: Limpa todas as obras do sistema
    Master: Limpa apenas as obras que tem acesso
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        
        # Determinar quais obras o usuário pode acessar
        if current_user.role == 'administrador':
            obras = Obra.query.all()
        else:
            obras = current_user.obras_permitidas
        
        if not obras:
            return jsonify({"mensagem": "Nenhuma obra acessível encontrada"}), 200
        
        resultado_por_obra = []
        total_geral_excluido = 0
        total_geral_removido = 0.0
        
        for obra in obras:
            # Buscar lançamentos pendentes desta obra
            lancamentos = Lancamento.query.filter_by(obra_id=obra.id).filter(
                Lancamento.valor_total > Lancamento.valor_pago
            ).all()
            
            if lancamentos:
                excluidos = []
                valor_total_obra = 0
                
                for lancamento in lancamentos:
                    valor_restante = lancamento.valor_total - lancamento.valor_pago
                    
                    excluidos.append({
                        'lancamento_id': lancamento.id,
                        'descricao': lancamento.descricao,
                        'valor_pendente': valor_restante
                    })
                    valor_total_obra += valor_restante
                    
                    # Excluir do banco
                    db.session.delete(lancamento)
                
                total_geral_excluido += len(excluidos)
                total_geral_removido += valor_total_obra
                
                resultado_por_obra.append({
                    'obra_id': obra.id,
                    'obra_nome': obra.nome,
                    'quantidade_excluida': len(excluidos),
                    'valor_removido': round(valor_total_obra, 2),
                    'lancamentos': excluidos
                })
        
        db.session.commit()
        
        print(f"--- [LOG] LIMPEZA GLOBAL: {total_geral_excluido} lançamentos excluídos em {len(resultado_por_obra)} obras. Total: R$ {total_geral_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"Limpeza concluída! {total_geral_excluido} lançamentos excluídos em {len(resultado_por_obra)} obras",
            "total_obras_processadas": len(obras),
            "obras_com_pendencias": len(resultado_por_obra),
            "total_lancamentos_excluidos": total_geral_excluido,
            "valor_total_removido": round(total_geral_removido, 2),
            "detalhes_por_obra": resultado_por_obra
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /excluir-todos-pendentes-global: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: EXCLUIR PAGAMENTOS DE SERVIÇO PENDENTES (UMA OBRA) ---
@app.route('/obras/<int:obra_id>/pagamentos-servico/excluir-todos-pendentes', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_pagamentos_servico_pendentes(obra_id):
    """
    Exclui TODOS os pagamentos de serviço com saldo pendente de uma obra.
    Remove completamente do banco de dados.
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamentos de serviço com saldo pendente
        pagamentos = db.session.query(PagamentoServico).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_total > PagamentoServico.valor_pago
        ).all()
        
        if not pagamentos:
            return jsonify({"mensagem": "Nenhum pagamento de serviço pendente encontrado"}), 200
        
        excluidos = []
        valor_total_removido = 0
        
        for pagamento in pagamentos:
            valor_restante = pagamento.valor_total - pagamento.valor_pago
            
            # Buscar nome do serviço
            servico = Servico.query.get(pagamento.servico_id)
            descricao = pagamento.fornecedor or (servico.nome if servico else f"Pagamento ID {pagamento.id}")
            
            excluidos.append({
                'pagamento_id': pagamento.id,
                'servico_id': pagamento.servico_id,
                'descricao': descricao,
                'tipo': pagamento.tipo_pagamento,
                'valor_pendente_removido': valor_restante
            })
            valor_total_removido += valor_restante
            
            # Excluir do banco
            db.session.delete(pagamento)
        
        db.session.commit()
        
        print(f"--- [LOG] {len(excluidos)} pagamentos de serviço pendentes excluídos da obra {obra_id}. Total: R$ {valor_total_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"{len(excluidos)} pagamentos de serviço pendentes excluídos com sucesso",
            "quantidade_excluida": len(excluidos),
            "valor_total_removido": round(valor_total_removido, 2),
            "pagamentos_excluidos": excluidos
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /pagamentos-servico/excluir-todos-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT GLOBAL: EXCLUIR PAGAMENTOS DE SERVIÇO PENDENTES (TODAS AS OBRAS) ---
@app.route('/pagamentos-servico/excluir-todos-pendentes-global', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_pagamentos_servico_pendentes_global():
    """
    Exclui TODOS os pagamentos de serviço com saldo pendente de TODAS as obras.
    
    Administrador: Limpa todas as obras do sistema
    Master: Limpa apenas as obras que tem acesso
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        
        # Determinar quais obras o usuário pode acessar
        if current_user.role == 'administrador':
            obras = Obra.query.all()
        else:
            obras = current_user.obras_permitidas
        
        if not obras:
            return jsonify({"mensagem": "Nenhuma obra acessível encontrada"}), 200
        
        resultado_por_obra = []
        total_geral_excluido = 0
        total_geral_removido = 0.0
        
        for obra in obras:
            # Buscar pagamentos de serviço pendentes desta obra
            pagamentos = db.session.query(PagamentoServico).join(
                Servico, PagamentoServico.servico_id == Servico.id
            ).filter(
                Servico.obra_id == obra.id,
                PagamentoServico.valor_total > PagamentoServico.valor_pago
            ).all()
            
            if pagamentos:
                excluidos = []
                valor_total_obra = 0
                
                for pagamento in pagamentos:
                    valor_restante = pagamento.valor_total - pagamento.valor_pago
                    
                    # Buscar nome do serviço
                    servico = Servico.query.get(pagamento.servico_id)
                    descricao = pagamento.fornecedor or (servico.nome if servico else f"Pagamento ID {pagamento.id}")
                    
                    excluidos.append({
                        'pagamento_id': pagamento.id,
                        'descricao': descricao,
                        'tipo': pagamento.tipo_pagamento,
                        'valor_pendente': valor_restante
                    })
                    valor_total_obra += valor_restante
                    
                    # Excluir do banco
                    db.session.delete(pagamento)
                
                total_geral_excluido += len(excluidos)
                total_geral_removido += valor_total_obra
                
                resultado_por_obra.append({
                    'obra_id': obra.id,
                    'obra_nome': obra.nome,
                    'quantidade_excluida': len(excluidos),
                    'valor_removido': round(valor_total_obra, 2),
                    'pagamentos': excluidos
                })
        
        db.session.commit()
        
        print(f"--- [LOG] LIMPEZA GLOBAL PAGAMENTOS: {total_geral_excluido} pagamentos de serviço excluídos em {len(resultado_por_obra)} obras. Total: R$ {total_geral_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"Limpeza de pagamentos concluída! {total_geral_excluido} pagamentos excluídos em {len(resultado_por_obra)} obras",
            "total_obras_processadas": len(obras),
            "obras_com_pendencias": len(resultado_por_obra),
            "total_pagamentos_excluidos": total_geral_excluido,
            "valor_total_removido": round(total_geral_removido, 2),
            "detalhes_por_obra": resultado_por_obra
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /pagamentos-servico/excluir-todos-pendentes-global: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: LIMPEZA TOTAL (LANÇAMENTOS + PAGAMENTOS DE SERVIÇO) ---
@app.route('/limpar-tudo-pendente-global', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def limpar_tudo_pendente_global():
    """
    SUPER LIMPEZA: Exclui TODOS os lançamentos E pagamentos de serviço pendentes de TODAS as obras.
    
    Este é o endpoint mais poderoso - limpa TUDO que contribui para "Liberado p/ Pagamento":
    - Lançamentos com saldo pendente
    - Pagamentos de Serviço com saldo pendente
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        
        # Determinar quais obras o usuário pode acessar
        if current_user.role == 'administrador':
            obras = Obra.query.all()
        else:
            obras = current_user.obras_permitidas
        
        if not obras:
            return jsonify({"mensagem": "Nenhuma obra acessível encontrada"}), 200
        
        resultado_por_obra = []
        total_lancamentos_excluidos = 0
        total_pagamentos_excluidos = 0
        total_valor_removido = 0.0
        
        for obra in obras:
            lancamentos_obra = []
            pagamentos_obra = []
            valor_obra = 0
            
            # 1. Lançamentos pendentes
            lancamentos = Lancamento.query.filter_by(obra_id=obra.id).filter(
                Lancamento.valor_total > Lancamento.valor_pago
            ).all()
            
            for lancamento in lancamentos:
                valor_restante = lancamento.valor_total - lancamento.valor_pago
                lancamentos_obra.append({
                    'id': lancamento.id,
                    'tipo': 'Lançamento',
                    'descricao': lancamento.descricao,
                    'valor': valor_restante
                })
                valor_obra += valor_restante
                db.session.delete(lancamento)
            
            # 2. Pagamentos de Serviço pendentes
            pagamentos = db.session.query(PagamentoServico).join(
                Servico, PagamentoServico.servico_id == Servico.id
            ).filter(
                Servico.obra_id == obra.id,
                PagamentoServico.valor_total > PagamentoServico.valor_pago
            ).all()
            
            for pagamento in pagamentos:
                valor_restante = pagamento.valor_total - pagamento.valor_pago
                
                # Buscar nome do serviço
                servico = Servico.query.get(pagamento.servico_id)
                descricao = pagamento.fornecedor or (servico.nome if servico else f"Pagamento ID {pagamento.id}")
                
                pagamentos_obra.append({
                    'id': pagamento.id,
                    'tipo': 'Pagamento de Serviço',
                    'descricao': descricao,
                    'valor': valor_restante
                })
                valor_obra += valor_restante
                db.session.delete(pagamento)
            
            if lancamentos_obra or pagamentos_obra:
                total_lancamentos_excluidos += len(lancamentos_obra)
                total_pagamentos_excluidos += len(pagamentos_obra)
                total_valor_removido += valor_obra
                
                resultado_por_obra.append({
                    'obra_id': obra.id,
                    'obra_nome': obra.nome,
                    'lancamentos_excluidos': len(lancamentos_obra),
                    'pagamentos_excluidos': len(pagamentos_obra),
                    'total_excluido': len(lancamentos_obra) + len(pagamentos_obra),
                    'valor_removido': round(valor_obra, 2),
                    'detalhes': {
                        'lancamentos': lancamentos_obra,
                        'pagamentos': pagamentos_obra
                    }
                })
        
        db.session.commit()
        
        print(f"--- [LOG] SUPER LIMPEZA: {total_lancamentos_excluidos} lançamentos + {total_pagamentos_excluidos} pagamentos excluídos. Total: R$ {total_valor_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"SUPER LIMPEZA concluída! {total_lancamentos_excluidos + total_pagamentos_excluidos} itens excluídos em {len(resultado_por_obra)} obras",
            "total_obras_processadas": len(obras),
            "obras_com_pendencias": len(resultado_por_obra),
            "total_lancamentos_excluidos": total_lancamentos_excluidos,
            "total_pagamentos_excluidos": total_pagamentos_excluidos,
            "total_itens_excluidos": total_lancamentos_excluidos + total_pagamentos_excluidos,
            "valor_total_removido": round(total_valor_removido, 2),
            "detalhes_por_obra": resultado_por_obra
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /limpar-tudo-pendente-global: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---

# --- MUDANÇA 3: NOVO ENDPOINT - INSERIR PAGAMENTO ---
@app.route('/obras/<int:obra_id>/inserir-pagamento', methods=['POST', 'OPTIONS'])
@jwt_required()
def inserir_pagamento(obra_id):
    """
    Novo endpoint para inserir pagamentos com vínculo opcional a serviços.
    Permite escolher tipo (Material/Mão de Obra) e status (Pago/A Pagar).
    Atualiza automaticamente a % de conclusão do serviço vinculado.
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/inserir-pagamento (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json
        
        # Campos obrigatórios
        descricao = dados.get('descricao')
        valor_total = float(dados.get('valor', 0))
        tipo = dados.get('tipo')  # 'Material', 'Mão de Obra', ou 'Serviço'
        status = dados.get('status', 'A Pagar')  # 'Pago' ou 'A Pagar'
        data = date.fromisoformat(dados.get('data'))
        
        # Campos opcionais
        servico_id = dados.get('servico_id')
        fornecedor = dados.get('fornecedor')
        data_vencimento = dados.get('data_vencimento')
        pix = dados.get('pix')
        prioridade = int(dados.get('prioridade', 0))
        
        # Calcular valor_pago baseado no status
        valor_pago = valor_total if status == 'Pago' else 0.0
        
        # ===== LÓGICA REFATORADA: STATUS "PAGO" vs "A PAGAR" =====
        
        # CASO 1: STATUS "PAGO" COM SERVIÇO VINCULADO
        if servico_id and status == 'Pago':
            servico = Servico.query.get_or_404(servico_id)
            
            # Determinar tipo_pagamento para PagamentoServico
            if tipo == 'Mão de Obra':
                tipo_pagamento = 'mao_de_obra'
            elif tipo == 'Material':
                tipo_pagamento = 'material'
            else:
                tipo_pagamento = 'material'  # default
            
            novo_pagamento = PagamentoServico(
                servico_id=servico_id,
                tipo_pagamento=tipo_pagamento,
                valor_total=valor_total,
                valor_pago=valor_pago,
                data=data,
                data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else None,
                status=status,
                prioridade=prioridade,
                fornecedor=fornecedor
            )
            db.session.add(novo_pagamento)
            
            # Recalcular percentual do serviço
            db.session.flush()  # Garante que o pagamento seja salvo antes do cálculo
            
            pagamentos = PagamentoServico.query.filter_by(servico_id=servico_id).all()
            
            # Separar por tipo
            pagamentos_mao_de_obra = [p for p in pagamentos if p.tipo_pagamento == 'mao_de_obra']
            pagamentos_material = [p for p in pagamentos if p.tipo_pagamento == 'material']
            
            # Calcular percentuais
            if servico.valor_global_mao_de_obra > 0:
                total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
            
            if servico.valor_global_material > 0:
                total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
            
            db.session.commit()
            print(f"--- [LOG] ✅ PagamentoServico PAGO criado e vinculado ao serviço {servico_id} ---")
            return jsonify(novo_pagamento.to_dict()), 201
        
        # CASO 2: STATUS "A PAGAR" COM SERVIÇO VINCULADO
        elif servico_id and status == 'A Pagar':
            servico = Servico.query.get_or_404(servico_id)
            
            print(f"--- [DEBUG] Criando PagamentoFuturo vinculado ao serviço {servico_id} ---")
            novo_futuro = PagamentoFuturo(
                obra_id=obra_id,
                descricao=f"{descricao} (Serviço: {servico.nome})",
                valor=valor_total,
                data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else data,
                fornecedor=fornecedor,
                pix=pix,
                observacoes=f"Vinculado ao serviço {servico.nome}",
                status='Previsto',
                servico_id=servico_id,  # ✅ NOVO: Vincula ao serviço
                tipo=tipo  # ✅ NOVO: Armazena o tipo (Mão de Obra / Material)
            )
            db.session.add(novo_futuro)
            db.session.commit()
            print(f"--- [LOG] ✅ PagamentoFuturo criado vinculado ao serviço {servico_id} (Cronograma) ---")
            return jsonify(novo_futuro.to_dict()), 201
        
        # CASO 3: STATUS "A PAGAR" SEM SERVIÇO
        elif status == 'A Pagar':
            print(f"--- [DEBUG] Criando PagamentoFuturo sem vínculo (status='A Pagar') ---")
            novo_futuro = PagamentoFuturo(
                obra_id=obra_id,
                descricao=descricao,
                valor=valor_total,
                data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else data,
                fornecedor=fornecedor,
                pix=pix,
                observacoes=f"Tipo: {tipo}",
                status='Previsto',
                servico_id=None,
                tipo=tipo  # ✅ Armazena o tipo
            )
            db.session.add(novo_futuro)
            db.session.commit()
            print(f"--- [LOG] ✅ PagamentoFuturo criado sem vínculo (Cronograma Financeiro) ---")
            return jsonify(novo_futuro.to_dict()), 201
        
        # CASO 4: STATUS "PAGO" SEM SERVIÇO
        else:
            # Criar Lançamento normal (status='Pago', vai pro histórico)
            print(f"--- [DEBUG] Criando Lancamento (status='Pago') ---")
            novo_lancamento = Lancamento(
                obra_id=obra_id,
                tipo=tipo,
                descricao=descricao,
                valor_total=valor_total,
                valor_pago=valor_pago,
                data=data,
                data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else None,
                status=status,
                pix=pix,
                prioridade=prioridade,
                fornecedor=fornecedor
            )
            db.session.add(novo_lancamento)
            db.session.commit()
            print(f"--- [LOG] ✅ Lançamento criado: ID {novo_lancamento.id} (Histórico) ---")
            return jsonify(novo_lancamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/inserir-pagamento: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DO ENDPOINT INSERIR PAGAMENTO ---


# --- MUDANÇA 5: NOVO ENDPOINT - MARCAR MÚLTIPLOS COMO PAGO ---
@app.route('/obras/<int:obra_id>/cronograma/marcar-multiplos-pagos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def marcar_multiplos_como_pago(obra_id):
    """
    Marca múltiplos pagamentos (futuros e parcelas) como pagos de uma vez.
    Permite anexar comprovante/nota fiscal para cada item.
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/cronograma/marcar-multiplos-pagos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json
        itens_selecionados = dados.get('itens', [])  # Lista de {tipo: 'futuro'|'parcela', id: X}
        data_pagamento = dados.get('data_pagamento')
        
        if data_pagamento:
            data_pagamento = date.fromisoformat(data_pagamento)
        else:
            data_pagamento = date.today()
        
        resultados = []
        
        for item in itens_selecionados:
            tipo_item = item.get('tipo')
            item_id = item.get('id')
            
            try:
                if tipo_item == 'futuro':
                    # ===== LÓGICA CORRIGIDA: Verificar se tem vínculo com serviço =====
                    pagamento = PagamentoFuturo.query.get(item_id)
                    if pagamento and pagamento.obra_id == obra_id:
                        
                        # CASO 1: Pagamento vinculado a SERVIÇO
                        if pagamento.servico_id:
                            servico = Servico.query.get(pagamento.servico_id)
                            if servico:
                                # Determinar tipo_pagamento
                                if pagamento.tipo == 'Mão de Obra':
                                    tipo_pagamento = 'mao_de_obra'
                                elif pagamento.tipo == 'Material':
                                    tipo_pagamento = 'material'
                                else:
                                    tipo_pagamento = 'material'  # default
                                
                                # Criar PagamentoServico
                                novo_pag_servico = PagamentoServico(
                                    servico_id=pagamento.servico_id,
                                    tipo_pagamento=tipo_pagamento,
                                    valor_total=pagamento.valor,
                                    valor_pago=pagamento.valor,  # Marcar como totalmente pago
                                    data=data_pagamento,
                                    data_vencimento=pagamento.data_vencimento,
                                    status='Pago',
                                    prioridade=0,
                                    fornecedor=pagamento.fornecedor
                                )
                                db.session.add(novo_pag_servico)
                                db.session.flush()
                                
                                # Recalcular percentual do serviço
                                pagamentos_serv = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                                pagamentos_mao_de_obra = [p for p in pagamentos_serv if p.tipo_pagamento == 'mao_de_obra']
                                pagamentos_material = [p for p in pagamentos_serv if p.tipo_pagamento == 'material']
                                
                                if servico.valor_global_mao_de_obra > 0:
                                    total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                                    servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                                
                                if servico.valor_global_material > 0:
                                    total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                                    servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                                
                                # DELETE o PagamentoFuturo
                                db.session.delete(pagamento)
                                
                                resultados.append({
                                    "tipo": "futuro",
                                    "id": item_id,
                                    "status": "success",
                                    "mensagem": f"Pagamento '{pagamento.descricao}' vinculado ao serviço '{servico.nome}' e marcado como pago",
                                    "pagamento_servico_id": novo_pag_servico.id
                                })
                            else:
                                resultados.append({
                                    "tipo": "futuro",
                                    "id": item_id,
                                    "status": "error",
                                    "mensagem": "Serviço vinculado não encontrado"
                                })
                        
                        # CASO 2: Pagamento SEM vínculo com serviço
                        else:
                            # Criar Lançamento no Histórico
                            novo_lancamento = Lancamento(
                                obra_id=pagamento.obra_id,
                                tipo=pagamento.tipo or 'Despesa',
                                descricao=pagamento.descricao,
                                valor_total=pagamento.valor,
                                valor_pago=pagamento.valor,
                                data=data_pagamento,
                                data_vencimento=pagamento.data_vencimento,
                                status='Pago',
                                pix=pagamento.pix,
                                prioridade=0,
                                fornecedor=pagamento.fornecedor,
                                servico_id=None
                            )
                            db.session.add(novo_lancamento)
                            
                            # DELETE o PagamentoFuturo
                            db.session.delete(pagamento)
                            
                            resultados.append({
                                "tipo": "futuro",
                                "id": item_id,
                                "status": "success",
                                "mensagem": f"Pagamento futuro '{pagamento.descricao}' movido para o histórico",
                                "lancamento_id": novo_lancamento.id
                            })
                    else:
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento futuro não encontrado"
                        })
                
                elif tipo_item == 'parcela':
                    # Marcar parcela como paga
                    parcela = ParcelaIndividual.query.get(item_id)
                    if parcela:
                        pag_parcelado = PagamentoParcelado.query.get(parcela.pagamento_parcelado_id)
                        if pag_parcelado and pag_parcelado.obra_id == obra_id:
                            parcela.status = 'Pago'
                            parcela.data_pagamento = data_pagamento
                            
                            # Atualizar contador de parcelas pagas
                            parcelas_pagas = ParcelaIndividual.query.filter_by(
                                pagamento_parcelado_id=pag_parcelado.id,
                                status='Pago'
                            ).count()
                            pag_parcelado.parcelas_pagas = parcelas_pagas
                            
                            # Se todas as parcelas foram pagas, marcar como Concluído
                            if parcelas_pagas >= pag_parcelado.numero_parcelas:
                                pag_parcelado.status = 'Concluído'
                            
                            resultados.append({
                                "tipo": "parcela",
                                "id": item_id,
                                "status": "success",
                                "mensagem": f"Parcela {parcela.numero_parcela} marcada como paga"
                            })
                        else:
                            resultados.append({
                                "tipo": "parcela",
                                "id": item_id,
                                "status": "error",
                                "mensagem": "Pagamento parcelado não encontrado"
                            })
                    else:
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Parcela não encontrada"
                        })
                
                elif tipo_item == 'servico':
                    # NOVO: Marcar pagamento de serviço como totalmente pago
                    pagamento_servico = PagamentoServico.query.get(item_id)
                    if pagamento_servico:
                        servico = Servico.query.get(pagamento_servico.servico_id)
                        if servico and servico.obra_id == obra_id:
                            # Marcar como totalmente pago
                            pagamento_servico.valor_pago = pagamento_servico.valor_total
                            
                            # Atualizar percentuais do serviço
                            pagamentos = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                            
                            # Separar por tipo
                            pagamentos_mao_de_obra = [p for p in pagamentos if p.tipo_pagamento == 'mao_de_obra']
                            pagamentos_material = [p for p in pagamentos if p.tipo_pagamento == 'material']
                            
                            # Calcular percentuais
                            if servico.valor_global_mao_de_obra > 0:
                                total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                                servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                            
                            if servico.valor_global_material > 0:
                                total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                                servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                            
                            resultados.append({
                                "tipo": "servico",
                                "id": item_id,
                                "status": "success",
                                "mensagem": f"Pagamento do serviço '{servico.nome}' marcado como pago"
                            })
                        else:
                            resultados.append({
                                "tipo": "servico",
                                "id": item_id,
                                "status": "error",
                                "mensagem": "Serviço não encontrado ou acesso negado"
                            })
                    else:
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento de serviço não encontrado"
                        })
            
            except Exception as e:
                resultados.append({
                    "tipo": tipo_item,
                    "id": item_id,
                    "status": "error",
                    "mensagem": str(e)
                })
        
        db.session.commit()
        print(f"--- [LOG] {len([r for r in resultados if r['status'] == 'success'])} itens marcados como pagos ---")
        
        return jsonify({
            "mensagem": "Processamento concluído",
            "resultados": resultados
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] marcar-multiplos-pagos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DO ENDPOINT MARCAR MÚLTIPLOS COMO PAGO ---

# ========================================
# ROTAS DO DIÁRIO DE OBRAS
# ========================================

@app.route('/obras/<int:obra_id>/diario', methods=['GET'])
@jwt_required()
def listar_diario_obra(obra_id):
    """Lista todas as entradas do diário de uma obra"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        entradas = DiarioObra.query.filter_by(obra_id=obra_id).order_by(DiarioObra.data.desc()).all()
        
        return jsonify({
            'entradas': [entrada.to_dict() for entrada in entradas]
        }), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET /obras/{obra_id}/diario: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/obras/<int:obra_id>/diario', methods=['POST'])
@jwt_required()
def criar_entrada_diario(obra_id):
    """Cria uma nova entrada no diário de obras"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        
        # Criar entrada
        entrada = DiarioObra(
            obra_id=obra_id,
            data=datetime.strptime(data.get('data'), '%Y-%m-%d').date() if data.get('data') else datetime.utcnow().date(),
            titulo=data.get('titulo'),
            descricao=data.get('descricao'),
            clima=data.get('clima'),
            temperatura=data.get('temperatura'),
            equipe_presente=data.get('equipe_presente'),
            atividades_realizadas=data.get('atividades_realizadas'),
            materiais_utilizados=data.get('materiais_utilizados'),
            equipamentos_utilizados=data.get('equipamentos_utilizados'),
            observacoes=data.get('observacoes'),
            criado_por=int(get_jwt_identity())
        )
        
        db.session.add(entrada)
        db.session.flush()  # Para obter o ID
        
        # Processar imagens (base64)
        if 'imagens' in data and isinstance(data['imagens'], list):
            for idx, img_data in enumerate(data['imagens']):
                imagem = DiarioImagem(
                    diario_id=entrada.id,
                    arquivo_nome=img_data.get('nome', f'imagem_{idx+1}.jpg'),
                    arquivo_base64=img_data.get('base64', ''),
                    legenda=img_data.get('legenda', ''),
                    ordem=idx
                )
                db.session.add(imagem)
        
        db.session.commit()
        
        print(f"--- [LOG] Entrada de diário criada: ID {entrada.id} na obra {obra_id} ---")
        return jsonify({
            'mensagem': 'Entrada criada com sucesso',
            'entrada': entrada.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] POST /obras/{obra_id}/diario: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/diario/<int:entrada_id>', methods=['GET'])
@jwt_required()
def obter_entrada_diario(entrada_id):
    """Obtém uma entrada específica do diário"""
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        return jsonify(entrada.to_dict()), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET /diario/{entrada_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/diario/<int:entrada_id>', methods=['PUT'])
@jwt_required()
def atualizar_entrada_diario(entrada_id):
    """Atualiza uma entrada do diário"""
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        
        # Atualizar campos
        if 'data' in data:
            entrada.data = datetime.strptime(data['data'], '%Y-%m-%d').date()
        if 'titulo' in data:
            entrada.titulo = data['titulo']
        if 'descricao' in data:
            entrada.descricao = data['descricao']
        if 'clima' in data:
            entrada.clima = data['clima']
        if 'temperatura' in data:
            entrada.temperatura = data['temperatura']
        if 'equipe_presente' in data:
            entrada.equipe_presente = data['equipe_presente']
        if 'atividades_realizadas' in data:
            entrada.atividades_realizadas = data['atividades_realizadas']
        if 'materiais_utilizados' in data:
            entrada.materiais_utilizados = data['materiais_utilizados']
        if 'equipamentos_utilizados' in data:
            entrada.equipamentos_utilizados = data['equipamentos_utilizados']
        if 'observacoes' in data:
            entrada.observacoes = data['observacoes']
        
        db.session.commit()
        
        print(f"--- [LOG] Entrada {entrada_id} atualizada ---")
        return jsonify({
            'mensagem': 'Entrada atualizada com sucesso',
            'entrada': entrada.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] PUT /diario/{entrada_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/diario/<int:entrada_id>', methods=['DELETE'])
@jwt_required()
def deletar_entrada_diario(entrada_id):
    """Deleta uma entrada do diário"""
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        db.session.delete(entrada)
        db.session.commit()
        
        print(f"--- [LOG] Entrada {entrada_id} deletada ---")
        return jsonify({'mensagem': 'Entrada deletada com sucesso'}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] DELETE /diario/{entrada_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/diario/<int:entrada_id>/imagens', methods=['POST'])
@jwt_required()
def adicionar_imagem_diario(entrada_id):
    """Adiciona uma imagem a uma entrada existente"""
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        
        # Obter próxima ordem
        max_ordem = db.session.query(func.max(DiarioImagem.ordem)).filter_by(diario_id=entrada_id).scalar() or -1
        
        imagem = DiarioImagem(
            diario_id=entrada_id,
            arquivo_nome=data.get('nome', 'imagem.jpg'),
            arquivo_base64=data.get('base64', ''),
            legenda=data.get('legenda', ''),
            ordem=max_ordem + 1
        )
        
        db.session.add(imagem)
        db.session.commit()
        
        print(f"--- [LOG] Imagem adicionada à entrada {entrada_id} ---")
        return jsonify({
            'mensagem': 'Imagem adicionada com sucesso',
            'imagem': imagem.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] POST /diario/{entrada_id}/imagens: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/diario/imagens/<int:imagem_id>', methods=['DELETE'])
@jwt_required()
def deletar_imagem_diario(imagem_id):
    """Deleta uma imagem do diário"""
    try:
        imagem = db.session.get(DiarioImagem, imagem_id)
        if not imagem:
            return jsonify({"erro": "Imagem não encontrada"}), 404
        
        entrada = db.session.get(DiarioObra, imagem.diario_id)
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        db.session.delete(imagem)
        db.session.commit()
        
        print(f"--- [LOG] Imagem {imagem_id} deletada ---")
        return jsonify({'mensagem': 'Imagem deletada com sucesso'}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] DELETE /diario/imagens/{imagem_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/obras/<int:obra_id>/diario/relatorio', methods=['GET'])
@jwt_required()
def gerar_relatorio_diario(obra_id):
    """Gera relatório PDF do diário de obras"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        # Filtros
        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim')
        
        query = DiarioObra.query.filter_by(obra_id=obra_id)
        
        if data_inicio:
            query = query.filter(DiarioObra.data >= datetime.strptime(data_inicio, '%Y-%m-%d').date())
        if data_fim:
            query = query.filter(DiarioObra.data <= datetime.strptime(data_fim, '%Y-%m-%d').date())
        
        entradas = query.order_by(DiarioObra.data.asc()).all()
        
        # Criar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
        
        story = []
        styles = getSampleStyleSheet()
        
        # Título
        titulo = Paragraph(f"<b>Diário de Obras - {obra.nome}</b>", styles['Title'])
        story.append(titulo)
        story.append(Spacer(1, 0.5*cm))
        
        # Informações do relatório
        info_data = [
            ['Relatório gerado em:', datetime.now().strftime('%d/%m/%Y %H:%M')],
            ['Obra:', obra.nome],
            ['Cliente:', obra.cliente or 'N/A'],
        ]
        
        if data_inicio or data_fim:
            periodo = f"{data_inicio or 'Início'} até {data_fim or 'Hoje'}"
            info_data.append(['Período:', periodo])
        
        info_table = Table(info_data, colWidths=[5*cm, 12*cm])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e2e8f0')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))
        
        story.append(info_table)
        story.append(Spacer(1, 1*cm))
        
        # Entradas
        for entrada in entradas:
            # Data e título
            story.append(Paragraph(f"<b>{entrada.data.strftime('%d/%m/%Y')}</b> - {entrada.titulo}", styles['Heading2']))
            
            # Clima
            if entrada.clima or entrada.temperatura:
                clima_info = []
                if entrada.clima:
                    clima_info.append(f"Clima: {entrada.clima}")
                if entrada.temperatura:
                    clima_info.append(f"Temperatura: {entrada.temperatura}")
                story.append(Paragraph(" | ".join(clima_info), styles['Normal']))
                story.append(Spacer(1, 0.2*cm))
            
            # Descrição
            if entrada.descricao:
                story.append(Paragraph("<b>Descrição:</b>", styles['Normal']))
                story.append(Paragraph(entrada.descricao, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))
            
            # Atividades
            if entrada.atividades_realizadas:
                story.append(Paragraph("<b>Atividades Realizadas:</b>", styles['Normal']))
                story.append(Paragraph(entrada.atividades_realizadas, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))
            
            # Equipe
            if entrada.equipe_presente:
                story.append(Paragraph("<b>Equipe Presente:</b>", styles['Normal']))
                story.append(Paragraph(entrada.equipe_presente, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))
            
            # Materiais
            if entrada.materiais_utilizados:
                story.append(Paragraph("<b>Materiais Utilizados:</b>", styles['Normal']))
                story.append(Paragraph(entrada.materiais_utilizados, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))
            
            # Observações
            if entrada.observacoes:
                story.append(Paragraph("<b>Observações:</b>", styles['Normal']))
                story.append(Paragraph(entrada.observacoes, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))
            
            # Imagens
            if entrada.imagens:
                story.append(Paragraph(f"<b>Imagens:</b> {len(entrada.imagens)} foto(s)", styles['Normal']))
                story.append(Spacer(1, 0.3*cm))
                
                for img_obj in entrada.imagens:
                    try:
                        # Decodificar base64
                        img_data = base64.b64decode(img_obj.arquivo_base64)
                        img_buffer = io.BytesIO(img_data)
                        
                        # Criar objeto Image do ReportLab
                        img = Image(img_buffer)
                        
                        # Ajustar tamanho (largura máxima: 15cm, altura proporcional)
                        max_width = 15 * cm
                        max_height = 12 * cm
                        
                        # Calcular dimensões mantendo proporção
                        aspect = img.imageHeight / img.imageWidth
                        if img.imageWidth > max_width:
                            img.drawWidth = max_width
                            img.drawHeight = max_width * aspect
                        else:
                            img.drawWidth = img.imageWidth
                            img.drawHeight = img.imageHeight
                        
                        # Se altura ainda for muito grande, ajustar pela altura
                        if img.drawHeight > max_height:
                            img.drawHeight = max_height
                            img.drawWidth = max_height / aspect
                        
                        story.append(img)
                        
                        # Legenda/nome do arquivo
                        if img_obj.arquivo_nome:
                            story.append(Paragraph(f"<i>{img_obj.arquivo_nome}</i>", styles['Normal']))
                        
                        story.append(Spacer(1, 0.3*cm))
                        
                    except Exception as img_error:
                        print(f"--- [ERRO] Erro ao processar imagem {img_obj.id}: {str(img_error)} ---")
                        story.append(Paragraph(f"<i>[Erro ao carregar imagem: {img_obj.arquivo_nome}]</i>", styles['Normal']))
                        story.append(Spacer(1, 0.3*cm))

            
            # Separador
            story.append(Spacer(1, 0.5*cm))
            story.append(Paragraph("_" * 100, styles['Normal']))
            story.append(Spacer(1, 0.5*cm))
        
        # Gerar PDF
        doc.build(story)
        buffer.seek(0)
        
        print(f"--- [LOG] Relatório do diário gerado para obra {obra_id} ---")
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'diario_obra_{obra.nome}_{datetime.now().strftime("%Y%m%d")}.pdf'
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET /obras/{obra_id}/diario/relatorio: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- MIGRAÇÃO DE DADOS ---
@app.route('/admin/migrar-lancamentos-para-futuros/<int:obra_id>', methods=['POST'])
@jwt_required()
def migrar_lancamentos_para_futuros(obra_id):
    """
    Converte Lançamentos com status='A Pagar' em PagamentoFuturo.
    Isso faz os pagamentos antigos aparecerem no Cronograma Financeiro.
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        print(f"--- [DEBUG MIGRAÇÃO] Buscando Lançamentos com status='A Pagar' na obra {obra_id} ---")
        
        # Buscar todos os Lançamentos com status='A Pagar'
        lancamentos_a_pagar = Lancamento.query.filter_by(
            obra_id=obra_id,
            status='A Pagar',
            servico_id=None  # Apenas lançamentos gerais, não vinculados a serviço
        ).all()
        
        print(f"--- [DEBUG MIGRAÇÃO] Encontrados {len(lancamentos_a_pagar)} lançamentos para migrar ---")
        
        if not lancamentos_a_pagar:
            return jsonify({"mensagem": "Nenhum lançamento 'A Pagar' encontrado"}), 200
        
        migrados = []
        for lanc in lancamentos_a_pagar:
            print(f"--- [DEBUG MIGRAÇÃO] Migrando: {lanc.descricao}, Valor: R$ {lanc.valor_total:.2f} ---")
            
            # Criar PagamentoFuturo com TODOS os campos
            novo_futuro = PagamentoFuturo(
                obra_id=lanc.obra_id,
                descricao=lanc.descricao,
                valor=lanc.valor_total - lanc.valor_pago,  # Saldo pendente
                data_vencimento=lanc.data_vencimento or lanc.data,
                fornecedor=lanc.fornecedor,
                pix=lanc.pix,  # Copiar PIX
                observacoes=f"Migrado de Lançamento ID {lanc.id}",
                status='Previsto'
            )
            db.session.add(novo_futuro)
            db.session.flush()  # Para obter o ID
            
            print(f"--- [DEBUG MIGRAÇÃO] ✅ Criado PagamentoFuturo ID {novo_futuro.id} ---")
            
            # Deletar o Lançamento antigo
            db.session.delete(lanc)
            
            migrados.append({
                "lancamento_id": lanc.id,
                "descricao": lanc.descricao,
                "valor": lanc.valor_total - lanc.valor_pago,
                "novo_pagamento_futuro_id": novo_futuro.id
            })
        
        db.session.commit()
        
        print(f"--- [LOG] ✅ {len(migrados)} lançamentos migrados para PagamentoFuturo na obra {obra_id} ---")
        return jsonify({
            "mensagem": f"{len(migrados)} lançamentos migrados com sucesso",
            "migrados": migrados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] POST /admin/migrar-lancamentos-para-futuros/{obra_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- FIM DAS ROTAS DO DIÁRIO DE OBRAS ---

# ==============================================================================
# ROTA TEMPORÁRIA PARA MIGRAÇÃO DE PAGAMENTOS ANTIGOS
# ==============================================================================
@app.route('/admin/migrar-pagamentos-antigos', methods=['POST', 'OPTIONS'])
def migrar_pagamentos_antigos():
    """
    ROTA TEMPORÁRIA: Migra pagamentos com status 'Pago' do cronograma para o histórico.
    
    Esta rota deve ser executada UMA VEZ após o deploy da correção.
    Depois de executar, você pode remover esta rota do código.
    """
    # Tratar preflight OPTIONS com headers CORS explícitos
    if request.method == 'OPTIONS':
        response = make_response(jsonify({"message": "OPTIONS allowed"}), 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    
    try:
        # Garantir que está autenticado
        verify_jwt_in_request()
        
        # Verificar se é administrador
        current_user = get_current_user()
        if not current_user:
            return jsonify({"erro": "Autenticação necessária."}), 401
            
        if current_user.nivel_acesso != 'administrador':
            return jsonify({"erro": "Acesso negado. Apenas administradores podem executar esta migração."}), 403
        
        print("=" * 80)
        print("🔄 INICIANDO MIGRAÇÃO DE PAGAMENTOS ANTIGOS")
        print("=" * 80)
        
        # 1. Buscar todos os pagamentos com status "Pago"
        pagamentos_pagos = PagamentoFuturo.query.filter(
            PagamentoFuturo.status == 'Pago'
        ).all()
        
        total = len(pagamentos_pagos)
        print(f"📊 Total de pagamentos encontrados com status 'Pago': {total}")
        
        if total == 0:
            return jsonify({
                "mensagem": "Nenhum pagamento para migrar!",
                "total": 0,
                "migrados": 0,
                "erros": 0
            }), 200
        
        # 2. Preparar lista de pagamentos
        lista_pagamentos = []
        for p in pagamentos_pagos:
            lista_pagamentos.append({
                "id": p.id,
                "obra_id": p.obra_id,
                "descricao": p.descricao,
                "valor": p.valor,
                "fornecedor": p.fornecedor
            })
        
        print(f"📋 Pagamentos a serem migrados:")
        for p in lista_pagamentos:
            print(f"  • ID: {p['id']} | Obra: {p['obra_id']} | {p['descricao']} | R$ {p['valor']:,.2f}")
        
        # 3. Executar migração
        migrados = 0
        erros = []
        lancamentos_criados = []
        
        for pagamento in pagamentos_pagos:
            try:
                # Criar lançamento no histórico
                novo_lancamento = Lancamento(
                    obra_id=pagamento.obra_id,
                    tipo='Despesa',
                    descricao=pagamento.descricao,
                    valor_total=pagamento.valor,
                    valor_pago=pagamento.valor,
                    data=date.today(),
                    data_vencimento=pagamento.data_vencimento,
                    status='Pago',
                    pix=pagamento.pix,
                    prioridade=0,
                    fornecedor=pagamento.fornecedor,
                    servico_id=None
                )
                db.session.add(novo_lancamento)
                db.session.flush()  # Gera o ID
                
                # Guardar informação
                lancamentos_criados.append({
                    "pagamento_id": pagamento.id,
                    "lancamento_id": novo_lancamento.id,
                    "descricao": pagamento.descricao,
                    "valor": pagamento.valor
                })
                
                # Deletar do cronograma
                db.session.delete(pagamento)
                
                migrados += 1
                print(f"  ✅ Migrado: {pagamento.descricao} (Pagamento ID: {pagamento.id} → Lançamento ID: {novo_lancamento.id})")
                
            except Exception as e:
                db.session.rollback()
                erro_msg = f"Erro ao migrar ID {pagamento.id}: {str(e)}"
                print(f"  ❌ {erro_msg}")
                erros.append({
                    "pagamento_id": pagamento.id,
                    "descricao": pagamento.descricao,
                    "erro": str(e)
                })
                continue
        
        # 4. Commit final
        if migrados > 0:
            db.session.commit()
            print(f"\n✅ Commit realizado: {migrados} pagamentos migrados com sucesso!")
        
        # 5. Relatório
        print("\n" + "=" * 80)
        print("📊 RELATÓRIO DA MIGRAÇÃO")
        print("=" * 80)
        print(f"✅ Pagamentos migrados com sucesso: {migrados}")
        print(f"❌ Erros durante a migração: {len(erros)}")
        print(f"📈 Total processado: {migrados + len(erros)}/{total}")
        print("=" * 80)
        
        return jsonify({
            "mensagem": "Migração concluída!",
            "total": total,
            "migrados": migrados,
            "erros_count": len(erros),
            "pagamentos_migrados": lancamentos_criados,
            "erros": erros
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"❌ ERRO CRÍTICO na migração: {str(e)}\n{error_details}")
        return jsonify({
            "erro": str(e),
            "details": error_details
        }), 500

# ==============================================================================

# ==============================================================================
# CRONOGRAMA DA OBRA - MODELO E ROTAS
# ==============================================================================

# ======================================================================
# CRONOGRAMA DA OBRA - MODELO
# ======================================================================

class CronogramaObra(db.Model):
    __tablename__ = 'cronograma_obra'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)

    servico_nome = db.Column(db.String(200), nullable=False)
    ordem = db.Column(db.Integer, nullable=False, default=1)
    
    # ===== PLANEJAMENTO (o que você DEFINE) =====
    data_inicio = db.Column(db.Date, nullable=False)  # Data de início PREVISTA
    data_fim_prevista = db.Column(db.Date, nullable=False)  # Data de término PREVISTA
    
    # ===== EXECUÇÃO REAL (o que você ATUALIZA MANUALMENTE) =====
    data_inicio_real = db.Column(db.Date, nullable=True)  # Quando começou DE FATO
    data_fim_real = db.Column(db.Date, nullable=True)  # Quando terminou DE FATO
    percentual_conclusao = db.Column(db.Float, nullable=False, default=0.0)  # Avanço físico REAL (você informa manualmente)
    
    # ===== TIPO DE MEDIÇÃO (NOVO) =====
    tipo_medicao = db.Column(db.String(20), default='empreitada')  # 'area' ou 'empreitada'
    area_total = db.Column(db.Float)  # Para modo 'area'
    area_executada = db.Column(db.Float, default=0)  # Para modo 'area'
    unidade_medida = db.Column(db.String(10), default='m²')  # m², m³, m, un, kg, L
    
    observacoes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    obra = db.relationship('Obra', backref=db.backref('cronograma_items', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'servico_nome': self.servico_nome,
            'ordem': self.ordem,
            # PLANEJAMENTO
            'data_inicio': self.data_inicio.isoformat() if self.data_inicio else None,
            'data_fim_prevista': self.data_fim_prevista.isoformat() if self.data_fim_prevista else None,
            # EXECUÇÃO REAL
            'data_inicio_real': self.data_inicio_real.isoformat() if self.data_inicio_real else None,
            'data_fim_real': self.data_fim_real.isoformat() if self.data_fim_real else None,
            'percentual_conclusao': float(self.percentual_conclusao),
            # TIPO DE MEDIÇÃO (NOVO)
            'tipo_medicao': self.tipo_medicao,
            'area_total': self.area_total,
            'area_executada': self.area_executada,
            'unidade_medida': self.unidade_medida,
            'observacoes': self.observacoes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }



@app.route('/obras/<int:obra_id>/servicos', methods=['GET'])
@jwt_required()
def get_servicos_obra(obra_id):
    """Busca todos os serviços de uma obra"""
    try:
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        return jsonify([{
            'id': s.id,
            'nome': s.nome,
            'responsavel': s.responsavel,
            'valor_global_mao_de_obra': s.valor_global_mao_de_obra,
            'valor_global_material': s.valor_global_material
        } for s in servicos]), 200
    except Exception as e:
        print(f"[ERRO] get_servicos_obra: {str(e)}")
        return jsonify({'error': 'Erro ao buscar serviços'}), 500


@app.route('/obras/<int:obra_id>/servico-financeiro', methods=['GET', 'OPTIONS'])
@jwt_required()
def get_servico_financeiro(obra_id):
    """
    Retorna dados financeiros de um serviço específico da obra para análise de valor agregado (EVM)
    Query parameter: servico_nome (string obrigatório)
    
    Retorna:
    - valor_total: Soma de valor_global_mao_de_obra + valor_global_material do serviço
    - valor_pago: Soma de todos os pagamentos efetivados (valor_pago) vinculados a este serviço
    - area_total: Área total do cronograma (se tipo_medicao = 'area')
    - area_executada: Área executada do cronograma
    - percentual_pago: Percentual do valor total que já foi pago
    - percentual_executado: Percentual de conclusão físico do cronograma
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/servico-financeiro (GET) acessada ---")
    
    try:
        # Obter servico_nome da query string
        servico_nome = request.args.get('servico_nome')
        
        if not servico_nome:
            print("[ERRO] servico_nome não fornecido")
            return jsonify({'erro': 'servico_nome é obrigatório'}), 400
        
        # Verificar acesso à obra
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 404
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'erro': 'Obra não encontrada'}), 404
        
        print(f"[LOG] Buscando dados financeiros para serviço: '{servico_nome}' na obra {obra_id}")
        
        # 1. Buscar o serviço na planilha de custos
        servico = Servico.query.filter_by(
            obra_id=obra_id,
            nome=servico_nome
        ).first()
        
        if not servico:
            print(f"[INFO] Serviço '{servico_nome}' não encontrado na planilha de custos")
            # Retornar dados vazios mas válidos
            return jsonify({
                'servico_nome': servico_nome,
                'valor_total': 0.0,
                'valor_pago': 0.0,
                'area_total': None,
                'area_executada': None,
                'percentual_pago': 0.0,
                'percentual_executado': 0.0
            }), 200
        
        # 2. Calcular valor total orçado (MO + Material)
        valor_total = float(servico.valor_global_mao_de_obra or 0.0) + float(servico.valor_global_material or 0.0)
        print(f"[LOG] Valor total orçado: R$ {valor_total:.2f}")
        
        # 3. Calcular valor já pago
        # Opção A: Pagamentos vinculados diretamente ao servico_id via PagamentoServico
        pagamentos_servico = db.session.query(
            func.sum(PagamentoServico.valor_pago).label('total_pago')
        ).filter(
            PagamentoServico.servico_id == servico.id
        ).first()
        
        valor_pago_servico = float(pagamentos_servico.total_pago or 0.0)
        
        # Opção B: Lançamentos vinculados ao servico_id e marcados como 'Pago'
        lancamentos_pagos = db.session.query(
            func.sum(Lancamento.valor_pago).label('total_pago')
        ).filter(
            Lancamento.obra_id == obra_id,
            Lancamento.servico_id == servico.id
        ).first()
        
        valor_pago_lancamentos = float(lancamentos_pagos.total_pago or 0.0)
        
        # Opção C: Parcelas pagas de pagamentos parcelados vinculados ao servico_id
        # Buscar pagamentos parcelados vinculados ao serviço
        pagamentos_parcelados_vinculados = PagamentoParcelado.query.filter_by(
            obra_id=obra_id,
            servico_id=servico.id
        ).all()
        
        valor_pago_parcelas = 0.0
        for pp in pagamentos_parcelados_vinculados:
            # Somar o valor das parcelas já pagas (parcelas_pagas * valor_parcela)
            valor_pago_parcelas += pp.parcelas_pagas * pp.valor_parcela
        
        # Somar todos os pagamentos
        valor_pago = valor_pago_servico + valor_pago_lancamentos + valor_pago_parcelas
        print(f"[LOG] Valor já pago (PagamentoServico): R$ {valor_pago_servico:.2f}")
        print(f"[LOG] Valor já pago (Lancamentos): R$ {valor_pago_lancamentos:.2f}")
        print(f"[LOG] Valor já pago (Parcelas): R$ {valor_pago_parcelas:.2f}")
        print(f"[LOG] Valor total pago: R$ {valor_pago:.2f}")
        
        # 4. Buscar dados do cronograma
        etapa_cronograma = CronogramaObra.query.filter_by(
            obra_id=obra_id,
            servico_nome=servico_nome
        ).first()
        
        area_total = None
        area_executada = None
        percentual_executado = 0.0
        
        if etapa_cronograma:
            area_total = float(etapa_cronograma.area_total) if etapa_cronograma.area_total else None
            area_executada = float(etapa_cronograma.area_executada) if etapa_cronograma.area_executada else None
            percentual_executado = float(etapa_cronograma.percentual_conclusao or 0.0)
            print(f"[LOG] Cronograma encontrado - % Executado: {percentual_executado:.1f}%")
        else:
            print(f"[INFO] Cronograma não encontrado para este serviço")
        
        # 5. Calcular percentual pago
        percentual_pago = (valor_pago / valor_total * 100.0) if valor_total > 0 else 0.0
        
        # 6. Montar resposta
        resposta = {
            'servico_nome': servico_nome,
            'valor_total': valor_total,
            'valor_pago': valor_pago,
            'area_total': area_total,
            'area_executada': area_executada,
            'percentual_pago': round(percentual_pago, 2),
            'percentual_executado': round(percentual_executado, 2)
        }
        
        print(f"[LOG] Resposta: {resposta}")
        return jsonify(resposta), 200
        
    except Exception as e:
        print(f"[ERRO] get_servico_financeiro: {str(e)}")
        traceback.print_exc()
        return jsonify({'erro': 'Erro ao buscar dados financeiros do serviço'}), 500


@app.route('/cronograma/<int:obra_id>', methods=['GET'])
@jwt_required()
def get_cronograma_obra(obra_id):
    try:
        # Simplificar: só verificar se obra existe
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        cronograma_items = CronogramaObra.query.filter_by(obra_id=obra_id).order_by(CronogramaObra.ordem).all()
        return jsonify([item.to_dict() for item in cronograma_items]), 200
    except Exception as e:
        print(f"[ERRO] get_cronograma_obra: {str(e)}")
        return jsonify({'error': 'Erro ao buscar cronograma'}), 500


@app.route('/cronograma', methods=['POST'])
@jwt_required()
def create_cronograma():
    try:
        current_user_id = get_jwt_identity()
        data = request.json
        required_fields = ['obra_id', 'servico_nome', 'data_inicio', 'data_fim_prevista']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Campo obrigatório ausente: {field}'}), 400
        
        obra = Obra.query.get(data['obra_id'])
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        try:
            data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
            data_fim_prevista = datetime.strptime(data['data_fim_prevista'], '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Formato de data inválido. Use YYYY-MM-DD'}), 400
        
        if data_fim_prevista < data_inicio:
            return jsonify({'error': 'Data de término não pode ser anterior à data de início'}), 400
        
        # Processar datas reais opcionais
        data_inicio_real = None
        data_fim_real = None
        
        if 'data_inicio_real' in data and data['data_inicio_real']:
            try:
                data_inicio_real = datetime.strptime(data['data_inicio_real'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Formato de data_inicio_real inválido'}), 400
        
        if 'data_fim_real' in data and data['data_fim_real']:
            try:
                data_fim_real = datetime.strptime(data['data_fim_real'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Formato de data_fim_real inválido'}), 400
        
        novo_item = CronogramaObra(
            obra_id=data['obra_id'],
            servico_nome=data['servico_nome'],
            ordem=data.get('ordem', 1),
            data_inicio=data_inicio,
            data_fim_prevista=data_fim_prevista,
            data_inicio_real=data_inicio_real,
            data_fim_real=data_fim_real,
            percentual_conclusao=float(data.get('percentual_conclusao', 0)),
            tipo_medicao=data.get('tipo_medicao', 'empreitada'),
            area_total=float(data['area_total']) if data.get('area_total') else None,
            area_executada=float(data.get('area_executada', 0)) if data.get('area_total') else None,
            unidade_medida=data.get('unidade_medida', 'm²') if data.get('area_total') else None,
            observacoes=data.get('observacoes')
        )
        
        db.session.add(novo_item)
        db.session.commit()
        
        print(f"[LOG] Cronograma criado: ID={novo_item.id}, Serviço={novo_item.servico_nome}")
        return jsonify(novo_item.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] create_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao criar etapa do cronograma'}), 500


@app.route('/cronograma/<int:cronograma_id>', methods=['PUT'])
@jwt_required()
def update_cronograma(cronograma_id):
    try:
        current_user_id = get_jwt_identity()
        data = request.json
        
        item = CronogramaObra.query.get(cronograma_id)
        if not item:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        obra = Obra.query.get(item.obra_id)
        if not obra:
            return jsonify({'error': 'Não autorizado'}), 403
        
        if 'servico_nome' in data:
            item.servico_nome = data['servico_nome']
        if 'ordem' in data:
            item.ordem = int(data['ordem'])
        
        # PLANEJAMENTO (datas previstas)
        if 'data_inicio' in data:
            try:
                item.data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Formato de data_inicio inválido'}), 400
        if 'data_fim_prevista' in data:
            try:
                item.data_fim_prevista = datetime.strptime(data['data_fim_prevista'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Formato de data_fim_prevista inválido'}), 400
        
        # EXECUÇÃO REAL (datas reais e percentual)
        if 'data_inicio_real' in data:
            if data['data_inicio_real']:
                try:
                    item.data_inicio_real = datetime.strptime(data['data_inicio_real'], '%Y-%m-%d').date()
                except ValueError:
                    return jsonify({'error': 'Formato de data_inicio_real inválido'}), 400
            else:
                item.data_inicio_real = None
        
        if 'data_fim_real' in data:
            if data['data_fim_real']:
                try:
                    item.data_fim_real = datetime.strptime(data['data_fim_real'], '%Y-%m-%d').date()
                except ValueError:
                    return jsonify({'error': 'Formato de data_fim_real inválido'}), 400
            else:
                item.data_fim_real = None
        
        if 'percentual_conclusao' in data:
            percentual = float(data['percentual_conclusao'])
            item.percentual_conclusao = max(0, min(100, percentual))
            # Auto-preencher data_fim_real quando atingir 100%
            if item.percentual_conclusao >= 100 and not item.data_fim_real:
                item.data_fim_real = datetime.now().date()
        
        if 'observacoes' in data:
            item.observacoes = data['observacoes']
        
        # CAMPOS DE MEDIÇÃO (novos)
        if 'tipo_medicao' in data:
            item.tipo_medicao = data['tipo_medicao']
        
        if 'area_total' in data:
            item.area_total = float(data['area_total']) if data['area_total'] else None
        
        if 'area_executada' in data:
            item.area_executada = float(data['area_executada']) if data['area_executada'] else None
        
        if 'unidade_medida' in data:
            item.unidade_medida = data['unidade_medida']
        
        if item.data_fim_prevista < item.data_inicio:
            return jsonify({'error': 'Data de término não pode ser anterior à data de início'}), 400
        
        item.updated_at = datetime.utcnow()
        db.session.commit()
        
        print(f"[LOG] Cronograma atualizado: ID={item.id}, %={item.percentual_conclusao}")
        return jsonify(item.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] update_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao atualizar cronograma'}), 500


@app.route('/cronograma/<int:cronograma_id>', methods=['DELETE'])
@jwt_required()
def delete_cronograma(cronograma_id):
    try:
        current_user_id = get_jwt_identity()
        
        item = CronogramaObra.query.get(cronograma_id)
        if not item:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        obra = Obra.query.get(item.obra_id)
        if not obra:
            return jsonify({'error': 'Não autorizado'}), 403
        
        servico_nome = item.servico_nome
        db.session.delete(item)
        db.session.commit()
        
        print(f"[LOG] Cronograma excluído: ID={cronograma_id}, Serviço={servico_nome}")
        return jsonify({'message': 'Etapa excluída com sucesso'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] delete_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao excluir etapa'}), 500

# ==============================================================================
# ROTA TEMPORÁRIA DE MIGRATION - ADICIONAR servico_id
# ==============================================================================
@app.route('/admin/migrate-add-servico-id', methods=['GET'])
@jwt_required()
@check_permission(roles=['master'])
def migrate_add_servico_id():
    """
    ROTA TEMPORÁRIA - Executa migration para adicionar servico_id ao pagamento_parcelado
    Apenas usuários MASTER podem executar
    Acesse: https://seu-backend.railway.app/admin/migrate-add-servico-id
    IMPORTANTE: Após executar com sucesso, REMOVA esta rota do código!
    """
    try:
        resultados = []
        
        # 1. ADD COLUMN
        try:
            db.session.execute(db.text(
                "ALTER TABLE pagamento_parcelado ADD COLUMN servico_id INTEGER;"
            ))
            db.session.commit()
            resultados.append("✅ Coluna servico_id adicionada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Coluna servico_id já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao adicionar coluna: {str(e)}")
        
        # 2. ADD FOREIGN KEY
        try:
            db.session.execute(db.text("""
                ALTER TABLE pagamento_parcelado 
                ADD CONSTRAINT fk_pagamento_parcelado_servico 
                FOREIGN KEY (servico_id) REFERENCES servico(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ Foreign key adicionada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Foreign key já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao adicionar foreign key: {str(e)}")
        
        # 3. CREATE INDEX
        try:
            db.session.execute(db.text(
                "CREATE INDEX idx_pagamento_parcelado_servico ON pagamento_parcelado(servico_id);"
            ))
            db.session.commit()
            resultados.append("✅ Índice criado com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Índice já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao criar índice: {str(e)}")
        
        # 4. VALIDAR
        try:
            result = db.session.execute(db.text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'pagamento_parcelado' 
                  AND column_name = 'servico_id';
            """))
            if result.fetchone():
                resultados.append("✅ VALIDAÇÃO: Coluna servico_id existe!")
                resultados.append("")
                resultados.append("🎉 MIGRATION CONCLUÍDA COM SUCESSO!")
                resultados.append("")
                resultados.append("🚀 Próximos passos:")
                resultados.append("1. Deploy do frontend (App.js)")
                resultados.append("2. Testar criação de pagamento parcelado")
                resultados.append("3. REMOVER esta rota /admin/migrate-add-servico-id do código")
            else:
                resultados.append("❌ VALIDAÇÃO FALHOU: Coluna não foi criada!")
        except Exception as e:
            resultados.append(f"❌ Erro na validação: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': 'Migration executada',
            'detalhes': resultados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] migrate_add_servico_id: {str(e)}\n{error_details}")
        return jsonify({
            'success': False,
            'error': str(e),
            'details': error_details
        }), 500

# ==============================================================================
# ENDPOINTS DE DIAGNÓSTICO E MIGRATION - REMOVER APÓS USO
# ==============================================================================

@app.route('/admin/check-pagamento-parcelado-info', methods=['GET'])
def check_pagamento_info():
    """Verificar informações sobre a tabela pagamento_parcelado"""
    try:
        # Contar registros
        result = db.session.execute(db.text("SELECT COUNT(*) FROM pagamento_parcelado;"))
        count = result.scalar()
        
        # Verificar se coluna existe
        result_col = db.session.execute(db.text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'pagamento_parcelado' AND column_name = 'servico_id';
        """))
        coluna_existe = result_col.fetchone() is not None
        
        return jsonify({
            'total_registros': count,
            'coluna_servico_id_existe': coluna_existe,
            'recomendacao': 'LIMPAR TABELA' if count < 50 else 'MIGRATION DIRETA'
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/limpar-pagamento-parcelado-e-adicionar-coluna', methods=['POST'])
def limpar_e_adicionar_coluna():
    """ATENÇÃO: APAGA TODOS os pagamentos parcelados e adiciona a coluna"""
    try:
        resultados = []
        
        # TRUNCATE (limpar tabela)
        db.session.execute(db.text("TRUNCATE TABLE pagamento_parcelado CASCADE;"))
        db.session.commit()
        resultados.append("✅ Tabela pagamento_parcelado limpa")
        
        # ADD COLUMN
        db.session.execute(db.text("ALTER TABLE pagamento_parcelado ADD COLUMN servico_id INTEGER;"))
        db.session.commit()
        resultados.append("✅ Coluna servico_id adicionada")
        
        # VALIDAR
        result = db.session.execute(db.text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'pagamento_parcelado' AND column_name = 'servico_id';
        """))
        
        if result.fetchone():
            resultados.append("✅ VALIDAÇÃO OK!")
            resultados.append("🎉 MIGRATION CONCLUÍDA!")
        
        return jsonify({'success': True, 'detalhes': resultados}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e), 'success': False}), 500


# ==============================================================================
# ROTAS DE EXPORTAÇÃO CSV
# ==============================================================================

@app.route('/obras/<int:obra_id>/servicos/exportar-csv', methods=['GET'])
@jwt_required()
def exportar_servicos_csv(obra_id):
    """Exporta a planilha de serviços para CSV"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        # Buscar todos os serviços da obra
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        
        # Criar CSV em memória
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Cabeçalho
        writer.writerow([
            'Serviço',
            'Responsável',
            'Valor Global Mão de Obra',
            'Valor Global Material',
            'Mão de Obra Orçada',
            'Mão de Obra Paga',
            'Mão de Obra Restante',
            '% Mão de Obra',
            'Material Orçado',
            'Material Pago',
            'Material Restante',
            '% Material',
            'Total Orçado',
            'Total Pago',
            'Total Restante',
            '% Total Executado'
        ])
        
        # Dados
        for servico in servicos:
            # Calcular valores de mão de obra
            mao_obra_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'mao_de_obra'
            )
            mao_obra_orcado = servico.valor_global_mao_de_obra
            mao_obra_restante = mao_obra_orcado - mao_obra_pago
            perc_mao_obra = (mao_obra_pago / mao_obra_orcado * 100) if mao_obra_orcado > 0 else 0
            
            # Calcular valores de material
            material_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'material'
            )
            material_orcado = servico.valor_global_material
            material_restante = material_orcado - material_pago
            perc_material = (material_pago / material_orcado * 100) if material_orcado > 0 else 0
            
            # Totais
            total_orcado = mao_obra_orcado + material_orcado
            total_pago = mao_obra_pago + material_pago
            total_restante = total_orcado - total_pago
            perc_total = (total_pago / total_orcado * 100) if total_orcado > 0 else 0
            
            writer.writerow([
                servico.nome,
                servico.responsavel or '-',
                f'R$ {mao_obra_orcado:,.2f}',
                f'R$ {material_orcado:,.2f}',
                f'R$ {mao_obra_orcado:,.2f}',
                f'R$ {mao_obra_pago:,.2f}',
                f'R$ {mao_obra_restante:,.2f}',
                f'{perc_mao_obra:.1f}%',
                f'R$ {material_orcado:,.2f}',
                f'R$ {material_pago:,.2f}',
                f'R$ {material_restante:,.2f}',
                f'{perc_material:.1f}%',
                f'R$ {total_orcado:,.2f}',
                f'R$ {total_pago:,.2f}',
                f'R$ {total_restante:,.2f}',
                f'{perc_total:.1f}%'
            ])
        
        # Preparar para download
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),  # UTF-8 com BOM para Excel
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'Servicos_{obra.nome.replace(" ", "_")}_{date.today()}.csv'
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] exportar_servicos_csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/cronograma-financeiro/exportar-csv', methods=['GET'])
@jwt_required()
def exportar_cronograma_csv(obra_id):
    """Exporta o cronograma financeiro para CSV"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        hoje = date.today()
        
        # Buscar dados
        pagamentos_futuros = PagamentoFuturo.query.filter_by(obra_id=obra_id).all()
        pagamentos_parcelados = PagamentoParcelado.query.filter_by(obra_id=obra_id).all()
        
        # Criar CSV em memória
        output = io.StringIO()
        writer = csv.writer(output)
        
        # SEÇÃO 1: PAGAMENTOS FUTUROS (ÚNICOS)
        writer.writerow(['===== PAGAMENTOS FUTUROS (ÚNICOS) ====='])
        writer.writerow([
            'Descrição',
            'Fornecedor',
            'Vencimento',
            'Valor',
            'Status',
            'Tipo',
            'Serviço Vinculado'
        ])
        
        for pag in pagamentos_futuros:
            servico_nome = '-'
            if pag.servico_id:
                servico = Servico.query.get(pag.servico_id)
                servico_nome = servico.nome if servico else '-'
            
            status_display = 'Pago' if pag.status == 'Pago' else ('Vencido' if pag.data_vencimento < hoje else 'Previsto')
            tipo_display = pag.tipo if hasattr(pag, 'tipo') and pag.tipo else '-'
            
            writer.writerow([
                pag.descricao,
                pag.fornecedor or '-',
                pag.data_vencimento.strftime('%d/%m/%Y') if pag.data_vencimento else '-',
                f'R$ {pag.valor:,.2f}',
                status_display,
                tipo_display,
                servico_nome
            ])
        
        writer.writerow([])  # Linha em branco
        
        # SEÇÃO 2: PAGAMENTOS PARCELADOS
        writer.writerow(['===== PAGAMENTOS PARCELADOS ====='])
        writer.writerow([
            'Descrição',
            'Fornecedor',
            'Valor Total',
            'Parcelas',
            'Valor/Parcela',
            'Periodicidade',
            'Parcelas Pagas',
            'Status',
            'Segmento',
            'Serviço Vinculado'
        ])
        
        for pag in pagamentos_parcelados:
            servico_nome = '-'
            if pag.servico_id:
                servico = Servico.query.get(pag.servico_id)
                servico_nome = servico.nome if servico else '-'
            
            segmento = 'Material'
            try:
                if hasattr(pag, 'segmento') and pag.segmento:
                    segmento = pag.segmento
            except:
                pass
            
            writer.writerow([
                pag.descricao,
                pag.fornecedor or '-',
                f'R$ {pag.valor_total:,.2f}',
                f'{pag.numero_parcelas}',
                f'R$ {pag.valor_parcela:,.2f}',
                pag.periodicidade,
                f'{pag.parcelas_pagas}/{pag.numero_parcelas}',
                pag.status,
                segmento,
                servico_nome
            ])
        
        writer.writerow([])  # Linha em branco
        
        # SEÇÃO 3: RESUMO FINANCEIRO
        writer.writerow(['===== RESUMO FINANCEIRO ====='])
        
        # Calcular totais
        total_futuros_previsto = sum(p.valor for p in pagamentos_futuros if p.status != 'Pago' and p.data_vencimento >= hoje)
        total_futuros_vencido = sum(p.valor for p in pagamentos_futuros if p.status != 'Pago' and p.data_vencimento < hoje)
        total_futuros_pago = sum(p.valor for p in pagamentos_futuros if p.status == 'Pago')
        
        total_parcelado = sum(p.valor_total for p in pagamentos_parcelados)
        total_parcelado_pago = sum(p.parcelas_pagas * p.valor_parcela for p in pagamentos_parcelados)
        total_parcelado_restante = total_parcelado - total_parcelado_pago
        
        writer.writerow([
            'Total Pagamentos Futuros (Previstos)',
            f'R$ {total_futuros_previsto:,.2f}'
        ])
        writer.writerow([
            'Total Pagamentos Futuros (Vencidos)',
            f'R$ {total_futuros_vencido:,.2f}'
        ])
        writer.writerow([
            'Total Pagamentos Futuros (Pagos)',
            f'R$ {total_futuros_pago:,.2f}'
        ])
        writer.writerow([
            'Total Parcelados (Valor Total)',
            f'R$ {total_parcelado:,.2f}'
        ])
        writer.writerow([
            'Total Parcelados (Já Pago)',
            f'R$ {total_parcelado_pago:,.2f}'
        ])
        writer.writerow([
            'Total Parcelados (Restante)',
            f'R$ {total_parcelado_restante:,.2f}'
        ])
        writer.writerow([
            'TOTAL GERAL A PAGAR',
            f'R$ {(total_futuros_previsto + total_futuros_vencido + total_parcelado_restante):,.2f}'
        ])
        
        # Preparar para download
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'Cronograma_{obra.nome.replace(" ", "_")}_{date.today()}.csv'
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] exportar_cronograma_csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# ==============================================================================



# ==============================================================================
# ROTAS EXATAS DO FRONTEND - Pagamentos Futuros com servico-ID
# ==============================================================================

# -----------------------------------------------------------------------------
# DELETAR Pagamento Futuro (rota exata do frontend)
# DELETE /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/servico-{id}
# -----------------------------------------------------------------------------
@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/servico-<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_pagamento_futuro_servico(obra_id, pagamento_id):
    """Deleta pagamento futuro - rota exata do frontend"""
    try:
        if request.method == 'OPTIONS':
            return '', 200
        
        print(f"[LOG] DELETE pagamento futuro: obra_id={obra_id}, pagamento_id={pagamento_id}")
        
        # Buscar pagamento usando servico_id como filtro adicional
        pagamento = PagamentoFuturo.query.filter_by(
            id=pagamento_id,
            obra_id=obra_id
        ).first()
        
        if not pagamento:
            print(f"[ERRO] Pagamento {pagamento_id} não encontrado na obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Deletar
        db.session.delete(pagamento)
        db.session.commit()
        
        print(f"[LOG] ✅ Pagamento futuro {pagamento_id} deletado com sucesso")
        return jsonify({"mensagem": "Pagamento deletado com sucesso", "id": pagamento_id}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] deletar_pagamento_futuro_servico: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


# -----------------------------------------------------------------------------
# EDITAR Pagamento Futuro (rota exata do frontend)
# PUT /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/servico-{id}
# -----------------------------------------------------------------------------
@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/servico-<int:pagamento_id>', methods=['PUT', 'PATCH', 'OPTIONS'])
@jwt_required()
def editar_pagamento_futuro_servico(obra_id, pagamento_id):
    """Edita pagamento futuro - rota exata do frontend"""
    try:
        if request.method == 'OPTIONS':
            return '', 200
        
        print(f"[LOG] PUT pagamento futuro: obra_id={obra_id}, pagamento_id={pagamento_id}")
        
        pagamento = PagamentoFuturo.query.filter_by(
            id=pagamento_id,
            obra_id=obra_id
        ).first()
        
        if not pagamento:
            print(f"[ERRO] Pagamento {pagamento_id} não encontrado na obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json()
        
        if 'descricao' in data:
            pagamento.descricao = data['descricao']
        if 'valor' in data:
            pagamento.valor = float(data['valor'])
        if 'data_vencimento' in data:
            pagamento.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        if 'fornecedor' in data:
            pagamento.fornecedor = data['fornecedor']
        if 'pix' in data:
            pagamento.pix = data['pix']
        if 'observacoes' in data:
            pagamento.observacoes = data['observacoes']
        
        db.session.commit()
        
        print(f"[LOG] ✅ Pagamento futuro {pagamento_id} editado com sucesso")
        return jsonify({"mensagem": "Pagamento atualizado com sucesso", "id": pagamento_id}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] editar_pagamento_futuro_servico: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


# ROTA PARA EXPORTAR SERVICOS EM PDF
@app.route('/obras/<int:obra_id>/servicos/exportar-pdf', methods=['GET'])
@jwt_required()
def exportar_servicos_pdf(obra_id):
    """Exporta a planilha de serviços para PDF"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        # Buscar todos os serviços da obra
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        
        # Criar PDF em memória
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm, topMargin=2*cm, bottomMargin=2*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Função para formatar valores em reais
        def formatar_real(valor):
            return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        
        # Título
        titulo = Paragraph(f"<b>Planilha de Serviços - {obra.nome}</b>", styles['Title'])
        elements.append(titulo)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo
        subtitulo = Paragraph(f"Gerado em: {date.today().strftime('%d/%m/%Y')}", styles['Normal'])
        elements.append(subtitulo)
        elements.append(Spacer(1, 1*cm))
        
        # Preparar dados da tabela
        data = [
            ['Serviço', 'Responsável', 'MO Orçado', 'MO Pago', '% MO', 'Mat Orçado', 'Mat Pago', '% Mat', 'Total', '% Total']
        ]
        
        total_geral_orcado = 0
        total_geral_pago = 0
        
        for servico in servicos:
            # Calcular valores de mão de obra
            mao_obra_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'mao_de_obra'
            )
            mao_obra_orcado = servico.valor_global_mao_de_obra
            perc_mao_obra = (mao_obra_pago / mao_obra_orcado * 100) if mao_obra_orcado > 0 else 0
            
            # Calcular valores de material
            material_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'material'
            )
            material_orcado = servico.valor_global_material
            perc_material = (material_pago / material_orcado * 100) if material_orcado > 0 else 0
            
            # Totais
            total_orcado = mao_obra_orcado + material_orcado
            total_pago = mao_obra_pago + material_pago
            perc_total = (total_pago / total_orcado * 100) if total_orcado > 0 else 0
            
            total_geral_orcado += total_orcado
            total_geral_pago += total_pago
            
            # Truncar nome do serviço se muito longo
            nome_servico = servico.nome if len(servico.nome) <= 20 else servico.nome[:17] + '...'
            resp = servico.responsavel if servico.responsavel and len(servico.responsavel) <= 15 else (servico.responsavel[:12] + '...' if servico.responsavel else '-')
            
            data.append([
                nome_servico,
                resp,
                formatar_real(mao_obra_orcado),
                formatar_real(mao_obra_pago),
                f'{perc_mao_obra:.1f}%',
                formatar_real(material_orcado),
                formatar_real(material_pago),
                f'{perc_material:.1f}%',
                formatar_real(total_orcado),
                f'{perc_total:.1f}%'
            ])
        
        # Linha de totais
        perc_geral = (total_geral_pago / total_geral_orcado * 100) if total_geral_orcado > 0 else 0
        data.append([
            'TOTAL',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            formatar_real(total_geral_orcado),
            f'{perc_geral:.1f}%'
        ])
        
        # Criar tabela
        table = Table(data, colWidths=[3*cm, 2.5*cm, 2*cm, 2*cm, 1.5*cm, 2*cm, 2*cm, 1.5*cm, 2.5*cm, 1.5*cm])
        
        # Estilo da tabela
        style_list = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4CAF50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.whitesmoke, colors.white]),
            # Linha de totais
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#FFC107')),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.black),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]
        
        table.setStyle(TableStyle(style_list))
        elements.append(table)
        
        # Legenda
        elements.append(Spacer(1, 1*cm))
        legenda = Paragraph("<b>Legenda:</b> MO = Mão de Obra | Mat = Material", styles['Normal'])
        elements.append(legenda)
        
        # Construir PDF
        doc.build(elements)
        buffer.seek(0)
        
        print(f"--- [LOG] PDF de serviços gerado para obra {obra_id} ---")
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Servicos_{obra.nome.replace(' ', '_')}_{date.today()}.pdf",
            mimetype='application/pdf'
        )
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] ao gerar PDF de serviços: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


# ==============================================================================
# INICIALIZAÇÃO DO SERVIDOR (DEVE SER A ÚLTIMA COISA DO ARQUIVO)
# ==============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"--- [LOG] Iniciando servidor Flask na porta {port} ---")
    app.run(host='0.0.0.0', port=port, debug=True)
