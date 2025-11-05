from app import app, db
from sqlalchemy import text, inspect

print("Iniciando migracao do Cronograma de Compras...")
print("")

with app.app_context():
    try:
        # Verifica se tabela existe
        inspector = inspect(db.engine)
        tabelas = inspector.get_table_names()
        
        if 'compra_agendada' in tabelas:
            print("AVISO: Tabela compra_agendada ja existe!")
            print("Digite S para recriar (apaga dados) ou N para cancelar:")
            resposta = input()
            
            if resposta.upper() != 'S':
                print("Cancelado.")
                exit()
            
            print("Apagando tabela antiga...")
            db.session.execute(text('DROP TABLE IF EXISTS compra_agendada CASCADE'))
            db.session.commit()
            print("OK - Tabela apagada")
        
        print("Criando tabela compra_agendada...")
        db.create_all()
        
        # Verifica se foi criada
        inspector = inspect(db.engine)
        tabelas = inspector.get_table_names()
        
        if 'compra_agendada' in tabelas:
            print("")
            print("SUCESSO! Tabela criada com sucesso!")
            print("")
            print("Cronograma de Compras pronto para usar!")
        else:
            print("ERRO: Tabela nao foi criada")
            
    except Exception as e:
        print("ERRO:")
        print(str(e))
        db.session.rollback()
