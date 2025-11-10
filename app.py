# ============================================================================
# CORREÇÃO: Quebra Automática de Texto na Coluna PIX do PDF
# ============================================================================
# Este arquivo contém as CORREÇÕES ESPECÍFICAS para aplicar no app.py
# 
# LOCALIZE e SUBSTITUA estes trechos no seu arquivo app.py atual
# ============================================================================

# ----------------------------------------------------------------------------
# CORREÇÃO 1: Importar Paragraph no início do arquivo (se ainda não tiver)
# ----------------------------------------------------------------------------
# Certifique-se que esta linha está no topo do arquivo:

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

# ----------------------------------------------------------------------------
# CORREÇÃO 2: Seção "Resumo Urgente" (Próximos 7 dias)
# ----------------------------------------------------------------------------
# Procure por: "RESUMO - Próximos 7 dias" no código
# Substitua o trecho que monta a tabela por este código:

        # RESUMO - Próximos 7 dias
        if pagamentos_resumo:
            section_title = Paragraph("<b>RESUMO - Próximos 7 dias</b><br/><font size=9>(Pagamentos urgentes que vencem nos próximos 7 dias)</font>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            data_resumo = [['Descrição', 'Fornecedor', 'PIX', 'Valor', 'Vencimento', 'Status']]
            
            for pag in pagamentos_resumo:
                # Determinar texto do status
                if pag['status'] == 'vencido':
                    status_text = '🔴 VENCIDO'
                elif pag['status'] == 'vence_hoje':
                    status_text = '🟡 HOJE'
                else:
                    status_text = '🟢 Próximos 7'
                
                # Criar Paragraph para PIX com quebra automática
                pix_value = pag['pix'] if pag['pix'] and pag['pix'] != '-' else '-'
                pix_paragraph = Paragraph(pix_value, styles['Normal'])
                
                data_resumo.append([
                    pag['descricao'][:25],
                    pag['fornecedor'][:15],
                    pix_paragraph,  # ← PARAGRAPH COM QUEBRA AUTOMÁTICA
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y'),
                    status_text
                ])
            
            # Ajustar larguras: PIX agora tem 3.5cm
            table = Table(data_resumo, colWidths=[4.5*cm, 2.5*cm, 3.5*cm, 2.5*cm, 2.5*cm, 2*cm])
            
            # Estilo da tabela com WORDWRAP na coluna PIX
            table.setStyle(TableStyle([
                # Cabeçalho
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dc3545')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                
                # Corpo da tabela
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fff3cd')]),
                
                # QUEBRA AUTOMÁTICA E ALINHAMENTO VERTICAL NA COLUNA PIX
                ('VALIGN', (2, 1), (2, -1), 'MIDDLE'),  # Coluna PIX (índice 2)
                ('WORDWRAP', (2, 1), (2, -1), True),    # Quebra automática
            ]))
            
            elements.append(table)
            elements.append(Spacer(1, 0.8*cm))

# ----------------------------------------------------------------------------
# CORREÇÃO 3: Seção "Pagamentos Futuros" (Após 7 dias)
# ----------------------------------------------------------------------------
# Procure por: "Pagamentos Futuros" no código
# Substitua o trecho que monta a tabela por este código:

        # Seção: Pagamentos Futuros (Após 7 dias)
        # Contar seções anteriores para numeração correta
        secao_numero = 1 if not pagamentos_resumo else 2
        
        if pagamentos_futuros_normais:
            section_title = Paragraph(f"<b>{secao_numero}. Pagamentos Futuros</b><br/><font size=9>(Após 7 dias)</font>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            data_futuros = [['Descrição', 'Fornecedor', 'PIX', 'Valor', 'Vencimento']]
            
            # Adicionar pagamentos futuros (após 7 dias)
            for pag in pagamentos_futuros_normais:
                # Criar Paragraph para PIX com quebra automática
                pix_value = pag['pix'] if pag['pix'] and pag['pix'] != '-' else '-'
                pix_paragraph = Paragraph(pix_value, styles['Normal'])
                
                data_futuros.append([
                    pag['descricao'][:25],
                    pag['fornecedor'][:15],
                    pix_paragraph,  # ← PARAGRAPH COM QUEBRA AUTOMÁTICA
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y')
                ])
            
            # Ajustar larguras: PIX agora tem 3.5cm
            table = Table(data_futuros, colWidths=[5*cm, 3*cm, 3.5*cm, 2.5*cm, 2.5*cm])
            
            # Estilo da tabela com WORDWRAP na coluna PIX
            table.setStyle(TableStyle([
                # Cabeçalho
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#007bff')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                
                # Corpo da tabela
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                
                # QUEBRA AUTOMÁTICA E ALINHAMENTO VERTICAL NA COLUNA PIX
                ('VALIGN', (2, 1), (2, -1), 'MIDDLE'),  # Coluna PIX (índice 2)
                ('WORDWRAP', (2, 1), (2, -1), True),    # Quebra automática
            ]))
            
            elements.append(table)
            elements.append(Spacer(1, 0.8*cm))

# ============================================================================
# RESUMO DAS MUDANÇAS:
# ============================================================================
# 
# 1. PIX agora usa Paragraph() em vez de string simples
# 2. Largura da coluna PIX: 3cm → 3.5cm
# 3. Texto do PIX NÃO é mais cortado ([:15] ou [:20])
# 4. Adicionado WORDWRAP para quebra automática
# 5. Adicionado VALIGN='MIDDLE' para centralização vertical
# 
# RESULTADO: PIX completo e quebrando automaticamente quando necessário!
# ============================================================================
