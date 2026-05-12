import io
import os
import base64
import logging
import traceback
from datetime import datetime, date

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required
from sqlalchemy import func

from extensions import db
from models.obra import Obra
from models.caixa_obra import CaixaObra
from models.movimentacao_caixa import MovimentacaoCaixa
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet
from services import get_current_user, user_has_access_to_obra

logger = logging.getLogger(__name__)

caixa_bp = Blueprint('caixa', __name__, url_prefix='/obras/<int:obra_id>/caixa')


@caixa_bp.route('', methods=['GET', 'POST'])
@jwt_required()
def gerenciar_caixa_obra(obra_id):
    """
    GET: Retorna informações do caixa da obra (dashboard)
    POST: Cria ou inicializa o caixa da obra
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403

        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404

        if request.method == 'GET':
            caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()

            if not caixa:
                hoje = date.today()
                caixa = CaixaObra(
                    obra_id=obra_id,
                    saldo_inicial=0,
                    saldo_atual=0,
                    mes_atual=hoje.month,
                    ano_atual=hoje.year,
                    status='Ativo'
                )
                db.session.add(caixa)
                db.session.commit()

            movimentacoes_mes = MovimentacaoCaixa.query.filter(
                MovimentacaoCaixa.caixa_id == caixa.id,
                func.extract('month', MovimentacaoCaixa.data) == caixa.mes_atual,
                func.extract('year', MovimentacaoCaixa.data) == caixa.ano_atual
            ).all()

            total_entradas_mes = sum(m.valor for m in movimentacoes_mes if m.tipo == 'Entrada')
            total_saidas_mes = sum(m.valor for m in movimentacoes_mes if m.tipo == 'Saída')

            resultado = caixa.to_dict()
            resultado['total_entradas_mes'] = total_entradas_mes
            resultado['total_saidas_mes'] = total_saidas_mes
            resultado['obra_nome'] = obra.nome

            return jsonify(resultado), 200

        elif request.method == 'POST':
            data = request.get_json()
            caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()

            if caixa:
                return jsonify({"erro": "Caixa já existe para esta obra"}), 400

            hoje = date.today()
            caixa = CaixaObra(
                obra_id=obra_id,
                saldo_inicial=float(data.get('saldo_inicial', 0)),
                saldo_atual=float(data.get('saldo_inicial', 0)),
                mes_atual=hoje.month,
                ano_atual=hoje.year,
                status='Ativo'
            )

            db.session.add(caixa)
            db.session.commit()

            logger.info(f"[LOG] Caixa criado para obra {obra_id}")
            return jsonify(caixa.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] gerenciar_caixa_obra: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@caixa_bp.route('/movimentacoes', methods=['GET', 'POST'])
@jwt_required()
def gerenciar_movimentacoes_caixa(obra_id):
    """
    GET: Lista movimentações do caixa (com filtros opcionais)
    POST: Adiciona nova movimentação
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403

        caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
        if not caixa:
            return jsonify({"erro": "Caixa não encontrado para esta obra"}), 404

        if request.method == 'GET':
            mes = request.args.get('mes', type=int)
            ano = request.args.get('ano', type=int)
            tipo = request.args.get('tipo')

            query = MovimentacaoCaixa.query.filter_by(caixa_id=caixa.id)

            if mes:
                query = query.filter(func.extract('month', MovimentacaoCaixa.data) == mes)
            if ano:
                query = query.filter(func.extract('year', MovimentacaoCaixa.data) == ano)
            if tipo:
                query = query.filter_by(tipo=tipo)

            movimentacoes = query.order_by(MovimentacaoCaixa.data.desc()).all()

            return jsonify([m.to_dict() for m in movimentacoes]), 200

        elif request.method == 'POST':
            data = request.get_json()

            if 'tipo' not in data or data['tipo'] not in ['Entrada', 'Saída']:
                return jsonify({"erro": "Tipo deve ser 'Entrada' ou 'Saída'"}), 400

            if 'valor' not in data or float(data['valor']) <= 0:
                return jsonify({"erro": "Valor deve ser maior que zero"}), 400

            if 'descricao' not in data or not data['descricao'].strip():
                return jsonify({"erro": "Descrição é obrigatória"}), 400

            data_movimentacao = datetime.now()
            if 'data' in data and data['data']:
                try:
                    data_movimentacao = datetime.fromisoformat(data['data'].replace('Z', '+00:00'))
                except Exception:
                    logger.warning("Excecao suprimida em ", exc_info=True)

            movimentacao = MovimentacaoCaixa(
                caixa_id=caixa.id,
                data=data_movimentacao,
                tipo=data['tipo'],
                valor=float(data['valor']),
                descricao=data['descricao'].strip(),
                comprovante_url=data.get('comprovante_url'),
                observacoes=data.get('observacoes'),
                criado_por=current_user.id
            )

            db.session.add(movimentacao)

            if data['tipo'] == 'Entrada':
                caixa.saldo_atual += float(data['valor'])
            else:
                caixa.saldo_atual -= float(data['valor'])

            db.session.commit()

            logger.info(f"[LOG] Movimentação {data['tipo']} de R$ {data['valor']} registrada no caixa {caixa.id}")
            return jsonify(movimentacao.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] gerenciar_movimentacoes_caixa: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@caixa_bp.route('/movimentacoes/<int:mov_id>', methods=['PUT', 'DELETE'])
