import io
import logging
import traceback
from datetime import datetime, date

from flask import Blueprint, request, jsonify, make_response, send_file
from flask_jwt_extended import jwt_required, get_jwt

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from extensions import db
from models.obra import Obra
from models.servico import Servico
from models.pagamento_servico import PagamentoServico
from models.servico_usuario import ServicoUsuario
from models.servico_base import ServicoBase
from services import (
    get_current_user,
    user_has_access_to_obra,
    check_permission,
    notificar_operadores_obra,
    notificar_masters,
    notificar_administradores,
)

logger = logging.getLogger(__name__)

servicos_bp = Blueprint('servicos', __name__)


# --- ROTAS DE SERVIÇO (Atualizadas) ---

@servicos_bp.route('/obras/<int:obra_id>/servicos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master', 'comum']) 
def add_servico(obra_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/servicos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.json
        
        # Tratar valores vazios ou nulos
        def safe_float(value, default=0.0):
            if value is None or value == '':
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        novo_servico = Servico(
            obra_id=obra_id,
            nome=dados['nome'],
            responsavel=dados.get('responsavel', ''),
            valor_global_mao_de_obra=safe_float(dados.get('valor_global_mao_de_obra')),
            valor_global_material=safe_float(dados.get('valor_global_material')),
            pix=dados.get('pix')
        )
        db.session.add(novo_servico)
        db.session.commit()
        
        # --- NOTIFICAÇÕES ---
        obra = Obra.query.get(obra_id)
        obra_nome = obra.nome if obra else f"Obra {obra_id}"
        
        # Notificar todos os operadores (comum) com acesso à obra
        notificar_operadores_obra(
            obra_id=obra_id,
            tipo='servico_criado',
            titulo=f'Novo serviço criado',
            mensagem=f'O serviço "{novo_servico.nome}" foi criado na obra {obra_nome}',
            item_id=novo_servico.id,
            item_type='servico',
            usuario_origem_id=user.id
        )
        
        # Notificar todos os masters
        notificar_masters(
            tipo='servico_criado',
            titulo=f'Novo serviço criado',
            mensagem=f'{user.username} criou o serviço "{novo_servico.nome}" na obra {obra_nome}',
            obra_id=obra_id,
            item_id=novo_servico.id,
            item_type='servico',
            usuario_origem_id=user.id
        )
        
        # Notificar administradores
        notificar_administradores(
            tipo='servico_criado',
            titulo=f'Novo serviço criado',
            mensagem=f'{user.username} criou o serviço "{novo_servico.nome}" na obra {obra_nome}',
            obra_id=obra_id,
            item_id=novo_servico.id,
            item_type='servico',
            usuario_origem_id=user.id
        )
        
        return jsonify(novo_servico.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/servicos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@servicos_bp.route('/obras/<int:obra_id>/servicos-nomes', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_servicos_nomes(obra_id):
    """
    Retorna lista simplificada de serviços (id, nome) para uso em dropdowns
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        servicos = Servico.query.filter_by(obra_id=obra_id).order_by(Servico.nome).all()
        
        servicos_simples = [
            {
                'id': s.id,
                'nome': s.nome
            }
            for s in servicos
        ]
        
        return jsonify({'servicos': servicos_simples}), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/servicos-nomes (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@servicos_bp.route('/servicos/<int:servico_id>', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def editar_servico(servico_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /servicos/{servico_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        servico = Servico.query.get_or_404(servico_id)
        
        if not user_has_access_to_obra(user, servico.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        dados = request.json
        
        # Tratar valores vazios ou nulos
        def safe_float(value, default=0.0):
            if value is None or value == '':
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        servico.nome = dados.get('nome', servico.nome)
        servico.responsavel = dados.get('responsavel', servico.responsavel)
        servico.valor_global_mao_de_obra = safe_float(dados.get('valor_global_mao_de_obra'), servico.valor_global_mao_de_obra or 0.0)
        servico.valor_global_material = safe_float(dados.get('valor_global_material'), servico.valor_global_material or 0.0)
        servico.pix = dados.get('pix', servico.pix)
        db.session.commit()
        return jsonify(servico.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /servicos/{servico_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@servicos_bp.route('/servicos/<int:servico_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def deletar_servico(servico_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /servicos/{servico_id} (DELETE) acessada ---")
    try:
        servico = Servico.query.get_or_404(servico_id)
        db.session.delete(servico)
        db.session.commit()
        return jsonify({"sucesso": "Serviço deletado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /servicos/{servico_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@servicos_bp.route('/servicos/<int:servico_id>/concluir', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def toggle_servico_concluido(servico_id):
    """
    Marca/desmarca um serviço como concluído
    Um serviço pode estar concluído mesmo sem ter sido totalmente pago
    """
    if request.method == 'OPTIONS':
        return '', 200
        
    logger.info(f"--- [LOG] Rota /servicos/{servico_id}/concluir (PATCH) acessada ---")
    try:
        user = get_current_user()
        servico = Servico.query.get_or_404(servico_id)
        
        if not user_has_access_to_obra(user, servico.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json or {}
        
        # Toggle ou valor específico
        if 'concluido' in dados:
            servico.concluido = dados['concluido']
        else:
            # Toggle: se não especificado, inverte o valor atual
            servico.concluido = not (servico.concluido or False)
        
        # Definir data de conclusão
        if servico.concluido:
            servico.data_conclusao = dados.get('data_conclusao', date.today()) if isinstance(dados.get('data_conclusao'), date) else date.fromisoformat(dados['data_conclusao']) if dados.get('data_conclusao') else date.today()
        else:
            servico.data_conclusao = None
        
        db.session.commit()
        
        logger.info(f"--- [LOG] Serviço {servico_id} marcado como {'CONCLUÍDO' if servico.concluido else 'NÃO CONCLUÍDO'} ---")
        
        return jsonify({
            "sucesso": f"Serviço {'marcado como concluído' if servico.concluido else 'desmarcado como concluído'}",
            "servico": servico.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /servicos/{servico_id}/concluir (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


# ===== ROTA PARA DELETAR PAGAMENTO DE SERVIÇO =====
@servicos_bp.route('/servicos/<int:servico_id>/pagamentos/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_pagamento_servico(servico_id, pagamento_id):
    """
    Deleta um pagamento de serviço com regras específicas:
    - Pagamentos PAGOS só podem ser deletados por usuários MASTER
    - Pagamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
    """
    logger.info(f"--- [LOG] Rota /servicos/{servico_id}/pagamentos/{pagamento_id} (DELETE) acessada ---")
    
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        pagamento = PagamentoServico.query.filter_by(
            id=pagamento_id, 
            servico_id=servico_id
        ).first()
        
        if not pagamento:
            # Tentar buscar apenas pelo ID
            pagamento = db.session.get(PagamentoServico, pagamento_id)
        
        if not pagamento:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        # Obter o papel do usuário
        claims = get_jwt()
        user_role = claims.get('role')
        
        # Verificar se o pagamento está PAGO (completamente executado)
        is_pago = (pagamento.valor_pago or 0) >= (pagamento.valor_total or 0)
        
        # REGRA: Se está PAGO, ADMINISTRADOR ou MASTER podem deletar
        if is_pago and user_role not in ['administrador', 'master']:
            logger.error(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO de serviço por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Apenas administradores e masters podem excluir pagamentos já executados (PAGOS)."
            }), 403
        
        # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
        if not is_pago and user_role not in ['administrador', 'master']:
            logger.error(f"--- [LOG] ❌ Tentativa de deletar pagamento de serviço por usuário {user_role} (sem permissão) ---")
            return jsonify({
                "erro": "Acesso negado: Permissão insuficiente para excluir este pagamento."
            }), 403
        
        db.session.delete(pagamento)
        db.session.commit()
        
        logger.info(f"--- [LOG] ✅ Pagamento de serviço {pagamento_id} deletado com sucesso pelo usuário {user_role} ---")
        return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /servicos/.../pagamentos (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500
# ===============================================================================


@servicos_bp.route('/obras/<int:obra_id>/servicos', methods=['GET'])
@jwt_required()
def get_servicos_obra(obra_id):
    """Busca todos os serviços de uma obra"""
    try:
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'error': 'Obra não encontrada'}), 404
        
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        return jsonify([{
            'id': s.id,
            'nome': s.nome,
            'responsavel': s.responsavel,
            'valor_global_mao_de_obra': s.valor_global_mao_de_obra,
            'valor_global_material': s.valor_global_material
        } for s in servicos]), 200
    except Exception as e:
        logger.exception(f"[ERRO] get_servicos_obra: {str(e)}")


# ROTA PARA EXPORTAR SERVICOS EM PDF
@servicos_bp.route('/obras/<int:obra_id>/servicos/exportar-pdf', methods=['GET'])
@jwt_required()
def exportar_servicos_pdf(obra_id):
    """Exporta a planilha de serviços para PDF"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        # Buscar todos os serviços da obra
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        
        # Criar PDF em memória
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.5*cm, rightMargin=1.5*cm, topMargin=2*cm, bottomMargin=2*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Função para formatar valores em reais
        def formatar_real(valor):
            return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        
        # Título
        titulo = Paragraph(f"<b>Planilha de Serviços - {obra.nome}</b>", styles['Title'])
        elements.append(titulo)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo
        subtitulo = Paragraph(f"Gerado em: {date.today().strftime('%d/%m/%Y')}", styles['Normal'])
        elements.append(subtitulo)
        elements.append(Spacer(1, 1*cm))
        
        # Preparar dados da tabela
        data = [
            ['Serviço', 'Responsável', 'MO Orçado', 'MO Pago', '% MO', 'Mat Orçado', 'Mat Pago', '% Mat', 'Total', '% Total']
        ]
        
        total_geral_orcado = 0
        total_geral_pago = 0
        
        for servico in servicos:
            # Calcular valores de mão de obra
            mao_obra_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'mao_de_obra'
            )
            mao_obra_orcado = servico.valor_global_mao_de_obra
            perc_mao_obra = (mao_obra_pago / mao_obra_orcado * 100) if mao_obra_orcado > 0 else 0
            
            # Calcular valores de material
            material_pago = sum(
                pag.valor_pago for pag in servico.pagamentos 
                if pag.tipo_pagamento == 'material'
            )
            material_orcado = servico.valor_global_material
            perc_material = (material_pago / material_orcado * 100) if material_orcado > 0 else 0
            
            # Totais
            total_orcado = mao_obra_orcado + material_orcado
            total_pago = mao_obra_pago + material_pago
            perc_total = (total_pago / total_orcado * 100) if total_orcado > 0 else 0
            
            total_geral_orcado += total_orcado
            total_geral_pago += total_pago
            
            # Truncar nome do serviço se muito longo
            nome_servico = servico.nome if len(servico.nome) <= 20 else servico.nome[:17] + '...'
            resp = servico.responsavel if servico.responsavel and len(servico.responsavel) <= 15 else (servico.responsavel[:12] + '...' if servico.responsavel else '-')
            
            data.append([
                nome_servico,
                resp,
                formatar_real(mao_obra_orcado),
                formatar_real(mao_obra_pago),
                f'{perc_mao_obra:.1f}%',
                formatar_real(material_orcado),
                formatar_real(material_pago),
                f'{perc_material:.1f}%',
                formatar_real(total_orcado),
                f'{perc_total:.1f}%'
            ])
        
        # Linha de totais
        perc_geral = (total_geral_pago / total_geral_orcado * 100) if total_geral_orcado > 0 else 0
        data.append([
            'TOTAL',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            formatar_real(total_geral_orcado),
            f'{perc_geral:.1f}%'
        ])
        
        # Criar tabela
        table = Table(data, colWidths=[3*cm, 2.5*cm, 2*cm, 2*cm, 1.5*cm, 2*cm, 2*cm, 1.5*cm, 2.5*cm, 1.5*cm])
        
        # Estilo da tabela
        style_list = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4CAF50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.whitesmoke, colors.white]),
            # Linha de totais
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#FFC107')),
            ('TEXTCOLOR', (0, -1), (-1, -1), colors.black),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]
        
        table.setStyle(TableStyle(style_list))
        elements.append(table)
        
        # Legenda
        elements.append(Spacer(1, 1*cm))
        legenda = Paragraph("<b>Legenda:</b> MO = Mão de Obra | Mat = Material", styles['Normal'])
        elements.append(legenda)
        
        # Construir PDF
        doc.build(elements)
        buffer.seek(0)
        
        logger.info(f"--- [LOG] PDF de serviços gerado para obra {obra_id} ---")
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"Servicos_{obra.nome.replace(' ', '_')}_{date.today()}.pdf",
            mimetype='application/pdf'
        )
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] ao gerar PDF de serviços: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@servicos_bp.route('/servicos-usuario', methods=['GET'])
@jwt_required()
def listar_servicos_usuario():
    """
    Lista serviços personalizados do usuário com autocomplete
    Query params: q (busca)
    """
    try:
        user = get_current_user()
        q = request.args.get('q', '').strip().lower()
        
        query = ServicoUsuario.query.filter(
            ServicoUsuario.user_id == user.id
        )
        
        if q:
            query = query.filter(ServicoUsuario.descricao.ilike(f'%{q}%'))
        
        # Ordenar por mais usados primeiro
        servicos = query.order_by(
            ServicoUsuario.vezes_usado.desc(),
            ServicoUsuario.ultima_utilizacao.desc()
        ).limit(30).all()
        
        return jsonify({
            'servicos': [s.to_dict() for s in servicos],
            'total': len(servicos)
        })
        
    except Exception as e:
        return jsonify({"erro": "Erro interno no servidor"}), 500


