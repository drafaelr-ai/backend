import io
import base64
import calendar
import logging
import traceback
from datetime import datetime, date, timedelta

from flask import Blueprint, jsonify, request, make_response, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import func

from extensions import db
from models.obra import Obra
from models.servico import Servico
from models.pagamento_servico import PagamentoServico
from models.pagamento_futuro import PagamentoFuturo
from models.lancamento import Lancamento
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from services.orcamento_service import resolver_orcamento_item_id
from models.parcela_individual import ParcelaIndividual
from models.pagamento_parcelado import PagamentoParcelado
from models.cronograma_etapa import CronogramaEtapa
from models.cronograma_obra import CronogramaObra
from models.agenda_demanda import AgendaDemanda
from models.boleto import Boleto
from models.servico_base import ServicoBase
from services import (
    get_current_user,
    user_has_access_to_obra,
    check_permission,
)
from utils import formatar_real
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

logger = logging.getLogger(__name__)
cronograma_bp = Blueprint('cronograma', __name__)

@cronograma_bp.route('/obras/<int:obra_id>/relatorio-cronograma-pdf', methods=['GET'])
@jwt_required()
def gerar_relatorio_cronograma_pdf(obra_id):
    """Gera um relatório PDF do cronograma financeiro de uma obra"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        # Buscar dados do cronograma
        hoje = date.today()
        
        pagamentos_futuros = PagamentoFuturo.query.filter_by(
            obra_id=obra_id
        ).order_by(PagamentoFuturo.data_vencimento).all()
        
        # Separar pagamentos em vencidos e previstos
        pagamentos_vencidos = []
        pagamentos_previstos = []
        
        for pag in pagamentos_futuros:
            if pag.status == 'Previsto' and pag.data_vencimento < hoje:
                pagamentos_vencidos.append(pag)
            elif pag.status == 'Previsto':
                pagamentos_previstos.append(pag)
        
        # NOVO: Buscar também pagamentos de serviços pendentes
        pagamentos_servicos_pendentes = []
        pagamentos_servicos_vencidos = []
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        for servico in servicos:
            pagamentos_servico = PagamentoServico.query.filter_by(
                servico_id=servico.id
            ).filter(
                PagamentoServico.valor_pago < PagamentoServico.valor_total
            ).all()
            
            for pag_serv in pagamentos_servico:
                valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
                if valor_pendente > 0 and pag_serv.data_vencimento:
                    # Determinar descrição do tipo (mão de obra ou material)
                    tipo_desc = pag_serv.tipo_pagamento.replace('_', ' ').title() if pag_serv.tipo_pagamento else ''
                    
                    # Determinar forma de pagamento (PIX, Boleto, TED, etc)
                    forma_pag = pag_serv.forma_pagamento if pag_serv.forma_pagamento else None
                    
                    # Determinar PIX - agora o pagamento tem seu próprio campo PIX
                    pix_display = pag_serv.pix if pag_serv.pix else '-'
                    
                    # Montar descrição (removemos a forma da descrição já que terá coluna própria)
                    descricao_completa = f"{servico.nome} - {tipo_desc}"
                    
                    pag_dict = {
                        'descricao': descricao_completa,
                        'fornecedor': pag_serv.fornecedor,
                        'pix': pix_display,  # Incluir PIX/forma de pagamento
                        'valor': valor_pendente,
                        'data_vencimento': pag_serv.data_vencimento,
                        'tipo_pagamento': '-',
                        'status': 'Previsto' if pag_serv.data_vencimento >= hoje else 'Vencido'
                    }
                    
                    if pag_serv.data_vencimento < hoje:
                        pagamentos_servicos_vencidos.append(pag_dict)
                    else:
                        pagamentos_servicos_pendentes.append(pag_dict)
        
        pagamentos_parcelados = PagamentoParcelado.query.filter_by(
            obra_id=obra_id
        ).all()
        
        # Buscar parcelas de todos os pagamentos parcelados
        todas_parcelas = []
        for pag_parcelado in pagamentos_parcelados:
            parcelas = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=pag_parcelado.id
            ).order_by(ParcelaIndividual.numero_parcela).all()
            todas_parcelas.extend(parcelas)
        
        # Buscar boletos da obra
        try:
            boletos_obra = Boleto.query.filter_by(obra_id=obra_id).order_by(Boleto.data_vencimento.asc()).all()
        except Exception:
            boletos_obra = []
        
        # Criar o PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        secao_numero = 0  # Contador para numeração dinâmica das seções
        
        # Título
        title_style = styles['Title']
        title = Paragraph(f"<b>Relatório do Cronograma Financeiro</b><br/>{obra.nome}", title_style)
        elements.append(title)
        elements.append(Spacer(1, 0.5*cm))
        
        # Informações da obra
        info_style = styles['Normal']
        info_text = f"<b>Cliente:</b> {obra.cliente or 'N/A'}<br/>"
        info_text += f"<b>Data do Relatório:</b> {date.today().strftime('%d/%m/%Y')}"
        elements.append(Paragraph(info_text, info_style))
        elements.append(Spacer(1, 0.5*cm))
        
        # Seção: RESUMO - Atenção Urgente (Vencidos + Próximos 7 dias)
        hoje = date.today()
        limite_7_dias = hoje + timedelta(days=7)
        
        # Separar pagamentos por urgência
        pagamentos_resumo = []  # Vencidos + próximos 7 dias
        pagamentos_futuros_normais = []  # Após 7 dias
        
        # Adicionar vencidos ao resumo
        for pag in pagamentos_vencidos:
            pagamentos_resumo.append({
                'descricao': pag.descricao,
                'fornecedor': pag.fornecedor if pag.fornecedor else '-',
                'pix': pag.pix if pag.pix else '-',  # Chave PIX do pagamento
                'valor': pag.valor,
                'vencimento': pag.data_vencimento,
                'status': 'Vencido',
                'urgente': True
            })
        
        # Adicionar serviços vencidos ao resumo
        for pag_serv in pagamentos_servicos_vencidos:
            pagamentos_resumo.append({
                'descricao': pag_serv['descricao'],
                'fornecedor': pag_serv['fornecedor'] if pag_serv['fornecedor'] else '-',
                'pix': pag_serv['pix'],  # PIX já está no dicionário
                'valor': pag_serv['valor'],
                'vencimento': pag_serv['data_vencimento'],
                'status': 'Vencido',
                'urgente': True
            })
        
        # Classificar pagamentos previstos (únicos)
        for pag in pagamentos_previstos:
            if pag.data_vencimento <= limite_7_dias:
                pagamentos_resumo.append({
                    'descricao': pag.descricao,
                    'fornecedor': pag.fornecedor if pag.fornecedor else '-',
                    'pix': pag.pix if pag.pix else '-',  # Chave PIX do pagamento
                    'valor': pag.valor,
                    'vencimento': pag.data_vencimento,
                    'status': 'Próximos 7 dias',
                    'urgente': True
                })
            else:
                pagamentos_futuros_normais.append({
                    'descricao': pag.descricao,
                    'fornecedor': pag.fornecedor if pag.fornecedor else '-',
                    'tipo_pagamento': '-',
                    'valor': pag.valor,
                    'vencimento': pag.data_vencimento,
                    'status': pag.status
                })
        
        # Classificar pagamentos de serviços pendentes
        for pag_serv in pagamentos_servicos_pendentes:
            if pag_serv['data_vencimento'] <= limite_7_dias:
                pagamentos_resumo.append({
                    'descricao': pag_serv['descricao'],
                    'fornecedor': pag_serv['fornecedor'] if pag_serv['fornecedor'] else '-',
                    'pix': pag_serv['pix'],  # PIX já está no dicionário
                    'valor': pag_serv['valor'],
                    'vencimento': pag_serv['data_vencimento'],
                    'status': 'Próximos 7 dias',
                    'urgente': True
                })
            else:
                pagamentos_futuros_normais.append({
                    'descricao': pag_serv['descricao'],
                    'fornecedor': pag_serv['fornecedor'] if pag_serv['fornecedor'] else '-',
                    'tipo_pagamento': pag_serv['tipo_pagamento'],
                    'valor': pag_serv['valor'],
                    'vencimento': pag_serv['data_vencimento'],
                    'status': pag_serv['status']
                })
        
        # Ordenar resumo por data (mais antigos primeiro)
        pagamentos_resumo.sort(key=lambda x: x['vencimento'])
        
        # Mostrar seção RESUMO se houver pagamentos urgentes
        if pagamentos_resumo:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. RESUMO</b><br/><font size=9>(Vencidos e próximos 7 dias)</font>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            data_resumo = [['Descrição', 'Fornecedor', 'PIX', 'Valor', 'Vencimento', 'Status']]
            
            # Estilo para células com quebra de linha (criar novo estilo, não modificar o global)
            cell_style_resumo = ParagraphStyle(
                'CellStyleResumo',
                parent=styles['Normal'],
                fontSize=8,
                leading=10,
                alignment=TA_LEFT
            )
            
            for pag in pagamentos_resumo:
                # Usar Paragraph para permitir quebra de linha em todas as colunas de texto
                descricao_para = Paragraph(pag['descricao'], cell_style_resumo)
                fornecedor_para = Paragraph(pag['fornecedor'], cell_style_resumo)
                pix_para = Paragraph(pag['pix'] if pag['pix'] != '-' else '-', cell_style_resumo)
                status_para = Paragraph(pag['status'], cell_style_resumo)
                
                data_resumo.append([
                    descricao_para,  # Usar Paragraph para quebra automática
                    fornecedor_para,  # Usar Paragraph para quebra automática
                    pix_para,  # Usar Paragraph para quebra automática
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y'),
                    status_para  # Usar Paragraph para quebra automática
                ])
            
            # Ajustar larguras das colunas (agora são 6 colunas)
            table = Table(data_resumo, colWidths=[5*cm, 3*cm, 2.5*cm, 2.5*cm, 2.5*cm, 2*cm], repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff6f00')),  # Laranja escuro
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # MIDDLE para melhor seleção de texto
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 1), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fff3e0')),  # Fundo laranja claro
            ]))
            elements.append(table)
            elements.append(Spacer(1, 0.5*cm))
        
        # Seção: Pagamentos Futuros (Após 7 dias)
        if pagamentos_futuros_normais:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. Pagamentos Futuros</b><br/><font size=9>(Após 7 dias)</font>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            data_futuros = [['Descrição', 'Fornecedor', 'Valor', 'Vencimento']]
            
            # Estilo para células com quebra de linha (criar novo estilo)
            cell_style_futuros = ParagraphStyle(
                'CellStyleFuturos',
                parent=styles['Normal'],
                fontSize=8,
                leading=10,
                alignment=TA_LEFT
            )
            
            # Adicionar pagamentos futuros (após 7 dias)
            for pag in pagamentos_futuros_normais:
                # Usar Paragraph para permitir quebra de linha
                descricao_para = Paragraph(pag['descricao'], cell_style_futuros)
                fornecedor_para = Paragraph(pag['fornecedor'], cell_style_futuros)
                
                data_futuros.append([
                    descricao_para,  # Usar Paragraph para quebra automática
                    fornecedor_para,  # Usar Paragraph para quebra automática
                    formatar_real(pag['valor']),
                    pag['vencimento'].strftime('%d/%m/%Y')
                ])
            
            # Ajustar larguras sem coluna Tipo e Status
            table = Table(data_futuros, colWidths=[7.5*cm, 4*cm, 2.5*cm, 3*cm], repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a90e2')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # MIDDLE para melhor seleção de texto
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 1), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white])
            ]))
            elements.append(table)
            elements.append(Spacer(1, 0.5*cm))
        
        # Seção: Pagamentos Parcelados
        # FILTRAR: Mostrar apenas pagamentos parcelados com parcelas pendentes (não totalmente pagos)
        pagamentos_parcelados_pendentes = []
        for pag_parcelado in pagamentos_parcelados:
            # Verificar se há pelo menos uma parcela não paga
            parcelas = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=pag_parcelado.id
            ).all()
            
            # Verificar se existe alguma parcela com status diferente de 'Pago'
            tem_parcela_pendente = any(p.status != 'Pago' for p in parcelas)
            
            if tem_parcela_pendente:
                pagamentos_parcelados_pendentes.append(pag_parcelado)
        
        if pagamentos_parcelados_pendentes:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. Pagamentos Parcelados</b>", styles['Heading2'])
            elements.append(section_title)
            elements.append(Spacer(1, 0.3*cm))
            
            for pag_parcelado in pagamentos_parcelados_pendentes:
                # Buscar parcelas deste pagamento para calcular o total
                parcelas = ParcelaIndividual.query.filter_by(
                    pagamento_parcelado_id=pag_parcelado.id
                ).order_by(ParcelaIndividual.numero_parcela).all()
                
                # Calcular valor total real de todas as parcelas
                valor_total_parcelas = sum(p.valor_parcela for p in parcelas)
                
                # Subtítulo do pagamento parcelado - mostra apenas o valor total
                sub_title = Paragraph(
                    f"<b>{pag_parcelado.descricao}</b> - Total: {formatar_real(valor_total_parcelas)} | Fornecedor: {pag_parcelado.fornecedor or '-'}",
                    styles['Heading3']
                )
                elements.append(sub_title)
                elements.append(Spacer(1, 0.2*cm))
                
                if parcelas:
                    data_parcelas = [['Parcela', 'Valor', 'Vencimento', 'Status', 'Tipo', 'PIX/Código', 'Pago em']]
                    
                    # Variável para controlar cores
                    row_colors = []
                    
                    # Obter forma de pagamento e PIX do pagamento parcelado (pai) de forma defensiva
                    try:
                        forma_pag = pag_parcelado.forma_pagamento if hasattr(pag_parcelado, 'forma_pagamento') and pag_parcelado.forma_pagamento else 'PIX'
                    except Exception:
                        forma_pag = 'PIX'
                    
                    try:
                        pix_raw = pag_parcelado.pix if hasattr(pag_parcelado, 'pix') and pag_parcelado.pix else ''
                    except Exception:
                        pix_raw = ''
                    
                    for parcela in parcelas:
                        # Determinar se está vencida
                        status_display = parcela.status
                        if parcela.status == 'Previsto' and parcela.data_vencimento < hoje:
                            status_display = 'Vencido'
                            row_colors.append(colors.HexColor('#ffcdd2'))  # Vermelho claro
                        else:
                            row_colors.append(colors.whitesmoke if len(row_colors) % 2 == 0 else colors.white)
                        
                        # Determinar valor da coluna "PIX/Código"
                        # Priorizar código de barras da parcela (boleto), senão usar PIX do pagamento
                        try:
                            codigo_barras = parcela.codigo_barras if hasattr(parcela, 'codigo_barras') and parcela.codigo_barras else ''
                        except Exception:
                            codigo_barras = ''
                        if codigo_barras:
                            # Truncar código de barras (mostrar últimos 12 dígitos)
                            pix_codigo_display = '...' + codigo_barras[-12:] if len(codigo_barras) > 12 else codigo_barras
                        elif pix_raw:
                            # Truncar PIX longo (máx 16 caracteres)
                            pix_codigo_display = (pix_raw[:14] + '..') if len(pix_raw) > 16 else pix_raw
                        else:
                            pix_codigo_display = '-'
                        
                        # Determinar valor da coluna "Pago em"
                        pago_em_display = parcela.data_pagamento.strftime('%d/%m/%Y') if parcela.data_pagamento else '-'
                        
                        data_parcelas.append([
                            f"{parcela.numero_parcela}/{pag_parcelado.numero_parcelas}",
                            formatar_real(parcela.valor_parcela),
                            parcela.data_vencimento.strftime('%d/%m/%Y'),
                            status_display,
                            pag_parcelado.periodicidade or '-',  # Tipo = Periodicidade
                            pix_codigo_display,  # PIX ou Código de Barras (truncado)
                            pago_em_display
                        ])
                    
                    # Ajustar larguras: Parcela, Valor, Vencimento, Status, Tipo, PIX/Código, Pago em
                    table_parcelas = Table(data_parcelas, colWidths=[1.5*cm, 2*cm, 2.2*cm, 1.8*cm, 1.8*cm, 3*cm, 2.2*cm], repeatRows=1)
                    
                    style_list = [
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#5cb85c')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # MIDDLE para melhor seleção de texto
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 9),
                        ('TOPPADDING', (0, 1), (-1, -1), 6),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                        ('LEFTPADDING', (0, 0), (-1, -1), 4),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                        ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ]
                    
                    # Adicionar cores de fundo linha por linha
                    for i, color in enumerate(row_colors, start=1):
                        style_list.append(('BACKGROUND', (0, i), (-1, i), color))
                        if color == colors.HexColor('#ffcdd2'):  # Se for vencida
                            style_list.append(('TEXTCOLOR', (3, i), (3, i), colors.HexColor('#d32f2f')))  # Status em vermelho
                    
                    table_parcelas.setStyle(TableStyle(style_list))
                    elements.append(table_parcelas)
                    elements.append(Spacer(1, 0.3*cm))
        
        # Seção: Boletos
        if boletos_obra:
            secao_numero += 1
            section_title = Paragraph(f"<b>{secao_numero}. Boletos</b>", styles['Heading2'])
            
            # Separar boletos por status
            boletos_pendentes = [b for b in boletos_obra if b.status == 'Pendente' and b.data_vencimento >= hoje]
            boletos_vencidos = [b for b in boletos_obra if b.status == 'Vencido' or (b.status == 'Pendente' and b.data_vencimento < hoje)]
            boletos_pagos = [b for b in boletos_obra if b.status == 'Pago']
            
            # Estilo para células com quebra de linha
            cell_style = ParagraphStyle(
                'CellStyle',
                parent=styles['Normal'],
                fontSize=8,
                leading=10,
                alignment=TA_LEFT
            )
            cell_style_center = ParagraphStyle(
                'CellStyleCenter',
                parent=styles['Normal'],
                fontSize=8,
                leading=10,
                alignment=TA_CENTER
            )
            
            # Tabela de boletos pendentes
            if boletos_pendentes or boletos_vencidos:
                data_boletos = [['Descrição', 'Beneficiário', 'Vencimento', 'Valor', 'Status', 'Código']]
                row_colors_boletos = []
                
                # Vencidos primeiro
                for boleto in boletos_vencidos:
                    codigo_truncado = ('...' + boleto.codigo_barras[-12:]) if boleto.codigo_barras and len(boleto.codigo_barras) > 12 else (boleto.codigo_barras or '-')
                    data_boletos.append([
                        Paragraph(boleto.descricao or '-', cell_style),
                        Paragraph(boleto.beneficiario or '-', cell_style),
                        boleto.data_vencimento.strftime('%d/%m/%Y'),
                        formatar_real(boleto.valor),
                        'Vencido',
                        Paragraph(codigo_truncado, cell_style)
                    ])
                    row_colors_boletos.append(colors.HexColor('#ffcdd2'))  # Vermelho claro
                
                # Pendentes
                for boleto in boletos_pendentes:
                    dias_para_vencer = (boleto.data_vencimento - hoje).days
                    codigo_truncado = ('...' + boleto.codigo_barras[-12:]) if boleto.codigo_barras and len(boleto.codigo_barras) > 12 else (boleto.codigo_barras or '-')
                    
                    # Cor baseada na urgência
                    if dias_para_vencer <= 3:
                        cor = colors.HexColor('#ffcc80')  # Laranja claro
                    elif dias_para_vencer <= 7:
                        cor = colors.HexColor('#fff9c4')  # Amarelo claro
                    else:
                        cor = colors.whitesmoke if len(row_colors_boletos) % 2 == 0 else colors.white
                    
                    data_boletos.append([
                        Paragraph(boleto.descricao or '-', cell_style),
                        Paragraph(boleto.beneficiario or '-', cell_style),
                        boleto.data_vencimento.strftime('%d/%m/%Y'),
                        formatar_real(boleto.valor),
                        f'{dias_para_vencer}d' if dias_para_vencer >= 0 else 'Vencido',
                        Paragraph(codigo_truncado, cell_style)
                    ])
                    row_colors_boletos.append(cor)
                
                table_boletos = Table(data_boletos, colWidths=[4*cm, 3.5*cm, 2*cm, 2*cm, 1.3*cm, 3.2*cm], repeatRows=1)
                
                style_boletos = [
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#607d8b')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),  # MIDDLE para melhor seleção de texto
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('TOPPADDING', (0, 1), (-1, -1), 6),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('LEFTPADDING', (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ]
                
                for i, color in enumerate(row_colors_boletos, start=1):
                    style_boletos.append(('BACKGROUND', (0, i), (-1, i), color))
                    if color == colors.HexColor('#ffcdd2'):
                        style_boletos.append(('TEXTCOLOR', (4, i), (4, i), colors.HexColor('#d32f2f')))
                
                table_boletos.setStyle(TableStyle(style_boletos))
                
                # Usar KeepTogether para manter título e tabela na mesma página (se couber)
                elements.append(KeepTogether([section_title, Spacer(1, 0.3*cm), table_boletos]))
                elements.append(Spacer(1, 0.3*cm))
            else:
                # Se não tem tabela, só adiciona o título
                elements.append(section_title)
                elements.append(Spacer(1, 0.3*cm))
            
            # Resumo de boletos pagos
            if boletos_pagos:
                total_boletos_pagos = sum(b.valor for b in boletos_pagos)
                info_pagos = Paragraph(
                    f"<i>Boletos pagos: {len(boletos_pagos)} | Total: {formatar_real(total_boletos_pagos)}</i>",
                    styles['Normal']
                )
                elements.append(info_pagos)
                elements.append(Spacer(1, 0.3*cm))
        
        # Seção: Resumo Financeiro
        secao_numero += 1
        section_title_resumo = Paragraph(f"<b>{secao_numero}. Resumo Financeiro</b>", styles['Heading2'])
        
        # Calcular totais
        total_futuros = sum(pag.valor for pag in pagamentos_previstos)
        total_vencidos_unicos = sum(pag.valor for pag in pagamentos_vencidos)
        
        # Adicionar pagamentos de serviços
        total_servicos_pendentes = sum(pag_serv['valor'] for pag_serv in pagamentos_servicos_pendentes)
        total_servicos_vencidos = sum(pag_serv['valor'] for pag_serv in pagamentos_servicos_vencidos)
        
        # Parcelas
        total_parcelados = sum(
            parcela.valor_parcela for parcela in todas_parcelas if parcela.status == 'Previsto' and parcela.data_vencimento >= hoje
        )
        total_parcelas_vencidas = sum(
            parcela.valor_parcela for parcela in todas_parcelas if parcela.status == 'Previsto' and parcela.data_vencimento < hoje
        )
        total_pago_parcelas = sum(
            parcela.valor_parcela for parcela in todas_parcelas if parcela.status == 'Pago'
        )
        
        # Boletos
        total_boletos_pendentes = sum(b.valor for b in boletos_obra if b.status == 'Pendente' and b.data_vencimento >= hoje) if boletos_obra else 0
        total_boletos_vencidos = sum(b.valor for b in boletos_obra if b.status == 'Vencido' or (b.status == 'Pendente' and b.data_vencimento < hoje)) if boletos_obra else 0
        total_boletos_pagos = sum(b.valor for b in boletos_obra if b.status == 'Pago') if boletos_obra else 0
        
        total_geral_vencido = total_vencidos_unicos + total_servicos_vencidos + total_parcelas_vencidas + total_boletos_vencidos
        total_geral_previsto = total_futuros + total_servicos_pendentes + total_parcelados + total_boletos_pendentes
        total_geral = total_geral_vencido + total_geral_previsto
        
        resumo_data = [
            ['Descrição', 'Valor'],
            ['Total de Pagamentos Futuros (Previstos)', formatar_real(total_futuros)],
            ['Total de Pagamentos de Serviços (Previstos)', formatar_real(total_servicos_pendentes)],
            ['Total de Parcelas (Previstas)', formatar_real(total_parcelados)],
            ['Total de Boletos (Pendentes)', formatar_real(total_boletos_pendentes)],
            ['', ''],  # Linha em branco
            ['Total de Pagamentos VENCIDOS (Únicos)', formatar_real(total_vencidos_unicos)],
            ['Total de Pagamentos de Serviços VENCIDOS', formatar_real(total_servicos_vencidos)],
            ['Total de Parcelas VENCIDAS', formatar_real(total_parcelas_vencidas)],
            ['Total de Boletos VENCIDOS', formatar_real(total_boletos_vencidos)],
            ['', ''],  # Linha em branco
            ['Total de Parcelas PAGAS', formatar_real(total_pago_parcelas)],
            ['Total de Boletos PAGOS', formatar_real(total_boletos_pagos)],
            ['', ''],  # Linha em branco
            ['TOTAL VENCIDO ⚠️', formatar_real(total_geral_vencido)],
            ['TOTAL PREVISTO', formatar_real(total_geral_previsto)],
            ['TOTAL GERAL (A Pagar)', formatar_real(total_geral)]
        ]
        
        table_resumo = Table(resumo_data, colWidths=[12*cm, 5*cm], repeatRows=1)
        
        style_list = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff9800')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            # Destacar linha TOTAL VENCIDO em vermelho
            ('BACKGROUND', (0, 11), (-1, 11), colors.HexColor('#ffcdd2')),
            ('TEXTCOLOR', (0, 11), (-1, 11), colors.HexColor('#d32f2f')),
            ('FONTNAME', (0, 11), (-1, 11), 'Helvetica-Bold'),
            # Destacar linha TOTAL GERAL em laranja escuro
            ('BACKGROUND', (0, 13), (-1, 13), colors.HexColor('#ff9800')),
            ('TEXTCOLOR', (0, 13), (-1, 13), colors.whitesmoke),
            ('FONTNAME', (0, 13), (-1, 13), 'Helvetica-Bold'),
        ]
        
        table_resumo.setStyle(TableStyle(style_list))
        
        # Usar KeepTogether para manter título e tabela juntos
        elements.append(KeepTogether([section_title_resumo, Spacer(1, 0.3*cm), table_resumo]))
        
        # Construir o PDF
        doc.build(elements)
        buffer.seek(0)
        
        logger.info(f"--- [LOG] PDF do cronograma gerado para obra {obra_id} ---")
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Cronograma_{obra.nome.replace(' ', '_')}_{date.today()}.pdf",
            mimetype='application/pdf'
        )
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] ao gerar PDF do cronograma: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500
# --- FIM DO ENDPOINT DE RELATÓRIO DO CRONOGRAMA ---


# --- ALIAS: ROTA ALTERNATIVA PARA PDF DO CRONOGRAMA (USADA PELO FRONTEND) ---
@cronograma_bp.route('/obras/<int:obra_id>/cronograma-financeiro/pdf', methods=['GET'])
@jwt_required()
def gerar_pdf_cronograma_financeiro_alias(obra_id):
    """Alias para rota de PDF do cronograma - usado pelo frontend"""
    return gerar_relatorio_cronograma_pdf(obra_id)

# --- MUDANÇA 3: NOVO ENDPOINT - INSERIR PAGAMENTO ---
@cronograma_bp.route('/obras/<int:obra_id>/inserir-pagamento', methods=['POST', 'OPTIONS'])
@jwt_required()
def inserir_pagamento(obra_id):
    """
    🆕 ENDPOINT UNIFICADO - Insere pagamentos (à vista ou parcelados) com vínculo opcional a serviços.
    
    Suporta:
    - Pagamentos à vista (Pago ou A Pagar)
    - Pagamentos parcelados (Semanal/Quinzenal/Mensal)
    - Vínculo opcional ao serviço
    - Atualização automática de % de conclusão do serviço
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"\n{'='*80}")
    logger.info(f"💰 INSERIR PAGAMENTO - Obra {obra_id}")
    logger.info(f"{'='*80}")
    
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json
        logger.info(f"📋 Dados recebidos: {dados}")
        
        # DEBUG: Verificar campos de entrada especificamente
        logger.info(f"🔍 DEBUG ENTRADA:")
        logger.info(f"   tem_entrada: {dados.get('tem_entrada')}")
        logger.info(f"   valor_entrada: {dados.get('valor_entrada')}")
        logger.info(f"   percentual_entrada: {dados.get('percentual_entrada')}")
        logger.info(f"   data_entrada: {dados.get('data_entrada')}")
        
        # Campos obrigatórios
        descricao = dados.get('descricao')
        valor_total = float(dados.get('valor', 0))
        tipo = dados.get('tipo')  # 'Material' ou 'Mão de Obra'
        status = dados.get('status', 'A Pagar')  # 'Pago' ou 'A Pagar'
        data = date.fromisoformat(dados.get('data'))
        
        # Campos opcionais
        servico_id = dados.get('servico_id')
        fornecedor = dados.get('fornecedor')
        data_vencimento = dados.get('data_vencimento')
        pix = dados.get('pix')
        prioridade = int(dados.get('prioridade', 0))
        
        # 🆕 NOVOS CAMPOS PARA PARCELAMENTO
        tipo_forma_pagamento = dados.get('tipo_forma_pagamento', 'avista')  # 'avista' ou 'parcelado'
        numero_parcelas = dados.get('numero_parcelas')
        periodicidade = dados.get('periodicidade')  # 'Semanal', 'Quinzenal', 'Mensal'
        data_primeira_parcela = dados.get('data_primeira_parcela')
        
        logger.info(f"   Tipo pagamento: {tipo_forma_pagamento}")
        logger.info(f"   Status: {status}")
        logger.info(f"   Serviço vinculado: {servico_id}")
        
        # ===== FLUXO PARCELADO =====
        if tipo_forma_pagamento == 'parcelado':
            logger.info(f"   📦 Criando pagamento PARCELADO")
            logger.info(f"      - Parcelas: {numero_parcelas}")
            logger.info(f"      - Periodicidade: {periodicidade}")
            
            if not numero_parcelas or not periodicidade or not data_primeira_parcela:
                return jsonify({"erro": "Parcelas, periodicidade e data da primeira parcela são obrigatórios para parcelamento"}), 400
            
            numero_parcelas = int(numero_parcelas)
            data_primeira = date.fromisoformat(data_primeira_parcela)
            
            # 🆕 Verificar se tem entrada
            tem_entrada = dados.get('tem_entrada', False)
            valor_entrada = float(dados.get('valor_entrada', 0)) if tem_entrada else 0
            data_entrada = dados.get('data_entrada')
            percentual_entrada = float(dados.get('percentual_entrada', 0)) if tem_entrada else 0
            
            # 🆕 Parcelas customizadas (boletos com valores/códigos por parcela)
            parcelas_customizadas = dados.get('parcelas_customizadas') or []
            usar_customizadas = (
                isinstance(parcelas_customizadas, list)
                and len(parcelas_customizadas) == numero_parcelas
                and all(p.get('valor') not in (None, '') for p in parcelas_customizadas)
            )
            if usar_customizadas:
                # valor_total passa a ser a soma real dos boletos + entrada
                soma_custom = round(sum(float(p['valor']) for p in parcelas_customizadas), 2)
                valor_total = round(soma_custom + valor_entrada, 2)

            # Calcular valor das parcelas (após entrada) — arredondado; o resíduo
            # de centavos é ajustado na ÚLTIMA parcela (ex.: 1000/3 = 333.33 +
            # 333.33 + 333.34).
            valor_restante = round(valor_total - valor_entrada, 2)
            valor_parcela = round(valor_restante / numero_parcelas, 2) if numero_parcelas > 0 else 0

            # Total de pagamentos = entrada (se houver) + parcelas
            total_pagamentos = numero_parcelas + (1 if tem_entrada and valor_entrada > 0 else 0)
            
            logger.info(f"   💰 Entrada: R$ {valor_entrada:.2f} ({percentual_entrada:.0f}%)")
            logger.info(f"   💰 Restante: R$ {valor_restante:.2f} em {numero_parcelas}x R$ {valor_parcela:.2f}")
            
            # Criar PagamentoParcelado
            novo_parcelado = PagamentoParcelado(
                obra_id=obra_id,
                descricao=descricao,
                fornecedor=fornecedor,
                servico_id=servico_id,
                segmento=tipo,  # 'Material' ou 'Mão de Obra'
                valor_total=valor_total,
                numero_parcelas=total_pagamentos,  # Incluir entrada no total
                valor_parcela=valor_parcela,
                data_primeira_parcela=data_primeira,
                periodicidade=periodicidade,
                parcelas_pagas=0,
                status='Ativo'
            )
            db.session.add(novo_parcelado)
            db.session.flush()
            
            # Vínculo com item do orçamento — via ORM, com validação explícita.
            oid, erro = resolver_orcamento_item_id(dados.get('orcamento_item_id'))
            if erro:
                db.session.rollback()
                logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (novo parcelado): {erro} ---")
                return jsonify({"erro": erro}), 400
            novo_parcelado.orcamento_item_id = oid
            
            logger.info(f"   ✅ PagamentoParcelado criado: ID={novo_parcelado.id}")
            
            # Gerar parcelas individuais
            from datetime import timedelta
            import calendar
            
            # 🆕 Criar parcela de ENTRADA (se houver). Se o pagamento já nasce
            # 'Pago', a entrada também nasce paga (antes ficava 'Previsto' e o
            # parcelamento ia pra 'Concluído' com pendência fantasma).
            if tem_entrada and valor_entrada > 0:
                data_entrada_parsed = date.fromisoformat(data_entrada) if data_entrada else data

                parcela_entrada = ParcelaIndividual(
                    pagamento_parcelado_id=novo_parcelado.id,
                    numero_parcela=0,  # Parcela 0 = Entrada
                    valor_parcela=round(valor_entrada, 2),
                    data_vencimento=data_entrada_parsed,
                    status='Pago' if status == 'Pago' else 'Previsto',
                    data_pagamento=data_entrada_parsed if status == 'Pago' else None,
                    forma_pagamento=pix if status == 'Pago' else None,
                    observacao=f'ENTRADA ({percentual_entrada:.0f}%)'
                )
                db.session.add(parcela_entrada)
                logger.info(f"      ✅ ENTRADA: R$ {valor_entrada:.2f} - {data_entrada_parsed}")

            soma_geradas = 0.0
            for i in range(1, numero_parcelas + 1):
                if usar_customizadas:
                    custom = parcelas_customizadas[i - 1]
                    valor_i = round(float(custom['valor']), 2)
                    try:
                        data_venc = date.fromisoformat(str(custom.get('data_vencimento'))[:10])
                    except (TypeError, ValueError):
                        data_venc = None
                    codigo_barras_i = (custom.get('codigo_barras') or '').strip() or None
                else:
                    valor_i = None  # calculado abaixo
                    data_venc = None
                    codigo_barras_i = None

                if data_venc is None:
                    # Calcular data de vencimento da parcela
                    if periodicidade == 'Semanal':
                        data_venc = data_primeira + timedelta(days=(i-1) * 7)
                    elif periodicidade == 'Quinzenal':
                        data_venc = data_primeira + timedelta(days=(i-1) * 15)
                    else:  # Mensal
                        month = data_primeira.month - 1 + (i-1)
                        year = data_primeira.year + month // 12
                        month = month % 12 + 1
                        day = min(data_primeira.day, calendar.monthrange(year, month)[1])
                        data_venc = date(year, month, day)

                if valor_i is None:
                    if i < numero_parcelas:
                        valor_i = valor_parcela
                    else:
                        # última parcela absorve o resíduo de centavos
                        valor_i = round(valor_restante - soma_geradas, 2)
                soma_geradas = round(soma_geradas + valor_i, 2)

                # Status da parcela
                if status == 'Pago':
                    parcela_status = 'Pago'
                    parcela_data_pagamento = data
                else:
                    parcela_status = 'Previsto'
                    parcela_data_pagamento = None

                nova_parcela = ParcelaIndividual(
                    pagamento_parcelado_id=novo_parcelado.id,
                    numero_parcela=i,
                    valor_parcela=valor_i,
                    data_vencimento=data_venc,
                    status=parcela_status,
                    data_pagamento=parcela_data_pagamento,
                    forma_pagamento=pix if status == 'Pago' else None,
                    codigo_barras=codigo_barras_i,
                )
                db.session.add(nova_parcela)
                logger.info(f"      ✅ Parcela {i}/{numero_parcelas}: R$ {valor_i:.2f} - {data_venc} ({parcela_status})")
            
            db.session.flush()
            
            # Se STATUS = PAGO, criar PagamentoServico para cada parcela
            if status == 'Pago' and servico_id:
                logger.info(f"   💰 Status=PAGO com serviço vinculado, criando PagamentoServico...")
                
                servico = Servico.query.get(servico_id)
                if servico:
                    # Determinar tipo_pagamento
                    tipo_pagamento = ('mao_de_obra' if tipo == 'Mão de Obra'
                                       else 'equipamento' if tipo == 'Equipamentos'
                                       else 'material')

                    # Criar UM PagamentoServico com valor total
                    novo_pag_servico = PagamentoServico(
                        servico_id=servico_id,
                        tipo_pagamento=tipo_pagamento,
                        valor_total=valor_total,
                        valor_pago=valor_total,
                        data=data,
                        status='Pago',
                        fornecedor=fornecedor,
                        prioridade=prioridade
                    )
                    db.session.add(novo_pag_servico)
                    db.session.flush()
                    
                    logger.info(f"      ✅ PagamentoServico criado: ID={novo_pag_servico.id}, valor={valor_total}")
                    
                    # Atualizar parcelas_pagas (todas as linhas, incluindo a entrada)
                    novo_parcelado.parcelas_pagas = total_pagamentos
                    novo_parcelado.status = 'Concluído'
                    
                    # Recalcular % do serviço
                    pagamentos = PagamentoServico.query.filter_by(servico_id=servico_id).all()
                    pagamentos_mao = [p for p in pagamentos if p.tipo_pagamento == 'mao_de_obra']
                    pagamentos_mat = [p for p in pagamentos if p.tipo_pagamento == 'material']
                    
                    if servico.valor_global_mao_de_obra > 0:
                        total_pago = sum(p.valor_pago for p in pagamentos_mao)
                        servico.percentual_conclusao_mao_obra = min(100, (total_pago / servico.valor_global_mao_de_obra) * 100)
                    
                    if servico.valor_global_material > 0:
                        total_pago = sum(p.valor_pago for p in pagamentos_mat)
                        servico.percentual_conclusao_material = min(100, (total_pago / servico.valor_global_material) * 100)
                    
                    logger.info(f"      ✅ Serviço atualizado: MO={servico.percentual_conclusao_mao_obra:.1f}%, MAT={servico.percentual_conclusao_material:.1f}%")
            
            elif status == 'Pago':
                # Status=Pago mas sem serviço vinculado
                novo_parcelado.parcelas_pagas = total_pagamentos
                novo_parcelado.status = 'Concluído'
                logger.info(f"   ✅ Todas as parcelas marcadas como pagas (sem vínculo ao serviço)")
            
            db.session.commit()
            logger.info(f"{'='*80}")
            logger.info(f"✅ SUCESSO: Pagamento parcelado criado")
            logger.info(f"{'='*80}\n")
            
            return jsonify({
                "mensagem": "Pagamento parcelado criado com sucesso",
                "pagamento_parcelado": novo_parcelado.to_dict()
            }), 201
        
        # ===== FLUXO À VISTA =====
        else:
            logger.info(f"   💵 Criando pagamento À VISTA")
            valor_pago = valor_total if status == 'Pago' else 0.0
            
            # CASO 1: STATUS "PAGO" COM SERVIÇO VINCULADO
            if servico_id and status == 'Pago':
                servico = Servico.query.get_or_404(servico_id)
                tipo_pagamento = ('mao_de_obra' if tipo == 'Mão de Obra'
                                   else 'equipamento' if tipo == 'Equipamentos'
                                   else 'material')

                novo_pagamento = PagamentoServico(
                    servico_id=servico_id,
                    tipo_pagamento=tipo_pagamento,
                    valor_total=valor_total,
                    valor_pago=valor_pago,
                    data=data,
                    data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else None,
                    status=status,
                    prioridade=prioridade,
                    fornecedor=fornecedor
                )
                db.session.add(novo_pagamento)
                db.session.flush()
                
                # Recalcular percentual do serviço
                pagamentos = PagamentoServico.query.filter_by(servico_id=servico_id).all()
                pagamentos_mao = [p for p in pagamentos if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_mat = [p for p in pagamentos if p.tipo_pagamento == 'material']
                
                if servico.valor_global_mao_de_obra > 0:
                    total_pago = sum(p.valor_pago for p in pagamentos_mao)
                    servico.percentual_conclusao_mao_obra = min(100, (total_pago / servico.valor_global_mao_de_obra) * 100)
                
                if servico.valor_global_material > 0:
                    total_pago = sum(p.valor_pago for p in pagamentos_mat)
                    servico.percentual_conclusao_material = min(100, (total_pago / servico.valor_global_material) * 100)
                
                db.session.commit()
                logger.info(f"   ✅ PagamentoServico PAGO criado: ID={novo_pagamento.id}")
                logger.info(f"{'='*80}\n")
                return jsonify(novo_pagamento.to_dict()), 201
            
            # CASO 2: STATUS "A PAGAR" COM SERVIÇO VINCULADO
            elif servico_id and status == 'A Pagar':
                servico = Servico.query.get_or_404(servico_id)
                
                novo_futuro = PagamentoFuturo(
                    obra_id=obra_id,
                    descricao=f"{descricao} (Serviço: {servico.nome})",
                    valor=valor_total,
                    data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else data,
                    fornecedor=fornecedor,
                    pix=pix,
                    observacoes=f"Vinculado ao serviço {servico.nome}",
                    status='Previsto',
                    servico_id=servico_id,
                    tipo=tipo
                )
                db.session.add(novo_futuro)
                db.session.flush()
                
                # Vínculo com item do orçamento — via ORM, com validação explícita.
                oid, erro = resolver_orcamento_item_id(dados.get('orcamento_item_id'))
                if erro:
                    db.session.rollback()
                    logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (novo pagamento futuro): {erro} ---")
                    return jsonify({"erro": erro}), 400
                novo_futuro.orcamento_item_id = oid
                
                db.session.commit()
                logger.info(f"   ✅ PagamentoFuturo criado: ID={novo_futuro.id}, orcamento_item_id={oid}")
                logger.info(f"{'='*80}\n")
                return jsonify(novo_futuro.to_dict()), 201
            
            # CASO 3: STATUS "A PAGAR" SEM SERVIÇO
            elif status == 'A Pagar':
                novo_futuro = PagamentoFuturo(
                    obra_id=obra_id,
                    descricao=descricao,
                    valor=valor_total,
                    data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else data,
                    fornecedor=fornecedor,
                    pix=pix,
                    observacoes=f"Tipo: {tipo}",
                    status='Previsto',
                    servico_id=None,
                    tipo=tipo
                )
                db.session.add(novo_futuro)
                db.session.flush()
                
                # Vínculo com item do orçamento — via ORM, com validação explícita.
                oid, erro = resolver_orcamento_item_id(dados.get('orcamento_item_id'))
                if erro:
                    db.session.rollback()
                    logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (novo pagamento futuro): {erro} ---")
                    return jsonify({"erro": erro}), 400
                novo_futuro.orcamento_item_id = oid
                
                db.session.commit()
                logger.info(f"   ✅ PagamentoFuturo criado: ID={novo_futuro.id}, orcamento_item_id={oid}")
                logger.info(f"{'='*80}\n")
                return jsonify(novo_futuro.to_dict()), 201
            
            # CASO 4: STATUS "PAGO" SEM SERVIÇO
            else:
                novo_lancamento = Lancamento(
                    obra_id=obra_id,
                    tipo=tipo,
                    descricao=descricao,
                    valor_total=valor_total,
                    valor_pago=valor_pago,
                    data=data,
                    data_vencimento=date.fromisoformat(data_vencimento) if data_vencimento else None,
                    status=status,
                    pix=pix,
                    prioridade=prioridade,
                    fornecedor=fornecedor
                )
                db.session.add(novo_lancamento)
                db.session.flush()
                
                # Vínculo com item do orçamento — via ORM, com validação explícita.
                oid, erro = resolver_orcamento_item_id(dados.get('orcamento_item_id'))
                if erro:
                    db.session.rollback()
                    logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (novo lancamento): {erro} ---")
                    return jsonify({"erro": erro}), 400
                novo_lancamento.orcamento_item_id = oid
                
                db.session.commit()
                logger.info(f"   ✅ Lançamento criado: ID={novo_lancamento.id}, orcamento_item_id={oid}")
                logger.info(f"{'='*80}\n")
                return jsonify(novo_lancamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.info(f"\n{'='*80}")
        logger.error(f"❌ ERRO em inserir_pagamento:")
        logger.info(f"   {str(e)}")
        logger.info(f"\nStack trace:")
        logger.error(error_details)
        logger.info(f"{'='*80}\n")
        return jsonify({"erro": "Erro interno no servidor"}), 500
# --- FIM DO ENDPOINT INSERIR PAGAMENTO ---


# --- MUDANÇA 5: NOVO ENDPOINT - MARCAR MÚLTIPLOS COMO PAGO ---
@cronograma_bp.route('/obras/<int:obra_id>/cronograma/marcar-multiplos-pagos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def marcar_multiplos_como_pago(obra_id):
    """
    Marca múltiplos pagamentos (futuros e parcelas) como pagos de uma vez.
    Permite anexar comprovante/nota fiscal para cada item.
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/cronograma/marcar-multiplos-pagos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json
        itens_selecionados = dados.get('itens', [])  # Lista de {tipo: 'futuro'|'parcela'|'servico', id: X}
        data_pagamento = dados.get('data_pagamento')
        
        logger.info(f"--- [LOG] Total de itens recebidos: {len(itens_selecionados)} ---")
        logger.info(f"--- [LOG] Itens: {itens_selecionados} ---")
        
        if data_pagamento:
            data_pagamento = date.fromisoformat(data_pagamento)
        else:
            data_pagamento = date.today()
        
        resultados = []
        
        for item in itens_selecionados:
            tipo_item = item.get('tipo')
            item_id = item.get('id')
            
            logger.info(f"--- [LOG] Processando item: tipo={tipo_item}, id={item_id} ---")
            
            # CORREÇÃO CRÍTICA: Usar savepoint para isolar cada item
            # Se um item der erro, não afeta os outros
            savepoint = db.session.begin_nested()
            
            try:
                if tipo_item == 'futuro':
                    # ===== LÓGICA CORRIGIDA: Verificar se tem vínculo com serviço =====
                    pagamento = db.session.get(PagamentoFuturo, item_id)
                    
                    if not pagamento:
                        savepoint.rollback()
                        erro_msg = f"Pagamento futuro ID {item_id} não encontrado no banco"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "error",
                            "mensagem": erro_msg
                        })
                        continue
                    
                    if pagamento.obra_id != obra_id:
                        savepoint.rollback()
                        erro_msg = f"Pagamento futuro ID {item_id} não pertence à obra {obra_id}"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento não pertence a esta obra"
                        })
                        continue
                    
                    # CASO 1: Pagamento vinculado a SERVIÇO
                    if pagamento.servico_id:
                        servico = db.session.get(Servico, pagamento.servico_id)
                        if not servico:
                            savepoint.rollback()
                            erro_msg = f"Serviço ID {pagamento.servico_id} não encontrado"
                            logger.error(f"--- [ERRO] {erro_msg} ---")
                            resultados.append({
                                "tipo": "futuro",
                                "id": item_id,
                                "status": "error",
                                "mensagem": "Serviço vinculado não encontrado"
                            })
                            continue
                        
                        # Determinar tipo_pagamento
                        if pagamento.tipo == 'Mão de Obra':
                            tipo_pagamento = 'mao_de_obra'
                        elif pagamento.tipo == 'Equipamentos':
                            tipo_pagamento = 'equipamento'
                        elif pagamento.tipo == 'Material':
                            tipo_pagamento = 'material'
                        else:
                            tipo_pagamento = 'material'  # default

                        # Criar PagamentoServico
                        novo_pag_servico = PagamentoServico(
                            servico_id=pagamento.servico_id,
                            tipo_pagamento=tipo_pagamento,
                            valor_total=pagamento.valor,
                            valor_pago=pagamento.valor,  # Marcar como totalmente pago
                            data=data_pagamento,
                            data_vencimento=pagamento.data_vencimento,
                            status='Pago',
                            prioridade=0,
                            fornecedor=pagamento.fornecedor,
                            pix=pagamento.pix
                        )
                        db.session.add(novo_pag_servico)
                        db.session.flush()
                        
                        # Recalcular percentual do serviço
                        pagamentos_serv = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                        pagamentos_mao_de_obra = [p for p in pagamentos_serv if p.tipo_pagamento == 'mao_de_obra']
                        pagamentos_material = [p for p in pagamentos_serv if p.tipo_pagamento == 'material']
                        
                        if servico.valor_global_mao_de_obra > 0:
                            total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                            servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                        
                        if servico.valor_global_material > 0:
                            total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                            servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                        
                        # DELETE o PagamentoFuturo
                        db.session.delete(pagamento)
                        
                        logger.info(f"--- [LOG] ✅ Pagamento futuro ID {item_id} vinculado ao serviço '{servico.nome}' ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "success",
                            "mensagem": f"Pagamento '{pagamento.descricao}' vinculado ao serviço '{servico.nome}' e marcado como pago",
                            "pagamento_servico_id": novo_pag_servico.id
                        })
                    
                    # CASO 2: Pagamento SEM vínculo com serviço
                    else:
                        # Criar Lançamento no Histórico
                        novo_lancamento = Lancamento(
                            obra_id=pagamento.obra_id,
                            tipo=pagamento.tipo or 'Despesa',
                            descricao=pagamento.descricao,
                            valor_total=pagamento.valor,
                            valor_pago=pagamento.valor,
                            data=data_pagamento,
                            data_vencimento=pagamento.data_vencimento,
                            status='Pago',
                            pix=pagamento.pix,
                            prioridade=0,
                            fornecedor=pagamento.fornecedor,
                            servico_id=None
                        )
                        db.session.add(novo_lancamento)
                        db.session.flush()
                        
                        # DELETE o PagamentoFuturo
                        db.session.delete(pagamento)
                        
                        logger.info(f"--- [LOG] ✅ Pagamento futuro ID {item_id} movido para histórico (Lançamento ID {novo_lancamento.id}) ---")
                        resultados.append({
                            "tipo": "futuro",
                            "id": item_id,
                            "status": "success",
                            "mensagem": f"Pagamento futuro '{pagamento.descricao}' movido para o histórico",
                            "lancamento_id": novo_lancamento.id
                        })
                
                elif tipo_item == 'parcela':
                    # Marcar parcela como paga
                    parcela = db.session.get(ParcelaIndividual, item_id)
                    
                    if not parcela:
                        savepoint.rollback()
                        erro_msg = f"Parcela ID {item_id} não encontrada no banco"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Parcela não encontrada"
                        })
                        continue
                    
                    pag_parcelado = db.session.get(PagamentoParcelado, parcela.pagamento_parcelado_id)
                    
                    if not pag_parcelado:
                        savepoint.rollback()
                        erro_msg = f"Pagamento parcelado ID {parcela.pagamento_parcelado_id} não encontrado"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento parcelado não encontrado"
                        })
                        continue
                    
                    if pag_parcelado.obra_id != obra_id:
                        savepoint.rollback()
                        erro_msg = f"Pagamento parcelado não pertence à obra {obra_id}"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento não pertence a esta obra"
                        })
                        continue
                    
                    # Verificar se já está paga
                    if parcela.status == 'Pago':
                        savepoint.rollback()
                        logger.warning(f"--- [AVISO] Parcela ID {item_id} já está paga, pulando ---")
                        resultados.append({
                            "tipo": "parcela",
                            "id": item_id,
                            "status": "error",
                            "mensagem": f"Parcela {parcela.numero_parcela} já está paga"
                        })
                        continue
                    
                    parcela.status = 'Pago'
                    parcela.data_pagamento = data_pagamento

                    # Bug F: Criar Lançamento se parcela não tiver serviço (consistência com marcar_parcela_paga)
                    if not pag_parcelado.servico_id:
                        descricao_lanc = f"{pag_parcelado.descricao} (Parcela {parcela.numero_parcela}/{pag_parcelado.numero_parcelas})"
                        segmento_info = getattr(pag_parcelado, 'segmento', None) or 'Material'
                        novo_lanc = Lancamento(
                            obra_id=pag_parcelado.obra_id,
                            tipo='Despesa',
                            descricao=descricao_lanc,
                            valor_total=parcela.valor_parcela,
                            valor_pago=parcela.valor_parcela,
                            data=parcela.data_pagamento,
                            data_vencimento=parcela.data_vencimento,
                            status='Pago',
                            pix=None,
                            prioridade=0,
                            fornecedor=pag_parcelado.fornecedor,
                            servico_id=None
                        )
                        if hasattr(novo_lanc, 'segmento'):
                            novo_lanc.segmento = segmento_info
                        db.session.add(novo_lanc)
                        db.session.flush()

                    # Atualizar contador de parcelas pagas
                    parcelas_pagas = ParcelaIndividual.query.filter_by(
                        pagamento_parcelado_id=pag_parcelado.id,
                        status='Pago'
                    ).count()
                    pag_parcelado.parcelas_pagas = parcelas_pagas
                    
                    # Se todas as parcelas foram pagas, marcar como Concluído
                    if parcelas_pagas >= pag_parcelado.numero_parcelas:
                        pag_parcelado.status = 'Concluído'
                    
                    logger.info(f"--- [LOG] ✅ Parcela ID {item_id} marcada como paga ---")
                    resultados.append({
                        "tipo": "parcela",
                        "id": item_id,
                        "status": "success",
                        "mensagem": f"Parcela {parcela.numero_parcela} marcada como paga"
                    })
                
                elif tipo_item == 'servico':
                    # NOVO: Marcar pagamento de serviço como totalmente pago
                    pagamento_servico = db.session.get(PagamentoServico, item_id)
                    
                    if not pagamento_servico:
                        savepoint.rollback()
                        erro_msg = f"Pagamento de serviço ID {item_id} não encontrado"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento de serviço não encontrado"
                        })
                        continue
                    
                    servico = db.session.get(Servico, pagamento_servico.servico_id)
                    
                    if not servico:
                        savepoint.rollback()
                        erro_msg = f"Serviço ID {pagamento_servico.servico_id} não encontrado"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Serviço não encontrado"
                        })
                        continue
                    
                    if servico.obra_id != obra_id:
                        savepoint.rollback()
                        erro_msg = f"Serviço não pertence à obra {obra_id}"
                        logger.error(f"--- [ERRO] {erro_msg} ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Serviço não pertence a esta obra"
                        })
                        continue
                    
                    # Verificar se já está totalmente pago
                    if pagamento_servico.valor_pago >= pagamento_servico.valor_total:
                        savepoint.rollback()
                        logger.warning(f"--- [AVISO] Pagamento de serviço ID {item_id} já está totalmente pago ---")
                        resultados.append({
                            "tipo": "servico",
                            "id": item_id,
                            "status": "error",
                            "mensagem": "Pagamento já está totalmente pago"
                        })
                        continue
                    
                    # Marcar como totalmente pago
                    pagamento_servico.valor_pago = pagamento_servico.valor_total
                    pagamento_servico.data = data_pagamento
                    pagamento_servico.status = 'Pago'
                    
                    # Atualizar percentuais do serviço
                    pagamentos = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                    
                    # Separar por tipo
                    pagamentos_mao_de_obra = [p for p in pagamentos if p.tipo_pagamento == 'mao_de_obra']
                    pagamentos_material = [p for p in pagamentos if p.tipo_pagamento == 'material']
                    
                    # Calcular percentuais
                    if servico.valor_global_mao_de_obra > 0:
                        total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                        servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                    
                    if servico.valor_global_material > 0:
                        total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                        servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                    
                    logger.info(f"--- [LOG] ✅ Pagamento de serviço ID {item_id} marcado como pago ---")
                    resultados.append({
                        "tipo": "servico",
                        "id": item_id,
                        "status": "success",
                        "mensagem": f"Pagamento do serviço '{servico.nome}' marcado como pago"
                    })
                
                else:
                    erro_msg = f"Tipo de item inválido: '{tipo_item}'"
                    logger.error(f"--- [ERRO] {erro_msg} ---")
                    resultados.append({
                        "tipo": tipo_item,
                        "id": item_id,
                        "status": "error",
                        "mensagem": "Tipo de item inválido (esperado: 'futuro', 'parcela' ou 'servico')"
                    })
                    savepoint.rollback()
                    continue
                
                # SUCESSO: Commit do savepoint
                savepoint.commit()
                logger.info(f"--- [LOG] ✅ Item processado com sucesso (savepoint committed) ---")
            
            except Exception as e:
                # ERRO: Rollback do savepoint (isola o erro deste item)
                savepoint.rollback()
                error_details = traceback.format_exc()
                erro_msg = f"Erro ao processar item tipo={tipo_item}, id={item_id}: {str(e)}"
                logger.error(f"--- [ERRO] {erro_msg} ---")
                logger.error(error_details)
                resultados.append({
                    "tipo": tipo_item,
                    "id": item_id,
                    "status": "error",
                    "mensagem": "Erro ao processar item"
                })
        
        db.session.commit()
        
        sucessos = len([r for r in resultados if r['status'] == 'success'])
        erros = len([r for r in resultados if r['status'] == 'error'])
        logger.error(f"--- [LOG] ✅ {sucessos} item(ns) marcado(s) como pago | ❌ {erros} erro(s) ---")
        
        # Listar os erros no log
        if erros > 0:
            logger.error("--- [LOG] Detalhes dos erros: ---")
            for r in resultados:
                if r['status'] == 'error':
                    logger.error(f"  - Tipo: {r['tipo']}, ID: {r['id']}, Erro: {r['mensagem']}")
        
        return jsonify({
            "mensagem": "Processamento concluído",
            "resultados": resultados
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO FATAL] marcar-multiplos-pagos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500
# --- FIM DO ENDPOINT MARCAR MÚLTIPLOS COMO PAGO ---


@cronograma_bp.route('/obras/<int:obra_id>/servico-financeiro', methods=['GET', 'OPTIONS'])
@jwt_required()
def get_servico_financeiro(obra_id):
    """
    Retorna dados financeiros de um serviço específico da obra para análise de valor agregado (EVM)
    Query parameter: servico_nome (string obrigatório)
    
    Retorna:
    - valor_total: Soma de valor_global_mao_de_obra + valor_global_material do serviço
    - valor_pago: Soma de todos os pagamentos efetivados (valor_pago) vinculados a este serviço
    - area_total: Área total do cronograma (se tipo_medicao = 'area')
    - area_executada: Área executada do cronograma
    - percentual_pago: Percentual do valor total que já foi pago
    - percentual_executado: Percentual de conclusão físico do cronograma
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/servico-financeiro (GET) acessada ---")
    
    try:
        # Obter servico_nome da query string
        servico_nome = request.args.get('servico_nome')
        
        if not servico_nome:
            logger.error("[ERRO] servico_nome não fornecido")
            return jsonify({'erro': 'servico_nome é obrigatório'}), 400
        
        # Verificar acesso à obra
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 404
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'erro': 'Obra não encontrada'}), 404
        
        logger.info(f"[LOG] Buscando dados financeiros para serviço: '{servico_nome}' na obra {obra_id}")
        
        # 1. Buscar o serviço na planilha de custos
        servico = Servico.query.filter_by(
            obra_id=obra_id,
            nome=servico_nome
        ).first()
        
        if not servico:
            logger.info(f"[INFO] Serviço '{servico_nome}' não encontrado na planilha de custos — buscando no Orçamento de Engenharia")
            
            # Tentar buscar pelo vínculo cronograma_obra → orcamento_etapa_id
            try:
                cron_result = db.session.execute(db.text("""
                    SELECT co.id, co.percentual_conclusao, co.area_total, co.area_executada, co.tipo_medicao,
                           co.orcamento_etapa_id
                    FROM cronograma_obra co
                    WHERE co.obra_id = :obra_id
                      AND LOWER(co.servico_nome) = LOWER(:nome)
                    LIMIT 1
                """), {"obra_id": obra_id, "nome": servico_nome}).fetchone()

                # Se não tem orcamento_etapa_id, tentar encontrar a etapa pelo nome
                etapa_id_fallback = None
                if cron_result and not cron_result[5]:
                    etapa_by_name = db.session.execute(db.text("""
                        SELECT id FROM orcamento_eng_etapa
                        WHERE obra_id = :obra_id AND LOWER(nome) = LOWER(:nome)
                        LIMIT 1
                    """), {"obra_id": obra_id, "nome": servico_nome}).fetchone()
                    if etapa_by_name:
                        etapa_id_fallback = etapa_by_name[0]

                if cron_result and (cron_result[5] or etapa_id_fallback):
                    etapa_id = cron_result[5] or etapa_id_fallback
                    # Calcular total da etapa somando itens
                    totais = db.session.execute(db.text("""
                        SELECT
                            COALESCE(SUM(CASE
                                WHEN tipo_composicao = 'separado' THEN quantidade * COALESCE(preco_mao_obra, 0)
                                ELSE quantidade * COALESCE(preco_unitario, 0) * COALESCE(rateio_mo, 50) / 100
                            END), 0) as total_mo,
                            COALESCE(SUM(CASE
                                WHEN tipo_composicao = 'separado' THEN quantidade * COALESCE(preco_material, 0)
                                ELSE quantidade * COALESCE(preco_unitario, 0) * COALESCE(rateio_mat, 50) / 100
                            END), 0) as total_mat
                        FROM orcamento_eng_item
                        WHERE etapa_id = :etapa_id
                    """), {"etapa_id": etapa_id}).fetchone()

                    valor_total_orc = float((totais[0] or 0) + (totais[1] or 0))

                    # Calcular valor pago: mesma lógica do orçamento (4 fontes de pagamento)
                    # 1. Lançamentos pagos vinculados aos itens da etapa
                    vp_lanc = db.session.execute(db.text("""
                        SELECT COALESCE(SUM(l.valor_pago), 0)
                        FROM lancamento l
                        JOIN orcamento_eng_item oi ON l.orcamento_item_id = oi.id
                        WHERE oi.etapa_id = :etapa_id AND l.status = 'Pago'
                    """), {"etapa_id": etapa_id}).scalar() or 0

                    # 2. Pagamentos futuros pagos
                    vp_futuro = db.session.execute(db.text("""
                        SELECT COALESCE(SUM(pf.valor), 0)
                        FROM pagamento_futuro pf
                        JOIN orcamento_eng_item oi ON pf.orcamento_item_id = oi.id
                        WHERE oi.etapa_id = :etapa_id AND pf.status = 'Pago'
                    """), {"etapa_id": etapa_id}).scalar() or 0

                    # 3. Parcelas pagas de pagamentos parcelados
                    vp_parcelas = db.session.execute(db.text("""
                        SELECT COALESCE(SUM(pi.valor_parcela), 0)
                        FROM parcela_individual pi
                        JOIN pagamento_parcelado_v2 pp ON pi.pagamento_parcelado_id = pp.id
                        JOIN orcamento_eng_item oi ON pp.orcamento_item_id = oi.id
                        WHERE oi.etapa_id = :etapa_id AND pi.status = 'Pago'
                    """), {"etapa_id": etapa_id}).scalar() or 0

                    # 4. Boletos pagos
                    vp_boletos = db.session.execute(db.text("""
                        SELECT COALESCE(SUM(b.valor), 0)
                        FROM boleto b
                        JOIN orcamento_eng_item oi ON b.orcamento_item_id = oi.id
                        WHERE oi.etapa_id = :etapa_id AND b.status = 'Pago'
                    """), {"etapa_id": etapa_id}).scalar() or 0

                    valor_pago_orc = float(vp_lanc) + float(vp_futuro) + float(vp_parcelas) + float(vp_boletos)

                    percentual_pago = round((valor_pago_orc / valor_total_orc * 100) if valor_total_orc > 0 else 0, 1)
                    percentual_exec = float(cron_result[1] or 0)

                    return jsonify({
                        'servico_nome': servico_nome,
                        'valor_total': float(valor_total_orc),
                        'valor_pago': float(valor_pago_orc),
                        'area_total': cron_result[2],
                        'area_executada': cron_result[3],
                        'percentual_pago': percentual_pago,
                        'percentual_executado': percentual_exec,
                        'fonte': 'orcamento_engenharia'
                    }), 200
            except Exception as e_orc:
                logger.exception(f"[AVISO] Erro ao buscar do orçamento de engenharia: {e_orc}")

            # Nada encontrado — retornar vazio
            return jsonify({
                'servico_nome': servico_nome,
                'valor_total': 0.0,
                'valor_pago': 0.0,
                'area_total': None,
                'area_executada': None,
                'percentual_pago': 0.0,
                'percentual_executado': 0.0
            }), 200
        
        # 2. Calcular valor total orçado (MO + Material)
        valor_total = float(servico.valor_global_mao_de_obra or 0.0) + float(servico.valor_global_material or 0.0)
        logger.info(f"[LOG] Valor total orçado (Servico): R$ {valor_total:.2f}")

        # CORREÇÃO BUG #3: Se Servico tem valor zero (caso típico quando o usuário
        # mantém o orçamento no módulo Orçamento de Engenharia), buscar o valor
        # somando os itens vinculados ao servico_id no orcamento_eng_item.
        if valor_total <= 0:
            try:
                totais_orc = db.session.execute(db.text("""
                    SELECT
                        COALESCE(SUM(CASE
                            WHEN tipo_composicao = 'separado' THEN quantidade * COALESCE(preco_mao_obra, 0)
                            ELSE quantidade * COALESCE(preco_unitario, 0) * COALESCE(rateio_mo, 50) / 100
                        END), 0) AS total_mo,
                        COALESCE(SUM(CASE
                            WHEN tipo_composicao = 'separado' THEN quantidade * COALESCE(preco_material, 0)
                            ELSE quantidade * COALESCE(preco_unitario, 0) * COALESCE(rateio_mat, 50) / 100
                        END), 0) AS total_mat
                    FROM orcamento_eng_item
                    WHERE servico_id = :sid
                """), {"sid": servico.id}).fetchone()
                if totais_orc:
                    valor_total_orc_eng = float((totais_orc[0] or 0) + (totais_orc[1] or 0))
                    if valor_total_orc_eng > 0:
                        valor_total = valor_total_orc_eng
                        logger.info(f"[LOG] Valor total recalculado via orcamento_eng_item: R$ {valor_total:.2f}")
            except Exception as e_fallback:
                logger.exception(f"[AVISO] Falha ao calcular valor_total via orcamento_eng_item: {e_fallback}")

        # 3. Calcular valor já pago
        # Opção A: Pagamentos vinculados diretamente ao servico_id via PagamentoServico
        pagamentos_servico = db.session.query(
            func.sum(PagamentoServico.valor_pago).label('total_pago')
        ).filter(
            PagamentoServico.servico_id == servico.id
        ).first()

        valor_pago_servico = float(pagamentos_servico.total_pago or 0.0)

        # Opção B: Lançamentos vinculados ao servico_id (qualquer status — usamos valor_pago)
        lancamentos_pagos = db.session.query(
            func.sum(Lancamento.valor_pago).label('total_pago')
        ).filter(
            Lancamento.obra_id == obra_id,
            Lancamento.servico_id == servico.id
        ).first()

        valor_pago_lancamentos = float(lancamentos_pagos.total_pago or 0.0)

        # CORREÇÃO BUG #3: Opção C — lançamentos vinculados aos itens do orçamento
        # de engenharia que pertencem a este serviço. Necessário porque o módulo
        # de orçamento de engenharia grava o vínculo via orcamento_item_id em vez
        # de servico_id. Sem isto, valor_pago vinha zero quando o usuário registra
        # custos pelo módulo novo de orçamento.
        valor_pago_orc_item = 0.0
        try:
            res_orc_item = db.session.execute(db.text("""
                SELECT COALESCE(SUM(l.valor_pago), 0)
                FROM lancamento l
                JOIN orcamento_eng_item oei ON l.orcamento_item_id = oei.id
                WHERE l.obra_id = :obra_id
                  AND oei.servico_id = :sid
                  AND l.servico_id IS DISTINCT FROM :sid
            """), {"obra_id": obra_id, "sid": servico.id}).scalar()
            valor_pago_orc_item = float(res_orc_item or 0.0)
        except Exception as e_oc:
            logger.exception(f"[AVISO] Falha em Opção C (orcamento_item_id): {e_oc}")

        # NOTA: Não somamos parcelas pagas aqui porque quando uma parcela é marcada como paga,
        # já é criado um PagamentoServico (contabilizado na Opção A acima).
        # Somar aqui causaria duplicidade de valores!

        # Somar todos os pagamentos (sem duplicidade)
        valor_pago = valor_pago_servico + valor_pago_lancamentos + valor_pago_orc_item
        logger.info(f"[LOG] Valor já pago (PagamentoServico): R$ {valor_pago_servico:.2f}")
        logger.info(f"[LOG] Valor já pago (Lancamentos servico_id): R$ {valor_pago_lancamentos:.2f}")
        logger.info(f"[LOG] Valor já pago (Lancamentos orcamento_item_id): R$ {valor_pago_orc_item:.2f}")
        logger.info(f"[LOG] Valor total pago: R$ {valor_pago:.2f}")
        
        # 4. Buscar dados do cronograma
        etapa_cronograma = CronogramaObra.query.filter_by(
            obra_id=obra_id,
            servico_nome=servico_nome
        ).first()
        
        area_total = None
        area_executada = None
        percentual_executado = 0.0
        
        if etapa_cronograma:
            area_total = float(etapa_cronograma.area_total) if etapa_cronograma.area_total else None
            area_executada = float(etapa_cronograma.area_executada) if etapa_cronograma.area_executada else None
            percentual_executado = float(etapa_cronograma.percentual_conclusao or 0.0)
            logger.info(f"[LOG] Cronograma encontrado - % Executado: {percentual_executado:.1f}%")
        else:
            logger.info(f"[INFO] Cronograma não encontrado para este serviço")
        
        # 5. Calcular percentual pago
        percentual_pago = (valor_pago / valor_total * 100.0) if valor_total > 0 else 0.0
        
        # 6. Montar resposta
        resposta = {
            'servico_nome': servico_nome,
            'valor_total': valor_total,
            'valor_pago': valor_pago,
            'area_total': area_total,
            'area_executada': area_executada,
            'percentual_pago': round(percentual_pago, 2),
            'percentual_executado': round(percentual_executado, 2)
        }
        
        logger.info(f"[LOG] Resposta: {resposta}")
        return jsonify(resposta), 200
        
    except Exception as e:
        logger.exception(f"[ERRO] get_servico_financeiro: {str(e)}")
        traceback.print_exc()
        return jsonify({'erro': 'Erro ao buscar dados financeiros do serviço'}), 500


@cronograma_bp.route('/obras/<int:obra_id>/cronograma', methods=['GET'])
@jwt_required()
def get_cronograma_obra_by_obra(obra_id):
    """Busca cronograma da obra - rota alternativa"""
    try:
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        cronograma_items = CronogramaObra.query.filter_by(obra_id=obra_id).order_by(CronogramaObra.ordem).all()
        return jsonify([item.to_dict() for item in cronograma_items]), 200
    except Exception as e:
        logger.exception(f"[ERRO] get_cronograma_obra_by_obra: {str(e)}")
        return jsonify({'error': 'Erro ao buscar cronograma'}), 500


@cronograma_bp.route('/cronograma/<int:obra_id>', methods=['GET'])
@jwt_required()
def get_cronograma_obra(obra_id):
    try:
        # Simplificar: só verificar se obra existe
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        cronograma_items = CronogramaObra.query.filter_by(obra_id=obra_id).order_by(CronogramaObra.ordem).all()
        return jsonify([item.to_dict() for item in cronograma_items]), 200
    except Exception as e:
        logger.exception(f"[ERRO] get_cronograma_obra: {str(e)}")
        return jsonify({'error': 'Erro ao buscar cronograma'}), 500


# ==============================================================================
# SINCRONIZAÇÃO AUTOMÁTICA: ORÇAMENTO → CRONOGRAMA DE OBRAS
# ==============================================================================

def sincronizar_etapa_orcamento_para_cronograma(etapa_id, obra_id):
    """
    Função auxiliar para sincronizar uma etapa do orçamento com o cronograma de obras.
    Chamada automaticamente quando uma nova etapa é criada no orçamento.
    """
    try:
        etapa = OrcamentoEngEtapa.query.get(etapa_id)
        if not etapa:
            logger.info(f"[SYNC] Etapa {etapa_id} não encontrada")
            return None
        
        # Verificar se já existe no cronograma (evitar duplicatas)
        cronograma_existente = CronogramaObra.query.filter_by(obra_id=obra_id).all()
        nomes_cronograma = [c.servico_nome.lower().strip() for c in cronograma_existente]
        
        if etapa.nome.lower().strip() in nomes_cronograma:
            logger.info(f"[SYNC] Etapa '{etapa.nome}' já existe no cronograma da obra {obra_id}")
            return None
        
        # Calcular ordem e datas
        max_ordem = db.session.query(db.func.max(CronogramaObra.ordem)).filter_by(obra_id=obra_id).scalar() or 0
        data_inicio = date.today()
        duracao_padrao = 30
        data_fim = data_inicio + timedelta(days=duracao_padrao - 1)
        
        # Criar serviço no cronograma
        novo_servico = CronogramaObra(
            obra_id=obra_id,
            servico_nome=etapa.nome,
            ordem=max_ordem + 1,
            data_inicio=data_inicio,
            data_fim_prevista=data_fim,
            tipo_medicao='etapas',
            percentual_conclusao=0,
            observacoes=f"Importado automaticamente do Orçamento - {etapa.codigo or 'N/A'}"
        )
        
        db.session.add(novo_servico)
        db.session.flush()
        
        # Tentar vincular ao orçamento
        try:
            db.session.execute(db.text(
                f"UPDATE cronograma_obra SET orcamento_etapa_id = {etapa.id} WHERE id = {novo_servico.id}"
            ))
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            pass
        
        # Criar etapa pai no cronograma
        etapa_cronograma = CronogramaEtapa(
            cronograma_id=novo_servico.id,
            nome=etapa.nome,
            ordem=1,
            duracao_dias=duracao_padrao,
            data_inicio=data_inicio,
            data_fim=data_fim,
            percentual_conclusao=0,
            observacoes=f"Código: {etapa.codigo or 'N/A'}"
        )
        db.session.add(etapa_cronograma)
        
        logger.info(f"[SYNC] ✅ Etapa '{etapa.nome}' adicionada ao cronograma da obra {obra_id}")
        return novo_servico
        
    except Exception as e:
        logger.exception(f"[SYNC] ❌ Erro ao sincronizar etapa {etapa_id}: {str(e)}")
        return None


@cronograma_bp.route('/obras/<int:obra_id>/cronograma/exportar-pdf', methods=['GET'])
@jwt_required()
def exportar_cronograma_fisico_pdf(obra_id):
    """Gera PDF do cronograma financeiro da obra (mesmo formato da tela principal)"""
    # Simplesmente chamar a função principal de relatório
    return gerar_relatorio_cronograma_pdf(obra_id)


@cronograma_bp.route('/obras/<int:obra_id>/cronograma-obra/relatorio-pdf', methods=['GET'])
@jwt_required()
def gerar_relatorio_cronograma_obra_pdf(obra_id):
    """
    Gera relatório PDF completo do Cronograma de Obras
    Inclui: status, etapas, medições por área, análise EVM
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        # Buscar obra
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        # Buscar cronograma
        cronograma_items = CronogramaObra.query.filter_by(obra_id=obra_id).order_by(CronogramaObra.ordem).all()
        
        # Criar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Estilos customizados
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        
        style_title = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=20,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#2d3748')
        )
        
        style_subtitle = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Heading2'],
            fontSize=14,
            spaceBefore=15,
            spaceAfter=10,
            textColor=colors.HexColor('#4a5568')
        )
        
        style_normal = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=5
        )
        
        style_small = ParagraphStyle(
            'CustomSmall',
            parent=styles['Normal'],
            fontSize=8,
            textColor=colors.HexColor('#718096')
        )
        
        # ==================== CABEÇALHO ====================
        elements.append(Paragraph("🏗️ OBRALY", style_title))
        elements.append(Paragraph("RELATÓRIO DE CRONOGRAMA DE OBRAS", ParagraphStyle(
            'SubTitle', parent=styles['Heading2'], fontSize=14, alignment=TA_CENTER, textColor=colors.HexColor('#4f46e5')
        )))
        elements.append(Spacer(1, 10))
        
        # Info da obra
        hoje = datetime.now().strftime('%d/%m/%Y às %H:%M')
        header_data = [
            ['Obra:', obra.nome, 'Data:', hoje],
            ['Gerado por:', current_user.username if current_user else 'Sistema', '', '']
        ]
        header_table = Table(header_data, colWidths=[2.5*cm, 7*cm, 2.5*cm, 5*cm])
        header_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#4a5568')),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 15))
        
        # ==================== RESUMO EXECUTIVO ====================
        elements.append(Paragraph("📊 RESUMO EXECUTIVO", style_subtitle))
        
        # Calcular estatísticas
        total_servicos = len(cronograma_items)
        concluidos = sum(1 for s in cronograma_items if s.percentual_conclusao >= 100)
        hoje_date = date.today()
        atrasados = sum(1 for s in cronograma_items if s.data_fim_prevista and s.data_fim_prevista <= hoje_date and s.percentual_conclusao < 100)
        em_andamento = sum(1 for s in cronograma_items if s.data_inicio_real and s.percentual_conclusao < 100 and (not s.data_fim_prevista or s.data_fim_prevista > hoje_date))
        a_iniciar = total_servicos - concluidos - atrasados - em_andamento
        
        # Progresso geral
        if total_servicos > 0:
            progresso_geral = sum(s.percentual_conclusao for s in cronograma_items) / total_servicos
        else:
            progresso_geral = 0
        
        resumo_data = [
            ['Total de Serviços:', str(total_servicos), 'Concluídos:', str(concluidos)],
            ['Em Andamento:', str(em_andamento), 'A Iniciar:', str(a_iniciar)],
            ['Atrasados:', str(atrasados), 'Progresso Geral:', f'{progresso_geral:.1f}%']
        ]
        resumo_table = Table(resumo_data, colWidths=[3.5*cm, 3*cm, 3.5*cm, 3*cm])
        resumo_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f7fafc')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(resumo_table)
        elements.append(Spacer(1, 20))
        
        # ==================== DETALHES POR SERVIÇO ====================
        elements.append(Paragraph("📋 DETALHES POR SERVIÇO", style_subtitle))
        elements.append(Spacer(1, 10))
        
        for idx, servico in enumerate(cronograma_items, 1):
            # Determinar status
            percentual = servico.percentual_conclusao
            if percentual >= 100:
                status = "✅ CONCLUÍDO"
                status_color = colors.HexColor('#28a745')
            elif servico.data_fim_prevista and servico.data_fim_prevista <= hoje_date:
                status = "⚠️ ATRASADO"
                status_color = colors.HexColor('#dc3545')
            elif servico.data_inicio_real:
                status = "🔄 EM ANDAMENTO"
                status_color = colors.HexColor('#007bff')
            else:
                status = "⏳ A INICIAR"
                status_color = colors.HexColor('#6c757d')
            
            # Tipo de medição
            if servico.tipo_medicao == 'etapas':
                tipo_texto = "📋 Por Etapas"
            elif servico.tipo_medicao == 'area':
                tipo_texto = f"📐 Por Área ({servico.unidade_medida})"
            else:
                tipo_texto = "🔧 Empreitada"
            
            # Cabeçalho do serviço
            servico_header = [
                [f'#{idx}  {servico.servico_nome}', status, tipo_texto]
            ]
            servico_header_table = Table(servico_header, colWidths=[9*cm, 4*cm, 4*cm])
            servico_header_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (0, 0), 12),
                ('FONTSIZE', (1, 0), (2, 0), 10),
                ('TEXTCOLOR', (1, 0), (1, 0), status_color),
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f0f4f8')),
                ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#4f46e5')),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ]))
            elements.append(servico_header_table)
            
            # Cronograma
            data_inicio = servico.data_inicio.strftime('%d/%m/%Y') if servico.data_inicio else '-'
            data_fim = servico.data_fim_prevista.strftime('%d/%m/%Y') if servico.data_fim_prevista else '-'
            data_inicio_real = servico.data_inicio_real.strftime('%d/%m/%Y') if servico.data_inicio_real else '-'
            data_fim_real = servico.data_fim_real.strftime('%d/%m/%Y') if servico.data_fim_real else '-'
            
            cronograma_data = [
                ['📅 CRONOGRAMA', '', '', ''],
                ['Início Previsto:', data_inicio, 'Término Previsto:', data_fim],
                ['Início Real:', data_inicio_real, 'Término Real:', data_fim_real]
            ]
            cronograma_table = Table(cronograma_data, colWidths=[3.5*cm, 4.75*cm, 3.5*cm, 4.75*cm])
            cronograma_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                ('SPAN', (0, 0), (-1, 0)),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (2, 1), (2, -1), 'Helvetica-Bold'),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(cronograma_table)
            
            # Execução
            barra_progresso = '█' * int(percentual / 5) + '░' * (20 - int(percentual / 5))
            
            # Se for por área
            if servico.tipo_medicao == 'area' and servico.area_total:
                area_exec = servico.area_executada or 0
                exec_data = [
                    ['📈 EXECUÇÃO', '', ''],
                    ['Progresso:', f'{barra_progresso} {percentual:.1f}%', ''],
                    ['Área Executada:', f'{area_exec} de {servico.area_total} {servico.unidade_medida}', f'({(area_exec/servico.area_total*100):.1f}%)']
                ]
            else:
                exec_data = [
                    ['📈 EXECUÇÃO', '', ''],
                    ['Progresso:', f'{barra_progresso} {percentual:.1f}%', '']
                ]
            
            exec_table = Table(exec_data, colWidths=[3.5*cm, 10*cm, 3*cm])
            exec_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                ('SPAN', (0, 0), (-1, 0)),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(exec_table)
            
            # ETAPAS (se houver)
            try:
                etapas_list = servico.etapas.order_by(CronogramaEtapa.ordem).all() if servico.etapas else []
                if etapas_list:
                    total_dias_etapas = sum(e.duracao_dias or 0 for e in etapas_list)
                    etapas_header = [[f'📋 ETAPAS ({len(etapas_list)}) - {total_dias_etapas} dias', '', '', '', '']]
                    etapas_data = [['#', 'Etapa', 'Duração', 'Período', 'Status']]
                    
                    for i, etapa in enumerate(etapas_list, 1):
                        etapa_inicio = etapa.data_inicio.strftime('%d/%m') if etapa.data_inicio else '-'
                        etapa_fim = etapa.data_fim.strftime('%d/%m') if etapa.data_fim else '-'
                        
                        if etapa.percentual_conclusao >= 100:
                            etapa_status = '✅ 100%'
                        elif etapa.percentual_conclusao > 0:
                            etapa_status = f'🔄 {etapa.percentual_conclusao:.0f}%'
                        else:
                            etapa_status = '⏳ 0%'
                        
                        etapas_data.append([
                            str(i),
                            etapa.nome[:25] + '...' if len(etapa.nome) > 25 else etapa.nome,
                            f'{etapa.duracao_dias} dias',
                            f'{etapa_inicio} → {etapa_fim}',
                            etapa_status
                        ])
                    
                    etapas_table = Table(etapas_header + etapas_data, colWidths=[1*cm, 6*cm, 2.5*cm, 4*cm, 3*cm])
                    etapas_table.setStyle(TableStyle([
                        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                        ('FONTSIZE', (0, 0), (-1, -1), 8),
                        ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                        ('SPAN', (0, 0), (-1, 0)),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
                        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#f0f4f8')),
                        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
                        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                        ('INNERGRID', (0, 1), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
                        ('TOPPADDING', (0, 0), (-1, -1), 4),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                        ('LEFTPADDING', (0, 0), (-1, -1), 5),
                        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
                        ('ALIGN', (2, 1), (2, -1), 'CENTER'),
                        ('ALIGN', (4, 1), (4, -1), 'CENTER'),
                    ]))
                    elements.append(etapas_table)
            except Exception as e:
                logger.exception(f"[AVISO] Erro ao carregar etapas para PDF: {str(e)}")
            
            # ANÁLISE EVM
            try:
                # Buscar dados financeiros
                servico_db = Servico.query.filter_by(obra_id=obra_id, nome=servico.servico_nome).first()
                if servico_db:
                    valor_total = (servico_db.valor_global_mao_de_obra or 0) + (servico_db.valor_global_material or 0)
                    
                    # Buscar pagamentos
                    pagamentos = PagamentoServico.query.filter_by(servico_id=servico_db.id).all()
                    valor_pago = sum(p.valor_pago or 0 for p in pagamentos)
                    
                    if valor_total > 0:
                        percentual_pago = (valor_pago / valor_total) * 100
                        percentual_exec = percentual
                        diferenca = percentual_exec - percentual_pago
                        
                        if diferenca >= 5:
                            evm_status = "🟢 ADIANTADO"
                            evm_color = colors.HexColor('#28a745')
                        elif diferenca >= -5:
                            evm_status = "🔵 NO PRAZO"
                            evm_color = colors.HexColor('#007bff')
                        elif diferenca >= -15:
                            evm_status = "🟡 ATENÇÃO"
                            evm_color = colors.HexColor('#ffc107')
                        else:
                            evm_status = "🔴 CRÍTICO"
                            evm_color = colors.HexColor('#dc3545')
                        
                        evm_data = [
                            ['💰 ANÁLISE FINANCEIRA (EVM)', evm_status, ''],
                            ['Total Orçado:', f'R$ {valor_total:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'), ''],
                            ['Já Pago:', f'R$ {valor_pago:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.'), f'({percentual_pago:.1f}%)'],
                            ['Pago vs Executado:', f'{percentual_pago:.0f}% pago | {percentual_exec:.0f}% executado', f'Diferença: {diferenca:+.0f}%']
                        ]
                        evm_table = Table(evm_data, colWidths=[4*cm, 8.5*cm, 4*cm])
                        evm_table.setStyle(TableStyle([
                            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                            ('FONTSIZE', (0, 0), (-1, -1), 9),
                            ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                            ('TEXTCOLOR', (1, 0), (1, 0), evm_color),
                            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fef3c7')),
                            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fffbeb')),
                            ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
                            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#fbbf24')),
                            ('TOPPADDING', (0, 0), (-1, -1), 5),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                            ('LEFTPADDING', (0, 0), (-1, -1), 8),
                        ]))
                        elements.append(evm_table)
            except Exception as e:
                logger.exception(f"[AVISO] Erro ao calcular EVM para PDF: {str(e)}")
            
            # Observações
            if servico.observacoes:
                obs_data = [['📝 Observações:', servico.observacoes[:200]]]
                obs_table = Table(obs_data, colWidths=[3.5*cm, 13*cm])
                obs_table.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#718096')),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                ]))
                elements.append(obs_table)
            
            elements.append(Spacer(1, 15))
        
        # ==================== LEGENDA ====================
        elements.append(Paragraph("📋 LEGENDA", style_subtitle))
        
        legenda_data = [
            ['STATUS', 'INDICADOR EVM'],
            ['✅ Concluído - Serviço 100% executado', '🟢 ADIANTADO - Execução maior que pagamento (+5%)'],
            ['🔄 Em Andamento - Em execução', '🔵 NO PRAZO - Proporcional (±5%)'],
            ['⏳ A Iniciar - Não iniciado', '🟡 ATENÇÃO - Pagou mais (-5% a -15%)'],
            ['⚠️ Atrasado - Passou do prazo', '🔴 CRÍTICO - Pagou muito mais (-15% ou mais)'],
        ]
        legenda_table = Table(legenda_data, colWidths=[8.5*cm, 8.5*cm])
        legenda_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(legenda_table)
        
        # Rodapé
        elements.append(Spacer(1, 20))
        elements.append(Paragraph(f"Gerado em: {hoje} - Obraly v1.0", ParagraphStyle(
            'Footer', parent=styles['Normal'], fontSize=8, alignment=TA_CENTER, textColor=colors.HexColor('#a0aec0')
        )))
        
        # Gerar PDF
        doc.build(elements)
        buffer.seek(0)
        
        # Retornar arquivo
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'cronograma_obras_{obra.nome}_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf'
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] gerar_relatorio_cronograma_obra_pdf: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao gerar PDF'}), 500


@cronograma_bp.route('/cronograma', methods=['POST', 'OPTIONS'])
@jwt_required()
def create_cronograma():
    """Cria uma nova etapa do cronograma"""
    # Tratar OPTIONS para CORS preflight
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        # Verificar autenticação
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        data = request.json
        required_fields = ['obra_id', 'servico_nome', 'data_inicio', 'data_fim_prevista']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Campo obrigatório ausente: {field}'}), 400
        
        obra = Obra.query.get(data['obra_id'])
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        # Verificar acesso à obra
        if not user_has_access_to_obra(current_user, data['obra_id']):
            return jsonify({'error': 'Acesso negado a esta obra'}), 403
        
        try:
            data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
            data_fim_prevista = datetime.strptime(data['data_fim_prevista'], '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Formato de data inválido. Use YYYY-MM-DD'}), 400
        
        if data_fim_prevista < data_inicio:
            return jsonify({'error': 'Data de término não pode ser anterior à data de início'}), 400
        
        # Processar datas reais opcionais
        data_inicio_real = None
        data_fim_real = None
        
        if 'data_inicio_real' in data and data['data_inicio_real']:
            try:
                data_inicio_real = datetime.strptime(data['data_inicio_real'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Formato de data_inicio_real inválido'}), 400
        
        if 'data_fim_real' in data and data['data_fim_real']:
            try:
                data_fim_real = datetime.strptime(data['data_fim_real'], '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Formato de data_fim_real inválido'}), 400
        
        novo_item = CronogramaObra(
            obra_id=data['obra_id'],
            servico_nome=data['servico_nome'],
            ordem=data.get('ordem', 1),
            data_inicio=data_inicio,
            data_fim_prevista=data_fim_prevista,
            data_inicio_real=data_inicio_real,
            data_fim_real=data_fim_real,
            percentual_conclusao=float(data.get('percentual_conclusao', 0)),
            tipo_medicao=data.get('tipo_medicao', 'empreitada'),
            area_total=float(data['area_total']) if data.get('area_total') else None,
            area_executada=float(data.get('area_executada', 0)) if data.get('area_total') else None,
            unidade_medida=data.get('unidade_medida', 'm²') if data.get('area_total') else None,
            observacoes=data.get('observacoes')
        )
        
        db.session.add(novo_item)
        db.session.commit()
        
        logger.info(f"[LOG] Cronograma criado: ID={novo_item.id}, Serviço={novo_item.servico_nome}")
        return jsonify(novo_item.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] create_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao criar etapa do cronograma'}), 500


@cronograma_bp.route('/cronograma/<int:cronograma_id>', methods=['PUT', 'OPTIONS'])
@jwt_required()
def update_cronograma(cronograma_id):
    """Atualiza uma etapa do cronograma"""
    # Tratar OPTIONS para CORS preflight
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'PUT, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401

        data = request.json

        item = CronogramaObra.query.get(cronograma_id)
        if not item:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        # Verificar acesso à obra
        if not user_has_access_to_obra(current_user, item.obra_id):
            return jsonify({'error': 'Acesso negado a esta obra'}), 403
        
        if 'servico_nome' in data:
            item.servico_nome = data['servico_nome']
        if 'ordem' in data:
            item.ordem = int(data['ordem'])
        
        # PLANEJAMENTO (datas previstas)
        if 'data_inicio' in data:
            if data['data_inicio']:
                try:
                    item.data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    return jsonify({'error': 'Formato de data_inicio inválido'}), 400
        if 'data_fim_prevista' in data:
            if data['data_fim_prevista']:
                try:
                    item.data_fim_prevista = datetime.strptime(data['data_fim_prevista'], '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    return jsonify({'error': 'Formato de data_fim_prevista inválido'}), 400
        
        # EXECUÇÃO REAL (datas reais e percentual)
        if 'data_inicio_real' in data:
            if data['data_inicio_real']:
                try:
                    item.data_inicio_real = datetime.strptime(data['data_inicio_real'], '%Y-%m-%d').date()
                except ValueError:
                    return jsonify({'error': 'Formato de data_inicio_real inválido'}), 400
            else:
                item.data_inicio_real = None
        
        if 'data_fim_real' in data:
            if data['data_fim_real']:
                try:
                    item.data_fim_real = datetime.strptime(data['data_fim_real'], '%Y-%m-%d').date()
                except ValueError:
                    return jsonify({'error': 'Formato de data_fim_real inválido'}), 400
            else:
                item.data_fim_real = None
        
        if 'percentual_conclusao' in data:
            percentual = float(data['percentual_conclusao'])
            item.percentual_conclusao = max(0, min(100, percentual))
            # Auto-preencher data_fim_real quando atingir 100%
            if item.percentual_conclusao >= 100 and not item.data_fim_real:
                item.data_fim_real = datetime.now().date()
        
        if 'observacoes' in data:
            item.observacoes = data['observacoes']
        
        # CAMPOS DE MEDIÇÃO (novos)
        if 'tipo_medicao' in data:
            item.tipo_medicao = data['tipo_medicao']
        
        if 'area_total' in data:
            item.area_total = float(data['area_total']) if data['area_total'] else None
        
        if 'area_executada' in data:
            item.area_executada = float(data['area_executada']) if data['area_executada'] else None
        
        if 'unidade_medida' in data:
            item.unidade_medida = data['unidade_medida']
        
        # Validar datas apenas se ambas existirem
        if item.data_fim_prevista and item.data_inicio and item.data_fim_prevista < item.data_inicio:
            return jsonify({'error': 'Data de término não pode ser anterior à data de início'}), 400
        
        item.updated_at = datetime.utcnow()
        db.session.commit()
        
        logger.info(f"[LOG] Cronograma atualizado: ID={item.id}, %={item.percentual_conclusao}")
        return jsonify(item.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] update_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao atualizar cronograma'}), 500


@cronograma_bp.route('/cronograma/<int:cronograma_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def delete_cronograma(cronograma_id):
    """Deleta uma etapa do cronograma"""
    # Tratar OPTIONS para CORS preflight
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'DELETE, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        # Verificar autenticação
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        item = CronogramaObra.query.get(cronograma_id)
        if not item:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        # Verificar acesso à obra
        if not user_has_access_to_obra(current_user, item.obra_id):
            return jsonify({'error': 'Acesso negado a esta obra'}), 403
        
        servico_nome = item.servico_nome
        db.session.delete(item)
        db.session.commit()
        
        logger.info(f"[LOG] Cronograma excluído: ID={cronograma_id}, Serviço={servico_nome}")
        return jsonify({'message': 'Etapa excluída com sucesso'}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] delete_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao excluir etapa'}), 500


# ==============================================================================
# ENDPOINTS DE ETAPAS DO CRONOGRAMA
# ==============================================================================

def recalcular_datas_etapas(cronograma_id):
    """
    Recalcula as datas das etapas em cascata considerando hierarquia.
    
    1. Para cada ETAPA PAI:
       - Recalcula datas das SUBETAPAS em cascata
       - Atualiza datas da etapa pai baseado nas subetapas
    
    2. Para ETAPAS PAI entre si:
       - Aplica condições de início (apos_termino, dias_apos, dias_antes)
    """
    try:
        # Buscar apenas etapas pai (não subetapas)
        etapas_pai = CronogramaEtapa.query.filter_by(
            cronograma_id=cronograma_id,
            etapa_pai_id=None
        ).order_by(CronogramaEtapa.ordem).all()
        
        if not etapas_pai:
            # Fallback: se não tem etapas pai, pode ser estrutura antiga
            etapas = CronogramaEtapa.query.filter_by(cronograma_id=cronograma_id).order_by(CronogramaEtapa.ordem).all()
            for i, etapa in enumerate(etapas):
                if i == 0:
                    etapa.calcular_data_fim()
                else:
                    if not etapa.inicio_ajustado_manualmente and etapas[i-1].data_fim:
                        etapa.data_inicio = etapas[i - 1].data_fim + timedelta(days=1)
                    etapa.calcular_data_fim()
        else:
            # Nova estrutura hierárquica
            for i, etapa_pai in enumerate(etapas_pai):
                # 1. Recalcular subetapas em cascata
                subetapas = CronogramaEtapa.query.filter_by(etapa_pai_id=etapa_pai.id).order_by(CronogramaEtapa.ordem).all()
                
                for j, sub in enumerate(subetapas):
                    if j == 0:
                        # Primeira subetapa: só calcular data_fim
                        sub.calcular_data_fim()
                    else:
                        # Subetapas seguintes
                        sub_anterior = subetapas[j - 1]
                        if not sub.inicio_ajustado_manualmente and sub_anterior.data_fim:
                            sub.data_inicio = sub_anterior.data_fim + timedelta(days=1)
                        sub.calcular_data_fim()
                
                # 2. Atualizar datas da etapa pai baseado nas subetapas
                etapa_pai.calcular_datas_das_subetapas()
                etapa_pai.percentual_conclusao = etapa_pai.calcular_percentual_das_subetapas()
                
                # 3. Aplicar condições entre etapas pai
                if i > 0 and not etapa_pai.inicio_ajustado_manualmente:
                    # Determinar etapa anterior (pode ser específica ou a anterior na ordem)
                    if etapa_pai.etapa_anterior_id:
                        etapa_anterior = CronogramaEtapa.query.get(etapa_pai.etapa_anterior_id)
                    else:
                        etapa_anterior = etapas_pai[i - 1]
                    
                    if etapa_anterior and etapa_anterior.data_fim:
                        nova_data = None
                        
                        if etapa_pai.tipo_condicao == 'apos_termino' or not etapa_pai.tipo_condicao:
                            nova_data = etapa_anterior.data_fim + timedelta(days=1)
                        elif etapa_pai.tipo_condicao == 'dias_apos':
                            nova_data = etapa_anterior.data_fim + timedelta(days=(etapa_pai.dias_offset or 0) + 1)
                        elif etapa_pai.tipo_condicao == 'dias_antes':
                            nova_data = etapa_anterior.data_fim - timedelta(days=(etapa_pai.dias_offset or 0))
                        
                        if nova_data and etapa_pai.data_inicio != nova_data:
                            # Calcular diferença para ajustar subetapas
                            if etapa_pai.data_inicio:
                                diferenca = (nova_data - etapa_pai.data_inicio).days
                                if diferenca != 0 and subetapas:
                                    primeira_sub = subetapas[0]
                                    if not primeira_sub.inicio_ajustado_manualmente:
                                        primeira_sub.data_inicio = nova_data
                                        primeira_sub.calcular_data_fim()
                                        # Recalcular subetapas em cascata novamente
                                        for k in range(1, len(subetapas)):
                                            if not subetapas[k].inicio_ajustado_manualmente:
                                                subetapas[k].data_inicio = subetapas[k-1].data_fim + timedelta(days=1)
                                            subetapas[k].calcular_data_fim()
                                        # Atualizar etapa pai
                                        etapa_pai.calcular_datas_das_subetapas()
        
        # Atualizar datas do cronograma pai
        cronograma = CronogramaObra.query.get(cronograma_id)
        if cronograma:
            cronograma.atualizar_datas_por_etapas()
            if cronograma.tipo_medicao == 'etapas':
                cronograma.percentual_conclusao = cronograma.calcular_percentual_por_etapas()
                
    except Exception as e:
        logger.exception(f"[AVISO] Erro ao recalcular datas das etapas: {str(e)}")


@cronograma_bp.route('/cronograma/<int:cronograma_id>/etapas', methods=['GET'])
@jwt_required()
def get_etapas_cronograma(cronograma_id):
    """Lista todas as etapas PAI de um item do cronograma (subetapas vêm dentro via to_dict)"""
    try:
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'error': 'Cronograma não encontrado'}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        # Buscar apenas etapas pai (etapa_pai_id IS NULL)
        # Subetapas são retornadas dentro de cada etapa pai via to_dict()
        try:
            etapas = CronogramaEtapa.query.filter_by(
                cronograma_id=cronograma_id,
                etapa_pai_id=None
            ).order_by(CronogramaEtapa.ordem).all()
        except Exception:
            # Fallback para compatibilidade (se coluna etapa_pai_id não existir)
            etapas = CronogramaEtapa.query.filter_by(cronograma_id=cronograma_id).order_by(CronogramaEtapa.ordem).all()
        
        return jsonify([etapa.to_dict() for etapa in etapas]), 200
    except Exception as e:
        logger.exception(f"[ERRO] get_etapas_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao buscar etapas'}), 500


@cronograma_bp.route('/cronograma/<int:cronograma_id>/etapas', methods=['POST', 'OPTIONS'])
@jwt_required()
def create_etapa_cronograma(cronograma_id):
    """
    Cria uma nova etapa ou subetapa no cronograma
    
    Para criar ETAPA PAI: não passar etapa_pai_id
    Para criar SUBETAPA: passar etapa_pai_id
    
    Campos especiais para ETAPA PAI:
    - etapa_anterior_id: ID da etapa anterior para condições de início
    - tipo_condicao: 'apos_termino', 'dias_apos', 'dias_antes', 'manual'
    - dias_offset: número de dias para offset
    """
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'error': 'Cronograma não encontrado'}), 404
        
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        data = request.json
        
        # Campos obrigatórios
        if 'nome' not in data:
            return jsonify({'error': 'Campo obrigatório: nome'}), 400
        
        # Verificar se é subetapa
        etapa_pai_id = data.get('etapa_pai_id')
        is_subetapa = etapa_pai_id is not None
        
        if is_subetapa:
            # === CRIANDO SUBETAPA ===
            etapa_pai = CronogramaEtapa.query.get(etapa_pai_id)
            if not etapa_pai:
                return jsonify({'error': 'Etapa pai não encontrada'}), 404
            
            # Determinar ordem (última subetapa + 1)
            ultima_sub = CronogramaEtapa.query.filter_by(etapa_pai_id=etapa_pai_id).order_by(CronogramaEtapa.ordem.desc()).first()
            nova_ordem = (ultima_sub.ordem + 1) if ultima_sub else 1
            
            # Determinar data_inicio
            duracao_dias = int(data.get('duracao_dias', 1))
            
            if 'data_inicio' in data and data['data_inicio']:
                data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
                inicio_ajustado = True
            elif ultima_sub and ultima_sub.data_fim:
                data_inicio = ultima_sub.data_fim + timedelta(days=1)
                inicio_ajustado = False
            elif etapa_pai.data_inicio:
                data_inicio = etapa_pai.data_inicio
                inicio_ajustado = False
            else:
                data_inicio = cronograma.data_inicio or date.today()
                inicio_ajustado = False
            
            data_fim = data_inicio + timedelta(days=duracao_dias - 1) if data_inicio else None
            
            nova_etapa = CronogramaEtapa(
                cronograma_id=cronograma_id,
                etapa_pai_id=etapa_pai_id,
                nome=data['nome'],
                ordem=nova_ordem,
                duracao_dias=duracao_dias,
                data_inicio=data_inicio,
                data_fim=data_fim,
                inicio_ajustado_manualmente=inicio_ajustado,
                percentual_conclusao=float(data.get('percentual_conclusao', 0)),
                observacoes=data.get('observacoes')
            )
        else:
            # === CRIANDO ETAPA PAI ===
            # Determinar ordem entre etapas pai
            ultima_etapa_pai = CronogramaEtapa.query.filter_by(
                cronograma_id=cronograma_id,
                etapa_pai_id=None
            ).order_by(CronogramaEtapa.ordem.desc()).first()
            nova_ordem = (ultima_etapa_pai.ordem + 1) if ultima_etapa_pai else 1
            
            # Condições de início (apenas para etapa pai)
            etapa_anterior_id = data.get('etapa_anterior_id')
            tipo_condicao = data.get('tipo_condicao', 'apos_termino')
            dias_offset = int(data.get('dias_offset', 0))
            
            # Determinar data_inicio baseado na condição
            data_inicio = None
            inicio_ajustado = False
            
            if 'data_inicio' in data and data['data_inicio']:
                data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
                inicio_ajustado = True
                tipo_condicao = 'manual'
            elif etapa_anterior_id:
                etapa_anterior = CronogramaEtapa.query.get(etapa_anterior_id)
                if etapa_anterior and etapa_anterior.data_fim:
                    if tipo_condicao == 'apos_termino':
                        data_inicio = etapa_anterior.data_fim + timedelta(days=1)
                    elif tipo_condicao == 'dias_apos':
                        data_inicio = etapa_anterior.data_fim + timedelta(days=dias_offset + 1)
                    elif tipo_condicao == 'dias_antes':
                        data_inicio = etapa_anterior.data_fim - timedelta(days=dias_offset)
            elif ultima_etapa_pai and ultima_etapa_pai.data_fim:
                # Usar última etapa como referência automática
                data_inicio = ultima_etapa_pai.data_fim + timedelta(days=1)
                etapa_anterior_id = ultima_etapa_pai.id
            else:
                # Primeira etapa: usar data do cronograma
                data_inicio = cronograma.data_inicio or date.today()
            
            nova_etapa = CronogramaEtapa(
                cronograma_id=cronograma_id,
                etapa_pai_id=None,  # É etapa pai
                nome=data['nome'],
                ordem=nova_ordem,
                duracao_dias=None,  # Calculado das subetapas
                data_inicio=data_inicio,
                data_fim=data_inicio,  # Será atualizado quando adicionar subetapas
                inicio_ajustado_manualmente=inicio_ajustado,
                etapa_anterior_id=etapa_anterior_id,
                tipo_condicao=tipo_condicao,
                dias_offset=dias_offset,
                percentual_conclusao=0,
                observacoes=data.get('observacoes')
            )
        
        db.session.add(nova_etapa)
        
        # Atualizar tipo do cronograma para 'etapas' se ainda não for
        if cronograma.tipo_medicao != 'etapas':
            cronograma.tipo_medicao = 'etapas'
        
        db.session.commit()
        
        # Se criou subetapa, atualizar datas da etapa pai
        if is_subetapa:
            recalcular_subetapas_cascata(etapa_pai_id)
        
        # Recalcular datas e percentuais do cronograma
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        tipo = "Subetapa" if is_subetapa else "Etapa"
        logger.info(f"[LOG] {tipo} criada: ID={nova_etapa.id}, Nome={nova_etapa.nome}, Cronograma={cronograma_id}")
        return jsonify(nova_etapa.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] create_etapa_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao criar etapa'}), 500


def recalcular_subetapas_cascata(etapa_pai_id):
    """Recalcula datas das subetapas em cascata e atualiza a etapa pai"""
    try:
        subetapas = CronogramaEtapa.query.filter_by(etapa_pai_id=etapa_pai_id).order_by(CronogramaEtapa.ordem).all()
        
        for i, sub in enumerate(subetapas):
            if i == 0:
                sub.calcular_data_fim()
            else:
                sub_anterior = subetapas[i - 1]
                if not sub.inicio_ajustado_manualmente and sub_anterior.data_fim:
                    sub.data_inicio = sub_anterior.data_fim + timedelta(days=1)
                sub.calcular_data_fim()
        
        # Atualizar datas da etapa pai
        etapa_pai = CronogramaEtapa.query.get(etapa_pai_id)
        if etapa_pai:
            etapa_pai.calcular_datas_das_subetapas()
        
        db.session.commit()
    except Exception as e:
        logger.exception(f"[AVISO] Erro ao recalcular subetapas: {str(e)}")


@cronograma_bp.route('/cronograma/<int:cronograma_id>/etapas/<int:etapa_id>', methods=['PUT', 'OPTIONS'])
@jwt_required()
def update_etapa_cronograma(cronograma_id, etapa_id):
    """Atualiza uma etapa do cronograma"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'PUT, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        etapa = CronogramaEtapa.query.get(etapa_id)
        if not etapa or etapa.cronograma_id != cronograma_id:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        data = request.json
        
        if 'nome' in data:
            etapa.nome = data['nome']
        
        if 'ordem' in data:
            etapa.ordem = int(data['ordem'])
        
        if 'duracao_dias' in data:
            etapa.duracao_dias = int(data['duracao_dias'])
        
        if 'data_inicio' in data and data['data_inicio']:
            etapa.data_inicio = datetime.strptime(data['data_inicio'], '%Y-%m-%d').date()
            etapa.inicio_ajustado_manualmente = True
        
        if 'percentual_conclusao' in data:
            etapa.percentual_conclusao = max(0, min(100, float(data['percentual_conclusao'])))
        
        if 'observacoes' in data:
            etapa.observacoes = data['observacoes']
        
        # Resetar ajuste manual se solicitado
        if data.get('resetar_ajuste_manual'):
            etapa.inicio_ajustado_manualmente = False
        
        etapa.updated_at = datetime.utcnow()
        db.session.commit()
        
        # Recalcular datas em cascata
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        # Recarregar etapa atualizada
        etapa = CronogramaEtapa.query.get(etapa_id)
        
        logger.info(f"[LOG] Etapa atualizada: ID={etapa_id}, Nome={etapa.nome}")
        return jsonify(etapa.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] update_etapa_cronograma: {str(e)}\n{error_details}")
        return jsonify({'error': 'Erro ao atualizar etapa'}), 500


@cronograma_bp.route('/cronograma/<int:cronograma_id>/etapas/<int:etapa_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def delete_etapa_cronograma(cronograma_id, etapa_id):
    """Exclui uma etapa do cronograma"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'DELETE, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        etapa = CronogramaEtapa.query.get(etapa_id)
        if not etapa or etapa.cronograma_id != cronograma_id:
            return jsonify({'error': 'Etapa não encontrada'}), 404
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        nome_etapa = etapa.nome
        db.session.delete(etapa)
        db.session.commit()
        
        # Recalcular datas das etapas restantes
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        logger.info(f"[LOG] Etapa excluída: ID={etapa_id}, Nome={nome_etapa}")
        return jsonify({'message': 'Etapa excluída com sucesso'}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] delete_etapa_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao excluir etapa'}), 500


@cronograma_bp.route('/cronograma/<int:cronograma_id>/etapas/reordenar', methods=['PUT', 'OPTIONS'])
@jwt_required()
def reordenar_etapas_cronograma(cronograma_id):
    """Reordena as etapas do cronograma"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'PUT, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Usuário não autenticado'}), 401
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'error': 'Cronograma não encontrado'}), 404
        
        if not user_has_access_to_obra(current_user, cronograma.obra_id):
            return jsonify({'error': 'Acesso negado'}), 403
        
        data = request.json
        # Espera: {"ordem": [{"id": 1, "ordem": 1}, {"id": 2, "ordem": 2}, ...]}
        
        if 'ordem' not in data:
            return jsonify({'error': 'Campo obrigatório: ordem'}), 400
        
        for item in data['ordem']:
            etapa = CronogramaEtapa.query.get(item['id'])
            if etapa and etapa.cronograma_id == cronograma_id:
                etapa.ordem = item['ordem']
                # Resetar ajuste manual para recalcular em cascata
                if item.get('resetar_ajuste'):
                    etapa.inicio_ajustado_manualmente = False
        
        db.session.commit()
        
        # Recalcular datas
        recalcular_datas_etapas(cronograma_id)
        db.session.commit()
        
        return jsonify({'message': 'Etapas reordenadas com sucesso'}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] reordenar_etapas_cronograma: {str(e)}")
        return jsonify({'error': 'Erro ao reordenar etapas'}), 500