@jwt_required()
def editar_deletar_movimentacao(obra_id, mov_id):
    """
    PUT: Edita uma movimentação existente
    DELETE: Deleta uma movimentação
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403

        caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
        if not caixa:
            return jsonify({"erro": "Caixa não encontrado"}), 404

        movimentacao = db.session.get(MovimentacaoCaixa, mov_id)
        if not movimentacao or movimentacao.caixa_id != caixa.id:
            return jsonify({"erro": "Movimentação não encontrada"}), 404

        if request.method == 'PUT':
            data = request.get_json()

            if movimentacao.tipo == 'Entrada':
                caixa.saldo_atual -= movimentacao.valor
            else:
                caixa.saldo_atual += movimentacao.valor

            if 'tipo' in data and data['tipo'] in ['Entrada', 'Saída']:
                movimentacao.tipo = data['tipo']

            if 'valor' in data and float(data['valor']) > 0:
                movimentacao.valor = float(data['valor'])

            if 'descricao' in data:
                movimentacao.descricao = data['descricao']

            if 'data' in data:
                try:
                    movimentacao.data = datetime.fromisoformat(data['data'].replace('Z', '+00:00'))
                except Exception:
                    logger.warning("Excecao suprimida em ", exc_info=True)

            if 'comprovante_url' in data:
                movimentacao.comprovante_url = data['comprovante_url']

            if 'observacoes' in data:
                movimentacao.observacoes = data['observacoes']

            if movimentacao.tipo == 'Entrada':
                caixa.saldo_atual += movimentacao.valor
            else:
                caixa.saldo_atual -= movimentacao.valor

            db.session.commit()

            logger.info(f"[LOG] Movimentação {mov_id} atualizada")
            return jsonify(movimentacao.to_dict()), 200

        elif request.method == 'DELETE':
            if movimentacao.tipo == 'Entrada':
                caixa.saldo_atual -= movimentacao.valor
            else:
                caixa.saldo_atual += movimentacao.valor

            db.session.delete(movimentacao)
            db.session.commit()

            logger.info(f"[LOG] Movimentação {mov_id} deletada")
            return jsonify({"mensagem": "Movimentação deletada com sucesso"}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] editar_deletar_movimentacao: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@caixa_bp.route('/upload-comprovante', methods=['POST'])
@jwt_required()
def upload_comprovante_caixa(obra_id):
    """Upload de imagem de comprovante (base64) - salva direto no banco"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403

        data = request.get_json()

        if 'imagem' not in data:
            return jsonify({"erro": "Imagem não fornecida"}), 400

        imagem_base64 = data['imagem']

        if not imagem_base64.startswith('data:image'):
            imagem_base64 = f"data:image/jpeg;base64,{imagem_base64}"

        logger.info(f"[LOG] Comprovante base64 recebido para obra {obra_id} ({len(imagem_base64)} chars)")
        return jsonify({"comprovante_url": imagem_base64}), 200

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] upload_comprovante_caixa: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500


