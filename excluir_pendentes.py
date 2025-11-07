#!/usr/bin/env python3
"""
Script para EXCLUIR lançamentos pendentes antigos (valores "fantasmas").

USO:
1. Instalar requests: pip install requests
2. Executar: python excluir_pendentes.py
"""

import requests
import json

# CONFIGURAÇÕES
API_URL = "https://backend-production-78c9.up.railway.app"
# Coloque seu token JWT aqui (copie do localStorage do navegador)
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmcmVzaCI6ZmFsc2UsImlhdCI6MTc2MjU0MzAyMiwianRpIjoiMDhkNWU3MmMtODQ1YS00ZWExLWJmNzctOTVkNTZmNmUxNmRkIiwidHlwZSI6ImFjY2VzcyIsInN1YiI6IjEiLCJuYmYiOjE3NjI1NDMwMjIsImNzcmYiOiJiZjAyMDM4OS00MWM2LTQwZWYtYmRiNS0wYTc3ZmFiODMwYTkiLCJleHAiOjE3NjI1NDM5MjIsInVzZXJuYW1lIjoiYWRtaW5fcHJpbmNpcGFsIiwicm9sZSI6ImFkbWluaXN0cmFkb3IifQ.tf2spq90Dc1NZoNjHvC6aoVLZ3qR3sWWdfPivvg-G4Q"

# ID da obra que você quer verificar/limpar
OBRA_ID = 1  # Altere para o ID da sua obra

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

def listar_lancamentos_pendentes():
    """Lista todos os lançamentos com saldo pendente"""
    print("\n" + "="*60)
    print("LISTANDO LANÇAMENTOS PENDENTES (Valores Fantasmas)")
    print("="*60)
    
    url = f"{API_URL}/obras/{OBRA_ID}/lancamentos-pendentes"
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✅ Encontrados {data['total_lancamentos']} lançamentos pendentes")
        print(f"💰 Valor Total Pendente: R$ {data['total_pendente']:.2f}\n")
        
        if data['lancamentos']:
            print("📋 DETALHES DOS LANÇAMENTOS:\n")
            for lanc in data['lancamentos']:
                print(f"  ID: {lanc['id']}")
                print(f"  Descrição: {lanc['descricao']}")
                print(f"  Tipo: {lanc['tipo']}")
                print(f"  Fornecedor: {lanc['fornecedor'] or 'N/A'}")
                print(f"  ⚠️  VALOR PENDENTE: R$ {lanc['valor_restante']:.2f}")
                print(f"  Data: {lanc['data']}")
                print(f"  Vencimento: {lanc['data_vencimento'] or 'Sem vencimento'}")
                print("-" * 50)
        
        return data
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return None

