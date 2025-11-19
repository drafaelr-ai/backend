#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MIGRATION: Adicionar campos servico_id e tipo na tabela pagamento_futuro
Data: 19/11/2025
Executar via: railway run python migrate.py
"""

import psycopg2
import os
from urllib.parse import urlparse

def get_database_url():
    """Pega a connection string do ambiente"""
    # Tenta pegar do formato usado no app.py
    db_password = os.environ.get('DB_PASSWORD')
    
    if db_password:
        # Formato do app.py
        from urllib.parse import quote_plus
        encoded_password = quote_plus(db_password)
        return f"postgresql://postgres.kwmuiviyqjcxawuiqkrl:{encoded_password}@aws-1-sa-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
    
    # Fallback para DATABASE_URL direta
    return os.environ.get('DATABASE_URL')

def run_migration():
    """Executa a migration"""
    print("=" * 70)
    print("🔧 MIGRATION: Adicionar servico_id e tipo em pagamento_futuro")
    print("=" * 70)
    
    database_url = get_database_url()
    
    if not database_url:
        print("❌ ERRO: DATABASE_URL não encontrada!")
        print("Certifique-se de que DB_PASSWORD está configurada no Railway")
        return False
    
    try:
        # Conectar ao banco
        print("\n📡 Conectando ao banco de dados...")
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()
        print("✅ Conectado com sucesso!")
        
        # PASSO 1: Adicionar coluna servico_id
        print("\n[1/4] Adicionando coluna servico_id...")
        cur.execute("""
            ALTER TABLE pagamento_futuro 
            ADD COLUMN IF NOT EXISTS servico_id INTEGER;
        """)
        print("✅ Coluna servico_id adicionada")
        
        # PASSO 2: Adicionar coluna tipo
        print("\n[2/4] Adicionando coluna tipo...")
        cur.execute("""
            ALTER TABLE pagamento_futuro 
            ADD COLUMN IF NOT EXISTS tipo VARCHAR(50);
        """)
        print("✅ Coluna tipo adicionada")
        
        # PASSO 3: Criar foreign key
        print("\n[3/4] Criando foreign key...")
        try:
            cur.execute("""
                ALTER TABLE pagamento_futuro 
                ADD CONSTRAINT fk_pagamento_futuro_servico 
                FOREIGN KEY (servico_id) 
                REFERENCES servico(id) 
                ON DELETE SET NULL;
            """)
            print("✅ Foreign key criada")
        except psycopg2.errors.DuplicateObject:
            print("⚠️ Foreign key já existe (ok)")
            conn.rollback()
        
        # PASSO 4: Criar índice
        print("\n[4/4] Criando índice para performance...")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_pagamento_futuro_servico 
            ON pagamento_futuro(servico_id);
        """)
        print("✅ Índice criado")
        
        # Commit
        conn.commit()
        
        # VERIFICAÇÃO
        print("\n🔍 Verificando colunas criadas...")
        cur.execute("""
            SELECT column_name, data_type, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'pagamento_futuro' 
            AND column_name IN ('servico_id', 'tipo')
            ORDER BY column_name;
        """)
        
        colunas = cur.fetchall()
        if colunas:
            print("\n✅ COLUNAS CRIADAS COM SUCESSO:")
            for col in colunas:
                print(f"   - {col[0]} ({col[1]}) - Nullable: {col[2]}")
        else:
            print("\n⚠️ AVISO: Colunas não encontradas!")
        
        # Fechar conexão
        cur.close()
        conn.close()
        
        print("\n" + "=" * 70)
        print("🎉 MIGRATION CONCLUÍDA COM SUCESSO!")
        print("=" * 70)
        return True
        
    except psycopg2.Error as e:
        print(f"\n❌ ERRO NO BANCO DE DADOS: {e}")
        if conn:
            conn.rollback()
        return False
        
    except Exception as e:
        print(f"\n❌ ERRO GERAL: {e}")
        return False
        
    finally:
        if 'cur' in locals() and cur:
            cur.close()
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    print("\n")
    success = run_migration()
    print("\n")
    
    if success:
        exit(0)  # Sucesso
    else:
        exit(1)  # Erro
