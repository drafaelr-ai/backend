#!/usr/bin/env python3
"""
Script auxiliar para testar o Cronograma da Obra
Execute: python setup_cronograma.py
"""

import sys
import os

def print_header(text):
    print("\n" + "="*60)
    print(f"  {text}")
    print("="*60)

def print_success(text):
    print(f"✅ {text}")

def print_error(text):
    print(f"❌ {text}")

def print_info(text):
    print(f"ℹ️  {text}")

def criar_tabela():
    """Cria a tabela cronograma_obra no banco"""
    print_header("CRIANDO TABELA NO BANCO")
    
    try:
        from app import app, db
        
        with app.app_context():
            # Verifica se a tabela já existe
            inspector = db.inspect(db.engine)
            if 'cronograma_obra' in inspector.get_table_names():
                print_info("Tabela cronograma_obra já existe!")
                resposta = input("Deseja recriar a tabela? (s/n): ")
                if resposta.lower() == 's':
                    print_info("Recriando tabela...")
                    db.session.execute(db.text("DROP TABLE IF EXISTS cronograma_obra CASCADE"))
                    db.session.commit()
                else:
                    print_info("Mantendo tabela existente.")
                    return True
            
            # Criar tabela
            db.create_all()
            print_success("Tabela cronograma_obra criada com sucesso!")
            return True
            
    except ImportError:
        print_error("Não foi possível importar app.py")
        print_info("Certifique-se de estar no diretório correto do backend")
        return False
    except Exception as e:
        print_error(f"Erro ao criar tabela: {str(e)}")
        return False

def verificar_modelo():
    """Verifica se o modelo CronogramaObra foi adicionado ao app.py"""
    print_header("VERIFICANDO MODELO")
    
    try:
        from app import CronogramaObra
        print_success("Modelo CronogramaObra encontrado no app.py!")
        return True
    except ImportError:
        print_error("Modelo CronogramaObra não encontrado!")
        print_info("Você precisa adicionar o modelo ao app.py primeiro.")
        print_info("Veja o arquivo TESTE_RAPIDO.md passo 1.1")
        return False

def verificar_rotas():
    """Verifica se as rotas foram adicionadas"""
    print_header("VERIFICANDO ROTAS")
    
    try:
        with open('app.py', 'r', encoding='utf-8') as f:
            conteudo = f.read()
            
        rotas = [
            '/obras/<int:obra_id>/cronograma',
            '/cronograma',
            '/cronograma/<int:cronograma_id>'
        ]
        
        rotas_encontradas = []
        rotas_faltando = []
        
        for rota in rotas:
            if rota in conteudo:
                rotas_encontradas.append(rota)
            else:
                rotas_faltando.append(rota)
        
        if rotas_faltando:
            print_error(f"Faltam {len(rotas_faltando)} rotas:")
            for rota in rotas_faltando:
                print(f"  - {rota}")
            print_info("Adicione as rotas do arquivo TESTE_RAPIDO.md passo 1.2")
            return False
        else:
            print_success(f"Todas as {len(rotas_encontradas)} rotas encontradas!")
            return True
            
    except FileNotFoundError:
        print_error("Arquivo app.py não encontrado!")
        print_info("Execute este script no diretório do backend")
        return False

def listar_obras():
    """Lista obras disponíveis"""
    try:
        from app import app, db, Obra
        
        with app.app_context():
            obras = Obra.query.all()
            
            if not obras:
                print_error("Nenhuma obra encontrada!")
                print_info("Crie uma obra através da interface antes de continuar")
                return None
            
            print_info(f"Encontradas {len(obras)} obra(s):")
            for obra in obras:
                print(f"  ID: {obra.id} - {obra.nome}")
            
            return obras
            
    except Exception as e:
        print_error(f"Erro ao listar obras: {str(e)}")
        return None