def excluir_um_lancamento(lancamento_id):
    """Exclui um lançamento específico"""
    print(f"\n🗑️  Excluindo lançamento ID {lancamento_id}...")
    
    url = f"{API_URL}/obras/{OBRA_ID}/lancamentos/{lancamento_id}/excluir-pendente"
    response = requests.delete(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ {data['mensagem']}")
        print(f"   Descrição: {data['descricao']}")
        print(f"   Valor que estava pendente: R$ {data['valor_que_estava_pendente']:.2f}")
        return True
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return False

def excluir_todos():
    """Exclui TODOS os lançamentos pendentes de uma vez"""
    print("\n" + "="*60)
    print("⚠️  EXCLUSÃO EM MASSA - TODOS OS LANÇAMENTOS PENDENTES")
    print("="*60)
    
    # Listar primeiro para o usuário ver o que vai ser excluído
    dados = listar_lancamentos_pendentes()
    if not dados or dados['total_lancamentos'] == 0:
        print("\n✅ Nenhum lançamento pendente encontrado. Nada a fazer!")
        return
    
    print("\n" + "⚠️ " * 20)
    print("ATENÇÃO: Você está prestes a EXCLUIR permanentemente:")
    print(f"  • {dados['total_lancamentos']} lançamentos")
    print(f"  • Valor total de R$ {dados['total_pendente']:.2f}")
    print("⚠️ " * 20)
    
    confirmacao = input("\n⚠️  Tem CERTEZA que deseja EXCLUIR todos? (digite 'EXCLUIR' para confirmar): ")
    if confirmacao != 'EXCLUIR':
        print("❌ Operação cancelada.")
        return
    
    url = f"{API_URL}/obras/{OBRA_ID}/lancamentos/excluir-todos-pendentes"
    response = requests.delete(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✅ {data['mensagem']}")
        print(f"   Quantidade excluída: {data['quantidade_excluida']}")
        print(f"   💰 Valor total removido: R$ {data['valor_total_removido']:.2f}\n")
        
        print("📋 LANÇAMENTOS EXCLUÍDOS:")
        for lanc in data['lancamentos_excluidos']:
            print(f"   • ID {lanc['lancamento_id']}: {lanc['descricao']} - R$ {lanc['valor_pendente_removido']:.2f}")
        
        print("\n✅ Pronto! O KPI 'Liberado p/ Pagamento' deve estar zerado agora.")
        return True
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return False

def excluir_todas_obras():
    """Exclui TODOS os lançamentos pendentes de TODAS as obras de uma vez"""
    print("\n" + "="*60)
    print("🚨 LIMPEZA GLOBAL - TODAS AS OBRAS (Apenas Lançamentos)")
    print("="*60)
    
    print("\n" + "⚠️ " * 20)
    print("ATENÇÃO MÁXIMA!")
    print("Você está prestes a EXCLUIR PERMANENTEMENTE:")
    print("  • TODOS os LANÇAMENTOS pendentes")
    print("  • De TODAS as obras que você tem acesso")
    print("  • Esta operação NÃO PODE ser desfeita!")
    print("⚠️ " * 20)
    
    confirmacao1 = input("\n⚠️  Tem CERTEZA ABSOLUTA? (digite 'SIM' para continuar): ")
    if confirmacao1 != 'SIM':
        print("❌ Operação cancelada.")
        return
    
    confirmacao2 = input("⚠️  Última confirmação - digite 'EXCLUIR TUDO' para confirmar: ")
    if confirmacao2 != 'EXCLUIR TUDO':
        print("❌ Operação cancelada.")
        return
    
    print("\n🔄 Processando limpeza global...")
    
    url = f"{API_URL}/lancamentos/excluir-todos-pendentes-global"
    response = requests.delete(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✅ {data['mensagem']}")
        print(f"   Total de obras processadas: {data['total_obras_processadas']}")
        print(f"   Obras com pendências: {data['obras_com_pendencias']}")
        print(f"   💰 Valor total removido: R$ {data['valor_total_removido']:.2f}\n")
        
        if data['detalhes_por_obra']:
            print("📋 DETALHES POR OBRA:\n")
            for obra in data['detalhes_por_obra']:
                print(f"  🏗️  {obra['obra_nome']} (ID: {obra['obra_id']})")
                print(f"      Lançamentos excluídos: {obra['quantidade_excluida']}")
                print(f"      Valor removido: R$ {obra['valor_removido']:.2f}")
                print()
        
        print("\n✅ LIMPEZA GLOBAL CONCLUÍDA!")
        print("   Todos os KPIs 'Liberado p/ Pagamento' devem estar corretos agora.")
        return True
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return False

def super_limpeza_global():
    """SUPER LIMPEZA: Exclui TODOS os lançamentos E pagamentos de serviço pendentes"""
    print("\n" + "="*60)
    print("🔥 SUPER LIMPEZA - TUDO (Lançamentos + Pagamentos)")
    print("="*60)
    
    print("\n" + "🔥 " * 20)
    print("⚠️  ATENÇÃO MÁXIMA - SUPER LIMPEZA! ⚠️")
    print()
    print("Você está prestes a EXCLUIR PERMANENTEMENTE:")
    print("  ✓ TODOS os LANÇAMENTOS com saldo pendente")
    print("  ✓ TODOS os PAGAMENTOS DE SERVIÇO com saldo pendente")
    print("  ✓ De TODAS as obras que você tem acesso")
    print()
    print("Isso vai ZERAR completamente o KPI 'Liberado p/ Pagamento'!")
    print("Esta operação NÃO PODE ser desfeita!")
    print("🔥 " * 20)
    
    confirmacao1 = input("\n⚠️  Tem CERTEZA ABSOLUTA? (digite 'SIM' para continuar): ")
    if confirmacao1 != 'SIM':
        print("❌ Operação cancelada.")
        return
    
    confirmacao2 = input("⚠️  Digite 'LIMPAR TUDO' para confirmar a SUPER LIMPEZA: ")
    if confirmacao2 != 'LIMPAR TUDO':
        print("❌ Operação cancelada.")
        return
    
    print("\n🔥 Processando SUPER LIMPEZA...")
    
    url = f"{API_URL}/limpar-tudo-pendente-global"
    response = requests.delete(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✅ {data['mensagem']}")
        print(f"   Total de obras processadas: {data['total_obras_processadas']}")
        print(f"   Obras com pendências: {data['obras_com_pendencias']}")
        print(f"   Lançamentos excluídos: {data['total_lancamentos_excluidos']}")
        print(f"   Pagamentos excluídos: {data['total_pagamentos_excluidos']}")
        print(f"   💰 Valor total removido: R$ {data['valor_total_removido']:.2f}\n")
        
        if data['detalhes_por_obra']:
            print("📋 DETALHES POR OBRA:\n")
            for obra in data['detalhes_por_obra']:
                print(f"  🏗️  {obra['obra_nome']} (ID: {obra['obra_id']})")
                print(f"      Lançamentos: {obra['lancamentos_excluidos']}")
                print(f"      Pagamentos: {obra['pagamentos_excluidos']}")
                print(f"      Total: {obra['total_excluido']} itens")
                print(f"      Valor removido: R$ {obra['valor_removido']:.2f}")
                print()
        
        print("\n🔥 SUPER LIMPEZA CONCLUÍDA!")
        print("   O KPI 'Liberado p/ Pagamento' deve estar ZERADO agora!")
        return True
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return False

def menu():
    """Menu principal"""
    while True:
        print("\n" + "="*60)
        print("LIMPEZA DE LANÇAMENTOS PENDENTES (Valores Fantasmas)")
        print("="*60)
        print(f"Obra ID: {OBRA_ID}")
        print("\nOpções:")
        print("  1 - Listar lançamentos pendentes de UMA obra")
        print("  2 - Excluir um lançamento específico")
        print("  3 - Excluir TODOS os lançamentos de UMA obra")
        print("  4 - 🚨 Limpar lançamentos de TODAS as obras")
        print("  5 - 🔥 SUPER LIMPEZA - Lançamentos + Pagamentos (RECOMENDADO)")
        print("  0 - Sair")
        print("="*60)
        print("\n💡 DICA: Use a opção 5 para limpar TUDO de uma vez!")
        
        opcao = input("\nEscolha uma opção: ")
        
        if opcao == "1":
            listar_lancamentos_pendentes()
        
        elif opcao == "2":
            lancamento_id = input("Digite o ID do lançamento a excluir: ")
            try:
                excluir_um_lancamento(int(lancamento_id))
            except ValueError:
                print("❌ ID inválido!")
        
        elif opcao == "3":
            excluir_todos()
        
        elif opcao == "4":
            excluir_todas_obras()
        
        elif opcao == "5":
            super_limpeza_global()
        
        elif opcao == "0":
            print("\n👋 Até logo!")
            break
        
        else:
            print("❌ Opção inválida!")

if __name__ == "__main__":
    print("\n🚀 Iniciando ferramenta de limpeza...")
    print("⚠️  ATENÇÃO: Configure o TOKEN e OBRA_ID antes de executar!")
    
    if TOKEN == "SEU_TOKEN_AQUI":
        print("\n❌ ERRO: Você precisa configurar o TOKEN no script!")
        print("   Copie o token JWT do localStorage do navegador")
        exit(1)
    
    menu()
