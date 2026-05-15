import os
from datetime import timedelta


class Config:
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=30)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }


class DevelopmentConfig(Config):
    DEBUG = True

    @classmethod
    def from_env(cls):
        obj = cls()
        obj.JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY_ADMIN', 'obraly-admin-secret-key-2026')
        url = os.environ.get('DATABASE_URL_ADMIN', 'sqlite:///obraly_admin.db')
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        obj.SQLALCHEMY_DATABASE_URI = url
        return obj


class ProductionConfig(Config):
    DEBUG = False

    @classmethod
    def from_env(cls):
        secret = os.environ.get('JWT_SECRET_KEY_ADMIN')
        if not secret:
            raise RuntimeError("JWT_SECRET_KEY_ADMIN environment variable is required.")
        url = os.environ.get('DATABASE_URL_ADMIN')
        if not url:
            raise ValueError("DATABASE_URL_ADMIN environment variable is not defined.")
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        obj = cls()
        obj.JWT_SECRET_KEY = secret
        obj.SQLALCHEMY_DATABASE_URI = url
        return obj


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': ProductionConfig,
}
