#!/usr/bin/env python3
"""
Script para testar e executar a migração de lançamentos pendentes para o cronograma financeiro.

USO:
1. Instalar requests: pip install requests
2. Executar: python testar_migracao.py
"""

import requests
import json

# CONFIGURAÇÕES
API_URL = "https://backend-production-78c9.up.railway.app"
# Coloque seu token JWT aqui (copie do localStorage do navegador)
TOKEN = "SEU_TOKEN_AQUI"

# ID da obra que você quer verificar/migrar
OBRA_ID = 1  # Altere para o ID da sua obra

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

def listar_lancamentos_pendentes():
    """Lista todos os lançamentos com saldo pendente"""
    print("\n" + "="*60)
    print("LISTANDO LANÇAMENTOS PENDENTES")
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
                print(f"  Valor Total: R$ {lanc['valor_total']:.2f}")
                print(f"  Valor Pago: R$ {lanc['valor_pago']:.2f}")
                print(f"  ⚠️  VALOR RESTANTE: R$ {lanc['valor_restante']:.2f}")
                print(f"  Data: {lanc['data']}")
                print(f"  Vencimento: {lanc['data_vencimento'] or 'Sem vencimento'}")
                print(f"  Status: {lanc['status']}")
                print("-" * 50)
        
        return data
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return None

def migrar_um_lancamento(lancamento_id):
    """Migra um lançamento específico para o cronograma"""
    print(f"\n🔄 Migrando lançamento ID {lancamento_id}...")
    
    url = f"{API_URL}/obras/{OBRA_ID}/lancamentos/{lancamento_id}/migrar-cronograma"
    response = requests.post(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ {data['mensagem']}")
        print(f"   Valor migrado: R$ {data['valor_migrado']:.2f}")
        print(f"   Novo pagamento futuro ID: {data['pagamento_futuro_id']}")
        return True
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return False

def migrar_todos():
    """Migra TODOS os lançamentos pendentes de uma vez"""
    print("\n" + "="*60)
    print("⚠️  MIGRAÇÃO EM MASSA - TODOS OS LANÇAMENTOS")
    print("="*60)
    
    confirmacao = input("\n⚠️  Tem certeza que deseja migrar TODOS os lançamentos pendentes? (sim/não): ")
    if confirmacao.lower() != 'sim':
        print("❌ Operação cancelada.")
        return
    
    url = f"{API_URL}/obras/{OBRA_ID}/lancamentos/migrar-todos-cronograma"
    response = requests.post(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✅ {data['mensagem']}")
        print(f"   Quantidade migrada: {data['quantidade_migrada']}")
        print(f"   💰 Valor total migrado: R$ {data['valor_total_migrado']:.2f}\n")
        
        print("📋 LANÇAMENTOS MIGRADOS:")
        for lanc in data['lancamentos']:
            print(f"   • ID {lanc['lancamento_id']}: {lanc['descricao']} - R$ {lanc['valor_migrado']:.2f}")
        
        return True
    else:
        print(f"❌ Erro: {response.status_code}")
        print(response.text)
        return False

def menu():
    """Menu principal"""
    while True:
        print("\n" + "="*60)
        print("FERRAMENTA DE MIGRAÇÃO DE LANÇAMENTOS PENDENTES")
        print("="*60)
        print(f"Obra ID: {OBRA_ID}")
        print("\nOpções:")
        print("  1 - Listar lançamentos pendentes")
        print("  2 - Migrar um lançamento específico")
        print("  3 - Migrar TODOS os lançamentos (use com cuidado!)")
        print("  0 - Sair")
        print("="*60)
        
        opcao = input("\nEscolha uma opção: ")
        
        if opcao == "1":
            listar_lancamentos_pendentes()
        
        elif opcao == "2":
            lancamento_id = input("Digite o ID do lançamento a migrar: ")
            try:
                migrar_um_lancamento(int(lancamento_id))
            except ValueError:
                print("❌ ID inválido!")
        
        elif opcao == "3":
            migrar_todos()
        
        elif opcao == "0":
            print("\n👋 Até logo!")
            break
        
        else:
            print("❌ Opção inválida!")

if __name__ == "__main__":
    print("\n🚀 Iniciando ferramenta de migração...")
    print("⚠️  ATENÇÃO: Configure o TOKEN e OBRA_ID antes de executar!")
    
    if TOKEN == "SEU_TOKEN_AQUI":
        print("\n❌ ERRO: Você precisa configurar o TOKEN no script!")
        print("   Copie o token JWT do localStorage do navegador")
        exit(1)
    
    menu()
