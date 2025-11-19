#!/usr/bin/env python3
"""
Script de Correção Automática do app.py
Aplica todas as correções necessárias nas funções:
- listar_parcelas_individuais
- marcar_parcela_paga

Uso:
    python aplicar_correcoes.py
"""

import re
import sys
from pathlib import Path

# ============================================================================
# FUNÇÃO 1: listar_parcelas_individuais (VERSÃO CORRIGIDA)
# ============================================================================

FUNCAO_LISTAR_PARCELAS = '''@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas', methods=['GET', 'OPTIONS'])
@jwt_required(optional=True)
def listar_parcelas_individuais(obra_id, pagamento_id):
    """
    Lista todas as parcelas individuais de um pagamento parcelado.
    Se as parcelas não existirem, gera automaticamente baseado na configuração do pagamento.
    """
    # Handler para OPTIONS (CORS preflight)
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'GET, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    print("\\n" + "="*80)
    print(f"📋 INÍCIO: listar_parcelas_individuais")
    print(f"   obra_id={obra_id}, pagamento_id={pagamento_id}")
    print("="*80)
    
    try:
        # Validações de acesso
        current_user = get_current_user()
        print(f"   👤 Usuário: {current_user.username}")
        
        if not user_has_access_to_obra(current_user, obra_id):
            print(f"   ❌ Acesso negado à obra {obra_id}")
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamento parcelado
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            print(f"   ❌ Pagamento {pagamento_id} não encontrado ou não pertence à obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        print(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        print(f"      - Valor total: R$ {pagamento.valor_total}")
        print(f"      - Número de parcelas: {pagamento.numero_parcelas}")
        print(f"      - Periodicidade: {pagamento.periodicidade}")
        print(f"      - Parcelas pagas: {pagamento.parcelas_pagas}")
        
        # Buscar parcelas individuais
        parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).order_by(ParcelaIndividual.numero_parcela).all()
        
        print(f"   📊 Parcelas existentes no banco: {len(parcelas)}")
        
        # Gerar parcelas automaticamente se não existirem
        if not parcelas:
            print(f"   🔄 Nenhuma parcela encontrada. Gerando automaticamente...")
            
            # Validar dados necessários
            if not pagamento.data_primeira_parcela:
                error_msg = "Pagamento não possui data_primeira_parcela definida"
                print(f"   ❌ {error_msg}")
                return jsonify({"erro": error_msg}), 400
            
            if not pagamento.valor_parcela or pagamento.valor_parcela <= 0:
                error_msg = "Pagamento não possui valor_parcela válido"
                print(f"   ❌ {error_msg}")
                return jsonify({"erro": error_msg}), 400
            
            # Determinar intervalo entre parcelas
            if pagamento.periodicidade == 'Semanal':
                dias_intervalo = 7
            elif pagamento.periodicidade == 'Quinzenal':
                dias_intervalo = 15
            elif pagamento.periodicidade == 'Mensal':
                dias_intervalo = 30
            else:
                dias_intervalo = 30
            
            print(f"      - Intervalo entre parcelas: {dias_intervalo} dias")
            
            valor_parcela_padrao = pagamento.valor_parcela
            
            # Gerar cada parcela
            for i in range(pagamento.numero_parcelas):
                numero_parcela = i + 1
                
                # Ajustar valor da última parcela
                if numero_parcela == pagamento.numero_parcelas:
                    valor_parcelas_anteriores = valor_parcela_padrao * (pagamento.numero_parcelas - 1)
                    valor_desta_parcela = pagamento.valor_total - valor_parcelas_anteriores
                    print(f"      - Parcela {numero_parcela}/{pagamento.numero_parcelas}: R$ {valor_desta_parcela} (ajustada)")
                else:
                    valor_desta_parcela = valor_parcela_padrao
                    print(f"      - Parcela {numero_parcela}/{pagamento.numero_parcelas}: R$ {valor_desta_parcela}")
                
                # Calcular data de vencimento
                data_vencimento = pagamento.data_primeira_parcela + timedelta(days=dias_intervalo * i)
                
                # Determinar status e data de pagamento
                if i < pagamento.parcelas_pagas:
                    status = 'Pago'
                    data_pagamento = data_vencimento
                else:
                    status = 'Previsto'
                    data_pagamento = None
                
                # Criar parcela
                parcela = ParcelaIndividual(
                    pagamento_parcelado_id=pagamento_id,
                    numero_parcela=numero_parcela,
                    valor_parcela=valor_desta_parcela,
                    data_vencimento=data_vencimento,
                    data_pagamento=data_pagamento,
                    status=status,
                    forma_pagamento=None,
                    observacoes=None
                )
                db.session.add(parcela)
            
            # Commit das parcelas geradas
            db.session.commit()
            print(f"   ✅ {pagamento.numero_parcelas} parcelas geradas e salvas no banco")
            
            # Recarregar parcelas do banco
            parcelas = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=pagamento_id
            ).order_by(ParcelaIndividual.numero_parcela).all()
            
            print(f"   📊 Parcelas após geração: {len(parcelas)}")
        
        # Preparar resposta
        parcelas_dict = [p.to_dict() for p in parcelas]
        
        # Log resumo
        parcelas_pagas_count = sum(1 for p in parcelas if p.status == 'Pago')
        parcelas_previstas_count = len(parcelas) - parcelas_pagas_count
        
        print(f"   📊 Resumo final:")
        print(f"      - Total de parcelas: {len(parcelas)}")
        print(f"      - Pagas: {parcelas_pagas_count}")
        print(f"      - Previstas: {parcelas_previstas_count}")
        print(f"   ✅ Retornando {len(parcelas_dict)} parcelas")
        print("="*80 + "\\n")
        
        return jsonify(parcelas_dict), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"\\n" + "="*80)
        print(f"❌ ERRO FATAL em listar_parcelas_individuais:")
        print(f"   {str(e)}")
        print(f"\\nStack trace completo:")
        print(error_details)
        print("="*80 + "\\n")
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500'''

