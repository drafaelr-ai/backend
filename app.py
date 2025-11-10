# ============================================================
# ALTERAÇÕES COMPLETAS NO app.py PARA USAR forma_pagamento
# ============================================================

# ========================================
# 1️⃣ MODELO PagamentoServico (Linha 196)
# ========================================

# ADICIONAR ESTA LINHA após a linha 196 (tipo_pagamento):
    forma_pagamento = db.Column(db.String(20), nullable=True)  # PIX, Boleto, TED, Dinheiro, etc

# ========================================
# 2️⃣ MÉTODO to_dict() (Linha 207)
# ========================================

# ADICIONAR ESTA LINHA após "tipo_pagamento": self.tipo_pagamento,
            "forma_pagamento": self.forma_pagamento,

# ========================================
# 3️⃣ ROTA POST /servicos/<id>/pagamentos (Linha 1141-1151)
# ========================================

# SUBSTITUIR O BLOCO INTEIRO de criação do novo_pagamento:

        novo_pagamento = PagamentoServico(
            servico_id=servico_id,
            data=datetime.date.fromisoformat(dados['data']),
            data_vencimento=datetime.date.fromisoformat(dados['data_vencimento']) if dados.get('data_vencimento') else None,
            valor_total=valor_total, 
            valor_pago=valor_pago, 
            status=status,
            tipo_pagamento=tipo_pagamento,
            forma_pagamento=dados.get('forma_pagamento'),  # NOVA LINHA
            prioridade=int(dados.get('prioridade', 0)),
            fornecedor=dados.get('fornecedor') 
        )

# ========================================
# 4️⃣ RELATÓRIO DO CRONOGRAMA (Linhas 3462-3472)
# ========================================

# SUBSTITUIR O BLOCO COMPLETO:

                    # Determinar descrição do tipo (mão de obra ou material)
                    tipo_desc = pag_serv.tipo_pagamento.replace('_', ' ').title() if pag_serv.tipo_pagamento else ''
                    
                    # Determinar forma de pagamento (PIX, Boleto, TED, etc)
                    forma_pag = pag_serv.forma_pagamento if pag_serv.forma_pagamento else '-'
                    
                    pag_dict = {
                        'descricao': f"{servico.nome} - {tipo_desc}",
                        'fornecedor': pag_serv.fornecedor,
                        'valor': valor_pendente,
                        'data_vencimento': pag_serv.data_vencimento,
                        'tipo_pagamento': forma_pag,  # MUDOU: agora usa forma_pagamento
                        'status': 'Previsto' if pag_serv.data_vencimento >= hoje else 'Vencido'
                    }

# ========================================
# 5️⃣ VALIDAÇÃO OPCIONAL - Adicionar após linha 1135
# ========================================

# Se quiser validar as formas de pagamento aceitas, adicione:

        # Validar forma_pagamento se fornecida
        forma_pagamento = dados.get('forma_pagamento')
        if forma_pagamento:
            formas_validas = ['PIX', 'Boleto', 'TED', 'Dinheiro', 'Cartão', 'Cheque']
            if forma_pagamento not in formas_validas:
                return jsonify({
                    "erro": f"Forma de pagamento inválida. Use: {', '.join(formas_validas)}"
                }), 400

# ============================================================
# 📝 RESUMO DAS ALTERAÇÕES
# ============================================================

"""
LOCALIZAÇÃO DAS MUDANÇAS:

✅ Linha ~196: Adicionar campo no modelo
✅ Linha ~207: Adicionar campo no to_dict()
✅ Linha ~1149: Adicionar campo ao criar pagamento (POST)
✅ Linha ~3462-3472: Usar forma_pagamento no relatório

TOTAL: 4 mudanças principais no código
"""

# ============================================================
# 🧪 TESTE RÁPIDO
# ============================================================

"""
Após fazer as alterações:

1. Reinicie o Railway:
   - Faça commit das mudanças
   - Push para o repositório
   - O Railway vai fazer redeploy automaticamente

2. Teste no frontend:
   - Criar um novo pagamento de serviço
   - Selecionar uma forma de pagamento (PIX, Boleto, etc)
   - Gerar o relatório do cronograma
   - Verificar se a coluna "Tipo" mostra a forma de pagamento

3. Dados antigos:
   - Pagamentos sem forma_pagamento aparecerão como "-"
   - Você pode editar manualmente no banco se necessário
"""
