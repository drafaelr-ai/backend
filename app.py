# ============================================================================
# VERSÃO CORRIGIDA - 18/NOV/2025 - SEM COLUNA SEGMENTO
# Esta versão REMOVE a definição de coluna segmento dos modelos
# para evitar erro "column segmento does not exist"
# ============================================================================
import os
import traceback  # Importado para log de erros detalhado
import re  # Importado para o CORS com regex
import zipfile  # Importado para criar ZIP de notas fiscais
import json  # Para parsing de JSON
import urllib.request  # Para chamar API externa (nativo do Python)
import urllib.error  # Para tratamento de erros HTTP
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
        
        # 2.5 NOVO: Adicionar campos de pagamento na tabela orcamento
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'orcamento' AND column_name = 'data_vencimento';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE orcamento ADD COLUMN data_vencimento DATE;")
            print("✅ Coluna data_vencimento adicionada em orcamento")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'orcamento' AND column_name = 'numero_parcelas';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE orcamento ADD COLUMN numero_parcelas INTEGER DEFAULT 1;")
            print("✅ Coluna numero_parcelas adicionada em orcamento")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'orcamento' AND column_name = 'periodicidade';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE orcamento ADD COLUMN periodicidade VARCHAR(20) DEFAULT 'Mensal';")
            print("✅ Coluna periodicidade adicionada em orcamento")
        
        # 2.6 NOVO: Adicionar coluna concluida na tabela obra
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'obra' AND column_name = 'concluida';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE obra ADD COLUMN concluida BOOLEAN DEFAULT FALSE;")
            print("✅ Coluna concluida adicionada em obra")
        
        # =================================================================
        # 3. CORREÇÃO DO ERRO DE FOREIGN KEY (CRÍTICO)
        # Verificar se a tabela parcela_individual existe E se a FK está correta
        # =================================================================
        print("🔄 Verificando tabela parcela_individual...")
        
        # Verificar se a tabela existe
        cur.execute("SELECT to_regclass('public.parcela_individual');")
        tabela_existe = cur.fetchone()[0]
        
        if not tabela_existe:
            # Tabela não existe, criar
            print("📝 Criando tabela parcela_individual...")
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
            print("✅ Tabela parcela_individual criada!")
        else:
            # Tabela existe, verificar se FK está correta
            cur.execute("""
                SELECT ccu.table_name 
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu 
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.table_name = 'parcela_individual' 
                AND tc.constraint_type = 'FOREIGN KEY'
                AND ccu.column_name = 'id';
            """)
            fk_result = cur.fetchone()
            fk_table = fk_result[0] if fk_result else None
            
            if fk_table == 'pagamento_parcelado_v2':
                print("✅ Tabela parcela_individual já existe com FK correta")
            else:
                print(f"⚠️ FK atual aponta para: {fk_table}")
                print("⚠️ NÃO vamos dropar a tabela para preservar dados")
                print("⚠️ Se houver problemas de FK, corrija manualmente")
        
        # 4. Alterar comprovante_url para TEXT (suportar base64 grande)
        print("🔄 Verificando coluna comprovante_url...")
        cur.execute("""
            SELECT data_type FROM information_schema.columns 
            WHERE table_name = 'movimentacao_caixa' AND column_name = 'comprovante_url';
        """)
        result = cur.fetchone()
        if result and result[0] != 'text':
            print("📝 Alterando comprovante_url para TEXT...")
            cur.execute("ALTER TABLE movimentacao_caixa ALTER COLUMN comprovante_url TYPE TEXT;")
            print("✅ Coluna comprovante_url alterada para TEXT!")
        
        # 5. Remover FK constraints problemáticas em criado_por (para permitir exclusão de usuários)
        print("🔄 Removendo FK constraints em criado_por...")
        fk_constraints_to_drop = [
            ("diario_obra", "diario_obra_criado_por_fkey"),
            ("movimentacao_caixa", "movimentacao_caixa_criado_por_fkey"),
            ("fechamento_caixa", "fechamento_caixa_fechado_por_fkey"),
        ]
        for table, constraint in fk_constraints_to_drop:
            try:
                cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint};")
                print(f"   ✅ Constraint {constraint} removida (ou não existia)")
            except Exception as e:
                print(f"   ⚠️ {constraint}: {str(e)[:50]}")
        
        # 6. Criar tabela de boletos (Gestão de Boletos)
        print("🔄 Verificando tabela boleto...")
        cur.execute("SELECT to_regclass('public.boleto');")
        if not cur.fetchone()[0]:
            print("📝 Criando tabela boleto...")
            cur.execute("""
                CREATE TABLE boleto (
                    id SERIAL PRIMARY KEY,
                    obra_id INTEGER NOT NULL REFERENCES obra(id) ON DELETE CASCADE,
                    usuario_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
                    
                    -- Dados do boleto
                    codigo_barras VARCHAR(60),
                    descricao VARCHAR(255),
                    beneficiario VARCHAR(255),
                    valor DECIMAL(12,2),
                    data_vencimento DATE NOT NULL,
                    
                    -- Controle
                    status VARCHAR(20) DEFAULT 'Pendente',
                    data_pagamento DATE,
                    vinculado_servico_id INTEGER,
                    
                    -- Arquivo PDF
                    arquivo_nome VARCHAR(255),
                    arquivo_pdf TEXT,
                    
                    -- Alertas enviados
                    alerta_7dias BOOLEAN DEFAULT FALSE,
                    alerta_3dias BOOLEAN DEFAULT FALSE,
                    alerta_hoje BOOLEAN DEFAULT FALSE,
                    alerta_vencido BOOLEAN DEFAULT FALSE,
                    
                    -- Timestamps
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX idx_boleto_obra ON boleto(obra_id);
                CREATE INDEX idx_boleto_vencimento ON boleto(data_vencimento);
                CREATE INDEX idx_boleto_status ON boleto(status);
            """)
            print("✅ Tabela boleto criada!")
        else:
            print("   ℹ️ Tabela boleto já existe")
        
        # =================================================================
        # MÓDULO ORÇAMENTO DE ENGENHARIA - NOVAS TABELAS E CAMPOS
        # =================================================================
        print("🔄 Verificando estrutura do módulo de Orçamento de Engenharia...")
        
        # 1. Adicionar campos bdi e area na tabela obra
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'obra' AND column_name = 'bdi';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE obra ADD COLUMN bdi FLOAT DEFAULT 0;")
            print("✅ Coluna bdi adicionada em obra")
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'obra' AND column_name = 'area';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE obra ADD COLUMN area FLOAT;")
            print("✅ Coluna area adicionada em obra")
        
        # 2. Criar tabela servico_base (base de referência tipo SINAPI)
        cur.execute("SELECT to_regclass('public.servico_base');")
        if not cur.fetchone()[0]:
            cur.execute("""
                CREATE TABLE servico_base (
                    id SERIAL PRIMARY KEY,
                    categoria VARCHAR(100) NOT NULL,
                    codigo_ref VARCHAR(50),
                    descricao VARCHAR(500) NOT NULL,
                    unidade VARCHAR(20) NOT NULL,
                    tipo_composicao VARCHAR(20) DEFAULT 'separado',
                    preco_mao_obra FLOAT,
                    preco_material FLOAT,
                    preco_unitario FLOAT,
                    rateio_mo FLOAT DEFAULT 50,
                    rateio_mat FLOAT DEFAULT 50,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_servico_base_categoria ON servico_base(categoria);
                CREATE INDEX idx_servico_base_descricao ON servico_base(descricao);
            """)
            print("✅ Tabela servico_base criada!")
        else:
            print("   ℹ️ Tabela servico_base já existe")
        
        # 3. Criar tabela servico_usuario (biblioteca do usuário)
        cur.execute("SELECT to_regclass('public.servico_usuario');")
        if not cur.fetchone()[0]:
            cur.execute("""
                CREATE TABLE servico_usuario (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    categoria VARCHAR(100),
                    descricao VARCHAR(500) NOT NULL,
                    unidade VARCHAR(20) NOT NULL,
                    tipo_composicao VARCHAR(20) DEFAULT 'separado',
                    preco_mao_obra FLOAT,
                    preco_material FLOAT,
                    preco_unitario FLOAT,
                    rateio_mo FLOAT DEFAULT 50,
                    rateio_mat FLOAT DEFAULT 50,
                    vezes_usado INTEGER DEFAULT 0,
                    ultima_utilizacao TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_servico_usuario_user ON servico_usuario(user_id);
                CREATE INDEX idx_servico_usuario_descricao ON servico_usuario(descricao);
            """)
            print("✅ Tabela servico_usuario criada!")
        else:
            print("   ℹ️ Tabela servico_usuario já existe")
        
        # 4. Criar tabela orcamento_eng_etapa
        cur.execute("SELECT to_regclass('public.orcamento_eng_etapa');")
        if not cur.fetchone()[0]:
            cur.execute("""
                CREATE TABLE orcamento_eng_etapa (
                    id SERIAL PRIMARY KEY,
                    obra_id INTEGER NOT NULL REFERENCES obra(id) ON DELETE CASCADE,
                    codigo VARCHAR(20),
                    nome VARCHAR(200) NOT NULL,
                    ordem INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_orc_etapa_obra ON orcamento_eng_etapa(obra_id);
            """)
            print("✅ Tabela orcamento_eng_etapa criada!")
        else:
            print("   ℹ️ Tabela orcamento_eng_etapa já existe")
        
        # 5. Criar tabela orcamento_eng_item
        cur.execute("SELECT to_regclass('public.orcamento_eng_item');")
        if not cur.fetchone()[0]:
            cur.execute("""
                CREATE TABLE orcamento_eng_item (
                    id SERIAL PRIMARY KEY,
                    etapa_id INTEGER NOT NULL REFERENCES orcamento_eng_etapa(id) ON DELETE CASCADE,
                    codigo VARCHAR(20),
                    descricao VARCHAR(500) NOT NULL,
                    unidade VARCHAR(20) NOT NULL,
                    quantidade FLOAT DEFAULT 0,
                    tipo_composicao VARCHAR(20) DEFAULT 'separado',
                    preco_mao_obra FLOAT,
                    preco_material FLOAT,
                    preco_unitario FLOAT,
                    rateio_mo FLOAT DEFAULT 50,
                    rateio_mat FLOAT DEFAULT 50,
                    servico_id INTEGER,
                    valor_pago_mo FLOAT DEFAULT 0,
                    valor_pago_mat FLOAT DEFAULT 0,
                    ordem INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_orc_item_etapa ON orcamento_eng_item(etapa_id);
                CREATE INDEX idx_orc_item_servico ON orcamento_eng_item(servico_id);
            """)
            print("✅ Tabela orcamento_eng_item criada!")
        else:
            print("   ℹ️ Tabela orcamento_eng_item já existe")
        
        print("✅ Módulo de Orçamento de Engenharia verificado!")
        
        # =================================================================
        # CAMPO CONCLUÍDO NO SERVIÇO
        # =================================================================
        print("🔄 Verificando campo concluido em servico...")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'servico' AND column_name = 'concluido';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE servico ADD COLUMN concluido BOOLEAN DEFAULT FALSE;")
            print("✅ Coluna concluido adicionada em servico")
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'servico' AND column_name = 'data_conclusao';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE servico ADD COLUMN data_conclusao DATE;")
            print("✅ Coluna data_conclusao adicionada em servico")
        
        # =================================================================
        # MÓDULO AGENDA DE DEMANDAS - NOVA TABELA
        # =================================================================
        print("🔄 Verificando tabela agenda_demanda...")
        cur.execute("SELECT to_regclass('public.agenda_demanda');")
        if not cur.fetchone()[0]:
            print("📝 Criando tabela agenda_demanda...")
            cur.execute("""
                CREATE TABLE agenda_demanda (
                    id SERIAL PRIMARY KEY,
                    obra_id INTEGER NOT NULL REFERENCES obra(id) ON DELETE CASCADE,
                    
                    -- Dados básicos
                    descricao VARCHAR(255) NOT NULL,
                    tipo VARCHAR(50) NOT NULL DEFAULT 'material',
                    fornecedor VARCHAR(255),
                    telefone VARCHAR(50),
                    
                    -- Valores
                    valor FLOAT,
                    
                    -- Datas
                    data_prevista DATE NOT NULL,
                    data_conclusao DATE,
                    
                    -- Status: aguardando, concluido, atrasado, cancelado
                    status VARCHAR(50) NOT NULL DEFAULT 'aguardando',
                    
                    -- Origem: manual, pagamento, orcamento
                    origem VARCHAR(50) NOT NULL DEFAULT 'manual',
                    
                    -- IDs de referência (para importações)
                    pagamento_servico_id INTEGER,
                    orcamento_item_id INTEGER,
                    servico_id INTEGER,
                    
                    -- Observações
                    observacoes TEXT,
                    
                    -- Timestamps
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX idx_agenda_demanda_obra ON agenda_demanda(obra_id);
                CREATE INDEX idx_agenda_demanda_data ON agenda_demanda(data_prevista);
                CREATE INDEX idx_agenda_demanda_status ON agenda_demanda(status);
            """)
            print("✅ Tabela agenda_demanda criada!")
        else:
            print("   ℹ️ Tabela agenda_demanda já existe")
            
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
# CORREÇÃO: Aumentar tempo de expiração do token de 15min (padrão) para 24 horas
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24)
jwt = JWTManager(app)
print("--- [LOG] JWT Manager inicializado com expiração de 24 horas ---")
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

# --- MODELO DE NOTIFICAÇÕES ---
class Notificacao(db.Model):
    __tablename__ = 'notificacao'
    id = db.Column(db.Integer, primary_key=True)
    usuario_destino_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    usuario_origem_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    tipo = db.Column(db.String(50), nullable=False)  # 'servico_criado', 'pagamento_inserido', 'orcamento_aprovado'
    titulo = db.Column(db.String(255), nullable=False)
    mensagem = db.Column(db.Text, nullable=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=True)
    item_id = db.Column(db.Integer, nullable=True)
    item_type = db.Column(db.String(50), nullable=True)  # 'servico', 'lancamento', 'orcamento'
    lida = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamentos
    usuario_destino = db.relationship('User', foreign_keys=[usuario_destino_id], backref='notificacoes_recebidas')
    usuario_origem = db.relationship('User', foreign_keys=[usuario_origem_id], backref='notificacoes_enviadas')
    obra = db.relationship('Obra', backref='notificacoes')
    
    def to_dict(self):
        return {
            "id": self.id,
            "usuario_destino_id": self.usuario_destino_id,
            "usuario_origem_id": self.usuario_origem_id,
            "usuario_origem_nome": self.usuario_origem.username if self.usuario_origem else None,
            "tipo": self.tipo,
            "titulo": self.titulo,
            "mensagem": self.mensagem,
            "obra_id": self.obra_id,
            "obra_nome": self.obra.nome if self.obra else None,
            "item_id": self.item_id,
            "item_type": self.item_type,
            "lida": self.lida,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

# Função helper para criar notificações
def criar_notificacao(usuario_destino_id, tipo, titulo, mensagem=None, obra_id=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Cria uma nova notificação para um usuário"""
    try:
        notificacao = Notificacao(
            usuario_destino_id=usuario_destino_id,
            usuario_origem_id=usuario_origem_id,
            tipo=tipo,
            titulo=titulo,
            mensagem=mensagem,
            obra_id=obra_id,
            item_id=item_id,
            item_type=item_type
        )
        db.session.add(notificacao)
        db.session.commit()
        print(f"--- [NOTIF] Notificação criada: {tipo} para usuário {usuario_destino_id} ---")
        return notificacao
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] Falha ao criar notificação: {e} ---")
        return None

def notificar_masters(tipo, titulo, mensagem=None, obra_id=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Notifica todos os usuários master"""
    masters = User.query.filter_by(role='master').all()
    for master in masters:
        if master.id != usuario_origem_id:  # Não notificar a si mesmo
            criar_notificacao(
                usuario_destino_id=master.id,
                tipo=tipo,
                titulo=titulo,
                mensagem=mensagem,
                obra_id=obra_id,
                item_id=item_id,
                item_type=item_type,
                usuario_origem_id=usuario_origem_id
            )

def notificar_operadores_obra(obra_id, tipo, titulo, mensagem=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Notifica todos os operadores (comum) com acesso a uma obra"""
    obra = Obra.query.get(obra_id)
    if not obra:
        return
    
    # Buscar usuários com acesso à obra que são 'comum'
    for user in obra.usuarios_permitidos:
        if user.role == 'comum' and user.id != usuario_origem_id:
            criar_notificacao(
                usuario_destino_id=user.id,
                tipo=tipo,
                titulo=titulo,
                mensagem=mensagem,
                obra_id=obra_id,
                item_id=item_id,
                item_type=item_type,
                usuario_origem_id=usuario_origem_id
            )

def notificar_administradores(tipo, titulo, mensagem=None, obra_id=None, item_id=None, item_type=None, usuario_origem_id=None):
    """Notifica todos os usuários administradores"""
    admins = User.query.filter_by(role='administrador').all()
    for admin in admins:
        if admin.id != usuario_origem_id:  # Não notificar a si mesmo
            criar_notificacao(
                usuario_destino_id=admin.id,
                tipo=tipo,
                titulo=titulo,
                mensagem=mensagem,
                obra_id=obra_id,
                item_id=item_id,
                item_type=item_type,
                usuario_origem_id=usuario_origem_id
            )

# ---------------------------------------------

# ===== MODELO DE BOLETOS (GESTÃO DE BOLETOS) =====
class Boleto(db.Model):
    """Modelo para gestão de boletos com upload de PDF e extração automática"""
    __tablename__ = 'boleto'
    
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Dados do boleto
    codigo_barras = db.Column(db.String(60), nullable=True)
    descricao = db.Column(db.String(255), nullable=True)
    beneficiario = db.Column(db.String(255), nullable=True)
    valor = db.Column(db.Float, nullable=True)
    data_vencimento = db.Column(db.Date, nullable=False)
    
    # Controle
    status = db.Column(db.String(20), default='Pendente')  # Pendente, Pago, Vencido
    data_pagamento = db.Column(db.Date, nullable=True)
    vinculado_servico_id = db.Column(db.Integer, nullable=True)
    
    # Arquivo PDF
    arquivo_nome = db.Column(db.String(255), nullable=True)
    arquivo_pdf = db.Column(db.Text, nullable=True)  # Base64 do PDF
    
    # Alertas enviados
    alerta_7dias = db.Column(db.Boolean, default=False)
    alerta_3dias = db.Column(db.Boolean, default=False)
    alerta_hoje = db.Column(db.Boolean, default=False)
    alerta_vencido = db.Column(db.Boolean, default=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    usuario = db.relationship('User', backref='boletos_cadastrados')
    # Nota: vinculado_servico_id não tem ForeignKey, busca manual no to_dict()
    
    def to_dict(self):
        # Calcular dias para vencimento
        hoje = date.today()
        dias_para_vencer = (self.data_vencimento - hoje).days if self.data_vencimento else 0
        
        # Determinar urgência
        if self.status == 'Pago':
            urgencia = 'pago'
        elif dias_para_vencer < 0:
            urgencia = 'vencido'
        elif dias_para_vencer == 0:
            urgencia = 'hoje'
        elif dias_para_vencer <= 3:
            urgencia = 'urgente'
        elif dias_para_vencer <= 7:
            urgencia = 'atencao'
        else:
            urgencia = 'normal'
        
        # Buscar nome do serviço vinculado
        servico_nome = None
        if self.vinculado_servico_id:
            try:
                servico = db.session.get(Servico, self.vinculado_servico_id)
                servico_nome = servico.nome if servico else None
            except:
                pass
        
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "usuario_id": self.usuario_id,
            "usuario_nome": self.usuario.username if self.usuario else None,
            "codigo_barras": self.codigo_barras,
            "descricao": self.descricao,
            "beneficiario": self.beneficiario,
            "valor": self.valor,
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "status": self.status,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "vinculado_servico_id": self.vinculado_servico_id,
            "servico_nome": servico_nome,
            "arquivo_nome": self.arquivo_nome,
            "tem_pdf": bool(self.arquivo_pdf),
            "dias_para_vencer": dias_para_vencer,
            "urgencia": urgencia,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

# Função para extrair dados do PDF do boleto
def extrair_dados_boleto_pdf(pdf_base64):
    """Extrai código de barras, vencimento e valor do PDF do boleto (suporta múltiplos boletos)"""
    try:
        import pdfplumber
        
        # Decodificar base64
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        pdf_bytes = base64.b64decode(pdf_base64)
        
        boletos_encontrados = []
        
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            print(f"--- [BOLETO] PDF com {len(pdf.pages)} páginas ---")
            
            # Processar cada página separadamente
            for page_num, page in enumerate(pdf.pages, 1):
                texto = page.extract_text() or ""
                
                if not texto.strip():
                    continue
                
                print(f"--- [BOLETO] Página {page_num}: {len(texto)} chars ---")
                
                boleto = {
                    'codigo_barras': None,
                    'data_vencimento': None,
                    'valor': None,
                    'beneficiario': None,
                    'pagina': page_num
                }
                
                # =====================================================
                # 1. CÓDIGO DE BARRAS (LINHA DIGITÁVEL)
                # =====================================================
                patterns_codigo = [
                    # Itaú: 34191.57007 00014.647382 59766.050005 1 12960001029833
                    r'(\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})',
                    # Inter: 07790.00116 12070.514091 03958.220455 4 11960000057900
                    r'(\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})',
                    # Genérico com pontos e espaços variados
                    r'(\d{5}\.?\d{5}\s*\d{5}\.?\d{6}\s*\d{5}\.?\d{6}\s*\d\s*\d{14})',
                ]
                
                for pattern in patterns_codigo:
                    match = re.search(pattern, texto)
                    if match:
                        codigo_raw = match.group(1)
                        codigo = re.sub(r'[\s\.]', '', codigo_raw)
                        if len(codigo) >= 47:
                            boleto['codigo_barras'] = codigo[:47] if len(codigo) >= 47 else codigo
                            break
                
                # =====================================================
                # 2. VALOR
                # =====================================================
                patterns_valor = [
                    # Itaú: (=) Valor do Documento\n157 R$ 10.298,33
                    r'\(=\)\s*[Vv]alor\s*(?:do\s*)?[Dd]ocumento\s*[\n\r]*\s*\d+\s*R\$\s*([\d.]+,\d{2})',
                    # Valor do Documento genérico
                    r'[Vv]alor\s*(?:do\s*)?[Dd]ocumento\s*[\n\r]*\s*([\d.]+,\d{2})',
                    r'\(=\)\s*[Vv]alor\s*(?:do\s*)?[Dd]ocumento\s*[\n\r]*\s*([\d.]+,\d{2})',
                    # R$ com milhar
                    r'R\$\s*([\d]{1,3}(?:\.\d{3})*,\d{2})',
                ]
                
                for pattern in patterns_valor:
                    matches = re.findall(pattern, texto)
                    for match_str in matches:
                        try:
                            valor_str = match_str.replace('.', '').replace(',', '.')
                            valor = float(valor_str)
                            if valor > 10 and valor < 10000000:  # Entre R$10 e R$10mi
                                boleto['valor'] = valor
                                break
                        except:
                            continue
                    if boleto['valor']:
                        break
                
                # =====================================================
                # 3. DATA DE VENCIMENTO
                # =====================================================
                patterns_venc = [
                    r'[Vv]encimento\n.*?\n(\d{2}/\d{2}/\d{4})',  # Itaú: Vencimento\nLocal...\n15/12/2025
                    r'[Vv]encimento\s+(\d{2}/\d{2}/\d{4})',  # Inter: Vencimento 06/09/2025
                    r'[Vv]encimento.*?(\d{2}/\d{2}/\d{4})',  # Qualquer texto entre
                ]
                
                hoje = date.today()
                datas_encontradas = []
                
                for pattern in patterns_venc:
                    matches = re.findall(pattern, texto, re.DOTALL)
                    for data_str in matches:
                        try:
                            data_parsed = datetime.strptime(data_str, '%d/%m/%Y').date()
                            if data_parsed.year >= hoje.year:
                                datas_encontradas.append(data_parsed)
                        except:
                            continue
                
                # Preferir data futura (vencimento) ao invés de data passada (emissão)
                if datas_encontradas:
                    # Ordenar: primeiro as datas >= hoje, depois as passadas
                    datas_futuras = [d for d in datas_encontradas if d >= hoje]
                    datas_passadas = [d for d in datas_encontradas if d < hoje]
                    
                    if datas_futuras:
                        # Pegar a data futura mais próxima
                        boleto['data_vencimento'] = min(datas_futuras).isoformat()
                    elif datas_passadas:
                        # Se só tiver passadas, pegar a mais recente
                        boleto['data_vencimento'] = max(datas_passadas).isoformat()
                
                # =====================================================
                # 4. BENEFICIÁRIO
                # =====================================================
                patterns_benef = [
                    r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\s*-\s*([A-Z][A-Za-zÀ-ÿ\s]+(?:LTDA|ALUMINIO|S\.?A\.?|ME|EPP|EIRELI))',  # CNPJ - NOME LTDA
                    r'[Bb]enefici[áa]rio\s*[\n\r]*\s*([A-Z][A-Za-zÀ-ÿ\s]+(?:LTDA|ALUMINIO|S\.?A\.?|ME|EPP|EIRELI))',
                ]
                
                for pattern in patterns_benef:
                    match = re.search(pattern, texto)
                    if match:
                        benef = match.group(1).strip()
                        benef = re.sub(r'\s+', ' ', benef).strip()
                        if len(benef) > 3:
                            boleto['beneficiario'] = benef[:100]
                            break
                
                # Verificar se encontrou dados válidos nesta página
                tem_dados = any([
                    boleto['codigo_barras'],
                    boleto['data_vencimento'],
                    boleto['valor']
                ])
                
                if tem_dados:
                    # Verificar se não é duplicata (mesmo código de barras)
                    codigo_existente = any(
                        b['codigo_barras'] == boleto['codigo_barras'] 
                        for b in boletos_encontrados 
                        if boleto['codigo_barras']
                    )
                    if not codigo_existente:
                        boletos_encontrados.append(boleto)
                        print(f"--- [BOLETO] Página {page_num}: Código={boleto['codigo_barras'][:20] if boleto['codigo_barras'] else 'N/A'}..., Valor={boleto['valor']}, Venc={boleto['data_vencimento']} ---")
        
        # Retornar resultado
        if len(boletos_encontrados) == 0:
            return {
                'sucesso': False,
                'multiplos': False,
                'quantidade': 0,
                'boletos': [],
                'codigo_barras': None,
                'data_vencimento': None,
                'valor': None,
                'beneficiario': None
            }
        elif len(boletos_encontrados) == 1:
            # Boleto único - manter compatibilidade
            b = boletos_encontrados[0]
            return {
                'sucesso': True,
                'multiplos': False,
                'quantidade': 1,
                'boletos': boletos_encontrados,
                'codigo_barras': b['codigo_barras'],
                'data_vencimento': b['data_vencimento'],
                'valor': b['valor'],
                'beneficiario': b['beneficiario']
            }
        else:
            # Múltiplos boletos encontrados
            print(f"--- [BOLETO] {len(boletos_encontrados)} boletos encontrados no PDF ---")
            return {
                'sucesso': True,
                'multiplos': True,
                'quantidade': len(boletos_encontrados),
                'boletos': boletos_encontrados,
                # Dados do primeiro boleto para compatibilidade
                'codigo_barras': boletos_encontrados[0]['codigo_barras'],
                'data_vencimento': boletos_encontrados[0]['data_vencimento'],
                'valor': boletos_encontrados[0]['valor'],
                'beneficiario': boletos_encontrados[0]['beneficiario']
            }
        
    except ImportError as e:
        print(f"--- [BOLETO] pdfplumber não instalado: {e} ---")
        return {'sucesso': False, 'multiplos': False, 'quantidade': 0, 'boletos': [], 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}
    except Exception as e:
        print(f"--- [BOLETO] Erro: {e} ---")
        traceback.print_exc()
        return {'sucesso': False, 'multiplos': False, 'quantidade': 0, 'boletos': [], 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}

# Função para verificar e criar alertas de boletos vencendo
def verificar_alertas_boletos():
    """Verifica boletos próximos do vencimento e cria notificações"""
    try:
        hoje = date.today()
        
        # Buscar boletos pendentes
        boletos = Boleto.query.filter(Boleto.status == 'Pendente').all()
        
        for boleto in boletos:
            dias = (boleto.data_vencimento - hoje).days
            obra = db.session.get(Obra, boleto.obra_id)
            obra_nome = obra.nome if obra else "Obra"
            
            # Boleto vencido
            if dias < 0 and not boleto.alerta_vencido:
                boleto.status = 'Vencido'
                boleto.alerta_vencido = True
                # Notificar masters e admins
                notificar_masters(
                    tipo='boleto_vencido',
                    titulo=f'🚨 Boleto VENCIDO - {obra_nome}',
                    mensagem=f'{boleto.descricao or boleto.beneficiario} - R$ {boleto.valor:.2f} venceu em {boleto.data_vencimento.strftime("%d/%m/%Y")}',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
            
            # Vence hoje
            elif dias == 0 and not boleto.alerta_hoje:
                boleto.alerta_hoje = True
                notificar_masters(
                    tipo='boleto_hoje',
                    titulo=f'🚨 Boleto vence HOJE - {obra_nome}',
                    mensagem=f'{boleto.descricao or boleto.beneficiario} - R$ {boleto.valor:.2f}',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
            
            # Vence em 3 dias
            elif dias <= 3 and dias > 0 and not boleto.alerta_3dias:
                boleto.alerta_3dias = True
                notificar_masters(
                    tipo='boleto_3dias',
                    titulo=f'⚠️ Boleto vence em {dias} dias - {obra_nome}',
                    mensagem=f'{boleto.descricao or boleto.beneficiario} - R$ {boleto.valor:.2f} vence em {boleto.data_vencimento.strftime("%d/%m/%Y")}',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
            
            # Vence em 7 dias
            elif dias <= 7 and dias > 3 and not boleto.alerta_7dias:
                boleto.alerta_7dias = True
                notificar_masters(
                    tipo='boleto_7dias',
                    titulo=f'🔔 Boleto vence em {dias} dias - {obra_nome}',
                    mensagem=f'{boleto.descricao or boleto.beneficiario} - R$ {boleto.valor:.2f} vence em {boleto.data_vencimento.strftime("%d/%m/%Y")}',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
        
        db.session.commit()
        print(f"--- [BOLETO] Verificação de alertas concluída ({len(boletos)} boletos) ---")
        
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] Falha ao verificar alertas de boletos: {e} ---")


# --- MODELOS DO BANCO DE DADOS (PRINCIPAIS) ---
class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))
    concluida = db.Column(db.Boolean, default=False, nullable=False)  # NOVO: Marca obra como concluída
    bdi = db.Column(db.Float, default=0)  # BDI do orçamento de engenharia (%)
    area = db.Column(db.Float, nullable=True)  # Área da obra em m²
    lancamentos = db.relationship('Lancamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    servicos = db.relationship('Servico', backref='obra', lazy=True, cascade="all, delete-orphan")
    orcamentos = db.relationship('Orcamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    notas_fiscais = db.relationship('NotaFiscal', backref='obra', lazy=True, cascade="all, delete-orphan")
    cronograma_items = db.relationship('CronogramaObra', backref='obra', lazy=True, cascade="all, delete-orphan")
    pagamentos_futuros = db.relationship('PagamentoFuturo', backref='obra', lazy=True, cascade="all, delete-orphan")
    pagamentos_parcelados = db.relationship('PagamentoParcelado', backref='obra', lazy=True, cascade="all, delete-orphan")
    diarios = db.relationship('DiarioObra', backref='obra', lazy=True, cascade="all, delete-orphan")
    boletos = db.relationship('Boleto', backref='obra', lazy=True, cascade="all, delete-orphan")
    orcamento_eng_etapas = db.relationship('OrcamentoEngEtapa', backref='obra', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        try:
            bdi_val = self.bdi if hasattr(self, 'bdi') and self.bdi is not None else 0
            area_val = self.area if hasattr(self, 'area') else None
        except:
            bdi_val = 0
            area_val = None
        return { 
            "id": self.id, 
            "nome": self.nome, 
            "cliente": self.cliente, 
            "concluida": self.concluida if hasattr(self, 'concluida') else False,
            "bdi": bdi_val,
            "area": area_val
        }

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
    concluido = db.Column(db.Boolean, default=False)  # NOVO: Marcar serviço como concluído
    data_conclusao = db.Column(db.Date, nullable=True)  # NOVO: Data da conclusão
    pagamentos = db.relationship('PagamentoServico', backref='servico', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self):
        return {
            "id": self.id, "obra_id": self.obra_id, "nome": self.nome,
            "responsavel": self.responsavel,
            "valor_global_mao_de_obra": self.valor_global_mao_de_obra,
            "valor_global_material": self.valor_global_material,
            "pix": self.pix,
            "concluido": self.concluido or False,
            "data_conclusao": self.data_conclusao.isoformat() if self.data_conclusao else None,
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
    
    # NOVOS CAMPOS - Condições de Pagamento
    data_vencimento = db.Column(db.Date, nullable=True)
    numero_parcelas = db.Column(db.Integer, nullable=True, default=1)
    periodicidade = db.Column(db.String(20), nullable=True, default='Mensal')  # Semanal, Quinzenal, Mensal
    
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
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "numero_parcelas": self.numero_parcelas or 1,
            "periodicidade": self.periodicidade or 'Mensal',
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
    # NOTA: Colunas pix e forma_pagamento serão adicionadas após ALTER TABLE no banco
    # pix = db.Column(db.String(255), nullable=True)
    # forma_pagamento = db.Column(db.String(20), nullable=True, default='PIX')
    
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

        # Buscar próxima parcela pendente diretamente do banco
        proxima_parcela = None
        proxima_parcela_numero = None
        proxima_parcela_vencimento = None
        valor_proxima_parcela = self.valor_parcela
        
        try:
            # Buscar primeira parcela com status diferente de 'Pago', ordenada por numero_parcela
            proxima_parcela = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == self.id,
                ParcelaIndividual.status != 'Pago'
            ).order_by(ParcelaIndividual.numero_parcela.asc()).first()
            
            if proxima_parcela:
                proxima_parcela_numero = proxima_parcela.numero_parcela
                proxima_parcela_vencimento = proxima_parcela.data_vencimento.isoformat() if proxima_parcela.data_vencimento else None
                valor_proxima_parcela = proxima_parcela.valor_parcela
                
                # Se for parcela 0 (entrada), exibir como "Entrada"
                if proxima_parcela_numero == 0:
                    proxima_parcela_numero = 0  # Manter como 0 para indicar entrada
        except Exception as e:
            print(f"[AVISO] Erro ao buscar próxima parcela: {e}")
            # Fallback: usar cálculo antigo
            proxima_parcela_numero = self.parcelas_pagas + 1
            if proxima_parcela_numero <= self.numero_parcelas:
                try:
                    if self.periodicidade == 'Semanal':
                        from datetime import timedelta
                        dias_incremento = (proxima_parcela_numero - 1) * 7
                        proxima_data = self.data_primeira_parcela + timedelta(days=dias_incremento)
                        proxima_parcela_vencimento = proxima_data.isoformat()
                    else:  # Mensal
                        proxima_data = add_months_safe(self.data_primeira_parcela, (proxima_parcela_numero - 1))
                        proxima_parcela_vencimento = proxima_data.isoformat()
                except:
                    pass
        
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
        # Tratar pix e forma_pagamento de forma defensiva (colunas podem não existir ainda)
        pix_value = None
        forma_pagamento_value = 'PIX'
        
        # Tentar acessar se existir
        try:
            if hasattr(self, 'pix'):
                pix_value = self.pix
        except:
            pass
        
        try:
            if hasattr(self, 'forma_pagamento'):
                forma_pagamento_value = self.forma_pagamento or 'PIX'
        except:
            pass
        
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "fornecedor": self.fornecedor,
            "segmento": segmento_value,
            "valor_total": self.valor_total,
            "numero_parcelas": self.numero_parcelas,
            "valor_parcela": self.valor_parcela,
            "valor_proxima_parcela": valor_proxima_parcela,
            "data_primeira_parcela": self.data_primeira_parcela.isoformat() if self.data_primeira_parcela else None,
            "periodicidade": self.periodicidade,
            "parcelas_pagas": self.parcelas_pagas,
            "status": self.status,
            "observacoes": self.observacoes,
            "pix": pix_value,
            "forma_pagamento": forma_pagamento_value,
            "proxima_parcela_numero": proxima_parcela_numero if proxima_parcela_numero is not None else None,
            "proxima_parcela_vencimento": proxima_parcela_vencimento,
            "servico_id": self.servico_id,
            "servico_nome": servico_nome
        }
    
# ----------------------------------------------------
class ParcelaIndividual(db.Model):
    """Modelo para armazenar valores individuais de cada parcela"""
    __tablename__ = 'parcela_individual'
    
    id = db.Column(db.Integer, primary_key=True)
    pagamento_parcelado_id = db.Column(db.Integer, db.ForeignKey('pagamento_parcelado_v2.id'), nullable=False, index=True)  # OTIMIZAÇÃO: Índice adicionado
    numero_parcela = db.Column(db.Integer, nullable=False)  # 1, 2, 3...
    valor_parcela = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False, index=True)  # OTIMIZAÇÃO: Índice adicionado
    status = db.Column(db.String(20), nullable=False, default='Previsto', index=True)  # OTIMIZAÇÃO: Índice adicionado
    data_pagamento = db.Column(db.Date, nullable=True)
    forma_pagamento = db.Column(db.String(50), nullable=True)  # PIX, Boleto, TED, Dinheiro, etc
    # NOTA: Coluna codigo_barras será adicionada após ALTER TABLE no banco
    # codigo_barras = db.Column(db.String(60), nullable=True)
    observacao = db.Column(db.String(255), nullable=True)
    
    # OTIMIZAÇÃO: Índice composto para consultas mais eficientes
    __table_args__ = (
        db.Index('idx_parcela_pagamento_numero', 'pagamento_parcelado_id', 'numero_parcela'),
    )
    
    pagamento_parcelado = db.relationship('PagamentoParcelado', backref=db.backref('parcelas_individuais', cascade='all, delete-orphan'))
    
    def to_dict(self):
        # Tratar codigo_barras de forma defensiva (coluna pode não existir ainda)
        codigo_barras_value = None
        try:
            if hasattr(self, 'codigo_barras'):
                codigo_barras_value = self.codigo_barras
        except:
            pass
        
        return {
            "id": self.id,
            "pagamento_parcelado_id": self.pagamento_parcelado_id,
            "numero_parcela": self.numero_parcela,
            "valor_parcela": self.valor_parcela,
            "data_vencimento": self.data_vencimento.isoformat(),
            "status": self.status,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "forma_pagamento": self.forma_pagamento,
            "codigo_barras": codigo_barras_value,
            "observacao": self.observacao
        }

# ===== MODELOS DO CAIXA DE OBRA =====
class CaixaObra(db.Model):
    """Caixa principal da obra para pequenas despesas"""
    __tablename__ = 'caixa_obra'
    
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False, unique=True)
    saldo_inicial = db.Column(db.Float, default=0, nullable=False)
    saldo_atual = db.Column(db.Float, default=0, nullable=False)
    mes_atual = db.Column(db.Integer, nullable=False)  # 1-12
    ano_atual = db.Column(db.Integer, nullable=False)  # 2025
    status = db.Column(db.String(20), default='Ativo', nullable=False)  # Ativo, Fechado
    criado_em = db.Column(db.DateTime, default=func.now())
    atualizado_em = db.Column(db.DateTime, default=func.now(), onupdate=func.now())
    
    # Relacionamentos
    obra = db.relationship('Obra', backref='caixa')
    movimentacoes = db.relationship('MovimentacaoCaixa', backref='caixa', lazy=True, cascade='all, delete-orphan')
    fechamentos = db.relationship('FechamentoCaixa', backref='caixa', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'saldo_inicial': self.saldo_inicial,
            'saldo_atual': self.saldo_atual,
            'mes_atual': self.mes_atual,
            'ano_atual': self.ano_atual,
            'status': self.status,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None
        }

class MovimentacaoCaixa(db.Model):
    """Movimentações (entradas e saídas) do caixa"""
    __tablename__ = 'movimentacao_caixa'
    
    id = db.Column(db.Integer, primary_key=True)
    caixa_id = db.Column(db.Integer, db.ForeignKey('caixa_obra.id'), nullable=False, index=True)
    data = db.Column(db.DateTime, nullable=False, default=func.now(), index=True)
    tipo = db.Column(db.String(10), nullable=False, index=True)  # 'Entrada' ou 'Saída'
    valor = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(500), nullable=False)
    comprovante_url = db.Column(db.Text, nullable=True)  # Base64 da imagem do comprovante
    observacoes = db.Column(db.Text, nullable=True)
    criado_por = db.Column(db.Integer, nullable=True)  # Sem FK para permitir exclusão de usuários
    criado_em = db.Column(db.DateTime, default=func.now())
    atualizado_em = db.Column(db.DateTime, default=func.now(), onupdate=func.now())
    
    def to_dict(self):
        return {
            'id': self.id,
            'caixa_id': self.caixa_id,
            'data': self.data.isoformat() if self.data else None,
            'tipo': self.tipo,
            'valor': self.valor,
            'descricao': self.descricao,
            'comprovante_url': self.comprovante_url,
            'observacoes': self.observacoes,
            'criado_por': self.criado_por,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None
        }

class FechamentoCaixa(db.Model):
    """Fechamento mensal do caixa com relatório"""
    __tablename__ = 'fechamento_caixa'
    
    id = db.Column(db.Integer, primary_key=True)
    caixa_id = db.Column(db.Integer, db.ForeignKey('caixa_obra.id'), nullable=False)
    mes = db.Column(db.Integer, nullable=False)  # 1-12
    ano = db.Column(db.Integer, nullable=False)  # 2025
    saldo_inicial = db.Column(db.Float, nullable=False)
    total_entradas = db.Column(db.Float, nullable=False)
    total_saidas = db.Column(db.Float, nullable=False)
    saldo_final = db.Column(db.Float, nullable=False)
    quantidade_movimentacoes = db.Column(db.Integer, nullable=False)
    quantidade_comprovantes = db.Column(db.Integer, nullable=False)
    pdf_url = db.Column(db.String(500), nullable=True)
    fechado_em = db.Column(db.DateTime, nullable=False, default=func.now())
    fechado_por = db.Column(db.Integer, nullable=True)  # Sem FK para permitir exclusão de usuários
    
    # Índice composto para consulta rápida por período
    __table_args__ = (
        db.Index('idx_fechamento_periodo', 'caixa_id', 'ano', 'mes'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'caixa_id': self.caixa_id,
            'mes': self.mes,
            'ano': self.ano,
            'saldo_inicial': self.saldo_inicial,
            'total_entradas': self.total_entradas,
            'total_saidas': self.total_saidas,
            'saldo_final': self.saldo_final,
            'quantidade_movimentacoes': self.quantidade_movimentacoes,
            'quantidade_comprovantes': self.quantidade_comprovantes,
            'pdf_url': self.pdf_url,
            'fechado_em': self.fechado_em.isoformat() if self.fechado_em else None,
            'fechado_por': self.fechado_por
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
    
    def to_dict(self, include_images_base64=False):
        """Retorna dict. Por padrao NAO inclui base64 das imagens"""
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
            'fotos': [img.to_dict(include_base64=include_images_base64) for img in self.imagens],
            'imagens': [img.to_dict(include_base64=include_images_base64) for img in self.imagens]
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
    
    def to_dict(self, include_base64=False):
        """Retorna dict. Por padrao NAO inclui base64 para economizar banda"""
        result = {
            'id': self.id,
            'diario_id': self.diario_id,
            'arquivo_nome': self.arquivo_nome,
            'legenda': self.legenda,
            'ordem': self.ordem,
            'criado_em': self.criado_em.strftime('%Y-%m-%d %H:%M:%S') if self.criado_em else None,
            'has_image': bool(self.arquivo_base64)
        }
        if include_base64:
            result['arquivo_base64'] = self.arquivo_base64
        return result
    
    def to_dict_full(self):
        """Retorna dict COM base64 - usar apenas quando necessario"""
        return {
            'id': self.id,
            'diario_id': self.diario_id,
            'arquivo_nome': self.arquivo_nome,
            'arquivo_base64': self.arquivo_base64,
            'legenda': self.legenda,
            'ordem': self.ordem,
            'criado_em': self.criado_em.strftime('%Y-%m-%d %H:%M:%S') if self.criado_em else None
        }


# ==============================================================================
# MÓDULO DE ORÇAMENTO DE ENGENHARIA
# ==============================================================================

class ServicoBase(db.Model):
    """
    Base de serviços de referência (estilo SINAPI/TCPO)
    Tabela readonly - populada com seed inicial
    """
    __tablename__ = 'servico_base'
    
    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(100), nullable=False)  # preliminares, fundacao, estrutura, etc
    codigo_ref = db.Column(db.String(50), nullable=True)  # Código SINAPI/TCPO se aplicável
    descricao = db.Column(db.String(500), nullable=False)
    unidade = db.Column(db.String(20), nullable=False)  # m², m³, m, kg, un, pt, vb
    
    # Tipo de composição
    tipo_composicao = db.Column(db.String(20), default='separado')  # separado | composto
    
    # Se separado
    preco_mao_obra = db.Column(db.Float, nullable=True)
    preco_material = db.Column(db.Float, nullable=True)
    
    # Se composto
    preco_unitario = db.Column(db.Float, nullable=True)
    rateio_mo = db.Column(db.Float, default=50)  # % estimado para MO
    rateio_mat = db.Column(db.Float, default=50)  # % estimado para Material
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'categoria': self.categoria,
            'codigo_ref': self.codigo_ref,
            'descricao': self.descricao,
            'unidade': self.unidade,
            'tipo_composicao': self.tipo_composicao,
            'preco_mao_obra': self.preco_mao_obra,
            'preco_material': self.preco_material,
            'preco_unitario': self.preco_unitario,
            'rateio_mo': self.rateio_mo,
            'rateio_mat': self.rateio_mat,
            'fonte': 'base'
        }


class ServicoUsuario(db.Model):
    """
    Serviços personalizados salvos pelo usuário
    Compartilhados por conta (todos os usuários da mesma empresa)
    """
    __tablename__ = 'servico_usuario'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Criador
    
    categoria = db.Column(db.String(100), nullable=True)
    descricao = db.Column(db.String(500), nullable=False)
    unidade = db.Column(db.String(20), nullable=False)
    
    # Tipo de composição
    tipo_composicao = db.Column(db.String(20), default='separado')
    
    # Se separado
    preco_mao_obra = db.Column(db.Float, nullable=True)
    preco_material = db.Column(db.Float, nullable=True)
    
    # Se composto
    preco_unitario = db.Column(db.Float, nullable=True)
    rateio_mo = db.Column(db.Float, default=50)
    rateio_mat = db.Column(db.Float, default=50)
    
    # Estatísticas de uso
    vezes_usado = db.Column(db.Integer, default=0)
    ultima_utilizacao = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'categoria': self.categoria,
            'descricao': self.descricao,
            'unidade': self.unidade,
            'tipo_composicao': self.tipo_composicao,
            'preco_mao_obra': self.preco_mao_obra,
            'preco_material': self.preco_material,
            'preco_unitario': self.preco_unitario,
            'rateio_mo': self.rateio_mo,
            'rateio_mat': self.rateio_mat,
            'vezes_usado': self.vezes_usado,
            'ultima_utilizacao': self.ultima_utilizacao.isoformat() if self.ultima_utilizacao else None,
            'fonte': 'usuario'
        }


class OrcamentoEngEtapa(db.Model):
    """
    Etapas do orçamento de engenharia (ex: Fundação, Estrutura, Alvenaria)
    """
    __tablename__ = 'orcamento_eng_etapa'
    
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    
    codigo = db.Column(db.String(20))  # 01, 02, 03...
    nome = db.Column(db.String(200), nullable=False)  # FUNDAÇÃO, ESTRUTURA...
    ordem = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamento com itens
    itens = db.relationship('OrcamentoEngItem', backref='etapa', lazy=True, cascade="all, delete-orphan")
    
    def to_dict(self, include_itens=True):
        result = {
            'id': self.id,
            'obra_id': self.obra_id,
            'codigo': self.codigo,
            'nome': self.nome,
            'ordem': self.ordem
        }
        if include_itens:
            result['itens'] = [item.to_dict() for item in self.itens]
        return result


class OrcamentoEngItem(db.Model):
    """
    Itens do orçamento de engenharia
    Cada item pode ser vinculado a um Serviço (Kanban)
    """
    __tablename__ = 'orcamento_eng_item'
    
    id = db.Column(db.Integer, primary_key=True)
    etapa_id = db.Column(db.Integer, db.ForeignKey('orcamento_eng_etapa.id'), nullable=False)
    
    codigo = db.Column(db.String(20))  # 01.01, 01.02...
    descricao = db.Column(db.String(500), nullable=False)
    unidade = db.Column(db.String(20), nullable=False)
    quantidade = db.Column(db.Float, default=0)
    
    # Tipo de composição
    tipo_composicao = db.Column(db.String(20), default='separado')  # separado | composto
    
    # Se separado
    preco_mao_obra = db.Column(db.Float, nullable=True)
    preco_material = db.Column(db.Float, nullable=True)
    
    # Se composto
    preco_unitario = db.Column(db.Float, nullable=True)
    rateio_mo = db.Column(db.Float, default=50)
    rateio_mat = db.Column(db.Float, default=50)
    
    # Vinculação com Serviço (Kanban)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='orcamento_itens', lazy=True)
    
    # Valores pagos (calculados a partir dos pagamentos do Serviço)
    valor_pago_mo = db.Column(db.Float, default=0)
    valor_pago_mat = db.Column(db.Float, default=0)
    
    ordem = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def calcular_totais(self):
        """Calcula totais do item baseado no tipo de composição"""
        if self.tipo_composicao == 'composto':
            total = (self.preco_unitario or 0) * (self.quantidade or 0)
            total_mo = total * (self.rateio_mo or 50) / 100
            total_mat = total * (self.rateio_mat or 50) / 100
        else:
            total_mo = (self.preco_mao_obra or 0) * (self.quantidade or 0)
            total_mat = (self.preco_material or 0) * (self.quantidade or 0)
            total = total_mo + total_mat
        
        return {
            'total_mao_obra': total_mo,
            'total_material': total_mat,
            'total': total
        }
    
    def to_dict(self):
        totais = self.calcular_totais()
        total_pago = (self.valor_pago_mo or 0) + (self.valor_pago_mat or 0)
        percentual = (total_pago / totais['total'] * 100) if totais['total'] > 0 else 0
        
        return {
            'id': self.id,
            'etapa_id': self.etapa_id,
            'codigo': self.codigo,
            'descricao': self.descricao,
            'unidade': self.unidade,
            'quantidade': self.quantidade,
            'tipo_composicao': self.tipo_composicao,
            'preco_mao_obra': self.preco_mao_obra,
            'preco_material': self.preco_material,
            'preco_unitario': self.preco_unitario,
            'rateio_mo': self.rateio_mo,
            'rateio_mat': self.rateio_mat,
            'servico_id': self.servico_id,
            'servico_nome': self.servico.nome if self.servico else None,
            'valor_pago_mo': self.valor_pago_mo or 0,
            'valor_pago_mat': self.valor_pago_mat or 0,
            'total_mao_obra': totais['total_mao_obra'],
            'total_material': totais['total_material'],
            'total': totais['total'],
            'total_pago': total_pago,
            'percentual_executado': round(percentual, 1),
            'ordem': self.ordem
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
        
        # CORREÇÃO: 4. Pagamentos Futuros (Cronograma Financeiro) - TODOS com status Previsto OU Pendente
        pagamentos_futuros_sum = db.session.query(
            PagamentoFuturo.obra_id,
            func.sum(PagamentoFuturo.valor).label('total_futuro')
        ).filter(
            PagamentoFuturo.status.in_(['Previsto', 'Pendente'])
        ).group_by(PagamentoFuturo.obra_id).subquery()
        
        # NOVO: 4b. Pagamentos Futuros SEM serviço (Despesas Extras)
        pagamentos_futuros_extra_sum = db.session.query(
            PagamentoFuturo.obra_id,
            func.sum(PagamentoFuturo.valor).label('total_futuro_extra')
        ).filter(
            PagamentoFuturo.status.in_(['Previsto', 'Pendente']),
            PagamentoFuturo.servico_id.is_(None)
        ).group_by(PagamentoFuturo.obra_id).subquery()
        
        # CORREÇÃO: 5. Parcelas Previstas (Cronograma Financeiro) - TODAS
        parcelas_previstas_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(ParcelaIndividual.status == 'Previsto') \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 5b. Parcelas SEM serviço (Despesas Extras)
        parcelas_extra_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_extra')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(
             ParcelaIndividual.status == 'Previsto',
             PagamentoParcelado.servico_id.is_(None)
         ) \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 5c. Parcelas PAGAS com serviço (para somar em valores pagos)
        parcelas_pagas_com_servico_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_pagas')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(
             ParcelaIndividual.status == 'Pago',
             PagamentoParcelado.servico_id.isnot(None)  # COM serviço
         ) \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 5d. Parcelas PAGAS SEM serviço (despesas extras pagas)
        parcelas_pagas_sem_servico_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_pagas_sem')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(
             ParcelaIndividual.status == 'Pago',
             PagamentoParcelado.servico_id.is_(None)  # SEM serviço
         ) \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 6a. Orçamento de Engenharia TOTAL por obra
        orcamento_eng_sum = db.session.query(
            OrcamentoEngEtapa.obra_id,
            func.sum(
                db.case(
                    (OrcamentoEngItem.tipo_composicao == 'separado',
                     OrcamentoEngItem.quantidade * (
                         func.coalesce(OrcamentoEngItem.preco_mao_obra, 0) +
                         func.coalesce(OrcamentoEngItem.preco_material, 0)
                     )),
                    else_=OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_unitario, 0)
                )
            ).label('total_orcamento_eng')
        ).select_from(OrcamentoEngItem) \
         .join(OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id) \
         .group_by(OrcamentoEngEtapa.obra_id) \
         .subquery()
        
        # NOVO: 6b. Valores de Serviços vinculados ao Orçamento de Engenharia (para evitar duplicação)
        servicos_orcamento_sum = db.session.query(
            OrcamentoEngEtapa.obra_id,
            func.sum(Servico.valor_global_mao_de_obra + Servico.valor_global_material).label('total_servicos_orcamento')
        ).select_from(OrcamentoEngItem) \
         .join(OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id) \
         .join(Servico, OrcamentoEngItem.servico_id == Servico.id) \
         .group_by(OrcamentoEngEtapa.obra_id) \
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
            func.coalesce(parcelas_previstas_sum.c.total_parcelas, 0).label('parcelas_previstas'),
            func.coalesce(pagamentos_futuros_extra_sum.c.total_futuro_extra, 0).label('futuro_extra'),
            func.coalesce(parcelas_extra_sum.c.total_parcelas_extra, 0).label('parcelas_extra'),
            func.coalesce(parcelas_pagas_com_servico_sum.c.total_parcelas_pagas, 0).label('parcelas_pagas_com_servico'),
            func.coalesce(parcelas_pagas_sem_servico_sum.c.total_parcelas_pagas_sem, 0).label('parcelas_pagas_sem_servico'),
            func.coalesce(orcamento_eng_sum.c.total_orcamento_eng, 0).label('orcamento_eng'),
            func.coalesce(servicos_orcamento_sum.c.total_servicos_orcamento, 0).label('servicos_orcamento')
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
        ).outerjoin(
            pagamentos_futuros_extra_sum, Obra.id == pagamentos_futuros_extra_sum.c.obra_id
        ).outerjoin(
            parcelas_extra_sum, Obra.id == parcelas_extra_sum.c.obra_id
        ).outerjoin(
            parcelas_pagas_com_servico_sum, Obra.id == parcelas_pagas_com_servico_sum.c.obra_id
        ).outerjoin(
            parcelas_pagas_sem_servico_sum, Obra.id == parcelas_pagas_sem_servico_sum.c.obra_id
        ).outerjoin(
            orcamento_eng_sum, Obra.id == orcamento_eng_sum.c.obra_id
        ).outerjoin(
            servicos_orcamento_sum, Obra.id == servicos_orcamento_sum.c.obra_id
        )

        # 7. Filtra permissões E status de conclusão
        mostrar_concluidas = request.args.get('mostrar_concluidas', 'false').lower() == 'true'
        
        if user.role == 'administrador':
            if mostrar_concluidas:
                obras_com_totais = obras_query.order_by(Obra.nome).all()
            else:
                obras_com_totais = obras_query.filter(
                    db.or_(Obra.concluida == False, Obra.concluida.is_(None))
                ).order_by(Obra.nome).all()
        else:
            base_query = obras_query.join(
                user_obra_association, Obra.id == user_obra_association.c.obra_id
            ).filter(
                user_obra_association.c.user_id == user.id
            )
            if mostrar_concluidas:
                obras_com_totais = base_query.order_by(Obra.nome).all()
            else:
                obras_com_totais = base_query.filter(
                    db.or_(Obra.concluida == False, Obra.concluida.is_(None))
                ).order_by(Obra.nome).all()

        # 8. Formata a Saída com os 4 KPIs
        resultados = []
        for obra, lanc_geral, lanc_pago, lanc_pendente, serv_budget_mo, serv_budget_mat, pag_pago, pag_pendente, futuro_previsto, parcelas_previstas, futuro_extra, parcelas_extra, parcelas_pagas_com_servico, parcelas_pagas_sem_servico, orcamento_eng, servicos_orcamento in obras_com_totais:
            
            # Calcular valores COM serviço
            futuro_com_servico = float(futuro_previsto) - float(futuro_extra)
            parcelas_com_servico = float(parcelas_previstas) - float(parcelas_extra)
            
            # KPI 1: Orçamento Total
            # = Serviços do Kanban (não vinculados ao orçamento) + Orçamento de Engenharia completo
            # Lógica: Subtrair do Kanban os serviços que vieram do orçamento para não duplicar
            total_servicos = float(serv_budget_mo) + float(serv_budget_mat)
            total_servicos_ajustado = max(0, total_servicos - float(servicos_orcamento))
            orcamento_total = total_servicos_ajustado + float(orcamento_eng)
            
            # KPI 2: Total Pago (Valores Efetivados)
            # Inclui: lançamentos + pagamentos de serviço + parcelas pagas COM serviço
            # NOTA: Parcelas pagas SEM serviço já estão em lanc_pago (Lancamento criado ao pagar)
            total_pago = float(lanc_pago) + float(pag_pago) + float(parcelas_pagas_com_servico)
            
            # KPI 3: Liberado para Pagamento (Fila) - Incluindo Cronograma Financeiro
            liberado_pagamento = (
                float(lanc_pendente) + 
                float(pag_pendente) + 
                float(futuro_previsto) + 
                float(parcelas_previstas)
            )
            
            # KPI 4: Despesas Extras (Pagamentos Fora da Planilha)
            despesas_extras = float(futuro_extra) + float(parcelas_extra)
            
            resultados.append({
                "id": obra.id,
                "nome": obra.nome,
                "cliente": obra.cliente,
                "concluida": obra.concluida or False,
                "orcamento_total": orcamento_total,
                "total_pago": total_pago,
                "liberado_pagamento": liberado_pagamento,
                "despesas_extras": despesas_extras
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
    """Cria uma nova obra e associa automaticamente o usuário criador"""
    print("--- [LOG] Rota /obras (POST) acessada ---")
    try:
        # Obter usuário atual
        current_user = get_current_user()
        if not current_user:
            return jsonify({"erro": "Usuário não encontrado"}), 404
        
        dados = request.json
        nova_obra = Obra(nome=dados['nome'], cliente=dados.get('cliente'))
        db.session.add(nova_obra)
        db.session.flush()  # Gera o ID da obra sem fazer commit final
        
        # CORREÇÃO: Associar automaticamente o usuário criador à obra
        if nova_obra not in current_user.obras_permitidas:
            current_user.obras_permitidas.append(nova_obra)
        
        db.session.commit()
        
        print(f"--- [LOG] Obra '{nova_obra.nome}' (ID={nova_obra.id}) criada e associada ao usuário {current_user.username} ---")
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
        
        # Total de Lançamentos SEM serviço vinculado (para evitar duplicação com orçamento de serviços)
        # Lançamentos COM serviço_id já estão contabilizados no orçamento do serviço (MO + Material)
        total_lancamentos_query = db.session.query(
            func.sum(Lancamento.valor_total).label('total_lanc')
        ).filter(
            Lancamento.obra_id == obra_id,
            Lancamento.servico_id.is_(None)  # CORREÇÃO: Apenas lançamentos SEM serviço
        ).first()
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
        # Pagamentos Futuros com status='Previsto' OU 'Pendente' (TODOS)
        pagamentos_futuros_previstos = db.session.query(
            func.sum(PagamentoFuturo.valor).label('total_futuro')
        ).filter(
            PagamentoFuturo.obra_id == obra_id,
            PagamentoFuturo.status.in_(['Previsto', 'Pendente'])
        ).first()
        
        # Pagamentos Futuros SEM serviço (Despesas Extras)
        pagamentos_futuros_sem_servico = db.session.query(
            func.sum(PagamentoFuturo.valor).label('total_futuro_extra')
        ).filter(
            PagamentoFuturo.obra_id == obra_id,
            PagamentoFuturo.status.in_(['Previsto', 'Pendente']),
            PagamentoFuturo.servico_id.is_(None)
        ).first()
        
        # Parcelas Individuais com status='Previsto' (TODAS)
        parcelas_previstas = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas')
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).first()
        
        # Parcelas SEM serviço (Despesas Extras)
        parcelas_sem_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_extra')
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto',
            PagamentoParcelado.servico_id.is_(None)
        ).first()
        
        total_futuros = float(pagamentos_futuros_previstos.total_futuro or 0.0)
        total_parcelas_previstas = float(parcelas_previstas.total_parcelas or 0.0)
        total_futuros_extra = float(pagamentos_futuros_sem_servico.total_futuro_extra or 0.0)
        total_parcelas_extra = float(parcelas_sem_servico.total_parcelas_extra or 0.0)
        
        # Calcular valores COM serviço (para somar ao orçamento)
        total_futuros_com_servico = total_futuros - total_futuros_extra
        total_parcelas_com_servico = total_parcelas_previstas - total_parcelas_extra
        
        # Logs de DEBUG para rastreamento
        print(f"--- [DEBUG KPI] obra_id={obra_id} ---")
        print(f"--- [DEBUG KPI] total_lancamentos: R$ {total_lancamentos:.2f} ---")
        print(f"--- [DEBUG KPI] total_budget_mo: R$ {total_budget_mo:.2f} ---")
        print(f"--- [DEBUG KPI] total_budget_mat: R$ {total_budget_mat:.2f} ---")
        print(f"--- [DEBUG KPI] total_futuros (PagamentoFuturo): R$ {total_futuros:.2f} ---")
        print(f"--- [DEBUG KPI] total_parcelas_previstas: R$ {total_parcelas_previstas:.2f} ---")
        print(f"--- [DEBUG KPI] total_futuros_com_servico: R$ {total_futuros_com_servico:.2f} ---")
        print(f"--- [DEBUG KPI] total_parcelas_com_servico: R$ {total_parcelas_com_servico:.2f} ---")
        print(f"--- [DEBUG KPI] total_futuros_extra (sem serviço): R$ {total_futuros_extra:.2f} ---")
        print(f"--- [DEBUG KPI] total_parcelas_extra (sem serviço): R$ {total_parcelas_extra:.2f} ---")
        
        # CORREÇÃO: Buscar parcelas PAGAS com serviço vinculado ANTES dos KPIs
        # Parcelas sem serviço NÃO devem ser somadas aqui pois já são contabilizadas via Lancamento criado
        parcelas_pagas_com_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_pagas')
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Pago',
            PagamentoParcelado.servico_id.isnot(None)  # COM serviço
        ).first()
        total_parcelas_pagas_com_servico = float(parcelas_pagas_com_servico.total_parcelas_pagas or 0.0)
        print(f"--- [DEBUG KPI] total_parcelas_pagas_com_servico: R$ {total_parcelas_pagas_com_servico:.2f} ---")
        
        # NOTA: Parcelas PAGAS SEM serviço NÃO são mais contadas aqui
        # Elas já são contabilizadas via Lancamento criado em marcar_parcela_paga()
        # Isso evita DUPLICAÇÃO
        print(f"--- [DEBUG KPI] parcelas_pagas_sem_servico: NÃO SOMADO (já está no Lancamento) ---")
        
        # === ORÇAMENTO DE ENGENHARIA ===
        # CORREÇÃO: Sempre usar os valores do Orçamento de Engenharia como fonte primária
        # Os serviços do Kanban vinculados são apenas para controle de pagamentos
        try:
            # Total COMPLETO do Orçamento de Engenharia (todos os itens)
            orcamento_eng_total = db.session.query(
                func.sum(
                    db.case(
                        (OrcamentoEngItem.tipo_composicao == 'separado',
                         OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_mao_obra, 0)),
                        else_=OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_unitario, 0) * 
                              func.coalesce(OrcamentoEngItem.rateio_mo, 50) / 100
                    )
                ).label('total_mo'),
                func.sum(
                    db.case(
                        (OrcamentoEngItem.tipo_composicao == 'separado',
                         OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_material, 0)),
                        else_=OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_unitario, 0) * 
                              func.coalesce(OrcamentoEngItem.rateio_mat, 50) / 100
                    )
                ).label('total_mat')
            ).join(OrcamentoEngEtapa).filter(
                OrcamentoEngEtapa.obra_id == obra_id
            ).first()
            
            total_orcamento_eng_mo = float(orcamento_eng_total.total_mo or 0.0)
            total_orcamento_eng_mat = float(orcamento_eng_total.total_mat or 0.0)
            total_orcamento_eng = total_orcamento_eng_mo + total_orcamento_eng_mat
            print(f"--- [DEBUG KPI] ORÇAMENTO ENG TOTAL: MO R$ {total_orcamento_eng_mo:.2f}, MAT R$ {total_orcamento_eng_mat:.2f} = R$ {total_orcamento_eng:.2f} ---")
            
            # Verificar serviços vinculados ao orçamento de engenharia
            # Para evitar duplicação, subtraímos do total_budget os valores de serviços que vieram do Orçamento
            servicos_do_orcamento = db.session.query(
                func.sum(Servico.valor_global_mao_de_obra).label('total_mo'),
                func.sum(Servico.valor_global_material).label('total_mat')
            ).join(OrcamentoEngItem, OrcamentoEngItem.servico_id == Servico.id).join(OrcamentoEngEtapa).filter(
                OrcamentoEngEtapa.obra_id == obra_id
            ).first()
            
            servicos_orcamento_mo = float(servicos_do_orcamento.total_mo or 0.0) if servicos_do_orcamento else 0.0
            servicos_orcamento_mat = float(servicos_do_orcamento.total_mat or 0.0) if servicos_do_orcamento else 0.0
            print(f"--- [DEBUG KPI] Serviços vinculados ao Orçamento: MO R$ {servicos_orcamento_mo:.2f}, MAT R$ {servicos_orcamento_mat:.2f} ---")
            
            # Remover dos totais do Kanban os valores que vieram do Orçamento de Engenharia
            # para não duplicar, já que vamos usar os valores do Orçamento como fonte primária
            total_budget_mo_ajustado = max(0, total_budget_mo - servicos_orcamento_mo)
            total_budget_mat_ajustado = max(0, total_budget_mat - servicos_orcamento_mat)
            print(f"--- [DEBUG KPI] Kanban ajustado (sem orçamento eng): MO R$ {total_budget_mo_ajustado:.2f}, MAT R$ {total_budget_mat_ajustado:.2f} ---")
            
        except Exception as e:
            print(f"--- [DEBUG KPI] Erro ao buscar Orçamento de Engenharia: {e} ---")
            import traceback
            traceback.print_exc()
            total_orcamento_eng = 0.0
            total_orcamento_eng_mo = 0.0
            total_orcamento_eng_mat = 0.0
            total_budget_mo_ajustado = total_budget_mo
            total_budget_mat_ajustado = total_budget_mat
        
        # KPI 1: ORÇAMENTO TOTAL
        # = Serviços do Kanban (não vinculados ao orçamento) + Orçamento de Engenharia completo
        kpi_orcamento_total = total_budget_mo_ajustado + total_budget_mat_ajustado + total_orcamento_eng
        print(f"--- [DEBUG KPI] ✅ ORÇAMENTO TOTAL = Kanban({total_budget_mo_ajustado + total_budget_mat_ajustado:.2f}) + OrcEng({total_orcamento_eng:.2f}) = R$ {kpi_orcamento_total:.2f} ---")
        
        # KPI 2: VALORES EFETIVADOS/PAGOS
        # Inclui: lançamentos pagos + pagamentos de serviço + parcelas pagas COM serviço
        # NOTA: Parcelas sem serviço já estão em total_pago_lancamentos (Lancamento criado ao pagar)
        kpi_valores_pagos = total_pago_lancamentos + total_pago_servicos + total_parcelas_pagas_com_servico
        print(f"--- [DEBUG KPI] ✅ VALORES PAGOS = R$ {kpi_valores_pagos:.2f} ---")
        
        # KPI 3: LIBERADO PARA PAGAMENTO (Valores pendentes = valor_total - valor_pago)
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
        
        # KPI 4: DESPESAS EXTRAS (Pagamentos Fora da Planilha de Custos)
        # Pagamentos futuros e parcelas SEM serviço vinculado
        kpi_despesas_extras = total_futuros_extra + total_parcelas_extra
        print(f"--- [DEBUG KPI] ✅ DESPESAS EXTRAS (fora da planilha) = R$ {kpi_despesas_extras:.2f} ---")
        
        # --- BOLETOS ---
        boletos_obra = Boleto.query.filter_by(obra_id=obra_id).all()
        
        # Boletos COM serviço vinculado = são forma de PAGAMENTO do serviço, NÃO orçamento adicional
        # O orçamento do serviço já está em valor_global_mao_de_obra + valor_global_material
        total_boletos_com_servico = sum(b.valor or 0 for b in boletos_obra if b.vinculado_servico_id)
        total_boletos_com_servico_pendentes = sum(b.valor or 0 for b in boletos_obra if b.vinculado_servico_id and b.status in ['Pendente', 'Vencido'])
        total_boletos_com_servico_pagos = sum(b.valor or 0 for b in boletos_obra if b.vinculado_servico_id and b.status == 'Pago')
        
        # Boletos SEM serviço vinculado = despesas extras
        total_boletos_sem_servico_pendentes = sum(b.valor or 0 for b in boletos_obra if not b.vinculado_servico_id and b.status in ['Pendente', 'Vencido'])
        total_boletos_sem_servico_pagos = sum(b.valor or 0 for b in boletos_obra if not b.vinculado_servico_id and b.status == 'Pago')
        
        # Atualizar KPIs com boletos
        # CORREÇÃO: Boletos com serviço NÃO aumentam orçamento - são forma de pagamento do serviço
        kpi_valores_pagos += total_boletos_com_servico_pagos + total_boletos_sem_servico_pagos  # TODOS boletos pagos vão para valores pagos
        kpi_liberado_pagamento += total_boletos_com_servico_pendentes  # Boletos pendentes com serviço vão para liberado
        kpi_despesas_extras += total_boletos_sem_servico_pendentes + total_boletos_sem_servico_pagos  # Boletos sem serviço vão para despesas extras
        
        print(f"--- [DEBUG KPI] 📄 BOLETOS: com_servico={total_boletos_com_servico:.2f} (pend={total_boletos_com_servico_pendentes:.2f}, pago={total_boletos_com_servico_pagos:.2f}), sem_servico_pend={total_boletos_sem_servico_pendentes:.2f}, sem_servico_pago={total_boletos_sem_servico_pagos:.2f} ---")

        # Sumário de Segmentos (Apenas Lançamentos Gerais)
        total_por_segmento = db.session.query(
            Lancamento.tipo,
            func.sum(Lancamento.valor_total)
        ).filter(
            Lancamento.obra_id == obra_id, 
            Lancamento.servico_id.is_(None)
        ).group_by(Lancamento.tipo).all()
        
        # <--- Enviando os 4 KPIs corretos (ATUALIZADO v2) -->
        sumarios_dict = {
            "orcamento_total": kpi_orcamento_total,        # Card 1 - Orçamento Total (Vermelho)
            "valores_pagos": kpi_valores_pagos,            # Card 2 - Valores Pagos (Azul/Índigo)
            "liberado_pagamento": kpi_liberado_pagamento,  # Card 3 - Liberado p/ Pagamento (Verde)
            "despesas_extras": kpi_despesas_extras,        # Card 4 - Despesas Extras (Roxo/Amarelo)
            
            # Totais para o gráfico de distribuição de custos
            # Inclui: Kanban ajustado + Orçamento de Engenharia
            "total_mao_obra": total_budget_mo_ajustado + total_orcamento_eng_mo,
            "total_material": total_budget_mat_ajustado + total_orcamento_eng_mat,
            
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
        
        # --- INCLUIR PARCELAS INDIVIDUAIS PAGAS ---
        # CORREÇÃO: Apenas parcelas COM serviço, pois parcelas SEM serviço já criaram Lançamento
        parcelas_pagas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Pago',
            PagamentoParcelado.servico_id.isnot(None)  # Apenas COM serviço
        ).all()
        
        print(f"--- [DEBUG] Parcelas pagas COM serviço encontradas: {len(parcelas_pagas)} ---")
        
        for parcela in parcelas_pagas:
            pag_parcelado = parcela.pagamento_parcelado
            servico_nome = None
            if pag_parcelado.servico_id:
                servico = db.session.get(Servico, pag_parcelado.servico_id)
                servico_nome = servico.nome if servico else None
            
            historico_unificado.append({
                "id": f"parcela-{parcela.id}",
                "tipo_registro": "parcela_individual",
                "data": parcela.data_pagamento or parcela.data_vencimento,
                "data_vencimento": parcela.data_vencimento,
                "descricao": f"{pag_parcelado.descricao} ({parcela.numero_parcela}/{pag_parcelado.numero_parcelas})",
                "tipo": pag_parcelado.segmento or "Material",
                "valor_total": float(parcela.valor_parcela or 0.0),
                "valor_pago": float(parcela.valor_parcela or 0.0),
                "status": "Pago",
                "pix": None,
                "servico_id": pag_parcelado.servico_id,
                "servico_nome": servico_nome,
                "pagamento_parcelado_id": pag_parcelado.id,
                "parcela_id": parcela.id,
                "prioridade": 0,
                "fornecedor": pag_parcelado.fornecedor
            })
        
        # CORREÇÃO: Incluir parcelas SEM serviço que podem não ter criado Lançamento
        # (backup para casos onde a criação do lançamento falhou)
        # NOTA: Parcelas pagas SEM serviço NÃO são adicionadas aqui
        # Elas já aparecem via Lancamento criado em marcar_parcela_paga()
        # Isso evita DUPLICAÇÃO no histórico
        print(f"--- [DEBUG] Parcelas pagas SEM serviço: não adicionadas (já têm Lancamento) ---")
        
        # --- INCLUIR BOLETOS PAGOS NO HISTÓRICO ---
        for boleto in boletos_obra:
            if boleto.status == 'Pago':
                servico_nome = None
                if boleto.vinculado_servico_id:
                    servico = db.session.get(Servico, boleto.vinculado_servico_id)
                    servico_nome = servico.nome if servico else None
                
                historico_unificado.append({
                    "id": f"boleto-{boleto.id}",
                    "tipo_registro": "boleto",
                    "data": boleto.data_pagamento or boleto.data_vencimento,
                    "data_vencimento": boleto.data_vencimento,
                    "descricao": f"📄 Boleto: {boleto.descricao or boleto.beneficiario or 'Sem descrição'}",
                    "tipo": "Boleto",
                    "valor_total": float(boleto.valor or 0.0),
                    "valor_pago": float(boleto.valor or 0.0),
                    "status": "Pago",
                    "pix": boleto.codigo_barras,
                    "servico_id": boleto.vinculado_servico_id,
                    "servico_nome": servico_nome,
                    "boleto_id": boleto.id,
                    "prioridade": 0,
                    "fornecedor": boleto.beneficiario
                })
        
        # Re-ordenar após incluir parcelas
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
            
            # Lancamentos vinculados ao serviço
            lancamentos_servico = [l for l in todos_lancamentos if l.servico_id == s.id]
            
            # COMPROMETIDO (valor_total de todos os lancamentos)
            gastos_vinculados_mo = sum(
                float(l.valor_total or 0.0) for l in lancamentos_servico
                if l.tipo == 'Mão de Obra'
            )
            gastos_vinculados_mat = sum(
                float(l.valor_total or 0.0) for l in lancamentos_servico 
                if l.tipo == 'Material'
            )
            serv_dict['total_gastos_vinculados_mo'] = gastos_vinculados_mo
            serv_dict['total_gastos_vinculados_mat'] = gastos_vinculados_mat
            
            # NOVO: Incluir lancamentos pagos no histórico de pagamentos do serviço
            # (Esses valores já são contados no total_gastos, então só adicionamos ao histórico)
            lancamentos_pagos = [l for l in lancamentos_servico if l.status == 'Pago']
            for lanc in lancamentos_pagos:
                serv_dict['pagamentos'].append({
                    "id": f"lanc-{lanc.id}",
                    "data": lanc.data.isoformat() if lanc.data else None,
                    "tipo_pagamento": "mao_de_obra" if lanc.tipo == 'Mão de Obra' else "material",
                    "fornecedor": lanc.fornecedor,
                    "valor_total": lanc.valor_total,
                    "valor_pago": lanc.valor_pago,
                    "status": "Pago",
                    "descricao": lanc.descricao,
                    "is_lancamento": True
                })
            
            # Incluir parcelas pagas de pagamentos parcelados vinculados ao serviço
            parcelas_do_servico = ParcelaIndividual.query.join(PagamentoParcelado).filter(
                PagamentoParcelado.servico_id == s.id,
                ParcelaIndividual.status == 'Pago'
            ).all()
            
            parcelas_list = []
            for parcela in parcelas_do_servico:
                pag = parcela.pagamento_parcelado
                parcelas_list.append({
                    "id": parcela.id,
                    "data": (parcela.data_pagamento or parcela.data_vencimento).isoformat() if (parcela.data_pagamento or parcela.data_vencimento) else None,
                    "tipo_pagamento": "mao_de_obra" if pag.segmento == "Mão de Obra" else "material",
                    "fornecedor": pag.fornecedor,
                    "valor_total": parcela.valor_parcela,
                    "valor_pago": parcela.valor_parcela,
                    "status": "Pago",
                    "descricao": f"{pag.descricao} ({parcela.numero_parcela}/{pag.numero_parcelas})",
                    "is_parcela": True
                })
            
            # Adicionar parcelas ao histórico de pagamentos do serviço
            if parcelas_list:
                serv_dict['pagamentos'] = serv_dict.get('pagamentos', []) + parcelas_list
            
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
    print(f"--- [LOG] Rota /obras/{obra_id} (DELETE) acessada ---")
    try:
        obra = Obra.query.get_or_404(obra_id)
        
        # 1. Deletar parcelas individuais dos pagamentos parcelados desta obra
        pagamentos_parcelados_ids = [p.id for p in PagamentoParcelado.query.filter_by(obra_id=obra_id).all()]
        if pagamentos_parcelados_ids:
            ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id.in_(pagamentos_parcelados_ids)
            ).delete(synchronize_session=False)
            print(f"--- [LOG] Parcelas individuais deletadas para obra {obra_id} ---")
        
        # 2. Deletar pagamentos parcelados
        PagamentoParcelado.query.filter_by(obra_id=obra_id).delete(synchronize_session=False)
        print(f"--- [LOG] Pagamentos parcelados deletados para obra {obra_id} ---")
        
        # 3. Deletar CaixaObra associado (não tem cascade automático)
        CaixaObra.query.filter_by(obra_id=obra_id).delete(synchronize_session=False)
        
        # 4. Deletar a obra (cascade deleta o resto)
        db.session.delete(obra)
        db.session.commit()
        print(f"--- [LOG] Obra {obra_id} deletada com sucesso ---")
        return jsonify({"sucesso": "Obra deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


@app.route('/obras/<int:obra_id>/concluir', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def concluir_obra(obra_id):
    """Marca uma obra como concluída ou reabre"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/concluir (PATCH) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        dados = request.get_json() or {}
        
        # Se não passar 'concluida', alterna o estado atual
        if 'concluida' in dados:
            obra.concluida = dados['concluida']
        else:
            obra.concluida = not (obra.concluida or False)
        
        db.session.commit()
        
        status_texto = "concluída" if obra.concluida else "reaberta"
        print(f"--- [LOG] Obra '{obra.nome}' marcada como {status_texto} ---")
        
        return jsonify({
            "sucesso": f"Obra {status_texto} com sucesso!",
            "obra": obra.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/concluir: {str(e)}\n{error_details} ---")
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
            
            # --- NOTIFICAÇÃO PARA MASTERS ---
            obra = Obra.query.get(obra_id)
            obra_nome = obra.nome if obra else f"Obra {obra_id}"
            notificar_masters(
                tipo='pagamento_inserido',
                titulo='Novo pagamento agendado',
                mensagem=f'{user.username} agendou pagamento "{dados["descricao"]}" de R$ {valor_total:.2f} na obra {obra_nome}',
                obra_id=obra_id,
                item_id=novo_pagamento_futuro.id,
                item_type='pagamento_futuro',
                usuario_origem_id=user.id
            )
            
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
            
            # --- NOTIFICAÇÃO PARA MASTERS ---
            obra = Obra.query.get(obra_id)
            obra_nome = obra.nome if obra else f"Obra {obra_id}"
            notificar_masters(
                tipo='pagamento_inserido',
                titulo='Novo pagamento registrado',
                mensagem=f'{user.username} registrou pagamento "{dados["descricao"]}" de R$ {valor_total:.2f} na obra {obra_nome}',
                obra_id=obra_id,
                item_id=novo_lancamento.id,
                item_type='lancamento',
                usuario_origem_id=user.id
            )
            
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

@app.route('/lancamentos/<int:lancamento_id>', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def atualizar_lancamento_parcial(lancamento_id):
    """Atualização parcial de lançamento (ex: vincular serviço)"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        user = get_current_user()
        claims = get_jwt()
        user_role = claims.get('role')
        
        if user_role not in ['administrador', 'master']:
            return jsonify({"erro": "Acesso negado"}), 403
        
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json
        
        # Atualizar apenas os campos fornecidos
        if 'servico_id' in dados:
            lancamento.servico_id = dados['servico_id'] if dados['servico_id'] else None
        if 'fornecedor' in dados:
            lancamento.fornecedor = dados['fornecedor']
        if 'prioridade' in dados:
            lancamento.prioridade = int(dados['prioridade'])
        
        db.session.commit()
        print(f"--- [LOG] Lançamento {lancamento_id} atualizado parcialmente ---")
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_lancamento(lancamento_id):
    """
    Deleta um lançamento com regras específicas:
    - Lançamentos PAGOS só podem ser deletados por usuários MASTER
    - Lançamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
    - Remove também notas fiscais associadas ao lançamento
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
        
        # REGRA: Se está PAGO, ADMINISTRADOR ou MASTER podem deletar
        if is_pago and user_role not in ['administrador', 'master']:
            print(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Apenas administradores e masters podem excluir pagamentos já executados (PAGOS)."
            }), 403
        
        # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
        if not is_pago and user_role not in ['administrador', 'master']:
            print(f"--- [LOG] ❌ Tentativa de deletar lançamento por usuário {user_role} (sem permissão) ---")
            return jsonify({
                "erro": "Acesso negado: Permissão insuficiente para excluir este lançamento."
            }), 403
        
        # 1. Remover notas fiscais associadas a este lançamento
        notas_removidas = NotaFiscal.query.filter_by(
            item_id=lancamento_id,
            item_type='lancamento'
        ).delete()
        if notas_removidas > 0:
            print(f"--- [LOG] {notas_removidas} nota(s) fiscal(is) removida(s) do lançamento {lancamento_id} ---")
        
        # 2. Deletar o lançamento
        db.session.delete(lancamento)
        db.session.commit()
        
        print(f"--- [LOG] ✅ Lançamento {lancamento_id} (Status: {lancamento.status}) e dados associados deletados com sucesso pelo usuário {user_role} ---")
        return jsonify({"sucesso": "Lançamento e dados associados deletados"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500


# --- ROTAS DE SERVIÇO (Atualizadas) ---

@app.route('/obras/<int:obra_id>/servicos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master', 'comum']) 
def add_servico(obra_id):
    # ... (código inalterado) ...
    print(f"--- [LOG] Rota /obras/{obra_id}/servicos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.json
        
        # Tratar valores vazios ou nulos
        def safe_float(value, default=0.0):
            if value is None or value == '':
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        novo_servico = Servico(
            obra_id=obra_id,
            nome=dados['nome'],
            responsavel=dados.get('responsavel', ''),
            valor_global_mao_de_obra=safe_float(dados.get('valor_global_mao_de_obra')),
            valor_global_material=safe_float(dados.get('valor_global_material')),
            pix=dados.get('pix')
        )
        db.session.add(novo_servico)
        db.session.commit()
        
        # --- NOTIFICAÇÕES ---
        obra = Obra.query.get(obra_id)
        obra_nome = obra.nome if obra else f"Obra {obra_id}"
        
        # Notificar todos os operadores (comum) com acesso à obra
        notificar_operadores_obra(
            obra_id=obra_id,
            tipo='servico_criado',
            titulo=f'Novo serviço criado',
            mensagem=f'O serviço "{novo_servico.nome}" foi criado na obra {obra_nome}',
            item_id=novo_servico.id,
            item_type='servico',
            usuario_origem_id=user.id
        )
        
        # Notificar todos os masters
        notificar_masters(
            tipo='servico_criado',
            titulo=f'Novo serviço criado',
            mensagem=f'{user.username} criou o serviço "{novo_servico.nome}" na obra {obra_nome}',
            obra_id=obra_id,
            item_id=novo_servico.id,
            item_type='servico',
            usuario_origem_id=user.id
        )
        
        # Notificar administradores
        notificar_administradores(
            tipo='servico_criado',
            titulo=f'Novo serviço criado',
            mensagem=f'{user.username} criou o serviço "{novo_servico.nome}" na obra {obra_nome}',
            obra_id=obra_id,
            item_id=novo_servico.id,
            item_type='servico',
            usuario_origem_id=user.id
        )
        
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
        
        # Tratar valores vazios ou nulos
        def safe_float(value, default=0.0):
            if value is None or value == '':
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        servico.nome = dados.get('nome', servico.nome)
        servico.responsavel = dados.get('responsavel', servico.responsavel)
        servico.valor_global_mao_de_obra = safe_float(dados.get('valor_global_mao_de_obra'), servico.valor_global_mao_de_obra or 0.0)
        servico.valor_global_material = safe_float(dados.get('valor_global_material'), servico.valor_global_material or 0.0)
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


@app.route('/servicos/<int:servico_id>/concluir', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def toggle_servico_concluido(servico_id):
    """
    Marca/desmarca um serviço como concluído
    Um serviço pode estar concluído mesmo sem ter sido totalmente pago
    """
    if request.method == 'OPTIONS':
        return '', 200
        
    print(f"--- [LOG] Rota /servicos/{servico_id}/concluir (PATCH) acessada ---")
    try:
        user = get_current_user()
        servico = Servico.query.get_or_404(servico_id)
        
        if not user_has_access_to_obra(user, servico.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json or {}
        
        # Toggle ou valor específico
        if 'concluido' in dados:
            servico.concluido = dados['concluido']
        else:
            # Toggle: se não especificado, inverte o valor atual
            servico.concluido = not (servico.concluido or False)
        
        # Definir data de conclusão
        if servico.concluido:
            servico.data_conclusao = dados.get('data_conclusao', date.today()) if isinstance(dados.get('data_conclusao'), date) else date.fromisoformat(dados['data_conclusao']) if dados.get('data_conclusao') else date.today()
        else:
            servico.data_conclusao = None
        
        db.session.commit()
        
        print(f"--- [LOG] Serviço {servico_id} marcado como {'CONCLUÍDO' if servico.concluido else 'NÃO CONCLUÍDO'} ---")
        
        return jsonify({
            "sucesso": f"Serviço {'marcado como concluído' if servico.concluido else 'desmarcado como concluído'}",
            "servico": servico.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/{servico_id}/concluir (PATCH): {str(e)}\n{error_details} ---")
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

# ===== ROTA PARA LIMPAR PAGAMENTOS DUPLICADOS DE PARCELAS =====
@app.route('/obras/<int:obra_id>/limpar-pagamentos-parcelas-duplicados', methods=['POST'])
@jwt_required()
def limpar_pagamentos_parcelas_duplicados(obra_id):
    """
    Remove PagamentoServico que foram criados a partir de parcelas (antes da correção).
    Isso evita duplicação no histórico do serviço, já que as parcelas pagas
    agora aparecem via query de ParcelaIndividual.
    """
    try:
        user = get_current_user()
        
        if user.role not in ['master', 'administrador']:
            return jsonify({"erro": "Apenas administradores podem executar esta ação"}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão para esta obra"}), 403
        
        # Buscar todos os pagamentos parcelados COM serviço desta obra
        pagamentos_parcelados = PagamentoParcelado.query.filter(
            PagamentoParcelado.obra_id == obra_id,
            PagamentoParcelado.servico_id.isnot(None)
        ).all()
        
        pagamentos_removidos = 0
        detalhes = []
        
        for pag_parcelado in pagamentos_parcelados:
            # Para cada parcela PAGA, verificar se existe um PagamentoServico duplicado
            parcelas_pagas = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                ParcelaIndividual.status == 'Pago'
            ).all()
            
            for parcela in parcelas_pagas:
                # Buscar PagamentoServico com mesmo valor e serviço
                pagamentos_servico = PagamentoServico.query.filter(
                    PagamentoServico.servico_id == pag_parcelado.servico_id,
                    PagamentoServico.valor_total == parcela.valor_parcela
                ).all()
                
                for pag_serv in pagamentos_servico:
                    # Verificar se a data corresponde ou se é próxima
                    if pag_serv.data_pagamento and parcela.data_pagamento:
                        diff_dias = abs((pag_serv.data_pagamento - parcela.data_pagamento).days)
                        if diff_dias <= 1:  # Mesma data ou 1 dia de diferença
                            detalhes.append({
                                "id": pag_serv.id,
                                "valor": float(pag_serv.valor_total),
                                "data": pag_serv.data_pagamento.isoformat() if pag_serv.data_pagamento else None,
                                "parcela": f"{pag_parcelado.descricao} ({parcela.numero_parcela}/{pag_parcelado.numero_parcelas})"
                            })
                            db.session.delete(pag_serv)
                            pagamentos_removidos += 1
                            break
        
        db.session.commit()
        
        return jsonify({
            "mensagem": f"Limpeza concluída! {pagamentos_removidos} pagamentos duplicados removidos.",
            "pagamentos_removidos": pagamentos_removidos,
            "detalhes": detalhes
        })
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


# ===== ROTA PARA DELETAR PAGAMENTO DE SERVIÇO =====
@app.route('/servicos/<int:servico_id>/pagamentos/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_pagamento_servico(servico_id, pagamento_id):
    """
    Deleta um pagamento de serviço com regras específicas:
    - Pagamentos PAGOS só podem ser deletados por usuários MASTER
    - Pagamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
    """
    print(f"--- [LOG] Rota /servicos/{servico_id}/pagamentos/{pagamento_id} (DELETE) acessada ---")
    
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        pagamento = PagamentoServico.query.filter_by(
            id=pagamento_id, 
            servico_id=servico_id
        ).first()
        
        if not pagamento:
            # Tentar buscar apenas pelo ID
            pagamento = db.session.get(PagamentoServico, pagamento_id)
        
        if not pagamento:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        # Obter o papel do usuário
        claims = get_jwt()
        user_role = claims.get('role')
        
        # Verificar se o pagamento está PAGO (completamente executado)
        is_pago = (pagamento.valor_pago or 0) >= (pagamento.valor_total or 0)
        
        # REGRA: Se está PAGO, ADMINISTRADOR ou MASTER podem deletar
        if is_pago and user_role not in ['administrador', 'master']:
            print(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO de serviço por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Apenas administradores e masters podem excluir pagamentos já executados (PAGOS)."
            }), 403
        
        # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
        if not is_pago and user_role not in ['administrador', 'master']:
            print(f"--- [LOG] ❌ Tentativa de deletar pagamento de serviço por usuário {user_role} (sem permissão) ---")
            return jsonify({
                "erro": "Acesso negado: Permissão insuficiente para excluir este pagamento."
            }), 403
        
        db.session.delete(pagamento)
        db.session.commit()
        
        print(f"--- [LOG] ✅ Pagamento de serviço {pagamento_id} deletado com sucesso pelo usuário {user_role} ---")
        return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /servicos/.../pagamentos (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# ===============================================================================

# Rota para deletar pagamento de serviço pelo ID (usado pelo histórico de pagamentos)
@app.route('/pagamentos-servico/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_pagamento_servico_por_id(pagamento_id):
    """
    Deleta um pagamento de serviço pelo ID.
    Regras:
    - Pagamentos PAGOS só podem ser deletados por usuários MASTER
    - Pagamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
    - Remove também notas fiscais associadas ao pagamento
    """
    print(f"--- [LOG] Rota /pagamentos-servico/{pagamento_id} (DELETE) acessada ---")
    
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        pagamento = db.session.get(PagamentoServico, pagamento_id)
        
        if not pagamento:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        # Obter o papel do usuário
        claims = get_jwt()
        user_role = claims.get('role')
        
        # Verificar se o pagamento está PAGO
        is_pago = (pagamento.valor_pago or 0) >= (pagamento.valor_total or 0)
        
        # REGRA: Se está PAGO, ADMINISTRADOR ou MASTER podem deletar
        if is_pago and user_role not in ['administrador', 'master']:
            print(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Apenas administradores e masters podem excluir pagamentos já executados."
            }), 403
        
        # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
        if not is_pago and user_role not in ['administrador', 'master']:
            print(f"--- [LOG] ❌ Tentativa de deletar pagamento por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Permissão insuficiente."
            }), 403
        
        # 1. Remover notas fiscais associadas a este pagamento
        notas_removidas = NotaFiscal.query.filter_by(
            item_id=pagamento_id,
            item_type='pagamento_servico'
        ).delete()
        if notas_removidas > 0:
            print(f"--- [LOG] {notas_removidas} nota(s) fiscal(is) removida(s) do pagamento {pagamento_id} ---")
        
        # 2. Remover o pagamento
        db.session.delete(pagamento)
        db.session.commit()
        
        print(f"--- [LOG] ✅ Pagamento de serviço {pagamento_id} deletado pelo usuário {user_role} ---")
        return jsonify({"sucesso": "Pagamento e dados associados deletados com sucesso"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /pagamentos-servico/{pagamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

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
@check_permission(roles=['administrador', 'master', 'comum'])  # Operador e Admin podem cadastrar
def add_orcamento(obra_id):
    """Cria uma nova solicitação de compra"""
    print(f"--- [LOG] Rota /obras/{obra_id}/orcamentos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.form
        
        # Processar data de vencimento
        data_vencimento = None
        if dados.get('data_vencimento'):
            try:
                data_vencimento = datetime.strptime(dados['data_vencimento'], '%Y-%m-%d').date()
            except:
                pass
        
        novo_orcamento = Orcamento(
            obra_id=obra_id,
            descricao=dados['descricao'],
            fornecedor=dados.get('fornecedor') or None,
            valor=float(dados.get('valor', 0)),
            dados_pagamento=dados.get('dados_pagamento') or None,
            tipo=dados['tipo'],
            status='Pendente',
            observacoes=dados.get('observacoes') or None, 
            servico_id=int(dados['servico_id']) if dados.get('servico_id') else None,
            # NOVOS CAMPOS
            data_vencimento=data_vencimento,
            numero_parcelas=int(dados.get('numero_parcelas', 1)) if dados.get('numero_parcelas') else 1,
            periodicidade=dados.get('periodicidade') or 'Mensal'
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
        
        # --- NOTIFICAÇÃO PARA MASTERS ---
        obra = Obra.query.get(obra_id)
        obra_nome = obra.nome if obra else f"Obra {obra_id}"
        valor_formatado = f"R$ {novo_orcamento.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        notificar_masters(
            tipo='orcamento_pendente',
            titulo='📋 Nova solicitação aguardando aprovação',
            mensagem=f'{user.username} cadastrou "{novo_orcamento.descricao}" ({valor_formatado}) na obra {obra_nome}',
            obra_id=obra_id,
            item_id=novo_orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
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
@check_permission(roles=['master'])  # APENAS Master pode aprovar
def aprovar_orcamento(orcamento_id):
    """
    Master aprova a solicitação com 1 clique.
    Sistema cria automaticamente o Pagamento Futuro/Parcelado.
    Valores são somados ao serviço vinculado (se houver).
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /orcamentos/{orcamento_id}/aprovar (POST) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if orcamento.status != 'Pendente':
            return jsonify({"erro": f"Esta solicitação já foi processada. Status atual: {orcamento.status}"}), 400

        # 1. Marcar como aprovado
        orcamento.status = 'Aprovado'
        
        # 2. Se tem serviço vinculado, somar valor ao orçamento do serviço
        if orcamento.servico_id:
            servico = Servico.query.get(orcamento.servico_id)
            if servico:
                tipo_orcamento = orcamento.tipo or ''
                if 'material' in tipo_orcamento.lower():
                    servico.valor_global_material = (servico.valor_global_material or 0) + (orcamento.valor or 0)
                    print(f"[LOG] ✅ Valor somado ao material do serviço {servico.id}: +R$ {orcamento.valor}")
                else:
                    servico.valor_global_mao_de_obra = (servico.valor_global_mao_de_obra or 0) + (orcamento.valor or 0)
                    print(f"[LOG] ✅ Valor somado à MO do serviço {servico.id}: +R$ {orcamento.valor}")
        
        # 3. Criar Pagamento Futuro automaticamente
        valor_orcamento = orcamento.valor or 0.0
        descricao_pagamento = f"{orcamento.descricao}"
        
        # Usar dados de pagamento da solicitação (ou defaults)
        data_vencimento = orcamento.data_vencimento if hasattr(orcamento, 'data_vencimento') and orcamento.data_vencimento else date.today() + timedelta(days=30)
        numero_parcelas = orcamento.numero_parcelas if hasattr(orcamento, 'numero_parcelas') and orcamento.numero_parcelas else 1
        periodicidade = orcamento.periodicidade if hasattr(orcamento, 'periodicidade') and orcamento.periodicidade else 'Mensal'
        
        if numero_parcelas == 1:
            # Criar Pagamento Futuro Único
            pagamento_futuro = PagamentoFuturo(
                obra_id=orcamento.obra_id,
                descricao=descricao_pagamento,
                fornecedor=orcamento.fornecedor,
                valor=valor_orcamento,
                data_vencimento=data_vencimento,
                status='Previsto',
                servico_id=orcamento.servico_id,
                observacoes=f"Solicitação #{orcamento.id} aprovada"
            )
            db.session.add(pagamento_futuro)
            print(f"[LOG] ✅ Pagamento Futuro criado: R$ {valor_orcamento:.2f} para {data_vencimento}")
            
        else:
            # Criar Pagamento Parcelado
            valor_parcela = valor_orcamento / numero_parcelas
            
            pagamento_parcelado = PagamentoParcelado(
                obra_id=orcamento.obra_id,
                descricao=descricao_pagamento,
                fornecedor=orcamento.fornecedor,
                servico_id=orcamento.servico_id,
                valor_total=valor_orcamento,
                numero_parcelas=numero_parcelas,
                valor_parcela=valor_parcela,
                data_primeira_parcela=data_vencimento,
                periodicidade=periodicidade,
                parcelas_pagas=0,
                status='Ativo',
                observacoes=f"Solicitação #{orcamento.id} aprovada"
            )
            db.session.add(pagamento_parcelado)
            db.session.flush()
            
            # Criar parcelas individuais
            for i in range(numero_parcelas):
                if periodicidade == 'Semanal':
                    data_parcela = data_vencimento + timedelta(weeks=i)
                elif periodicidade == 'Quinzenal':
                    data_parcela = data_vencimento + timedelta(weeks=i*2)
                else:  # Mensal
                    mes = data_vencimento.month + i
                    ano = data_vencimento.year + (mes - 1) // 12
                    mes = ((mes - 1) % 12) + 1
                    try:
                        data_parcela = data_vencimento.replace(year=ano, month=mes)
                    except ValueError:
                        import calendar
                        ultimo_dia = calendar.monthrange(ano, mes)[1]
                        data_parcela = data_vencimento.replace(year=ano, month=mes, day=min(data_vencimento.day, ultimo_dia))
                
                parcela = ParcelaIndividual(
                    pagamento_parcelado_id=pagamento_parcelado.id,
                    numero_parcela=i + 1,
                    valor_parcela=valor_parcela,
                    data_vencimento=data_parcela,
                    status='Previsto'
                )
                db.session.add(parcela)
            
            print(f"[LOG] ✅ Pagamento Parcelado criado: {numero_parcelas}x R$ {valor_parcela:.2f}")
        
        db.session.commit()
        
        # 4. NOTIFICAÇÕES
        obra = Obra.query.get(orcamento.obra_id)
        obra_nome = obra.nome if obra else f"Obra {orcamento.obra_id}"
        
        # Notificar operadores
        notificar_operadores_obra(
            obra_id=orcamento.obra_id,
            tipo='orcamento_aprovado',
            titulo='✅ Solicitação aprovada',
            mensagem=f'A solicitação "{orcamento.descricao}" foi aprovada e enviada para pagamento',
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar administradores
        notificar_administradores(
            tipo='orcamento_aprovado',
            titulo='💰 Nova compra autorizada',
            mensagem=f'Solicitação "{orcamento.descricao}" - R$ {valor_orcamento:,.2f} adicionada ao cronograma financeiro',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar masters
        notificar_masters(
            tipo='orcamento_aprovado',
            titulo='✅ Solicitação aprovada',
            mensagem=f'{user.username} aprovou "{orcamento.descricao}" na obra {obra_nome}',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        msg_sucesso = f"Solicitação aprovada! Pagamento de R$ {valor_orcamento:,.2f} adicionado ao cronograma."
        if numero_parcelas > 1:
            msg_sucesso = f"Solicitação aprovada! {numero_parcelas}x R$ {valor_orcamento/numero_parcelas:,.2f} adicionado ao cronograma."
        
        return jsonify({"sucesso": msg_sucesso}), 200
        
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
                servico_id=None  # ⚠️ Não vincular ao serviço - vincular apenas via PagamentoServico quando pago
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
        
        # --- NOTIFICAÇÕES ---
        obra = Obra.query.get(orcamento.obra_id)
        obra_nome = obra.nome if obra else f"Obra {orcamento.obra_id}"
        
        # Notificar operadores da obra
        notificar_operadores_obra(
            obra_id=orcamento.obra_id,
            tipo='orcamento_rejeitado',
            titulo='Orçamento rejeitado',
            mensagem=f'O orçamento "{orcamento.descricao}" foi rejeitado por {user.username}',
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar administradores
        notificar_administradores(
            tipo='orcamento_rejeitado',
            titulo='Orçamento rejeitado',
            mensagem=f'O orçamento "{orcamento.descricao}" foi rejeitado na obra {obra_nome}',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar masters
        notificar_masters(
            tipo='orcamento_rejeitado',
            titulo='Orçamento rejeitado',
            mensagem=f'{user.username} rejeitou o orçamento "{orcamento.descricao}" na obra {obra_nome}',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
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
        username_backup = user.username  # Guardar para log
        
        # Master pode excluir qualquer usuário (exceto a si mesmo, já verificado acima)
        claims = get_jwt()
        current_user_role = claims.get('role')
        
        if user.role == 'master' and current_user_role != 'master':
            return jsonify({"erro": "Apenas usuários MASTER podem excluir outros MASTER."}), 403

        print(f"--- [LOG] Limpando referências do usuário '{username_backup}' (ID: {user_id}) ---")
        
        # Lista de tabelas/colunas para limpar (SET NULL)
        tabelas_para_limpar = [
            ("diario_obra", "criado_por"),
            ("movimentacao_caixa", "criado_por"),
            ("fechamento_caixa", "fechado_por"),
            ("lancamento", "criado_por"),
            ("pagamento_servico", "criado_por"),
            ("nota_fiscal", "criado_por"),
        ]
        
        for tabela, coluna in tabelas_para_limpar:
            try:
                result = db.session.execute(
                    db.text(f"UPDATE {tabela} SET {coluna} = NULL WHERE {coluna} = :uid"),
                    {"uid": user_id}
                )
                db.session.commit()
                print(f"   ✅ {tabela}.{coluna} limpo ({result.rowcount} registros)")
            except Exception as e:
                db.session.rollback()
                print(f"   ⚠️ {tabela}.{coluna}: {str(e)[:50]}")
        
        # Remover associações de user_obra
        try:
            result = db.session.execute(
                db.text("DELETE FROM user_obra_association WHERE user_id = :uid"),
                {"uid": user_id}
            )
            db.session.commit()
            print(f"   ✅ user_obra_association removido ({result.rowcount} registros)")
        except Exception as e:
            db.session.rollback()
            print(f"   ⚠️ user_obra_association: {str(e)[:50]}")
        
        # Recarregar o usuário (pode ter sido invalidado pelos commits)
        user = User.query.get(user_id)
        if not user:
            return jsonify({"erro": "Usuário não encontrado após limpeza."}), 404
        
        # Agora excluir o usuário
        db.session.delete(user)
        db.session.commit()
        
        print(f"--- [LOG] ✅ Usuário '{username_backup}' (ID: {user_id}) foi deletado com sucesso ---")
        return jsonify({"sucesso": f"Usuário {username_backup} deletado com sucesso."}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/users/{user_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500
# --- FIM DA NOVA ROTA ---
# ---------------------------------------------------

# --- ROTAS DE NOTIFICAÇÕES ---
@app.route('/notificacoes', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_notificacoes():
    """Lista notificações do usuário logado"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        
        # Parâmetros opcionais
        apenas_nao_lidas = request.args.get('apenas_nao_lidas', 'false').lower() == 'true'
        limite = request.args.get('limite', 50, type=int)
        
        query = Notificacao.query.filter_by(usuario_destino_id=current_user_id)
        
        if apenas_nao_lidas:
            query = query.filter_by(lida=False)
        
        notificacoes = query.order_by(Notificacao.created_at.desc()).limit(limite).all()
        
        return jsonify([n.to_dict() for n in notificacoes]), 200
    except Exception as e:
        print(f"--- [ERRO] GET /notificacoes: {e} ---")
        return jsonify({"erro": str(e)}), 500

@app.route('/notificacoes/count', methods=['GET', 'OPTIONS'])
@jwt_required()
def contar_notificacoes():
    """Retorna apenas o contador de notificações não lidas"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        count = Notificacao.query.filter_by(
            usuario_destino_id=current_user_id,
            lida=False
        ).count()
        
        return jsonify({"count": count}), 200
    except Exception as e:
        print(f"--- [ERRO] GET /notificacoes/count: {e} ---")
        return jsonify({"erro": str(e)}), 500

@app.route('/notificacoes/<int:notificacao_id>/lida', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def marcar_notificacao_lida(notificacao_id):
    """Marca uma notificação como lida ou não lida"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        
        notificacao = Notificacao.query.get_or_404(notificacao_id)
        
        # Verificar se pertence ao usuário
        if notificacao.usuario_destino_id != current_user_id:
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json() or {}
        lida = data.get('lida', True)  # Por padrão marca como lida
        
        notificacao.lida = lida
        db.session.commit()
        
        return jsonify(notificacao.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] PATCH /notificacoes/{notificacao_id}/lida: {e} ---")
        return jsonify({"erro": str(e)}), 500

@app.route('/notificacoes/marcar-todas-lidas', methods=['POST', 'OPTIONS'])
@jwt_required()
def marcar_todas_lidas():
    """Marca todas as notificações do usuário como lidas"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        
        Notificacao.query.filter_by(
            usuario_destino_id=current_user_id,
            lida=False
        ).update({'lida': True})
        
        db.session.commit()
        
        return jsonify({"sucesso": "Todas as notificações foram marcadas como lidas"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] POST /notificacoes/marcar-todas-lidas: {e} ---")
        return jsonify({"erro": str(e)}), 500

@app.route('/notificacoes/limpar-lidas', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def limpar_notificacoes_lidas():
    """Remove todas as notificações lidas do usuário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        
        deleted = Notificacao.query.filter_by(
            usuario_destino_id=current_user_id,
            lida=True
        ).delete()
        
        db.session.commit()
        
        return jsonify({"sucesso": f"{deleted} notificações removidas"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] DELETE /notificacoes/limpar-lidas: {e} ---")
        return jsonify({"erro": str(e)}), 500

@app.route('/notificacoes/<int:notificacao_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_notificacao(notificacao_id):
    """Remove uma notificação específica"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        
        notificacao = Notificacao.query.get_or_404(notificacao_id)
        
        # Verificar se pertence ao usuário
        if notificacao.usuario_destino_id != current_user_id:
            return jsonify({"erro": "Acesso negado"}), 403
        
        db.session.delete(notificacao)
        db.session.commit()
        
        return jsonify({"sucesso": "Notificação removida"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] DELETE /notificacoes/{notificacao_id}: {e} ---")
        return jsonify({"erro": str(e)}), 500

# --- ROTA PARA ALTERAR ROLE DE USUÁRIO ---
@app.route('/admin/users/<int:user_id>/role', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['master'])
def alterar_role_usuario(user_id):
    """Permite ao master alterar o role de qualquer usuário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        data = request.get_json()
        novo_role = data.get('role')
        
        if novo_role not in ['master', 'administrador', 'comum']:
            return jsonify({"erro": "Role inválido. Use: master, administrador ou comum"}), 400
        
        user = User.query.get_or_404(user_id)
        role_anterior = user.role
        
        user.role = novo_role
        db.session.commit()
        
        print(f"--- [LOG] Role do usuário '{user.username}' alterado de '{role_anterior}' para '{novo_role}' ---")
        
        return jsonify({
            "sucesso": f"Role alterado para {novo_role}",
            "user": user.to_dict()
        }), 200
    except Exception as e:
        db.session.rollback()
        print(f"--- [ERRO] PATCH /admin/users/{user_id}/role: {e} ---")
        return jsonify({"erro": str(e)}), 500

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
        pagamentos_futuros = PagamentoFuturo.query.filter(PagamentoFuturo.obra_id == obra_id, PagamentoFuturo.status.in_(['Previsto', 'Pendente'])).all()
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
        
        # Calcular pagamentos futuros/parcelas COM serviço
        futuros_com_servico = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is not None)
        parcelas_com_servico = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                   if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is not None)
        
        # Orçamento total inclui serviços + pagamentos COM serviço
        orcamento_total = orcamento_total_servicos + futuros_com_servico + parcelas_com_servico
        
        valores_pagos_lancamentos = sum((l.valor_pago or 0) for l in lancamentos)
        valores_pagos_servicos = sum(
            sum((p.valor_pago or 0) for p in s.pagamentos)
            for s in servicos
        )
        valores_pagos = valores_pagos_lancamentos + valores_pagos_servicos
        
        # Despesas extras = futuros/parcelas SEM serviço
        despesas_extras_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is None)
        despesas_extras_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                       if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is None)
        
        despesas_extras_total = despesas_extras_futuros + despesas_extras_parcelas
        custo_real_previsto = orcamento_total + despesas_extras_total
        falta_pagar = custo_real_previsto - valores_pagos
        
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
        
        # === SEÇÃO 1: RESUMO FINANCEIRO COMPLETO ===
        elements.append(Paragraph("<b>1. RESUMO FINANCEIRO COMPLETO</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        # Subtítulo: Orçamento e Custos
        elements.append(Paragraph("<b>ORÇAMENTO E CUSTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        data_orcamento = [
            ['Descrição', 'Valor'],
            ['Orçamento Original (Serviços)', formatar_real(orcamento_total)],
            ['Despesas Extras (Fora da Planilha)', formatar_real(despesas_extras_total)],
        ]
        
        table_orcamento = Table(data_orcamento, colWidths=[10*cm, 6*cm])
        table_orcamento.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_orcamento)
        
        # Linha de total (Custo Real Previsto)
        data_custo_real = [
            ['CUSTO REAL PREVISTO', formatar_real(custo_real_previsto)]
        ]
        table_custo_real = Table(data_custo_real, colWidths=[10*cm, 6*cm])
        table_custo_real.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_custo_real)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Situação de Pagamentos
        elements.append(Paragraph("<b>SITUAÇÃO DE PAGAMENTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        # Calcular liberado (TODAS as parcelas/pagamentos previstos, com ou sem serviço)
        liberado_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros)
        liberado_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas)
        liberado_pagamento = liberado_futuros + liberado_parcelas
        
        data_pagamentos = [
            ['Descrição', 'Valor'],
            ['Valores Já Pagos', formatar_real(valores_pagos)],
            ['Liberado p/ Pagamento (Previsto)', formatar_real(liberado_pagamento)],
        ]
        
        table_pagamentos = Table(data_pagamentos, colWidths=[10*cm, 6*cm])
        table_pagamentos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_pagamentos)
        
        # Linha de total (Falta Pagar)
        data_falta = [
            ['FALTA PAGAR PARA CONCLUIR', formatar_real(falta_pagar)]
        ]
        table_falta = Table(data_falta, colWidths=[10*cm, 6*cm])
        table_falta.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_falta)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Análise de Execução
        elements.append(Paragraph("<b>ANÁLISE DE EXECUÇÃO</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        perc_executado = (valores_pagos / custo_real_previsto * 100) if custo_real_previsto > 0 else 0
        perc_sobre_orcamento = (valores_pagos / orcamento_total * 100) if orcamento_total > 0 else 0
        variacao_extras = (despesas_extras_total / orcamento_total * 100) if orcamento_total > 0 else 0
        
        data_analise = [
            ['Indicador', 'Valor'],
            ['Percentual Executado (sobre custo real)', f"{perc_executado:.1f}%"],
            ['Percentual sobre Orçamento Original', f"{perc_sobre_orcamento:.1f}%"],
            ['Variação (Despesas Extras)', f"+{variacao_extras:.1f}%"],
        ]
        
        table_analise = Table(data_analise, colWidths=[10*cm, 6*cm])
        table_analise.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_analise)
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


# --- RELATÓRIO DE PAGAMENTOS PDF ---
@app.route('/obras/<int:obra_id>/relatorio/pagamentos-pdf', methods=['GET', 'OPTIONS'])
@jwt_required()
def gerar_relatorio_pagamentos_pdf(obra_id):
    """Gera relatório PDF completo com análise financeira da obra"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"--- [LOG] Rota /obras/{obra_id}/relatorio/pagamentos-pdf (GET) acessada ---")
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
        pagamentos_futuros = PagamentoFuturo.query.filter(PagamentoFuturo.obra_id == obra_id, PagamentoFuturo.status.in_(['Previsto', 'Pendente'])).all()
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
        
        # Calcular pagamentos futuros/parcelas COM serviço
        futuros_com_servico = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is not None)
        parcelas_com_servico = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                   if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is not None)
        
        # Orçamento total inclui serviços + pagamentos COM serviço
        orcamento_total = orcamento_total_servicos + futuros_com_servico + parcelas_com_servico
        
        valores_pagos_lancamentos = sum((l.valor_pago or 0) for l in lancamentos)
        valores_pagos_servicos = sum(
            sum((p.valor_pago or 0) for p in s.pagamentos)
            for s in servicos
        )
        valores_pagos = valores_pagos_lancamentos + valores_pagos_servicos
        
        # Despesas extras = futuros/parcelas SEM serviço
        despesas_extras_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is None)
        despesas_extras_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                       if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is None)
        
        despesas_extras_total = despesas_extras_futuros + despesas_extras_parcelas
        custo_real_previsto = orcamento_total + despesas_extras_total
        falta_pagar = custo_real_previsto - valores_pagos
        
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
        
        # === SEÇÃO 1: RESUMO FINANCEIRO COMPLETO ===
        elements.append(Paragraph("<b>1. RESUMO FINANCEIRO COMPLETO</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        # Subtítulo: Orçamento e Custos
        elements.append(Paragraph("<b>ORÇAMENTO E CUSTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        data_orcamento = [
            ['Descrição', 'Valor'],
            ['Orçamento Original (Serviços)', formatar_real(orcamento_total)],
            ['Despesas Extras (Fora da Planilha)', formatar_real(despesas_extras_total)],
        ]
        
        table_orcamento = Table(data_orcamento, colWidths=[10*cm, 6*cm])
        table_orcamento.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_orcamento)
        
        # Linha de total (Custo Real Previsto)
        data_custo_real = [
            ['CUSTO REAL PREVISTO', formatar_real(custo_real_previsto)]
        ]
        table_custo_real = Table(data_custo_real, colWidths=[10*cm, 6*cm])
        table_custo_real.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_custo_real)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Situação de Pagamentos
        elements.append(Paragraph("<b>SITUAÇÃO DE PAGAMENTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        # Calcular liberado (TODAS as parcelas/pagamentos previstos, com ou sem serviço)
        liberado_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros)
        liberado_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas)
        liberado_pagamento = liberado_futuros + liberado_parcelas
        
        data_pagamentos = [
            ['Descrição', 'Valor'],
            ['Valores Já Pagos', formatar_real(valores_pagos)],
            ['Liberado p/ Pagamento (Previsto)', formatar_real(liberado_pagamento)],
        ]
        
        table_pagamentos = Table(data_pagamentos, colWidths=[10*cm, 6*cm])
        table_pagamentos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_pagamentos)
        
        # Linha de total (Falta Pagar)
        data_falta = [
            ['FALTA PAGAR PARA CONCLUIR', formatar_real(falta_pagar)]
        ]
        table_falta = Table(data_falta, colWidths=[10*cm, 6*cm])
        table_falta.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_falta)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Análise de Execução
        elements.append(Paragraph("<b>ANÁLISE DE EXECUÇÃO</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        perc_executado = (valores_pagos / custo_real_previsto * 100) if custo_real_previsto > 0 else 0
        perc_sobre_orcamento = (valores_pagos / orcamento_total * 100) if orcamento_total > 0 else 0
        variacao_extras = (despesas_extras_total / orcamento_total * 100) if orcamento_total > 0 else 0
        
        data_analise = [
            ['Indicador', 'Valor'],
            ['Percentual Executado (sobre custo real)', f"{perc_executado:.1f}%"],
            ['Percentual sobre Orçamento Original', f"{perc_sobre_orcamento:.1f}%"],
            ['Variação (Despesas Extras)', f"+{variacao_extras:.1f}%"],
        ]
        
        table_analise = Table(data_analise, colWidths=[10*cm, 6*cm])
        table_analise.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_analise)
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
        response.headers['Content-Disposition'] = f'attachment; filename=relatorio_pagamentos_{obra.nome.replace(" ", "_")}.pdf'
        
        print(f"--- [LOG] Relatório de pagamentos (completo) gerado para obra {obra_id} ---")
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/relatorio/pagamentos-pdf (GET): {str(e)}\n{error_details} ---")
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
        
        resultado = []
        
        # 1. Pagamentos Futuros (cadastrados pelo botão azul) - 1 query
        pagamentos_futuros = PagamentoFuturo.query.filter_by(obra_id=obra_id).order_by(PagamentoFuturo.data_vencimento).all()
        for p in pagamentos_futuros:
            resultado.append(p.to_dict())
        
        # 2. Pagamentos de Serviços com saldo pendente - OTIMIZADO: 1 query com JOIN
        pagamentos_servico_pendentes = db.session.query(
            PagamentoServico, Servico.nome.label('servico_nome')
        ).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_pago < PagamentoServico.valor_total,
            PagamentoServico.data_vencimento.isnot(None)
        ).all()
        
        for pag_serv, servico_nome in pagamentos_servico_pendentes:
            valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
            if valor_pendente > 0:
                resultado.append({
                    'id': f'servico-{pag_serv.id}',
                    'tipo_origem': 'servico',
                    'pagamento_servico_id': pag_serv.id,
                    'servico_id': pag_serv.servico_id,
                    'servico_nome': servico_nome,
                    'descricao': f"{servico_nome} - {pag_serv.tipo_pagamento.replace('_', ' ').title()}",
                    'fornecedor': pag_serv.fornecedor,
                    'valor': valor_pendente,
                    'data_vencimento': pag_serv.data_vencimento.isoformat(),
                    'status': 'Previsto',
                    'periodicidade': None
                })
        
        # Ordenar por data de vencimento
        resultado.sort(key=lambda x: x.get('data_vencimento', '9999-12-31'))
        
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
    """Marca um pagamento futuro como pago e move para o histórico ou serviço"""
    try:
        print(f"\n{'='*80}")
        print(f"💰 INÍCIO: marcar_pagamento_futuro_pago")
        print(f"   obra_id={obra_id}, pagamento_id={pagamento_id}")
        print(f"{'='*80}")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoFuturo, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        if pagamento.status == 'Pago':
            return jsonify({"mensagem": "Pagamento já está marcado como pago"}), 200
        
        print(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        print(f"      - servico_id: {pagamento.servico_id}")
        print(f"      - tipo: {pagamento.tipo}")
        print(f"      - valor: R$ {pagamento.valor}")
        
        data_pagamento = date.today()
        
        # ===== LÓGICA CORRIGIDA: Verificar se tem vínculo com serviço =====
        
        # CASO 1: Pagamento vinculado a SERVIÇO
        if pagamento.servico_id:
            servico = db.session.get(Servico, pagamento.servico_id)
            if servico:
                print(f"   📋 Pagamento vinculado ao serviço '{servico.nome}'")
                
                # Determinar tipo_pagamento
                if pagamento.tipo == 'Mão de Obra':
                    tipo_pagamento = 'mao_de_obra'
                elif pagamento.tipo == 'Material':
                    tipo_pagamento = 'material'
                else:
                    tipo_pagamento = 'material'  # default
                
                print(f"      - tipo_pagamento determinado: {tipo_pagamento}")
                
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
                    fornecedor=pagamento.fornecedor,
                    pix=pagamento.pix
                )
                db.session.add(novo_pag_servico)
                db.session.flush()
                
                print(f"   ✅ PagamentoServico criado com ID={novo_pag_servico.id}")
                
                # Recalcular percentual do serviço
                pagamentos_serv = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                pagamentos_mao_de_obra = [p for p in pagamentos_serv if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_material = [p for p in pagamentos_serv if p.tipo_pagamento == 'material']
                
                if servico.valor_global_mao_de_obra > 0:
                    total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                    servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                    print(f"   📊 Percentual MO atualizado: {servico.percentual_conclusao_mao_obra:.1f}%")
                
                if servico.valor_global_material > 0:
                    total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                    servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                    print(f"   📊 Percentual Material atualizado: {servico.percentual_conclusao_material:.1f}%")
                
                # DELETE o PagamentoFuturo
                db.session.delete(pagamento)
                
                # Commit das alterações
                db.session.commit()
                
                print(f"   🎉 SUCESSO: Pagamento vinculado ao serviço '{servico.nome}' e marcado como pago")
                print(f"{'='*80}\n")
                
                return jsonify({
                    "mensagem": f"Pagamento vinculado ao serviço '{servico.nome}' e marcado como pago",
                    "pagamento_servico_id": novo_pag_servico.id
                }), 200
            else:
                print(f"   ⚠️ Serviço {pagamento.servico_id} não encontrado, criando lançamento genérico")
        
        # CASO 2: Pagamento SEM vínculo com serviço
        print(f"   📄 Criando lançamento no histórico (sem vínculo de serviço)")
        
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
        
        # Commit das alterações
        db.session.commit()
        
        print(f"   ✅ Lançamento criado com ID={novo_lancamento.id}")
        print(f"   🎉 SUCESSO: Pagamento movido para o histórico")
        print(f"{'='*80}\n")
        
        return jsonify({
            "mensagem": "Pagamento marcado como pago e movido para o histórico com sucesso",
            "lancamento_id": novo_lancamento.id
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"\n{'='*80}")
        print(f"❌ ERRO em marcar_pagamento_futuro_pago:")
        print(f"   {str(e)}")
        print(f"\nStack trace:")
        print(error_details)
        print(f"{'='*80}\n")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- PAGAMENTOS PARCELADOS ---
@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados', methods=['GET'])
@jwt_required()
def listar_pagamentos_parcelados(obra_id):
    """Lista todos os pagamentos parcelados de uma obra - OTIMIZADO"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Query única com eager loading das parcelas
        pagamentos = PagamentoParcelado.query.filter_by(obra_id=obra_id).order_by(PagamentoParcelado.data_primeira_parcela).all()
        
        # Buscar todas as parcelas de uma vez só - 1 query
        pagamento_ids = [p.id for p in pagamentos]
        if pagamento_ids:
            todas_parcelas = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id.in_(pagamento_ids)
            ).order_by(ParcelaIndividual.pagamento_parcelado_id, ParcelaIndividual.numero_parcela).all()
            
            # Agrupar parcelas por pagamento_parcelado_id
            parcelas_por_pagamento = {}
            for parcela in todas_parcelas:
                if parcela.pagamento_parcelado_id not in parcelas_por_pagamento:
                    parcelas_por_pagamento[parcela.pagamento_parcelado_id] = []
                parcelas_por_pagamento[parcela.pagamento_parcelado_id].append(parcela)
        else:
            parcelas_por_pagamento = {}
        
        # Montar resultado
        resultado = []
        for pag in pagamentos:
            pag_dict = pag.to_dict()
            parcelas = parcelas_por_pagamento.get(pag.id, [])
            
            if parcelas:
                # Encontrar a próxima parcela não paga
                proxima_parcela = next((p for p in parcelas if p.status not in ['Pago', 'pago']), None)
                if proxima_parcela:
                    pag_dict['valor_proxima_parcela'] = float(proxima_parcela.valor_parcela)
                else:
                    pag_dict['valor_proxima_parcela'] = float(parcelas[0].valor_parcela)
            
            resultado.append(pag_dict)
        
        return jsonify(resultado), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados', methods=['POST'])
@jwt_required()
def criar_pagamento_parcelado(obra_id):
    """Cria um novo pagamento parcelado com suporte a entrada e parcelas customizadas (boletos)"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        
        valor_total = float(data.get('valor_total', 0))
        numero_parcelas = int(data.get('numero_parcelas', 1))
        periodicidade = data.get('periodicidade', 'Mensal')  # Semanal, Quinzenal ou Mensal
        forma_pagamento = data.get('forma_pagamento', 'PIX')  # PIX, Boleto, Transferência
        
        # 🆕 Verificar se tem entrada
        tem_entrada = data.get('tem_entrada', False)
        valor_entrada = float(data.get('valor_entrada', 0)) if tem_entrada else 0
        data_entrada = data.get('data_entrada')
        percentual_entrada = float(data.get('percentual_entrada', 0)) if tem_entrada else 0
        
        # Calcular valor das parcelas (após entrada)
        valor_restante = valor_total - valor_entrada
        valor_parcela = valor_restante / numero_parcelas if numero_parcelas > 0 else 0
        
        # Total de pagamentos = entrada (se houver) + parcelas
        total_pagamentos = numero_parcelas + (1 if tem_entrada else 0)
        
        print(f"--- [LOG] Criando parcelamento: Total={valor_total}, Entrada={valor_entrada} ({percentual_entrada}%), Parcelas={numero_parcelas}x{valor_parcela:.2f} ---")
        
        # Criar pagamento parcelado
        novo_pagamento = PagamentoParcelado(
            obra_id=obra_id,
            descricao=data.get('descricao'),
            fornecedor=data.get('fornecedor') or None,
            servico_id=data.get('servico_id') or None,
            valor_total=valor_total,
            numero_parcelas=total_pagamentos,  # Incluir entrada no total de parcelas
            valor_parcela=valor_parcela,
            data_primeira_parcela=datetime.strptime(data.get('data_primeira_parcela'), '%Y-%m-%d').date(),
            periodicidade=periodicidade,
            parcelas_pagas=0,
            status='Ativo',
            observacoes=data.get('observacoes') or None
        )
        
        # Tentar atribuir campos opcionais
        try:
            novo_pagamento.pix = data.get('pix') or None
        except:
            pass
        
        try:
            novo_pagamento.forma_pagamento = forma_pagamento
        except:
            pass
        
        db.session.add(novo_pagamento)
        db.session.flush()  # Para obter o ID do pagamento
        
        # 🆕 Criar parcela de ENTRADA (se houver)
        if tem_entrada and valor_entrada > 0:
            data_entrada_parsed = datetime.strptime(data_entrada, '%Y-%m-%d').date() if data_entrada else date.today()
            
            parcela_entrada = ParcelaIndividual(
                pagamento_parcelado_id=novo_pagamento.id,
                numero_parcela=0,  # Parcela 0 = Entrada
                valor_parcela=valor_entrada,
                data_vencimento=data_entrada_parsed,
                status='Previsto',
                data_pagamento=None,
                forma_pagamento=forma_pagamento,
                observacao=f'ENTRADA ({percentual_entrada:.0f}%)'
            )
            db.session.add(parcela_entrada)
            print(f"--- [LOG] Parcela de ENTRADA criada: R$ {valor_entrada:.2f} para {data_entrada_parsed} ---")
        
        # Verificar se há parcelas customizadas (valores diferentes ou boletos com código)
        parcelas_customizadas = data.get('parcelas_customizadas', [])
        
        if parcelas_customizadas and len(parcelas_customizadas) > 0:
            # Criar parcelas com valores e códigos de barras customizados
            print(f"--- [LOG] Criando {len(parcelas_customizadas)} parcelas customizadas ---")
            
            for i, parcela_data in enumerate(parcelas_customizadas):
                numero = i + 1
                valor = float(parcela_data.get('valor', valor_parcela))
                data_venc = datetime.strptime(parcela_data.get('data_vencimento'), '%Y-%m-%d').date()
                codigo_barras = parcela_data.get('codigo_barras') or None
                
                nova_parcela = ParcelaIndividual(
                    pagamento_parcelado_id=novo_pagamento.id,
                    numero_parcela=numero,
                    valor_parcela=valor,
                    data_vencimento=data_venc,
                    status='Previsto',
                    data_pagamento=None,
                    forma_pagamento=forma_pagamento,
                    observacao=None
                )
                
                try:
                    nova_parcela.codigo_barras = codigo_barras
                except:
                    pass
                
                db.session.add(nova_parcela)
            
            # Atualizar valor_total se houver valores customizados
            soma_valores = sum(float(p.get('valor', 0)) for p in parcelas_customizadas)
            novo_pagamento.valor_total = soma_valores + valor_entrada
            
        else:
            # Criar parcelas com valores iguais
            data_primeira = datetime.strptime(data.get('data_primeira_parcela'), '%Y-%m-%d').date()
            
            for i in range(numero_parcelas):
                # Calcular data da parcela
                if periodicidade == 'Semanal':
                    data_parcela = data_primeira + timedelta(weeks=i)
                elif periodicidade == 'Quinzenal':
                    data_parcela = data_primeira + timedelta(weeks=i*2)
                else:  # Mensal
                    mes = data_primeira.month + i
                    ano = data_primeira.year + (mes - 1) // 12
                    mes = ((mes - 1) % 12) + 1
                    try:
                        data_parcela = data_primeira.replace(year=ano, month=mes)
                    except ValueError:
                        import calendar
                        ultimo_dia = calendar.monthrange(ano, mes)[1]
                        data_parcela = data_primeira.replace(year=ano, month=mes, day=min(data_primeira.day, ultimo_dia))
                
                nova_parcela = ParcelaIndividual(
                    pagamento_parcelado_id=novo_pagamento.id,
                    numero_parcela=i + 1,
                    valor_parcela=valor_parcela,
                    data_vencimento=data_parcela,
                    status='Previsto',
                    data_pagamento=None,
                    forma_pagamento=forma_pagamento,
                    observacao=None
                )
                db.session.add(nova_parcela)
        
        db.session.commit()
        
        msg = f"Pagamento parcelado criado: ID {novo_pagamento.id}"
        if tem_entrada:
            msg += f" (Entrada de R$ {valor_entrada:.2f} + {numero_parcelas}x R$ {valor_parcela:.2f})"
        else:
            msg += f" ({numero_parcelas}x R$ {valor_parcela:.2f})"
        
        print(f"--- [LOG] {msg} ---")
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
        if 'pix' in data:
            try:
                pagamento.pix = data['pix']
            except:
                pass
        if 'forma_pagamento' in data:
            try:
                pagamento.forma_pagamento = data['forma_pagamento']
            except:
                pass
        
        # CORREÇÃO: Atualizar servico_id quando vinculado a um serviço
        if 'servico_id' in data:
            servico_id_novo = data['servico_id']
            if servico_id_novo:
                # Validar se o serviço existe e pertence à obra
                servico = db.session.get(Servico, servico_id_novo)
                if servico and servico.obra_id == obra_id:
                    pagamento.servico_id = servico_id_novo
                    print(f"--- [LOG] PagamentoParcelado {pagamento_id} vinculado ao serviço '{servico.nome}' ---")
                else:
                    print(f"--- [WARN] Serviço {servico_id_novo} não encontrado ou não pertence à obra ---")
            else:
                # Desvincular do serviço
                pagamento.servico_id = None
                print(f"--- [LOG] PagamentoParcelado {pagamento_id} desvinculado de serviço ---")
        
        # CORREÇÃO: Atualizar segmento quando alterado
        if 'segmento' in data:
            pagamento.segmento = data['segmento']
        
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
    """Deleta um pagamento parcelado e todos os registros relacionados"""
    try:
        print(f"\n{'='*80}")
        print(f"🗑️ INÍCIO: deletar_pagamento_parcelado")
        print(f"   obra_id={obra_id}, pagamento_id={pagamento_id}")
        print(f"{'='*80}")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        print(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        print(f"      - servico_id: {pagamento.servico_id}")
        
        # ===== DELETAR TODOS OS REGISTROS RELACIONADOS =====
        
        # 1. Buscar todas as parcelas deste pagamento
        parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        print(f"   📋 Encontradas {len(parcelas)} parcelas")
        
        # 2. Para cada parcela paga, deletar os registros relacionados
        for parcela in parcelas:
            if parcela.status == 'Pago':
                print(f"   🔍 Parcela {parcela.numero_parcela} está PAGA, buscando registros relacionados...")
                
                # Deletar Lançamentos vinculados
                descricao_lancamento = f"{pagamento.descricao} (Parcela {parcela.numero_parcela}/{pagamento.numero_parcelas})"
                lancamentos = Lancamento.query.filter_by(
                    obra_id=obra_id,
                    descricao=descricao_lancamento
                ).all()
                
                for lanc in lancamentos:
                    print(f"      ❌ Deletando Lancamento ID={lanc.id}")
                    db.session.delete(lanc)
                
                # Se o pagamento está vinculado a um serviço, deletar PagamentoServico
                if pagamento.servico_id:
                    # Buscar PagamentoServico que pode ter sido criado para esta parcela
                    pagamentos_servico = PagamentoServico.query.filter_by(
                        servico_id=pagamento.servico_id,
                        fornecedor=pagamento.fornecedor
                    ).all()
                    
                    for pag_serv in pagamentos_servico:
                        # Verificar se o valor corresponde à parcela
                        # Não podemos ter certeza absoluta, então vamos deletar se o valor bate
                        # ou reduzir o valor_pago se for maior
                        if pag_serv.valor_pago >= parcela.valor_parcela:
                            if pag_serv.valor_pago == parcela.valor_parcela:
                                print(f"      ❌ Deletando PagamentoServico ID={pag_serv.id} (valor_pago={pag_serv.valor_pago})")
                                db.session.delete(pag_serv)
                            else:
                                print(f"      ➖ Reduzindo PagamentoServico ID={pag_serv.id}: {pag_serv.valor_pago} -> {pag_serv.valor_pago - parcela.valor_parcela}")
                                pag_serv.valor_pago -= parcela.valor_parcela
                                if pag_serv.valor_pago <= 0:
                                    print(f"      ❌ Valor zerado, deletando PagamentoServico ID={pag_serv.id}")
                                    db.session.delete(pag_serv)
                            break  # Processar apenas o primeiro encontrado
        
        # 3. Deletar todas as parcelas individuais
        for parcela in parcelas:
            print(f"   ❌ Deletando ParcelaIndividual ID={parcela.id}")
            db.session.delete(parcela)
        
        # 4. Finalmente, deletar o pagamento parcelado
        print(f"   ❌ Deletando PagamentoParcelado ID={pagamento_id}")
        db.session.delete(pagamento)
        
        # 5. Commit de todas as alterações
        db.session.commit()
        
        print(f"   🎉 SUCESSO: Pagamento parcelado e todos os registros relacionados deletados")
        print(f"{'='*80}\n")
        
        return jsonify({"mensagem": "Pagamento parcelado deletado com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"\n{'='*80}")
        print(f"❌ ERRO em deletar_pagamento_parcelado:")
        print(f"   {str(e)}")
        print(f"\nStack trace:")
        print(error_details)
        print(f"{'='*80}\n")
        return jsonify({"erro": str(e), "details": error_details}), 500

# --- TABELA DE PREVISÕES (CÁLCULO) ---
@app.route('/sid/cronograma-financeiro/<int:obra_id>/previsoes', methods=['GET'])
@jwt_required()
def calcular_previsoes(obra_id):
    """Calcula a tabela de previsões mensais - OTIMIZADO"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        previsoes_por_mes = {}
        
        # 1. Pagamentos Futuros (Únicos) - 1 query
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
        
        # 2. Parcelas Individuais - 1 query com JOIN
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
        
        # 3. Pagamentos de Serviços pendentes - OTIMIZADO: 1 query com JOIN
        pagamentos_servico_pendentes = db.session.query(PagamentoServico).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_pago < PagamentoServico.valor_total,
            PagamentoServico.data_vencimento.isnot(None)
        ).all()
        
        for pag_serv in pagamentos_servico_pendentes:
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
            
            # Preparar lista de parcelas para inserção em lote (OTIMIZAÇÃO)
            parcelas_para_inserir = []
            
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
                
                # Criar parcela (adicionar à lista, não ao db ainda)
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
                parcelas_para_inserir.append(parcela)
            
            # OTIMIZAÇÃO: Inserir todas as parcelas de uma vez (bulk insert)
            db.session.bulk_save_objects(parcelas_para_inserir)
            db.session.commit()
            print(f"--- [LOG] {len(parcelas_para_inserir)} parcelas geradas em lote (bulk insert) ---")
            
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
        
        if 'codigo_barras' in data:
            try:
                parcela.codigo_barras = data['codigo_barras'] or None
            except:
                pass
        
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
        
        # Criar lançamento ou pagamento de serviço baseado no vínculo
        descricao_lancamento = f"{pagamento.descricao} (Parcela {parcela.numero_parcela}/{pagamento.numero_parcelas})"
        
        # Tratamento seguro do segmento
        segmento_info = 'Material'
        if hasattr(pagamento, 'segmento') and pagamento.segmento:
            segmento_info = pagamento.segmento
        
        print(f"   📄 Processando pagamento: '{descricao_lancamento}'")
        print(f"      - segmento: {segmento_info}")
        print(f"      - servico_id: {pagamento.servico_id}")
        
        # CORREÇÃO: Se tem serviço vinculado, NÃO criar PagamentoServico
        # As parcelas pagas já aparecem no histórico do serviço via query de ParcelaIndividual
        # Criar PagamentoServico causaria DUPLICAÇÃO no histórico
        if pagamento.servico_id:
            servico = db.session.get(Servico, pagamento.servico_id)
            if servico:
                print(f"   ✅ Parcela vinculada ao serviço '{servico.nome}'")
                print(f"      - NÃO criando PagamentoServico (parcela já aparece no histórico via ParcelaIndividual)")
            else:
                print(f"   ⚠️ Serviço {pagamento.servico_id} não existe, mas parcela será mostrada via ParcelaIndividual")
        else:
            # Parcela SEM serviço - criar Lancamento normal
            print(f"   ✅ Parcela sem serviço, criando lançamento geral")
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
                servico_id=None
            )
            if hasattr(novo_lancamento, 'segmento'):
                novo_lancamento.segmento = segmento_info
            db.session.add(novo_lancamento)
            db.session.flush()
            print(f"   ✅ Lançamento criado com ID={novo_lancamento.id}")
        
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
        
        print(f"   ✅ SUCESSO: Parcela {parcela_id} marcada como paga")
        print(f"{'='*80}\n")
        
        return jsonify({
            "mensagem": "Parcela paga com sucesso",
            "parcela": parcela.to_dict()
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


@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas/<int:parcela_id>/desfazer', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def desfazer_pagamento_parcela(obra_id, pagamento_id, parcela_id):
    """Desfaz o pagamento de uma parcela individual - volta para status Previsto"""
    
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
        print(f"↩️ INÍCIO: desfazer_pagamento_parcela")
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
        
        # Buscar parcela
        parcela = db.session.get(ParcelaIndividual, parcela_id)
        if not parcela or parcela.pagamento_parcelado_id != pagamento_id:
            print(f"   ❌ Parcela {parcela_id} não encontrada ou não pertence ao pagamento {pagamento_id}")
            return jsonify({"erro": "Parcela não encontrada"}), 404
        
        if parcela.status != 'Pago':
            print(f"   ⚠️ Parcela {parcela_id} não está paga, status atual: {parcela.status}")
            return jsonify({"erro": "Parcela não está marcada como paga"}), 400
        
        print(f"   ✅ Parcela encontrada: {parcela.numero_parcela}/{pagamento.numero_parcelas}")
        print(f"      - valor: R$ {parcela.valor_parcela}")
        print(f"      - data_pagamento: {parcela.data_pagamento}")
        
        # Descrição padrão da parcela para buscar registros relacionados
        descricao_parcela = f"{pagamento.descricao} (Parcela {parcela.numero_parcela}/{pagamento.numero_parcelas})"
        descricao_parcela_alt = f"{pagamento.descricao} ({parcela.numero_parcela}/{pagamento.numero_parcelas})"
        
        # Se TEM serviço vinculado, verificar e remover PagamentoServico correspondente
        if pagamento.servico_id:
            # Buscar PagamentoServico que corresponda a esta parcela
            # Pode ter sido criado antes da correção que removeu a criação automática
            pagamentos_servico = PagamentoServico.query.filter(
                PagamentoServico.servico_id == pagamento.servico_id,
                PagamentoServico.valor_total == parcela.valor_parcela
            ).all()
            
            # Tentar encontrar por descrição ou data
            for pag_serv in pagamentos_servico:
                # Verificar se é da mesma data ou descrição similar
                if (pag_serv.data_pagamento and parcela.data_pagamento and 
                    pag_serv.data_pagamento == parcela.data_pagamento):
                    print(f"   🗑️ Removendo PagamentoServico ID={pag_serv.id} (mesmo valor e data)")
                    db.session.delete(pag_serv)
                    break
                elif pag_serv.descricao and (descricao_parcela in pag_serv.descricao or descricao_parcela_alt in pag_serv.descricao):
                    print(f"   🗑️ Removendo PagamentoServico ID={pag_serv.id} (descrição corresponde)")
                    db.session.delete(pag_serv)
                    break
            else:
                print(f"   ℹ️ Nenhum PagamentoServico correspondente encontrado (normal se criado após correção)")
        else:
            # Se NÃO tem serviço vinculado, tentar remover o lançamento criado
            lancamento_existente = Lancamento.query.filter(
                Lancamento.obra_id == pagamento.obra_id,
                db.or_(
                    Lancamento.descricao == descricao_parcela,
                    Lancamento.descricao == descricao_parcela_alt
                )
            ).first()
            
            if lancamento_existente:
                print(f"   🗑️ Removendo lançamento ID={lancamento_existente.id}")
                db.session.delete(lancamento_existente)
            else:
                print(f"   ℹ️ Nenhum lançamento correspondente encontrado")
        
        # Voltar parcela para status Previsto
        parcela.status = 'Previsto'
        parcela.data_pagamento = None
        parcela.forma_pagamento = None
        
        print(f"   ✅ Parcela voltou para status 'Previsto'")
        
        # Atualizar contador de parcelas pagas
        todas_parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        parcelas_pagas_count = sum(1 for p in todas_parcelas if p.status == 'Pago')
        pagamento.parcelas_pagas = parcelas_pagas_count
        
        # Voltar status do pagamento para Ativo se estava Concluído
        if pagamento.status == 'Concluído':
            pagamento.status = 'Ativo'
            print(f"   ✅ Pagamento voltou para status 'Ativo'")
        
        print(f"   📊 Total de parcelas pagas agora: {parcelas_pagas_count}/{pagamento.numero_parcelas}")
        
        # Commit final
        db.session.commit()
        
        print(f"   ✅ SUCESSO: Pagamento da parcela {parcela_id} desfeito")
        print(f"{'='*80}\n")
        
        return jsonify({
            "mensagem": "Pagamento desfeito com sucesso",
            "parcela": parcela.to_dict()
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"\n{'='*80}")
        print(f"❌ ERRO FATAL em desfazer_pagamento_parcela:")
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
        
        # Buscar boletos da obra
        try:
            boletos_obra = Boleto.query.filter_by(obra_id=obra_id).order_by(Boleto.data_vencimento.asc()).all()
        except:
            boletos_obra = []
        
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
            
            # Estilo para células com quebra de linha
            cell_style = styles['Normal']
            cell_style.fontSize = 8
            cell_style.leading = 10
            
            for pag in pagamentos_resumo:
                # Usar Paragraph para permitir quebra de linha em todas as colunas de texto
                descricao_para = Paragraph(pag['descricao'], cell_style)
                fornecedor_para = Paragraph(pag['fornecedor'], cell_style)
                pix_para = Paragraph(pag['pix'] if pag['pix'] != '-' else '-', cell_style)
                status_para = Paragraph(pag['status'], cell_style)
                
                data_resumo.append([
                    descricao_para,  # Usar Paragraph para quebra automática
                    fornecedor_para,  # Usar Paragraph para quebra automática
                    pix_para,  # Usar Paragraph para quebra automática
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y'),
                    status_para  # Usar Paragraph para quebra automática
                ])
            
            # Ajustar larguras das colunas (agora são 6 colunas)
            table = Table(data_resumo, colWidths=[5*cm, 3*cm, 2.5*cm, 2.5*cm, 2.5*cm, 2*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff6f00')),  # Laranja escuro
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # Alinhamento vertical no topo
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
            
            # Estilo para células com quebra de linha
            cell_style = styles['Normal']
            cell_style.fontSize = 8
            cell_style.leading = 10
            
            # Adicionar pagamentos futuros (após 7 dias)
            for pag in pagamentos_futuros_normais:
                # Usar Paragraph para permitir quebra de linha
                descricao_para = Paragraph(pag['descricao'], cell_style)
                fornecedor_para = Paragraph(pag['fornecedor'], cell_style)
                
                data_futuros.append([
                    descricao_para,  # Usar Paragraph para quebra automática
                    fornecedor_para,  # Usar Paragraph para quebra automática
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y')
                ])
            
            # Ajustar larguras sem coluna Tipo e Status
            table = Table(data_futuros, colWidths=[7.5*cm, 4*cm, 2.5*cm, 3*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a90e2')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # Alinhamento vertical no topo
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
                    data_parcelas = [['Parcela', 'Valor', 'Vencimento', 'Status', 'Tipo', 'PIX/Código', 'Pago em']]
                    
                    # Variável para controlar cores
                    row_colors = []
                    
                    # Obter forma de pagamento e PIX do pagamento parcelado (pai) de forma defensiva
                    try:
                        forma_pag = pag_parcelado.forma_pagamento if hasattr(pag_parcelado, 'forma_pagamento') and pag_parcelado.forma_pagamento else 'PIX'
                    except:
                        forma_pag = 'PIX'
                    
                    try:
                        pix_raw = pag_parcelado.pix if hasattr(pag_parcelado, 'pix') and pag_parcelado.pix else ''
                    except:
                        pix_raw = ''
                    
                    for parcela in parcelas:
                        # Determinar se está vencida
                        status_display = parcela.status
                        if parcela.status == 'Previsto' and parcela.data_vencimento < hoje:
                            status_display = 'Vencido'
                            row_colors.append(colors.HexColor('#ffcdd2'))  # Vermelho claro
                        else:
                            row_colors.append(colors.whitesmoke if len(row_colors) % 2 == 0 else colors.white)
                        
                        # Determinar valor da coluna "PIX/Código"
                        # Priorizar código de barras da parcela (boleto), senão usar PIX do pagamento
                        try:
                            codigo_barras = parcela.codigo_barras if hasattr(parcela, 'codigo_barras') and parcela.codigo_barras else ''
                        except:
                            codigo_barras = ''
                        if codigo_barras:
                            # Truncar código de barras (mostrar últimos 12 dígitos)
                            pix_codigo_display = '...' + codigo_barras[-12:] if len(codigo_barras) > 12 else codigo_barras
                        elif pix_raw:
                            # Truncar PIX longo (máx 16 caracteres)
                            pix_codigo_display = (pix_raw[:14] + '..') if len(pix_raw) > 16 else pix_raw
                        else:
                            pix_codigo_display = '-'
                        
                        # Determinar valor da coluna "Pago em"
                        pago_em_display = parcela.data_pagamento.strftime('%d/%m/%Y') if parcela.data_pagamento else '-'
                        
                        data_parcelas.append([
                            f"{parcela.numero_parcela}/{pag_parcelado.numero_parcelas}",
                            formatar_real(parcela.valor_parcela),
                            parcela.data_vencimento.strftime('%d/%m/%Y'),
                            status_display,
                            pag_parcelado.periodicidade or '-',  # Tipo = Periodicidade
                            pix_codigo_display,  # PIX ou Código de Barras (truncado)
                            pago_em_display
                        ])
                    
                    # Ajustar larguras: Parcela, Valor, Vencimento, Status, Tipo, PIX/Código, Pago em
                    table_parcelas = Table(data_parcelas, colWidths=[1.5*cm, 2*cm, 2.2*cm, 1.8*cm, 1.8*cm, 3*cm, 2.2*cm])
                    
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
        
        # Seção: Boletos
        if boletos_obra:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. Boletos</b>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            # Separar boletos por status
            boletos_pendentes = [b for b in boletos_obra if b.status == 'Pendente' and b.data_vencimento >= hoje]
            boletos_vencidos = [b for b in boletos_obra if b.status == 'Vencido' or (b.status == 'Pendente' and b.data_vencimento < hoje)]
            boletos_pagos = [b for b in boletos_obra if b.status == 'Pago']
            
            # Tabela de boletos pendentes
            if boletos_pendentes or boletos_vencidos:
                data_boletos = [['Descrição', 'Beneficiário', 'Vencimento', 'Valor', 'Status', 'Código']]
                row_colors_boletos = []
                
                # Vencidos primeiro
                for boleto in boletos_vencidos:
                    codigo_truncado = ('...' + boleto.codigo_barras[-12:]) if boleto.codigo_barras and len(boleto.codigo_barras) > 12 else (boleto.codigo_barras or '-')
                    data_boletos.append([
                        boleto.descricao[:30] + '...' if len(boleto.descricao) > 30 else boleto.descricao,
                        (boleto.beneficiario[:20] + '...' if boleto.beneficiario and len(boleto.beneficiario) > 20 else boleto.beneficiario) or '-',
                        boleto.data_vencimento.strftime('%d/%m/%Y'),
                        formatar_real(boleto.valor),
                        'Vencido',
                        codigo_truncado
                    ])
                    row_colors_boletos.append(colors.HexColor('#ffcdd2'))  # Vermelho claro
                
                # Pendentes
                for boleto in boletos_pendentes:
                    dias_para_vencer = (boleto.data_vencimento - hoje).days
                    codigo_truncado = ('...' + boleto.codigo_barras[-12:]) if boleto.codigo_barras and len(boleto.codigo_barras) > 12 else (boleto.codigo_barras or '-')
                    
                    # Cor baseada na urgência
                    if dias_para_vencer <= 3:
                        cor = colors.HexColor('#ffcc80')  # Laranja claro
                    elif dias_para_vencer <= 7:
                        cor = colors.HexColor('#fff9c4')  # Amarelo claro
                    else:
                        cor = colors.whitesmoke if len(row_colors_boletos) % 2 == 0 else colors.white
                    
                    data_boletos.append([
                        boleto.descricao[:30] + '...' if len(boleto.descricao) > 30 else boleto.descricao,
                        (boleto.beneficiario[:20] + '...' if boleto.beneficiario and len(boleto.beneficiario) > 20 else boleto.beneficiario) or '-',
                        boleto.data_vencimento.strftime('%d/%m/%Y'),
                        formatar_real(boleto.valor),
                        f'{dias_para_vencer}d' if dias_para_vencer >= 0 else 'Vencido',
                        codigo_truncado
                    ])
                    row_colors_boletos.append(cor)
                
                table_boletos = Table(data_boletos, colWidths=[4*cm, 3*cm, 2.2*cm, 2.2*cm, 1.5*cm, 3*cm])
                
                style_boletos = [
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#607d8b')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]
                
                for i, color in enumerate(row_colors_boletos, start=1):
                    style_boletos.append(('BACKGROUND', (0, i), (-1, i), color))
                    if color == colors.HexColor('#ffcdd2'):
                        style_boletos.append(('TEXTCOLOR', (4, i), (4, i), colors.HexColor('#d32f2f')))
                
                table_boletos.setStyle(TableStyle(style_boletos))
                elements.append(table_boletos)
                elements.append(Spacer(1, 0.3*cm))
            
            # Resumo de boletos pagos
            if boletos_pagos:
                total_boletos_pagos = sum(b.valor for b in boletos_pagos)
                info_pagos = Paragraph(
                    f"<i>Boletos pagos: {len(boletos_pagos)} | Total: {formatar_real(total_boletos_pagos)}</i>",
                    styles['Normal']
                )
                elements.append(info_pagos)
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
        
        # Boletos
        total_boletos_pendentes = sum(b.valor for b in boletos_obra if b.status == 'Pendente' and b.data_vencimento >= hoje) if boletos_obra else 0
        total_boletos_vencidos = sum(b.valor for b in boletos_obra if b.status == 'Vencido' or (b.status == 'Pendente' and b.data_vencimento < hoje)) if boletos_obra else 0
        total_boletos_pagos = sum(b.valor for b in boletos_obra if b.status == 'Pago') if boletos_obra else 0
        
        total_geral_vencido = total_vencidos_unicos + total_servicos_vencidos + total_parcelas_vencidas + total_boletos_vencidos
        total_geral_previsto = total_futuros + total_servicos_pendentes + total_parcelados + total_boletos_pendentes
        total_geral = total_geral_vencido + total_geral_previsto
        
        resumo_data = [
            ['Descrição', 'Valor'],
            ['Total de Pagamentos Futuros (Previstos)', formatar_real(total_futuros)],
            ['Total de Pagamentos de Serviços (Previstos)', formatar_real(total_servicos_pendentes)],
            ['Total de Parcelas (Previstas)', formatar_real(total_parcelados)],
            ['Total de Boletos (Pendentes)', formatar_real(total_boletos_pendentes)],
            ['', ''],  # Linha em branco
            ['Total de Pagamentos VENCIDOS (Únicos)', formatar_real(total_vencidos_unicos)],
            ['Total de Pagamentos de Serviços VENCIDOS', formatar_real(total_servicos_vencidos)],
            ['Total de Parcelas VENCIDAS', formatar_real(total_parcelas_vencidas)],
            ['Total de Boletos VENCIDOS', formatar_real(total_boletos_vencidos)],
            ['', ''],  # Linha em branco
            ['Total de Parcelas PAGAS', formatar_real(total_pago_parcelas)],
            ['Total de Boletos PAGOS', formatar_real(total_boletos_pagos)],
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
    🆕 ENDPOINT UNIFICADO - Insere pagamentos (à vista ou parcelados) com vínculo opcional a serviços.
    
    Suporta:
    - Pagamentos à vista (Pago ou A Pagar)
    - Pagamentos parcelados (Semanal/Quinzenal/Mensal)
    - Vínculo opcional ao serviço
    - Atualização automática de % de conclusão do serviço
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    print(f"\n{'='*80}")
    print(f"💰 INSERIR PAGAMENTO - Obra {obra_id}")
    print(f"{'='*80}")
    
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json
        print(f"📋 Dados recebidos: {dados}")
        
        # DEBUG: Verificar campos de entrada especificamente
        print(f"🔍 DEBUG ENTRADA:")
        print(f"   tem_entrada: {dados.get('tem_entrada')}")
        print(f"   valor_entrada: {dados.get('valor_entrada')}")
        print(f"   percentual_entrada: {dados.get('percentual_entrada')}")
        print(f"   data_entrada: {dados.get('data_entrada')}")
        
        # Campos obrigatórios
        descricao = dados.get('descricao')
        valor_total = float(dados.get('valor', 0))
        tipo = dados.get('tipo')  # 'Material' ou 'Mão de Obra'
        status = dados.get('status', 'A Pagar')  # 'Pago' ou 'A Pagar'
        data = date.fromisoformat(dados.get('data'))
        
        # Campos opcionais
        servico_id = dados.get('servico_id')
        fornecedor = dados.get('fornecedor')
        data_vencimento = dados.get('data_vencimento')
        pix = dados.get('pix')
        prioridade = int(dados.get('prioridade', 0))
        
        # 🆕 NOVOS CAMPOS PARA PARCELAMENTO
        tipo_forma_pagamento = dados.get('tipo_forma_pagamento', 'avista')  # 'avista' ou 'parcelado'
        numero_parcelas = dados.get('numero_parcelas')
        periodicidade = dados.get('periodicidade')  # 'Semanal', 'Quinzenal', 'Mensal'
        data_primeira_parcela = dados.get('data_primeira_parcela')
        
        print(f"   Tipo pagamento: {tipo_forma_pagamento}")
        print(f"   Status: {status}")
        print(f"   Serviço vinculado: {servico_id}")
        
        # ===== FLUXO PARCELADO =====
        if tipo_forma_pagamento == 'parcelado':
            print(f"   📦 Criando pagamento PARCELADO")
            print(f"      - Parcelas: {numero_parcelas}")
            print(f"      - Periodicidade: {periodicidade}")
            
            if not numero_parcelas or not periodicidade or not data_primeira_parcela:
                return jsonify({"erro": "Parcelas, periodicidade e data da primeira parcela são obrigatórios para parcelamento"}), 400
            
            numero_parcelas = int(numero_parcelas)
            data_primeira = date.fromisoformat(data_primeira_parcela)
            
            # 🆕 Verificar se tem entrada
            tem_entrada = dados.get('tem_entrada', False)
            valor_entrada = float(dados.get('valor_entrada', 0)) if tem_entrada else 0
            data_entrada = dados.get('data_entrada')
            percentual_entrada = float(dados.get('percentual_entrada', 0)) if tem_entrada else 0
            
            # Calcular valor das parcelas (após entrada)
            valor_restante = valor_total - valor_entrada
            valor_parcela = valor_restante / numero_parcelas if numero_parcelas > 0 else 0
            
            # Total de pagamentos = entrada (se houver) + parcelas
            total_pagamentos = numero_parcelas + (1 if tem_entrada and valor_entrada > 0 else 0)
            
            print(f"   💰 Entrada: R$ {valor_entrada:.2f} ({percentual_entrada:.0f}%)")
            print(f"   💰 Restante: R$ {valor_restante:.2f} em {numero_parcelas}x R$ {valor_parcela:.2f}")
            
            # Criar PagamentoParcelado
            novo_parcelado = PagamentoParcelado(
                obra_id=obra_id,
                descricao=descricao,
                fornecedor=fornecedor,
                servico_id=servico_id,
                segmento=tipo,  # 'Material' ou 'Mão de Obra'
                valor_total=valor_total,
                numero_parcelas=total_pagamentos,  # Incluir entrada no total
                valor_parcela=valor_parcela,
                data_primeira_parcela=data_primeira,
                periodicidade=periodicidade,
                parcelas_pagas=0,
                status='Ativo'
            )
            db.session.add(novo_parcelado)
            db.session.flush()
            
            print(f"   ✅ PagamentoParcelado criado: ID={novo_parcelado.id}")
            
            # Gerar parcelas individuais
            from datetime import timedelta
            import calendar
            
            # 🆕 Criar parcela de ENTRADA (se houver)
            if tem_entrada and valor_entrada > 0:
                data_entrada_parsed = date.fromisoformat(data_entrada) if data_entrada else data
                
                parcela_entrada = ParcelaIndividual(
                    pagamento_parcelado_id=novo_parcelado.id,
                    numero_parcela=0,  # Parcela 0 = Entrada
                    valor_parcela=valor_entrada,
                    data_vencimento=data_entrada_parsed,
                    status='Previsto',
                    data_pagamento=None,
                    forma_pagamento=None,
                    observacao=f'ENTRADA ({percentual_entrada:.0f}%)'
                )
                db.session.add(parcela_entrada)
                print(f"      ✅ ENTRADA: R$ {valor_entrada:.2f} - {data_entrada_parsed}")
            
            for i in range(1, numero_parcelas + 1):
                # Calcular data de vencimento da parcela
                if periodicidade == 'Semanal':
                    data_venc = data_primeira + timedelta(days=(i-1) * 7)
                elif periodicidade == 'Quinzenal':
                    data_venc = data_primeira + timedelta(days=(i-1) * 15)
                else:  # Mensal
                    month = data_primeira.month - 1 + (i-1)
                    year = data_primeira.year + month // 12
                    month = month % 12 + 1
                    day = min(data_primeira.day, calendar.monthrange(year, month)[1])
                    data_venc = date(year, month, day)
                
                # Status da parcela
                if status == 'Pago':
                    parcela_status = 'Pago'
                    parcela_data_pagamento = data
                else:
                    parcela_status = 'Previsto'
                    parcela_data_pagamento = None
                
                nova_parcela = ParcelaIndividual(
                    pagamento_parcelado_id=novo_parcelado.id,
                    numero_parcela=i,
                    valor_parcela=valor_parcela,
                    data_vencimento=data_venc,
                    status=parcela_status,
                    data_pagamento=parcela_data_pagamento,
                    forma_pagamento=pix if status == 'Pago' else None
                )
                db.session.add(nova_parcela)
                print(f"      ✅ Parcela {i}/{numero_parcelas}: R$ {valor_parcela:.2f} - {data_venc} ({parcela_status})")
            
            db.session.flush()
            
            # Se STATUS = PAGO, criar PagamentoServico para cada parcela
            if status == 'Pago' and servico_id:
                print(f"   💰 Status=PAGO com serviço vinculado, criando PagamentoServico...")
                
                servico = Servico.query.get(servico_id)
                if servico:
                    # Determinar tipo_pagamento
                    tipo_pagamento = 'mao_de_obra' if tipo == 'Mão de Obra' else 'material'
                    
                    # Criar UM PagamentoServico com valor total
                    novo_pag_servico = PagamentoServico(
                        servico_id=servico_id,
                        tipo_pagamento=tipo_pagamento,
                        valor_total=valor_total,
                        valor_pago=valor_total,
                        data=data,
                        status='Pago',
                        fornecedor=fornecedor,
                        prioridade=prioridade
                    )
                    db.session.add(novo_pag_servico)
                    db.session.flush()
                    
                    print(f"      ✅ PagamentoServico criado: ID={novo_pag_servico.id}, valor={valor_total}")
                    
                    # Atualizar parcelas_pagas
                    novo_parcelado.parcelas_pagas = numero_parcelas
                    novo_parcelado.status = 'Concluído'
                    
                    # Recalcular % do serviço
                    pagamentos = PagamentoServico.query.filter_by(servico_id=servico_id).all()
                    pagamentos_mao = [p for p in pagamentos if p.tipo_pagamento == 'mao_de_obra']
                    pagamentos_mat = [p for p in pagamentos if p.tipo_pagamento == 'material']
                    
                    if servico.valor_global_mao_de_obra > 0:
                        total_pago = sum(p.valor_pago for p in pagamentos_mao)
                        servico.percentual_conclusao_mao_obra = min(100, (total_pago / servico.valor_global_mao_de_obra) * 100)
                    
                    if servico.valor_global_material > 0:
                        total_pago = sum(p.valor_pago for p in pagamentos_mat)
                        servico.percentual_conclusao_material = min(100, (total_pago / servico.valor_global_material) * 100)
                    
                    print(f"      ✅ Serviço atualizado: MO={servico.percentual_conclusao_mao_obra:.1f}%, MAT={servico.percentual_conclusao_material:.1f}%")
            
            elif status == 'Pago':
                # Status=Pago mas sem serviço vinculado
                novo_parcelado.parcelas_pagas = numero_parcelas
                novo_parcelado.status = 'Concluído'
                print(f"   ✅ Todas as parcelas marcadas como pagas (sem vínculo ao serviço)")
            
            db.session.commit()
            print(f"{'='*80}")
            print(f"✅ SUCESSO: Pagamento parcelado criado")
            print(f"{'='*80}\n")
            
            return jsonify({
                "mensagem": "Pagamento parcelado criado com sucesso",
                "pagamento_parcelado": novo_parcelado.to_dict()
            }), 201
        
        # ===== FLUXO À VISTA =====
        else:
            print(f"   💵 Criando pagamento À VISTA")
            valor_pago = valor_total if status == 'Pago' else 0.0
            
            # CASO 1: STATUS "PAGO" COM SERVIÇO VINCULADO
            if servico_id and status == 'Pago':
                servico = Servico.query.get_or_404(servico_id)
                tipo_pagamento = 'mao_de_obra' if tipo == 'Mão de Obra' else 'material'
                
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
                db.session.flush()
                
                # Recalcular percentual do serviço
                pagamentos = PagamentoServico.query.filter_by(servico_id=servico_id).all()
                pagamentos_mao = [p for p in pagamentos if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_mat = [p for p in pagamentos if p.tipo_pagamento == 'material']
                
                if servico.valor_global_mao_de_obra > 0:
                    total_pago = sum(p.valor_pago for p in pagamentos_mao)
                    servico.percentual_conclusao_mao_obra = min(100, (total_pago / servico.valor_global_mao_de_obra) * 100)
                
                if servico.valor_global_material > 0:
                    total_pago = sum(p.valor_pago for p in pagamentos_mat)
                    servico.percentual_conclusao_material = min(100, (total_pago / servico.valor_global_material) * 100)
                
                db.session.commit()
                print(f"   ✅ PagamentoServico PAGO criado: ID={novo_pagamento.id}")
                print(f"{'='*80}\n")
                return jsonify(novo_pagamento.to_dict()), 201
            
            # CASO 2: STATUS "A PAGAR" COM SERVIÇO VINCULADO
            elif servico_id and status == 'A Pagar':
                servico = Servico.query.get_or_404(servico_id)
                
                novo_futuro = PagamentoFuturo(
                    obra_id=obra_id,
                    descricao=f"{descricao} (Serviço: {servico.nome})",
                    valor=valor_total,
                    data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else data,
                    fornecedor=fornecedor,
                    pix=pix,
                    observacoes=f"Vinculado ao serviço {servico.nome}",
                    status='Previsto',
                    servico_id=servico_id,
                    tipo=tipo
                )
                db.session.add(novo_futuro)
                db.session.commit()
                print(f"   ✅ PagamentoFuturo criado: ID={novo_futuro.id}")
                print(f"{'='*80}\n")
                return jsonify(novo_futuro.to_dict()), 201
            
            # CASO 3: STATUS "A PAGAR" SEM SERVIÇO
            elif status == 'A Pagar':
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
                    tipo=tipo
                )
                db.session.add(novo_futuro)
                db.session.commit()
                print(f"   ✅ PagamentoFuturo criado: ID={novo_futuro.id}")
                print(f"{'='*80}\n")
                return jsonify(novo_futuro.to_dict()), 201
            
            # CASO 4: STATUS "PAGO" SEM SERVIÇO
            else:
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
                print(f"   ✅ Lançamento criado: ID={novo_lancamento.id}")
                print(f"{'='*80}\n")
                return jsonify(novo_lancamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"\n{'='*80}")
        print(f"❌ ERRO em inserir_pagamento:")
        print(f"   {str(e)}")
        print(f"\nStack trace:")
        print(error_details)
        print(f"{'='*80}\n")
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
        itens_selecionados = dados.get('itens', [])  # Lista de {tipo: 'futuro'|'parcela'|'servico', id: X}
        data_pagamento = dados.get('data_pagamento')
        
        print(f"--- [LOG] Total de itens recebidos: {len(itens_selecionados)} ---")
        print(f"--- [LOG] Itens: {itens_selecionados} ---")
        
        if data_pagamento:
            data_pagamento = date.fromisoformat(data_pagamento)
        else:
            data_pagamento = date.today()
        
        resultados = []
        
        for item in itens_selecionados:
            tipo_item = item.get('tipo')
            item_id = item.get('id')
            
            print(f"--- [LOG] Processando item: tipo={tipo_item}, id={item_id} ---")
            
            # CORREÇÃO CRÍTICA: Usar savepoint para isolar cada item
            # Se um item der erro, não afeta os outros
            savepoint = db.session.begin_nested()
            
            try:
                if tipo_item == 'futuro':
                    # ===== LÓGICA CORRIGIDA: Verificar se tem vínculo com serviço =====
                    pagamento = db.session.get(PagamentoFuturo, item_id)
                    
                    if not pagamento:
                        savepoint.rollback()
                        erro_msg = f"Pagamento futuro ID {item_id} não encontrado no banco"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "error",
                            "mensagem": erro_msg
                        })
                        continue
                    
                    if pagamento.obra_id != obra_id:
                        savepoint.rollback()
                        erro_msg = f"Pagamento futuro ID {item_id} não pertence à obra {obra_id}"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento não pertence a esta obra"
                        })
                        continue
                    
                    # CASO 1: Pagamento vinculado a SERVIÇO
                    if pagamento.servico_id:
                        servico = db.session.get(Servico, pagamento.servico_id)
                        if not servico:
                            savepoint.rollback()
                            erro_msg = f"Serviço ID {pagamento.servico_id} não encontrado"
                            print(f"--- [ERRO] {erro_msg} ---")
                            resultados.append({
                                "tipo": "futuro",
                                "id": item_id,
                                "status": "error",
                                "mensagem": "Serviço vinculado não encontrado"
                            })
                            continue
                        
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
                            fornecedor=pagamento.fornecedor,
                            pix=pagamento.pix
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
                        
                        print(f"--- [LOG] ✅ Pagamento futuro ID {item_id} vinculado ao serviço '{servico.nome}' ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "success",
                            "mensagem": f"Pagamento '{pagamento.descricao}' vinculado ao serviço '{servico.nome}' e marcado como pago",
                            "pagamento_servico_id": novo_pag_servico.id
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
                        db.session.flush()
                        
                        # DELETE o PagamentoFuturo
                        db.session.delete(pagamento)
                        
                        print(f"--- [LOG] ✅ Pagamento futuro ID {item_id} movido para histórico (Lançamento ID {novo_lancamento.id}) ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "success",
                            "mensagem": f"Pagamento futuro '{pagamento.descricao}' movido para o histórico",
                            "lancamento_id": novo_lancamento.id
                        })
                
                elif tipo_item == 'parcela':
                    # Marcar parcela como paga
                    parcela = db.session.get(ParcelaIndividual, item_id)
                    
                    if not parcela:
                        savepoint.rollback()
                        erro_msg = f"Parcela ID {item_id} não encontrada no banco"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Parcela não encontrada"
                        })
                        continue
                    
                    pag_parcelado = db.session.get(PagamentoParcelado, parcela.pagamento_parcelado_id)
                    
                    if not pag_parcelado:
                        savepoint.rollback()
                        erro_msg = f"Pagamento parcelado ID {parcela.pagamento_parcelado_id} não encontrado"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento parcelado não encontrado"
                        })
                        continue
                    
                    if pag_parcelado.obra_id != obra_id:
                        savepoint.rollback()
                        erro_msg = f"Pagamento parcelado não pertence à obra {obra_id}"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento não pertence a esta obra"
                        })
                        continue
                    
                    # Verificar se já está paga
                    if parcela.status == 'Pago':
                        savepoint.rollback()
                        print(f"--- [AVISO] Parcela ID {item_id} já está paga, pulando ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": f"Parcela {parcela.numero_parcela} já está paga"
                        })
                        continue
                    
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
                    
                    print(f"--- [LOG] ✅ Parcela ID {item_id} marcada como paga ---")
                    resultados.append({
                        "tipo": "parcela",
                        "id": item_id,
                        "status": "success",
                        "mensagem": f"Parcela {parcela.numero_parcela} marcada como paga"
                    })
                
                elif tipo_item == 'servico':
                    # NOVO: Marcar pagamento de serviço como totalmente pago
                    pagamento_servico = db.session.get(PagamentoServico, item_id)
                    
                    if not pagamento_servico:
                        savepoint.rollback()
                        erro_msg = f"Pagamento de serviço ID {item_id} não encontrado"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento de serviço não encontrado"
                        })
                        continue
                    
                    servico = db.session.get(Servico, pagamento_servico.servico_id)
                    
                    if not servico:
                        savepoint.rollback()
                        erro_msg = f"Serviço ID {pagamento_servico.servico_id} não encontrado"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Serviço não encontrado"
                        })
                        continue
                    
                    if servico.obra_id != obra_id:
                        savepoint.rollback()
                        erro_msg = f"Serviço não pertence à obra {obra_id}"
                        print(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Serviço não pertence a esta obra"
                        })
                        continue
                    
                    # Verificar se já está totalmente pago
                    if pagamento_servico.valor_pago >= pagamento_servico.valor_total:
                        savepoint.rollback()
                        print(f"--- [AVISO] Pagamento de serviço ID {item_id} já está totalmente pago ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento já está totalmente pago"
                        })
                        continue
                    
                    # Marcar como totalmente pago
                    pagamento_servico.valor_pago = pagamento_servico.valor_total
                    pagamento_servico.data = data_pagamento
                    pagamento_servico.status = 'Pago'
                    
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
                    
                    print(f"--- [LOG] ✅ Pagamento de serviço ID {item_id} marcado como pago ---")
                    resultados.append({
                        "tipo": "servico",
                        "id": item_id,
                        "status": "success",
                        "mensagem": f"Pagamento do serviço '{servico.nome}' marcado como pago"
                    })
                
                else:
                    erro_msg = f"Tipo de item inválido: '{tipo_item}'"
                    print(f"--- [ERRO] {erro_msg} ---")
                    resultados.append({
                        "tipo": tipo_item,
                        "id": item_id,
                        "status": "error",
                        "mensagem": "Tipo de item inválido (esperado: 'futuro', 'parcela' ou 'servico')"
                    })
                    savepoint.rollback()
                    continue
                
                # SUCESSO: Commit do savepoint
                savepoint.commit()
                print(f"--- [LOG] ✅ Item processado com sucesso (savepoint committed) ---")
            
            except Exception as e:
                # ERRO: Rollback do savepoint (isola o erro deste item)
                savepoint.rollback()
                error_details = traceback.format_exc()
                erro_msg = f"Erro ao processar item tipo={tipo_item}, id={item_id}: {str(e)}"
                print(f"--- [ERRO] {erro_msg} ---")
                print(error_details)
                resultados.append({
                    "tipo": tipo_item,
                    "id": item_id,
                    "status": "error",
                    "mensagem": f"Erro: {str(e)}"
                })
        
        db.session.commit()
        
        sucessos = len([r for r in resultados if r['status'] == 'success'])
        erros = len([r for r in resultados if r['status'] == 'error'])
        print(f"--- [LOG] ✅ {sucessos} item(ns) marcado(s) como pago | ❌ {erros} erro(s) ---")
        
        # Listar os erros no log
        if erros > 0:
            print("--- [LOG] Detalhes dos erros: ---")
            for r in resultados:
                if r['status'] == 'error':
                    print(f"  - Tipo: {r['tipo']}, ID: {r['id']}, Erro: {r['mensagem']}")
        
        return jsonify({
            "mensagem": "Processamento concluído",
            "resultados": resultados
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO FATAL] marcar-multiplos-pagos: {str(e)}\n{error_details} ---")
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


@app.route('/diario/imagens/<int:imagem_id>', methods=['GET'])
@jwt_required()
def get_imagem_diario(imagem_id):
    """Busca uma imagem do diario com base64"""
    try:
        imagem = db.session.get(DiarioImagem, imagem_id)
        if not imagem:
            return jsonify({"erro": "Imagem nao encontrada"}), 404
        
        entrada = db.session.get(DiarioObra, imagem.diario_id)
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        return jsonify(imagem.to_dict_full()), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] GET /diario/imagens/{imagem_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


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
    
    # ===== TIPO DE MEDIÇÃO =====
    tipo_medicao = db.Column(db.String(20), default='empreitada')  # 'area', 'empreitada' ou 'etapas'
    area_total = db.Column(db.Float)  # Para modo 'area'
    area_executada = db.Column(db.Float, default=0)  # Para modo 'area'
    unidade_medida = db.Column(db.String(10), default='m²')  # m², m³, m, un, kg, L
    
    observacoes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    # Relacionamento com etapas (para tipo_medicao='etapas')
    # Usando lazy='dynamic' para evitar erro quando tabela não existe
    etapas = db.relationship('CronogramaEtapa', backref='cronograma', lazy='dynamic', cascade="all, delete-orphan")

    def calcular_percentual_por_etapas(self):
        """Calcula o percentual de conclusão baseado nas etapas (média ponderada por duração)"""
        try:
            etapas_list = self.etapas.order_by(CronogramaEtapa.ordem).all() if self.etapas else []
            if not etapas_list:
                return 0.0
            
            total_dias = 0
            soma_ponderada = 0
            
            for etapa in etapas_list:
                dias = etapa.duracao_dias or 1
                total_dias += dias
                soma_ponderada += (etapa.percentual_conclusao or 0) * dias
            
            if total_dias == 0:
                return 0.0
            
            return round(soma_ponderada / total_dias, 2)
        except Exception as e:
            print(f"[AVISO] Erro ao calcular percentual por etapas: {str(e)}")
            return 0.0

    def atualizar_datas_por_etapas(self):
        """Atualiza as datas do serviço baseado nas etapas"""
        try:
            etapas_list = self.etapas.order_by(CronogramaEtapa.ordem).all() if self.etapas else []
            if not etapas_list:
                return
            
            if etapas_list:
                # Data início = primeira etapa
                self.data_inicio = etapas_list[0].data_inicio
                # Data fim = última etapa
                self.data_fim_prevista = etapas_list[-1].data_fim
        except Exception as e:
            print(f"[AVISO] Erro ao atualizar datas por etapas: {str(e)}")

    def to_dict(self):
        # Se tipo_medicao for 'etapas', calcular percentual automaticamente
        percentual = self.percentual_conclusao
        etapas_list = []
        
        # Tentar carregar etapas PAI (etapa_pai_id IS NULL)
        # As subetapas são carregadas dentro de cada etapa pai via to_dict()
        try:
            # Buscar apenas etapas pai (não subetapas)
            etapas_query = CronogramaEtapa.query.filter_by(
                cronograma_id=self.id,
                etapa_pai_id=None  # Apenas etapas pai
            ).order_by(CronogramaEtapa.ordem).all()
            
            if etapas_query:
                etapas_list = [etapa.to_dict() for etapa in etapas_query]
                if self.tipo_medicao == 'etapas':
                    percentual = self.calcular_percentual_por_etapas()
        except Exception as e:
            # Tabela cronograma_etapa pode não existir ainda ou não ter a coluna etapa_pai_id
            try:
                # Fallback: tentar carregar todas as etapas (compatibilidade)
                etapas_query = self.etapas.order_by(CronogramaEtapa.ordem).all() if self.etapas else []
                if etapas_query:
                    etapas_list = [etapa.to_dict() for etapa in etapas_query]
                    if self.tipo_medicao == 'etapas':
                        percentual = self.calcular_percentual_por_etapas()
            except:
                print(f"[AVISO] Não foi possível carregar etapas: {str(e)}")
                etapas_list = []
        
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
            'percentual_conclusao': float(percentual),
            # TIPO DE MEDIÇÃO
            'tipo_medicao': self.tipo_medicao,
            'area_total': self.area_total,
            'area_executada': self.area_executada,
            'unidade_medida': self.unidade_medida,
            'observacoes': self.observacoes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            # ETAPAS (se houver)
            'etapas': etapas_list,
        }


class CronogramaEtapa(db.Model):
    """
    Etapas e Subetapas do cronograma (estrutura hierárquica)
    - etapa_pai_id = NULL → É uma ETAPA (agrupador: Infraestrutura, Revestimento)
    - etapa_pai_id = X → É uma SUBETAPA (item: Escavação, Tubulação)
    """
    __tablename__ = 'cronograma_etapa'

    id = db.Column(db.Integer, primary_key=True)
    cronograma_id = db.Column(db.Integer, db.ForeignKey('cronograma_obra.id'), nullable=False)
    
    # Hierarquia: NULL = etapa pai, preenchido = subetapa
    etapa_pai_id = db.Column(db.Integer, db.ForeignKey('cronograma_etapa.id'), nullable=True)
    
    nome = db.Column(db.String(200), nullable=False)
    ordem = db.Column(db.Integer, nullable=False, default=1)
    
    # Duração e datas (para subetapas; etapas pai calculam das filhas)
    duracao_dias = db.Column(db.Integer, nullable=True, default=1)
    data_inicio = db.Column(db.Date, nullable=True)
    data_fim = db.Column(db.Date, nullable=True)
    
    # Flag para indicar se data_inicio foi ajustada manualmente
    inicio_ajustado_manualmente = db.Column(db.Boolean, default=False)
    
    # Condição de início (APENAS para etapas pai - relacionamento com etapa anterior)
    etapa_anterior_id = db.Column(db.Integer, db.ForeignKey('cronograma_etapa.id'), nullable=True)
    tipo_condicao = db.Column(db.String(20), nullable=True)  # 'apos_termino', 'dias_apos', 'dias_antes', 'manual'
    dias_offset = db.Column(db.Integer, nullable=True, default=0)
    
    # Execução
    percentual_conclusao = db.Column(db.Float, nullable=False, default=0.0)
    
    observacoes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    # Relacionamentos
    subetapas = db.relationship('CronogramaEtapa', 
                                 backref=db.backref('etapa_pai', remote_side=[id]),
                                 lazy='dynamic',
                                 foreign_keys=[etapa_pai_id])

    def is_etapa_pai(self):
        """Retorna True se é uma etapa principal (não é subetapa)"""
        return self.etapa_pai_id is None

    def calcular_data_fim(self):
        """Calcula data_fim baseado em data_inicio + duracao_dias"""
        if self.data_inicio and self.duracao_dias:
            self.data_fim = self.data_inicio + timedelta(days=self.duracao_dias - 1)
        return self.data_fim

    def calcular_datas_das_subetapas(self):
        """Para etapas pai: calcula datas baseado nas subetapas"""
        try:
            subs = self.subetapas.order_by(CronogramaEtapa.ordem).all()
            if subs:
                datas_inicio = [s.data_inicio for s in subs if s.data_inicio]
                datas_fim = [s.data_fim for s in subs if s.data_fim]
                if datas_inicio:
                    self.data_inicio = min(datas_inicio)
                if datas_fim:
                    self.data_fim = max(datas_fim)
        except:
            pass

    def calcular_percentual_das_subetapas(self):
        """Para etapas pai: calcula percentual como média ponderada das subetapas"""
        try:
            subs = self.subetapas.all()
            if not subs:
                return self.percentual_conclusao or 0.0
            
            total_dias = 0
            soma_ponderada = 0
            
            for sub in subs:
                dias = sub.duracao_dias or 1
                total_dias += dias
                soma_ponderada += (sub.percentual_conclusao or 0) * dias
            
            if total_dias == 0:
                return 0.0
            
            return round(soma_ponderada / total_dias, 2)
        except:
            return self.percentual_conclusao or 0.0

    def total_dias_subetapas(self):
        """Para etapas pai: soma total de dias das subetapas"""
        try:
            subs = self.subetapas.all()
            return sum(s.duracao_dias or 0 for s in subs)
        except:
            return self.duracao_dias or 0

    def to_dict(self):
        # Se é etapa pai, incluir subetapas
        subetapas_list = []
        total_dias = self.duracao_dias or 0
        percentual = float(self.percentual_conclusao or 0)
        
        if self.is_etapa_pai():
            try:
                subetapas_list = [s.to_dict() for s in self.subetapas.order_by(CronogramaEtapa.ordem).all()]
                total_dias = self.total_dias_subetapas()
                percentual = self.calcular_percentual_das_subetapas()
            except:
                pass
        
        return {
            'id': self.id,
            'cronograma_id': self.cronograma_id,
            'etapa_pai_id': self.etapa_pai_id,
            'is_etapa_pai': self.is_etapa_pai(),
            'nome': self.nome,
            'ordem': self.ordem,
            'duracao_dias': self.duracao_dias,
            'total_dias': total_dias,
            'data_inicio': self.data_inicio.isoformat() if self.data_inicio else None,
            'data_fim': self.data_fim.isoformat() if self.data_fim else None,
            'inicio_ajustado_manualmente': self.inicio_ajustado_manualmente,
            # Condições (só para etapas pai)
            'etapa_anterior_id': self.etapa_anterior_id,
            'tipo_condicao': self.tipo_condicao,
            'dias_offset': self.dias_offset,
            # Execução
            'percentual_conclusao': percentual,
            'observacoes': self.observacoes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            # Subetapas (só para etapas pai)
            'subetapas': subetapas_list,
        }


# ==================== MODELO AGENDA DE DEMANDAS ====================
class AgendaDemanda(db.Model):
    """
    Agenda de Demandas - Acompanhamento de entregas, visitas, serviços contratados, etc.
    Pode ser importado de Pagamentos ou Orçamento, ou cadastrado manualmente.
    """
    __tablename__ = 'agenda_demanda'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    
    # Dados básicos
    descricao = db.Column(db.String(255), nullable=False)
    tipo = db.Column(db.String(50), nullable=False, default='material')  # material, servico, visita, outro
    fornecedor = db.Column(db.String(255), nullable=True)
    telefone = db.Column(db.String(50), nullable=True)
    
    # Valores
    valor = db.Column(db.Float, nullable=True)
    
    # Datas
    data_prevista = db.Column(db.Date, nullable=False)
    data_conclusao = db.Column(db.Date, nullable=True)
    
    # Status: aguardando, concluido, atrasado, cancelado
    status = db.Column(db.String(50), nullable=False, default='aguardando')
    
    # Origem: manual, pagamento, orcamento
    origem = db.Column(db.String(50), nullable=False, default='manual')
    
    # IDs de referência (para importações)
    pagamento_servico_id = db.Column(db.Integer, db.ForeignKey('pagamento_servico.id'), nullable=True)
    orcamento_item_id = db.Column(db.Integer, db.ForeignKey('orcamento_eng_item.id'), nullable=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    
    # Observações
    observacoes = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    # Relacionamentos
    obra = db.relationship('Obra', backref=db.backref('agenda_demandas', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'descricao': self.descricao,
            'tipo': self.tipo,
            'fornecedor': self.fornecedor,
            'telefone': self.telefone,
            'valor': float(self.valor) if self.valor else None,
            'data_prevista': self.data_prevista.isoformat() if self.data_prevista else None,
            'data_conclusao': self.data_conclusao.isoformat() if self.data_conclusao else None,
            'status': self.status,
            'origem': self.origem,
            'pagamento_servico_id': self.pagamento_servico_id,
            'orcamento_item_id': self.orcamento_item_id,
            'servico_id': self.servico_id,
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
        
        # NOTA: Não somamos parcelas pagas aqui porque quando uma parcela é marcada como paga,
        # já é criado um PagamentoServico (contabilizado na Opção A acima).
        # Somar aqui causaria duplicidade de valores!
        
        # Somar todos os pagamentos (sem duplicidade)
        valor_pago = valor_pago_servico + valor_pago_lancamentos
        print(f"[LOG] Valor já pago (PagamentoServico): R$ {valor_pago_servico:.2f}")
        print(f"[LOG] Valor já pago (Lancamentos): R$ {valor_pago_lancamentos:.2f}")
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


@app.route('/obras/<int:obra_id>/cronograma', methods=['GET'])
@jwt_required()
def get_cronograma_obra_by_obra(obra_id):
    """Busca cronograma da obra - rota alternativa"""
    try:
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        cronograma_items = CronogramaObra.query.filter_by(obra_id=obra_id).order_by(CronogramaObra.ordem).all()
        return jsonify([item.to_dict() for item in cronograma_items]), 200
    except Exception as e:
        print(f"[ERRO] get_cronograma_obra_by_obra: {str(e)}")
        return jsonify({'error': 'Erro ao buscar cronograma'}), 500


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


@app.route('/obras/<int:obra_id>/cronograma/exportar-pdf', methods=['GET'])
@jwt_required()
def exportar_cronograma_fisico_pdf(obra_id):
    """Gera PDF do cronograma financeiro da obra (mesmo formato da tela principal)"""
    # Simplesmente chamar a função principal de relatório
    return gerar_relatorio_cronograma_pdf(obra_id)


@app.route('/obras/<int:obra_id>/cronograma-obra/relatorio-pdf', methods=['GET'])
@jwt_required()
def gerar_relatorio_cronograma_obra_pdf(obra_id):
    """
    Gera relatório PDF completo do Cronograma de Obras
    Inclui: status, etapas, medições por área, análise EVM
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        # Buscar obra
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        # Buscar cronograma
        cronograma_items = CronogramaObra.query.filter_by(obra_id=obra_id).order_by(CronogramaObra.ordem).all()
        
        # Criar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Estilos customizados
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        
        style_title = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=20,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#2d3748')
        )
        
        style_subtitle = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Heading2'],
            fontSize=14,
            spaceBefore=15,
            spaceAfter=10,
            textColor=colors.HexColor('#4a5568')
        )
        
        style_normal = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=5
        )
        
        style_small = ParagraphStyle(
            'CustomSmall',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#718096')
        )
        
        # ==================== CABEÇALHO ====================
        elements.append(Paragraph("🏗️ OBRALY", style_title))
        elements.append(Paragraph("RELATÓRIO DE CRONOGRAMA DE OBRAS", ParagraphStyle(
            'SubTitle', parent=styles['Heading2'], fontSize=14, alignment=TA_CENTER, textColor=colors.HexColor('#4f46e5')
        )))
        elements.append(Spacer(1, 10))
        
        # Info da obra
        hoje = datetime.now().strftime('%d/%m/%Y às %H:%M')
        header_data = [
            ['Obra:', obra.nome, 'Data:', hoje],
            ['Gerado por:', current_user.username if current_user else 'Sistema', '', '']
        ]
        header_table = Table(header_data, colWidths=[2.5*cm, 7*cm, 2.5*cm, 5*cm])
        header_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#4a5568')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 15))
        
        # ==================== RESUMO EXECUTIVO ====================
        elements.append(Paragraph("📊 RESUMO EXECUTIVO", style_subtitle))
        
        # Calcular estatísticas
        total_servicos = len(cronograma_items)
        concluidos = sum(1 for s in cronograma_items if s.percentual_conclusao >= 100)
        hoje_date = date.today()
        atrasados = sum(1 for s in cronograma_items if s.data_fim_prevista and s.data_fim_prevista < hoje_date and s.percentual_conclusao < 100)
        em_andamento = sum(1 for s in cronograma_items if s.data_inicio_real and s.percentual_conclusao < 100 and (not s.data_fim_prevista or s.data_fim_prevista >= hoje_date))
        a_iniciar = total_servicos - concluidos - atrasados - em_andamento
        
        # Progresso geral
        if total_servicos > 0:
            progresso_geral = sum(s.percentual_conclusao for s in cronograma_items) / total_servicos
        else:
            progresso_geral = 0
        
        resumo_data = [
            ['Total de Serviços:', str(total_servicos), 'Concluídos:', str(concluidos)],
            ['Em Andamento:', str(em_andamento), 'A Iniciar:', str(a_iniciar)],
            ['Atrasados:', str(atrasados), 'Progresso Geral:', f'{progresso_geral:.1f}%']
        ]
        resumo_table = Table(resumo_data, colWidths=[3.5*cm, 3*cm, 3.5*cm, 3*cm])
        resumo_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f7fafc')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(resumo_table)
        elements.append(Spacer(1, 20))
        
        # ==================== DETALHES POR SERVIÇO ====================
        elements.append(Paragraph("📋 DETALHES POR SERVIÇO", style_subtitle))
        elements.append(Spacer(1, 10))
        
        for idx, servico in enumerate(cronograma_items, 1):
            # Determinar status
            percentual = servico.percentual_conclusao
            if percentual >= 100:
                status = "✅ CONCLUÍDO"
                status_color = colors.HexColor('#28a745')
            elif servico.data_fim_prevista and servico.data_fim_prevista < hoje_date:
                status = "⚠️ ATRASADO"
                status_color = colors.HexColor('#dc3545')
            elif servico.data_inicio_real:
                status = "🔄 EM ANDAMENTO"
                status_color = colors.HexColor('#007bff')
            else:
                status = "⏳ A INICIAR"
                status_color = colors.HexColor('#6c757d')
            
            # Tipo de medição
            if servico.tipo_medicao == 'etapas':
                tipo_texto = "📋 Por Etapas"
            elif servico.tipo_medicao == 'area':
                tipo_texto = f"📐 Por Área ({servico.unidade_medida})"
            else:
                tipo_texto = "🔧 Empreitada"
            
            # Cabeçalho do serviço
            servico_header = [
                [f'#{idx}  {servico.servico_nome}', status, tipo_texto]
            ]
            servico_header_table = Table(servico_header, colWidths=[9*cm, 4*cm, 4*cm])
            servico_header_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (0, 0), 12),
                ('FONTSIZE', (1, 0), (2, 0), 10),
                ('TEXTCOLOR', (1, 0), (1, 0), status_color),
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0f4f8')),
                ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#4f46e5')),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ]))
            elements.append(servico_header_table)
            
            # Cronograma
            data_inicio = servico.data_inicio.strftime('%d/%m/%Y') if servico.data_inicio else '-'
            data_fim = servico.data_fim_prevista.strftime('%d/%m/%Y') if servico.data_fim_prevista else '-'
            data_inicio_real = servico.data_inicio_real.strftime('%d/%m/%Y') if servico.data_inicio_real else '-'
            data_fim_real = servico.data_fim_real.strftime('%d/%m/%Y') if servico.data_fim_real else '-'
            
            cronograma_data = [
                ['📅 CRONOGRAMA', '', '', ''],
                ['Início Previsto:', data_inicio, 'Término Previsto:', data_fim],
                ['Início Real:', data_inicio_real, 'Término Real:', data_fim_real]
            ]
            cronograma_table = Table(cronograma_data, colWidths=[3.5*cm, 4.75*cm, 3.5*cm, 4.75*cm])
            cronograma_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                ('SPAN', (0, 0), (-1, 0)),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 1), (2, -1), 'Helvetica-Bold'),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(cronograma_table)
            
            # Execução
            barra_progresso = '█' * int(percentual / 5) + '░' * (20 - int(percentual / 5))
            
            # Se for por área
            if servico.tipo_medicao == 'area' and servico.area_total:
                area_exec = servico.area_executada or 0
                exec_data = [
                    ['📈 EXECUÇÃO', '', ''],
                    ['Progresso:', f'{barra_progresso} {percentual:.1f}%', ''],
                    ['Área Executada:', f'{area_exec} de {servico.area_total} {servico.unidade_medida}', f'({(area_exec/servico.area_total*100):.1f}%)']
                ]
            else:
                exec_data = [
                    ['📈 EXECUÇÃO', '', ''],
                    ['Progresso:', f'{barra_progresso} {percentual:.1f}%', '']
                ]
            
            exec_table = Table(exec_data, colWidths=[3.5*cm, 10*cm, 3*cm])
            exec_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                ('SPAN', (0, 0), (-1, 0)),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(exec_table)
            
            # ETAPAS (se houver)
            try:
                etapas_list = servico.etapas.order_by(CronogramaEtapa.ordem).all() if servico.etapas else []
                if etapas_list:
                    total_dias_etapas = sum(e.duracao_dias or 0 for e in etapas_list)
                    etapas_header = [[f'📋 ETAPAS ({len(etapas_list)}) - {total_dias_etapas} dias', '', '', '', '']]
                    etapas_data = [['#', 'Etapa', 'Duração', 'Período', 'Status']]
                    
                    for i, etapa in enumerate(etapas_list, 1):
                        etapa_inicio = etapa.data_inicio.strftime('%d/%m') if etapa.data_inicio else '-'
                        etapa_fim = etapa.data_fim.strftime('%d/%m') if etapa.data_fim else '-'
                        
                        if etapa.percentual_conclusao >= 100:
                            etapa_status = '✅ 100%'
                        elif etapa.percentual_conclusao > 0:
                            etapa_status = f'🔄 {etapa.percentual_conclusao:.0f}%'
                        else:
                            etapa_status = '⏳ 0%'
                        
                        etapas_data.append([
                            str(i),
                            etapa.nome[:25] + '...' if len(etapa.nome) > 25 else etapa.nome,
                            f'{etapa.duracao_dias} dias',
                            f'{etapa_inicio} → {etapa_fim}',
                            etapa_status
                        ])
                    
                    etapas_table = Table(etapas_header + etapas_data, colWidths=[1*cm, 6*cm, 2.5*cm, 4*cm, 3*cm])
                    etapas_table.setStyle(TableStyle([
                        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                        ('FONTSIZE', (0, 0), (-1, -1), 8),
                        ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                        ('SPAN', (0, 0), (-1, 0)),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#f0f4f8')),
                        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
                        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                        ('INNERGRID', (0, 1), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('LEFTPADDING', (0, 0), (-1, -1), 5),
                        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
                        ('ALIGN', (2, 1), (2, -1), 'CENTER'),
                        ('ALIGN', (4, 1), (4, -1), 'CENTER'),
                    ]))
                    elements.append(etapas_table)
            except Exception as e:
                print(f"[AVISO] Erro ao carregar etapas para PDF: {str(e)}")
            
            # ANÁLISE EVM
            try:
                # Buscar dados financeiros
                servico_db = Servico.query.filter_by(obra_id=obra_id, nome=servico.servico_nome).first()
                if servico_db:
                    valor_total = (servico_db.valor_global_mao_de_obra or 0) + (servico_db.valor_global_material or 0)
                    
                    # Buscar pagamentos
                    pagamentos = PagamentoServico.query.filter_by(servico_id=servico_db.id).all()
                    valor_pago = sum(p.valor_pago or 0 for p in pagamentos)
                    
                    if valor_total > 0:
                        percentual_pago = (valor_pago / valor_total) * 100
                        percentual_exec = percentual
                        diferenca = percentual_exec - percentual_pago
                        
                        if diferenca >= 5:
                            evm_status = "🟢 ADIANTADO"
                            evm_color = colors.HexColor('#28a745')
                        elif diferenca >= -5:
                            evm_status = "🔵 NO PRAZO"
                            evm_color = colors.HexColor('#007bff')
                        elif diferenca >= -15:
                            evm_status = "🟡 ATENÇÃO"
                            evm_color = colors.HexColor('#ffc107')
                        else:
                            evm_status = "🔴 CRÍTICO"
                            evm_color = colors.HexColor('#dc3545')
                        
                        evm_data = [
                            ['💰 ANÁLISE FINANCEIRA (EVM)', evm_status, ''],
                            ['Total Orçado:', f'R$ {valor_total:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'), ''],
                            ['Já Pago:', f'R$ {valor_pago:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'), f'({percentual_pago:.1f}%)'],
                            ['Pago vs Executado:', f'{percentual_pago:.0f}% pago | {percentual_exec:.0f}% executado', f'Diferença: {diferenca:+.0f}%']
                        ]
                        evm_table = Table(evm_data, colWidths=[4*cm, 8.5*cm, 4*cm])
                        evm_table.setStyle(TableStyle([
                            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                            ('FONTSIZE', (0, 0), (-1, -1), 9),
                            ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                            ('TEXTCOLOR', (1, 0), (1, 0), evm_color),
                            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fef3c7')),
                            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fffbeb')),
                            ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#fbbf24')),
                            ('TOPPADDING', (0, 0), (-1, -1), 5),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                            ('LEFTPADDING', (0, 0), (-1, -1), 8),
                        ]))
                        elements.append(evm_table)
            except Exception as e:
                print(f"[AVISO] Erro ao calcular EVM para PDF: {str(e)}")
            
            # Observações
            if servico.observacoes:
                obs_data = [['📝 Observações:', servico.observacoes[:200]]]
                obs_table = Table(obs_data, colWidths=[3.5*cm, 13*cm])
                obs_table.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#718096')),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ]))
                elements.append(obs_table)
            
            elements.append(Spacer(1, 15))
        
        # ==================== LEGENDA ====================
        elements.append(Paragraph("📋 LEGENDA", style_subtitle))
        
        legenda_data = [
            ['STATUS', 'INDICADOR EVM'],
            ['✅ Concluído - Serviço 100% executado', '🟢 ADIANTADO - Execução maior que pagamento (+5%)'],
            ['🔄 Em Andamento - Em execução', '🔵 NO PRAZO - Proporcional (±5%)'],
            ['⏳ A Iniciar - Não iniciado', '🟡 ATENÇÃO - Pagou mais (-5% a -15%)'],
            ['⚠️ Atrasado - Passou do prazo', '🔴 CRÍTICO - Pagou muito mais (-15% ou mais)'],
        ]
        legenda_table = Table(legenda_data, colWidths=[8.5*cm, 8.5*cm])
        legenda_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(legenda_table)
        
        # Rodapé
        elements.append(Spacer(1, 20))
        elements.append(Paragraph(f"Gerado em: {hoje} - Obraly v1.0", ParagraphStyle(
            'Footer', parent=styles['Normal'], fontSize=8, alignment=TA_CENTER, textColor=colors.HexColor('#a0aec0')
        )))
        
        # Gerar PDF
        doc.build(elements)
        buffer.seek(0)
        
        # Retornar arquivo
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'cronograma_obras_{obra.nome}_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf'
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"[ERRO] gerar_relatorio_cronograma_obra_pdf: {str(e)}\n{error_details}")
        return jsonify({'error': f'Erro ao gerar PDF: {str(e)}'}), 500


@app.route('/cronograma', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def create_cronograma():
    """Cria uma nova etapa do cronograma"""
    # Tratar OPTIONS para CORS preflight
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        # Verificar autenticação
        verify_jwt_in_request()
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        data = request.json
        required_fields = ['obra_id', 'servico_nome', 'data_inicio', 'data_fim_prevista']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Campo obrigatório ausente: {field}'}), 400
        
        obra = Obra.query.get(data['obra_id'])
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        # Verificar acesso à obra
        if not user_has_access_to_obra(current_user, data['obra_id']):
            return jsonify({'error': 'Acesso negado a esta obra'}), 403
        
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
        error_details = traceback.format_exc()
        print(f"[ERRO] create_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao criar etapa do cronograma'}), 500


@app.route('/cronograma/<int:cronograma_id>', methods=['PUT', 'OPTIONS'])
@jwt_required(optional=True)
def update_cronograma(cronograma_id):
    """Atualiza uma etapa do cronograma"""
    # Tratar OPTIONS para CORS preflight
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'PUT, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        # Verificar autenticação
        verify_jwt_in_request()
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        data = request.json
        
        item = CronogramaObra.query.get(cronograma_id)
        if not item:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        # Verificar acesso à obra
        if not user_has_access_to_obra(current_user, item.obra_id):
            return jsonify({'error': 'Acesso negado a esta obra'}), 403
        
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
        
        # Validar datas apenas se ambas existirem
        if item.data_fim_prevista and item.data_inicio and item.data_fim_prevista < item.data_inicio:
            return jsonify({'error': 'Data de término não pode ser anterior à data de início'}), 400
        
        item.updated_at = datetime.utcnow()
        db.session.commit()
        
        print(f"[LOG] Cronograma atualizado: ID={item.id}, %={item.percentual_conclusao}")
        return jsonify(item.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] update_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao atualizar cronograma'}), 500


@app.route('/cronograma/<int:cronograma_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required(optional=True)
def delete_cronograma(cronograma_id):
    """Deleta uma etapa do cronograma"""
    # Tratar OPTIONS para CORS preflight
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'DELETE, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        # Verificar autenticação
        verify_jwt_in_request()
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        item = CronogramaObra.query.get(cronograma_id)
        if not item:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        # Verificar acesso à obra
        if not user_has_access_to_obra(current_user, item.obra_id):
            return jsonify({'error': 'Acesso negado a esta obra'}), 403
        
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
# ENDPOINTS DE ETAPAS DO CRONOGRAMA
# ==============================================================================

def recalcular_datas_etapas(cronograma_id):
    """
    Recalcula as datas das etapas em cascata considerando hierarquia.
    
    1. Para cada ETAPA PAI:
       - Recalcula datas das SUBETAPAS em cascata
       - Atualiza datas da etapa pai baseado nas subetapas
    
    2. Para ETAPAS PAI entre si:
       - Aplica condições de início (apos_termino, dias_apos, dias_antes)
    """
    try:
        # Buscar apenas etapas pai (não subetapas)
        etapas_pai = CronogramaEtapa.query.filter_by(
            cronograma_id=cronograma_id,
            etapa_pai_id=None
        ).order_by(CronogramaEtapa.ordem).all()
        
        if not etapas_pai:
            # Fallback: se não tem etapas pai, pode ser estrutura antiga
            etapas = CronogramaEtapa.query.filter_by(cronograma_id=cronograma_id).order_by(CronogramaEtapa.ordem).all()
            for i, etapa in enumerate(etapas):
                if i == 0:
                    etapa.calcular_data_fim()
                else:
                    if not etapa.inicio_ajustado_manualmente and etapas[i-1].data_fim:
                        etapa.data_inicio = etapas[i - 1].data_fim + timedelta(days=1)
                    etapa.calcular_data_fim()
        else:
            # Nova estrutura hierárquica
            for i, etapa_pai in enumerate(etapas_pai):
                # 1. Recalcular subetapas em cascata
                subetapas = CronogramaEtapa.query.filter_by(etapa_pai_id=etapa_pai.id).order_by(CronogramaEtapa.ordem).all()
                
                for j, sub in enumerate(subetapas):
                    if j == 0:
                        # Primeira subetapa: só calcular data_fim
                        sub.calcular_data_fim()
                    else:
                        # Subetapas seguintes
                        sub_anterior = subetapas[j - 1]
                        if not sub.inicio_ajustado_manualmente and sub_anterior.data_fim:
                            sub.data_inicio = sub_anterior.data_fim + timedelta(days=1)
                        sub.calcular_data_fim()
                
                # 2. Atualizar datas da etapa pai baseado nas subetapas
                etapa_pai.calcular_datas_das_subetapas()
                etapa_pai.percentual_conclusao = etapa_pai.calcular_percentual_das_subetapas()
                
                # 3. Aplicar condições entre etapas pai
                if i > 0 and not etapa_pai.inicio_ajustado_manualmente:
                    # Determinar etapa anterior (pode ser específica ou a anterior na ordem)
                    if etapa_pai.etapa_anterior_id:
                        etapa_anterior = CronogramaEtapa.query.get(etapa_pai.etapa_anterior_id)
                    else:
                        etapa_anterior = etapas_pai[i - 1]
                    
                    if etapa_anterior and etapa_anterior.data_fim:
                        nova_data = None
                        
                        if etapa_pai.tipo_condicao == 'apos_termino' or not etapa_pai.tipo_condicao:
                            nova_data = etapa_anterior.data_fim + timedelta(days=1)
                        elif etapa_pai.tipo_condicao == 'dias_apos':
                            nova_data = etapa_anterior.data_fim + timedelta(days=(etapa_pai.dias_offset or 0) + 1)
                        elif etapa_pai.tipo_condicao == 'dias_antes':
                            nova_data = etapa_anterior.data_fim - timedelta(days=(etapa_pai.dias_offset or 0))
                        
                        if nova_data and etapa_pai.data_inicio != nova_data:
                            # Calcular diferença para ajustar subetapas
                            if etapa_pai.data_inicio:
                                diferenca = (nova_data - etapa_pai.data_inicio).days
                                if diferenca != 0 and subetapas:
                                    primeira_sub = subetapas[0]
                                    if not primeira_sub.inicio_ajustado_manualmente:
                                        primeira_sub.data_inicio = nova_data
                                        primeira_sub.calcular_data_fim()
                                        # Recalcular subetapas em cascata novamente
                                        for k in range(1, len(subetapas)):
                                            if not subetapas[k].inicio_ajustado_manualmente:
                                                subetapas[k].data_inicio = subetapas[k-1].data_fim + timedelta(days=1)
                                            subetapas[k].calcular_data_fim()
                                        # Atualizar etapa pai
                                        etapa_pai.calcular_datas_das_subetapas()
        
        # Atualizar datas do cronograma pai
        cronograma = CronogramaObra.query.get(cronograma_id)
        if cronograma:
            cronograma.atualizar_datas_por_etapas()
            if cronograma.tipo_medicao == 'etapas':
                cronograma.percentual_conclusao = cronograma.calcular_percentual_por_etapas()
                
    except Exception as e:
        print(f"[AVISO] Erro ao recalcular datas das etapas: {str(e)}")


@app.route('/cronograma/<int:cronograma_id>/etapas', methods=['GET'])
@jwt_required()
def get_etapas_cronograma(cronograma_id):
    """Lista todas as etapas PAI de um item do cronograma (subetapas vêm dentro via to_dict)"""
    try:
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'error': 'Cronograma não encontrado'}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        # Buscar apenas etapas pai (etapa_pai_id IS NULL)
        # Subetapas são retornadas dentro de cada etapa pai via to_dict()
        try:
            etapas = CronogramaEtapa.query.filter_by(
                cronograma_id=cronograma_id,
                etapa_pai_id=None
            ).order_by(CronogramaEtapa.ordem).all()
        except:
            # Fallback para compatibilidade (se coluna etapa_pai_id não existir)
            etapas = CronogramaEtapa.query.filter_by(cronograma_id=cronograma_id).order_by(CronogramaEtapa.ordem).all()
        
        return jsonify([etapa.to_dict() for etapa in etapas]), 200
    except Exception as e:
        print(f"[ERRO] get_etapas_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao buscar etapas'}), 500


@app.route('/cronograma/<int:cronograma_id>/etapas', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def create_etapa_cronograma(cronograma_id):
    """
    Cria uma nova etapa ou subetapa no cronograma
    
    Para criar ETAPA PAI: não passar etapa_pai_id
    Para criar SUBETAPA: passar etapa_pai_id
    
    Campos especiais para ETAPA PAI:
    - etapa_anterior_id: ID da etapa anterior para condições de início
    - tipo_condicao: 'apos_termino', 'dias_apos', 'dias_antes', 'manual'
    - dias_offset: número de dias para offset
    """
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        verify_jwt_in_request()
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'error': 'Cronograma não encontrado'}), 404
        
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        data = request.json
        
        # Campos obrigatórios
        if 'nome' not in data:
            return jsonify({'error': 'Campo obrigatório: nome'}), 400
        
        # Verificar se é subetapa
        etapa_pai_id = data.get('etapa_pai_id')
        is_subetapa = etapa_pai_id is not None
        
        if is_subetapa:
            # === CRIANDO SUBETAPA ===
            etapa_pai = CronogramaEtapa.query.get(etapa_pai_id)
            if not etapa_pai:
                return jsonify({'error': 'Etapa pai não encontrada'}), 404
            
            # Determinar ordem (última subetapa + 1)
            ultima_sub = CronogramaEtapa.query.filter_by(etapa_pai_id=etapa_pai_id).order_by(CronogramaEtapa.ordem.desc()).first()
            nova_ordem = (ultima_sub.ordem + 1) if ultima_sub else 1
            
            # Determinar data_inicio
            duracao_dias = int(data.get('duracao_dias', 1))
            
            if 'data_inicio' in data and data['data_inicio']:
                data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
                inicio_ajustado = True
            elif ultima_sub and ultima_sub.data_fim:
                data_inicio = ultima_sub.data_fim + timedelta(days=1)
                inicio_ajustado = False
            elif etapa_pai.data_inicio:
                data_inicio = etapa_pai.data_inicio
                inicio_ajustado = False
            else:
                data_inicio = cronograma.data_inicio or date.today()
                inicio_ajustado = False
            
            data_fim = data_inicio + timedelta(days=duracao_dias - 1) if data_inicio else None
            
            nova_etapa = CronogramaEtapa(
                cronograma_id=cronograma_id,
                etapa_pai_id=etapa_pai_id,
                nome=data['nome'],
                ordem=nova_ordem,
                duracao_dias=duracao_dias,
                data_inicio=data_inicio,
                data_fim=data_fim,
                inicio_ajustado_manualmente=inicio_ajustado,
                percentual_conclusao=float(data.get('percentual_conclusao', 0)),
                observacoes=data.get('observacoes')
            )
        else:
            # === CRIANDO ETAPA PAI ===
            # Determinar ordem entre etapas pai
            ultima_etapa_pai = CronogramaEtapa.query.filter_by(
                cronograma_id=cronograma_id,
                etapa_pai_id=None
            ).order_by(CronogramaEtapa.ordem.desc()).first()
            nova_ordem = (ultima_etapa_pai.ordem + 1) if ultima_etapa_pai else 1
            
            # Condições de início (apenas para etapa pai)
            etapa_anterior_id = data.get('etapa_anterior_id')
            tipo_condicao = data.get('tipo_condicao', 'apos_termino')
            dias_offset = int(data.get('dias_offset', 0))
            
            # Determinar data_inicio baseado na condição
            data_inicio = None
            inicio_ajustado = False
            
            if 'data_inicio' in data and data['data_inicio']:
                data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
                inicio_ajustado = True
                tipo_condicao = 'manual'
            elif etapa_anterior_id:
                etapa_anterior = CronogramaEtapa.query.get(etapa_anterior_id)
                if etapa_anterior and etapa_anterior.data_fim:
                    if tipo_condicao == 'apos_termino':
                        data_inicio = etapa_anterior.data_fim + timedelta(days=1)
                    elif tipo_condicao == 'dias_apos':
                        data_inicio = etapa_anterior.data_fim + timedelta(days=dias_offset + 1)
                    elif tipo_condicao == 'dias_antes':
                        data_inicio = etapa_anterior.data_fim - timedelta(days=dias_offset)
            elif ultima_etapa_pai and ultima_etapa_pai.data_fim:
                # Usar última etapa como referência automática
                data_inicio = ultima_etapa_pai.data_fim + timedelta(days=1)
                etapa_anterior_id = ultima_etapa_pai.id
            else:
                # Primeira etapa: usar data do cronograma
                data_inicio = cronograma.data_inicio or date.today()
            
            nova_etapa = CronogramaEtapa(
                cronograma_id=cronograma_id,
                etapa_pai_id=None,  # É etapa pai
                nome=data['nome'],
                ordem=nova_ordem,
                duracao_dias=None,  # Calculado das subetapas
                data_inicio=data_inicio,
                data_fim=data_inicio,  # Será atualizado quando adicionar subetapas
                inicio_ajustado_manualmente=inicio_ajustado,
                etapa_anterior_id=etapa_anterior_id,
                tipo_condicao=tipo_condicao,
                dias_offset=dias_offset,
                percentual_conclusao=0,
                observacoes=data.get('observacoes')
            )
        
        db.session.add(nova_etapa)
        
        # Atualizar tipo do cronograma para 'etapas' se ainda não for
        if cronograma.tipo_medicao != 'etapas':
            cronograma.tipo_medicao = 'etapas'
        
        db.session.commit()
        
        # Se criou subetapa, atualizar datas da etapa pai
        if is_subetapa:
            recalcular_subetapas_cascata(etapa_pai_id)
        
        # Recalcular datas e percentuais do cronograma
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        tipo = "Subetapa" if is_subetapa else "Etapa"
        print(f"[LOG] {tipo} criada: ID={nova_etapa.id}, Nome={nova_etapa.nome}, Cronograma={cronograma_id}")
        return jsonify(nova_etapa.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] create_etapa_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': f'Erro ao criar etapa: {str(e)}'}), 500


def recalcular_subetapas_cascata(etapa_pai_id):
    """Recalcula datas das subetapas em cascata e atualiza a etapa pai"""
    try:
        subetapas = CronogramaEtapa.query.filter_by(etapa_pai_id=etapa_pai_id).order_by(CronogramaEtapa.ordem).all()
        
        for i, sub in enumerate(subetapas):
            if i == 0:
                sub.calcular_data_fim()
            else:
                sub_anterior = subetapas[i - 1]
                if not sub.inicio_ajustado_manualmente and sub_anterior.data_fim:
                    sub.data_inicio = sub_anterior.data_fim + timedelta(days=1)
                sub.calcular_data_fim()
        
        # Atualizar datas da etapa pai
        etapa_pai = CronogramaEtapa.query.get(etapa_pai_id)
        if etapa_pai:
            etapa_pai.calcular_datas_das_subetapas()
        
        db.session.commit()
    except Exception as e:
        print(f"[AVISO] Erro ao recalcular subetapas: {str(e)}")


@app.route('/cronograma/<int:cronograma_id>/etapas/<int:etapa_id>', methods=['PUT', 'OPTIONS'])
@jwt_required(optional=True)
def update_etapa_cronograma(cronograma_id, etapa_id):
    """Atualiza uma etapa do cronograma"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'PUT, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        verify_jwt_in_request()
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        etapa = CronogramaEtapa.query.get(etapa_id)
        if not etapa or etapa.cronograma_id != cronograma_id:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        data = request.json
        
        if 'nome' in data:
            etapa.nome = data['nome']
        
        if 'ordem' in data:
            etapa.ordem = int(data['ordem'])
        
        if 'duracao_dias' in data:
            etapa.duracao_dias = int(data['duracao_dias'])
        
        if 'data_inicio' in data and data['data_inicio']:
            etapa.data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
            etapa.inicio_ajustado_manualmente = True
        
        if 'percentual_conclusao' in data:
            etapa.percentual_conclusao = max(0, min(100, float(data['percentual_conclusao'])))
        
        if 'observacoes' in data:
            etapa.observacoes = data['observacoes']
        
        # Resetar ajuste manual se solicitado
        if data.get('resetar_ajuste_manual'):
            etapa.inicio_ajustado_manualmente = False
        
        etapa.updated_at = datetime.utcnow()
        db.session.commit()
        
        # Recalcular datas em cascata
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        # Recarregar etapa atualizada
        etapa = CronogramaEtapa.query.get(etapa_id)
        
        print(f"[LOG] Etapa atualizada: ID={etapa_id}, Nome={etapa.nome}")
        return jsonify(etapa.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] update_etapa_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao atualizar etapa'}), 500


@app.route('/cronograma/<int:cronograma_id>/etapas/<int:etapa_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required(optional=True)
def delete_etapa_cronograma(cronograma_id, etapa_id):
    """Exclui uma etapa do cronograma"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'DELETE, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        verify_jwt_in_request()
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        etapa = CronogramaEtapa.query.get(etapa_id)
        if not etapa or etapa.cronograma_id != cronograma_id:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        nome_etapa = etapa.nome
        db.session.delete(etapa)
        db.session.commit()
        
        # Recalcular datas das etapas restantes
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        print(f"[LOG] Etapa excluída: ID={etapa_id}, Nome={nome_etapa}")
        return jsonify({'message': 'Etapa excluída com sucesso'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] delete_etapa_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao excluir etapa'}), 500


@app.route('/cronograma/<int:cronograma_id>/etapas/reordenar', methods=['PUT', 'OPTIONS'])
@jwt_required(optional=True)
def reordenar_etapas_cronograma(cronograma_id):
    """Reordena as etapas do cronograma"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'PUT, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        verify_jwt_in_request()
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'error': 'Cronograma não encontrado'}), 404
        
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        data = request.json
        # Espera: {"ordem": [{"id": 1, "ordem": 1}, {"id": 2, "ordem": 2}, ...]}
        
        if 'ordem' not in data:
            return jsonify({'error': 'Campo obrigatório: ordem'}), 400
        
        for item in data['ordem']:
            etapa = CronogramaEtapa.query.get(item['id'])
            if etapa and etapa.cronograma_id == cronograma_id:
                etapa.ordem = item['ordem']
                # Resetar ajuste manual para recalcular em cascata
                if item.get('resetar_ajuste'):
                    etapa.inicio_ajustado_manualmente = False
        
        db.session.commit()
        
        # Recalcular datas
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        return jsonify({'message': 'Etapas reordenadas com sucesso'}), 200
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] reordenar_etapas_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao reordenar etapas'}), 500


# ==============================================================================
# MIGRATION: Estrutura Hierárquica de Etapas (Etapa Pai / Subetapas)
# ==============================================================================

@app.route('/setup/migrate-etapas-hierarquia', methods=['GET'])
def setup_migrate_etapas_hierarquia():
    """
    ROTA TEMPORÁRIA - Adiciona suporte a Etapas Pai e Subetapas
    Acesse: https://backend-production-78c9.up.railway.app/setup/migrate-etapas-hierarquia
    
    O que faz:
    1. Adiciona colunas: etapa_pai_id, etapa_anterior_id, tipo_condicao, dias_offset
    2. Torna data_inicio, data_fim, duracao_dias nullable
    3. Cria uma Etapa Pai padrão para cada serviço que já tem etapas
    """
    try:
        resultados = []
        
        # 1. Adicionar coluna etapa_pai_id (auto-referência)
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS etapa_pai_id INTEGER REFERENCES cronograma_etapa(id) ON DELETE CASCADE;
            """))
            db.session.commit()
            resultados.append("✅ Coluna etapa_pai_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ etapa_pai_id: {str(e)}")
        
        # 2. Adicionar coluna etapa_anterior_id (para condições entre etapas)
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS etapa_anterior_id INTEGER REFERENCES cronograma_etapa(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ Coluna etapa_anterior_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ etapa_anterior_id: {str(e)}")
        
        # 3. Adicionar coluna tipo_condicao
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS tipo_condicao VARCHAR(20);
            """))
            db.session.commit()
            resultados.append("✅ Coluna tipo_condicao adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ tipo_condicao: {str(e)}")
        
        # 4. Adicionar coluna dias_offset
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS dias_offset INTEGER DEFAULT 0;
            """))
            db.session.commit()
            resultados.append("✅ Coluna dias_offset adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ dias_offset: {str(e)}")
        
        # 5. Tornar data_inicio nullable
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa ALTER COLUMN data_inicio DROP NOT NULL;
            """))
            db.session.commit()
            resultados.append("✅ data_inicio agora aceita NULL")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ data_inicio: {str(e)}")
        
        # 6. Tornar data_fim nullable
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa ALTER COLUMN data_fim DROP NOT NULL;
            """))
            db.session.commit()
            resultados.append("✅ data_fim agora aceita NULL")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ data_fim: {str(e)}")
        
        # 7. Tornar duracao_dias nullable
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa ALTER COLUMN duracao_dias DROP NOT NULL;
            """))
            db.session.commit()
            resultados.append("✅ duracao_dias agora aceita NULL")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ duracao_dias: {str(e)}")
        
        # 8. Criar índice para etapa_pai_id
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_cronograma_etapa_pai_id 
                ON cronograma_etapa(etapa_pai_id);
            """))
            db.session.commit()
            resultados.append("✅ Índice etapa_pai_id criado")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índice: {str(e)}")
        
        # 9. MIGRAÇÃO DE DADOS: Criar Etapa Pai para cada cronograma que já tem etapas
        try:
            # Buscar cronogramas que têm etapas sem etapa_pai_id
            result = db.session.execute(db.text("""
                SELECT DISTINCT cronograma_id 
                FROM cronograma_etapa 
                WHERE etapa_pai_id IS NULL
            """))
            cronogramas_com_etapas = [row[0] for row in result.fetchall()]
            
            for cronograma_id in cronogramas_com_etapas:
                # Verificar se já existe uma etapa pai (sem etapa_pai_id e com subetapas)
                # Buscar a primeira data e criar etapa pai
                result = db.session.execute(db.text("""
                    SELECT MIN(data_inicio), MIN(data_fim)
                    FROM cronograma_etapa 
                    WHERE cronograma_id = :cid AND etapa_pai_id IS NULL
                """), {'cid': cronograma_id})
                row = result.fetchone()
                data_inicio = row[0]
                data_fim = row[1]
                
                # Criar a Etapa Pai
                db.session.execute(db.text("""
                    INSERT INTO cronograma_etapa 
                    (cronograma_id, nome, ordem, data_inicio, data_fim, percentual_conclusao, created_at, updated_at)
                    VALUES (:cid, 'Etapa 1', 1, :di, :df, 0, NOW(), NOW())
                    RETURNING id
                """), {'cid': cronograma_id, 'di': data_inicio, 'df': data_fim})
                etapa_pai_id = db.session.execute(db.text("SELECT lastval()")).scalar()
                
                # Atualizar as etapas existentes para serem subetapas
                db.session.execute(db.text("""
                    UPDATE cronograma_etapa 
                    SET etapa_pai_id = :pai_id 
                    WHERE cronograma_id = :cid 
                    AND etapa_pai_id IS NULL 
                    AND id != :pai_id
                """), {'pai_id': etapa_pai_id, 'cid': cronograma_id})
                
                db.session.commit()
                resultados.append(f"✅ Cronograma {cronograma_id}: Etapa Pai criada, subetapas vinculadas")
            
            if not cronogramas_com_etapas:
                resultados.append("ℹ️ Nenhum cronograma com etapas existentes para migrar")
                
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Migração de dados: {str(e)}")
        
        return jsonify({
            "status": "Migration de Hierarquia de Etapas executada!",
            "resultados": resultados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ROTA SEM AUTENTICAÇÃO - Use uma única vez e depois remova!
@app.route('/setup/create-cronograma-etapa-table', methods=['GET'])
def setup_create_cronograma_etapa():
    """
    ROTA TEMPORÁRIA SEM AUTENTICAÇÃO - Cria tabela cronograma_etapa
    Acesse: https://seu-backend.railway.app/setup/create-cronograma-etapa-table
    REMOVA ESTA ROTA APÓS USAR!
    """
    try:
        resultados = []
        
        # Criar tabela
        try:
            db.session.execute(db.text("""
                CREATE TABLE IF NOT EXISTS cronograma_etapa (
                    id SERIAL PRIMARY KEY,
                    cronograma_id INTEGER NOT NULL REFERENCES cronograma_obra(id) ON DELETE CASCADE,
                    nome VARCHAR(200) NOT NULL,
                    ordem INTEGER NOT NULL DEFAULT 1,
                    duracao_dias INTEGER NOT NULL DEFAULT 1,
                    data_inicio DATE NOT NULL,
                    data_fim DATE NOT NULL,
                    inicio_ajustado_manualmente BOOLEAN DEFAULT FALSE,
                    percentual_conclusao FLOAT NOT NULL DEFAULT 0.0,
                    observacoes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            db.session.commit()
            resultados.append("✅ Tabela cronograma_etapa criada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Tabela cronograma_etapa já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao criar tabela: {str(e)}")
        
        # Criar índice
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_cronograma_etapa_cronograma_id 
                ON cronograma_etapa(cronograma_id);
            """))
            db.session.commit()
            resultados.append("✅ Índice criado com sucesso")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índice: {str(e)}")
        
        return jsonify({
            "status": "Migration executada com sucesso!",
            "resultados": resultados,
            "aviso": "REMOVA esta rota do código após usar!"
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/admin/migrate-create-cronograma-etapa', methods=['GET'])
@jwt_required()
@check_permission(roles=['master'])
def migrate_create_cronograma_etapa():
    """
    ROTA TEMPORÁRIA - Cria tabela cronograma_etapa
    Apenas usuários MASTER podem executar
    Acesse: https://seu-backend.railway.app/admin/migrate-create-cronograma-etapa
    """
    try:
        resultados = []
        
        # Criar tabela
        try:
            db.session.execute(db.text("""
                CREATE TABLE IF NOT EXISTS cronograma_etapa (
                    id SERIAL PRIMARY KEY,
                    cronograma_id INTEGER NOT NULL REFERENCES cronograma_obra(id) ON DELETE CASCADE,
                    nome VARCHAR(200) NOT NULL,
                    ordem INTEGER NOT NULL DEFAULT 1,
                    duracao_dias INTEGER NOT NULL DEFAULT 1,
                    data_inicio DATE NOT NULL,
                    data_fim DATE NOT NULL,
                    inicio_ajustado_manualmente BOOLEAN DEFAULT FALSE,
                    percentual_conclusao FLOAT NOT NULL DEFAULT 0.0,
                    observacoes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            db.session.commit()
            resultados.append("✅ Tabela cronograma_etapa criada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Tabela cronograma_etapa já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao criar tabela: {str(e)}")
        
        # Criar índice
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_cronograma_etapa_cronograma_id 
                ON cronograma_etapa(cronograma_id);
            """))
            db.session.commit()
            resultados.append("✅ Índice criado com sucesso")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índice: {str(e)}")
        
        return jsonify({
            "status": "Migration executada",
            "resultados": resultados
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


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


@app.route('/admin/recuperar-parcelas-pagas', methods=['POST', 'GET'])
def recuperar_parcelas_pagas():
    """
    RECUPERAÇÃO DE DADOS: Reconstrói parcelas pagas a partir dos lançamentos existentes.
    
    Parâmetros:
    - preview=true : Apenas mostra o que seria feito, sem alterar dados
    - dias=30 : Só considera lançamentos dos últimos N dias (padrão: 30)
    
    Quando uma parcela SEM serviço é marcada como paga, ela cria um Lançamento.
    Esta rota usa esses lançamentos para reconstruir as parcelas que foram perdidas.
    """
    import re
    
    try:
        # Parâmetros
        preview_mode = request.args.get('preview', 'true').lower() == 'true'
        dias_limite = int(request.args.get('dias', 30))
        
        data_limite = date.today() - timedelta(days=dias_limite)
        
        resultados = {
            "modo": "PREVIEW (nenhuma alteração feita)" if preview_mode else "EXECUÇÃO",
            "filtro_dias": dias_limite,
            "data_minima": data_limite.isoformat(),
            "lancamentos_analisados": 0,
            "parcelas_a_recuperar": 0,
            "parcelas_ja_existentes": 0,
            "parcelas_previstas_a_criar": 0,
            "erros": [],
            "acoes": []
        }
        
        # 1. Buscar lançamentos RECENTES que têm padrão de parcela na descrição
        todos_lancamentos = Lancamento.query.filter(
            Lancamento.status == 'Pago',
            Lancamento.data >= data_limite  # Só lançamentos recentes
        ).all()
        
        # Regex para encontrar padrão de parcela
        padrao_parcela = re.compile(r'^(.+?)\s*\((?:Parcela\s*)?(\d+)/(\d+)\)$', re.IGNORECASE)
        
        for lanc in todos_lancamentos:
            if not lanc.descricao:
                continue
                
            match = padrao_parcela.match(lanc.descricao.strip())
            if not match:
                continue
            
            resultados["lancamentos_analisados"] += 1
            
            descricao_base = match.group(1).strip()
            numero_parcela = int(match.group(2))
            total_parcelas = int(match.group(3))
            
            # 2. Buscar o PagamentoParcelado correspondente
            pag_parcelado = PagamentoParcelado.query.filter(
                PagamentoParcelado.obra_id == lanc.obra_id,
                PagamentoParcelado.descricao.ilike(f"%{descricao_base}%")
            ).first()
            
            if not pag_parcelado:
                resultados["erros"].append(f"⚠️ PagamentoParcelado não encontrado para: {lanc.descricao} (obra {lanc.obra_id}) - IGNORADO")
                continue
            
            # 3. Verificar se a parcela individual já existe
            parcela_existente = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                ParcelaIndividual.numero_parcela == numero_parcela
            ).first()
            
            if parcela_existente:
                if parcela_existente.status != 'Pago':
                    resultados["acoes"].append(f"🔄 Atualizar status para Pago: {lanc.descricao}")
                    resultados["parcelas_a_recuperar"] += 1
                    
                    if not preview_mode:
                        parcela_existente.status = 'Pago'
                        parcela_existente.data_pagamento = lanc.data
                else:
                    resultados["parcelas_ja_existentes"] += 1
            else:
                resultados["acoes"].append(f"✅ Criar parcela paga: {lanc.descricao}")
                resultados["parcelas_a_recuperar"] += 1
                
                if not preview_mode:
                    # Calcular data de vencimento baseada na periodicidade
                    if pag_parcelado.periodicidade == 'Semanal':
                        dias_offset = (numero_parcela - 1) * 7
                    elif pag_parcelado.periodicidade == 'Quinzenal':
                        dias_offset = (numero_parcela - 1) * 15
                    else:  # Mensal
                        dias_offset = (numero_parcela - 1) * 30
                    
                    data_vencimento = pag_parcelado.data_primeira_parcela + timedelta(days=dias_offset)
                    
                    nova_parcela = ParcelaIndividual(
                        pagamento_parcelado_id=pag_parcelado.id,
                        numero_parcela=numero_parcela,
                        valor_parcela=lanc.valor_total or pag_parcelado.valor_parcela,
                        data_vencimento=data_vencimento,
                        status='Pago',
                        data_pagamento=lanc.data,
                        forma_pagamento=None,
                        observacao=f"Recuperado do lançamento {lanc.id}"
                    )
                    db.session.add(nova_parcela)
            
            # 4. Atualizar contador de parcelas pagas no PagamentoParcelado
            if not preview_mode:
                parcelas_pagas_count = ParcelaIndividual.query.filter(
                    ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                    ParcelaIndividual.status == 'Pago'
                ).count()
                pag_parcelado.parcelas_pagas = parcelas_pagas_count
                
                if parcelas_pagas_count >= pag_parcelado.numero_parcelas:
                    pag_parcelado.status = 'Concluído'
        
        # 5. Verificar parcelas faltantes (não pagas) para PagamentoParcelados recentes
        parcelados_recentes = PagamentoParcelado.query.filter(
            PagamentoParcelado.data_primeira_parcela >= data_limite
        ).all()
        
        for pag in parcelados_recentes:
            for num in range(1, pag.numero_parcelas + 1):
                parcela_existe = ParcelaIndividual.query.filter(
                    ParcelaIndividual.pagamento_parcelado_id == pag.id,
                    ParcelaIndividual.numero_parcela == num
                ).first()
                
                if not parcela_existe:
                    resultados["acoes"].append(f"📝 Criar parcela prevista: {pag.descricao} ({num}/{pag.numero_parcelas})")
                    resultados["parcelas_previstas_a_criar"] += 1
                    
                    if not preview_mode:
                        if pag.periodicidade == 'Semanal':
                            dias_offset = (num - 1) * 7
                        elif pag.periodicidade == 'Quinzenal':
                            dias_offset = (num - 1) * 15
                        else:
                            dias_offset = (num - 1) * 30
                        
                        data_vencimento = pag.data_primeira_parcela + timedelta(days=dias_offset)
                        
                        nova_parcela = ParcelaIndividual(
                            pagamento_parcelado_id=pag.id,
                            numero_parcela=num,
                            valor_parcela=pag.valor_parcela,
                            data_vencimento=data_vencimento,
                            status='Previsto',
                            data_pagamento=None,
                            forma_pagamento=None,
                            observacao=None
                        )
                        db.session.add(nova_parcela)
        
        if not preview_mode:
            db.session.commit()
        
        # Mensagem final
        if preview_mode:
            resultados["instrucao"] = "Para executar de verdade, acesse: /admin/recuperar-parcelas-pagas?preview=false&dias=" + str(dias_limite)
        
        return jsonify({
            "success": True,
            "message": f"{'Preview concluído' if preview_mode else 'Recuperação concluída'}! {resultados['parcelas_a_recuperar']} parcelas {'a recuperar' if preview_mode else 'recuperadas'}.",
            "resultados": resultados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] recuperar_parcelas_pagas: {str(e)}\n{error_details} ---")
        return jsonify({
            "success": False,
            "error": str(e),
            "details": error_details
        }), 500


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


# ==================== ENDPOINTS AGENDA DE DEMANDAS ====================

@app.route('/obras/<int:obra_id>/agenda', methods=['GET'])
@jwt_required()
def get_agenda_demandas(obra_id):
    """Lista todas as demandas da agenda de uma obra"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Atualizar status de atrasados automaticamente
        hoje = date.today()
        demandas_atrasadas = AgendaDemanda.query.filter(
            AgendaDemanda.obra_id == obra_id,
            AgendaDemanda.status == 'aguardando',
            AgendaDemanda.data_prevista < hoje
        ).all()
        
        for demanda in demandas_atrasadas:
            demanda.status = 'atrasado'
        
        if demandas_atrasadas:
            db.session.commit()
        
        # Buscar todas as demandas
        demandas = AgendaDemanda.query.filter_by(obra_id=obra_id).order_by(
            AgendaDemanda.data_prevista.asc()
        ).all()
        
        return jsonify([d.to_dict() for d in demandas]), 200
        
    except Exception as e:
        print(f"[ERRO] get_agenda_demandas: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/agenda', methods=['POST', 'OPTIONS'])
@jwt_required()
def criar_agenda_demanda(obra_id):
    """Cria uma nova demanda na agenda (manual ou importada)"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.json
        
        # Validação
        if not data.get('descricao'):
            return jsonify({"erro": "Descrição é obrigatória"}), 400
        if not data.get('data_prevista'):
            return jsonify({"erro": "Data é obrigatória"}), 400
        
        # Criar demanda
        demanda = AgendaDemanda(
            obra_id=obra_id,
            descricao=data.get('descricao'),
            tipo=data.get('tipo', 'material'),
            fornecedor=data.get('fornecedor'),
            telefone=data.get('telefone'),
            valor=float(data.get('valor')) if data.get('valor') else None,
            data_prevista=datetime.strptime(data.get('data_prevista'), '%Y-%m-%d').date(),
            status='aguardando',
            origem=data.get('origem', 'manual'),
            pagamento_servico_id=data.get('pagamento_servico_id'),
            orcamento_item_id=data.get('orcamento_item_id'),
            servico_id=data.get('servico_id'),
            observacoes=data.get('observacoes')
        )
        
        db.session.add(demanda)
        db.session.commit()
        
        print(f"[LOG] Demanda criada: {demanda.descricao} (origem: {demanda.origem})")
        
        return jsonify(demanda.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] criar_agenda_demanda: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/agenda/<int:demanda_id>', methods=['PUT', 'OPTIONS'])
@jwt_required()
def atualizar_agenda_demanda(obra_id, demanda_id):
    """Atualiza uma demanda da agenda"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        data = request.json
        
        # Atualizar campos
        if 'descricao' in data:
            demanda.descricao = data['descricao']
        if 'tipo' in data:
            demanda.tipo = data['tipo']
        if 'fornecedor' in data:
            demanda.fornecedor = data['fornecedor']
        if 'telefone' in data:
            demanda.telefone = data['telefone']
        if 'valor' in data:
            demanda.valor = float(data['valor']) if data['valor'] else None
        if 'data_prevista' in data:
            demanda.data_prevista = datetime.strptime(data['data_prevista'], '%Y-%m-%d').date()
        if 'observacoes' in data:
            demanda.observacoes = data['observacoes']
        
        db.session.commit()
        
        return jsonify(demanda.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] atualizar_agenda_demanda: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/agenda/<int:demanda_id>/concluir', methods=['PUT', 'OPTIONS'])
@jwt_required()
def concluir_agenda_demanda(obra_id, demanda_id):
    """Marca uma demanda como concluída"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        data = request.json or {}
        
        demanda.status = 'concluido'
        demanda.data_conclusao = datetime.strptime(data.get('data_conclusao'), '%Y-%m-%d').date() if data.get('data_conclusao') else date.today()
        
        if data.get('observacoes'):
            obs_atual = demanda.observacoes or ''
            demanda.observacoes = f"{obs_atual}\n[Conclusão] {data.get('observacoes')}".strip()
        
        db.session.commit()
        
        print(f"[LOG] Demanda concluída: {demanda.descricao}")
        
        return jsonify(demanda.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] concluir_agenda_demanda: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/agenda/<int:demanda_id>/reabrir', methods=['PUT', 'OPTIONS'])
@jwt_required()
def reabrir_agenda_demanda(obra_id, demanda_id):
    """Reabre uma demanda concluída"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        # Verificar se está atrasada
        hoje = date.today()
        if demanda.data_prevista < hoje:
            demanda.status = 'atrasado'
        else:
            demanda.status = 'aguardando'
        
        demanda.data_conclusao = None
        
        db.session.commit()
        
        return jsonify(demanda.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] reabrir_agenda_demanda: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/agenda/<int:demanda_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def excluir_agenda_demanda(obra_id, demanda_id):
    """Exclui uma demanda da agenda"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        descricao = demanda.descricao
        db.session.delete(demanda)
        db.session.commit()
        
        print(f"[LOG] Demanda excluída: {descricao}")
        
        return jsonify({"mensagem": "Demanda excluída com sucesso"}), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"[ERRO] excluir_agenda_demanda: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/agenda/importar/pagamentos', methods=['GET'])
@jwt_required()
def listar_pagamentos_para_importar(obra_id):
    """Lista pagamentos de material que podem ser importados para a agenda"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Buscar pagamentos de material já pagos
        pagamentos = PagamentoServico.query.join(Servico).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.tipo == 'material',
            PagamentoServico.status == 'Pago'
        ).order_by(PagamentoServico.data_pagamento.desc()).all()
        
        # IDs já importados
        ids_importados = set(
            d.pagamento_servico_id for d in AgendaDemanda.query.filter_by(obra_id=obra_id).all()
            if d.pagamento_servico_id
        )
        
        resultado = []
        for p in pagamentos:
            if p.id not in ids_importados:
                servico = Servico.query.get(p.servico_id)
                resultado.append({
                    'id': p.id,
                    'descricao': p.descricao or 'Material',
                    'servico': servico.nome if servico else None,
                    'fornecedor': p.fornecedor,
                    'valor': float(p.valor_pago) if p.valor_pago else float(p.valor) if p.valor else 0,
                    'data_pagamento': p.data_pagamento.isoformat() if p.data_pagamento else None,
                    'telefone': None
                })
        
        return jsonify(resultado), 200
        
    except Exception as e:
        print(f"[ERRO] listar_pagamentos_para_importar: {str(e)}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/agenda/importar/orcamento', methods=['GET'])
@jwt_required()
def listar_orcamento_para_importar(obra_id):
    """Lista itens do orçamento de engenharia que podem ser importados para a agenda"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Buscar itens do orçamento de engenharia
        itens = db.session.query(OrcamentoEngItem, OrcamentoEngEtapa).join(
            OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id
        ).filter(
            OrcamentoEngEtapa.obra_id == obra_id
        ).all()
        
        # IDs já importados
        ids_importados = set(
            d.orcamento_item_id for d in AgendaDemanda.query.filter_by(obra_id=obra_id).all()
            if d.orcamento_item_id
        )
        
        resultado = []
        for item, etapa in itens:
            if item.id not in ids_importados:
                # Calcular valor total
                if item.tipo_composicao == 'separado':
                    valor_total = item.quantidade * ((item.preco_mao_obra or 0) + (item.preco_material or 0))
                else:
                    valor_total = item.quantidade * (item.preco_unitario or 0)
                
                resultado.append({
                    'id': item.id,
                    'descricao': item.descricao,
                    'etapa': etapa.nome,
                    'quantidade': f"{item.quantidade} {item.unidade}",
                    'valor': float(valor_total),
                })
        
        return jsonify(resultado), 200
        
    except Exception as e:
        print(f"[ERRO] listar_orcamento_para_importar: {str(e)}")
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
# ROTAS DO CAIXA DE OBRA
# ==============================================================================

@app.route('/obras/<int:obra_id>/caixa', methods=['GET', 'POST'])
@jwt_required()
def gerenciar_caixa_obra(obra_id):
    """
    GET: Retorna informações do caixa da obra (dashboard)
    POST: Cria ou inicializa o caixa da obra
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        if request.method == 'GET':
            # Buscar ou criar caixa
            caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
            
            if not caixa:
                # Criar caixa automaticamente se não existir
                hoje = date.today()
                caixa = CaixaObra(
                    obra_id=obra_id,
                    saldo_inicial=0,
                    saldo_atual=0,
                    mes_atual=hoje.month,
                    ano_atual=hoje.year,
                    status='Ativo'
                )
                db.session.add(caixa)
                db.session.commit()
            
            # Calcular totais do mês atual
            movimentacoes_mes = MovimentacaoCaixa.query.filter(
                MovimentacaoCaixa.caixa_id == caixa.id,
                func.extract('month', MovimentacaoCaixa.data) == caixa.mes_atual,
                func.extract('year', MovimentacaoCaixa.data) == caixa.ano_atual
            ).all()
            
            total_entradas_mes = sum(m.valor for m in movimentacoes_mes if m.tipo == 'Entrada')
            total_saidas_mes = sum(m.valor for m in movimentacoes_mes if m.tipo == 'Saída')
            
            resultado = caixa.to_dict()
            resultado['total_entradas_mes'] = total_entradas_mes
            resultado['total_saidas_mes'] = total_saidas_mes
            resultado['obra_nome'] = obra.nome
            
            return jsonify(resultado), 200
        
        elif request.method == 'POST':
            # Criar/reinicializar caixa
            data = request.get_json()
            caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
            
            if caixa:
                return jsonify({"erro": "Caixa já existe para esta obra"}), 400
            
            hoje = date.today()
            caixa = CaixaObra(
                obra_id=obra_id,
                saldo_inicial=float(data.get('saldo_inicial', 0)),
                saldo_atual=float(data.get('saldo_inicial', 0)),
                mes_atual=hoje.month,
                ano_atual=hoje.year,
                status='Ativo'
            )
            
            db.session.add(caixa)
            db.session.commit()
            
            print(f"[LOG] ✅ Caixa criado para obra {obra_id}")
            return jsonify(caixa.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] gerenciar_caixa_obra: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/caixa/movimentacoes', methods=['GET', 'POST'])
@jwt_required()
def gerenciar_movimentacoes_caixa(obra_id):
    """
    GET: Lista movimentações do caixa (com filtros opcionais)
    POST: Adiciona nova movimentação
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Buscar caixa da obra
        caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
        if not caixa:
            return jsonify({"erro": "Caixa não encontrado para esta obra"}), 404
        
        if request.method == 'GET':
            # Parâmetros de filtro
            mes = request.args.get('mes', type=int)
            ano = request.args.get('ano', type=int)
            tipo = request.args.get('tipo')  # Entrada ou Saída
            
            query = MovimentacaoCaixa.query.filter_by(caixa_id=caixa.id)
            
            if mes:
                query = query.filter(func.extract('month', MovimentacaoCaixa.data) == mes)
            if ano:
                query = query.filter(func.extract('year', MovimentacaoCaixa.data) == ano)
            if tipo:
                query = query.filter_by(tipo=tipo)
            
            movimentacoes = query.order_by(MovimentacaoCaixa.data.desc()).all()
            
            return jsonify([m.to_dict() for m in movimentacoes]), 200
        
        elif request.method == 'POST':
            data = request.get_json()
            
            # Validações
            if 'tipo' not in data or data['tipo'] not in ['Entrada', 'Saída']:
                return jsonify({"erro": "Tipo deve ser 'Entrada' ou 'Saída'"}), 400
            
            if 'valor' not in data or float(data['valor']) <= 0:
                return jsonify({"erro": "Valor deve ser maior que zero"}), 400
            
            if 'descricao' not in data or not data['descricao'].strip():
                return jsonify({"erro": "Descrição é obrigatória"}), 400
            
            # Processar data (usar atual se não fornecida)
            data_movimentacao = datetime.now()
            if 'data' in data and data['data']:
                try:
                    data_movimentacao = datetime.fromisoformat(data['data'].replace('Z', '+00:00'))
                except:
                    pass
            
            # Criar movimentação
            movimentacao = MovimentacaoCaixa(
                caixa_id=caixa.id,
                data=data_movimentacao,
                tipo=data['tipo'],
                valor=float(data['valor']),
                descricao=data['descricao'].strip(),
                comprovante_url=data.get('comprovante_url'),
                observacoes=data.get('observacoes'),
                criado_por=current_user.id
            )
            
            db.session.add(movimentacao)
            
            # Atualizar saldo do caixa
            if data['tipo'] == 'Entrada':
                caixa.saldo_atual += float(data['valor'])
            else:  # Saída
                caixa.saldo_atual -= float(data['valor'])
            
            db.session.commit()
            
            print(f"[LOG] ✅ Movimentação {data['tipo']} de R$ {data['valor']} registrada no caixa {caixa.id}")
            return jsonify(movimentacao.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] gerenciar_movimentacoes_caixa: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/caixa/movimentacoes/<int:mov_id>', methods=['PUT', 'DELETE'])
@jwt_required()
def editar_deletar_movimentacao(obra_id, mov_id):
    """
    PUT: Edita uma movimentação existente
    DELETE: Deleta uma movimentação
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
        if not caixa:
            return jsonify({"erro": "Caixa não encontrado"}), 404
        
        movimentacao = db.session.get(MovimentacaoCaixa, mov_id)
        if not movimentacao or movimentacao.caixa_id != caixa.id:
            return jsonify({"erro": "Movimentação não encontrada"}), 404
        
        if request.method == 'PUT':
            data = request.get_json()
            
            # Reverter o impacto da movimentação antiga no saldo
            if movimentacao.tipo == 'Entrada':
                caixa.saldo_atual -= movimentacao.valor
            else:
                caixa.saldo_atual += movimentacao.valor
            
            # Atualizar campos
            if 'tipo' in data and data['tipo'] in ['Entrada', 'Saída']:
                movimentacao.tipo = data['tipo']
            
            if 'valor' in data and float(data['valor']) > 0:
                movimentacao.valor = float(data['valor'])
            
            if 'descricao' in data:
                movimentacao.descricao = data['descricao']
            
            if 'data' in data:
                try:
                    movimentacao.data = datetime.fromisoformat(data['data'].replace('Z', '+00:00'))
                except:
                    pass
            
            if 'comprovante_url' in data:
                movimentacao.comprovante_url = data['comprovante_url']
            
            if 'observacoes' in data:
                movimentacao.observacoes = data['observacoes']
            
            # Aplicar novo impacto no saldo
            if movimentacao.tipo == 'Entrada':
                caixa.saldo_atual += movimentacao.valor
            else:
                caixa.saldo_atual -= movimentacao.valor
            
            db.session.commit()
            
            print(f"[LOG] ✅ Movimentação {mov_id} atualizada")
            return jsonify(movimentacao.to_dict()), 200
        
        elif request.method == 'DELETE':
            # Reverter impacto no saldo
            if movimentacao.tipo == 'Entrada':
                caixa.saldo_atual -= movimentacao.valor
            else:
                caixa.saldo_atual += movimentacao.valor
            
            db.session.delete(movimentacao)
            db.session.commit()
            
            print(f"[LOG] ✅ Movimentação {mov_id} deletada")
            return jsonify({"mensagem": "Movimentação deletada com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"[ERRO] editar_deletar_movimentacao: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/caixa/upload-comprovante', methods=['POST'])
@jwt_required()
def upload_comprovante_caixa(obra_id):
    """Upload de imagem de comprovante (base64) - salva direto no banco"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json()
        
        if 'imagem' not in data:
            return jsonify({"erro": "Imagem não fornecida"}), 400
        
        # Pegar o base64 completo (com ou sem prefixo data:image)
        imagem_base64 = data['imagem']
        
        # Se não tiver o prefixo data:image, adicionar
        if not imagem_base64.startswith('data:image'):
            imagem_base64 = f"data:image/jpeg;base64,{imagem_base64}"
        
        # Retornar o base64 completo para ser salvo na movimentação
        print(f"[LOG] ✅ Comprovante base64 recebido para obra {obra_id} ({len(imagem_base64)} chars)")
        return jsonify({"comprovante_url": imagem_base64}), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"[ERRO] upload_comprovante_caixa: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/caixa/relatorio-pdf', methods=['POST'])
@jwt_required()
def gerar_relatorio_caixa_pdf(obra_id):
    """Gera relatorio PDF de prestacao de contas do caixa"""
    try:
        print(f"[LOG] Iniciando geracao de PDF do caixa para obra {obra_id}")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra nao encontrada"}), 404
        
        caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
        if not caixa:
            return jsonify({"erro": "Caixa nao encontrado"}), 404
        
        req_data = request.get_json() or {}
        mes = int(req_data.get('mes', date.today().month))
        ano = int(req_data.get('ano', date.today().year))
        
        print(f"[LOG] Buscando movimentacoes para mes={mes}, ano={ano}")
        
        # Buscar movimentacoes do periodo - data é DateTime
        todas_movs = MovimentacaoCaixa.query.filter_by(caixa_id=caixa.id).order_by(MovimentacaoCaixa.data).all()
        
        # Filtrar por mes/ano - data é DateTime então precisa acessar corretamente
        movimentacoes = []
        for m in todas_movs:
            if m.data:
                try:
                    mov_mes = m.data.month
                    mov_ano = m.data.year
                    if mov_mes == mes and mov_ano == ano:
                        movimentacoes.append(m)
                except Exception as e:
                    print(f"[WARN] Erro ao processar data da movimentacao {m.id}: {e}")
        
        print(f"[LOG] Encontradas {len(movimentacoes)} movimentacoes")
        
        # Calcular totais - verificar tipo com lowercase para evitar problemas
        saldo_inicial = float(caixa.saldo_inicial or 0)
        total_entradas = 0
        total_saidas = 0
        qtd_comprovantes = 0
        
        for m in movimentacoes:
            tipo = (m.tipo or '').lower()
            valor = float(m.valor or 0)
            if tipo == 'entrada':
                total_entradas += valor
            elif tipo in ['saida', 'saída']:
                total_saidas += valor
            if m.comprovante_url:
                qtd_comprovantes += 1
        
        saldo_final = saldo_inicial + total_entradas - total_saidas
        
        print(f"[LOG] Totais: entradas={total_entradas}, saidas={total_saidas}")
        
        # Funcoes auxiliares
        def formatar_real(valor):
            try:
                return f"R$ {float(valor):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
            except:
                return "R$ 0,00"
        
        def limpar_texto(texto):
            if not texto:
                return ""
            # Substituicoes manuais para evitar problemas
            subs = {
                'á': 'a', 'à': 'a', 'ã': 'a', 'â': 'a',
                'é': 'e', 'ê': 'e', 'è': 'e',
                'í': 'i', 'ì': 'i',
                'ó': 'o', 'ô': 'o', 'õ': 'o', 'ò': 'o',
                'ú': 'u', 'ù': 'u',
                'ç': 'c', 'ñ': 'n',
                'Á': 'A', 'À': 'A', 'Ã': 'A', 'Â': 'A',
                'É': 'E', 'Ê': 'E', 'È': 'E',
                'Í': 'I', 'Ì': 'I',
                'Ó': 'O', 'Ô': 'O', 'Õ': 'O', 'Ò': 'O',
                'Ú': 'U', 'Ù': 'U',
                'Ç': 'C', 'Ñ': 'N'
            }
            resultado = str(texto)
            for orig, subst in subs.items():
                resultado = resultado.replace(orig, subst)
            # Remove caracteres nao-ASCII
            return ''.join(c if ord(c) < 128 else '' for c in resultado)
        
        # Nome do mes
        nomes_meses = ['', 'Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho', 
                    'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        nome_mes = nomes_meses[mes] if 1 <= mes <= 12 else 'Mes'
        
        print(f"[LOG] Criando documento PDF...")
        
        # Criar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Limpar nomes
        obra_nome_limpo = limpar_texto(obra.nome)
        user_nome_limpo = limpar_texto(current_user.username if current_user else 'Sistema')
        
        # Titulo
        titulo = Paragraph("<b>PRESTACAO DE CONTAS - CAIXA DE OBRA</b>", styles['Title'])
        elements.append(titulo)
        elements.append(Spacer(1, 0.5*cm))
        
        # Informacoes
        info = f"<b>Obra:</b> {obra_nome_limpo}<br/>"
        info += f"<b>Periodo:</b> {nome_mes}/{ano}<br/>"
        info += f"<b>Responsavel:</b> {user_nome_limpo}<br/>"
        info += f"<b>Data do Relatorio:</b> {date.today().strftime('%d/%m/%Y')}"
        elements.append(Paragraph(info, styles['Normal']))
        elements.append(Spacer(1, 1*cm))
        
        # Saldo inicial
        data_saldo = [['SALDO INICIAL', formatar_real(saldo_inicial)]]
        table_saldo = Table(data_saldo, colWidths=[12*cm, 5*cm])
        table_saldo.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4CAF50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ]))
        elements.append(table_saldo)
        elements.append(Spacer(1, 0.5*cm))
        
        # Entradas - usar lowercase para comparacao
        entradas = [m for m in movimentacoes if (m.tipo or '').lower() == 'entrada']
        if entradas:
            elements.append(Paragraph("<b>ENTRADAS NO PERIODO</b>", styles['Heading2']))
            elements.append(Spacer(1, 0.3*cm))
            
            data_entradas = [['Data', 'Descricao', 'Valor']]
            for m in entradas:
                try:
                    data_str = m.data.strftime('%d/%m') if m.data else '-'
                except:
                    data_str = '-'
                data_entradas.append([
                    data_str,
                    limpar_texto(m.descricao or '')[:60],
                    formatar_real(m.valor)
                ])
            data_entradas.append(['', 'TOTAL ENTRADAS', formatar_real(total_entradas)])
            
            table_entradas = Table(data_entradas, colWidths=[2.5*cm, 11*cm, 3.5*cm])
            table_entradas.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2196F3')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#BBDEFB')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ]))
            elements.append(table_entradas)
            elements.append(Spacer(1, 0.7*cm))
        
        # Saidas - usar lowercase para comparacao
        saidas = [m for m in movimentacoes if (m.tipo or '').lower() in ['saida', 'saída']]
        if saidas:
            elements.append(Paragraph("<b>SAIDAS NO PERIODO</b>", styles['Heading2']))
            elements.append(Spacer(1, 0.3*cm))
            
            data_saidas = [['Data', 'Descricao', 'Valor', 'Comp.']]
            for m in saidas:
                try:
                    data_str = m.data.strftime('%d/%m') if m.data else '-'
                except:
                    data_str = '-'
                data_saidas.append([
                    data_str,
                    limpar_texto(m.descricao or '')[:60],
                    formatar_real(m.valor),
                    'Sim' if m.comprovante_url else '-'
                ])
            data_saidas.append(['', 'TOTAL SAIDAS', formatar_real(total_saidas), ''])
            
            table_saidas = Table(data_saidas, colWidths=[2.5*cm, 10*cm, 3.5*cm, 1*cm])
            table_saidas.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f44336')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('ALIGN', (3, 0), (3, -1), 'CENTER'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#FFCDD2')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ]))
            elements.append(table_saidas)
            elements.append(Spacer(1, 0.7*cm))
        
        # Saldo final
        data_final = [['SALDO FINAL', formatar_real(saldo_final)]]
        table_final = Table(data_final, colWidths=[12*cm, 5*cm])
        table_final.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#FF9800')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ]))
        elements.append(table_final)
        elements.append(Spacer(1, 1*cm))
        
        # Rodape
        rodape = f"Total de comprovantes anexos: {qtd_comprovantes}<br/>"
        rodape += f"Gerado em: {datetime.now().strftime('%d/%m/%Y as %H:%M')}<br/>"
        rodape += f"Por: {user_nome_limpo}"
        elements.append(Paragraph(rodape, styles['Normal']))
        
        # === SEÇÃO DE COMPROVANTES ===
        if qtd_comprovantes > 0:
            elements.append(Spacer(1, 1*cm))
            elements.append(Paragraph("<b>COMPROVANTES ANEXOS</b>", styles['Heading2']))
            elements.append(Spacer(1, 0.5*cm))
            
            # Adicionar cada comprovante
            comprovante_num = 0
            for m in movimentacoes:
                if m.comprovante_url:
                    comprovante_num += 1
                    try:
                        # Formatar data
                        try:
                            data_str = m.data.strftime('%d/%m/%Y') if m.data else '-'
                        except:
                            data_str = '-'
                        
                        # Título do comprovante
                        desc_limpa = limpar_texto(m.descricao or 'Sem descricao')[:50]
                        titulo_comp = f"<b>Comprovante {comprovante_num}:</b> {desc_limpa} - {data_str} - {formatar_real(m.valor)}"
                        elements.append(Paragraph(titulo_comp, styles['Normal']))
                        elements.append(Spacer(1, 0.3*cm))
                        
                        # Tentar carregar a imagem
                        img_data = None
                        
                        # Se for base64
                        if m.comprovante_url.startswith('data:image'):
                            try:
                                # Extrair dados base64
                                base64_data = m.comprovante_url.split(',')[1]
                                img_data = io.BytesIO(base64.b64decode(base64_data))
                            except Exception as e:
                                print(f"[WARN] Erro ao decodificar base64 do comprovante {comprovante_num}: {e}")
                        
                        # Se for caminho de arquivo local
                        elif m.comprovante_url.startswith('/uploads/') or m.comprovante_url.startswith('uploads/'):
                            try:
                                # Tentar carregar do sistema de arquivos
                                file_path = m.comprovante_url.lstrip('/')
                                if os.path.exists(file_path):
                                    with open(file_path, 'rb') as f:
                                        img_data = io.BytesIO(f.read())
                            except Exception as e:
                                print(f"[WARN] Erro ao carregar arquivo do comprovante {comprovante_num}: {e}")
                        
                        # Se for URL HTTP
                        elif m.comprovante_url.startswith('http'):
                            try:
                                import urllib.request
                                with urllib.request.urlopen(m.comprovante_url, timeout=10) as response:
                                    img_data = io.BytesIO(response.read())
                            except Exception as e:
                                print(f"[WARN] Erro ao baixar comprovante {comprovante_num}: {e}")
                        
                        # Adicionar imagem ao PDF se conseguiu carregar
                        if img_data:
                            try:
                                img = Image(img_data)
                                # Redimensionar para caber na página (max 15cm de largura, 10cm de altura)
                                img_width = img.drawWidth
                                img_height = img.drawHeight
                                max_width = 15 * cm
                                max_height = 10 * cm
                                
                                # Calcular proporção
                                ratio = min(max_width / img_width, max_height / img_height)
                                if ratio < 1:
                                    img.drawWidth = img_width * ratio
                                    img.drawHeight = img_height * ratio
                                
                                elements.append(img)
                                elements.append(Spacer(1, 0.5*cm))
                            except Exception as e:
                                print(f"[WARN] Erro ao adicionar imagem do comprovante {comprovante_num}: {e}")
                                elements.append(Paragraph(f"<i>(Erro ao carregar imagem)</i>", styles['Normal']))
                        else:
                            elements.append(Paragraph(f"<i>(Comprovante disponivel em: {m.comprovante_url[:60]}...)</i>", styles['Normal']))
                        
                        elements.append(Spacer(1, 0.5*cm))
                        
                    except Exception as e:
                        print(f"[WARN] Erro ao processar comprovante {comprovante_num}: {e}")
                        elements.append(Paragraph(f"<i>(Erro ao processar comprovante)</i>", styles['Normal']))
        
        # Construir PDF
        print(f"[LOG] Construindo PDF...")
        doc.build(elements)
        buffer.seek(0)
        
        print(f"[LOG] PDF do caixa gerado com sucesso")
        
        nome_arquivo = f"Caixa_{obra_nome_limpo.replace(' ', '_')}_{nome_mes}_{ano}.pdf"
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=nome_arquivo,
            mimetype='application/pdf'
        )
    
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"[ERRO] gerar_relatorio_caixa_pdf: {str(e)}\n{error_details}")
        return jsonify({
            "erro": "Erro ao gerar relatorio PDF",
            "mensagem": str(e),
            "detalhes": error_details
        }), 500


# ==============================================================================
# ROTAS DE GESTÃO DE BOLETOS
# ==============================================================================
# NOTA: A função extrair_dados_boleto_pdf está definida no início do arquivo (linha ~485)
# e suporta extração de múltiplos boletos de PDFs com várias páginas


@app.route('/obras/<int:obra_id>/boletos', methods=['GET'])
@jwt_required()
def listar_boletos(obra_id):
    """Lista todos os boletos de uma obra"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Parâmetros de filtro
        status_filtro = request.args.get('status', None)  # Pendente, Pago, Vencido
        dias = request.args.get('dias', None)  # Filtrar por dias até vencimento
        
        query = Boleto.query.filter_by(obra_id=obra_id)
        
        if status_filtro:
            query = query.filter_by(status=status_filtro)
        
        if dias:
            dias_int = int(dias)
            data_limite = date.today() + timedelta(days=dias_int)
            query = query.filter(Boleto.data_vencimento <= data_limite)
        
        boletos = query.order_by(Boleto.data_vencimento.asc()).all()
        
        # Atualizar status de vencidos
        hoje = date.today()
        for boleto in boletos:
            if boleto.status == 'Pendente' and boleto.data_vencimento < hoje:
                boleto.status = 'Vencido'
        db.session.commit()
        
        return jsonify([b.to_dict() for b in boletos]), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] listar_boletos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/boletos', methods=['POST'])
@jwt_required()
def criar_boleto(obra_id):
    """Cria um novo boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json()
        
        # Validar campos obrigatórios
        if not data.get('descricao'):
            return jsonify({"erro": "Descrição é obrigatória"}), 400
        if not data.get('valor'):
            return jsonify({"erro": "Valor é obrigatório"}), 400
        if not data.get('data_vencimento'):
            return jsonify({"erro": "Data de vencimento é obrigatória"}), 400
        
        # Verificar duplicidade por código de barras
        codigo_barras = data.get('codigo_barras')
        if codigo_barras:
            boleto_existente = Boleto.query.filter_by(
                obra_id=obra_id, 
                codigo_barras=codigo_barras
            ).first()
            if boleto_existente:
                print(f"--- [LOG] Boleto duplicado ignorado: código {codigo_barras[:20]}... já existe ---")
                return jsonify({"erro": "Boleto com este código de barras já existe", "duplicado": True}), 409
        
        novo_boleto = Boleto(
            obra_id=obra_id,
            usuario_id=user.id,
            codigo_barras=codigo_barras,
            descricao=data.get('descricao'),
            beneficiario=data.get('beneficiario'),
            valor=float(data.get('valor')),
            data_vencimento=datetime.strptime(data.get('data_vencimento'), '%Y-%m-%d').date(),
            status='Pendente',
            vinculado_servico_id=data.get('vinculado_servico_id'),  # Vincular a serviço
            arquivo_nome=data.get('arquivo_nome'),
            arquivo_pdf=data.get('arquivo_pdf') or data.get('arquivo_base64')
        )
        
        db.session.add(novo_boleto)
        db.session.commit()
        
        print(f"--- [LOG] Boleto criado: ID {novo_boleto.id} na obra {obra_id} ---")
        return jsonify(novo_boleto.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] criar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/boletos/extrair-pdf', methods=['POST'])
@jwt_required()
def extrair_pdf_boleto(obra_id):
    """Extrai dados de um PDF de boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json()
        pdf_base64 = data.get('arquivo_base64')
        
        if not pdf_base64:
            return jsonify({"erro": "Arquivo PDF não enviado"}), 400
        
        # Remover prefixo data:application/pdf;base64, se existir
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        resultado = extrair_dados_boleto_pdf(pdf_base64)
        
        return jsonify(resultado), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] extrair_pdf_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/boletos/<int:boleto_id>', methods=['PUT'])
@jwt_required()
def editar_boleto(obra_id, boleto_id):
    """Edita um boleto existente"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        data = request.get_json()
        
        if 'descricao' in data:
            boleto.descricao = data['descricao']
        if 'beneficiario' in data:
            boleto.beneficiario = data['beneficiario']
        if 'codigo_barras' in data:
            boleto.codigo_barras = data['codigo_barras']
        if 'valor' in data:
            boleto.valor = float(data['valor'])
        if 'data_vencimento' in data:
            boleto.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        if 'status' in data:
            boleto.status = data['status']
        if 'vinculado_servico_id' in data:
            boleto.vinculado_servico_id = data['vinculado_servico_id']
        
        db.session.commit()
        
        print(f"--- [LOG] Boleto {boleto_id} editado ---")
        return jsonify(boleto.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] editar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/boletos/<int:boleto_id>/pagar', methods=['POST'])
@jwt_required()
def pagar_boleto(obra_id, boleto_id):
    """Marca um boleto como pago"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        data = request.get_json() or {}
        
        boleto.status = 'Pago'
        boleto.data_pagamento = datetime.strptime(data.get('data_pagamento', date.today().isoformat()), '%Y-%m-%d').date()
        
        db.session.commit()
        
        print(f"--- [LOG] Boleto {boleto_id} marcado como pago ---")
        return jsonify(boleto.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] pagar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/boletos/<int:boleto_id>', methods=['DELETE'])
@jwt_required()
def deletar_boleto(obra_id, boleto_id):
    """Deleta um boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        db.session.delete(boleto)
        db.session.commit()
        
        print(f"--- [LOG] Boleto {boleto_id} deletado ---")
        return jsonify({"sucesso": True}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] deletar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/boletos/<int:boleto_id>/arquivo', methods=['GET'])
@jwt_required()
def obter_arquivo_boleto(obra_id, boleto_id):
    """Retorna o arquivo PDF do boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        if not boleto.arquivo_pdf:
            return jsonify({"erro": "Boleto não possui arquivo anexado"}), 404
        
        return jsonify({
            "arquivo_nome": boleto.arquivo_nome,
            "arquivo_base64": boleto.arquivo_pdf
        }), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] obter_arquivo_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/boletos/verificar-alertas', methods=['POST'])
@jwt_required()
def verificar_alertas_boletos():
    """Verifica boletos próximos do vencimento e cria notificações"""
    try:
        user = get_current_user()
        hoje = date.today()
        
        # Buscar boletos pendentes de todas as obras que o usuário tem acesso
        if user.role == 'master':
            boletos = Boleto.query.filter_by(status='Pendente').all()
        else:
            # Buscar obras que o usuário tem acesso
            obras_ids = [obra.id for obra in user.obras_permitidas]
            boletos = Boleto.query.filter(
                Boleto.obra_id.in_(obras_ids),
                Boleto.status == 'Pendente'
            ).all()
        
        alertas_criados = 0
        
        for boleto in boletos:
            dias_para_vencer = (boleto.data_vencimento - hoje).days
            obra = Obra.query.get(boleto.obra_id)
            obra_nome = obra.nome if obra else f"Obra {boleto.obra_id}"
            
            # Alerta 7 dias
            if dias_para_vencer <= 7 and dias_para_vencer > 3 and not boleto.alerta_7dias:
                criar_notificacao(
                    usuario_destino_id=boleto.usuario_id or user.id,
                    tipo='boleto_vencendo',
                    titulo='Boleto vence em 7 dias',
                    mensagem=f'O boleto "{boleto.descricao}" de {formatar_real(boleto.valor)} vence em {dias_para_vencer} dias ({boleto.data_vencimento.strftime("%d/%m/%Y")})',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
                boleto.alerta_7dias = True
                alertas_criados += 1
            
            # Alerta 3 dias
            elif dias_para_vencer <= 3 and dias_para_vencer > 0 and not boleto.alerta_3dias:
                criar_notificacao(
                    usuario_destino_id=boleto.usuario_id or user.id,
                    tipo='boleto_vencendo',
                    titulo='⚠️ Boleto vence em 3 dias',
                    mensagem=f'URGENTE: O boleto "{boleto.descricao}" de {formatar_real(boleto.valor)} vence em {dias_para_vencer} dias!',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
                boleto.alerta_3dias = True
                alertas_criados += 1
            
            # Alerta hoje
            elif dias_para_vencer == 0 and not boleto.alerta_hoje:
                criar_notificacao(
                    usuario_destino_id=boleto.usuario_id or user.id,
                    tipo='boleto_vencendo',
                    titulo='🚨 Boleto vence HOJE',
                    mensagem=f'ATENÇÃO: O boleto "{boleto.descricao}" de {formatar_real(boleto.valor)} vence HOJE!',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
                boleto.alerta_hoje = True
                alertas_criados += 1
            
            # Marcar como vencido
            elif dias_para_vencer < 0:
                boleto.status = 'Vencido'
        
        db.session.commit()
        
        return jsonify({
            "sucesso": True,
            "alertas_criados": alertas_criados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] verificar_alertas_boletos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/boletos/resumo', methods=['GET'])
@jwt_required()
def resumo_boletos(obra_id):
    """Retorna resumo dos boletos para relatório financeiro"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boletos = Boleto.query.filter_by(obra_id=obra_id).all()
        
        hoje = date.today()
        
        # Calcular totais
        total_pendente = sum(b.valor for b in boletos if b.status == 'Pendente')
        total_vencido = sum(b.valor for b in boletos if b.status == 'Vencido' or (b.status == 'Pendente' and b.data_vencimento < hoje))
        total_pago = sum(b.valor for b in boletos if b.status == 'Pago')
        
        # Boletos vencendo em 7 dias
        vencendo_7_dias = [b.to_dict() for b in boletos if b.status == 'Pendente' and 0 <= (b.data_vencimento - hoje).days <= 7]
        
        return jsonify({
            "total_pendente": total_pendente,
            "total_vencido": total_vencido,
            "total_pago": total_pago,
            "quantidade_pendente": len([b for b in boletos if b.status == 'Pendente']),
            "quantidade_vencido": len([b for b in boletos if b.status == 'Vencido']),
            "quantidade_pago": len([b for b in boletos if b.status == 'Pago']),
            "vencendo_7_dias": vencendo_7_dias
        }), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] resumo_boletos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


# ==============================================================================
# ROTA DE DEBUG - VERIFICAR DADOS DE PARCELAS E LANÇAMENTOS
# ==============================================================================
@app.route('/admin/debug-kpi/<int:obra_id>', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def debug_kpi(obra_id):
    """Rota de debug para verificar cálculos de KPI"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OK"}), 200)
    try:
        resultado = {
            "obra_id": obra_id,
            "parcelas_individuais": [],
            "pagamentos_parcelados": [],
            "lancamentos": [],
            "calculos": {}
        }
        
        # 1. Buscar todos os pagamentos parcelados
        pag_parcelados = PagamentoParcelado.query.filter_by(obra_id=obra_id).all()
        for pp in pag_parcelados:
            resultado["pagamentos_parcelados"].append({
                "id": pp.id,
                "descricao": pp.descricao,
                "servico_id": pp.servico_id,
                "valor_total": pp.valor_total,
                "numero_parcelas": pp.numero_parcelas,
                "parcelas_pagas": pp.parcelas_pagas,
                "status": pp.status
            })
        
        # 2. Buscar todas as parcelas individuais
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id
        ).all()
        for p in parcelas:
            resultado["parcelas_individuais"].append({
                "id": p.id,
                "pagamento_parcelado_id": p.pagamento_parcelado_id,
                "numero_parcela": p.numero_parcela,
                "valor_parcela": p.valor_parcela,
                "status": p.status,
                "data_pagamento": p.data_pagamento.isoformat() if p.data_pagamento else None
            })
        
        # 3. Buscar lançamentos
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).all()
        for l in lancamentos:
            resultado["lancamentos"].append({
                "id": l.id,
                "descricao": l.descricao,
                "valor_total": l.valor_total,
                "valor_pago": l.valor_pago,
                "status": l.status,
                "servico_id": l.servico_id
            })
        
        # 4. Calcular valores
        total_parcelas_pagas_sem_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela)
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Pago',
            PagamentoParcelado.servico_id.is_(None)
        ).scalar() or 0
        
        total_parcelas_previstas_sem_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela)
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto',
            PagamentoParcelado.servico_id.is_(None)
        ).scalar() or 0
        
        total_lancamentos_pagos = db.session.query(
            func.sum(Lancamento.valor_pago)
        ).filter(Lancamento.obra_id == obra_id).scalar() or 0
        
        resultado["calculos"] = {
            "total_parcelas_pagas_sem_servico": total_parcelas_pagas_sem_servico,
            "total_parcelas_previstas_sem_servico": total_parcelas_previstas_sem_servico,
            "total_lancamentos_pagos": total_lancamentos_pagos,
            "qtd_parcelas_pagas": len([p for p in parcelas if p.status == 'Pago']),
            "qtd_parcelas_previstas": len([p for p in parcelas if p.status == 'Previsto'])
        }
        
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"erro": str(e), "trace": traceback.format_exc()}), 500


# ==============================================================================
# ROTA DE LIMPEZA - REMOVER LANÇAMENTOS DUPLICADOS DE PARCELAS
# ==============================================================================
@app.route('/admin/limpar-lancamentos-duplicados', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def limpar_lancamentos_duplicados():
    """
    Remove lançamentos duplicados criados por parcelas pagas.
    
    Quando uma parcela SEM serviço é paga, o sistema cria um Lancamento.
    Porém, versões anteriores também adicionavam a ParcelaIndividual ao histórico,
    causando duplicação.
    
    Este script identifica e remove os Lancamentos duplicados.
    
    Parâmetros:
    - preview=true (default): Apenas mostra o que seria deletado
    - preview=false: Executa a deleção
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OK"}), 200)
    
    try:
        import re
        preview = request.args.get('preview', 'true').lower() == 'true'
        
        resultado = {
            "modo": "PREVIEW" if preview else "EXECUÇÃO",
            "lancamentos_duplicados": [],
            "total_encontrados": 0,
            "total_deletados": 0,
            "valor_total_duplicado": 0,
            "obras_afetadas": set()
        }
        
        # Padrão: "Descrição (Parcela X/Y)" 
        padrao_parcela = re.compile(r'^(.+)\s*\(Parcela\s*(\d+)/(\d+)\)$')
        
        # Buscar todos os lançamentos que parecem ser de parcelas
        lancamentos = Lancamento.query.filter(
            Lancamento.descricao.like('%(Parcela %')
        ).all()
        
        print(f"--- [LIMPEZA] Encontrados {len(lancamentos)} lançamentos com padrão de parcela ---")
        
        lancamentos_para_deletar = []
        
        for lanc in lancamentos:
            match = padrao_parcela.match(lanc.descricao)
            if not match:
                continue
            
            descricao_base = match.group(1).strip()
            numero_parcela = int(match.group(2))
            total_parcelas = int(match.group(3))
            
            # Buscar PagamentoParcelado correspondente
            pag_parcelado = PagamentoParcelado.query.filter(
                PagamentoParcelado.obra_id == lanc.obra_id,
                PagamentoParcelado.descricao == descricao_base,
                PagamentoParcelado.numero_parcelas == total_parcelas
            ).first()
            
            if not pag_parcelado:
                continue
            
            # Verificar se existe ParcelaIndividual paga correspondente
            parcela = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                ParcelaIndividual.numero_parcela == numero_parcela,
                ParcelaIndividual.status == 'Pago'
            ).first()
            
            if parcela:
                # Encontrou duplicação! O lançamento foi criado pela parcela
                # mas a parcela ainda existe como Pago
                lancamentos_para_deletar.append(lanc)
                resultado["obras_afetadas"].add(lanc.obra_id)
                resultado["lancamentos_duplicados"].append({
                    "lancamento_id": lanc.id,
                    "obra_id": lanc.obra_id,
                    "descricao": lanc.descricao,
                    "valor": lanc.valor_pago,
                    "data": lanc.data.isoformat() if lanc.data else None,
                    "parcela_id": parcela.id,
                    "pagamento_parcelado_id": pag_parcelado.id
                })
                resultado["valor_total_duplicado"] += lanc.valor_pago or 0
        
        resultado["total_encontrados"] = len(lancamentos_para_deletar)
        resultado["obras_afetadas"] = list(resultado["obras_afetadas"])
        
        if not preview and lancamentos_para_deletar:
            for lanc in lancamentos_para_deletar:
                db.session.delete(lanc)
            db.session.commit()
            resultado["total_deletados"] = len(lancamentos_para_deletar)
            print(f"--- [LIMPEZA] ✅ {len(lancamentos_para_deletar)} lançamentos duplicados removidos ---")
        
        return jsonify(resultado)
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e), "trace": traceback.format_exc()}), 500


# ==============================================================================
# ENDPOINTS DE BI (BUSINESS INTELLIGENCE)
# ==============================================================================

@app.route('/bi/vencimentos', methods=['GET'])
@jwt_required()
def bi_vencimentos():
    """
    Retorna todos os vencimentos (parcelas e pagamentos futuros) para o calendário do BI
    """
    try:
        user = get_current_user()
        
        # Filtrar obras do usuário
        if user.role == 'master':
            obras_ids = [o.id for o in Obra.query.all()]
        else:
            obras_ids = [o.id for o in user.obras]
        
        hoje = date.today()
        
        # Buscar todas as parcelas individuais pendentes
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id.in_(obras_ids),
            ParcelaIndividual.status.in_(['Previsto', 'Pendente'])
        ).all()
        
        # Organizar por data
        vencimentos = []
        for p in parcelas:
            pag = p.pagamento_parcelado
            obra = Obra.query.get(pag.obra_id) if pag else None
            
            vencimentos.append({
                'id': p.id,
                'tipo': 'parcela',
                'data': p.data_vencimento.isoformat() if p.data_vencimento else None,
                'valor': p.valor_parcela,
                'descricao': f"{pag.descricao} ({p.numero_parcela}/{pag.numero_parcelas})" if pag else f"Parcela {p.numero_parcela}",
                'obra_id': pag.obra_id if pag else None,
                'obra_nome': obra.nome if obra else 'N/A',
                'fornecedor': pag.fornecedor if pag else None,
                'status': 'vencido' if p.data_vencimento and p.data_vencimento < hoje else ('hoje' if p.data_vencimento == hoje else 'futuro'),
                'is_entrada': p.numero_parcela == 0
            })
        
        # Buscar lançamentos a pagar (pagamentos futuros únicos)
        lancamentos_futuros = Lancamento.query.filter(
            Lancamento.obra_id.in_(obras_ids),
            Lancamento.status == 'A Pagar',
            Lancamento.data_vencimento != None
        ).all()
        
        for l in lancamentos_futuros:
            obra = Obra.query.get(l.obra_id)
            vencimentos.append({
                'id': l.id,
                'tipo': 'lancamento',
                'data': l.data_vencimento.isoformat() if l.data_vencimento else None,
                'valor': l.valor_total or 0,
                'descricao': l.descricao,
                'obra_id': l.obra_id,
                'obra_nome': obra.nome if obra else 'N/A',
                'fornecedor': l.fornecedor,
                'status': 'vencido' if l.data_vencimento and l.data_vencimento < hoje else ('hoje' if l.data_vencimento == hoje else 'futuro'),
                'is_entrada': False
            })
        
        # Ordenar por data
        vencimentos.sort(key=lambda x: x['data'] or '9999-99-99')
        
        # Calcular resumos
        vencidos = [v for v in vencimentos if v['status'] == 'vencido']
        hoje_list = [v for v in vencimentos if v['status'] == 'hoje']
        semana = [v for v in vencimentos if v['data'] and hoje <= date.fromisoformat(v['data']) <= hoje + timedelta(days=7)]
        mes = [v for v in vencimentos if v['data'] and hoje <= date.fromisoformat(v['data']) <= hoje + timedelta(days=30)]
        
        return jsonify({
            'vencimentos': vencimentos,
            'resumo': {
                'total': len(vencimentos),
                'vencidos': len(vencidos),
                'valor_vencido': sum(v['valor'] for v in vencidos),
                'hoje': len(hoje_list),
                'valor_hoje': sum(v['valor'] for v in hoje_list),
                'semana': len(semana),
                'valor_semana': sum(v['valor'] for v in semana),
                'mes': len(mes),
                'valor_mes': sum(v['valor'] for v in mes)
            }
        })
        
    except Exception as e:
        print(f"[BI] Erro ao buscar vencimentos: {e}")
        return jsonify({"erro": str(e)}), 500


@app.route('/bi/historico-mensal', methods=['GET'])
@jwt_required()
def bi_historico_mensal():
    """
    Retorna histórico de pagamentos agrupado por mês para análise temporal
    """
    try:
        user = get_current_user()
        
        # Filtrar obras do usuário
        if user.role == 'master':
            obras_ids = [o.id for o in Obra.query.all()]
        else:
            obras_ids = [o.id for o in user.obras]
        
        print(f"[BI HISTORICO] Buscando para {len(obras_ids)} obras")
        
        # Buscar todos os lançamentos pagos
        lancamentos = Lancamento.query.filter(
            Lancamento.obra_id.in_(obras_ids),
            Lancamento.status == 'Pago'
        ).all()
        print(f"[BI HISTORICO] Lançamentos pagos: {len(lancamentos)}")
        
        # Buscar parcelas pagas
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id.in_(obras_ids),
            ParcelaIndividual.status == 'Pago'
        ).all()
        print(f"[BI HISTORICO] Parcelas pagas: {len(parcelas)}")
        
        # Buscar pagamentos de serviço
        pagamentos_servico = PagamentoServico.query.join(Servico).filter(
            Servico.obra_id.in_(obras_ids)
        ).all()
        print(f"[BI HISTORICO] Pagamentos de serviço: {len(pagamentos_servico)}")
        
        # Agrupar por mês
        meses = {}
        
        for l in lancamentos:
            # Usar data ou data_vencimento
            data_ref = l.data or l.data_vencimento
            if data_ref:
                mes_key = data_ref.strftime('%Y-%m')
                if mes_key not in meses:
                    meses[mes_key] = {'mes': mes_key, 'total': 0, 'qtd': 0, 'mao_obra': 0, 'material': 0}
                valor = l.valor_pago or l.valor_total or 0
                meses[mes_key]['total'] += valor
                meses[mes_key]['qtd'] += 1
                if l.tipo == 'Mão de Obra':
                    meses[mes_key]['mao_obra'] += valor
                else:
                    meses[mes_key]['material'] += valor
        
        for p in parcelas:
            data_ref = p.data_pagamento or p.data_vencimento
            if data_ref:
                mes_key = data_ref.strftime('%Y-%m')
                if mes_key not in meses:
                    meses[mes_key] = {'mes': mes_key, 'total': 0, 'qtd': 0, 'mao_obra': 0, 'material': 0}
                meses[mes_key]['total'] += p.valor_parcela or 0
                meses[mes_key]['qtd'] += 1
        
        for ps in pagamentos_servico:
            if ps.data_pagamento:
                mes_key = ps.data_pagamento.strftime('%Y-%m')
                if mes_key not in meses:
                    meses[mes_key] = {'mes': mes_key, 'total': 0, 'qtd': 0, 'mao_obra': 0, 'material': 0}
                meses[mes_key]['total'] += ps.valor_pago or 0
                meses[mes_key]['qtd'] += 1
                if ps.tipo_pagamento == 'mao_de_obra':
                    meses[mes_key]['mao_obra'] += ps.valor_pago or 0
                else:
                    meses[mes_key]['material'] += ps.valor_pago or 0
        
        print(f"[BI HISTORICO] Total de meses encontrados: {len(meses)}")
        
        # Converter para lista ordenada
        historico = sorted(meses.values(), key=lambda x: x['mes'])
        
        # Adicionar nome do mês formatado
        meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        for h in historico:
            ano, mes = h['mes'].split('-')
            h['mes_nome'] = f"{meses_nomes[int(mes)-1]}/{ano[2:]}"
            h['ano'] = int(ano)
            h['mes_num'] = int(mes)
        
        # Calcular totais e médias
        total_geral = sum(h['total'] for h in historico)
        media_mensal = total_geral / len(historico) if historico else 0
        
        # Identificar melhor e pior mês
        melhor_mes = max(historico, key=lambda x: x['total']) if historico else None
        pior_mes = min(historico, key=lambda x: x['total']) if historico else None
        
        return jsonify({
            'historico': historico,
            'resumo': {
                'total_geral': total_geral,
                'media_mensal': media_mensal,
                'melhor_mes': melhor_mes,
                'pior_mes': pior_mes,
                'total_meses': len(historico)
            }
        })
        
    except Exception as e:
        print(f"[BI] Erro ao buscar histórico mensal: {e}")
        return jsonify({"erro": str(e)}), 500


@app.route('/bi/projecao', methods=['GET'])
@jwt_required()
def bi_projecao():
    """
    Retorna projeção de gastos futuros baseado em parcelas e vencimentos
    """
    try:
        user = get_current_user()
        
        # Filtrar obras do usuário
        if user.role == 'master':
            obras_ids = [o.id for o in Obra.query.all()]
        else:
            obras_ids = [o.id for o in user.obras]
        
        hoje = date.today()
        
        # Buscar parcelas futuras
        parcelas_futuras = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id.in_(obras_ids),
            ParcelaIndividual.status.in_(['Previsto', 'Pendente']),
            ParcelaIndividual.data_vencimento >= hoje
        ).all()
        
        # Agrupar por mês
        projecao = {}
        for p in parcelas_futuras:
            if p.data_vencimento:
                mes_key = p.data_vencimento.strftime('%Y-%m')
                if mes_key not in projecao:
                    projecao[mes_key] = {'mes': mes_key, 'valor': 0, 'qtd': 0}
                projecao[mes_key]['valor'] += p.valor_parcela or 0
                projecao[mes_key]['qtd'] += 1
        
        # Converter para lista ordenada
        projecao_lista = sorted(projecao.values(), key=lambda x: x['mes'])
        
        # Adicionar nome do mês
        meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        for p in projecao_lista:
            ano, mes = p['mes'].split('-')
            p['mes_nome'] = f"{meses_nomes[int(mes)-1]}/{ano[2:]}"
        
        return jsonify({
            'projecao': projecao_lista,
            'total_projetado': sum(p['valor'] for p in projecao_lista),
            'total_parcelas': sum(p['qtd'] for p in projecao_lista)
        })
        
    except Exception as e:
        print(f"[BI] Erro ao buscar projeção: {e}")
        return jsonify({"erro": str(e)}), 500


# ==============================================================================
# ENDPOINTS DE ORÇAMENTO DE ENGENHARIA
# ==============================================================================

@app.route('/servicos-base', methods=['GET'])
@jwt_required()
def listar_servicos_base():
    """
    Lista serviços da base de referência com autocomplete
    Query params: q (busca), categoria
    """
    try:
        q = request.args.get('q', '').strip().lower()
        categoria = request.args.get('categoria', '')
        
        query = ServicoBase.query
        
        if q:
            query = query.filter(ServicoBase.descricao.ilike(f'%{q}%'))
        
        if categoria:
            query = query.filter(ServicoBase.categoria == categoria)
        
        servicos = query.order_by(ServicoBase.categoria, ServicoBase.descricao).limit(50).all()
        
        # Agrupar por categoria
        categorias = {}
        for s in servicos:
            if s.categoria not in categorias:
                categorias[s.categoria] = []
            categorias[s.categoria].append(s.to_dict())
        
        return jsonify({
            'servicos': [s.to_dict() for s in servicos],
            'por_categoria': categorias,
            'total': len(servicos)
        })
        
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route('/servicos-usuario', methods=['GET'])
@jwt_required()
def listar_servicos_usuario():
    """
    Lista serviços personalizados do usuário com autocomplete
    Query params: q (busca)
    """
    try:
        user = get_current_user()
        q = request.args.get('q', '').strip().lower()
        
        query = ServicoUsuario.query.filter(
            ServicoUsuario.user_id == user.id
        )
        
        if q:
            query = query.filter(ServicoUsuario.descricao.ilike(f'%{q}%'))
        
        # Ordenar por mais usados primeiro
        servicos = query.order_by(
            ServicoUsuario.vezes_usado.desc(),
            ServicoUsuario.ultima_utilizacao.desc()
        ).limit(30).all()
        
        return jsonify({
            'servicos': [s.to_dict() for s in servicos],
            'total': len(servicos)
        })
        
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route('/servicos-usuario', methods=['POST'])
@jwt_required()
def criar_servico_usuario():
    """
    Salva um novo serviço na biblioteca do usuário
    """
    try:
        user = get_current_user()
        dados = request.json
        
        servico = ServicoUsuario(
            user_id=user.id,
            categoria=dados.get('categoria'),
            descricao=dados['descricao'],
            unidade=dados['unidade'],
            tipo_composicao=dados.get('tipo_composicao', 'separado'),
            preco_mao_obra=dados.get('preco_mao_obra'),
            preco_material=dados.get('preco_material'),
            preco_unitario=dados.get('preco_unitario'),
            rateio_mo=dados.get('rateio_mo', 50),
            rateio_mat=dados.get('rateio_mat', 50),
            vezes_usado=1,
            ultima_utilizacao=datetime.utcnow()
        )
        
        db.session.add(servico)
        db.session.commit()
        
        return jsonify(servico.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/servicos-autocomplete', methods=['GET'])
@jwt_required()
def autocomplete_servicos():
    """
    Autocomplete híbrido: primeiro serviços do usuário, depois base de referência
    """
    try:
        user = get_current_user()
        q = request.args.get('q', '').strip().lower()
        
        if len(q) < 2:
            return jsonify({'servicos_usuario': [], 'servicos_base': []})
        
        # Buscar serviços do usuário
        servicos_usuario = ServicoUsuario.query.filter(
            ServicoUsuario.user_id == user.id,
            ServicoUsuario.descricao.ilike(f'%{q}%')
        ).order_by(ServicoUsuario.vezes_usado.desc()).limit(10).all()
        
        # Buscar serviços da base
        servicos_base = ServicoBase.query.filter(
            ServicoBase.descricao.ilike(f'%{q}%')
        ).order_by(ServicoBase.descricao).limit(15).all()
        
        print(f"[AUTOCOMPLETE] Busca: '{q}' -> Usuario: {len(servicos_usuario)}, Base: {len(servicos_base)}")
        
        return jsonify({
            'servicos_usuario': [s.to_dict() for s in servicos_usuario],
            'servicos_base': [s.to_dict() for s in servicos_base]
        })
        
    except Exception as e:
        print(f"[AUTOCOMPLETE] Erro: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng', methods=['GET'])
@jwt_required()
def obter_orcamento_eng(obra_id):
    """
    Retorna o orçamento de engenharia completo da obra
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        # Verificar permissão
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar etapas com itens
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).order_by(OrcamentoEngEtapa.ordem, OrcamentoEngEtapa.codigo).all()
        
        # Calcular totais
        total_mo = 0
        total_mat = 0
        total_pago_mo = 0
        total_pago_mat = 0
        total_itens = 0
        itens_vinculados = 0
        
        etapas_dict = []
        for etapa in etapas:
            etapa_mo = 0
            etapa_mat = 0
            etapa_pago_mo = 0
            etapa_pago_mat = 0
            
            itens_dict = []
            for item in etapa.itens:
                totais = item.calcular_totais()
                etapa_mo += totais['total_mao_obra']
                etapa_mat += totais['total_material']
                etapa_pago_mo += item.valor_pago_mo or 0
                etapa_pago_mat += item.valor_pago_mat or 0
                total_itens += 1
                if item.servico_id:
                    itens_vinculados += 1
                itens_dict.append(item.to_dict())
            
            total_mo += etapa_mo
            total_mat += etapa_mat
            total_pago_mo += etapa_pago_mo
            total_pago_mat += etapa_pago_mat
            
            etapa_total = etapa_mo + etapa_mat
            etapa_pago = etapa_pago_mo + etapa_pago_mat
            
            etapas_dict.append({
                **etapa.to_dict(include_itens=False),
                'itens': itens_dict,
                'total_mao_obra': etapa_mo,
                'total_material': etapa_mat,
                'total': etapa_total,
                'total_pago_mo': etapa_pago_mo,
                'total_pago_mat': etapa_pago_mat,
                'total_pago': etapa_pago,
                'percentual': round((etapa_pago / etapa_total * 100) if etapa_total > 0 else 0, 1)
            })
        
        subtotal = total_mo + total_mat
        total_pago = total_pago_mo + total_pago_mat
        bdi = obra.bdi if hasattr(obra, 'bdi') else 0
        valor_bdi = subtotal * (bdi / 100) if bdi else 0
        total_geral = subtotal + valor_bdi
        
        return jsonify({
            'obra_id': obra_id,
            'obra_nome': obra.nome,
            'etapas': etapas_dict,
            'resumo': {
                'total_mao_obra': total_mo,
                'total_material': total_mat,
                'subtotal': subtotal,
                'bdi': bdi,
                'valor_bdi': valor_bdi,
                'total_geral': total_geral,
                'total_pago_mo': total_pago_mo,
                'total_pago_mat': total_pago_mat,
                'total_pago': total_pago,
                'percentual_executado': round((total_pago / subtotal * 100) if subtotal > 0 else 0, 1),
                'total_etapas': len(etapas),
                'total_itens': total_itens,
                'itens_vinculados': itens_vinculados
            }
        })
        
    except Exception as e:
        print(f"[ORCAMENTO-ENG] Erro: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/etapas', methods=['POST'])
@jwt_required()
def criar_etapa_orcamento(obra_id):
    """
    Cria uma nova etapa no orçamento de engenharia
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        
        # Gerar código automaticamente se não fornecido
        if not dados.get('codigo'):
            ultima_etapa = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).order_by(OrcamentoEngEtapa.codigo.desc()).first()
            if ultima_etapa:
                try:
                    ultimo_num = int(ultima_etapa.codigo)
                    dados['codigo'] = f"{ultimo_num + 1:02d}"
                except:
                    dados['codigo'] = "01"
            else:
                dados['codigo'] = "01"
        
        # Calcular ordem
        max_ordem = db.session.query(db.func.max(OrcamentoEngEtapa.ordem)).filter_by(obra_id=obra_id).scalar() or 0
        
        etapa = OrcamentoEngEtapa(
            obra_id=obra_id,
            codigo=dados['codigo'],
            nome=dados['nome'].upper(),
            ordem=max_ordem + 1
        )
        
        db.session.add(etapa)
        db.session.commit()
        
        return jsonify(etapa.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/etapas/<int:etapa_id>', methods=['PUT'])
@jwt_required()
def editar_etapa_orcamento(obra_id, etapa_id):
    """
    Edita uma etapa do orçamento
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        etapa = OrcamentoEngEtapa.query.get_or_404(etapa_id)
        dados = request.json
        
        if 'codigo' in dados:
            etapa.codigo = dados['codigo']
        if 'nome' in dados:
            etapa.nome = dados['nome'].upper()
        if 'ordem' in dados:
            etapa.ordem = dados['ordem']
        
        db.session.commit()
        
        return jsonify(etapa.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/etapas/<int:etapa_id>', methods=['DELETE'])
@jwt_required()
def deletar_etapa_orcamento(obra_id, etapa_id):
    """
    Deleta uma etapa e todos os seus itens (e serviços vinculados)
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        etapa = OrcamentoEngEtapa.query.get_or_404(etapa_id)
        
        # Deletar serviços vinculados aos itens
        for item in etapa.itens:
            if item.servico_id:
                servico = Servico.query.get(item.servico_id)
                if servico:
                    db.session.delete(servico)
        
        db.session.delete(etapa)
        db.session.commit()
        
        return jsonify({"mensagem": "Etapa deletada com sucesso"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/itens', methods=['POST'])
@jwt_required()
def criar_item_orcamento(obra_id):
    """
    Cria um novo item no orçamento de engenharia
    Pode criar serviço automaticamente no Kanban
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        etapa_id = dados['etapa_id']
        
        # Verificar se etapa existe
        etapa = OrcamentoEngEtapa.query.get_or_404(etapa_id)
        if etapa.obra_id != obra_id:
            return jsonify({"erro": "Etapa não pertence a esta obra"}), 400
        
        # Gerar código automaticamente se não fornecido
        if not dados.get('codigo'):
            ultimo_item = OrcamentoEngItem.query.filter_by(etapa_id=etapa_id).order_by(OrcamentoEngItem.codigo.desc()).first()
            if ultimo_item:
                try:
                    partes = ultimo_item.codigo.split('.')
                    ultimo_num = int(partes[-1])
                    dados['codigo'] = f"{etapa.codigo}.{ultimo_num + 1:02d}"
                except:
                    dados['codigo'] = f"{etapa.codigo}.01"
            else:
                dados['codigo'] = f"{etapa.codigo}.01"
        
        # Calcular ordem
        max_ordem = db.session.query(db.func.max(OrcamentoEngItem.ordem)).filter_by(etapa_id=etapa_id).scalar() or 0
        
        # Criar item
        item = OrcamentoEngItem(
            etapa_id=etapa_id,
            codigo=dados['codigo'],
            descricao=dados['descricao'],
            unidade=dados['unidade'],
            quantidade=dados.get('quantidade', 0),
            tipo_composicao=dados.get('tipo_composicao', 'separado'),
            preco_mao_obra=dados.get('preco_mao_obra'),
            preco_material=dados.get('preco_material'),
            preco_unitario=dados.get('preco_unitario'),
            rateio_mo=dados.get('rateio_mo', 50),
            rateio_mat=dados.get('rateio_mat', 50),
            ordem=max_ordem + 1
        )
        
        db.session.add(item)
        db.session.flush()  # Para obter o ID do item
        
        # Opção de serviço
        opcao_servico = dados.get('opcao_servico', 'criar')  # criar | vincular | nao
        
        if opcao_servico == 'criar':
            # Criar serviço automaticamente no Kanban
            totais = item.calcular_totais()
            
            servico = Servico(
                obra_id=obra_id,
                nome=dados['descricao'],
                responsavel=dados.get('responsavel'),
                valor_global_mao_de_obra=totais['total_mao_obra'],
                valor_global_material=totais['total_material']
            )
            db.session.add(servico)
            db.session.flush()
            
            item.servico_id = servico.id
            
        elif opcao_servico == 'vincular' and dados.get('servico_id'):
            # Vincular a serviço existente
            servico_existente = Servico.query.get(dados['servico_id'])
            if servico_existente and servico_existente.obra_id == obra_id:
                item.servico_id = servico_existente.id
                
                # Atualizar valores do serviço (somar)
                totais = item.calcular_totais()
                servico_existente.valor_global_mao_de_obra += totais['total_mao_obra']
                servico_existente.valor_global_material += totais['total_material']
        
        # Salvar na biblioteca do usuário (opcional)
        if dados.get('salvar_biblioteca'):
            servico_usuario = ServicoUsuario.query.filter_by(
                user_id=user.id,
                descricao=dados['descricao'],
                unidade=dados['unidade']
            ).first()
            
            if servico_usuario:
                # Atualizar existente
                servico_usuario.vezes_usado += 1
                servico_usuario.ultima_utilizacao = datetime.utcnow()
                if dados.get('tipo_composicao') == 'separado':
                    servico_usuario.preco_mao_obra = dados.get('preco_mao_obra')
                    servico_usuario.preco_material = dados.get('preco_material')
                else:
                    servico_usuario.preco_unitario = dados.get('preco_unitario')
            else:
                # Criar novo
                novo_servico_usuario = ServicoUsuario(
                    user_id=user.id,
                    categoria=dados.get('categoria'),
                    descricao=dados['descricao'],
                    unidade=dados['unidade'],
                    tipo_composicao=dados.get('tipo_composicao', 'separado'),
                    preco_mao_obra=dados.get('preco_mao_obra'),
                    preco_material=dados.get('preco_material'),
                    preco_unitario=dados.get('preco_unitario'),
                    rateio_mo=dados.get('rateio_mo', 50),
                    rateio_mat=dados.get('rateio_mat', 50),
                    vezes_usado=1,
                    ultima_utilizacao=datetime.utcnow()
                )
                db.session.add(novo_servico_usuario)
        
        db.session.commit()
        
        return jsonify(item.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"[ORCAMENTO-ENG] Erro ao criar item: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/itens/<int:item_id>', methods=['PUT'])
@jwt_required()
def editar_item_orcamento(obra_id, item_id):
    """
    Edita um item do orçamento
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        item = OrcamentoEngItem.query.get_or_404(item_id)
        dados = request.json
        
        # Guardar totais antigos para atualizar serviço
        totais_antigos = item.calcular_totais() if item.servico_id else None
        
        # Atualizar campos
        if 'codigo' in dados:
            item.codigo = dados['codigo']
        if 'descricao' in dados:
            item.descricao = dados['descricao']
        if 'unidade' in dados:
            item.unidade = dados['unidade']
        if 'quantidade' in dados:
            item.quantidade = dados['quantidade']
        if 'tipo_composicao' in dados:
            item.tipo_composicao = dados['tipo_composicao']
        if 'preco_mao_obra' in dados:
            item.preco_mao_obra = dados['preco_mao_obra']
        if 'preco_material' in dados:
            item.preco_material = dados['preco_material']
        if 'preco_unitario' in dados:
            item.preco_unitario = dados['preco_unitario']
        if 'rateio_mo' in dados:
            item.rateio_mo = dados['rateio_mo']
        if 'rateio_mat' in dados:
            item.rateio_mat = dados['rateio_mat']
        
        # Atualizar serviço vinculado se existir
        if item.servico_id:
            servico = Servico.query.get(item.servico_id)
            if servico:
                totais_novos = item.calcular_totais()
                
                # Definir valores diretamente (não apenas diferença)
                # Se o serviço estava zerado, isso corrige o problema
                servico.valor_global_mao_de_obra = totais_novos['total_mao_obra']
                servico.valor_global_material = totais_novos['total_material']
                
                # Atualizar nome do serviço se descrição mudou
                if 'descricao' in dados:
                    servico.nome = dados['descricao']
                
                print(f"--- [LOG] Serviço {servico.id} atualizado: MO={totais_novos['total_mao_obra']}, MAT={totais_novos['total_material']} ---")
        
        db.session.commit()
        
        return jsonify(item.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/itens/<int:item_id>', methods=['DELETE'])
@jwt_required()
def deletar_item_orcamento(obra_id, item_id):
    """
    Deleta um item do orçamento E o serviço vinculado
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        item = OrcamentoEngItem.query.get_or_404(item_id)
        
        # Deletar serviço vinculado
        if item.servico_id:
            servico = Servico.query.get(item.servico_id)
            if servico:
                # Verificar se há outros itens usando este serviço
                outros_itens = OrcamentoEngItem.query.filter(
                    OrcamentoEngItem.servico_id == item.servico_id,
                    OrcamentoEngItem.id != item_id
                ).count()
                
                if outros_itens > 0:
                    # Outros itens usam este serviço, apenas desvincular
                    totais = item.calcular_totais()
                    servico.valor_global_mao_de_obra = max(0, servico.valor_global_mao_de_obra - totais['total_mao_obra'])
                    servico.valor_global_material = max(0, servico.valor_global_material - totais['total_material'])
                else:
                    # Nenhum outro item usa, deletar serviço
                    db.session.delete(servico)
        
        db.session.delete(item)
        db.session.commit()
        
        return jsonify({"mensagem": "Item deletado com sucesso"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/sincronizar-servicos', methods=['POST'])
@jwt_required()
def sincronizar_servicos_com_orcamento(obra_id):
    """
    Sincroniza os valores de TODOS os serviços do Kanban com o Orçamento de Engenharia
    - Cria serviços para itens que não têm serviço vinculado
    - Atualiza valores de serviços existentes
    - Remove vínculos a serviços que não existem mais
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar todos os itens do orçamento
        itens = OrcamentoEngItem.query.join(OrcamentoEngEtapa).filter(
            OrcamentoEngEtapa.obra_id == obra_id
        ).all()
        
        servicos_atualizados = 0
        servicos_criados = 0
        vinculos_corrigidos = 0
        
        for item in itens:
            totais = item.calcular_totais()
            
            if item.servico_id:
                # Verificar se o serviço existe
                servico = Servico.query.get(item.servico_id)
                
                if servico:
                    # Atualizar valores do serviço existente
                    servico.valor_global_mao_de_obra = totais['total_mao_obra']
                    servico.valor_global_material = totais['total_material']
                    servicos_atualizados += 1
                else:
                    # Serviço não existe mais - criar novo
                    novo_servico = Servico(
                        obra_id=obra_id,
                        nome=item.descricao,
                        valor_global_mao_de_obra=totais['total_mao_obra'],
                        valor_global_material=totais['total_material']
                    )
                    db.session.add(novo_servico)
                    db.session.flush()
                    item.servico_id = novo_servico.id
                    vinculos_corrigidos += 1
                    servicos_criados += 1
            else:
                # Item não tem serviço vinculado - criar um
                novo_servico = Servico(
                    obra_id=obra_id,
                    nome=item.descricao,
                    valor_global_mao_de_obra=totais['total_mao_obra'],
                    valor_global_material=totais['total_material']
                )
                db.session.add(novo_servico)
                db.session.flush()
                item.servico_id = novo_servico.id
                servicos_criados += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": f"Sincronização concluída!",
            "servicos_atualizados": servicos_atualizados,
            "servicos_criados": servicos_criados,
            "vinculos_corrigidos": vinculos_corrigidos
        })
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/apagar-tudo', methods=['DELETE'])
@jwt_required()
def apagar_orcamento_completo(obra_id):
    """
    Apaga TODO o orçamento de engenharia da obra (etapas, itens e serviços vinculados)
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        # Apenas master e administrador podem apagar
        if user.role not in ['master', 'administrador']:
            return jsonify({"erro": "Apenas administradores podem apagar o orçamento completo"}), 403
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar todas as etapas
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).all()
        
        itens_deletados = 0
        etapas_deletadas = 0
        servicos_deletados = 0
        
        for etapa in etapas:
            for item in etapa.itens:
                # Deletar serviço vinculado se existir
                if item.servico_id:
                    servico = Servico.query.get(item.servico_id)
                    if servico:
                        # Verificar se o serviço tem pagamentos
                        if len(servico.pagamentos) > 0:
                            # Não deletar serviço com pagamentos, apenas desvincular
                            item.servico_id = None
                        else:
                            db.session.delete(servico)
                            servicos_deletados += 1
                
                db.session.delete(item)
                itens_deletados += 1
            
            db.session.delete(etapa)
            etapas_deletadas += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": f"Orçamento apagado com sucesso!",
            "etapas_deletadas": etapas_deletadas,
            "itens_deletados": itens_deletados,
            "servicos_deletados": servicos_deletados
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Erro ao apagar orçamento: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/sincronizar-pagamentos', methods=['POST'])
@jwt_required()
def sincronizar_pagamentos_orcamento(obra_id):
    """
    Sincroniza os valores pagos dos itens do orçamento com os pagamentos do Kanban
    Deve ser chamado após registrar/deletar pagamentos
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar todos os itens do orçamento desta obra
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).all()
        
        itens_atualizados = 0
        
        for etapa in etapas:
            for item in etapa.itens:
                if item.servico_id:
                    servico = Servico.query.get(item.servico_id)
                    if servico:
                        # Calcular total pago no serviço
                        valor_pago_mo = 0
                        valor_pago_mat = 0
                        
                        for pag in servico.pagamentos:
                            if pag.status == 'Pago':
                                if pag.tipo_pagamento == 'mao_de_obra':
                                    valor_pago_mo += pag.valor_pago or pag.valor_total or 0
                                else:
                                    valor_pago_mat += pag.valor_pago or pag.valor_total or 0
                        
                        # Verificar se há outros itens usando o mesmo serviço
                        itens_mesmo_servico = OrcamentoEngItem.query.filter_by(servico_id=item.servico_id).all()
                        
                        if len(itens_mesmo_servico) > 1:
                            # Ratear proporcionalmente entre os itens
                            total_mo_servico = sum(i.calcular_totais()['total_mao_obra'] for i in itens_mesmo_servico)
                            total_mat_servico = sum(i.calcular_totais()['total_material'] for i in itens_mesmo_servico)
                            
                            totais_item = item.calcular_totais()
                            
                            if total_mo_servico > 0:
                                proporcao_mo = totais_item['total_mao_obra'] / total_mo_servico
                                item.valor_pago_mo = valor_pago_mo * proporcao_mo
                            
                            if total_mat_servico > 0:
                                proporcao_mat = totais_item['total_material'] / total_mat_servico
                                item.valor_pago_mat = valor_pago_mat * proporcao_mat
                        else:
                            # Item único para este serviço
                            item.valor_pago_mo = valor_pago_mo
                            item.valor_pago_mat = valor_pago_mat
                        
                        itens_atualizados += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": "Pagamentos sincronizados",
            "itens_atualizados": itens_atualizados
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/popular-servicos-base', methods=['GET', 'POST'])
def popular_servicos_base():
    """
    Popula a base de serviços de referência (executar apenas uma vez)
    GET: Acesso direto pelo navegador (sem autenticação, apenas para setup inicial)
    POST: Acesso autenticado
    """
    try:
        # Verificar se já está populado
        if ServicoBase.query.count() > 0:
            return jsonify({"mensagem": "Base já populada", "total": ServicoBase.query.count()})
        
        # Base de serviços comuns
        servicos = [
            # SERVIÇOS PRELIMINARES
            {"categoria": "preliminares", "descricao": "Limpeza de terreno", "unidade": "m²", "tipo": "composto", "preco": 5.50, "rateio_mo": 80},
            {"categoria": "preliminares", "descricao": "Locação de obra", "unidade": "m²", "tipo": "composto", "preco": 8.00, "rateio_mo": 90},
            {"categoria": "preliminares", "descricao": "Tapume em chapa compensada", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "preliminares", "descricao": "Barracão de obra", "unidade": "m²", "tipo": "composto", "preco": 320.00, "rateio_mo": 40},
            {"categoria": "preliminares", "descricao": "Placa de obra", "unidade": "m²", "tipo": "separado", "mo": 50.00, "mat": 120.00},
            {"categoria": "preliminares", "descricao": "Instalações provisórias (água/luz)", "unidade": "vb", "tipo": "composto", "preco": 2500.00, "rateio_mo": 30},
            
            # FUNDAÇÃO
            {"categoria": "fundacao", "descricao": "Escavação manual até 1,5m", "unidade": "m³", "tipo": "composto", "preco": 65.00, "rateio_mo": 95},
            {"categoria": "fundacao", "descricao": "Escavação mecânica", "unidade": "m³", "tipo": "composto", "preco": 28.00, "rateio_mo": 30},
            {"categoria": "fundacao", "descricao": "Apiloamento manual", "unidade": "m²", "tipo": "composto", "preco": 8.00, "rateio_mo": 100},
            {"categoria": "fundacao", "descricao": "Lastro de concreto magro", "unidade": "m³", "tipo": "separado", "mo": 80.00, "mat": 200.00},
            {"categoria": "fundacao", "descricao": "Forma para sapata", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 20.00},
            {"categoria": "fundacao", "descricao": "Forma para baldrame", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 22.00},
            {"categoria": "fundacao", "descricao": "Armação CA-50", "unidade": "kg", "tipo": "separado", "mo": 4.50, "mat": 8.00},
            {"categoria": "fundacao", "descricao": "Armação CA-60", "unidade": "kg", "tipo": "separado", "mo": 4.50, "mat": 8.50},
            {"categoria": "fundacao", "descricao": "Concreto fck 20 MPa", "unidade": "m³", "tipo": "separado", "mo": 70.00, "mat": 250.00},
            {"categoria": "fundacao", "descricao": "Concreto fck 25 MPa", "unidade": "m³", "tipo": "separado", "mo": 80.00, "mat": 300.00},
            {"categoria": "fundacao", "descricao": "Concreto fck 30 MPa", "unidade": "m³", "tipo": "separado", "mo": 90.00, "mat": 330.00},
            {"categoria": "fundacao", "descricao": "Impermeabilização de baldrame", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 20.00},
            {"categoria": "fundacao", "descricao": "Estaca broca d=25cm", "unidade": "m", "tipo": "separado", "mo": 35.00, "mat": 45.00},
            {"categoria": "fundacao", "descricao": "Estaca broca d=30cm", "unidade": "m", "tipo": "separado", "mo": 40.00, "mat": 55.00},
            
            # ESTRUTURA
            {"categoria": "estrutura", "descricao": "Forma para pilar", "unidade": "m²", "tipo": "separado", "mo": 30.00, "mat": 25.00},
            {"categoria": "estrutura", "descricao": "Forma para viga", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 24.00},
            {"categoria": "estrutura", "descricao": "Forma para laje", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 23.00},
            {"categoria": "estrutura", "descricao": "Escoramento de laje", "unidade": "m²", "tipo": "separado", "mo": 8.00, "mat": 10.00},
            {"categoria": "estrutura", "descricao": "Laje pré-moldada h=12cm", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 60.00},
            {"categoria": "estrutura", "descricao": "Laje pré-moldada h=16cm", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 72.00},
            {"categoria": "estrutura", "descricao": "Laje pré-moldada h=20cm", "unidade": "m²", "tipo": "separado", "mo": 32.00, "mat": 85.00},
            {"categoria": "estrutura", "descricao": "Verga/contraverga concreto", "unidade": "m", "tipo": "separado", "mo": 20.00, "mat": 25.00},
            {"categoria": "estrutura", "descricao": "Cinta de amarração", "unidade": "m", "tipo": "separado", "mo": 18.00, "mat": 22.00},
            
            # ALVENARIA
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco cerâmico 9x19x19", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 30.00},
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco cerâmico 14x19x39", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 40.00},
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco concreto 14x19x39", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 47.00},
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco concreto 19x19x39", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 55.00},
            {"categoria": "alvenaria", "descricao": "Encunhamento de alvenaria", "unidade": "m", "tipo": "separado", "mo": 8.00, "mat": 4.00},
            {"categoria": "alvenaria", "descricao": "Fixação de batente", "unidade": "un", "tipo": "composto", "preco": 85.00, "rateio_mo": 70},
            
            # INSTALAÇÕES HIDRÁULICAS
            {"categoria": "hidraulica", "descricao": "Ponto de água fria PVC", "unidade": "pt", "tipo": "separado", "mo": 85.00, "mat": 100.00},
            {"categoria": "hidraulica", "descricao": "Ponto de água quente CPVC", "unidade": "pt", "tipo": "separado", "mo": 90.00, "mat": 130.00},
            {"categoria": "hidraulica", "descricao": "Ponto de água quente PPR", "unidade": "pt", "tipo": "separado", "mo": 95.00, "mat": 140.00},
            {"categoria": "hidraulica", "descricao": "Ponto de esgoto PVC", "unidade": "pt", "tipo": "separado", "mo": 75.00, "mat": 90.00},
            {"categoria": "hidraulica", "descricao": "Caixa sifonada 100x100", "unidade": "un", "tipo": "separado", "mo": 45.00, "mat": 50.00},
            {"categoria": "hidraulica", "descricao": "Caixa de gordura", "unidade": "un", "tipo": "separado", "mo": 120.00, "mat": 180.00},
            {"categoria": "hidraulica", "descricao": "Caixa de inspeção", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 200.00},
            {"categoria": "hidraulica", "descricao": "Vaso sanitário com caixa acoplada", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 700.00},
            {"categoria": "hidraulica", "descricao": "Lavatório com coluna", "unidade": "un", "tipo": "separado", "mo": 120.00, "mat": 400.00},
            {"categoria": "hidraulica", "descricao": "Tanque de louça", "unidade": "un", "tipo": "separado", "mo": 100.00, "mat": 380.00},
            {"categoria": "hidraulica", "descricao": "Pia de cozinha inox", "unidade": "un", "tipo": "separado", "mo": 120.00, "mat": 450.00},
            {"categoria": "hidraulica", "descricao": "Registro de gaveta 3/4\"", "unidade": "un", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "hidraulica", "descricao": "Registro de pressão 3/4\"", "unidade": "un", "tipo": "separado", "mo": 35.00, "mat": 65.00},
            
            # INSTALAÇÕES ELÉTRICAS
            {"categoria": "eletrica", "descricao": "Ponto de luz", "unidade": "pt", "tipo": "separado", "mo": 55.00, "mat": 70.00},
            {"categoria": "eletrica", "descricao": "Ponto de tomada 2P+T", "unidade": "pt", "tipo": "separado", "mo": 40.00, "mat": 55.00},
            {"categoria": "eletrica", "descricao": "Ponto de tomada alta", "unidade": "pt", "tipo": "separado", "mo": 45.00, "mat": 65.00},
            {"categoria": "eletrica", "descricao": "Ponto de interruptor simples", "unidade": "pt", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "eletrica", "descricao": "Ponto de interruptor duplo", "unidade": "pt", "tipo": "separado", "mo": 40.00, "mat": 60.00},
            {"categoria": "eletrica", "descricao": "Ponto de ar condicionado", "unidade": "pt", "tipo": "separado", "mo": 85.00, "mat": 120.00},
            {"categoria": "eletrica", "descricao": "Ponto de chuveiro elétrico", "unidade": "pt", "tipo": "separado", "mo": 75.00, "mat": 95.00},
            {"categoria": "eletrica", "descricao": "Quadro distribuição 12 circuitos", "unidade": "un", "tipo": "separado", "mo": 200.00, "mat": 450.00},
            {"categoria": "eletrica", "descricao": "Quadro distribuição 24 circuitos", "unidade": "un", "tipo": "separado", "mo": 250.00, "mat": 700.00},
            {"categoria": "eletrica", "descricao": "Ponto de telefone/internet", "unidade": "pt", "tipo": "separado", "mo": 45.00, "mat": 55.00},
            {"categoria": "eletrica", "descricao": "Ponto de TV/antena", "unidade": "pt", "tipo": "separado", "mo": 45.00, "mat": 50.00},
            
            # REVESTIMENTOS
            {"categoria": "revestimento", "descricao": "Chapisco interno", "unidade": "m²", "tipo": "separado", "mo": 5.50, "mat": 3.00},
            {"categoria": "revestimento", "descricao": "Chapisco externo", "unidade": "m²", "tipo": "separado", "mo": 6.50, "mat": 3.50},
            {"categoria": "revestimento", "descricao": "Reboco interno e=2cm", "unidade": "m²", "tipo": "separado", "mo": 22.00, "mat": 10.00},
            {"categoria": "revestimento", "descricao": "Reboco externo e=2,5cm", "unidade": "m²", "tipo": "separado", "mo": 26.00, "mat": 12.00},
            {"categoria": "revestimento", "descricao": "Reboco paulista", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 14.00},
            {"categoria": "revestimento", "descricao": "Gesso liso", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 8.00},
            {"categoria": "revestimento", "descricao": "Forro de gesso", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 20.00},
            {"categoria": "revestimento", "descricao": "Forro de PVC", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 35.00},
            {"categoria": "revestimento", "descricao": "Contrapiso e=5cm", "unidade": "m²", "tipo": "separado", "mo": 22.00, "mat": 26.00},
            {"categoria": "revestimento", "descricao": "Contrapiso e=7cm", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 32.00},
            {"categoria": "revestimento", "descricao": "Piso cerâmico PEI-4", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 60.00},
            {"categoria": "revestimento", "descricao": "Piso cerâmico PEI-5", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 75.00},
            {"categoria": "revestimento", "descricao": "Piso porcelanato polido", "unidade": "m²", "tipo": "separado", "mo": 45.00, "mat": 100.00},
            {"categoria": "revestimento", "descricao": "Piso porcelanato acetinado", "unidade": "m²", "tipo": "separado", "mo": 45.00, "mat": 85.00},
            {"categoria": "revestimento", "descricao": "Azulejo 30x60", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 65.00},
            {"categoria": "revestimento", "descricao": "Rodapé cerâmico h=10cm", "unidade": "m", "tipo": "separado", "mo": 10.00, "mat": 12.00},
            {"categoria": "revestimento", "descricao": "Soleira granito", "unidade": "m", "tipo": "separado", "mo": 35.00, "mat": 60.00},
            {"categoria": "revestimento", "descricao": "Peitoril granito", "unidade": "m", "tipo": "separado", "mo": 30.00, "mat": 55.00},
            {"categoria": "revestimento", "descricao": "Bancada granito", "unidade": "m²", "tipo": "separado", "mo": 80.00, "mat": 350.00},
            
            # PINTURA
            {"categoria": "pintura", "descricao": "Massa corrida PVA", "unidade": "m²", "tipo": "separado", "mo": 12.00, "mat": 6.00},
            {"categoria": "pintura", "descricao": "Massa acrílica", "unidade": "m²", "tipo": "separado", "mo": 14.00, "mat": 8.00},
            {"categoria": "pintura", "descricao": "Pintura látex PVA 2 demãos", "unidade": "m²", "tipo": "separado", "mo": 12.00, "mat": 6.00},
            {"categoria": "pintura", "descricao": "Pintura acrílica 2 demãos", "unidade": "m²", "tipo": "separado", "mo": 14.00, "mat": 8.00},
            {"categoria": "pintura", "descricao": "Pintura acrílica semi-brilho", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 10.00},
            {"categoria": "pintura", "descricao": "Textura acrílica", "unidade": "m²", "tipo": "separado", "mo": 16.00, "mat": 12.00},
            {"categoria": "pintura", "descricao": "Grafiato", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 20.00},
            {"categoria": "pintura", "descricao": "Pintura esmalte em madeira", "unidade": "m²", "tipo": "separado", "mo": 18.00, "mat": 12.00},
            {"categoria": "pintura", "descricao": "Pintura esmalte em ferro", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 15.00},
            {"categoria": "pintura", "descricao": "Verniz em madeira", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 10.00},
            
            # ESQUADRIAS
            {"categoria": "esquadria", "descricao": "Porta madeira 80x210 completa", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 600.00},
            {"categoria": "esquadria", "descricao": "Porta madeira 70x210 completa", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 530.00},
            {"categoria": "esquadria", "descricao": "Porta madeira 60x210 completa", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 480.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio correr 120x120", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 700.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio correr 150x120", "unidade": "un", "tipo": "separado", "mo": 180.00, "mat": 850.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio correr 200x120", "unidade": "un", "tipo": "separado", "mo": 200.00, "mat": 1100.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio maxim-ar 60x60", "unidade": "un", "tipo": "separado", "mo": 80.00, "mat": 300.00},
            {"categoria": "esquadria", "descricao": "Porta alumínio correr 200x210", "unidade": "un", "tipo": "separado", "mo": 300.00, "mat": 1500.00},
            {"categoria": "esquadria", "descricao": "Porta alumínio pivotante", "unidade": "un", "tipo": "separado", "mo": 350.00, "mat": 2000.00},
            {"categoria": "esquadria", "descricao": "Box vidro temperado", "unidade": "m²", "tipo": "separado", "mo": 80.00, "mat": 300.00},
            {"categoria": "esquadria", "descricao": "Espelho 4mm", "unidade": "m²", "tipo": "separado", "mo": 50.00, "mat": 120.00},
            
            # COBERTURA
            {"categoria": "cobertura", "descricao": "Estrutura madeira para telha", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 60.00},
            {"categoria": "cobertura", "descricao": "Estrutura metálica para telha", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 80.00},
            {"categoria": "cobertura", "descricao": "Telha cerâmica", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 40.00},
            {"categoria": "cobertura", "descricao": "Telha de concreto", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 45.00},
            {"categoria": "cobertura", "descricao": "Telha fibrocimento 6mm", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 35.00},
            {"categoria": "cobertura", "descricao": "Telha sanduíche", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 110.00},
            {"categoria": "cobertura", "descricao": "Cumeeira cerâmica", "unidade": "m", "tipo": "separado", "mo": 15.00, "mat": 30.00},
            {"categoria": "cobertura", "descricao": "Calha galvanizada", "unidade": "m", "tipo": "separado", "mo": 30.00, "mat": 55.00},
            {"categoria": "cobertura", "descricao": "Rufo galvanizado", "unidade": "m", "tipo": "separado", "mo": 25.00, "mat": 40.00},
            {"categoria": "cobertura", "descricao": "Manta subcobertura", "unidade": "m²", "tipo": "separado", "mo": 8.00, "mat": 12.00},
            
            # IMPERMEABILIZAÇÃO
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização manta asfáltica 3mm", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização manta asfáltica 4mm", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 65.00},
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização argamassa polimérica", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 25.00},
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização acrílica", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 18.00},
            
            # LIMPEZA E ACABAMENTO
            {"categoria": "limpeza", "descricao": "Limpeza final da obra", "unidade": "m²", "tipo": "composto", "preco": 8.00, "rateio_mo": 90},
            {"categoria": "limpeza", "descricao": "Remoção de entulho", "unidade": "m³", "tipo": "composto", "preco": 95.00, "rateio_mo": 40},
            {"categoria": "limpeza", "descricao": "Regularização de terreno", "unidade": "m²", "tipo": "composto", "preco": 12.00, "rateio_mo": 80},
        ]
        
        # Inserir serviços
        for s in servicos:
            servico_base = ServicoBase(
                categoria=s['categoria'],
                descricao=s['descricao'],
                unidade=s['unidade'],
                tipo_composicao=s['tipo'],
                preco_mao_obra=s.get('mo'),
                preco_material=s.get('mat'),
                preco_unitario=s.get('preco'),
                rateio_mo=s.get('rateio_mo', 50),
                rateio_mat=100 - s.get('rateio_mo', 50) if s.get('rateio_mo') else 50
            )
            db.session.add(servico_base)
        
        db.session.commit()
        
        return jsonify({
            "mensagem": "Base populada com sucesso",
            "total": len(servicos)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@app.route('/categorias-servico', methods=['GET'])
@jwt_required()
def listar_categorias():
    """
    Lista todas as categorias de serviços disponíveis
    """
    categorias = [
        {"id": "preliminares", "nome": "Serviços Preliminares", "icone": "🏗️"},
        {"id": "fundacao", "nome": "Fundação", "icone": "🧱"},
        {"id": "estrutura", "nome": "Estrutura", "icone": "🏛️"},
        {"id": "alvenaria", "nome": "Alvenaria", "icone": "🧱"},
        {"id": "hidraulica", "nome": "Instalações Hidráulicas", "icone": "🚿"},
        {"id": "eletrica", "nome": "Instalações Elétricas", "icone": "⚡"},
        {"id": "revestimento", "nome": "Revestimentos", "icone": "🎨"},
        {"id": "pintura", "nome": "Pintura", "icone": "🖌️"},
        {"id": "esquadria", "nome": "Esquadrias", "icone": "🚪"},
        {"id": "cobertura", "nome": "Cobertura", "icone": "🏠"},
        {"id": "impermeabilizacao", "nome": "Impermeabilização", "icone": "💧"},
        {"id": "limpeza", "nome": "Limpeza e Acabamento", "icone": "🧹"},
    ]
    return jsonify(categorias)


# ==============================================================================
# GERAÇÃO DE ORÇAMENTO POR PLANTA BAIXA (CLAUDE VISION)
# ==============================================================================

@app.route('/obras/<int:obra_id>/orcamento-eng/gerar-por-planta', methods=['POST'])
@jwt_required()
def gerar_orcamento_por_planta(obra_id):
    """
    Recebe uma imagem de planta baixa e usa Claude Vision para gerar orçamento automaticamente
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        imagem_base64 = dados.get('imagem_base64')
        media_type = dados.get('media_type', 'image/jpeg')
        area_total = dados.get('area_total')
        padrao = dados.get('padrao', 'médio')
        pavimentos = dados.get('pavimentos', 1)
        tipo_construcao = dados.get('tipo_construcao', 'residencial')
        
        if not imagem_base64:
            return jsonify({"erro": "Imagem não fornecida"}), 400
        
        # Validar media_type (API Anthropic aceita imagens e PDF)
        tipos_imagem = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
        tipos_documento = ['application/pdf']
        tipos_validos = tipos_imagem + tipos_documento
        
        if media_type not in tipos_validos:
            return jsonify({"erro": f"Formato não suportado: {media_type}. Use JPG, PNG, GIF, WEBP ou PDF."}), 400
        
        # Determinar se é imagem ou documento (para estrutura da API)
        is_pdf = media_type in tipos_documento
        
        # Remover prefixo data:image se existir
        if ',' in imagem_base64:
            imagem_base64 = imagem_base64.split(',')[1]
        
        # Chave da API Anthropic (configurar como variável de ambiente)
        anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not anthropic_api_key:
            return jsonify({"erro": "API Key da Anthropic não configurada"}), 500
        
        print(f"[PLANTA-IA] Analisando planta para obra {obra_id}...")
        
        # Montar prompt para análise
        prompt = f"""Analise esta planta baixa de uma construção e gere um orçamento detalhado.

INFORMAÇÕES FORNECIDAS:
- Área total informada: {area_total if area_total else 'não informada (estimar pela planta)'}
- Padrão de acabamento: {padrao}
- Número de pavimentos: {pavimentos}
- Tipo de construção: {tipo_construcao}

INSTRUÇÕES:
1. Identifique todos os ambientes visíveis na planta (quartos, salas, banheiros, cozinha, etc.)
2. Estime as dimensões e áreas de cada ambiente se possível ver escala ou cotas
3. Calcule quantitativos para cada serviço de construção
4. Use valores realistas baseados nas dimensões identificadas

IMPORTANTE: Retorne APENAS um JSON válido, sem markdown, sem explicações, seguindo EXATAMENTE esta estrutura:

{{
    "dados_identificados": {{
        "area_estimada": 120,
        "ambientes": [
            {{"nome": "Sala", "area_estimada": 20}},
            {{"nome": "Quarto 1", "area_estimada": 12}},
            {{"nome": "Banheiro 1", "area_estimada": 4}}
        ],
        "total_ambientes": 8,
        "banheiros": 2,
        "paredes_lineares_m": 85,
        "portas_estimadas": 8,
        "janelas_estimadas": 10,
        "observacoes": "Casa térrea com planta retangular"
    }},
    "etapas": [
        {{
            "codigo": "01",
            "nome": "SERVIÇOS PRELIMINARES",
            "itens": [
                {{
                    "codigo": "01.01",
                    "descricao": "Limpeza do terreno",
                    "unidade": "m²",
                    "quantidade": 150,
                    "justificativa": "Área do terreno estimada em 25% maior que área construída"
                }},
                {{
                    "codigo": "01.02",
                    "descricao": "Locação da obra",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída total"
                }}
            ]
        }},
        {{
            "codigo": "02",
            "nome": "FUNDAÇÃO",
            "itens": [
                {{
                    "codigo": "02.01",
                    "descricao": "Escavação manual até 1,5m",
                    "unidade": "m³",
                    "quantidade": 36,
                    "justificativa": "Perímetro 40m x profundidade 0.6m x largura 1.5m"
                }},
                {{
                    "codigo": "02.02",
                    "descricao": "Concreto fck 25 MPa",
                    "unidade": "m³",
                    "quantidade": 18,
                    "justificativa": "Volume de concreto para sapatas e baldrame"
                }}
            ]
        }},
        {{
            "codigo": "03",
            "nome": "ESTRUTURA",
            "itens": [
                {{
                    "codigo": "03.01",
                    "descricao": "Laje pré-moldada h=12cm",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }}
            ]
        }},
        {{
            "codigo": "04",
            "nome": "ALVENARIA",
            "itens": [
                {{
                    "codigo": "04.01",
                    "descricao": "Alvenaria bloco cerâmico 14x19x39",
                    "unidade": "m²",
                    "quantidade": 238,
                    "justificativa": "Perímetro 85m x pé-direito 2.8m"
                }}
            ]
        }},
        {{
            "codigo": "05",
            "nome": "INSTALAÇÕES HIDRÁULICAS",
            "itens": [
                {{
                    "codigo": "05.01",
                    "descricao": "Ponto de água fria PVC",
                    "unidade": "pt",
                    "quantidade": 18,
                    "justificativa": "2 banheiros (8pt) + cozinha (4pt) + área serviço (4pt) + jardim (2pt)"
                }},
                {{
                    "codigo": "05.02",
                    "descricao": "Ponto de esgoto PVC",
                    "unidade": "pt",
                    "quantidade": 12,
                    "justificativa": "2 banheiros (6pt) + cozinha (3pt) + área serviço (3pt)"
                }},
                {{
                    "codigo": "05.03",
                    "descricao": "Vaso sanitário com caixa acoplada",
                    "unidade": "un",
                    "quantidade": 2,
                    "justificativa": "1 por banheiro"
                }},
                {{
                    "codigo": "05.04",
                    "descricao": "Lavatório com coluna",
                    "unidade": "un",
                    "quantidade": 2,
                    "justificativa": "1 por banheiro"
                }}
            ]
        }},
        {{
            "codigo": "06",
            "nome": "INSTALAÇÕES ELÉTRICAS",
            "itens": [
                {{
                    "codigo": "06.01",
                    "descricao": "Ponto de luz",
                    "unidade": "pt",
                    "quantidade": 15,
                    "justificativa": "Média de 1-2 por ambiente"
                }},
                {{
                    "codigo": "06.02",
                    "descricao": "Ponto de tomada 2P+T",
                    "unidade": "pt",
                    "quantidade": 45,
                    "justificativa": "Média de 5-6 por ambiente"
                }},
                {{
                    "codigo": "06.03",
                    "descricao": "Quadro distribuição 12 circuitos",
                    "unidade": "un",
                    "quantidade": 1,
                    "justificativa": "Quadro principal"
                }}
            ]
        }},
        {{
            "codigo": "07",
            "nome": "REVESTIMENTOS",
            "itens": [
                {{
                    "codigo": "07.01",
                    "descricao": "Chapisco interno",
                    "unidade": "m²",
                    "quantidade": 476,
                    "justificativa": "Paredes internas 238m² x 2 faces"
                }},
                {{
                    "codigo": "07.02",
                    "descricao": "Reboco interno e=2cm",
                    "unidade": "m²",
                    "quantidade": 476,
                    "justificativa": "Paredes internas"
                }},
                {{
                    "codigo": "07.03",
                    "descricao": "Contrapiso e=5cm",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }},
                {{
                    "codigo": "07.04",
                    "descricao": "Piso cerâmico PEI-4",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }},
                {{
                    "codigo": "07.05",
                    "descricao": "Azulejo 30x60",
                    "unidade": "m²",
                    "quantidade": 28,
                    "justificativa": "Paredes dos banheiros até 1.8m de altura"
                }}
            ]
        }},
        {{
            "codigo": "08",
            "nome": "PINTURA",
            "itens": [
                {{
                    "codigo": "08.01",
                    "descricao": "Massa corrida PVA",
                    "unidade": "m²",
                    "quantidade": 448,
                    "justificativa": "Paredes - azulejos"
                }},
                {{
                    "codigo": "08.02",
                    "descricao": "Pintura acrílica 2 demãos",
                    "unidade": "m²",
                    "quantidade": 568,
                    "justificativa": "Paredes + teto"
                }}
            ]
        }},
        {{
            "codigo": "09",
            "nome": "ESQUADRIAS",
            "itens": [
                {{
                    "codigo": "09.01",
                    "descricao": "Porta madeira 80x210 completa",
                    "unidade": "un",
                    "quantidade": 5,
                    "justificativa": "Portas internas dos quartos e banheiros"
                }},
                {{
                    "codigo": "09.02",
                    "descricao": "Porta madeira 70x210 completa",
                    "unidade": "un",
                    "quantidade": 3,
                    "justificativa": "Portas menores"
                }},
                {{
                    "codigo": "09.03",
                    "descricao": "Janela alumínio correr 120x120",
                    "unidade": "un",
                    "quantidade": 10,
                    "justificativa": "Janelas dos ambientes"
                }}
            ]
        }},
        {{
            "codigo": "10",
            "nome": "COBERTURA",
            "itens": [
                {{
                    "codigo": "10.01",
                    "descricao": "Estrutura madeira para telha",
                    "unidade": "m²",
                    "quantidade": 140,
                    "justificativa": "Área construída + beiral"
                }},
                {{
                    "codigo": "10.02",
                    "descricao": "Telha cerâmica",
                    "unidade": "m²",
                    "quantidade": 140,
                    "justificativa": "Área de cobertura"
                }}
            ]
        }},
        {{
            "codigo": "11",
            "nome": "LIMPEZA E ACABAMENTO",
            "itens": [
                {{
                    "codigo": "11.01",
                    "descricao": "Limpeza final da obra",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }}
            ]
        }}
    ]
}}

Adapte os quantitativos conforme o que você identificar na planta. Se a planta mostrar mais ou menos ambientes, ajuste proporcionalmente."""

        # Chamar API da Anthropic
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': anthropic_api_key,
            'anthropic-version': '2023-06-01'
        }
        
        # Adicionar header beta para suporte a PDF
        if is_pdf:
            headers['anthropic-beta'] = 'pdfs-2024-09-25'
        
        # Estrutura diferente para PDF (document) vs imagem (image)
        if is_pdf:
            content_block = {
                'type': 'document',
                'source': {
                    'type': 'base64',
                    'media_type': media_type,
                    'data': imagem_base64
                }
            }
        else:
            content_block = {
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': media_type,
                    'data': imagem_base64
                }
            }
        
        payload = {
            'model': 'claude-sonnet-4-20250514',
            'max_tokens': 8000,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        content_block,
                        {
                            'type': 'text',
                            'text': prompt
                        }
                    ]
                }
            ]
        }
        
        print(f"[PLANTA-IA] Enviando para Claude Vision... (tipo: {'PDF' if is_pdf else 'imagem'})")
        
        # Usar urllib (nativo do Python) para chamar API
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                response_data = response.read().decode('utf-8')
                result = json.loads(response_data)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else str(e)
            print(f"[PLANTA-IA] Erro da API: {e.code} - {error_body}")
            
            # Mensagens de erro mais claras
            erro_msg = f"Erro na API de IA: {e.code}"
            try:
                error_json = json.loads(error_body)
                error_type = error_json.get('error', {}).get('type', '')
                error_message = error_json.get('error', {}).get('message', '')
                
                if e.code == 400:
                    if 'credit' in error_message.lower() or 'billing' in error_message.lower():
                        erro_msg = "Créditos insuficientes na conta Anthropic. Adicione créditos em console.anthropic.com/billing"
                    elif 'invalid' in error_message.lower():
                        erro_msg = "API Key inválida. Verifique a configuração no Railway."
                    else:
                        erro_msg = f"Erro na requisição: {error_message}"
                elif e.code == 401:
                    erro_msg = "API Key inválida ou expirada. Crie uma nova chave em console.anthropic.com"
                elif e.code == 429:
                    erro_msg = "Limite de requisições excedido. Aguarde alguns minutos."
                elif e.code == 500:
                    erro_msg = "Erro interno da API Anthropic. Tente novamente."
                else:
                    erro_msg = f"Erro {e.code}: {error_message or error_body[:200]}"
            except:
                pass
            
            return jsonify({"erro": erro_msg}), 500
        
        print("[PLANTA-IA] Resposta recebida, processando...")
        
        # Extrair texto da resposta
        texto_resposta = result.get('content', [{}])[0].get('text', '')
        
        # Tentar parsear JSON
        try:
            # Limpar possíveis caracteres extras
            texto_limpo = texto_resposta.strip()
            if texto_limpo.startswith('```json'):
                texto_limpo = texto_limpo[7:]
            if texto_limpo.startswith('```'):
                texto_limpo = texto_limpo[3:]
            if texto_limpo.endswith('```'):
                texto_limpo = texto_limpo[:-3]
            texto_limpo = texto_limpo.strip()
            
            orcamento_gerado = json.loads(texto_limpo)
        except json.JSONDecodeError as e:
            print(f"[PLANTA-IA] Erro ao parsear JSON: {e}")
            print(f"[PLANTA-IA] Texto recebido: {texto_resposta[:500]}...")
            return jsonify({
                "erro": "Erro ao processar resposta da IA",
                "detalhes": str(e),
                "resposta_raw": texto_resposta[:1000]
            }), 500
        
        # Enriquecer com preços da base de serviços
        print("[PLANTA-IA] Enriquecendo com preços da base...")
        for etapa in orcamento_gerado.get('etapas', []):
            for item in etapa.get('itens', []):
                # Buscar serviço similar na base
                descricao = item.get('descricao', '')
                servico_base = ServicoBase.query.filter(
                    ServicoBase.descricao.ilike(f'%{descricao}%')
                ).first()
                
                if servico_base:
                    item['preco_mao_obra'] = servico_base.preco_mao_obra
                    item['preco_material'] = servico_base.preco_material
                    item['preco_unitario'] = servico_base.preco_unitario
                    item['tipo_composicao'] = servico_base.tipo_composicao
                    item['rateio_mo'] = servico_base.rateio_mo
                    item['rateio_mat'] = servico_base.rateio_mat
                    item['fonte_preco'] = 'base'
                else:
                    # Tentar busca mais flexível
                    palavras = descricao.split()[:2]  # Primeiras 2 palavras
                    if palavras:
                        servico_base = ServicoBase.query.filter(
                            ServicoBase.descricao.ilike(f'%{palavras[0]}%')
                        ).first()
                        if servico_base:
                            item['preco_mao_obra'] = servico_base.preco_mao_obra
                            item['preco_material'] = servico_base.preco_material
                            item['preco_unitario'] = servico_base.preco_unitario
                            item['tipo_composicao'] = servico_base.tipo_composicao
                            item['fonte_preco'] = 'base_aproximado'
                        else:
                            item['fonte_preco'] = 'nao_encontrado'
                            item['tipo_composicao'] = 'separado'
                    else:
                        item['fonte_preco'] = 'nao_encontrado'
                        item['tipo_composicao'] = 'separado'
        
        # Calcular totais
        total_geral = 0
        total_itens = 0
        for etapa in orcamento_gerado.get('etapas', []):
            etapa_total = 0
            for item in etapa.get('itens', []):
                qtd = item.get('quantidade', 0)
                if item.get('tipo_composicao') == 'composto' and item.get('preco_unitario'):
                    item_total = qtd * item.get('preco_unitario', 0)
                else:
                    mo = item.get('preco_mao_obra') or 0
                    mat = item.get('preco_material') or 0
                    item_total = qtd * (mo + mat)
                item['total_estimado'] = item_total
                etapa_total += item_total
                total_itens += 1
            etapa['total_etapa'] = etapa_total
            total_geral += etapa_total
        
        orcamento_gerado['resumo'] = {
            'total_geral': total_geral,
            'total_etapas': len(orcamento_gerado.get('etapas', [])),
            'total_itens': total_itens
        }
        
        print(f"[PLANTA-IA] Orçamento gerado: {total_itens} itens, total R$ {total_geral:,.2f}")
        
        return jsonify(orcamento_gerado)
        
    except urllib.error.URLError as e:
        print(f"[PLANTA-IA] Erro de conexão: {e}")
        if hasattr(e, 'reason') and 'timed out' in str(e.reason).lower():
            return jsonify({"erro": "Timeout ao processar imagem. Tente novamente."}), 504
        return jsonify({"erro": f"Erro de conexão: {e.reason}"}), 500
    except Exception as e:
        print(f"[PLANTA-IA] Erro: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@app.route('/obras/<int:obra_id>/orcamento-eng/importar-gerado', methods=['POST'])
@jwt_required()
def importar_orcamento_gerado(obra_id):
    """
    Importa o orçamento gerado pela IA para o banco de dados
    Recebe as etapas/itens selecionados pelo usuário após revisão
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if user.role != 'master' and obra not in user.obras:
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        etapas_importar = dados.get('etapas', [])
        criar_servicos = dados.get('criar_servicos', True)
        
        etapas_criadas = 0
        itens_criados = 0
        servicos_criados = 0
        
        for etapa_data in etapas_importar:
            # Verificar se etapa já existe
            etapa_existente = OrcamentoEngEtapa.query.filter_by(
                obra_id=obra_id,
                codigo=etapa_data.get('codigo')
            ).first()
            
            if etapa_existente:
                etapa = etapa_existente
            else:
                # Criar etapa
                max_ordem = db.session.query(db.func.max(OrcamentoEngEtapa.ordem)).filter_by(obra_id=obra_id).scalar() or 0
                etapa = OrcamentoEngEtapa(
                    obra_id=obra_id,
                    codigo=etapa_data.get('codigo'),
                    nome=etapa_data.get('nome', '').upper(),
                    ordem=max_ordem + 1
                )
                db.session.add(etapa)
                db.session.flush()
                etapas_criadas += 1
            
            # Criar itens
            for item_data in etapa_data.get('itens', []):
                if not item_data.get('selecionado', True):
                    continue
                
                # Calcular ordem do item
                max_ordem_item = db.session.query(db.func.max(OrcamentoEngItem.ordem)).filter_by(etapa_id=etapa.id).scalar() or 0
                
                item = OrcamentoEngItem(
                    etapa_id=etapa.id,
                    codigo=item_data.get('codigo'),
                    descricao=item_data.get('descricao'),
                    unidade=item_data.get('unidade'),
                    quantidade=item_data.get('quantidade', 0),
                    tipo_composicao=item_data.get('tipo_composicao', 'separado'),
                    preco_mao_obra=item_data.get('preco_mao_obra'),
                    preco_material=item_data.get('preco_material'),
                    preco_unitario=item_data.get('preco_unitario'),
                    rateio_mo=item_data.get('rateio_mo', 50),
                    rateio_mat=item_data.get('rateio_mat', 50),
                    ordem=max_ordem_item + 1
                )
                db.session.add(item)
                db.session.flush()
                itens_criados += 1
                
                # Criar serviço no Kanban se solicitado
                if criar_servicos and item_data.get('criar_servico', True):
                    totais = item.calcular_totais()
                    servico = Servico(
                        obra_id=obra_id,
                        nome=item_data.get('descricao'),
                        valor_global_mao_de_obra=totais['total_mao_obra'],
                        valor_global_material=totais['total_material']
                    )
                    db.session.add(servico)
                    db.session.flush()
                    item.servico_id = servico.id
                    servicos_criados += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": "Orçamento importado com sucesso",
            "etapas_criadas": etapas_criadas,
            "itens_criados": itens_criados,
            "servicos_criados": servicos_criados
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"[IMPORTAR-ORC] Erro: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


# ==============================================================================
# INICIALIZAÇÃO DO SERVIDOR (DEVE SER A ÚLTIMA COISA DO ARQUIVO)
# ==============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"--- [LOG] Iniciando servidor Flask na porta {port} ---")
    app.run(host='0.0.0.0', port=port, debug=True)
