import io
import base64
import logging
import traceback
from datetime import datetime

from flask import Blueprint, request, jsonify, make_response, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func

from extensions import db
from models.diario_obra import DiarioObra
from models.diario_imagem import DiarioImagem
from models.obra import Obra
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet
from services import get_current_user, user_has_access_to_obra

logger = logging.getLogger(__name__)

diario_bp = Blueprint('diario', __name__)


@diario_bp.route('/obras/<int:obra_id>/diario', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_diario_obra(obra_id):
    """Lista todas as entradas do diário de uma obra"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        entradas = DiarioObra.query.filter_by(obra_id=obra_id).order_by(DiarioObra.data.desc()).all()

        return jsonify({
            'entradas': [entrada.to_dict() for entrada in entradas]
        }), 200

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET /obras/{obra_id}/diario: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/obras/<int:obra_id>/diario', methods=['POST', 'OPTIONS'])
@jwt_required()
def criar_entrada_diario(obra_id):
    """Cria uma nova entrada no diário de obras"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        data = request.get_json()

        entrada = DiarioObra(
            obra_id=obra_id,
            data=datetime.strptime(data.get('data'), '%Y-%m-%d').date() if data.get('data') else datetime.utcnow().date(),
            titulo=data.get('titulo'),
            descricao=data.get('descricao'),
            clima=data.get('clima'),
            temperatura=data.get('temperatura'),
            equipe_presente=data.get('equipe_presente'),
            atividades_realizadas=data.get('atividades_realizadas'),
            materiais_utilizados=data.get('materiais_utilizados'),
            equipamentos_utilizados=data.get('equipamentos_utilizados'),
            observacoes=data.get('observacoes'),
            criado_por=int(get_jwt_identity())
        )

        db.session.add(entrada)
        db.session.flush()

        if 'imagens' in data and isinstance(data['imagens'], list):
            for idx, img_data in enumerate(data['imagens']):
                imagem = DiarioImagem(
                    diario_id=entrada.id,
                    arquivo_nome=img_data.get('nome', f'imagem_{idx+1}.jpg'),
                    arquivo_base64=img_data.get('base64', ''),
                    legenda=img_data.get('legenda', ''),
                    ordem=idx
                )
                db.session.add(imagem)

        db.session.commit()

        logger.info(f"--- [LOG] Entrada de diário criada: ID {entrada.id} na obra {obra_id} ---")
        return jsonify({
            'mensagem': 'Entrada criada com sucesso',
            'entrada': entrada.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] POST /obras/{obra_id}/diario: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/diario/<int:entrada_id>', methods=['GET', 'OPTIONS'])
@jwt_required()
def obter_entrada_diario(entrada_id):
    """Obtém uma entrada específica do diário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404

        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        return jsonify(entrada.to_dict()), 200

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET /diario/{entrada_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/diario/<int:entrada_id>', methods=['PUT', 'OPTIONS'])
@jwt_required()
def atualizar_entrada_diario(entrada_id):
    """Atualiza uma entrada do diário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404

        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        data = request.get_json()

        if 'data' in data:
            entrada.data = datetime.strptime(data['data'], '%Y-%m-%d').date()
        if 'titulo' in data:
            entrada.titulo = data['titulo']
        if 'descricao' in data:
            entrada.descricao = data['descricao']
        if 'clima' in data:
            entrada.clima = data['clima']
        if 'temperatura' in data:
            entrada.temperatura = data['temperatura']
        if 'equipe_presente' in data:
            entrada.equipe_presente = data['equipe_presente']
        if 'atividades_realizadas' in data:
            entrada.atividades_realizadas = data['atividades_realizadas']
        if 'materiais_utilizados' in data:
            entrada.materiais_utilizados = data['materiais_utilizados']
        if 'equipamentos_utilizados' in data:
            entrada.equipamentos_utilizados = data['equipamentos_utilizados']
        if 'observacoes' in data:
            entrada.observacoes = data['observacoes']

        db.session.commit()

        logger.info(f"--- [LOG] Entrada {entrada_id} atualizada ---")
        return jsonify({
            'mensagem': 'Entrada atualizada com sucesso',
            'entrada': entrada.to_dict()
        }), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] PUT /diario/{entrada_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/diario/<int:entrada_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_entrada_diario(entrada_id):
    """Deleta uma entrada do diário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404

        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        db.session.delete(entrada)
        db.session.commit()

        logger.info(f"--- [LOG] Entrada {entrada_id} deletada ---")
        return jsonify({'mensagem': 'Entrada deletada com sucesso'}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] DELETE /diario/{entrada_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/diario/<int:entrada_id>/imagens', methods=['POST', 'OPTIONS'])
@jwt_required()
def adicionar_imagem_diario(entrada_id):
    """Adiciona uma imagem a uma entrada existente"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        entrada = db.session.get(DiarioObra, entrada_id)
        if not entrada:
            return jsonify({"erro": "Entrada não encontrada"}), 404

        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        data = request.get_json()

        max_ordem = db.session.query(func.max(DiarioImagem.ordem)).filter_by(diario_id=entrada_id).scalar() or -1

        imagem = DiarioImagem(
            diario_id=entrada_id,
            arquivo_nome=data.get('nome', 'imagem.jpg'),
            arquivo_base64=data.get('base64', ''),
            legenda=data.get('legenda', ''),
            ordem=max_ordem + 1
        )

        db.session.add(imagem)
        db.session.commit()

        logger.info(f"--- [LOG] Imagem adicionada à entrada {entrada_id} ---")
        return jsonify({
            'mensagem': 'Imagem adicionada com sucesso',
            'imagem': imagem.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] POST /diario/{entrada_id}/imagens: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/diario/imagens/<int:imagem_id>', methods=['GET', 'OPTIONS'])
@jwt_required()
def get_imagem_diario(imagem_id):
    """Busca uma imagem do diario com base64"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        imagem = db.session.get(DiarioImagem, imagem_id)
        if not imagem:
            return jsonify({"erro": "Imagem nao encontrada"}), 404

        entrada = db.session.get(DiarioObra, imagem.diario_id)
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        return jsonify(imagem.to_dict_full()), 200

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET /diario/imagens/{imagem_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/diario/imagens/<int:imagem_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_imagem_diario(imagem_id):
    """Deleta uma imagem do diário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        imagem = db.session.get(DiarioImagem, imagem_id)
        if not imagem:
            return jsonify({"erro": "Imagem não encontrada"}), 404

        entrada = db.session.get(DiarioObra, imagem.diario_id)
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, entrada.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        db.session.delete(imagem)
        db.session.commit()

        logger.info(f"--- [LOG] Imagem {imagem_id} deletada ---")
        return jsonify({'mensagem': 'Imagem deletada com sucesso'}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] DELETE /diario/imagens/{imagem_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@diario_bp.route('/obras/<int:obra_id>/diario/relatorio', methods=['GET', 'OPTIONS'])
@jwt_required()
def gerar_relatorio_diario(obra_id):
    """Gera relatório PDF do diário de obras"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404

        data_inicio = request.args.get('data_inicio')
        data_fim = request.args.get('data_fim')

        query = DiarioObra.query.filter_by(obra_id=obra_id)

        if data_inicio:
            query = query.filter(DiarioObra.data >= datetime.strptime(data_inicio, '%Y-%m-%d').date())
        if data_fim:
            query = query.filter(DiarioObra.data <= datetime.strptime(data_fim, '%Y-%m-%d').date())

        entradas = query.order_by(DiarioObra.data.asc()).all()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)

        story = []
        styles = getSampleStyleSheet()

        titulo = Paragraph(f"<b>Diário de Obras - {obra.nome}</b>", styles['Title'])
        story.append(titulo)
        story.append(Spacer(1, 0.5*cm))

        info_data = [
            ['Relatório gerado em:', datetime.now().strftime('%d/%m/%Y %H:%M')],
            ['Obra:', obra.nome],
            ['Cliente:', obra.cliente or 'N/A'],
        ]

        if data_inicio or data_fim:
            periodo = f"{data_inicio or 'Início'} até {data_fim or 'Hoje'}"
            info_data.append(['Período:', periodo])

        info_table = Table(info_data, colWidths=[5*cm, 12*cm])
        info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e2e8f0')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))

        story.append(info_table)
        story.append(Spacer(1, 1*cm))

        for entrada in entradas:
            story.append(Paragraph(f"<b>{entrada.data.strftime('%d/%m/%Y')}</b> - {entrada.titulo}", styles['Heading2']))

            if entrada.clima or entrada.temperatura:
                clima_info = []
                if entrada.clima:
                    clima_info.append(f"Clima: {entrada.clima}")
                if entrada.temperatura:
                    clima_info.append(f"Temperatura: {entrada.temperatura}")
                story.append(Paragraph(" | ".join(clima_info), styles['Normal']))
                story.append(Spacer(1, 0.2*cm))

            if entrada.descricao:
                story.append(Paragraph("<b>Descrição:</b>", styles['Normal']))
                story.append(Paragraph(entrada.descricao, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))

            if entrada.atividades_realizadas:
                story.append(Paragraph("<b>Atividades Realizadas:</b>", styles['Normal']))
                story.append(Paragraph(entrada.atividades_realizadas, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))

            if entrada.equipe_presente:
                story.append(Paragraph("<b>Equipe Presente:</b>", styles['Normal']))
                story.append(Paragraph(entrada.equipe_presente, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))

            if entrada.materiais_utilizados:
                story.append(Paragraph("<b>Materiais Utilizados:</b>", styles['Normal']))
                story.append(Paragraph(entrada.materiais_utilizados, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))

            if entrada.observacoes:
                story.append(Paragraph("<b>Observações:</b>", styles['Normal']))
                story.append(Paragraph(entrada.observacoes, styles['Normal']))
                story.append(Spacer(1, 0.3*cm))

            if entrada.imagens:
                story.append(Paragraph(f"<b>Imagens:</b> {len(entrada.imagens)} foto(s)", styles['Normal']))
                story.append(Spacer(1, 0.3*cm))

                for img_obj in entrada.imagens:
                    try:
                        img_data = base64.b64decode(img_obj.arquivo_base64)
                        img_buffer = io.BytesIO(img_data)

                        img = Image(img_buffer)

                        max_width = 15 * cm
                        max_height = 12 * cm

                        aspect = img.imageHeight / img.imageWidth
                        if img.imageWidth > max_width:
                            img.drawWidth = max_width
                            img.drawHeight = max_width * aspect
                        else:
                            img.drawWidth = img.imageWidth
                            img.drawHeight = img.imageHeight

                        if img.drawHeight > max_height:
                            img.drawHeight = max_height
                            img.drawWidth = max_height / aspect

                        story.append(img)

                        if img_obj.arquivo_nome:
                            story.append(Paragraph(f"<i>{img_obj.arquivo_nome}</i>", styles['Normal']))

                        story.append(Spacer(1, 0.3*cm))

                    except Exception as img_error:
                        logger.exception(f"--- [ERRO] Erro ao processar imagem {img_obj.id}: {str(img_error)} ---")
                        story.append(Paragraph(f"<i>[Erro ao carregar imagem: {img_obj.arquivo_nome}]</i>", styles['Normal']))
                        story.append(Spacer(1, 0.3*cm))

            story.append(Spacer(1, 0.5*cm))
            story.append(Paragraph("_" * 100, styles['Normal']))
            story.append(Spacer(1, 0.5*cm))

        doc.build(story)
        buffer.seek(0)

        logger.info(f"--- [LOG] Relatório do diário gerado para obra {obra_id} ---")

        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'diario_obra_{obra.nome}_{datetime.now().strftime("%Y%m%d")}.pdf'
        )

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET /obras/{obra_id}/diario/relatorio: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500