# ==============================================================================
# IMPORTAR ETAPAS DO ORÇAMENTO PARA O CRONOGRAMA
# ==============================================================================

@cronograma_bp.route('/obras/<int:obra_id>/cronograma/importar-orcamento', methods=['GET'])
@jwt_required()
def listar_etapas_orcamento_para_cronograma(obra_id):
    """
    Lista as etapas do orçamento de engenharia disponíveis para importar no cronograma.
    Retorna apenas etapas que ainda não foram importadas.
    """
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão para acessar esta obra"}), 403
        
        # Buscar etapas do orçamento de engenharia
        etapas_orcamento = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).order_by(OrcamentoEngEtapa.ordem, OrcamentoEngEtapa.codigo).all()
        
        # Buscar serviços já existentes no cronograma
        cronograma_existente = CronogramaObra.query.filter_by(obra_id=obra_id).all()
        nomes_cronograma = [c.servico_nome.lower().strip() for c in cronograma_existente]
        
        # Filtrar etapas não importadas
        etapas_disponiveis = []
        for etapa in etapas_orcamento:
            # Calcular totais da etapa
            total_mo = 0
            total_mat = 0
            total_pago = 0
            
            for item in etapa.itens:
                totais = item.calcular_totais()
                total_mo += totais['total_mao_obra']
                total_mat += totais['total_material']
                total_pago += (item.valor_pago_mo or 0) + (item.valor_pago_mat or 0)
            
            etapa_total = total_mo + total_mat
            
            # Verificar se já existe no cronograma
            ja_importado = etapa.nome.lower().strip() in nomes_cronograma
            
            etapas_disponiveis.append({
                'id': etapa.id,
                'codigo': etapa.codigo,
                'nome': etapa.nome,
                'total_mao_obra': total_mo,
                'total_material': total_mat,
                'total': etapa_total,
                'total_pago': total_pago,
                'percentual_pago': round((total_pago / etapa_total * 100) if etapa_total > 0 else 0, 1),
                'qtd_itens': len(etapa.itens),
                'ja_importado': ja_importado
            })
        
        return jsonify({
            'etapas': etapas_disponiveis,
            'total_disponiveis': len([e for e in etapas_disponiveis if not e['ja_importado']]),
            'total_importadas': len([e for e in etapas_disponiveis if e['ja_importado']])
        })
        
    except Exception as e:
        logger.exception(f"[ERRO] listar_etapas_orcamento_para_cronograma: {e}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/cronograma/importar-orcamento', methods=['POST', 'OPTIONS'])
@jwt_required()
def importar_orcamento_para_cronograma(obra_id):
    """
    Importa etapas selecionadas do orçamento de engenharia para o cronograma.
    
    Espera no body:
    {
        "etapa_ids": [1, 2, 3],  // IDs das etapas do orçamento
        "data_inicio": "2026-01-15",  // Data de início para a primeira etapa
        "duracao_padrao": 30  // Duração padrão em dias para cada serviço
    }
    """
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão para acessar esta obra"}), 403
        
        data = request.json
        etapa_ids = data.get('etapa_ids', [])
        data_inicio_str = data.get('data_inicio')
        duracao_padrao = data.get('duracao_padrao', 30)
        
        if not etapa_ids:
            return jsonify({"erro": "Nenhuma etapa selecionada"}), 400
        
        # Converter data de início
        if data_inicio_str:
            data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
        else:
            data_inicio = date.today()
        
        # Buscar ordem atual máxima do cronograma
        max_ordem = db.session.query(db.func.max(CronogramaObra.ordem)).filter_by(obra_id=obra_id).scalar() or 0
        
        # Buscar nomes já existentes no cronograma
        cronograma_existente = CronogramaObra.query.filter_by(obra_id=obra_id).all()
        nomes_cronograma = [c.servico_nome.lower().strip() for c in cronograma_existente]
        
        servicos_criados = []
        data_atual = data_inicio
        
        for etapa_id in etapa_ids:
            etapa = OrcamentoEngEtapa.query.get(etapa_id)
            if not etapa or etapa.obra_id != obra_id:
                continue
            
            # Pular se já existe no cronograma
            if etapa.nome.lower().strip() in nomes_cronograma:
                continue
            
            max_ordem += 1
            
            # Calcular data fim
            data_fim = data_atual + timedelta(days=duracao_padrao - 1)
            
            # Criar serviço no cronograma
            novo_servico = CronogramaObra(
                obra_id=obra_id,
                servico_nome=etapa.nome,
                ordem=max_ordem,
                data_inicio=data_atual,
                data_fim_prevista=data_fim,
                tipo_medicao='etapas',  # Por padrão, usar medição por etapas
                percentual_conclusao=0,
                observacoes=f"Importado do Orçamento de Engenharia - {etapa.codigo}"
            )
            
            db.session.add(novo_servico)
            db.session.flush()  # Para obter o ID
            
            # Tentar vincular ao orçamento (coluna pode não existir)
            try:
                db.session.execute(db.text(
                    f"UPDATE cronograma_obra SET orcamento_etapa_id = {etapa.id} WHERE id = {novo_servico.id}"
                ))
            except Exception:
                logger.debug("Coluna orcamento_etapa_id nao existe, ignorando", exc_info=True)
                pass  # Coluna não existe, ignorar

            # Criar etapa pai no cronograma correspondente
            etapa_cronograma = CronogramaEtapa(
                cronograma_id=novo_servico.id,
                nome=etapa.nome,
                ordem=1,
                duracao_dias=duracao_padrao,
                data_inicio=data_atual,
                data_fim=data_fim,
                percentual_conclusao=0,
                observacoes=f"Código: {etapa.codigo}"
            )
            db.session.add(etapa_cronograma)
            
            servicos_criados.append({
                'id': novo_servico.id,
                'nome': etapa.nome,
                'codigo_origem': etapa.codigo,
                'data_inicio': data_atual.isoformat(),
                'data_fim': data_fim.isoformat()
            })
            
            # Avançar data para o próximo serviço
            data_atual = data_fim + timedelta(days=1)
            
            # Adicionar aos nomes existentes para evitar duplicação dentro do mesmo lote
            nomes_cronograma.append(etapa.nome.lower().strip())
        
        db.session.commit()
        
        return jsonify({
            'message': f'{len(servicos_criados)} serviço(s) importado(s) com sucesso',
            'servicos_criados': servicos_criados,
            'total_importados': len(servicos_criados)
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] importar_orcamento_para_cronograma: {e}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor"}), 500


def _buscar_orfaos_cronograma_orcamento(obra_id):
    """
    Candidatos a órfão: itens do cronograma que vieram do orçamento (via
    orcamento_etapa_id OU pela marca "Importado ... do Orçamento" deixada em
    observacoes na criação) e cujo nome não bate com nenhuma etapa atual do
    orçamento.

    NÃO dá pra confiar só em orcamento_etapa_id apontar para uma etapa
    inexistente: a coluna tem `ON DELETE SET NULL`, então o Postgres já zera
    a referência no instante em que a etapa é apagada — o "órfão" nunca fica
    com um ID pendurado pra gente encontrar depois. observacoes sobrevive a
    esse SET NULL e é o único sinal que resta.
    """
    etapas_validas_nomes = {
        (row[0] or '').lower().strip()
        for row in db.session.execute(db.text(
            "SELECT nome FROM orcamento_eng_etapa WHERE obra_id = :obra_id"
        ), {"obra_id": obra_id}).fetchall()
    }

    candidatos = db.session.execute(db.text("""
        SELECT id, servico_nome
        FROM cronograma_obra
        WHERE obra_id = :obra_id
          AND (orcamento_etapa_id IS NOT NULL OR observacoes LIKE '%Orçamento%')
    """), {"obra_id": obra_id}).fetchall()

    return [
        {"id": row[0], "nome": row[1]}
        for row in candidatos
        if (row[1] or '').lower().strip() not in etapas_validas_nomes
    ]


@cronograma_bp.route('/obras/<int:obra_id>/cronograma/sincronizar-orcamento', methods=['GET'])
@jwt_required()
def listar_orfaos_cronograma_orcamento(obra_id):
    """
    Lista itens do cronograma que vieram do orçamento (import ou auto-sync)
    e cuja etapa de origem não existe mais lá (foi apagada). Não considera
    itens criados manualmente no cronograma.
    """
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão para acessar esta obra"}), 403

        orfaos = _buscar_orfaos_cronograma_orcamento(obra_id)
        return jsonify({"orfaos": orfaos, "total": len(orfaos)})
    except Exception as e:
        logger.exception(f"[ERRO] listar_orfaos_cronograma_orcamento: {e}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/cronograma/sincronizar-orcamento', methods=['POST', 'OPTIONS'])
@jwt_required()
def sincronizar_cronograma_com_orcamento(obra_id):
    """
    Remove do cronograma os itens que vieram do orçamento (import ou
    auto-sync) cuja etapa de origem não existe mais lá. Não toca em itens
    criados manualmente no cronograma.
    """
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response

    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão para acessar esta obra"}), 403

        orfaos_ids = [o['id'] for o in _buscar_orfaos_cronograma_orcamento(obra_id)]

        removidos = 0
        for cid in orfaos_ids:
            item = CronogramaObra.query.get(cid)
            if item:
                db.session.delete(item)
                removidos += 1

        db.session.commit()
        return jsonify({"message": f"{removidos} item(ns) órfão(s) removido(s)", "removidos": removidos})
    except Exception as e:
        db.session.rollback()
        logger.exception(f"[ERRO] sincronizar_cronograma_com_orcamento: {e}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/cronograma/limpar', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def limpar_cronograma(obra_id):
    """Remove TODOS os itens do cronograma da obra. Ação explícita "Limpar", restrita a master/administrador."""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'DELETE, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response

    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão para acessar esta obra"}), 403
        if user.role not in ['master', 'administrador']:
            return jsonify({"erro": "Apenas administradores podem limpar o cronograma"}), 403

        itens = CronogramaObra.query.filter_by(obra_id=obra_id).all()
        total = len(itens)
        for item in itens:
            db.session.delete(item)
        db.session.commit()

        return jsonify({"message": f"{total} item(ns) removido(s) do cronograma", "removidos": total})
    except Exception as e:
        db.session.rollback()
        logger.exception(f"[ERRO] limpar_cronograma: {e}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@jwt_required()
def exportar_servicos_csv(obra_id):
    """Exporta a planilha de serviços para CSV"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        # Buscar todos os serviços da obra
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        
        # Criar CSV em memória
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Cabeçalho
        writer.writerow([
            'Serviço',
            'Responsável',
            'Valor Global Mão de Obra',
            'Valor Global Material',
            'Mão de Obra Orçada',
            'Mão de Obra Paga',
            'Mão de Obra Restante',
            '% Mão de Obra',
            'Material Orçado',
            'Material Pago',
            'Material Restante',
            '% Material',
            'Total Orçado',
            'Total Pago',
            'Total Restante',
            '% Total Executado'
        ])
        
        # Dados
        for servico in servicos:
            # Calcular valores de mão de obra
            mao_obra_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'mao_de_obra'
            )
            mao_obra_orcado = servico.valor_global_mao_de_obra
            mao_obra_restante = mao_obra_orcado - mao_obra_pago
            perc_mao_obra = (mao_obra_pago / mao_obra_orcado * 100) if mao_obra_orcado > 0 else 0
            
            # Calcular valores de material
            material_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'material'
            )
            material_orcado = servico.valor_global_material
            material_restante = material_orcado - material_pago
            perc_material = (material_pago / material_orcado * 100) if material_orcado > 0 else 0
            
            # Totais
            total_orcado = mao_obra_orcado + material_orcado
            total_pago = mao_obra_pago + material_pago
            total_restante = total_orcado - total_pago
            perc_total = (total_pago / total_orcado * 100) if total_orcado > 0 else 0
            
            writer.writerow([
                servico.nome,
                servico.responsavel or '-',
                f'R$ {mao_obra_orcado:,.2f}',
                f'R$ {material_orcado:,.2f}',
                f'R$ {mao_obra_orcado:,.2f}',
                f'R$ {mao_obra_pago:,.2f}',
                f'R$ {mao_obra_restante:,.2f}',
                f'{perc_mao_obra:.1f}%',
                f'R$ {material_orcado:,.2f}',
                f'R$ {material_pago:,.2f}',
                f'R$ {material_restante:,.2f}',
                f'{perc_material:.1f}%',
                f'R$ {total_orcado:,.2f}',
                f'R$ {total_pago:,.2f}',
                f'R$ {total_restante:,.2f}',
                f'{perc_total:.1f}%'
            ])
        
        # Preparar para download
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),  # UTF-8 com BOM para Excel
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'Servicos_{obra.nome.replace(" ", "_")}_{date.today()}.csv'
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] exportar_servicos_csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/cronograma-financeiro/exportar-csv', methods=['GET'])
@jwt_required()
def exportar_cronograma_csv(obra_id):
    """Exporta o cronograma financeiro para CSV"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        hoje = date.today()
        
        # Buscar dados
        pagamentos_futuros = PagamentoFuturo.query.filter_by(obra_id=obra_id).all()
        pagamentos_parcelados = PagamentoParcelado.query.filter_by(obra_id=obra_id).all()
        
        # Criar CSV em memória
        output = io.StringIO()
        writer = csv.writer(output)
        
        # SEÇÃO 1: PAGAMENTOS FUTUROS (ÚNICOS)
        writer.writerow(['===== PAGAMENTOS FUTUROS (ÚNICOS) ====='])
        writer.writerow([
            'Descrição',
            'Fornecedor',
            'Vencimento',
            'Valor',
            'Status',
            'Tipo',
            'Serviço Vinculado'
        ])
        
        for pag in pagamentos_futuros:
            servico_nome = '-'
            if pag.servico_id:
                servico = Servico.query.get(pag.servico_id)
                servico_nome = servico.nome if servico else '-'
            
            status_display = 'Pago' if pag.status == 'Pago' else ('Vencido' if pag.data_vencimento < hoje else 'Previsto')
            tipo_display = pag.tipo if hasattr(pag, 'tipo') and pag.tipo else '-'
            
            writer.writerow([
                pag.descricao,
                pag.fornecedor or '-',
                pag.data_vencimento.strftime('%d/%m/%Y') if pag.data_vencimento else '-',
                f'R$ {pag.valor:,.2f}',
                status_display,
                tipo_display,
                servico_nome
            ])
        
        writer.writerow([])  # Linha em branco
        
        # SEÇÃO 2: PAGAMENTOS PARCELADOS
        writer.writerow(['===== PAGAMENTOS PARCELADOS ====='])
        writer.writerow([
            'Descrição',
            'Fornecedor',
            'Valor Total',
            'Parcelas',
            'Valor/Parcela',
            'Periodicidade',
            'Parcelas Pagas',
            'Status',
            'Segmento',
            'Serviço Vinculado'
        ])
        
        for pag in pagamentos_parcelados:
            servico_nome = '-'
            if pag.servico_id:
                servico = Servico.query.get(pag.servico_id)
                servico_nome = servico.nome if servico else '-'
            
            segmento = 'Material'
            try:
                if hasattr(pag, 'segmento') and pag.segmento:
                    segmento = pag.segmento
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)
                pass
            
            writer.writerow([
                pag.descricao,
                pag.fornecedor or '-',
                f'R$ {pag.valor_total:,.2f}',
                f'{pag.numero_parcelas}',
                f'R$ {pag.valor_parcela:,.2f}',
                pag.periodicidade,
                f'{pag.parcelas_pagas}/{pag.numero_parcelas}',
                pag.status,
                segmento,
                servico_nome
            ])
        
        writer.writerow([])  # Linha em branco
        
        # SEÇÃO 3: RESUMO FINANCEIRO
        writer.writerow(['===== RESUMO FINANCEIRO ====='])
        
        # Calcular totais
        total_futuros_previsto = sum(p.valor for p in pagamentos_futuros if p.status != 'Pago' and p.data_vencimento >= hoje)
        total_futuros_vencido = sum(p.valor for p in pagamentos_futuros if p.status != 'Pago' and p.data_vencimento < hoje)
        total_futuros_pago = sum(p.valor for p in pagamentos_futuros if p.status == 'Pago')
        
        total_parcelado = sum(p.valor_total for p in pagamentos_parcelados)
        total_parcelado_pago = sum(p.parcelas_pagas * p.valor_parcela for p in pagamentos_parcelados)
        total_parcelado_restante = total_parcelado - total_parcelado_pago
        
        writer.writerow([
            'Total Pagamentos Futuros (Previstos)',
            f'R$ {total_futuros_previsto:,.2f}'
        ])
        writer.writerow([
            'Total Pagamentos Futuros (Vencidos)',
            f'R$ {total_futuros_vencido:,.2f}'
        ])
        writer.writerow([
            'Total Pagamentos Futuros (Pagos)',
            f'R$ {total_futuros_pago:,.2f}'
        ])
        writer.writerow([
            'Total Parcelados (Valor Total)',
            f'R$ {total_parcelado:,.2f}'
        ])
        writer.writerow([
            'Total Parcelados (Já Pago)',
            f'R$ {total_parcelado_pago:,.2f}'
        ])
        writer.writerow([
            'Total Parcelados (Restante)',
            f'R$ {total_parcelado_restante:,.2f}'
        ])
        writer.writerow([
            'TOTAL GERAL A PAGAR',
            f'R$ {(total_futuros_previsto + total_futuros_vencido + total_parcelado_restante):,.2f}'
        ])
        
        # Preparar para download
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'Cronograma_{obra.nome.replace(" ", "_")}_{date.today()}.csv'
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] exportar_cronograma_csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

# ==============================================================================



# ==============================================================================
# ROTAS EXATAS DO FRONTEND - Pagamentos Futuros com servico-ID
# ==============================================================================

@cronograma_bp.route('/obras/<int:obra_id>/agenda', methods=['GET'])
@jwt_required()
def get_agenda_demandas(obra_id):
    """Lista todas as demandas da agenda de uma obra"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Atualizar status de atrasados automaticamente
        hoje = date.today()
        demandas_atrasadas = AgendaDemanda.query.filter(
            AgendaDemanda.obra_id == obra_id,
            AgendaDemanda.status == 'aguardando',
            AgendaDemanda.data_prevista < hoje
        ).all()
        
        for demanda in demandas_atrasadas:
            demanda.status = 'atrasado'
        
        if demandas_atrasadas:
            db.session.commit()
        
        # Buscar todas as demandas
        demandas = AgendaDemanda.query.filter_by(obra_id=obra_id).order_by(
            AgendaDemanda.data_prevista.asc()
        ).all()
        
        return jsonify([d.to_dict() for d in demandas]), 200
        
    except Exception as e:
        logger.exception(f"[ERRO] get_agenda_demandas: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda', methods=['POST', 'OPTIONS'])
@jwt_required()
def criar_agenda_demanda(obra_id):
    """Cria uma nova demanda na agenda (manual ou importada)"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.json
        
        # Validação
        if not data.get('descricao'):
            return jsonify({"erro": "Descrição é obrigatória"}), 400
        if not data.get('data_prevista'):
            return jsonify({"erro": "Data é obrigatória"}), 400
        
        # Criar demanda
        demanda = AgendaDemanda(
            obra_id=obra_id,
            descricao=data.get('descricao'),
            tipo=data.get('tipo', 'material'),
            fornecedor=data.get('fornecedor'),
            telefone=data.get('telefone'),
            valor=float(data.get('valor')) if data.get('valor') else None,
            data_prevista=datetime.strptime(data.get('data_prevista'), '%Y-%m-%d').date(),
            horario=data.get('horario'),
            status='aguardando',
            origem=data.get('origem', 'manual'),
            pagamento_servico_id=data.get('pagamento_servico_id'),
            orcamento_item_id=data.get('orcamento_item_id'),
            servico_id=data.get('servico_id'),
            observacoes=data.get('observacoes')
        )
        
        db.session.add(demanda)
        db.session.commit()
        
        logger.info(f"[LOG] Demanda criada: {demanda.descricao} (origem: {demanda.origem})")
        
        return jsonify(demanda.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] criar_agenda_demanda: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/<int:demanda_id>', methods=['PUT', 'OPTIONS'])
@jwt_required()
def atualizar_agenda_demanda(obra_id, demanda_id):
    """Atualiza uma demanda da agenda"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        data = request.json
        
        # Atualizar campos
        if 'descricao' in data:
            demanda.descricao = data['descricao']
        if 'tipo' in data:
            demanda.tipo = data['tipo']
        if 'fornecedor' in data:
            demanda.fornecedor = data['fornecedor']
        if 'telefone' in data:
            demanda.telefone = data['telefone']
        if 'valor' in data:
            demanda.valor = float(data['valor']) if data['valor'] else None
        if 'data_prevista' in data:
            demanda.data_prevista = datetime.strptime(data['data_prevista'], '%Y-%m-%d').date()
        if 'horario' in data:
            demanda.horario = data['horario']
        if 'observacoes' in data:
            demanda.observacoes = data['observacoes']
        
        db.session.commit()
        
        return jsonify(demanda.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] atualizar_agenda_demanda: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/<int:demanda_id>/concluir', methods=['PUT', 'OPTIONS'])
@jwt_required()
def concluir_agenda_demanda(obra_id, demanda_id):
    """Marca uma demanda como concluída"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        data = request.json or {}
        
        demanda.status = 'concluido'
        demanda.data_conclusao = datetime.strptime(data.get('data_conclusao'), '%Y-%m-%d').date() if data.get('data_conclusao') else date.today()
        
        if data.get('observacoes'):
            obs_atual = demanda.observacoes or ''
            demanda.observacoes = f"{obs_atual}\n[Conclusão] {data.get('observacoes')}".strip()
        
        db.session.commit()
        
        logger.info(f"[LOG] Demanda concluída: {demanda.descricao}")
        
        return jsonify(demanda.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] concluir_agenda_demanda: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/<int:demanda_id>/reabrir', methods=['PUT', 'OPTIONS'])
@jwt_required()
def reabrir_agenda_demanda(obra_id, demanda_id):
    """Reabre uma demanda concluída"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        # Verificar se está atrasada
        hoje = date.today()
        if demanda.data_prevista < hoje:
            demanda.status = 'atrasado'
        else:
            demanda.status = 'aguardando'
        
        demanda.data_conclusao = None
        
        db.session.commit()
        
        return jsonify(demanda.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] reabrir_agenda_demanda: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/<int:demanda_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def excluir_agenda_demanda(obra_id, demanda_id):
    """Exclui uma demanda da agenda"""
    if request.method == 'OPTIONS':
        return jsonify({"message": "OK"}), 200
    
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        demanda = AgendaDemanda.query.filter_by(id=demanda_id, obra_id=obra_id).first()
        if not demanda:
            return jsonify({"erro": "Demanda não encontrada"}), 404
        
        descricao = demanda.descricao
        db.session.delete(demanda)
        db.session.commit()
        
        logger.info(f"[LOG] Demanda excluída: {descricao}")
        
        return jsonify({"mensagem": "Demanda excluída com sucesso"}), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] excluir_agenda_demanda: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/importar/pagamentos', methods=['GET'])
@jwt_required()
def listar_pagamentos_para_importar(obra_id):
    """Lista TODOS os pagamentos que podem ser importados para a agenda"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # IDs já importados da agenda
        demandas_existentes = AgendaDemanda.query.filter_by(obra_id=obra_id).all()
        ids_pag_servico = set(d.pagamento_servico_id for d in demandas_existentes if d.pagamento_servico_id)
        
        resultado = []
        
        # 1. PAGAMENTOS DE SERVIÇO (PagamentoServico) - Material e Mão de Obra
        pagamentos_servico = PagamentoServico.query.join(Servico).filter(
            Servico.obra_id == obra_id
        ).order_by(PagamentoServico.data.desc()).all()
        
        for p in pagamentos_servico:
            if p.id not in ids_pag_servico:
                servico = Servico.query.get(p.servico_id)
                tipo_display = "Material" if p.tipo_pagamento == 'material' else "Mão de Obra"
                resultado.append({
                    'id': p.id,
                    'fonte': 'pagamento_servico',
                    'descricao': f"{tipo_display} - {servico.nome}" if servico else tipo_display,
                    'servico': servico.nome if servico else None,
                    'fornecedor': p.fornecedor,
                    'valor': float(p.valor_pago) if p.valor_pago else float(p.valor_total) if p.valor_total else 0,
                    'data_pagamento': p.data.isoformat() if p.data else None,
                    'status': p.status,
                    'tipo': p.tipo_pagamento,
                    'telefone': None
                })
        
        # 2. PAGAMENTOS FUTUROS (PagamentoFuturo)
        pagamentos_futuros = PagamentoFuturo.query.filter_by(obra_id=obra_id).order_by(
            PagamentoFuturo.data_vencimento.asc()
        ).all()
        
        for pf in pagamentos_futuros:
            # Verificar se já foi importado (usando descrição como chave)
            ja_importado = any(
                d.descricao == pf.descricao and d.origem == 'pagamento' 
                for d in demandas_existentes
            )
            if not ja_importado:
                servico = Servico.query.get(pf.servico_id) if pf.servico_id else None
                resultado.append({
                    'id': f"futuro_{pf.id}",
                    'fonte': 'pagamento_futuro',
                    'descricao': pf.descricao,
                    'servico': servico.nome if servico else None,
                    'fornecedor': pf.fornecedor,
                    'valor': float(pf.valor) if pf.valor else 0,
                    'data_pagamento': pf.data_vencimento.isoformat() if pf.data_vencimento else None,
                    'status': pf.status,
                    'tipo': pf.tipo or 'Despesa',
                    'telefone': None
                })
        
        # 3. PAGAMENTOS PARCELADOS (PagamentoParcelado)
        pagamentos_parcelados = PagamentoParcelado.query.filter_by(obra_id=obra_id).order_by(
            PagamentoParcelado.data_primeira_parcela.asc()
        ).all()
        
        for pp in pagamentos_parcelados:
            # Verificar se já foi importado
            ja_importado = any(
                d.descricao == pp.descricao and d.origem == 'pagamento' 
                for d in demandas_existentes
            )
            if not ja_importado:
                servico = Servico.query.get(pp.servico_id) if pp.servico_id else None
                resultado.append({
                    'id': f"parcelado_{pp.id}",
                    'fonte': 'pagamento_parcelado',
                    'descricao': f"{pp.descricao} ({pp.parcelas_pagas}/{pp.numero_parcelas} parcelas)",
                    'servico': servico.nome if servico else None,
                    'fornecedor': pp.fornecedor,
                    'valor': float(pp.valor_total) if pp.valor_total else 0,
                    'data_pagamento': pp.data_primeira_parcela.isoformat() if pp.data_primeira_parcela else None,
                    'status': pp.status,
                    'tipo': pp.segmento or 'Material',
                    'telefone': None
                })
        
        return jsonify(resultado), 200
        
    except Exception as e:
        logger.exception(f"[ERRO] listar_pagamentos_para_importar: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/importar/orcamento', methods=['GET'])
@jwt_required()
def listar_orcamento_para_importar(obra_id):
    """Lista itens do orçamento de engenharia que podem ser importados para a agenda"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Buscar itens do orçamento de engenharia
        itens = db.session.query(OrcamentoEngItem, OrcamentoEngEtapa).join(
            OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id
        ).filter(
            OrcamentoEngEtapa.obra_id == obra_id
        ).all()
        
        # IDs já importados
        ids_importados = set(
            d.orcamento_item_id for d in AgendaDemanda.query.filter_by(obra_id=obra_id).all()
            if d.orcamento_item_id
        )
        
        resultado = []
        for item, etapa in itens:
            if item.id not in ids_importados:
                # Calcular valor total
                if item.tipo_composicao == 'separado':
                    valor_total = item.quantidade * ((item.preco_mao_obra or 0) + (item.preco_material or 0))
                else:
                    valor_total = item.quantidade * (item.preco_unitario or 0)
                
                resultado.append({
                    'id': item.id,
                    'descricao': item.descricao,
                    'etapa': etapa.nome,
                    'quantidade': f"{item.quantidade} {item.unidade}",
                    'valor': float(valor_total),
                })
        
        return jsonify(resultado), 200
        
    except Exception as e:
        logger.exception(f"[ERRO] listar_orcamento_para_importar: {str(e)}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/importar/servicos', methods=['GET'])
@jwt_required()
def listar_servicos_para_importar(obra_id):
    """Lista serviços do cronograma com data de início FUTURA para importar como eventos"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        hoje = date.today()
        
        # Buscar serviços do cronograma com data de início FUTURA
        servicos = CronogramaObra.query.filter(
            CronogramaObra.obra_id == obra_id,
            CronogramaObra.data_inicio.isnot(None),
            CronogramaObra.data_inicio > hoje  # Só futuros
        ).order_by(CronogramaObra.data_inicio.asc()).all()
        
        logger.info(f"[LOG] Encontrados {len(servicos)} serviços FUTUROS no cronograma para obra {obra_id}")
        
        # IDs já importados
        demandas_existentes = AgendaDemanda.query.filter_by(obra_id=obra_id, origem='cronograma').all()
        servicos_importados = set(d.servico_id for d in demandas_existentes if d.servico_id)
        descricoes_importadas = set(d.descricao for d in demandas_existentes)
        
        resultado = []
        for s in servicos:
            # Verificar se já foi importado
            if s.id in servicos_importados:
                continue
            if f"Início: {s.servico_nome}" in descricoes_importadas:
                continue
                
            resultado.append({
                'id': s.id,
                'nome': s.servico_nome,
                'etapa': None,
                'data_inicio': s.data_inicio.isoformat() if s.data_inicio else None,
                'data_termino': s.data_fim_prevista.isoformat() if s.data_fim_prevista else None,
                'responsavel': None,
                'status': 'A Iniciar',
                'percentual': 0
            })
        
        logger.info(f"[LOG] {len(resultado)} serviços futuros disponíveis para importar")
        return jsonify(resultado), 200
        
    except Exception as e:
        logger.exception(f"[ERRO] listar_servicos_para_importar: {str(e)}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor"}), 500


@cronograma_bp.route('/obras/<int:obra_id>/agenda/sincronizar-cronograma', methods=['POST'])
@jwt_required()
def sincronizar_cronograma_agenda(obra_id):
    """
    Sincroniza automaticamente os serviços do cronograma com a agenda.
    Importa serviços com data de início futura que ainda não foram importados.
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        hoje = date.today()
        
        # Buscar serviços do cronograma com data de início FUTURA
        servicos = CronogramaObra.query.filter(
            CronogramaObra.obra_id == obra_id,
            CronogramaObra.data_inicio.isnot(None),
            CronogramaObra.data_inicio > hoje
        ).all()
        
        # IDs já importados
        demandas_existentes = AgendaDemanda.query.filter_by(obra_id=obra_id, origem='cronograma').all()
        servicos_importados = set(d.servico_id for d in demandas_existentes if d.servico_id)
        descricoes_importadas = set(d.descricao for d in demandas_existentes)
        
        importados = 0
        for s in servicos:
            # Verificar se já foi importado
            if s.id in servicos_importados:
                continue
            if f"Início: {s.servico_nome}" in descricoes_importadas:
                continue
            
            # Criar demanda automaticamente
            demanda = AgendaDemanda(
                obra_id=obra_id,
                descricao=f"Início: {s.servico_nome}",
                tipo='servico',
                fornecedor=None,
                telefone=None,
                valor=None,
                data_prevista=s.data_inicio,
                horario=None,
                status='aguardando',
                origem='cronograma',
                servico_id=s.id,
                observacoes=f"Término previsto: {s.data_fim_prevista.strftime('%d/%m/%Y') if s.data_fim_prevista else '-'}"
            )
            db.session.add(demanda)
            importados += 1
        
        db.session.commit()
        logger.info(f"[LOG] Sincronização automática: {importados} serviços importados para agenda da obra {obra_id}")
        
        return jsonify({
            "sucesso": True,
            "importados": importados,
            "mensagem": f"{importados} serviços sincronizados com a agenda"
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] sincronizar_cronograma_agenda: {str(e)}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor"}), 500






@jwt_required()
def listar_servicos_base():
    """
    Lista serviços da base de referência com autocomplete
    Query params: q (busca), categoria
    """
    try:
        q = request.args.get('q', '').strip().lower()
        categoria = request.args.get('categoria', '')
        
        query = ServicoBase.query
        
        if q:
            query = query.filter(ServicoBase.descricao.ilike(f'%{q}%'))
        
        if categoria:
            query = query.filter(ServicoBase.categoria == categoria)
        
        servicos = query.order_by(ServicoBase.categoria, ServicoBase.descricao).limit(50).all()
        
        # Agrupar por categoria
        categorias = {}
        for s in servicos:
            if s.categoria not in categorias:
                categorias[s.categoria] = []
            categorias[s.categoria].append(s.to_dict())
        
        return jsonify({
            'servicos': [s.to_dict() for s in servicos],
            'por_categoria': categorias,
            'total': len(servicos)
        })
        
    except Exception as e:
        return jsonify({"erro": "Erro interno no servidor"}), 500