@caixa_bp.route('/relatorio-pdf', methods=['POST'])
@jwt_required()
def gerar_relatorio_caixa_pdf(obra_id):
    """Gera relatorio PDF de prestacao de contas do caixa"""
    try:
        logger.info(f"[LOG] Iniciando geracao de PDF do caixa para obra {obra_id}")

        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403

        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra nao encontrada"}), 404

        caixa = CaixaObra.query.filter_by(obra_id=obra_id).first()
        if not caixa:
            return jsonify({"erro": "Caixa nao encontrado"}), 404

        req_data = request.get_json() or {}
        mes = int(req_data.get('mes', date.today().month))
        ano = int(req_data.get('ano', date.today().year))

        logger.info(f"[LOG] Buscando movimentacoes para mes={mes}, ano={ano}")

        todas_movs = MovimentacaoCaixa.query.filter_by(caixa_id=caixa.id).order_by(MovimentacaoCaixa.data).all()

        movimentacoes = []
        for m in todas_movs:
            if m.data:
                try:
                    if m.data.month == mes and m.data.year == ano:
                        movimentacoes.append(m)
                except Exception as e:
                    logger.exception(f"[WARN] Erro ao processar data da movimentacao {m.id}: {e}")

        logger.info(f"[LOG] Encontradas {len(movimentacoes)} movimentacoes")

        saldo_inicial = float(caixa.saldo_inicial or 0)
        total_entradas = 0
        total_saidas = 0
        qtd_comprovantes = 0

        for m in movimentacoes:
            tipo = (m.tipo or '').lower()
            valor = float(m.valor or 0)
            if tipo == 'entrada':
                total_entradas += valor
            elif tipo in ['saida', 'saída']:
                total_saidas += valor
            if m.comprovante_url:
                qtd_comprovantes += 1

        saldo_final = saldo_inicial + total_entradas - total_saidas

        logger.info(f"[LOG] Totais: entradas={total_entradas}, saidas={total_saidas}")

        def _formatar_real(valor):
            try:
                return f"R$ {float(valor):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
            except Exception:
                return "R$ 0,00"

        def limpar_texto(texto):
            if not texto:
                return ""
            subs = {
                'á': 'a', 'à': 'a', 'ã': 'a', 'â': 'a',
                'é': 'e', 'ê': 'e', 'è': 'e',
                'í': 'i', 'ì': 'i',
                'ó': 'o', 'ô': 'o', 'õ': 'o', 'ò': 'o',
                'ú': 'u', 'ù': 'u',
                'ç': 'c', 'ñ': 'n',
                'Á': 'A', 'À': 'A', 'Ã': 'A', 'Â': 'A',
                'É': 'E', 'Ê': 'E', 'È': 'E',
                'Í': 'I', 'Ì': 'I',
                'Ó': 'O', 'Ô': 'O', 'Õ': 'O', 'Ò': 'O',
                'Ú': 'U', 'Ù': 'U',
                'Ç': 'C', 'Ñ': 'N'
            }
            resultado = str(texto)
            for orig, subst in subs.items():
                resultado = resultado.replace(orig, subst)
            return ''.join(c if ord(c) < 128 else '' for c in resultado)

        nomes_meses = ['', 'Janeiro', 'Fevereiro', 'Marco', 'Abril', 'Maio', 'Junho',
                       'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        nome_mes = nomes_meses[mes] if 1 <= mes <= 12 else 'Mes'

        logger.info("[LOG] Criando documento PDF...")

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
        elements = []
        styles = getSampleStyleSheet()

        obra_nome_limpo = limpar_texto(obra.nome)
        user_nome_limpo = limpar_texto(current_user.username if current_user else 'Sistema')

        titulo = Paragraph("<b>PRESTACAO DE CONTAS - CAIXA DE OBRA</b>", styles['Title'])
        elements.append(titulo)
        elements.append(Spacer(1, 0.5*cm))

        info = f"<b>Obra:</b> {obra_nome_limpo}<br/>"
        info += f"<b>Periodo:</b> {nome_mes}/{ano}<br/>"
        info += f"<b>Responsavel:</b> {user_nome_limpo}<br/>"
        info += f"<b>Data do Relatorio:</b> {date.today().strftime('%d/%m/%Y')}"
        elements.append(Paragraph(info, styles['Normal']))
        elements.append(Spacer(1, 1*cm))

        data_saldo = [['SALDO INICIAL', _formatar_real(saldo_inicial)]]
        table_saldo = Table(data_saldo, colWidths=[12*cm, 5*cm])
        table_saldo.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4CAF50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ]))
        elements.append(table_saldo)
        elements.append(Spacer(1, 0.5*cm))

        entradas = [m for m in movimentacoes if (m.tipo or '').lower() == 'entrada']
        if entradas:
            elements.append(Paragraph("<b>ENTRADAS NO PERIODO</b>", styles['Heading2']))
            elements.append(Spacer(1, 0.3*cm))

            data_entradas = [['Data', 'Descricao', 'Valor']]
            for m in entradas:
                try:
                    data_str = m.data.strftime('%d/%m') if m.data else '-'
                except Exception:
                    data_str = '-'
                data_entradas.append([
                    data_str,
                    limpar_texto(m.descricao or '')[:60],
                    _formatar_real(m.valor)
                ])
            data_entradas.append(['', 'TOTAL ENTRADAS', _formatar_real(total_entradas)])

            table_entradas = Table(data_entradas, colWidths=[2.5*cm, 11*cm, 3.5*cm])
            table_entradas.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2196F3')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#BBDEFB')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ]))
            elements.append(table_entradas)
            elements.append(Spacer(1, 0.7*cm))

        saidas = [m for m in movimentacoes if (m.tipo or '').lower() in ['saida', 'saída']]
        if saidas:
            elements.append(Paragraph("<b>SAIDAS NO PERIODO</b>", styles['Heading2']))
            elements.append(Spacer(1, 0.3*cm))

            data_saidas = [['Data', 'Descricao', 'Valor', 'Comp.']]
            for m in saidas:
                try:
                    data_str = m.data.strftime('%d/%m') if m.data else '-'
                except Exception:
                    data_str = '-'
                data_saidas.append([
                    data_str,
                    limpar_texto(m.descricao or '')[:60],
                    _formatar_real(m.valor),
                    'Sim' if m.comprovante_url else '-'
                ])
            data_saidas.append(['', 'TOTAL SAIDAS', _formatar_real(total_saidas), ''])

            table_saidas = Table(data_saidas, colWidths=[2.5*cm, 10*cm, 3.5*cm, 1*cm])
            table_saidas.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f44336')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('ALIGN', (3, 0), (3, -1), 'CENTER'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#FFCDD2')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ]))
            elements.append(table_saidas)
            elements.append(Spacer(1, 0.7*cm))

        data_final = [['SALDO FINAL', _formatar_real(saldo_final)]]
        table_final = Table(data_final, colWidths=[12*cm, 5*cm])
        table_final.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#FF9800')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ]))
        elements.append(table_final)
        elements.append(Spacer(1, 1*cm))

        rodape = f"Total de comprovantes anexos: {qtd_comprovantes}<br/>"
        rodape += f"Gerado em: {datetime.now().strftime('%d/%m/%Y as %H:%M')}<br/>"
        rodape += f"Por: {user_nome_limpo}"
        elements.append(Paragraph(rodape, styles['Normal']))

        if qtd_comprovantes > 0:
            elements.append(Spacer(1, 1*cm))
            elements.append(Paragraph("<b>COMPROVANTES ANEXOS</b>", styles['Heading2']))
            elements.append(Spacer(1, 0.5*cm))

            comprovante_num = 0
            for m in movimentacoes:
                if m.comprovante_url:
                    comprovante_num += 1
                    try:
                        try:
                            data_str = m.data.strftime('%d/%m/%Y') if m.data else '-'
                        except Exception:
                            data_str = '-'

                        desc_limpa = limpar_texto(m.descricao or 'Sem descricao')[:50]
                        titulo_comp = f"<b>Comprovante {comprovante_num}:</b> {desc_limpa} - {data_str} - {_formatar_real(m.valor)}"
                        elements.append(Paragraph(titulo_comp, styles['Normal']))
                        elements.append(Spacer(1, 0.3*cm))

                        img_data = None

                        if m.comprovante_url.startswith('data:image'):
                            try:
                                base64_data = m.comprovante_url.split(',')[1]
                                img_data = io.BytesIO(base64.b64decode(base64_data))
                            except Exception as e:
                                logger.exception(f"[WARN] Erro ao decodificar base64 do comprovante {comprovante_num}: {e}")

                        elif m.comprovante_url.startswith('/uploads/') or m.comprovante_url.startswith('uploads/'):
                            try:
                                file_path = m.comprovante_url.lstrip('/')
                                if os.path.exists(file_path):
                                    with open(file_path, 'rb') as f:
                                        img_data = io.BytesIO(f.read())
                            except Exception as e:
                                logger.exception(f"[WARN] Erro ao carregar arquivo do comprovante {comprovante_num}: {e}")

                        elif m.comprovante_url.startswith('http'):
                            try:
                                import urllib.request
                                with urllib.request.urlopen(m.comprovante_url, timeout=10) as response:
                                    img_data = io.BytesIO(response.read())
                            except Exception as e:
                                logger.exception(f"[WARN] Erro ao baixar comprovante {comprovante_num}: {e}")

                        if img_data:
                            try:
                                img = Image(img_data)
                                max_width = 15 * cm
                                max_height = 10 * cm
                                ratio = min(max_width / img.drawWidth, max_height / img.drawHeight)
                                if ratio < 1:
                                    img.drawWidth *= ratio
                                    img.drawHeight *= ratio
                                elements.append(img)
                                elements.append(Spacer(1, 0.5*cm))
                            except Exception as e:
                                logger.exception(f"[WARN] Erro ao adicionar imagem do comprovante {comprovante_num}: {e}")
                                elements.append(Paragraph("<i>(Erro ao carregar imagem)</i>", styles['Normal']))
                        else:
                            elements.append(Paragraph(f"<i>(Comprovante disponivel em: {m.comprovante_url[:60]}...)</i>", styles['Normal']))

                        elements.append(Spacer(1, 0.5*cm))

                    except Exception as e:
                        logger.exception(f"[WARN] Erro ao processar comprovante {comprovante_num}: {e}")
                        elements.append(Paragraph("<i>(Erro ao processar comprovante)</i>", styles['Normal']))

        logger.info("[LOG] Construindo PDF...")
        doc.build(elements)
        buffer.seek(0)

        logger.info("[LOG] PDF do caixa gerado com sucesso")

        nome_arquivo = f"Caixa_{obra_nome_limpo.replace(' ', '_')}_{nome_mes}_{ano}.pdf"

        return send_file(
            buffer,
            as_attachment=True,
            download_name=nome_arquivo,
            mimetype='application/pdf'
        )

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] gerar_relatorio_caixa_pdf: {str(e)}\n{error_details}")
        return jsonify({
            "erro": "Erro ao gerar relatorio PDF",
            "mensagem": str(e),
            "detalhes": error_details
        }), 500
