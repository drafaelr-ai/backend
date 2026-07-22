"""Startup auto-migration — runs once on container boot to ensure schema is up-to-date.

All operations are idempotent (IF NOT EXISTS / column checks). Safe to run on every
cold start. No models or Flask app required — uses psycopg2 directly.
"""
import os
import logging
import traceback

from config import _build_database_url

logger = logging.getLogger(__name__)


def run_auto_migration():
    """Executa migration automaticamente no startup"""
    logger.info("=" * 70)
    logger.info("🔧 AUTO-MIGRATION: Corrigindo estrutura do banco...")
    logger.info("=" * 70)

    try:
        import psycopg2

        db_password = os.environ.get('DB_PASSWORD')
        if not db_password:
            logger.warning("⚠️ DB_PASSWORD não encontrada, pulando migration")
            return

        url = _build_database_url()

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

        # 2.7 Adicionar coluna arquivada na tabela obra
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'obra' AND column_name = 'arquivada';")
        if not cur.fetchone():
            cur.execute("ALTER TABLE obra ADD COLUMN arquivada BOOLEAN NOT NULL DEFAULT FALSE;")
            logger.info("✅ Coluna arquivada adicionada em obra")

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

        # Fase 5-D: Performance indexes (idempotent — IF NOT EXISTS)
        logger.debug("Adicionando indexes de performance Fase 5-D...")
        perf_indexes = [
            ("idx_perf_lancamento_obra_id",          "lancamento(obra_id)"),
            ("idx_perf_lancamento_data",              "lancamento(data)"),
            ("idx_perf_lancamento_status",            "lancamento(status)"),
            ("idx_perf_pagamento_futuro_obra_id",     "pagamento_futuro(obra_id)"),
            ("idx_perf_pagamento_futuro_status",      "pagamento_futuro(status)"),
            ("idx_perf_pagamento_servico_servico_id", "pagamento_servico(servico_id)"),
            ("idx_perf_parcela_individual_pag_id",   "parcela_individual(pagamento_parcelado_id)"),
            ("idx_perf_parcela_individual_status",   "parcela_individual(status)"),
            ("idx_perf_movimentacao_obra_id",         "movimentacao_caixa(obra_id)"),
        ]
        idx_ok = 0
        for idx_name, idx_def in perf_indexes:
            # Falha por-indice e isolada aqui via SAVEPOINT/ROLLBACK e nunca
            # propaga pro except de nivel de funcao (raise no fim de
            # run_auto_migration): um indice com coluna inexistente (ex.:
            # idx_perf_movimentacao_obra_id, coluna removida/renomeada) so
            # loga um warning e segue, NAO derruba o boot.
            try:
                cur.execute("SAVEPOINT before_idx")
                cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def};")
                cur.execute("RELEASE SAVEPOINT before_idx")
                idx_ok += 1
            except Exception as idx_err:
                cur.execute("ROLLBACK TO SAVEPOINT before_idx")
                cur.execute("RELEASE SAVEPOINT before_idx")
                logger.warning("   ⚠️ Index %s ignorado: %s", idx_name, idx_err)
        logger.info(f"Indexes Fase 5-D: {idx_ok}/{len(perf_indexes)} aplicados (IF NOT EXISTS)")

        # =================================================================
        # SUPERLINK — tabela de links de pagamento compartilháveis
        # itens JSONB: [{descricao, valor, contexto, forma, pix_chave, codigo_barras}]
        # =================================================================
        cur.execute("SELECT to_regclass('public.superlink');")
        if not cur.fetchone()[0]:
            logger.info("📝 Criando tabela superlink...")
            cur.execute("""
                CREATE TABLE superlink (
                    id          SERIAL PRIMARY KEY,
                    token       VARCHAR(64) NOT NULL UNIQUE,
                    grupo_id    INTEGER,
                    titulo      VARCHAR(255) NOT NULL,
                    itens       JSONB NOT NULL,
                    valor_total DOUBLE PRECISION NOT NULL DEFAULT 0,
                    criado_em   TIMESTAMP NOT NULL DEFAULT NOW(),
                    expira_em   TIMESTAMP NOT NULL
                );
                CREATE INDEX idx_superlink_token ON superlink (token);
            """)
            logger.info("✅ Tabela superlink criada!")
        else:
            logger.info("   ℹ️ Tabela superlink já existe")

        # Coluna refs: [{tabela, id}] para query ao vivo (aditiva, idempotente)
        cur.execute("ALTER TABLE superlink ADD COLUMN IF NOT EXISTS refs JSONB;")
        logger.info("auto_migration: refs garantida em superlink")

        # Vínculo orçamento em pagamento_servico (aditiva, idempotente).
        # As demais tabelas (lancamento, pagamento_futuro, boleto, pagamento_parcelado_v2)
        # já têm a coluna; só pagamento_servico faltava.
        cur.execute("ALTER TABLE pagamento_servico ADD COLUMN IF NOT EXISTS orcamento_item_id INTEGER;")
        logger.info("auto_migration: orcamento_item_id garantida em pagamento_servico")

        # RH: estado da obra (UF) — origem do piso da CCT. Aditivo, idempotente,
        # nullable (dado existente intacto).
        cur.execute("ALTER TABLE obra ADD COLUMN IF NOT EXISTS uf VARCHAR(2);")
        logger.info("auto_migration: coluna uf garantida em obra (RH)")

        # =================================================================
        # MÓDULO PESSOAL / RH — 6 tabelas (aditivo, idempotente)
        # Ordem de dependência: categoria_mo → convencao_coletiva →
        # convencao_valor → funcionario → pagamento_salario → encargo.
        # FKs para obra.id usam ON DELETE SET NULL (não apaga RH ao remover obra).
        # Nenhuma tabela existente é alterada.
        # =================================================================
        logger.info("📝 RH: garantindo tabelas do módulo Pessoal/RH...")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS categoria_mo (
                id        SERIAL PRIMARY KEY,
                nome      VARCHAR(80) NOT NULL,
                descricao VARCHAR(200)
            );
            CREATE INDEX IF NOT EXISTS idx_categoria_mo_nome ON categoria_mo (nome);
        """)

        # Índice único (case-insensitive) em categoria_mo.nome — evita categorias
        # duplicadas criadas por confirmações de CCT concorrentes (RH-fix).
        # Guardado: só cria se não houver duplicatas hoje (não apaga/mescla
        # dado existente); se houver, apenas loga um aviso p/ limpeza manual.
        cur.execute("""
            SELECT lower(nome) FROM categoria_mo
            GROUP BY lower(nome) HAVING COUNT(*) > 1;
        """)
        dupes_categoria = cur.fetchall()
        if dupes_categoria:
            logger.warning(
                "⚠️ RH: categoria_mo tem nomes duplicados (case-insensitive) — "
                "pulando criação do índice único até limpeza manual: %s",
                [d[0] for d in dupes_categoria],
            )
        else:
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_categoria_mo_nome_unique
                ON categoria_mo (lower(nome));
            """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS convencao_coletiva (
                id              SERIAL PRIMARY KEY,
                uf              VARCHAR(2) NOT NULL,
                sindicato       VARCHAR(160),
                vigencia_inicio DATE NOT NULL,
                vigencia_fim    DATE NOT NULL,
                arquivo_url     VARCHAR(500),
                status          VARCHAR(20) NOT NULL DEFAULT 'rascunho',
                data_upload     TIMESTAMP DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS convencao_valor (
                id            SERIAL PRIMARY KEY,
                convencao_id  INTEGER NOT NULL REFERENCES convencao_coletiva(id) ON DELETE CASCADE,
                categoria_id  INTEGER NOT NULL REFERENCES categoria_mo(id),
                piso_salarial NUMERIC(12,2) NOT NULL,
                beneficios    JSONB DEFAULT '[]'::jsonb
            );
            CREATE INDEX IF NOT EXISTS idx_convencao_valor_convencao ON convencao_valor (convencao_id);
            CREATE INDEX IF NOT EXISTS idx_convencao_valor_categoria ON convencao_valor (categoria_id);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS funcionario (
                id            SERIAL PRIMARY KEY,
                nome          VARCHAR(160) NOT NULL,
                cpf           VARCHAR(14),
                categoria_id  INTEGER NOT NULL REFERENCES categoria_mo(id),
                obra_id       INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                salario       NUMERIC(12,2) NOT NULL,
                data_admissao DATE,
                data_demissao DATE,
                status        VARCHAR(20) NOT NULL DEFAULT 'ativo',
                data_criacao  TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_funcionario_cpf ON funcionario (cpf);
            CREATE INDEX IF NOT EXISTS idx_funcionario_obra ON funcionario (obra_id);
            CREATE INDEX IF NOT EXISTS idx_funcionario_categoria ON funcionario (categoria_id);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pagamento_salario (
                id              SERIAL PRIMARY KEY,
                funcionario_id  INTEGER NOT NULL REFERENCES funcionario(id),
                competencia     VARCHAR(7) NOT NULL,
                tipo            VARCHAR(20) NOT NULL,
                valor           NUMERIC(12,2) NOT NULL,
                data_pagamento  DATE NOT NULL,
                obra_id         INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                comprovante_url VARCHAR(500),
                observacao      VARCHAR(300),
                data_criacao    TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_pag_salario_competencia ON pagamento_salario (competencia);
            CREATE INDEX IF NOT EXISTS idx_pag_salario_funcionario ON pagamento_salario (funcionario_id);
            CREATE INDEX IF NOT EXISTS idx_pag_salario_obra ON pagamento_salario (obra_id);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS encargo (
                id             SERIAL PRIMARY KEY,
                tipo           VARCHAR(20) NOT NULL,
                competencia    VARCHAR(7) NOT NULL,
                vencimento     DATE,
                data_pagamento DATE,
                valor          NUMERIC(12,2) NOT NULL,
                arquivo_url    VARCHAR(500),
                obra_id        INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                funcionario_id INTEGER REFERENCES funcionario(id) ON DELETE SET NULL,
                observacao     VARCHAR(300),
                data_criacao   TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_encargo_competencia ON encargo (competencia);
            CREATE INDEX IF NOT EXISTS idx_encargo_tipo ON encargo (tipo);
            CREATE INDEX IF NOT EXISTS idx_encargo_obra ON encargo (obra_id);
        """)

        logger.info("✅ RH: 6 tabelas garantidas (categoria_mo, convencao_coletiva, "
                    "convencao_valor, funcionario, pagamento_salario, encargo)")

        # =================================================================
        # PONTO ELETRÔNICO (dentro de RH)
        # As batidas são preservadas em tabela própria; a folha é calculada a
        # partir delas e da jornada por funcionário. A referência externa
        # permite receber NSR do REP de forma idempotente.
        # =================================================================
        logger.info("📝 PONTO: garantindo jornada e marcações eletrônicas...")
        cur.execute("ALTER TABLE funcionario ADD COLUMN IF NOT EXISTS carga_horaria_diaria NUMERIC(5,2) NOT NULL DEFAULT 8;")
        cur.execute("ALTER TABLE funcionario ADD COLUMN IF NOT EXISTS horario_entrada TIME;")
        cur.execute("ALTER TABLE funcionario ADD COLUMN IF NOT EXISTS intervalo_minutos INTEGER NOT NULL DEFAULT 60;")
        cur.execute("ALTER TABLE funcionario ADD COLUMN IF NOT EXISTS dias_trabalho JSONB DEFAULT '[0,1,2,3,4]'::jsonb;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ponto_marcacao (
                id                  SERIAL PRIMARY KEY,
                funcionario_id      INTEGER NOT NULL REFERENCES funcionario(id) ON DELETE CASCADE,
                data_hora           TIMESTAMP NOT NULL,
                tipo                VARCHAR(30) NOT NULL DEFAULT 'entrada',
                origem              VARCHAR(20) NOT NULL DEFAULT 'manual',
                referencia_externa  VARCHAR(120),
                observacao          VARCHAR(300),
                registrada_por_id   INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
                data_criacao        TIMESTAMP DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ponto_marcacao_referencia
                ON ponto_marcacao (referencia_externa) WHERE referencia_externa IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_ponto_marcacao_funcionario_data
                ON ponto_marcacao (funcionario_id, data_hora);
        """)
        logger.info("✅ PONTO: jornada e tabela ponto_marcacao garantidas")

        # =================================================================
        # MÓDULO FROTA — 7 tabelas (aditivo, idempotente)
        # Ordem de dependência: frota_condutor → frota_veiculo →
        # frota_movimentacao → frota_documento → frota_manutencao →
        # frota_abastecimento → frota_multa.
        # FKs para obra(id)/funcionario(id): ON DELETE SET NULL.
        # Sub-recursos → frota_veiculo(id): ON DELETE CASCADE.
        # imovel_id é referência FRACA ao banco admin (admin_imovel) — sem FK.
        # Nenhuma tabela existente é alterada.
        # =================================================================
        logger.info("📝 FROTA: garantindo tabelas do módulo Frota...")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS frota_condutor (
                id             SERIAL PRIMARY KEY,
                nome           VARCHAR(160) NOT NULL,
                cpf            VARCHAR(14),
                telefone       VARCHAR(20),
                cnh_numero     VARCHAR(20),
                cnh_categoria  VARCHAR(5),
                cnh_validade   DATE,
                funcionario_id INTEGER REFERENCES funcionario(id) ON DELETE SET NULL,
                status         VARCHAR(20) NOT NULL DEFAULT 'ativo',
                observacao     VARCHAR(300),
                data_criacao   TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_frota_condutor_cpf ON frota_condutor (cpf);
            CREATE INDEX IF NOT EXISTS idx_frota_condutor_funcionario ON frota_condutor (funcionario_id);
            CREATE INDEX IF NOT EXISTS idx_frota_condutor_cnh_validade ON frota_condutor (cnh_validade);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS frota_veiculo (
                id                SERIAL PRIMARY KEY,
                placa             VARCHAR(10) NOT NULL,
                renavam           VARCHAR(20),
                chassi            VARCHAR(30),
                marca             VARCHAR(60),
                modelo            VARCHAR(80) NOT NULL,
                ano_fabricacao    INTEGER,
                ano_modelo        INTEGER,
                tipo              VARCHAR(30) NOT NULL DEFAULT 'carro',
                cor               VARCHAR(30),
                combustivel       VARCHAR(20),
                km_atual          INTEGER,
                status            VARCHAR(20) NOT NULL DEFAULT 'ativo',
                condutor_atual_id INTEGER REFERENCES frota_condutor(id) ON DELETE SET NULL,
                local_tipo        VARCHAR(10),
                obra_id           INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                imovel_id         INTEGER,
                imovel_nome       VARCHAR(160),
                observacao        VARCHAR(300),
                data_criacao      TIMESTAMP DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_frota_veiculo_placa ON frota_veiculo (upper(placa));
            CREATE INDEX IF NOT EXISTS idx_frota_veiculo_obra ON frota_veiculo (obra_id);
            CREATE INDEX IF NOT EXISTS idx_frota_veiculo_status ON frota_veiculo (status);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS frota_movimentacao (
                id                SERIAL PRIMARY KEY,
                veiculo_id        INTEGER NOT NULL REFERENCES frota_veiculo(id) ON DELETE CASCADE,
                destino_tipo      VARCHAR(10) NOT NULL,
                obra_id           INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                imovel_id         INTEGER,
                destino_nome      VARCHAR(160),
                data_movimentacao DATE NOT NULL,
                observacao        VARCHAR(300),
                data_criacao      TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_frota_mov_veiculo ON frota_movimentacao (veiculo_id);
            CREATE INDEX IF NOT EXISTS idx_frota_mov_obra ON frota_movimentacao (obra_id);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS frota_documento (
                id              SERIAL PRIMARY KEY,
                veiculo_id      INTEGER NOT NULL REFERENCES frota_veiculo(id) ON DELETE CASCADE,
                tipo            VARCHAR(20) NOT NULL,
                descricao       VARCHAR(160),
                data_vencimento DATE,
                valor           NUMERIC(12,2),
                arquivo_url     VARCHAR(500),
                observacao      VARCHAR(300),
                data_criacao    TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_frota_doc_veiculo ON frota_documento (veiculo_id);
            CREATE INDEX IF NOT EXISTS idx_frota_doc_vencimento ON frota_documento (data_vencimento);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS frota_manutencao (
                id           SERIAL PRIMARY KEY,
                veiculo_id   INTEGER NOT NULL REFERENCES frota_veiculo(id) ON DELETE CASCADE,
                tipo         VARCHAR(20) NOT NULL,
                descricao    VARCHAR(300),
                data         DATE NOT NULL,
                km           INTEGER,
                custo        NUMERIC(12,2) NOT NULL,
                oficina      VARCHAR(160),
                arquivo_url  VARCHAR(500),
                local_tipo   VARCHAR(10),
                obra_id      INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                imovel_id    INTEGER,
                local_nome   VARCHAR(160),
                observacao   VARCHAR(300),
                data_criacao TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_frota_manut_veiculo ON frota_manutencao (veiculo_id);
            CREATE INDEX IF NOT EXISTS idx_frota_manut_obra ON frota_manutencao (obra_id);
            CREATE INDEX IF NOT EXISTS idx_frota_manut_data ON frota_manutencao (data);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS frota_abastecimento (
                id           SERIAL PRIMARY KEY,
                veiculo_id   INTEGER NOT NULL REFERENCES frota_veiculo(id) ON DELETE CASCADE,
                data         DATE NOT NULL,
                litros       NUMERIC(10,2),
                valor        NUMERIC(12,2) NOT NULL,
                km           INTEGER,
                combustivel  VARCHAR(20),
                posto        VARCHAR(160),
                condutor_id  INTEGER REFERENCES frota_condutor(id) ON DELETE SET NULL,
                local_tipo   VARCHAR(10),
                obra_id      INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                imovel_id    INTEGER,
                local_nome   VARCHAR(160),
                observacao   VARCHAR(300),
                data_criacao TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_frota_abast_veiculo ON frota_abastecimento (veiculo_id);
            CREATE INDEX IF NOT EXISTS idx_frota_abast_obra ON frota_abastecimento (obra_id);
            CREATE INDEX IF NOT EXISTS idx_frota_abast_data ON frota_abastecimento (data);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS frota_multa (
                id               SERIAL PRIMARY KEY,
                veiculo_id       INTEGER NOT NULL REFERENCES frota_veiculo(id) ON DELETE CASCADE,
                data_infracao    DATE NOT NULL,
                descricao        VARCHAR(300),
                valor            NUMERIC(12,2) NOT NULL,
                pontos           INTEGER,
                condutor_id      INTEGER REFERENCES frota_condutor(id) ON DELETE SET NULL,
                status_pagamento VARCHAR(20) NOT NULL DEFAULT 'pendente',
                data_pagamento   DATE,
                arquivo_url      VARCHAR(500),
                observacao       VARCHAR(300),
                data_criacao     TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_frota_multa_veiculo ON frota_multa (veiculo_id);
            CREATE INDEX IF NOT EXISTS idx_frota_multa_status ON frota_multa (status_pagamento);
        """)

        logger.info("✅ FROTA: 7 tabelas garantidas (frota_condutor, frota_veiculo, "
                    "frota_movimentacao, frota_documento, frota_manutencao, "
                    "frota_abastecimento, frota_multa)")

        # =================================================================
        # MÓDULO SOLICITAÇÕES (compras) — 4 tabelas (aditivo, idempotente)
        # Ordem: solicitacao_compra → solicitacao_item / solicitacao_cotacao
        # → solicitacao_config (singleton id=1, criada sob demanda pela API).
        # obra_id: ON DELETE CASCADE (Obra não tem relationship ORM p/ cá —
        # o CASCADE no banco evita quebrar a exclusão de obra).
        # cotacao_aprovada_id / pagamento_futuro_id / aprovador_id são
        # referências FRACAS (sem FK) — nenhuma tabela existente é alterada.
        # =================================================================
        logger.info("📝 SOLICITAÇÕES: garantindo tabelas do módulo Solicitações...")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS solicitacao_compra (
                id                  SERIAL PRIMARY KEY,
                obra_id             INTEGER NOT NULL REFERENCES obra(id) ON DELETE CASCADE,
                solicitante_id      INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
                data_criacao        TIMESTAMP DEFAULT NOW(),
                data_necessidade    DATE,
                tipo                VARCHAR(30) NOT NULL DEFAULT 'Material',
                status              VARCHAR(30) NOT NULL DEFAULT 'Aberta',
                observacao          TEXT,
                token_publico       VARCHAR(64) NOT NULL,
                cotacao_aprovada_id INTEGER,
                pagamento_futuro_id INTEGER,
                aprovador_id        INTEGER,
                data_decisao        TIMESTAMP,
                motivo_rejeicao     VARCHAR(300)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_solicitacao_token ON solicitacao_compra (token_publico);
            CREATE INDEX IF NOT EXISTS idx_solicitacao_obra ON solicitacao_compra (obra_id);
            CREATE INDEX IF NOT EXISTS idx_solicitacao_status ON solicitacao_compra (status);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS solicitacao_item (
                id             SERIAL PRIMARY KEY,
                solicitacao_id INTEGER NOT NULL REFERENCES solicitacao_compra(id) ON DELETE CASCADE,
                descricao      VARCHAR(300) NOT NULL,
                quantidade     NUMERIC(12,2) NOT NULL,
                unidade        VARCHAR(20),
                observacao     VARCHAR(300)
            );
            CREATE INDEX IF NOT EXISTS idx_solicitacao_item_sol ON solicitacao_item (solicitacao_id);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS solicitacao_cotacao (
                id                 SERIAL PRIMARY KEY,
                solicitacao_id     INTEGER NOT NULL REFERENCES solicitacao_compra(id) ON DELETE CASCADE,
                fornecedor         VARCHAR(150) NOT NULL,
                valor_total        NUMERIC(12,2) NOT NULL,
                condicao_pagamento VARCHAR(200),
                prazo_entrega      VARCHAR(100),
                observacao         VARCHAR(300),
                arquivo_url        VARCHAR(500),
                criado_por_id      INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
                data_criacao       TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_solicitacao_cot_sol ON solicitacao_cotacao (solicitacao_id);
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS solicitacao_config (
                id             INTEGER PRIMARY KEY,
                alertados_ids  JSONB,
                aprovadores_ids JSONB,
                limite_valor   DOUBLE PRECISION,
                atualizado_em  TIMESTAMP
            );
        """)

        logger.info("✅ SOLICITAÇÕES: 4 tabelas garantidas (solicitacao_compra, "
                    "solicitacao_item, solicitacao_cotacao, solicitacao_config)")

        # =================================================================
        # MÓDULO ALMOXARIFADO (externo) — catálogo e histórico de estoque.
        # Não há coluna de saldo: cada saldo é derivado das movimentações, o
        # que mantém entradas, entregas de EPI/fardamento e ajustes auditáveis.
        # =================================================================
        logger.info("📝 ALMOXARIFADO: garantindo catálogo e movimentações...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS almoxarifado_item (
                id              SERIAL PRIMARY KEY,
                codigo          VARCHAR(60),
                nome            VARCHAR(160) NOT NULL,
                categoria       VARCHAR(30) NOT NULL DEFAULT 'outro',
                unidade         VARCHAR(20) NOT NULL DEFAULT 'un',
                tamanho         VARCHAR(30),
                estoque_minimo  NUMERIC(12,2) NOT NULL DEFAULT 0,
                descricao       TEXT,
                ativo           BOOLEAN NOT NULL DEFAULT TRUE,
                data_criacao    TIMESTAMP DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_almoxarifado_item_codigo
                ON almoxarifado_item (codigo) WHERE codigo IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_almoxarifado_item_nome ON almoxarifado_item (nome);
            CREATE INDEX IF NOT EXISTS idx_almoxarifado_item_ativo ON almoxarifado_item (ativo);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS almoxarifado_movimentacao (
                id                  SERIAL PRIMARY KEY,
                item_id             INTEGER NOT NULL REFERENCES almoxarifado_item(id) ON DELETE CASCADE,
                tipo                VARCHAR(20) NOT NULL,
                quantidade          NUMERIC(12,2) NOT NULL,
                data_movimentacao   DATE NOT NULL,
                funcionario_id      INTEGER REFERENCES funcionario(id) ON DELETE SET NULL,
                obra_id             INTEGER REFERENCES obra(id) ON DELETE SET NULL,
                usuario_id          INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
                observacao          VARCHAR(300),
                data_criacao        TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_almoxarifado_mov_item_data
                ON almoxarifado_movimentacao (item_id, data_movimentacao);
            CREATE INDEX IF NOT EXISTS idx_almoxarifado_mov_funcionario
                ON almoxarifado_movimentacao (funcionario_id);
            CREATE INDEX IF NOT EXISTS idx_almoxarifado_mov_obra
                ON almoxarifado_movimentacao (obra_id);
        """)
        logger.info("✅ ALMOXARIFADO: tabelas almoxarifado_item e movimentacao garantidas")

        # =================================================================
        # ACESSOS POR MÓDULO (aditivo, idempotente)
        # NULL = todos os módulos (comportamento anterior preservado).
        # Invariante: o usuário id=1 (Diego, ex-admin_principal) é o ÚNICO
        # master do sistema — qualquer outro master é rebaixado a administrador
        # em todo boot. Amarrado ao id (não ao username) p/ sobreviver a renomes.
        # =================================================================
        # Boletos parcelados: código de barras por parcela (aditivo, idempotente).
        cur.execute("ALTER TABLE parcela_individual ADD COLUMN IF NOT EXISTS codigo_barras VARCHAR(60);")
        logger.info("auto_migration: codigo_barras garantida em parcela_individual")

        cur.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS modulos_permitidos JSONB;')
        cur.execute("""
            UPDATE "user" SET role='administrador'
            WHERE role='master' AND id <> 1;
        """)
        if cur.rowcount:
            logger.warning("⚠️ ACESSOS: %s usuário(s) master rebaixado(s) a administrador "
                           "(único master é o usuário id=1)", cur.rowcount)
        logger.info("✅ ACESSOS: coluna modulos_permitidos garantida em user")

        conn.commit()
        cur.close()
        conn.close()
        logger.info("🎉 AUTO-MIGRATION CONCLUÍDA!")

    except Exception as e:
        # Nem toda falha conhecida chega aqui: o loop de indices de perf
        # (SAVEPOINT/ROLLBACK por indice, acima) e o dedup de categoria_mo
        # em _resolver_categoria (routes/rh.py) ja isolam seus proprios
        # erros esperados localmente. Este raise cobre falhas genuinamente
        # inesperadas (schema quebrado, conexao caiu no meio, etc.) e
        # propositalmente derruba o boot em vez de mascarar o problema.
        logger.exception(f"❌ Erro na auto-migration: {e}")
        traceback.print_exc()
        raise
