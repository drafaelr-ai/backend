"""
Script para criar todas as tabelas no banco de dados PostgreSQL
Execute este script UMA VEZ antes de iniciar o app.py

Como executar:
python create_tables.py
"""

import os
import sys
from urllib.parse import quote_plus

# Configuração do banco
DATABASE_URL = os.getenv("DATABASE_URL")

def normalize_db_url(url: str) -> str:
    """Corrige prefixo antigo e garante SSL"""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url

if DATABASE_URL:
    DATABASE_URL = normalize_db_url(DATABASE_URL)
else:
    # Alternativa: montar a URL a partir de variáveis individuais
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = quote_plus(os.getenv("DB_PASSWORD", ""))
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "postgres")
    DATABASE_URL = (
        f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?sslmode=require"
    )

print("=" * 60)
print("INICIALIZANDO BANCO DE DADOS")
print("=" * 60)
print(f"\n📦 Conectando em: {DATABASE_URL[:40]}...")

# Agora importa o app depois de configurar o DATABASE_URL
os.environ["DATABASE_URL"] = DATABASE_URL

try:
    from app import db, app
    
    with app.app_context():
        print("\n🔨 Criando tabelas...")
        db.create_all()
        print("✅ Tabelas criadas com sucesso!")
        
        # Lista as tabelas criadas
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        print(f"\n📋 Tabelas no banco ({len(tables)}):")
        for table in tables:
            print(f"   • {table}")
        
        print("\n" + "=" * 60)
        print("✅ BANCO PRONTO! Agora você pode rodar o app.py")
        print("=" * 60)
        
except Exception as e:
    print(f"\n❌ ERRO: {e}")
    print("\n💡 Dicas:")
    print("   1. Verifique se o DATABASE_URL está correto")
    print("   2. Verifique se o PostgreSQL está acessível")
    print("   3. Verifique as credenciais do banco")
    sys.exit(1)
