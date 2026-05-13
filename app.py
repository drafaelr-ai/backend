# ============================================================================
# VERSÃO CORRIGIDA - 18/NOV/2025 - SEM COLUNA SEGMENTO
# Esta versãof REMOVE a definição de coluna segmento dos modelos
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
from urllib.parse import quote_plus
from sqlalchemy import func, case
import io
import base64
import csv
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from sqlalchemy.orm import joinedload 
from datetime import datetime, date, timedelta
# Imports de Autenticação
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import (create_access_token,
                                jwt_required, get_jwt_identity,
                                verify_jwt_in_request, get_jwt)
from functools import wraps
import logging
from logging_setup import setup_logging
from models.servico_base import ServicoBase  # noqa: F401
from models.user import User, user_obra_association  # noqa: F401
from models.obra import Obra  # noqa: F401
from models.servico import Servico  # noqa: F401
from models.notificacao import Notificacao  # noqa: F401
from models.pagamento_servico import PagamentoServico  # noqa: F401
from models.lancamento import Lancamento  # noqa: F401
from models.orcamento import Orcamento  # noqa: F401
from models.nota_fiscal import NotaFiscal  # noqa: F401
from models.diario_obra import DiarioObra  # noqa: F401
from models.diario_imagem import DiarioImagem  # noqa: F401
from models.anexo_orcamento import AnexoOrcamento  # noqa: F401
from models.caixa_obra import CaixaObra  # noqa: F401
from models.servico_usuario import ServicoUsuario  # noqa: F401
from models.orcamento_eng_etapa import OrcamentoEngEtapa  # noqa: F401
from models.orcamento_eng_item import OrcamentoEngItem  # noqa: F401
from models.movimentacao_caixa import MovimentacaoCaixa  # noqa: F401
from models.fechamento_caixa import FechamentoCaixa  # noqa: F401
from models.pagamento_futuro import PagamentoFuturo  # noqa: F401
from models.boleto import Boleto  # noqa: F401
from models.parcela_individual import ParcelaIndividual  # noqa: F401
from models.pagamento_parcelado import PagamentoParcelado  # noqa: F401
from models.cronograma_etapa import CronogramaEtapa  # noqa: F401
from models.cronograma_obra import CronogramaObra  # noqa: F401
from models.agenda_demanda import AgendaDemanda  # noqa: F401

from extensions import db, jwt, cors, limiter
from config import Config
from utils import formatar_real
from routes import notificacoes_bp, bi_bp, diario_bp, auth_bp, admin_bp, sid_bp, caixa_bp, servicos_bp, boletos_bp, lancamentos_bp, cronograma_bp, orcamento_eng_bp, obras_bp
from services import (
    criar_notificacao,
    notificar_masters,
    notificar_operadores_obra,
    notificar_administradores,
    get_current_user,
    user_has_access_to_obra,
    check_permission,
)

setup_logging()
logger = logging.getLogger(__name__)
logger.info("--- [LOG] Iniciando app.py (VERSÃO COM DEBUG COMPLETO - KPIs v4) ---")

# Constante de módulo — usada por apply_cors_headers e por create_app
ALLOWED_ORIGINS = [
    'https://obraly.uk',
    'https://www.obraly.uk',
    'http://localhost:3000',
    'http://localhost:3001',
]