def popular_cronograma(obra_id):
    """Popula cronograma com dados de teste"""
    print_header(f"POPULANDO CRONOGRAMA DA OBRA ID={obra_id}")
    
    try:
        from app import app, db, CronogramaObra
        from datetime import datetime, timedelta
        
        with app.app_context():
            hoje = datetime.now().date()
            
            # Verifica se já tem cronograma
            existing = CronogramaObra.query.filter_by(obra_id=obra_id).count()
            if existing > 0:
                print_info(f"Obra já possui {existing} etapa(s) no cronograma")
                resposta = input("Adicionar mesmo assim? (s/n): ")
                if resposta.lower() != 's':
                    return True
            
            # Etapas de exemplo
            etapas = [
                {
                    'servico_nome': 'Fundação',
                    'ordem': 1,
                    'dias_inicio': -5,
                    'duracao_dias': 15,
                    'percentual_conclusao': 60,
                    'observacoes': 'Em andamento - 60% concluído'
                },
                {
                    'servico_nome': 'Estrutura',
                    'ordem': 2,
                    'dias_inicio': 8,
                    'duracao_dias': 20,
                    'percentual_conclusao': 0,
                    'observacoes': 'A iniciar em breve'
                },
                {
                    'servico_nome': 'Alvenaria',
                    'ordem': 3,
                    'dias_inicio': 25,
                    'duracao_dias': 18,
                    'percentual_conclusao': 0,
                    'observacoes': 'Aguardando conclusão da estrutura'
                },
                {
                    'servico_nome': 'Instalações',
                    'ordem': 4,
                    'dias_inicio': 40,
                    'duracao_dias': 15,
                    'percentual_conclusao': 0,
                    'observacoes': 'Hidráulica e elétrica'
                },
                {
                    'servico_nome': 'Acabamento',
                    'ordem': 5,
                    'dias_inicio': 50,
                    'duracao_dias': 20,
                    'percentual_conclusao': 0,
                    'observacoes': 'Pintura e revestimentos'
                }
            ]
            
            for etapa in etapas:
                data_inicio = hoje + timedelta(days=etapa['dias_inicio'])
                data_fim = data_inicio + timedelta(days=etapa['duracao_dias'])
                
                novo_item = CronogramaObra(
                    obra_id=obra_id,
                    servico_nome=etapa['servico_nome'],
                    ordem=etapa['ordem'],
                    data_inicio=data_inicio,
                    data_fim_prevista=data_fim,
                    percentual_conclusao=etapa['percentual_conclusao'],
                    observacoes=etapa['observacoes']
                )
                
                db.session.add(novo_item)
                print_success(f"Adicionada: {etapa['servico_nome']}")
            
            db.session.commit()
            print_success(f"✨ {len(etapas)} etapas criadas com sucesso!")
            return True
            
    except Exception as e:
        print_error(f"Erro ao popular cronograma: {str(e)}")
        return False

def menu_principal():
    """Menu interativo"""
    print_header("SETUP CRONOGRAMA DA OBRA")
    print("\nO que deseja fazer?")
    print("1. Verificar instalação completa")
    print("2. Criar tabela no banco")
    print("3. Popular dados de teste")
    print("4. Fazer tudo (verificar + criar + popular)")
    print("0. Sair")
    
    escolha = input("\nEscolha uma opção: ")
    return escolha

def main():
    """Função principal"""
    
    # Verificar se está no diretório correto
    if not os.path.exists('app.py'):
        print_error("Arquivo app.py não encontrado!")
        print_info("Execute este script no diretório do backend")
        sys.exit(1)
    
    while True:
        escolha = menu_principal()
        
        if escolha == '0':
            print("\n👋 Até logo!")
            break
            
        elif escolha == '1':
            # Verificação completa
            modelo_ok = verificar_modelo()
            rotas_ok = verificar_rotas()
            
            if modelo_ok and rotas_ok:
                print_success("\n🎉 Instalação completa! Tudo certo!")
            else:
                print_error("\n⚠️  Instalação incompleta. Veja as mensagens acima.")
            
            input("\nPressione Enter para continuar...")
            
        elif escolha == '2':
            # Criar tabela
            criar_tabela()
            input("\nPressione Enter para continuar...")
            
        elif escolha == '3':
            # Popular dados
            obras = listar_obras()
            if obras:
                try:
                    obra_id = int(input("\nDigite o ID da obra: "))
                    popular_cronograma(obra_id)
                except ValueError:
                    print_error("ID inválido!")
            
            input("\nPressione Enter para continuar...")
            
        elif escolha == '4':
            # Fazer tudo
            print_header("SETUP COMPLETO")
            
            # 1. Verificar
            modelo_ok = verificar_modelo()
            if not modelo_ok:
                print_error("Configure o modelo primeiro!")
                input("\nPressione Enter para continuar...")
                continue
            
            rotas_ok = verificar_rotas()
            if not rotas_ok:
                print_error("Configure as rotas primeiro!")
                input("\nPressione Enter para continuar...")
                continue
            
            # 2. Criar tabela
            tabela_ok = criar_tabela()
            if not tabela_ok:
                print_error("Erro ao criar tabela!")
                input("\nPressione Enter para continuar...")
                continue
            
            # 3. Popular
            obras = listar_obras()
            if obras:
                try:
                    obra_id = int(input("\nDigite o ID da obra para popular: "))
                    popular_cronograma(obra_id)
                    
                    print_success("\n🎉 SETUP COMPLETO!")
                    print_info("Agora você pode testar no frontend!")
                    
                except ValueError:
                    print_error("ID inválido!")
            
            input("\nPressione Enter para continuar...")
        
        else:
            print_error("Opção inválida!")
            input("\nPressione Enter para continuar...")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Setup cancelado pelo usuário")
        sys.exit(0)
