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

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        logger.exception(f"auto_migration_admin: erro — {e}")