def apply_cors_headers(response):
    """Camada 2 CORS — garante headers em toda resposta, independente do flask-cors."""
    origin = request.headers.get('Origin', '')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # JWT secret (lido do env — não é atributo estático de Config)
    jwt_secret = os.environ.get('JWT_SECRET_KEY')
    if not jwt_secret:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable is required. "
            "Defina-a no .env (dev) ou no provedor de deploy (prod)."
        )
    app.config['JWT_SECRET_KEY'] = jwt_secret
    logger.info("JWT configurado: access=7d")

    # DB password (lido do env)
    logger.info("--- [LOG] Lendo variável de ambiente DB_PASSWORD... ---")
    db_password = os.environ.get('DB_PASSWORD')
    if not db_password:
        logger.error("--- [ERRO CRÍTICO] Variável de ambiente DB_PASSWORD não foi encontrada! ---")
        raise ValueError("Variável de ambiente DB_PASSWORD não definida.")
    logger.info("--- [LOG] Variável DB_PASSWORD carregada com sucesso. ---")

    encoded_password = quote_plus(db_password)
    _DB_USER = "postgres.kwmuiviyqjcxawuiqkrl"
    _DB_HOST = "aws-1-sa-east-1.pooler.supabase.com"
    _DB_PORT = "6543"
    _DB_NAME = "postgres"
    database_url = f"postgresql://{_DB_USER}:{encoded_password}@{_DB_HOST}:{_DB_PORT}/{_DB_NAME}?sslmode=require"
    logger.info(f"--- [LOG] String de conexão criada para usuário {_DB_USER} (com sslmode=require) ---")
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url

    # Init extensions
    db.init_app(app)
    logger.info("--- [LOG] SQLAlchemy inicializado ---")
    jwt.init_app(app)
    limiter.init_app(app)

    # === CAMADA 1 — flask-cors ===
    cors.init_app(app, resources={r'/*': {'origins': ALLOWED_ORIGINS}}, supports_credentials=False)
    logger.info(f"CORS configurado para origens: {ALLOWED_ORIGINS}")

    # === CAMADA 2 — after_request ===
    app.after_request(apply_cors_headers)

    # Teardown de sessão do banco
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()
    logger.info("--- [LOG] Teardown de sessão configurado ---")

    # --- Blueprints ---
    app.register_blueprint(notificacoes_bp)
    app.register_blueprint(bi_bp)
    app.register_blueprint(diario_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(sid_bp)
    app.register_blueprint(caixa_bp)
    app.register_blueprint(servicos_bp)
    app.register_blueprint(boletos_bp)
    app.register_blueprint(lancamentos_bp)
    app.register_blueprint(cronograma_bp)
    app.register_blueprint(orcamento_eng_bp)
    app.register_blueprint(obras_bp)

    return app


def run_auto_migration():
    """Executa migration automaticamente no startup"""
    logger.info("=" * 70)
    logger.info("🔧 AUTO-MIGRATION: Corrigindo estrutura do banco...")
    logger.info("=" * 70)
    
    try:
        import psycopg2
        from urllib.parse import quote_plus
        
        db_password = os.environ.get('DB_PASSWORD')
        if not db_password:
            logger.warning("⚠️ DB_PASSWORD não encontrada, pulando migration")
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
            logger.info("✅ Coluna servico_id adicionada")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pagamento_futuro' AND column_name = 'tipo';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE pagamento_futuro ADD COLUMN tipo VARCHAR(50);")
            logger.info("✅ Coluna tipo adicionada")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pagamento_futuro' AND column_name = 'codigo_barras';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE pagamento_futuro ADD COLUMN codigo_barras VARCHAR(100);")
            logger.info("✅ Coluna codigo_barras adicionada em pagamento_futuro")
        # 2. Verificar coluna segmento em pagamento_parcelado_v2
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pagamento_parcelado_v2' AND column_name = 'segmento';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE pagamento_parcelado_v2 ADD COLUMN segmento VARCHAR(50) DEFAULT 'Material';")
            logger.info("✅ Coluna segmento adicionada")
        
        # 2.5 NOVO: Adicionar campos de pagamento na tabela orcamento
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'orcamento' AND column_name = 'data_vencimento';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE orcamento ADD COLUMN data_vencimento DATE;")
            logger.info("✅ Coluna data_vencimento adicionada em orcamento")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'orcamento' AND column_name = 'numero_parcelas';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE orcamento ADD COLUMN numero_parcelas INTEGER DEFAULT 1;")
            logger.info("✅ Coluna numero_parcelas adicionada em orcamento")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'orcamento' AND column_name = 'periodicidade';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE orcamento ADD COLUMN periodicidade VARCHAR(20) DEFAULT 'Mensal';")
            logger.info("✅ Coluna periodicidade adicionada em orcamento")
        
        # 2.6 NOVO: Adicionar coluna concluida na tabela obra
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'obra' AND column_name = 'concluida';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE obra ADD COLUMN concluida BOOLEAN DEFAULT FALSE;")
            logger.info("✅ Coluna concluida adicionada em obra")
        
        # =================================================================
        # 3. CORREÇÃO DO ERRO DE FOREIGN KEY (CRÍTICO)
        # Verificar se a tabela parcela_individual existe E se a FK está correta
        # =================================================================
        logger.debug("🔄 Verificando tabela parcela_individual...")
        
        # Verificar se a tabela existe
        cur.execute("SELECT to_regclass('public.parcela_individual');")
        tabela_existe = cur.fetchone()[0]
        
        if not tabela_existe:
            # Tabela não existe, criar
            logger.info("📝 Criando tabela parcela_individual...")
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
            logger.info("✅ Tabela parcela_individual criada!")
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
                logger.info("✅ Tabela parcela_individual já existe com FK correta")
            else:
                logger.warning(f"⚠️ FK atual aponta para: {fk_table}")
                logger.warning("⚠️ NÃO vamos dropar a tabela para preservar dados")
                logger.warning("⚠️ Se houver problemas de FK, corrija manualmente")
        
        # 4. Alterar comprovante_url para TEXT (suportar base64 grande)
        logger.debug("🔄 Verificando coluna comprovante_url...")
        cur.execute("""
            SELECT data_type FROM information_schema.columns 
            WHERE table_name = 'movimentacao_caixa' AND column_name = 'comprovante_url';
        """)
        result = cur.fetchone()
        if result and result[0] != 'text':
            logger.info("📝 Alterando comprovante_url para TEXT...")
            cur.execute("ALTER TABLE movimentacao_caixa ALTER COLUMN comprovante_url TYPE TEXT;")
            logger.info("✅ Coluna comprovante_url alterada para TEXT!")
        
        # 5. Remover FK constraints problemáticas em criado_por (para permitir exclusão de usuários)
        logger.info("🔄 Removendo FK constraints em criado_por...")
        fk_constraints_to_drop = [
            ("diario_obra", "diario_obra_criado_por_fkey"),
            ("movimentacao_caixa", "movimentacao_caixa_criado_por_fkey"),
            ("fechamento_caixa", "fechamento_caixa_fechado_por_fkey"),
        ]
        for table, constraint in fk_constraints_to_drop:
            try:
                cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint};")
                logger.info(f"   ✅ Constraint {constraint} removida (ou não existia)")
            except Exception as e:
                logger.exception(f"   ⚠️ {constraint}: {str(e)[:50]}")
        
        # 6. Criar tabela de boletos (Gestão de Boletos)
        logger.debug("🔄 Verificando tabela boleto...")
        cur.execute("SELECT to_regclass('public.boleto');")
        if not cur.fetchone()[0]:
            logger.info("📝 Criando tabela boleto...")
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
            logger.info("✅ Tabela boleto criada!")
        else:
            logger.info("   ℹ️ Tabela boleto já existe")
        
        # =================================================================
        # MÓDULO ORÇAMENTO DE ENGENHARIA - NOVAS TABELAS E CAMPOS
        # =================================================================
        logger.debug("🔄 Verificando estrutura do módulo de Orçamento de Engenharia...")
        
        # 1. Adicionar campos bdi e area na tabela obra
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'obra' AND column_name = 'bdi';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE obra ADD COLUMN bdi FLOAT DEFAULT 0;")
            logger.info("✅ Coluna bdi adicionada em obra")
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'obra' AND column_name = 'area';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE obra ADD COLUMN area FLOAT;")
            logger.info("✅ Coluna area adicionada em obra")
        
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
            logger.info("✅ Tabela servico_base criada!")
        else:
            logger.info("   ℹ️ Tabela servico_base já existe")
        
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
            logger.info("✅ Tabela servico_usuario criada!")
        else:
            logger.info("   ℹ️ Tabela servico_usuario já existe")
        
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
            logger.info("✅ Tabela orcamento_eng_etapa criada!")
        else:
            logger.info("   ℹ️ Tabela orcamento_eng_etapa já existe")
        
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
            logger.info("✅ Tabela orcamento_eng_item criada!")
        else:
            logger.info("   ℹ️ Tabela orcamento_eng_item já existe")
        
        logger.info("✅ Módulo de Orçamento de Engenharia verificado!")
        
        # =================================================================
        # CAMPO CONCLUÍDO NO SERVIÇO
        # =================================================================
        logger.debug("🔄 Verificando campo concluido em servico...")
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'servico' AND column_name = 'concluido';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE servico ADD COLUMN concluido BOOLEAN DEFAULT FALSE;")
            logger.info("✅ Coluna concluido adicionada em servico")
        
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'servico' AND column_name = 'data_conclusao';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE servico ADD COLUMN data_conclusao DATE;")
            logger.info("✅ Coluna data_conclusao adicionada em servico")
        
        # =================================================================
        # MÓDULO AGENDA DE DEMANDAS - NOVA TABELA
        # =================================================================
        logger.debug("🔄 Verificando tabela agenda_demanda...")
        cur.execute("SELECT to_regclass('public.agenda_demanda');")
        if not cur.fetchone()[0]:
            logger.info("📝 Criando tabela agenda_demanda...")
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
            logger.info("✅ Tabela agenda_demanda criada!")
        else:
            logger.info("   ℹ️ Tabela agenda_demanda já existe")
            # Verificar e adicionar coluna horário se não existir
            cur.execute("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'agenda_demanda' AND column_name = 'horario'
            """)
            if not cur.fetchone():
                logger.info("   🔄 Adicionando coluna horário à tabela agenda_demanda...")
                cur.execute("ALTER TABLE agenda_demanda ADD COLUMN horario VARCHAR(10)")
                logger.info("   ✅ Coluna horário adicionada!")

        # =================================================================
        # MÓDULO DIÁRIO DE OBRAS - GARANTIR EXISTÊNCIA DAS TABELAS
        # =================================================================
        logger.debug("🔄 Verificando tabela diario_obra...")
        cur.execute("SELECT to_regclass('public.diario_obra');")
        if not cur.fetchone()[0]:
            logger.info("📝 Criando tabela diario_obra...")
            cur.execute("""
                CREATE TABLE diario_obra (
                    id SERIAL PRIMARY KEY,
                    obra_id INTEGER NOT NULL REFERENCES obra(id) ON DELETE CASCADE,
                    data DATE NOT NULL,
                    titulo VARCHAR(200) NOT NULL,
                    descricao TEXT,
                    clima VARCHAR(50),
                    temperatura VARCHAR(50),
                    equipe_presente TEXT,
                    atividades_realizadas TEXT,
                    materiais_utilizados TEXT,
                    equipamentos_utilizados TEXT,
                    observacoes TEXT,
                    criado_por INTEGER,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_diario_obra_obra ON diario_obra(obra_id);
                CREATE INDEX idx_diario_obra_data ON diario_obra(data);
            """)
            logger.info("✅ Tabela diario_obra criada!")
        else:
            logger.info("   ℹ️ Tabela diario_obra já existe")
            # Garantir colunas que podem estar ausentes em bases antigas
            colunas_diario = [
                ('clima', 'VARCHAR(50)'),
                ('temperatura', 'VARCHAR(50)'),
                ('equipe_presente', 'TEXT'),
                ('atividades_realizadas', 'TEXT'),
                ('materiais_utilizados', 'TEXT'),
                ('equipamentos_utilizados', 'TEXT'),
                ('observacoes', 'TEXT'),
                ('criado_por', 'INTEGER'),
                ('criado_em', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
                ('atualizado_em', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
            ]
            for col, tipo in colunas_diario:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='diario_obra' AND column_name=%s;",
                    (col,)
                )
                if not cur.fetchone():
                    cur.execute(f"ALTER TABLE diario_obra ADD COLUMN {col} {tipo};")
                    logger.info(f"   ✅ Coluna {col} adicionada em diario_obra")

        logger.debug("🔄 Verificando tabela diario_imagens...")
        cur.execute("SELECT to_regclass('public.diario_imagens');")
        if not cur.fetchone()[0]:
            logger.info("📝 Criando tabela diario_imagens...")
            cur.execute("""
                CREATE TABLE diario_imagens (
                    id SERIAL PRIMARY KEY,
                    diario_id INTEGER NOT NULL REFERENCES diario_obra(id) ON DELETE CASCADE,
                    arquivo_nome VARCHAR(255) NOT NULL,
                    arquivo_base64 TEXT NOT NULL,
                    legenda VARCHAR(500),
                    ordem INTEGER DEFAULT 0,
                    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_diario_imagens_diario ON diario_imagens(diario_id);
            """)
            logger.info("✅ Tabela diario_imagens criada!")
        else:
            logger.info("   ℹ️ Tabela diario_imagens já existe")

        conn.commit()
        cur.close()
        conn.close()
        logger.info("🎉 AUTO-MIGRATION CONCLUÍDA!")
        
    except Exception as e:
        logger.exception(f"❌ Erro na auto-migration: {e}")
        traceback.print_exc()

# Executar migration automaticamente
logger.info("\n--- [LOG] Executando auto-migration antes de iniciar o app ---")
run_auto_migration()
logger.info("--- [LOG] Auto-migration concluída, iniciando app.py ---\n")

app = create_app()


# === CAMADA 3 — OPTIONS routes (evita 404 em preflights; migra pra blueprints no sub-lote E) ===
@app.route('/<path:any_path>', methods=['OPTIONS'])
def global_options(any_path):
    return ('', 200)



if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"--- [LOG] Iniciando servidor Flask na porta {port} ---")
    app.run(host='0.0.0.0', port=port, debug=True)
