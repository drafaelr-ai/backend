"""
Script de Migração - Cronograma de Compras
Sistema OBRALY v2.0

Este script cria a tabela 'compra_agendada' no banco de dados.
Execute este script após atualizar o app.py com o novo modelo.
"""

from app import app, db, CompraAgendada
from sqlalchemy import text, inspect
import sys

def verificar_tabela_existe(nome_tabela):
    """Verifica se uma tabela existe no banco de dados"""
    inspector = inspect(db.engine)
    return nome_tabela in inspector.get_table_names()

def criar_tabela_compras():
    """Cria a tabela de compras agendadas"""
    with app.app_context():
        try:
            print("=" * 60)
            print("MIGRAÇÃO: Cronograma de Compras")
            print("=" * 60)
            print()
            
            # Verifica se a tabela já existe
            if verificar_tabela_existe('compra_agendada'):
                print("⚠️  ATENÇÃO: A tabela 'compra_agendada' já existe!")
                resposta = input("Deseja recriar a tabela? (TODOS OS DADOS SERÃO PERDIDOS) [s/N]: ")
                
                if resposta.lower() != 's':
                    print("❌ Migração cancelada pelo usuário.")
                    return False
                
                print("🗑️  Deletando tabela existente...")
                db.session.execute(text('DROP TABLE IF EXISTS compra_agendada CASCADE'))
                db.session.commit()
                print("✅ Tabela deletada.")
                print()
            
            print("📦 Criando tabela 'compra_agendada'...")
            
            # Cria todas as tabelas (incluindo a nova)
            db.create_all()
            
            # Verifica se a tabela foi criada
            if verificar_tabela_existe('compra_agendada'):
                print("✅ Tabela 'compra_agendada' criada com sucesso!")
                print()
                
                # Exibe a estrutura da tabela
                inspector = inspect(db.engine)
                columns = inspector.get_columns('compra_agendada')
                
                print("📋 Estrutura da tabela:")
                print("-" * 60)
                for column in columns:
                    nullable = "NULL" if column['nullable'] else "NOT NULL"
                    default = f" DEFAULT {column['default']}" if column['default'] else ""
                    print(f"  • {column['name']:<25} {str(column['type']):<15} {nullable}{default}")
                print("-" * 60)
                print()
                
                print("✅ Migração concluída com sucesso!")
                print()
                print("🎉 O Cronograma de Compras está pronto para uso!")
                return True
            else:
                print("❌ ERRO: Tabela não foi criada corretamente.")
                return False
                
        except Exception as e:
            print(f"❌ ERRO durante a migração: {str(e)}")
            print()
            print("Traceback completo:")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return False

def criar_dados_exemplo():
    """Cria alguns dados de exemplo (opcional)"""
    with app.app_context():
        try:
            from app import Obra
            
            # Verifica se existem obras
            obras = Obra.query.all()
            if not obras:
                print("⚠️  Nenhuma obra encontrada. Dados de exemplo não serão criados.")
                return
            
            obra = obras[0]
            print(f"📝 Criando dados de exemplo para a obra '{obra.nome}'...")
            
            import datetime
            from datetime import timedelta
            
            exemplos = [
                CompraAgendada(
                    obra_id=obra.id,
                    item="Cimento CP-II (50 sacos)",
                    descricao="Cimento para fundação",
                    fornecedor_sugerido="Casa de Materiais ABC",
                    valor_estimado=1500.00,
                    data_prevista=datetime.date.today() + timedelta(days=3),
                    categoria="Material",
                    prioridade=4,
                    status="Pendente",
                    observacoes="Verificar disponibilidade antes de comprar"
                ),
                CompraAgendada(
                    obra_id=obra.id,
                    item="Areia fina (5m³)",
                    descricao="Areia para reboco",
                    fornecedor_sugerido="Areia Silva",
                    valor_estimado=350.00,
                    data_prevista=datetime.date.today() + timedelta(days=7),
                    categoria="Material",
                    prioridade=3,
                    status="Pendente"
                ),
                CompraAgendada(
                    obra_id=obra.id,
                    item="Furadeira de impacto",
                    descricao="Furadeira Bosch 800W",
                    fornecedor_sugerido="Ferramentas Pro",
                    valor_estimado=450.00,
                    data_prevista=datetime.date.today() + timedelta(days=1),
                    categoria="Ferramenta",
                    prioridade=5,
                    status="Pendente",
                    observacoes="URGENTE - Necessário para instalação elétrica"
                )
            ]
            
            for compra in exemplos:
                db.session.add(compra)
            
            db.session.commit()
            print(f"✅ {len(exemplos)} compras de exemplo criadas com sucesso!")
            print()
            
        except Exception as e:
            print(f"❌ Erro ao criar dados de exemplo: {str(e)}")
            db.session.rollback()

def menu_principal():
    """Menu interativo para o script de migração"""
    print()
    print("=" * 60)
    print("  MIGRAÇÃO: CRONOGRAMA DE COMPRAS - Sistema OBRALY")
    print("=" * 60)
    print()
    print("Escolha uma opção:")
    print()
    print("  1. Criar tabela (migração completa)")
    print("  2. Criar tabela + dados de exemplo")
    print("  3. Apenas verificar estrutura")
    print("  0. Sair")
    print()
    
    opcao = input("Digite a opção desejada: ")
    print()
    
    if opcao == "1":
        sucesso = criar_tabela_compras()
        if sucesso:
            print()
            print("💡 Dica: Você pode criar dados de exemplo executando:")
            print("   python migrate_compras.py --exemplo")
    
    elif opcao == "2":
        sucesso = criar_tabela_compras()
        if sucesso:
            criar_dados_exemplo()
    
    elif opcao == "3":
        with app.app_context():
            if verificar_tabela_existe('compra_agendada'):
                print("✅ Tabela 'compra_agendada' existe no banco de dados.")
                print()
                
                inspector = inspect(db.engine)
                columns = inspector.get_columns('compra_agendada')
                
                print("📋 Estrutura atual:")
                print("-" * 60)
                for column in columns:
                    nullable = "NULL" if column['nullable'] else "NOT NULL"
                    print(f"  • {column['name']:<25} {str(column['type']):<15} {nullable}")
                print("-" * 60)
            else:
                print("❌ Tabela 'compra_agendada' NÃO existe no banco de dados.")
                print()
                print("Execute a opção 1 para criar a tabela.")
    
    elif opcao == "0":
        print("👋 Saindo...")
        sys.exit(0)
    
    else:
        print("❌ Opção inválida!")

if __name__ == '__main__':
    import sys
    
    # Verifica argumentos de linha de comando
    if len(sys.argv) > 1:
        if sys.argv[1] == '--exemplo':
            criar_dados_exemplo()
        elif sys.argv[1] == '--verificar':
            with app.app_context():
                if verificar_tabela_existe('compra_agendada'):
                    print("✅ Tabela existe")
                    sys.exit(0)
                else:
                    print("❌ Tabela não existe")
                    sys.exit(1)
        elif sys.argv[1] == '--criar':
            criar_tabela_compras()
        else:
            print("Uso: python migrate_compras.py [--criar|--exemplo|--verificar]")
            sys.exit(1)
    else:
        # Menu interativo
        menu_principal()
