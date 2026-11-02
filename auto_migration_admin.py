"""Auto-migration para o banco admin (admin_* tables).

Idempotente: usa IF NOT EXISTS. Seguro em todo cold start.
Chamado por create_app() em app_admin.py.
"""
import logging
import os

logger = logging.getLogger(__name__)


# O Dockerfile.admin sobe gunicorn com --workers 2 e sem --preload, então
# run_auto_migration_admin() roda uma vez por worker, em paralelo. Mesmo com
# toda a DDL idempotente, dois CREATE TABLE IF NOT EXISTS concorrentes podem
# estourar "duplicate key value violates unique constraint
# pg_class_relname_nsp_index": o IF NOT EXISTS do perdedor não vê a tabela
# ainda não commitada do vencedor. Mesmo tratamento do auto_migration.py do
# app principal — id diferente só para deixar os dois rastreáveis em
# pg_locks; os bancos são separados.
_LOCK_MIGRATION_ADMIN = 728194056

# Abaixo do --wait-timeout 300 do `flyctl deploy`: estourar aqui derruba o
# deploy com health check vermelho, que é o resultado certo para uma
# migration travada.
_LOCK_ESPERA_ADMIN = '240s'


def run_auto_migration_admin():
    """Aplica migrations e indexes no banco admin."""
    url = os.environ.get('DATABASE_URL_ADMIN', '')
    if not url or url.startswith('sqlite'):
        logger.info("auto_migration_admin: SQLite ou sem DATABASE_URL_ADMIN — pulando")
        return

    conn = None
    try:
        import psycopg2
        from urllib.parse import urlparse

        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)

        conn = psycopg2.connect(url)
        cur = conn.cursor()

        # Serializa com o outro worker antes de qualquer DDL. O lock é de
        # sessão: se este processo morrer, o Postgres o devolve sozinho.
        cur.execute("SET statement_timeout = %s;", (_LOCK_ESPERA_ADMIN,))
        cur.execute("SELECT pg_advisory_lock(%s);", (_LOCK_MIGRATION_ADMIN,))
        logger.info("auto_migration_admin: lock de migration adquirido")

        cur.execute("SET statement_timeout = '900s';")

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
        # B-11: o PDF passa a viver no Storage; arquivo_pdf (base64) fica
        # pelos registros antigos, que seguem sendo servidos dela.
        cur.execute("ALTER TABLE admin_boleto ADD COLUMN IF NOT EXISTS arquivo_path VARCHAR(500);")
        logger.info("auto_migration_admin: orcamento_item_id em admin_boleto garantida")

        # F-08: `icone` passou a guardar classe do Tabler Icons
        # (ex. 'ti-building-community'), que não cabe em VARCHAR(10).
        # Alargar é aditivo; os emoji antigos continuam válidos e o front
        # traduz os dois formatos (src/utils/iconeCategoria.js).
        #
        # ALTER COLUMN TYPE não tem IF NOT EXISTS e pega ACCESS EXCLUSIVE,
        # então só é emitido quando a coluna existe e ainda está estreita —
        # assim o boot normal não toca na tabela.
        cur.execute("""
            SELECT character_maximum_length
              FROM information_schema.columns
             WHERE table_name = 'admin_categoria' AND column_name = 'icone';
        """)
        largura = cur.fetchone()
        if largura is None:
            logger.info("auto_migration_admin: admin_categoria.icone ainda não existe")
        elif largura[0] is not None and largura[0] < 40:
            cur.execute("ALTER TABLE admin_categoria ALTER COLUMN icone TYPE VARCHAR(40);")
            logger.info("auto_migration_admin: admin_categoria.icone alargada para VARCHAR(40)")
        else:
            logger.info("auto_migration_admin: admin_categoria.icone já cabe a classe Tabler")

        # Coluna refs: [{tabela, id}] para query ao vivo (aditiva, idempotente)
        cur.execute("ALTER TABLE superlink ADD COLUMN IF NOT EXISTS refs JSONB;")
        logger.info("auto_migration_admin: refs garantida em superlink")

        conn.commit()
        cur.close()

    except Exception as e:
        logger.exception(f"auto_migration_admin: erro — {e}")
    finally:
        # Fechar a conexão devolve o advisory lock. Importa no caminho de
        # erro: sem isto o outro worker esperaria os 240s inteiros por um
        # lock que já não tem dono útil.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("auto_migration_admin: conexão já estava fechada")