@servicos_bp.route('/servicos-usuario', methods=['POST'])
@jwt_required()
def criar_servico_usuario():
    """
    Salva um novo serviço na biblioteca do usuário
    """
    try:
        user = get_current_user()
        dados = request.json
        
        servico = ServicoUsuario(
            user_id=user.id,
            categoria=dados.get('categoria'),
            descricao=dados['descricao'],
            unidade=dados['unidade'],
            tipo_composicao=dados.get('tipo_composicao', 'separado'),
            preco_mao_obra=dados.get('preco_mao_obra'),
            preco_material=dados.get('preco_material'),
            preco_unitario=dados.get('preco_unitario'),
            rateio_mo=dados.get('rateio_mo', 50),
            rateio_mat=dados.get('rateio_mat', 50),
            vezes_usado=1,
            ultima_utilizacao=datetime.utcnow()
        )
        
        db.session.add(servico)
        db.session.commit()
        
        return jsonify(servico.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": "Erro interno no servidor"}), 500


@servicos_bp.route('/servicos-autocomplete', methods=['GET'])
@jwt_required()
def autocomplete_servicos():
    """
    Autocomplete híbrido: primeiro serviços do usuário, depois base de referência
    """
    try:
        user = get_current_user()
        q = request.args.get('q', '').strip().lower()
        
        if len(q) < 2:
            return jsonify({'servicos_usuario': [], 'servicos_base': []})
        
        # Buscar serviços do usuário
        servicos_usuario = ServicoUsuario.query.filter(
            ServicoUsuario.user_id == user.id,
            ServicoUsuario.descricao.ilike(f'%{q}%')
        ).order_by(ServicoUsuario.vezes_usado.desc()).limit(10).all()
        
        # Buscar serviços da base
        servicos_base = ServicoBase.query.filter(
            ServicoBase.descricao.ilike(f'%{q}%')
        ).order_by(ServicoBase.descricao).limit(15).all()
        
        logger.info(f"[AUTOCOMPLETE] Busca: '{q}' -> Usuario: {len(servicos_usuario)}, Base: {len(servicos_base)}")
        
        return jsonify({
            'servicos_usuario': [s.to_dict() for s in servicos_usuario],
            'servicos_base': [s.to_dict() for s in servicos_base]
        })
        
    except Exception as e:
        logger.exception(f"[AUTOCOMPLETE] Erro: {e}")
        traceback.print_exc()
        return jsonify({"erro": "Erro interno no servidor"}), 500
