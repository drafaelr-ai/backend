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

    ALLOWED_ORIGINS = [
        'https://obraly.uk',
        'https://www.obraly.uk',
        'http://localhost:3000',
        'http://localhost:3001',
    ]

    @classmethod
    def init_app(cls, app):
        pass


class DevelopmentConfig(Config):
    DEBUG = True

    @classmethod
    def from_env(cls):
        obj = cls()
        obj.JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'dev-secret-change-me')
        obj.SQLALCHEMY_DATABASE_URI = _build_database_url()
        return obj


class ProductionConfig(Config):
    DEBUG = False

    @classmethod
    def from_env(cls):
        secret = os.environ.get('JWT_SECRET_KEY')
        if not secret:
            raise RuntimeError(
                "JWT_SECRET_KEY environment variable is required."
            )
        password = os.environ.get('DB_PASSWORD')
        if not password:
            raise ValueError("DB_PASSWORD environment variable is not defined.")

        obj = cls()
        obj.JWT_SECRET_KEY = secret
        obj.SQLALCHEMY_DATABASE_URI = _build_database_url()
        return obj


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': ProductionConfig,
}