# ============================================================================
# FUNÇÃO 2: marcar_parcela_paga (VERSÃO CORRIGIDA)
# ============================================================================

FUNCAO_MARCAR_PARCELA = '''@app.route('/sid/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas/<int:parcela_id>/pagar', methods=['POST', 'OPTIONS'])
@jwt_required(optional=True)
def marcar_parcela_paga(obra_id, pagamento_id, parcela_id):
    """Marca uma parcela individual como paga e cria lançamento no histórico"""
    
    # Handler para OPTIONS (CORS preflight)
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response

    try:
        print(f"\\n{'='*80}")
        print(f"💳 INÍCIO: marcar_parcela_paga")
        print(f"   obra_id={obra_id}, pagamento_id={pagamento_id}, parcela_id={parcela_id}")
        print(f"{'='*80}")
        
        # Validações de acesso
        current_user = get_current_user()
        print(f"   👤 Usuário: {current_user.username} (role: {current_user.role})")
        
        if not user_has_access_to_obra(current_user, obra_id):
            print(f"   ❌ Acesso negado à obra {obra_id}")
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamento parcelado
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            print(f"   ❌ Pagamento {pagamento_id} não encontrado ou não pertence à obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        print(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        print(f"      - servico_id: {pagamento.servico_id}")
        print(f"      - fornecedor: {pagamento.fornecedor}")
        
        # Buscar parcela
        parcela = db.session.get(ParcelaIndividual, parcela_id)
        if not parcela or parcela.pagamento_parcelado_id != pagamento_id:
            print(f"   ❌ Parcela {parcela_id} não encontrada ou não pertence ao pagamento {pagamento_id}")
            return jsonify({"erro": "Parcela não encontrada"}), 404
        
        if parcela.status == 'Pago':
            print(f"   ⚠️ Parcela {parcela_id} já estava paga")
            return jsonify({"mensagem": "Parcela já está marcada como paga"}), 200
        
        print(f"   ✅ Parcela encontrada: {parcela.numero_parcela}/{pagamento.numero_parcelas}")
        print(f"      - valor: R$ {parcela.valor_parcela}")
        
        # Processar dados
        data = request.get_json()
        
        # Marcar parcela como paga
        parcela.status = 'Pago'
        parcela.data_pagamento = datetime.strptime(
            data.get('data_pagamento', date.today().isoformat()), 
            '%Y-%m-%d'
        ).date()
        parcela.forma_pagamento = data.get('forma_pagamento', None)
        
        print(f"   ✅ Parcela marcada como paga em {parcela.data_pagamento}")
        
        # Criar lançamento no histórico
        descricao_lancamento = f"{pagamento.descricao} (Parcela {parcela.numero_parcela}/{pagamento.numero_parcelas})"
        
        # Tratamento seguro do segmento
        segmento_info = 'Material'
        if hasattr(pagamento, 'segmento') and pagamento.segmento:
            segmento_info = pagamento.segmento
        
        print(f"   📄 Criando lançamento: '{descricao_lancamento}'")
        print(f"      - segmento: {segmento_info}")
        
        novo_lancamento = Lancamento(
            obra_id=pagamento.obra_id,
            tipo='Despesa',
            descricao=descricao_lancamento,
            valor_total=parcela.valor_parcela,
            valor_pago=parcela.valor_parcela,
            data=parcela.data_pagamento,
            data_vencimento=parcela.data_vencimento,
            status='Pago',
            pix=None,
            prioridade=0,
            fornecedor=pagamento.fornecedor,
            servico_id=pagamento.servico_id
        )
        
        # Tenta atribuir segmento se o modelo suportar
        if hasattr(novo_lancamento, 'segmento'):
            novo_lancamento.segmento = segmento_info
            print(f"      - segmento atribuído ao lançamento")
        
        db.session.add(novo_lancamento)
        db.session.flush()
        
        print(f"   ✅ Lançamento criado com ID={novo_lancamento.id}")
        
        # Criar/atualizar PagamentoServico se houver vínculo
        if pagamento.servico_id:
            print(f"   🔗 Parcela vinculada ao serviço {pagamento.servico_id}")
            
            # CRÍTICO: Validar se serviço existe
            servico = db.session.get(Servico, pagamento.servico_id)
            if not servico:
                print(f"      ❌ AVISO: Serviço {pagamento.servico_id} não existe no banco!")
                print(f"      ⚠️ Continuando sem vincular ao serviço (evitando erro de foreign key)")
                novo_lancamento.servico_id = None
            else:
                print(f"      ✅ Serviço encontrado: '{servico.nome}'")
                
                # Determinar tipo de pagamento
                tipo_pag = 'mao_de_obra' if segmento_info == 'Mão de Obra' else 'material'
                print(f"      - tipo_pagamento: {tipo_pag}")
                
                # Buscar PagamentoServico existente
                pagamento_servico_existente = PagamentoServico.query.filter_by(
                    servico_id=pagamento.servico_id,
                    fornecedor=pagamento.fornecedor,
                    tipo_pagamento=tipo_pag
                ).first()
                
                if pagamento_servico_existente:
                    pagamento_servico_existente.valor_pago += parcela.valor_parcela
                    print(f"      ✅ PagamentoServico ID={pagamento_servico_existente.id} atualizado")
                    print(f"         Novo valor_pago: R$ {pagamento_servico_existente.valor_pago}")
                else:
                    novo_pag_serv = PagamentoServico(
                        servico_id=pagamento.servico_id,
                        tipo_pagamento=tipo_pag,
                        valor_total=parcela.valor_parcela,
                        valor_pago=parcela.valor_parcela,
                        data=parcela.data_pagamento,
                        fornecedor=pagamento.fornecedor,
                        forma_pagamento=parcela.forma_pagamento,
                        prioridade=0
                    )
                    db.session.add(novo_pag_serv)
                    db.session.flush()
                    print(f"      ✅ Novo PagamentoServico criado com ID={novo_pag_serv.id}")
        
        # Atualizar contador de parcelas pagas
        todas_parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        parcelas_pagas_count = sum(1 for p in todas_parcelas if p.status == 'Pago')
        pagamento.parcelas_pagas = parcelas_pagas_count
        
        print(f"   📊 Total de parcelas pagas: {parcelas_pagas_count}/{pagamento.numero_parcelas}")
        
        # Se todas foram pagas, atualizar status
        if parcelas_pagas_count >= pagamento.numero_parcelas:
            pagamento.status = 'Concluído'
            print(f"   🎉 Pagamento marcado como Concluído")
        
        # Commit final
        db.session.commit()
        
        print(f"   ✅ SUCESSO: Parcela {parcela_id} paga e lançamento {novo_lancamento.id} criado")
        print(f"{'='*80}\\n")
        
        return jsonify({
            "mensagem": "Parcela paga com sucesso",
            "parcela": parcela.to_dict(),
            "lancamento_id": novo_lancamento.id
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"\\n{'='*80}")
        print(f"❌ ERRO FATAL em marcar_parcela_paga:")
        print(f"   {str(e)}")
        print(f"\\nStack trace completo:")
        print(error_details)
        print(f"{'='*80}\\n")
        return jsonify({"erro": str(e)}), 500'''


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def find_function_bounds(content, function_name):
    """Encontra o início e fim de uma função no código"""
    lines = content.split('\n')
    start_line = None
    end_line = None
    indent_level = None
    
    for i, line in enumerate(lines):
        # Encontrar o início da função
        if start_line is None:
            if f'def {function_name}(' in line:
                start_line = i
                # Pegar o nível de indentação da função
                indent_level = len(line) - len(line.lstrip())
                continue
        else:
            # Encontrar o fim da função
            # Uma função termina quando encontramos outra função no mesmo nível
            # ou um decorator (@) no mesmo nível
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped)
            
            # Se encontramos algo no mesmo nível de indentação (ou menor)
            # e não é uma linha vazia ou comentário
            if current_indent <= indent_level and stripped and not stripped.startswith('#'):
                if stripped.startswith('def ') or stripped.startswith('@'):
                    end_line = i
                    break
    
    if start_line is None:
        return None, None
    
    if end_line is None:
        # Se não encontrou o fim, vai até o final do arquivo
        end_line = len(lines)
    
    return start_line, end_line


