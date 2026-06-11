"""Auto-migration para o banco admin (admin_* tables).

Idempotente: usa IF NOT EXISTS. Seguro em todo cold start.
Chamado por create_app() em app_admin.py.
"""
import logging
import os

logger = logging.getLogger(__name__)


def run_auto_migration_admin():
    """Aplica migrations e indexes no banco admin."""
    url = os.environ.get('DATABASE_URL_ADMIN', '')
    if not url or url.startswith('sqlite'):
        logger.info("auto_migration_admin: SQLite ou sem DATABASE_URL_ADMIN — pulando")
        return

    try:
        import psycopg2
        from urllib.parse import urlparse

        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)

        conn = psycopg2.connect(url)
        cur = conn.cursor()

        # Fase 5-D: Performance indexes (admin tables)
        perf_indexes = [
            ("idx_perf_admin_lancamento_imovel_id",   "admin_lancamento(imovel_id)"),
            ("idx_perf_admin_lancamento_data",         "admin_lancamento(data_lancamento)"),
            ("idx_perf_admin_lancamento_status",       "admin_lancamento(status)"),
            ("idx_perf_admin_boleto_imovel_id",        "admin_boleto(imovel_id)"),
            ("idx_perf_admin_boleto_vencimento",       "admin_boleto(data_vencimento)"),
        ]
        for idx_name, idx_def in perf_indexes:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def};")
        logger.info(f"auto_migration_admin: {len(perf_indexes)} indexes aplicados (IF NOT EXISTS)")

        # SUPERLINK admin — tabela de links de pagamento compartilháveis
        # itens JSONB: [{descricao, valor, contexto, forma, pix_chave, codigo_barras}]
        cur.execute("SELECT to_regclass('public.superlink');")
        if not cur.fetchone()[0]:
            logger.info("auto_migration_admin: criando tabela superlink...")
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
            logger.info("auto_migration_admin: tabela superlink criada")
        else:
            logger.info("auto_migration_admin: superlink já existe")

        # Coluna orcamento_item_id em admin_boleto (aditiva, idempotente)
        cur.execute("ALTER TABLE admin_boleto ADD COLUMN IF NOT EXISTS orcamento_item_id INTEGER;")
        logger.info("auto_migration_admin: orcamento_item_id em admin_boleto garantida")

        # Coluna refs: [{tabela, id}] para query ao vivo (aditiva, idempotente)
        cur.execute("ALTER TABLE superlink ADD COLUMN IF NOT EXISTS refs JSONB;")
        logger.info("auto_migration_admin: refs garantida em superlink")

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        logger.exception(f"auto_migration_admin: erro — {e}")
