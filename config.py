import os
from datetime import timedelta
from urllib.parse import quote_plus

# Hardcoded DB connection params (Supabase pooler — transaction mode)
_DB_USER = "postgres.kwmuiviyqjcxawuiqkrl"
_DB_HOST = "aws-1-sa-east-1.pooler.supabase.com"
_DB_PORT = "6543"
_DB_NAME = "postgres"


def _build_database_url():
    password = os.environ.get('DB_PASSWORD', '')
    return (
        f"postgresql://{_DB_USER}:{quote_plus(password)}"
        f"@{_DB_HOST}:{_DB_PORT}/{_DB_NAME}?sslmode=require"
    )


class Config:
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
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
            'keepalives_count': 5,
        },
    }

    @classmethod
    def init_app(cls, app):
        pass

# Nota: app.py NÃO usa from_object + from_env() de subclasses — ele monta
# JWT_SECRET_KEY/SQLALCHEMY_DATABASE_URI manualmente em create_app() usando
# os helpers acima (_build_database_url) para validar env vars com suas
# próprias mensagens de erro. Subclasses DevelopmentConfig/ProductionConfig
# com from_env() foram removidas daqui por serem código morto (nunca
# instanciadas) — ver padrão equivalente, e efetivamente usado, em
# config_admin.py para o app admin.