def replace_function(content, function_name, new_function):
    """Substitui uma função no código"""
    start, end = find_function_bounds(content, function_name)
    
    if start is None:
        print(f"⚠️ Função {function_name} não encontrada!")
        return content, False
    
    lines = content.split('\n')
    
    # Manter tudo antes da função
    before = '\n'.join(lines[:start])
    
    # Manter tudo depois da função
    after = '\n'.join(lines[end:])
    
    # Juntar: antes + nova função + depois
    new_content = before + '\n' + new_function + '\n' + after
    
    print(f"✅ Função {function_name} substituída (linhas {start+1} a {end})")
    return new_content, True


def main():
    """Função principal"""
    print("="*80)
    print("🔧 SCRIPT DE CORREÇÃO AUTOMÁTICA - app.py")
    print("="*80)
    print()
    
    # Verificar se app.py existe
    app_path = Path('app.py')
    
    if not app_path.exists():
        print("❌ Erro: arquivo app.py não encontrado no diretório atual")
        print()
        print("Por favor:")
        print("1. Coloque este script no mesmo diretório que app.py")
        print("2. Ou execute: python aplicar_correcoes.py /caminho/para/app.py")
        sys.exit(1)
    
    # Ler conteúdo original
    print("📖 Lendo app.py...")
    with open(app_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_lines = len(content.split('\n'))
    print(f"   Total de linhas: {original_lines}")
    print()
    
    # Fazer backup
    backup_path = Path('app.py.backup')
    print(f"💾 Criando backup em {backup_path}...")
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("   ✅ Backup criado")
    print()
    
    # Aplicar correções
    print("🔄 Aplicando correções...")
    print()
    
    success_count = 0
    
    # Substituir listar_parcelas_individuais
    print("1️⃣ Substituindo listar_parcelas_individuais...")
    content, success = replace_function(content, 'listar_parcelas_individuais', FUNCAO_LISTAR_PARCELAS)
    if success:
        success_count += 1
    print()
    
    # Substituir marcar_parcela_paga
    print("2️⃣ Substituindo marcar_parcela_paga...")
    content, success = replace_function(content, 'marcar_parcela_paga', FUNCAO_MARCAR_PARCELA)
    if success:
        success_count += 1
    print()
    
    # Salvar arquivo corrigido
    if success_count > 0:
        print("💾 Salvando app.py corrigido...")
        with open(app_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        new_lines = len(content.split('\n'))
        print(f"   ✅ Arquivo salvo ({new_lines} linhas)")
        print()
        
        # Resumo
        print("="*80)
        print("✅ CORREÇÕES APLICADAS COM SUCESSO!")
        print("="*80)
        print()
        print(f"📊 Resumo:")
        print(f"   - Funções corrigidas: {success_count}/2")
        print(f"   - Backup salvo em: {backup_path}")
        print(f"   - Arquivo corrigido: {app_path}")
        print()
        print("🚀 Próximos passos:")
        print("   1. Revise as mudanças: git diff app.py")
        print("   2. Teste localmente se possível")
        print("   3. Commit: git add app.py")
        print("   4. Commit: git commit -m 'fix: Corrige funções de pagamento parcelado'")
        print("   5. Deploy: git push origin main")
        print()
        print("⚠️ Se algo der errado, restaure o backup:")
        print(f"   cp {backup_path} app.py")
        print()
    else:
        print("❌ Nenhuma correção foi aplicada")
        print("   Verifique se as funções existem no app.py")
        sys.exit(1)


if __name__ == '__main__':
    main()
