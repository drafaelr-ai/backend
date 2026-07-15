import logging
import traceback
from datetime import datetime, date, timedelta

from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models.obra import Obra
from models.lancamento import Lancamento
from models.servico import Servico
from models.pagamento_servico import PagamentoServico
from models.pagamento_parcelado import PagamentoParcelado
from models.parcela_individual import ParcelaIndividual
from models.pagamento_futuro import PagamentoFuturo
from services import get_current_user, user_has_access_to_obra, check_permission
from services.orcamento_service import resolver_orcamento_item_id

logger = logging.getLogger(__name__)

sid_bp = Blueprint('sid', __name__, url_prefix='/sid')

@sid_bp.route('/<path:any_path>', methods=['OPTIONS'])
def sid_options(any_path):
    return ('', 200)



# ===========================
# ROTAS DO CRONOGRAMA FINANCEIRO
# ===========================

# --- PAGAMENTOS FUTUROS (Únicos) ---
@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-futuros', methods=['GET'])
@jwt_required()
def listar_pagamentos_futuros(obra_id):
    """Lista todos os pagamentos futuros de uma obra, incluindo pagamentos de serviços pendentes"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        resultado = []
        
        # 1. Pagamentos Futuros (cadastrados pelo botão azul) - 1 query
        pagamentos_futuros = PagamentoFuturo.query.filter_by(obra_id=obra_id).order_by(PagamentoFuturo.data_vencimento).all()
        for p in pagamentos_futuros:
            resultado.append(p.to_dict())
        
        # 2. Pagamentos de Serviços com saldo pendente - OTIMIZADO: 1 query com JOIN
        pagamentos_servico_pendentes = db.session.query(
            PagamentoServico, Servico.nome.label('servico_nome')
        ).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_pago < PagamentoServico.valor_total,
            PagamentoServico.data_vencimento.isnot(None)
        ).all()
        
        hoje = date.today()
        for pag_serv, servico_nome in pagamentos_servico_pendentes:
            valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
            if valor_pendente > 0:
                dias_para_vencer = (pag_serv.data_vencimento - hoje).days
                resultado.append({
                    'id': f'servico-{pag_serv.id}',
                    'tipo_origem': 'servico',
                    'pagamento_servico_id': pag_serv.id,
                    'servico_id': pag_serv.servico_id,
                    'servico_nome': servico_nome,
                    'descricao': f"{servico_nome} - {pag_serv.tipo_pagamento.replace('_', ' ').title()}",
                    'fornecedor': pag_serv.fornecedor,
                    'valor': valor_pendente,
                    'data_vencimento': pag_serv.data_vencimento.isoformat(),
                    'status': 'Previsto',
                    'dias_para_vencer': dias_para_vencer,
                    'vencido': dias_para_vencer < 0,
                    'periodicidade': None
                })
        
        # Ordenar por data de vencimento
        resultado.sort(key=lambda x: x.get('data_vencimento', '9999-12-31'))
        
        return jsonify(resultado), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-futuros', methods=['POST', 'OPTIONS'])
@jwt_required()
def criar_pagamento_futuro(obra_id):
    """Cria um novo pagamento futuro"""
    # OPTIONS é permitido sem JWT
    if request.method == 'OPTIONS':
        return '', 200
    
    # POST requer JWT
    try:
        logger.debug(f"--- [DEBUG] Iniciando criação de pagamento futuro na obra {obra_id} ---")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        logger.debug(f"--- [DEBUG] Dados recebidos: {data} ---")
        
        pix_value = data.get('pix')
        logger.debug(f"--- [DEBUG] Campo PIX recebido: '{pix_value}' (tipo: {type(pix_value)}) ---")
        
        # Obter servico_id e tipo do payload
        servico_id = data.get('servico_id')
        tipo = data.get('tipo')  # 'Mão de Obra', 'Material', ou 'Despesa'
        status = data.get('status', 'Previsto')
        
        logger.debug(f"--- [DEBUG] servico_id: {servico_id}, tipo: {tipo}, status: {status} ---")
        
        # ===== CASO 1: Status='Pago' e tem servico_id → Criar PagamentoServico diretamente =====
        if status == 'Pago' and servico_id:
            servico = db.session.get(Servico, servico_id)
            if servico:
                logger.debug(f"--- [DEBUG] Pagamento já PAGO com serviço vinculado, criando PagamentoServico ---")
                
                # Determinar tipo_pagamento
                if tipo == 'Mão de Obra':
                    tipo_pagamento = 'mao_de_obra'
                elif tipo == 'Equipamentos':
                    tipo_pagamento = 'equipamento'
                elif tipo == 'Material':
                    tipo_pagamento = 'material'
                else:
                    tipo_pagamento = 'material'  # default

                # Criar PagamentoServico
                novo_pag_servico = PagamentoServico(
                    servico_id=servico_id,
                    tipo_pagamento=tipo_pagamento,
                    valor_total=float(data.get('valor', 0)),
                    valor_pago=float(data.get('valor', 0)),
                    data=date.today(),
                    data_vencimento=datetime.strptime(data.get('data_vencimento'), '%Y-%m-%d').date(),
                    status='Pago',
                    prioridade=0,
                    fornecedor=data.get('fornecedor'),
                    pix=pix_value
                )
                db.session.add(novo_pag_servico)
                db.session.flush()
                
                logger.debug(f"--- [DEBUG] PagamentoServico criado com ID={novo_pag_servico.id}, tipo_pagamento={tipo_pagamento} ---")
                
                # Recalcular percentual do serviço
                pagamentos_serv = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                pagamentos_mao_de_obra = [p for p in pagamentos_serv if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_material = [p for p in pagamentos_serv if p.tipo_pagamento == 'material']
                
                if servico.valor_global_mao_de_obra > 0:
                    total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                    servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                    logger.debug(f"--- [DEBUG] Percentual MO atualizado: {servico.percentual_conclusao_mao_obra:.1f}% ---")
                
                if servico.valor_global_material > 0:
                    total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                    servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                    logger.debug(f"--- [DEBUG] Percentual Material atualizado: {servico.percentual_conclusao_material:.1f}% ---")
                
                db.session.commit()
                logger.info(f"--- [LOG] ✅ Pagamento PAGO criado como PagamentoServico ID={novo_pag_servico.id} vinculado ao serviço '{servico.nome}' ---")
                
                return jsonify({
                    "mensagem": f"Pagamento criado e vinculado ao serviço '{servico.nome}'",
                    "pagamento_servico_id": novo_pag_servico.id,
                    "tipo_pagamento": tipo_pagamento,
                    "percentual_mo": servico.percentual_conclusao_mao_obra,
                    "percentual_material": servico.percentual_conclusao_material
                }), 201
        
        # ===== CASO 2: Criar PagamentoFuturo normalmente =====
        novo_pagamento = PagamentoFuturo(
            obra_id=obra_id,
            descricao=data.get('descricao'),
            valor=float(data.get('valor', 0)),
            data_vencimento=datetime.strptime(data.get('data_vencimento'), '%Y-%m-%d').date(),
            fornecedor=data.get('fornecedor'),
            pix=pix_value,
            codigo_barras=data.get('codigo_barras'),
            observacoes=data.get('observacoes'),
            status=status,
            servico_id=servico_id if servico_id else None,
            tipo=tipo if tipo else None
        )
        
        logger.debug(f"--- [DEBUG] Objeto PagamentoFuturo criado, tentando adicionar ao banco... ---")
        db.session.add(novo_pagamento)
        db.session.flush()  # Flush para obter o ID antes do commit
        logger.debug(f"--- [DEBUG] Flush OK, ID atribuído: {novo_pagamento.id} ---")
        
        # Vínculo com item do orçamento — via ORM, com validação explícita.
        oid, erro = resolver_orcamento_item_id(data.get('orcamento_item_id'))
        if erro:
            db.session.rollback()
            logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (novo pagamento): {erro} ---")
            return jsonify({"erro": erro}), 400
        novo_pagamento.orcamento_item_id = oid

        db.session.commit()
        logger.debug(f"--- [DEBUG] Commit realizado! ---")
        
        # Verificar se foi salvo
        verificacao = PagamentoFuturo.query.get(novo_pagamento.id)
        if verificacao:
            logger.debug(f"--- [DEBUG] ✅ VERIFICAÇÃO: PagamentoFuturo ID {verificacao.id} encontrado no banco ---")
            logger.debug(f"--- [DEBUG] ✅ Descrição: {verificacao.descricao}, Valor: {verificacao.valor}, Tipo: {verificacao.tipo}, Serviço: {verificacao.servico_id} ---")
        else:
            logger.error(f"--- [DEBUG] ❌ ERRO: PagamentoFuturo NÃO encontrado após commit! ---")
        
        logger.info(f"--- [LOG] ✅ Pagamento futuro criado: ID {novo_pagamento.id} na obra {obra_id} com Tipo: {tipo}, Serviço: {servico_id} ---")
        return jsonify(novo_pagamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] ❌ POST /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/<int:pagamento_id>', methods=['PUT', 'OPTIONS'])
@jwt_required()
def editar_pagamento_futuro(obra_id, pagamento_id):
    """Edita um pagamento futuro existente"""
    # OPTIONS é permitido sem JWT
    if request.method == 'OPTIONS':
        return '', 200
    
    # PUT requer JWT
    try:
        logger.debug(f"--- [DEBUG] Iniciando edição do pagamento {pagamento_id} da obra {obra_id} ---")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoFuturo, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        data = request.get_json()
        logger.debug(f"--- [DEBUG] Dados recebidos: {data} ---")
        
        if 'descricao' in data:
            pagamento.descricao = data['descricao']
        if 'valor' in data:
            pagamento.valor = float(data['valor'])
        if 'data_vencimento' in data:
            pagamento.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        if 'fornecedor' in data:
            pagamento.fornecedor = data['fornecedor']
        if 'pix' in data:
            logger.debug(f"--- [DEBUG] Salvando PIX: {data['pix']} ---")
            pagamento.pix = data['pix']
        if 'codigo_barras' in data:
            pagamento.codigo_barras = data['codigo_barras']
        if 'observacoes' in data:
            pagamento.observacoes = data['observacoes']
        if 'status' in data:
            pagamento.status = data['status']
        
        # CORRIGIDO: Atualizar tipo e servico_id
        if 'tipo' in data:
            pagamento.tipo = data['tipo']
            logger.debug(f"--- [DEBUG] Tipo atualizado: {data['tipo']} ---")
        if 'servico_id' in data:
            pagamento.servico_id = data['servico_id'] if data['servico_id'] else None
            logger.debug(f"--- [DEBUG] Serviço ID atualizado: {data['servico_id']} ---")
        
        # Vínculo com item do orçamento — via ORM, com validação explícita.
        # Item inválido => 400 (nunca mais 200 silencioso).
        if 'orcamento_item_id' in data:
            oid, erro = resolver_orcamento_item_id(data.get('orcamento_item_id'))
            if erro:
                db.session.rollback()
                logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (pagamento {pagamento_id}): {erro} ---")
                return jsonify({"erro": erro}), 400
            pagamento.orcamento_item_id = oid

        logger.debug(f"--- [DEBUG] Tentando commit no banco... ---")
        db.session.commit()
        
        logger.info(f"--- [LOG] ✅ Pagamento futuro {pagamento_id} editado com sucesso na obra {obra_id} ---")
        return jsonify(pagamento.to_dict()), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] ❌ PUT /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/{pagamento_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/<int:pagamento_id>', methods=['DELETE'])
@jwt_required()
def deletar_pagamento_futuro(obra_id, pagamento_id):
    """Deleta um pagamento futuro"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoFuturo, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        db.session.delete(pagamento)
        db.session.commit()
        
        logger.info(f"--- [LOG] Pagamento futuro {pagamento_id} deletado da obra {obra_id} ---")
        return jsonify({"mensagem": "Pagamento futuro deletado com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] DELETE /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/{pagamento_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


# ===================================================================================
# ROTAS DE CORREÇÃO DE PAGAMENTOS - Para corrigir tipo_pagamento em PagamentoServico
# ===================================================================================

@sid_bp.route('/obras/<int:obra_id>/diagnostico-pagamentos', methods=['GET'])
@jwt_required()
def diagnostico_pagamentos(obra_id):
    """
    Diagnóstico de pagamentos - Lista todos os PagamentoServico da obra
    mostrando o tipo_pagamento atual para identificar os que precisam correção
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar todos os serviços da obra
        servicos = Servico.query.filter_by(obra_id=obra_id).all()
        
        resultado = []
        total_pagamentos = 0
        pagamentos_mao_obra = 0
        pagamentos_material = 0
        pagamentos_indefinido = 0
        
        for servico in servicos:
            pagamentos = PagamentoServico.query.filter_by(servico_id=servico.id).all()
            
            pagamentos_servico = []
            for pag in pagamentos:
                total_pagamentos += 1
                
                if pag.tipo_pagamento == 'mao_de_obra':
                    pagamentos_mao_obra += 1
                elif pag.tipo_pagamento == 'material':
                    pagamentos_material += 1
                else:
                    pagamentos_indefinido += 1
                
                pagamentos_servico.append({
                    'id': pag.id,
                    'valor_pago': pag.valor_pago,
                    'tipo_pagamento': pag.tipo_pagamento,
                    'data': pag.data.isoformat() if pag.data else None,
                    'fornecedor': pag.fornecedor,
                    'status': pag.status
                })
            
            if pagamentos_servico:
                resultado.append({
                    'servico_id': servico.id,
                    'servico_nome': servico.nome,
                    'servico_codigo': servico.codigo,
                    'valor_mao_obra_orcado': servico.valor_global_mao_de_obra,
                    'valor_material_orcado': servico.valor_global_material,
                    'percentual_mao_obra': servico.percentual_conclusao_mao_obra,
                    'percentual_material': servico.percentual_conclusao_material,
                    'pagamentos': pagamentos_servico
                })
        
        return jsonify({
            'obra_id': obra_id,
            'resumo': {
                'total_pagamentos': total_pagamentos,
                'mao_de_obra': pagamentos_mao_obra,
                'material': pagamentos_material,
                'indefinido': pagamentos_indefinido
            },
            'servicos': resultado
        }), 200
        
    except Exception as e:
        logger.exception(f"[ERRO] Diagnóstico pagamentos: {e}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@sid_bp.route('/obras/<int:obra_id>/corrigir-pagamento/<int:pagamento_id>', methods=['PUT'])
@jwt_required()
def corrigir_tipo_pagamento(obra_id, pagamento_id):
    """
    Corrige o tipo_pagamento de um PagamentoServico existente
    e recalcula os percentuais do serviço
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        novo_tipo = data.get('tipo_pagamento')
        
        if novo_tipo not in ['mao_de_obra', 'material']:
            return jsonify({"erro": "tipo_pagamento deve ser 'mao_de_obra' ou 'material'"}), 400
        
        # Buscar o pagamento
        pagamento = db.session.get(PagamentoServico, pagamento_id)
        if not pagamento:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        # Verificar se o serviço pertence à obra
        servico = db.session.get(Servico, pagamento.servico_id)
        if not servico or servico.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não pertence a esta obra"}), 403
        
        tipo_anterior = pagamento.tipo_pagamento
        
        # Atualizar o tipo
        pagamento.tipo_pagamento = novo_tipo
        
        # Recalcular percentuais do serviço
        pagamentos_serv = PagamentoServico.query.filter_by(servico_id=servico.id).all()
        pagamentos_mao_de_obra = [p for p in pagamentos_serv if p.tipo_pagamento == 'mao_de_obra']
        pagamentos_material = [p for p in pagamentos_serv if p.tipo_pagamento == 'material']
        
        if servico.valor_global_mao_de_obra > 0:
            total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
            servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
        else:
            servico.percentual_conclusao_mao_obra = 0
        
        if servico.valor_global_material > 0:
            total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
            servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
        else:
            servico.percentual_conclusao_material = 0
        
        db.session.commit()
        
        logger.info(f"--- [LOG] ✅ Pagamento {pagamento_id} corrigido: {tipo_anterior} → {novo_tipo} ---")
        logger.info(f"--- [LOG] Serviço '{servico.nome}': MO={servico.percentual_conclusao_mao_obra:.1f}%, MAT={servico.percentual_conclusao_material:.1f}% ---")
        
        return jsonify({
            "mensagem": f"Tipo de pagamento corrigido de '{tipo_anterior}' para '{novo_tipo}'",
            "pagamento_id": pagamento_id,
            "servico": servico.nome,
            "novo_percentual_mao_obra": servico.percentual_conclusao_mao_obra,
            "novo_percentual_material": servico.percentual_conclusao_material
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] Corrigir pagamento: {e}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@sid_bp.route('/obras/<int:obra_id>/corrigir-pagamentos-lote', methods=['POST'])
@jwt_required()
def corrigir_pagamentos_lote(obra_id):
    """
    Corrige múltiplos pagamentos de uma vez
    Body: { "correcoes": [ { "pagamento_id": 1, "tipo_pagamento": "mao_de_obra" }, ... ] }
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        correcoes = data.get('correcoes', [])
        
        if not correcoes:
            return jsonify({"erro": "Nenhuma correção fornecida"}), 400
        
        resultados = []
        servicos_afetados = set()
        
        for correcao in correcoes:
            pagamento_id = correcao.get('pagamento_id')
            novo_tipo = correcao.get('tipo_pagamento')
            
            if novo_tipo not in ['mao_de_obra', 'material']:
                resultados.append({
                    "pagamento_id": pagamento_id,
                    "status": "erro",
                    "mensagem": "tipo_pagamento inválido"
                })
                continue
            
            pagamento = db.session.get(PagamentoServico, pagamento_id)
            if not pagamento:
                resultados.append({
                    "pagamento_id": pagamento_id,
                    "status": "erro",
                    "mensagem": "Pagamento não encontrado"
                })
                continue
            
            servico = db.session.get(Servico, pagamento.servico_id)
            if not servico or servico.obra_id != obra_id:
                resultados.append({
                    "pagamento_id": pagamento_id,
                    "status": "erro",
                    "mensagem": "Pagamento não pertence a esta obra"
                })
                continue
            
            tipo_anterior = pagamento.tipo_pagamento
            pagamento.tipo_pagamento = novo_tipo
            servicos_afetados.add(servico.id)
            
            resultados.append({
                "pagamento_id": pagamento_id,
                "status": "ok",
                "tipo_anterior": tipo_anterior,
                "novo_tipo": novo_tipo,
                "servico": servico.nome
            })
        
        # Recalcular percentuais de todos os serviços afetados
        for servico_id in servicos_afetados:
            servico = db.session.get(Servico, servico_id)
            if servico:
                pagamentos_serv = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                pagamentos_mao_de_obra = [p for p in pagamentos_serv if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_material = [p for p in pagamentos_serv if p.tipo_pagamento == 'material']
                
                if servico.valor_global_mao_de_obra > 0:
                    total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                    servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                else:
                    servico.percentual_conclusao_mao_obra = 0
                
                if servico.valor_global_material > 0:
                    total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                    servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                else:
                    servico.percentual_conclusao_material = 0
        
        db.session.commit()
        
        corrigidos = len([r for r in resultados if r['status'] == 'ok'])
        logger.info(f"--- [LOG] ✅ Correção em lote: {corrigidos} pagamentos corrigidos, {len(servicos_afetados)} serviços recalculados ---")
        
        return jsonify({
            "mensagem": f"{corrigidos} pagamentos corrigidos",
            "servicos_recalculados": len(servicos_afetados),
            "resultados": resultados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] Correção em lote: {e}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/<int:pagamento_id>/marcar-pago', methods=['POST'])
@jwt_required()
def marcar_pagamento_futuro_pago(obra_id, pagamento_id):
    """Marca um pagamento futuro como pago e move para o histórico ou serviço"""
    try:
        logger.info(f"\n{'='*80}")
        logger.info(f"💰 INÍCIO: marcar_pagamento_futuro_pago")
        logger.info(f"   obra_id={obra_id}, pagamento_id={pagamento_id}")
        logger.info(f"{'='*80}")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoFuturo, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        if pagamento.status == 'Pago':
            return jsonify({"mensagem": "Pagamento já está marcado como pago"}), 200
        
        logger.info(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        logger.info(f"      - servico_id: {pagamento.servico_id}")
        logger.info(f"      - tipo: {pagamento.tipo}")
        logger.info(f"      - valor: R$ {pagamento.valor}")
        
        data_pagamento = date.today()
        
        # ===== LÓGICA CORRIGIDA: Verificar se tem vínculo com serviço =====
        
        # CASO 1: Pagamento vinculado a SERVIÇO
        if pagamento.servico_id:
            servico = db.session.get(Servico, pagamento.servico_id)
            if servico:
                logger.info(f"   📋 Pagamento vinculado ao serviço '{servico.nome}'")
                
                # Determinar tipo_pagamento
                if pagamento.tipo == 'Mão de Obra':
                    tipo_pagamento = 'mao_de_obra'
                elif pagamento.tipo == 'Equipamentos':
                    tipo_pagamento = 'equipamento'
                elif pagamento.tipo == 'Material':
                    tipo_pagamento = 'material'
                else:
                    tipo_pagamento = 'material'  # default

                logger.info(f"      - tipo_pagamento determinado: {tipo_pagamento}")
                
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
                
                logger.info(f"   ✅ PagamentoServico criado com ID={novo_pag_servico.id}")
                
                # Recalcular percentual do serviço
                pagamentos_serv = PagamentoServico.query.filter_by(servico_id=servico.id).all()
                pagamentos_mao_de_obra = [p for p in pagamentos_serv if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_material = [p for p in pagamentos_serv if p.tipo_pagamento == 'material']
                
                if servico.valor_global_mao_de_obra > 0:
                    total_pago_mao = sum(p.valor_pago for p in pagamentos_mao_de_obra)
                    servico.percentual_conclusao_mao_obra = min(100, (total_pago_mao / servico.valor_global_mao_de_obra) * 100)
                    logger.info(f"   📊 Percentual MO atualizado: {servico.percentual_conclusao_mao_obra:.1f}%")
                
                if servico.valor_global_material > 0:
                    total_pago_mat = sum(p.valor_pago for p in pagamentos_material)
                    servico.percentual_conclusao_material = min(100, (total_pago_mat / servico.valor_global_material) * 100)
                    logger.info(f"   📊 Percentual Material atualizado: {servico.percentual_conclusao_material:.1f}%")
                
                # DELETE o PagamentoFuturo
                db.session.delete(pagamento)
                
                # Commit das alterações
                db.session.commit()
                
                logger.info(f"   🎉 SUCESSO: Pagamento vinculado ao serviço '{servico.nome}' e marcado como pago")
                logger.info(f"{'='*80}\n")
                
                return jsonify({
                    "mensagem": f"Pagamento vinculado ao serviço '{servico.nome}' e marcado como pago",
                    "pagamento_servico_id": novo_pag_servico.id
                }), 200
            else:
                logger.warning(f"   ⚠️ Serviço {pagamento.servico_id} não encontrado, criando lançamento genérico")
        
        # CASO 2: Pagamento SEM vínculo com serviço
        logger.info(f"   📄 Criando lançamento no histórico (sem vínculo de serviço)")
        
        # Buscar orcamento_item_id do PagamentoFuturo original
        orcamento_item_id_original = None
        try:
            result = db.session.execute(db.text(
                f"SELECT orcamento_item_id FROM pagamento_futuro WHERE id = {pagamento_id}"
            )).fetchone()
            if result and result[0]:
                orcamento_item_id_original = result[0]
                logger.info(f"   📦 Item do orçamento vinculado: {orcamento_item_id_original}")
        except Exception as e:
            logger.exception(f"   ⚠️ Erro ao buscar orcamento_item_id: {e}")
        
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
        
        # Copiar orcamento_item_id (dado interno já validado) para o novo lançamento via ORM.
        if orcamento_item_id_original:
            novo_lancamento.orcamento_item_id = orcamento_item_id_original
            logger.info(f"   ✅ orcamento_item_id copiado para lançamento")
        
        # DELETE o PagamentoFuturo
        db.session.delete(pagamento)
        
        # Commit das alterações
        db.session.commit()
        
        logger.info(f"   ✅ Lançamento criado com ID={novo_lancamento.id}")
        logger.info(f"   🎉 SUCESSO: Pagamento movido para o histórico")
        logger.info(f"{'='*80}\n")
        
        return jsonify({
            "mensagem": "Pagamento marcado como pago e movido para o histórico com sucesso",
            "lancamento_id": novo_lancamento.id
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.info(f"\n{'='*80}")
        logger.error(f"❌ ERRO em marcar_pagamento_futuro_pago:")
        logger.info(f"   {str(e)}")
        logger.info(f"\nStack trace:")
        logger.error(error_details)
        logger.info(f"{'='*80}\n")
        return jsonify({"erro": "Erro interno no servidor"}), 500

# --- PAGAMENTOS PARCELADOS ---
@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados', methods=['GET'])
@jwt_required()
def listar_pagamentos_parcelados(obra_id):
    """Lista todos os pagamentos parcelados de uma obra - OTIMIZADO"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Query única com eager loading das parcelas
        pagamentos = PagamentoParcelado.query.filter_by(obra_id=obra_id).order_by(PagamentoParcelado.data_primeira_parcela).all()
        
        # Buscar todas as parcelas de uma vez só - 1 query
        pagamento_ids = [p.id for p in pagamentos]
        if pagamento_ids:
            todas_parcelas = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id.in_(pagamento_ids)
            ).order_by(ParcelaIndividual.pagamento_parcelado_id, ParcelaIndividual.numero_parcela).all()
            
            # Agrupar parcelas por pagamento_parcelado_id
            parcelas_por_pagamento = {}
            for parcela in todas_parcelas:
                if parcela.pagamento_parcelado_id not in parcelas_por_pagamento:
                    parcelas_por_pagamento[parcela.pagamento_parcelado_id] = []
                parcelas_por_pagamento[parcela.pagamento_parcelado_id].append(parcela)
        else:
            parcelas_por_pagamento = {}
        
        # Montar resultado
        resultado = []
        for pag in pagamentos:
            pag_dict = pag.to_dict()
            parcelas = parcelas_por_pagamento.get(pag.id, [])
            
            if parcelas:
                # Encontrar a próxima parcela não paga
                proxima_parcela = next((p for p in parcelas if p.status not in ['Pago', 'pago']), None)
                if proxima_parcela:
                    pag_dict['valor_proxima_parcela'] = float(proxima_parcela.valor_parcela)
                else:
                    pag_dict['valor_proxima_parcela'] = float(parcelas[0].valor_parcela)
            
            resultado.append(pag_dict)
        
        return jsonify(resultado), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados', methods=['POST'])
@jwt_required()
def criar_pagamento_parcelado(obra_id):
    """Cria um novo pagamento parcelado com suporte a entrada e parcelas customizadas (boletos)"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        data = request.get_json()
        
        valor_total = float(data.get('valor_total', 0))
        numero_parcelas = int(data.get('numero_parcelas', 1))
        periodicidade = data.get('periodicidade', 'Mensal')  # Semanal, Quinzenal ou Mensal
        forma_pagamento = data.get('forma_pagamento', 'PIX')  # PIX, Boleto, Transferência
        
        # 🆕 Verificar se tem entrada
        tem_entrada = data.get('tem_entrada', False)
        valor_entrada = float(data.get('valor_entrada', 0)) if tem_entrada else 0
        data_entrada = data.get('data_entrada')
        percentual_entrada = float(data.get('percentual_entrada', 0)) if tem_entrada else 0
        
        # Calcular valor das parcelas (após entrada) — arredondado; resíduo de
        # centavos vai para a última parcela no loop de geração.
        valor_restante = round(valor_total - valor_entrada, 2)
        valor_parcela = round(valor_restante / numero_parcelas, 2) if numero_parcelas > 0 else 0
        
        # Total de pagamentos = entrada (se houver) + parcelas
        total_pagamentos = numero_parcelas + (1 if tem_entrada else 0)
        
        logger.info(f"--- [LOG] Criando parcelamento: Total={valor_total}, Entrada={valor_entrada} ({percentual_entrada}%), Parcelas={numero_parcelas}x{valor_parcela:.2f} ---")
        
        # Criar pagamento parcelado
        novo_pagamento = PagamentoParcelado(
            obra_id=obra_id,
            descricao=data.get('descricao'),
            fornecedor=data.get('fornecedor') or None,
            servico_id=data.get('servico_id') or None,
            valor_total=valor_total,
            numero_parcelas=total_pagamentos,  # Incluir entrada no total de parcelas
            valor_parcela=valor_parcela,
            data_primeira_parcela=datetime.strptime(data.get('data_primeira_parcela'), '%Y-%m-%d').date(),
            periodicidade=periodicidade,
            parcelas_pagas=0,
            status='Ativo',
            observacoes=data.get('observacoes') or None
        )
        
        # Tentar atribuir campos opcionais
        try:
            novo_pagamento.pix = data.get('pix') or None
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            pass
        
        try:
            novo_pagamento.forma_pagamento = forma_pagamento
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            pass
        
        db.session.add(novo_pagamento)
        db.session.flush()  # Para obter o ID do pagamento
        
        # Vínculo com item do orçamento — via ORM, com validação explícita.
        oid, erro = resolver_orcamento_item_id(data.get('orcamento_item_id'))
        if erro:
            db.session.rollback()
            logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (novo parcelado): {erro} ---")
            return jsonify({"erro": erro}), 400
        novo_pagamento.orcamento_item_id = oid
        
        # 🆕 Criar parcela de ENTRADA (se houver)
        if tem_entrada and valor_entrada > 0:
            data_entrada_parsed = datetime.strptime(data_entrada, '%Y-%m-%d').date() if data_entrada else date.today()
            
            parcela_entrada = ParcelaIndividual(
                pagamento_parcelado_id=novo_pagamento.id,
                numero_parcela=0,  # Parcela 0 = Entrada
                valor_parcela=valor_entrada,
                data_vencimento=data_entrada_parsed,
                status='Previsto',
                data_pagamento=None,
                forma_pagamento=forma_pagamento,
                observacao=f'ENTRADA ({percentual_entrada:.0f}%)'
            )
            db.session.add(parcela_entrada)
            logger.info(f"--- [LOG] Parcela de ENTRADA criada: R$ {valor_entrada:.2f} para {data_entrada_parsed} ---")
        
        # Verificar se há parcelas customizadas (valores diferentes ou boletos com código)
        parcelas_customizadas = data.get('parcelas_customizadas', [])
        
        if parcelas_customizadas and len(parcelas_customizadas) > 0:
            # Criar parcelas com valores e códigos de barras customizados
            logger.info(f"--- [LOG] Criando {len(parcelas_customizadas)} parcelas customizadas ---")
            
            for i, parcela_data in enumerate(parcelas_customizadas):
                numero = i + 1
                valor = float(parcela_data.get('valor', valor_parcela))
                data_venc = datetime.strptime(parcela_data.get('data_vencimento'), '%Y-%m-%d').date()
                codigo_barras = parcela_data.get('codigo_barras') or None
                
                nova_parcela = ParcelaIndividual(
                    pagamento_parcelado_id=novo_pagamento.id,
                    numero_parcela=numero,
                    valor_parcela=valor,
                    data_vencimento=data_venc,
                    status='Previsto',
                    data_pagamento=None,
                    forma_pagamento=forma_pagamento,
                    observacao=None
                )
                
                try:
                    nova_parcela.codigo_barras = codigo_barras
                except Exception:
                    logger.warning("Excecao suprimida em ", exc_info=True)
                    pass
                
                db.session.add(nova_parcela)
            
            # Atualizar valor_total se houver valores customizados
            soma_valores = sum(float(p.get('valor', 0)) for p in parcelas_customizadas)
            novo_pagamento.valor_total = soma_valores + valor_entrada
            
        else:
            # Criar parcelas com valores iguais (última absorve o resíduo de centavos)
            data_primeira = datetime.strptime(data.get('data_primeira_parcela'), '%Y-%m-%d').date()

            soma_geradas = 0.0
            for i in range(numero_parcelas):
                # Calcular data da parcela
                if periodicidade == 'Semanal':
                    data_parcela = data_primeira + timedelta(weeks=i)
                elif periodicidade == 'Quinzenal':
                    data_parcela = data_primeira + timedelta(weeks=i*2)
                else:  # Mensal
                    mes = data_primeira.month + i
                    ano = data_primeira.year + (mes - 1) // 12
                    mes = ((mes - 1) % 12) + 1
                    try:
                        data_parcela = data_primeira.replace(year=ano, month=mes)
                    except ValueError:
                        import calendar
                        ultimo_dia = calendar.monthrange(ano, mes)[1]
                        data_parcela = data_primeira.replace(year=ano, month=mes, day=min(data_primeira.day, ultimo_dia))

                if i < numero_parcelas - 1:
                    valor_i = valor_parcela
                else:
                    valor_i = round(valor_restante - soma_geradas, 2)
                soma_geradas = round(soma_geradas + valor_i, 2)

                nova_parcela = ParcelaIndividual(
                    pagamento_parcelado_id=novo_pagamento.id,
                    numero_parcela=i + 1,
                    valor_parcela=valor_i,
                    data_vencimento=data_parcela,
                    status='Previsto',
                    data_pagamento=None,
                    forma_pagamento=forma_pagamento,
                    observacao=None
                )
                db.session.add(nova_parcela)
        
        db.session.commit()
        
        msg = f"Pagamento parcelado criado: ID {novo_pagamento.id}"
        if tem_entrada:
            msg += f" (Entrada de R$ {valor_entrada:.2f} + {numero_parcelas}x R$ {valor_parcela:.2f})"
        else:
            msg += f" ({numero_parcelas}x R$ {valor_parcela:.2f})"
        
        logger.info(f"--- [LOG] {msg} ---")
        return jsonify(novo_pagamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] POST /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>', methods=['PUT'])
@jwt_required()
def editar_pagamento_parcelado(obra_id, pagamento_id):
    """Edita um pagamento parcelado existente"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        data = request.get_json()
        
        if 'descricao' in data:
            pagamento.descricao = data['descricao']
        if 'fornecedor' in data:
            pagamento.fornecedor = data['fornecedor']
        if 'observacoes' in data:
            pagamento.observacoes = data['observacoes']
        if 'pix' in data:
            try:
                pagamento.pix = data['pix']
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)
                pass
        if 'forma_pagamento' in data:
            try:
                pagamento.forma_pagamento = data['forma_pagamento']
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)
                pass
        
        # CORREÇÃO: Atualizar servico_id quando vinculado a um serviço
        if 'servico_id' in data:
            servico_id_novo = data['servico_id']
            if servico_id_novo:
                # Validar se o serviço existe e pertence à obra
                servico = db.session.get(Servico, servico_id_novo)
                if servico and servico.obra_id == obra_id:
                    pagamento.servico_id = servico_id_novo
                    logger.info(f"--- [LOG] PagamentoParcelado {pagamento_id} vinculado ao serviço '{servico.nome}' ---")
                else:
                    logger.info(f"--- [WARN] Serviço {servico_id_novo} não encontrado ou não pertence à obra ---")
            else:
                # Desvincular do serviço
                pagamento.servico_id = None
                logger.info(f"--- [LOG] PagamentoParcelado {pagamento_id} desvinculado de serviço ---")
        
        # Vínculo com item do orçamento — via ORM, com validação explícita.
        if 'orcamento_item_id' in data:
            oid, erro = resolver_orcamento_item_id(data.get('orcamento_item_id'))
            if erro:
                db.session.rollback()
                logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (parcelado {pagamento_id}): {erro} ---")
                return jsonify({"erro": erro}), 400
            pagamento.orcamento_item_id = oid
        
        # CORREÇÃO: Atualizar segmento quando alterado
        if 'segmento' in data:
            pagamento.segmento = data['segmento']
        
        # Cancelar/reativar explícitos são permitidos; 'Concluído' e o contador
        # são SEMPRE derivados das parcelas (nunca aceitos crus do request).
        if data.get('status') in ('Cancelado', 'Ativo'):
            pagamento.status = data['status']

        # Mudanças estruturais (valor/nº de parcelas/data/periodicidade)
        # REGENERAM as parcelas em aberto — as pagas são preservadas e o
        # restante é redistribuído (com ajuste de centavos na última).
        estrutura_mudou = False
        if 'valor_total' in data:
            pagamento.valor_total = round(float(data['valor_total']), 2)
            estrutura_mudou = True
        if 'numero_parcelas' in data:
            pagamento.numero_parcelas = int(data['numero_parcelas'])
            estrutura_mudou = True
        if 'data_primeira_parcela' in data:
            pagamento.data_primeira_parcela = datetime.strptime(data['data_primeira_parcela'], '%Y-%m-%d').date()
            estrutura_mudou = True
        if 'periodicidade' in data:
            pagamento.periodicidade = data['periodicidade']
            estrutura_mudou = True

        if estrutura_mudou:
            parcelas = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=pagamento.id).all()
            pagas = [p for p in parcelas if p.status == 'Pago']
            soma_pagas = round(sum(p.valor_parcela or 0 for p in pagas), 2)
            n_novas = int(pagamento.numero_parcelas) - len(pagas)
            if n_novas < 0:
                db.session.rollback()
                return jsonify({"erro": f"numero_parcelas ({pagamento.numero_parcelas}) é menor "
                                        f"que as parcelas já pagas ({len(pagas)})"}), 400
            restante = round((pagamento.valor_total or 0) - soma_pagas, 2)
            if restante < -0.005:
                db.session.rollback()
                return jsonify({"erro": f"valor_total (R$ {pagamento.valor_total:.2f}) é menor que a "
                                        f"soma das parcelas já pagas (R$ {soma_pagas:.2f})"}), 400

            for p in parcelas:
                if p.status != 'Pago':
                    db.session.delete(p)

            if n_novas > 0:
                valor_novo = round(restante / n_novas, 2)
                max_num = max([p.numero_parcela for p in pagas], default=0)
                idx_data = len([p for p in pagas if p.numero_parcela > 0])
                soma_geradas = 0.0
                for j in range(n_novas):
                    passo = idx_data + j
                    base = pagamento.data_primeira_parcela
                    if pagamento.periodicidade == 'Semanal':
                        data_venc = base + timedelta(weeks=passo)
                    elif pagamento.periodicidade == 'Quinzenal':
                        data_venc = base + timedelta(days=passo * 15)
                    else:  # Mensal
                        mes = base.month + passo
                        ano = base.year + (mes - 1) // 12
                        mes = ((mes - 1) % 12) + 1
                        import calendar as _cal
                        dia = min(base.day, _cal.monthrange(ano, mes)[1])
                        data_venc = date(ano, mes, dia)

                    valor_j = valor_novo if j < n_novas - 1 else round(restante - soma_geradas, 2)
                    soma_geradas = round(soma_geradas + valor_j, 2)
                    db.session.add(ParcelaIndividual(
                        pagamento_parcelado_id=pagamento.id,
                        numero_parcela=max_num + 1 + j,
                        valor_parcela=valor_j,
                        data_vencimento=data_venc,
                        status='Previsto',
                    ))
                pagamento.valor_parcela = valor_novo

            pagamento.parcelas_pagas = len(pagas)
            if pagamento.status != 'Cancelado':
                pagamento.status = 'Concluído' if n_novas == 0 else 'Ativo'

        db.session.commit()
        
        logger.info(f"--- [LOG] Pagamento parcelado {pagamento_id} editado na obra {obra_id} ---")
        return jsonify(pagamento.to_dict()), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] PUT /sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados/{pagamento_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>', methods=['DELETE'])
@jwt_required()
def deletar_pagamento_parcelado(obra_id, pagamento_id):
    """Deleta um pagamento parcelado e todos os registros relacionados"""
    try:
        logger.info(f"\n{'='*80}")
        logger.info(f"🗑️ INÍCIO: deletar_pagamento_parcelado")
        logger.info(f"   obra_id={obra_id}, pagamento_id={pagamento_id}")
        logger.info(f"{'='*80}")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        logger.info(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        logger.info(f"      - servico_id: {pagamento.servico_id}")
        
        # ===== DELETAR TODOS OS REGISTROS RELACIONADOS =====

        # 1. Buscar todas as parcelas deste pagamento
        parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()

        logger.info(f"   📋 Encontradas {len(parcelas)} parcelas")

        # Bug E: cascade catch-all de Lançamentos relacionados (independente do status da parcela)
        # marcar_parcela_paga e bulk pay criam Lançamento com padrão "<descricao> (Parcela X/Y)"
        descricao_pattern = f"{pagamento.descricao} (Parcela %"
        lancs_relacionados = Lancamento.query.filter(
            Lancamento.obra_id == obra_id,
            Lancamento.descricao.like(descricao_pattern)
        ).all()
        logger.info(f"   🗑️ Cascade Lançamentos: {len(lancs_relacionados)} encontrados via padrão '{descricao_pattern}'")
        for lanc in lancs_relacionados:
            logger.error(f"      ❌ Deletando Lancamento ID={lanc.id}: '{lanc.descricao}'")
            db.session.delete(lanc)

        # 2. Para cada parcela paga, limpar PagamentoServico relacionado
        for parcela in parcelas:
            if parcela.status == 'Pago':
                # Se o pagamento está vinculado a um serviço, deletar PagamentoServico
                if pagamento.servico_id:
                    # Buscar PagamentoServico que pode ter sido criado para esta parcela
                    pagamentos_servico = PagamentoServico.query.filter_by(
                        servico_id=pagamento.servico_id,
                        fornecedor=pagamento.fornecedor
                    ).all()
                    
                    for pag_serv in pagamentos_servico:
                        # Verificar se o valor corresponde à parcela
                        # Não podemos ter certeza absoluta, então vamos deletar se o valor bate
                        # ou reduzir o valor_pago se for maior
                        if pag_serv.valor_pago >= parcela.valor_parcela:
                            if pag_serv.valor_pago == parcela.valor_parcela:
                                logger.error(f"      ❌ Deletando PagamentoServico ID={pag_serv.id} (valor_pago={pag_serv.valor_pago})")
                                db.session.delete(pag_serv)
                            else:
                                logger.info(f"      ➖ Reduzindo PagamentoServico ID={pag_serv.id}: {pag_serv.valor_pago} -> {pag_serv.valor_pago - parcela.valor_parcela}")
                                pag_serv.valor_pago -= parcela.valor_parcela
                                if pag_serv.valor_pago <= 0:
                                    logger.error(f"      ❌ Valor zerado, deletando PagamentoServico ID={pag_serv.id}")
                                    db.session.delete(pag_serv)
                            break  # Processar apenas o primeiro encontrado
        
        # 3. Deletar todas as parcelas individuais
        for parcela in parcelas:
            logger.error(f"   ❌ Deletando ParcelaIndividual ID={parcela.id}")
            db.session.delete(parcela)
        
        # 4. Finalmente, deletar o pagamento parcelado
        logger.error(f"   ❌ Deletando PagamentoParcelado ID={pagamento_id}")
        db.session.delete(pagamento)
        
        # 5. Commit de todas as alterações
        db.session.commit()
        
        logger.info(f"   🎉 SUCESSO: Pagamento parcelado e todos os registros relacionados deletados")
        logger.info(f"{'='*80}\n")
        
        return jsonify({"mensagem": "Pagamento parcelado deletado com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.info(f"\n{'='*80}")
        logger.error(f"❌ ERRO em deletar_pagamento_parcelado:")
        logger.info(f"   {str(e)}")
        logger.info(f"\nStack trace:")
        logger.error(error_details)
        logger.info(f"{'='*80}\n")
        return jsonify({"erro": "Erro interno no servidor"}), 500

# --- TABELA DE PREVISÕES (CÁLCULO) ---
@sid_bp.route('/cronograma-financeiro/<int:obra_id>/previsoes', methods=['GET'])
@jwt_required()
def calcular_previsoes(obra_id):
    """Calcula a tabela de previsões mensais - OTIMIZADO"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        previsoes_por_mes = {}
        
        # 1. Pagamentos Futuros (Únicos) - 1 query
        pagamentos_futuros = PagamentoFuturo.query.filter_by(
            obra_id=obra_id
        ).filter(
            PagamentoFuturo.status != 'Cancelado',
            PagamentoFuturo.status != 'Pago'
        ).all()
        
        for pag in pagamentos_futuros:
            mes_chave = pag.data_vencimento.strftime('%Y-%m')
            if mes_chave not in previsoes_por_mes:
                previsoes_por_mes[mes_chave] = 0
            previsoes_por_mes[mes_chave] += pag.valor
        
        # 2. Parcelas Individuais - 1 query com JOIN
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            PagamentoParcelado.status != 'Cancelado',
            ParcelaIndividual.status == 'Previsto'
        ).all()
        
        for parcela in parcelas:
            mes_chave = parcela.data_vencimento.strftime('%Y-%m')
            if mes_chave not in previsoes_por_mes:
                previsoes_por_mes[mes_chave] = 0
            previsoes_por_mes[mes_chave] += parcela.valor_parcela
        
        # 3. Pagamentos de Serviços pendentes - OTIMIZADO: 1 query com JOIN
        pagamentos_servico_pendentes = db.session.query(PagamentoServico).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_pago < PagamentoServico.valor_total,
            PagamentoServico.data_vencimento.isnot(None)
        ).all()
        
        for pag_serv in pagamentos_servico_pendentes:
            valor_pendente = pag_serv.valor_total - pag_serv.valor_pago
            if valor_pendente > 0:
                mes_chave = pag_serv.data_vencimento.strftime('%Y-%m')
                if mes_chave not in previsoes_por_mes:
                    previsoes_por_mes[mes_chave] = 0
                previsoes_por_mes[mes_chave] += valor_pendente
        
        previsoes_lista = []
        for mes_chave in sorted(previsoes_por_mes.keys()):
            ano, mes = mes_chave.split('-')
            meses_pt = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
            mes_nome = meses_pt[int(mes)]
            
            previsoes_lista.append({
                'mes_chave': mes_chave,
                'mes_nome': f"{mes_nome}/{ano}",
                'valor': round(previsoes_por_mes[mes_chave], 2)
            })
        
        return jsonify(previsoes_lista), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET previsões: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

# ========================================
# ENDPOINTS: PARCELAS INDIVIDUAIS (NOVO!)
# ========================================

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_parcelas_individuais(obra_id, pagamento_id):
    """
    Lista todas as parcelas individuais de um pagamento parcelado.
    Se as parcelas não existirem, gera automaticamente baseado na configuração do pagamento.
    """
    # Handler para OPTIONS (CORS preflight)
    if request.method == 'OPTIONS':
        return make_response('', 200)
    
    try:
        # Validações de acesso
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamento parcelado (usando db.session.get para compatibilidade)
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        # Buscar parcelas individuais existentes
        parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).order_by(ParcelaIndividual.numero_parcela).all()
        
        # Gerar parcelas automaticamente se não existirem
        if not parcelas:
            logger.info(f"--- [LOG] Gerando parcelas para Pagamento ID {pagamento_id} ---")
            
            import calendar
            from datetime import timedelta

            # Função auxiliar local para cálculo preciso de meses
            def add_months_local(source_date, months):
                month = source_date.month - 1 + months
                year = source_date.year + month // 12
                month = month % 12 + 1
                day = min(source_date.day, calendar.monthrange(year, month)[1])
                return date(year, month, day)

            valor_parcela_padrao = pagamento.valor_parcela
            
            # Preparar lista de parcelas para inserção em lote (OTIMIZAÇÃO)
            parcelas_para_inserir = []
            
            # Gerar cada parcela
            for i in range(pagamento.numero_parcelas):
                numero_parcela = i + 1
                
                # Ajustar valor da última parcela para fechar o total exato (evita dízimas)
                if numero_parcela == pagamento.numero_parcelas:
                    valor_parcelas_anteriores = valor_parcela_padrao * (pagamento.numero_parcelas - 1)
                    valor_desta_parcela = pagamento.valor_total - valor_parcelas_anteriores
                else:
                    valor_desta_parcela = valor_parcela_padrao
                
                # Calcular data de vencimento (Lógica corrigida)
                if pagamento.periodicidade == 'Semanal':
                    data_vencimento = pagamento.data_primeira_parcela + timedelta(days=7 * i)
                elif pagamento.periodicidade == 'Quinzenal':
                    data_vencimento = pagamento.data_primeira_parcela + timedelta(days=15 * i)
                else: # Mensal (Padrão)
                    data_vencimento = add_months_local(pagamento.data_primeira_parcela, i)
                
                # Determinar status inicial
                status = 'Pago' if i < pagamento.parcelas_pagas else 'Previsto'
                data_pagamento = data_vencimento if status == 'Pago' else None
                
                # Criar parcela (adicionar à lista, não ao db ainda)
                parcela = ParcelaIndividual(
                    pagamento_parcelado_id=pagamento_id,
                    numero_parcela=numero_parcela,
                    valor_parcela=valor_desta_parcela,
                    data_vencimento=data_vencimento,
                    data_pagamento=data_pagamento,
                    status=status,
                    forma_pagamento=None,
                    observacao=None
                )
                parcelas_para_inserir.append(parcela)
            
            # OTIMIZAÇÃO: Inserir todas as parcelas de uma vez (bulk insert)
            db.session.bulk_save_objects(parcelas_para_inserir)
            db.session.commit()
            logger.info(f"--- [LOG] {len(parcelas_para_inserir)} parcelas geradas em lote (bulk insert) ---")
            
            # Recarregar parcelas geradas
            parcelas = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=pagamento_id
            ).order_by(ParcelaIndividual.numero_parcela).all()
        
        return jsonify([p.to_dict() for p in parcelas]), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] listar_parcelas_individuais: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas/<int:parcela_id>', methods=['PUT'])
@jwt_required()
def editar_parcela_individual(obra_id, pagamento_id, parcela_id):
    """Edita uma parcela individual (valor, data, observação)"""
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        parcela = db.session.get(ParcelaIndividual, parcela_id)
        if not parcela or parcela.pagamento_parcelado_id != pagamento_id:
            return jsonify({"erro": "Parcela não encontrada"}), 404
        
        data = request.get_json()

        # Atualiza os campos permitidos
        if 'valor_parcela' in data:
            # Bug C: redistribuir delta nas outras parcelas PENDENTES proporcionalmente
            if parcela.status == 'Pago':
                return jsonify({"erro": "Não é possível alterar o valor de uma parcela já paga"}), 400

            novo_valor = float(data['valor_parcela'])
            if novo_valor < 0:
                return jsonify({"erro": "Valor da parcela não pode ser negativo"}), 400

            valor_antigo = float(parcela.valor_parcela or 0.0)
            delta = novo_valor - valor_antigo

            outras_pendentes = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == pagamento_id,
                ParcelaIndividual.id != parcela_id,
                ParcelaIndividual.status != 'Pago'
            ).order_by(ParcelaIndividual.numero_parcela.asc()).all()

            if abs(delta) > 0.005 and outras_pendentes:
                soma_outras = sum(float(p.valor_parcela or 0.0) for p in outras_pendentes)
                if soma_outras <= 0:
                    return jsonify({"erro": "Não há saldo positivo nas outras parcelas pendentes para redistribuir"}), 400

                ajuste_total = -delta  # se parcela aumentou, outras diminuem
                novos_valores = []
                for p in outras_pendentes:
                    valor_atual = float(p.valor_parcela or 0.0)
                    proporção = valor_atual / soma_outras
                    novo = round(valor_atual + (ajuste_total * proporção), 2)
                    if novo < 0:
                        return jsonify({
                            "erro": f"Edição inviável: parcela {p.numero_parcela} ficaria negativa após redistribuição (R$ {novo:.2f})"
                        }), 400
                    novos_valores.append(novo)

                # Acertar resíduo de arredondamento na última pendente
                desejado_outras = round(soma_outras + ajuste_total, 2)
                soma_apos = round(sum(novos_valores), 2)
                residuo = round(desejado_outras - soma_apos, 2)
                if abs(residuo) > 0.001 and novos_valores:
                    novos_valores[-1] = round(novos_valores[-1] + residuo, 2)
                    if novos_valores[-1] < 0:
                        return jsonify({
                            "erro": "Edição inviável: resíduo de arredondamento tornaria a última parcela negativa"
                        }), 400

                for p, nv in zip(outras_pendentes, novos_valores):
                    p.valor_parcela = nv
                logger.info(f"--- [Bug C] Redistribuído delta {delta:.2f} em {len(outras_pendentes)} parcelas pendentes ---")

            parcela.valor_parcela = novo_valor

        if 'data_vencimento' in data:
            parcela.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        
        if 'observacao' in data:
            parcela.observacao = data['observacao']
        
        if 'codigo_barras' in data:
            try:
                parcela.codigo_barras = data['codigo_barras'] or None
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)
                pass
        
        if 'status' in data:
            parcela.status = data['status']
            if data['status'] == 'Pago' and 'data_pagamento' in data:
                parcela.data_pagamento = datetime.strptime(data['data_pagamento'], '%Y-%m-%d').date()
        
        db.session.commit()
        
        # Recalcula o valor_total do pagamento parcelado
        todas_parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        novo_valor_total = sum(p.valor_parcela for p in todas_parcelas)
        pagamento.valor_total = novo_valor_total
        
        # Atualiza parcelas_pagas
        parcelas_pagas_count = sum(1 for p in todas_parcelas if p.status == 'Pago')
        pagamento.parcelas_pagas = parcelas_pagas_count
        
        db.session.commit()
        
        logger.info(f"--- [LOG] Parcela {parcela_id} editada ---")
        return jsonify(parcela.to_dict()), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] PUT parcela individual: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas/<int:parcela_id>/pagar', methods=['POST', 'OPTIONS'])
@jwt_required()
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
        logger.info(f"\n{'='*80}")
        logger.info(f"💳 INÍCIO: marcar_parcela_paga")
        logger.info(f"   obra_id={obra_id}, pagamento_id={pagamento_id}, parcela_id={parcela_id}")
        logger.info(f"{'='*80}")
        
        # Validações de acesso
        current_user = get_current_user()
        logger.info(f"   👤 Usuário: {current_user.username} (role: {current_user.role})")
        
        if not user_has_access_to_obra(current_user, obra_id):
            logger.error(f"   ❌ Acesso negado à obra {obra_id}")
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamento parcelado
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            logger.error(f"   ❌ Pagamento {pagamento_id} não encontrado ou não pertence à obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        logger.info(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        logger.info(f"      - servico_id: {pagamento.servico_id}")
        logger.info(f"      - fornecedor: {pagamento.fornecedor}")
        
        # Buscar parcela
        parcela = db.session.get(ParcelaIndividual, parcela_id)
        if not parcela or parcela.pagamento_parcelado_id != pagamento_id:
            logger.error(f"   ❌ Parcela {parcela_id} não encontrada ou não pertence ao pagamento {pagamento_id}")
            return jsonify({"erro": "Parcela não encontrada"}), 404
        
        if parcela.status == 'Pago':
            logger.warning(f"   ⚠️ Parcela {parcela_id} já estava paga")
            return jsonify({"mensagem": "Parcela já está marcada como paga"}), 200
        
        logger.info(f"   ✅ Parcela encontrada: {parcela.numero_parcela}/{pagamento.numero_parcelas}")
        logger.info(f"      - valor: R$ {parcela.valor_parcela}")
        
        # Processar dados
        data = request.get_json()
        
        # Marcar parcela como paga
        parcela.status = 'Pago'
        parcela.data_pagamento = datetime.strptime(
            data.get('data_pagamento', date.today().isoformat()), 
            '%Y-%m-%d'
        ).date()
        parcela.forma_pagamento = data.get('forma_pagamento', None)
        
        logger.info(f"   ✅ Parcela marcada como paga em {parcela.data_pagamento}")
        
        # Criar lançamento ou pagamento de serviço baseado no vínculo
        descricao_lancamento = f"{pagamento.descricao} (Parcela {parcela.numero_parcela}/{pagamento.numero_parcelas})"
        
        # Tratamento seguro do segmento
        segmento_info = 'Material'
        if hasattr(pagamento, 'segmento') and pagamento.segmento:
            segmento_info = pagamento.segmento
        
        logger.info(f"   📄 Processando pagamento: '{descricao_lancamento}'")
        logger.info(f"      - segmento: {segmento_info}")
        logger.info(f"      - servico_id: {pagamento.servico_id}")
        
        # CORREÇÃO: Se tem serviço vinculado, NÃO criar PagamentoServico
        # As parcelas pagas já aparecem no histórico do serviço via query de ParcelaIndividual
        # Criar PagamentoServico causaria DUPLICAÇÃO no histórico
        if pagamento.servico_id:
            servico = db.session.get(Servico, pagamento.servico_id)
            if servico:
                logger.info(f"   ✅ Parcela vinculada ao serviço '{servico.nome}'")
                logger.info(f"      - NÃO criando PagamentoServico (parcela já aparece no histórico via ParcelaIndividual)")
            else:
                logger.warning(f"   ⚠️ Serviço {pagamento.servico_id} não existe, mas parcela será mostrada via ParcelaIndividual")
        else:
            # Parcela SEM serviço - criar Lancamento normal
            logger.info(f"   ✅ Parcela sem serviço, criando lançamento geral")
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
                servico_id=None
            )
            if hasattr(novo_lancamento, 'segmento'):
                novo_lancamento.segmento = segmento_info
            db.session.add(novo_lancamento)
            db.session.flush()
            logger.info(f"   ✅ Lançamento criado com ID={novo_lancamento.id}")
        
        # Atualizar contador de parcelas pagas
        todas_parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        parcelas_pagas_count = sum(1 for p in todas_parcelas if p.status == 'Pago')
        pagamento.parcelas_pagas = parcelas_pagas_count
        
        logger.info(f"   📊 Total de parcelas pagas: {parcelas_pagas_count}/{pagamento.numero_parcelas}")
        
        # Se todas foram pagas, atualizar status
        if parcelas_pagas_count >= pagamento.numero_parcelas:
            pagamento.status = 'Concluído'
            logger.info(f"   🎉 Pagamento marcado como Concluído")
        
        # Commit final
        db.session.commit()
        
        logger.info(f"   ✅ SUCESSO: Parcela {parcela_id} marcada como paga")
        logger.info(f"{'='*80}\n")
        
        return jsonify({
            "mensagem": "Parcela paga com sucesso",
            "parcela": parcela.to_dict()
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.info(f"\n{'='*80}")
        logger.error(f"❌ ERRO FATAL em marcar_parcela_paga:")
        logger.info(f"   {str(e)}")
        logger.info(f"\nStack trace completo:")
        logger.error(error_details)
        logger.info(f"{'='*80}\n")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-parcelados/<int:pagamento_id>/parcelas/<int:parcela_id>/desfazer', methods=['POST', 'OPTIONS'])
@jwt_required()
def desfazer_pagamento_parcela(obra_id, pagamento_id, parcela_id):
    """Desfaz o pagamento de uma parcela individual - volta para status Previsto"""
    
    # Handler para OPTIONS (CORS preflight)
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response

    try:
        logger.info(f"\n{'='*80}")
        logger.info(f"↩️ INÍCIO: desfazer_pagamento_parcela")
        logger.info(f"   obra_id={obra_id}, pagamento_id={pagamento_id}, parcela_id={parcela_id}")
        logger.info(f"{'='*80}")
        
        # Validações de acesso
        current_user = get_current_user()
        logger.info(f"   👤 Usuário: {current_user.username} (role: {current_user.role})")
        
        if not user_has_access_to_obra(current_user, obra_id):
            logger.error(f"   ❌ Acesso negado à obra {obra_id}")
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamento parcelado
        pagamento = db.session.get(PagamentoParcelado, pagamento_id)
        if not pagamento or pagamento.obra_id != obra_id:
            logger.error(f"   ❌ Pagamento {pagamento_id} não encontrado ou não pertence à obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        logger.info(f"   ✅ Pagamento encontrado: '{pagamento.descricao}'")
        logger.info(f"      - servico_id: {pagamento.servico_id}")
        
        # Buscar parcela
        parcela = db.session.get(ParcelaIndividual, parcela_id)
        if not parcela or parcela.pagamento_parcelado_id != pagamento_id:
            logger.error(f"   ❌ Parcela {parcela_id} não encontrada ou não pertence ao pagamento {pagamento_id}")
            return jsonify({"erro": "Parcela não encontrada"}), 404
        
        if parcela.status != 'Pago':
            logger.warning(f"   ⚠️ Parcela {parcela_id} não está paga, status atual: {parcela.status}")
            return jsonify({"erro": "Parcela não está marcada como paga"}), 400
        
        logger.info(f"   ✅ Parcela encontrada: {parcela.numero_parcela}/{pagamento.numero_parcelas}")
        logger.info(f"      - valor: R$ {parcela.valor_parcela}")
        logger.info(f"      - data_pagamento: {parcela.data_pagamento}")
        
        # Descrição padrão da parcela para buscar registros relacionados
        descricao_parcela = f"{pagamento.descricao} (Parcela {parcela.numero_parcela}/{pagamento.numero_parcelas})"
        descricao_parcela_alt = f"{pagamento.descricao} ({parcela.numero_parcela}/{pagamento.numero_parcelas})"
        
        # Se TEM serviço vinculado, verificar e remover PagamentoServico correspondente
        if pagamento.servico_id:
            # Buscar PagamentoServico que corresponda a esta parcela
            # Pode ter sido criado antes da correção que removeu a criação automática
            pagamentos_servico = PagamentoServico.query.filter(
                PagamentoServico.servico_id == pagamento.servico_id,
                PagamentoServico.valor_total == parcela.valor_parcela
            ).all()
            
            # Tentar encontrar por descrição ou data
            for pag_serv in pagamentos_servico:
                # Verificar se é da mesma data ou descrição similar
                if (pag_serv.data_pagamento and parcela.data_pagamento and 
                    pag_serv.data_pagamento == parcela.data_pagamento):
                    logger.info(f"   🗑️ Removendo PagamentoServico ID={pag_serv.id} (mesmo valor e data)")
                    db.session.delete(pag_serv)
                    break
                elif pag_serv.descricao and (descricao_parcela in pag_serv.descricao or descricao_parcela_alt in pag_serv.descricao):
                    logger.info(f"   🗑️ Removendo PagamentoServico ID={pag_serv.id} (descrição corresponde)")
                    db.session.delete(pag_serv)
                    break
            else:
                logger.info(f"   ℹ️ Nenhum PagamentoServico correspondente encontrado (normal se criado após correção)")
        else:
            # Se NÃO tem serviço vinculado, tentar remover o lançamento criado
            lancamento_existente = Lancamento.query.filter(
                Lancamento.obra_id == pagamento.obra_id,
                db.or_(
                    Lancamento.descricao == descricao_parcela,
                    Lancamento.descricao == descricao_parcela_alt
                )
            ).first()
            
            if lancamento_existente:
                logger.info(f"   🗑️ Removendo lançamento ID={lancamento_existente.id}")
                db.session.delete(lancamento_existente)
            else:
                logger.info(f"   ℹ️ Nenhum lançamento correspondente encontrado")
        
        # Voltar parcela para status Previsto
        parcela.status = 'Previsto'
        parcela.data_pagamento = None
        parcela.forma_pagamento = None
        
        logger.info(f"   ✅ Parcela voltou para status 'Previsto'")
        
        # Atualizar contador de parcelas pagas
        todas_parcelas = ParcelaIndividual.query.filter_by(
            pagamento_parcelado_id=pagamento_id
        ).all()
        
        parcelas_pagas_count = sum(1 for p in todas_parcelas if p.status == 'Pago')
        pagamento.parcelas_pagas = parcelas_pagas_count
        
        # Voltar status do pagamento para Ativo se estava Concluído
        if pagamento.status == 'Concluído':
            pagamento.status = 'Ativo'
            logger.info(f"   ✅ Pagamento voltou para status 'Ativo'")
        
        logger.info(f"   📊 Total de parcelas pagas agora: {parcelas_pagas_count}/{pagamento.numero_parcelas}")
        
        # Commit final
        db.session.commit()
        
        logger.info(f"   ✅ SUCESSO: Pagamento da parcela {parcela_id} desfeito")
        logger.info(f"{'='*80}\n")
        
        return jsonify({
            "mensagem": "Pagamento desfeito com sucesso",
            "parcela": parcela.to_dict()
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.info(f"\n{'='*80}")
        logger.error(f"❌ ERRO FATAL em desfazer_pagamento_parcela:")
        logger.info(f"   {str(e)}")
        logger.info(f"\nStack trace completo:")
        logger.error(error_details)
        logger.info(f"{'='*80}\n")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@sid_bp.route('/cronograma-financeiro/<int:obra_id>/alertas-vencimento', methods=['GET'])
@jwt_required()
def obter_alertas_vencimento(obra_id):
    """
    Retorna um resumo dos pagamentos por categoria de vencimento:
    - Vencidos (atrasados)
    - Vence Hoje
    - Vence Amanhã
    - Vence em 7 dias
    - Futuros (mais de 7 dias)
    """
    try:
        logger.debug(f"--- [DEBUG] Iniciando obter_alertas_vencimento para obra {obra_id} ---")
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        hoje = date.today()
        amanha = hoje + timedelta(days=1)
        em_7_dias = hoje + timedelta(days=7)
        
        logger.debug(f"--- [DEBUG] Hoje: {hoje}, Amanhã: {amanha}, Em 7 dias: {em_7_dias} ---")
        
        alertas = {
            "vencidos": {"quantidade": 0, "valor_total": 0, "itens": []},
            "vence_hoje": {"quantidade": 0, "valor_total": 0, "itens": []},
            "vence_amanha": {"quantidade": 0, "valor_total": 0, "itens": []},
            "vence_7_dias": {"quantidade": 0, "valor_total": 0, "itens": []},
            "futuros": {"quantidade": 0, "valor_total": 0, "itens": []}  # CORREÇÃO: Adicionado array "itens"
        }
        
        # 1. PAGAMENTOS FUTUROS
        pagamentos_futuros = PagamentoFuturo.query.filter_by(
            obra_id=obra_id
        ).filter(
            PagamentoFuturo.status == 'Previsto'
        ).all()
        
        logger.debug(f"--- [DEBUG] Encontrados {len(pagamentos_futuros)} PagamentoFuturo com status 'Previsto' ---")
        
        for pag in pagamentos_futuros:
            logger.debug(f"--- [DEBUG] PagamentoFuturo ID {pag.id}: {pag.descricao}, Valor: {pag.valor}, Vencimento: {pag.data_vencimento} ---")
            
            item = {
                "tipo": "Pagamento Futuro",
                "descricao": pag.descricao,
                "fornecedor": pag.fornecedor,
                "valor": pag.valor,
                "data_vencimento": pag.data_vencimento.isoformat(),
                "id": pag.id
            }
            
            if pag.data_vencimento < hoje:
                logger.debug(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCIDO ---")
                alertas["vencidos"]["quantidade"] += 1
                alertas["vencidos"]["valor_total"] += pag.valor
                alertas["vencidos"]["itens"].append(item)
            elif pag.data_vencimento == hoje:
                logger.debug(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCE HOJE ---")
                alertas["vence_hoje"]["quantidade"] += 1
                alertas["vence_hoje"]["valor_total"] += pag.valor
                alertas["vence_hoje"]["itens"].append(item)
            elif pag.data_vencimento == amanha:
                logger.debug(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCE AMANHÃ ---")
                alertas["vence_amanha"]["quantidade"] += 1
                alertas["vence_amanha"]["valor_total"] += pag.valor
                alertas["vence_amanha"]["itens"].append(item)
            elif pag.data_vencimento <= em_7_dias:
                logger.debug(f"--- [DEBUG] PagamentoFuturo {pag.id} → VENCE EM 7 DIAS ---")
                alertas["vence_7_dias"]["quantidade"] += 1
                alertas["vence_7_dias"]["valor_total"] += pag.valor
                alertas["vence_7_dias"]["itens"].append(item)
            else:
                logger.debug(f"--- [DEBUG] PagamentoFuturo {pag.id} → FUTURO (>7 dias) ---")
                alertas["futuros"]["quantidade"] += 1
                alertas["futuros"]["valor_total"] += pag.valor
                alertas["futuros"]["itens"].append(item)
        
        # 2. PARCELAS INDIVIDUAIS DE PAGAMENTOS PARCELADOS
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).all()
        
        for parcela in parcelas:
            pag = parcela.pagamento_parcelado
            item = {
                "tipo": "Parcela",
                "descricao": f"{pag.descricao} - Parcela {parcela.numero_parcela}/{pag.numero_parcelas}",
                "fornecedor": pag.fornecedor,
                "valor": parcela.valor_parcela,
                "data_vencimento": parcela.data_vencimento.isoformat(),
                "id": parcela.id,
                "pagamento_parcelado_id": pag.id
            }
            
            if parcela.data_vencimento < hoje:
                alertas["vencidos"]["quantidade"] += 1
                alertas["vencidos"]["valor_total"] += parcela.valor_parcela
                alertas["vencidos"]["itens"].append(item)
            elif parcela.data_vencimento == hoje:
                alertas["vence_hoje"]["quantidade"] += 1
                alertas["vence_hoje"]["valor_total"] += parcela.valor_parcela
                alertas["vence_hoje"]["itens"].append(item)
            elif parcela.data_vencimento == amanha:
                alertas["vence_amanha"]["quantidade"] += 1
                alertas["vence_amanha"]["valor_total"] += parcela.valor_parcela
                alertas["vence_amanha"]["itens"].append(item)
            elif parcela.data_vencimento <= em_7_dias:
                alertas["vence_7_dias"]["quantidade"] += 1
                alertas["vence_7_dias"]["valor_total"] += parcela.valor_parcela
                alertas["vence_7_dias"]["itens"].append(item)
            else:
                alertas["futuros"]["quantidade"] += 1
                alertas["futuros"]["valor_total"] += parcela.valor_parcela
                alertas["futuros"]["itens"].append(item)
        
        # 3. NOVO: PAGAMENTOS DE SERVIÇOS COM SALDO PENDENTE
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
                    item = {
                        "tipo": "Pagamento Serviço",
                        "descricao": f"{servico.nome} - {pag_serv.tipo_pagamento.replace('_', ' ').title()}",
                        "fornecedor": pag_serv.fornecedor,
                        "valor": valor_pendente,
                        "data_vencimento": pag_serv.data_vencimento.isoformat(),
                        "id": pag_serv.id,
                        "servico_id": servico.id
                    }
                    
                    if pag_serv.data_vencimento < hoje:
                        alertas["vencidos"]["quantidade"] += 1
                        alertas["vencidos"]["valor_total"] += valor_pendente
                        alertas["vencidos"]["itens"].append(item)
                    elif pag_serv.data_vencimento == hoje:
                        alertas["vence_hoje"]["quantidade"] += 1
                        alertas["vence_hoje"]["valor_total"] += valor_pendente
                        alertas["vence_hoje"]["itens"].append(item)
                    elif pag_serv.data_vencimento == amanha:
                        alertas["vence_amanha"]["quantidade"] += 1
                        alertas["vence_amanha"]["valor_total"] += valor_pendente
                        alertas["vence_amanha"]["itens"].append(item)
                    elif pag_serv.data_vencimento <= em_7_dias:
                        alertas["vence_7_dias"]["quantidade"] += 1
                        alertas["vence_7_dias"]["valor_total"] += valor_pendente
                        alertas["vence_7_dias"]["itens"].append(item)
                    else:
                        alertas["futuros"]["quantidade"] += 1
                        alertas["futuros"]["valor_total"] += valor_pendente
                        alertas["futuros"]["itens"].append(item)
        
        # Arredonda os valores
        for categoria in alertas.values():
            if 'valor_total' in categoria:
                categoria['valor_total'] = round(categoria['valor_total'], 2)
        
        logger.debug(f"--- [DEBUG] RESULTADO FINAL DOS ALERTAS ---")
        logger.info(f"  Vencidos: {alertas['vencidos']['quantidade']} itens, Total: R$ {alertas['vencidos']['valor_total']}")
        logger.info(f"  Vence Hoje: {alertas['vence_hoje']['quantidade']} itens, Total: R$ {alertas['vence_hoje']['valor_total']}")
        logger.info(f"  Vence Amanhã: {alertas['vence_amanha']['quantidade']} itens, Total: R$ {alertas['vence_amanha']['valor_total']}")
        logger.info(f"  Vence em 7 dias: {alertas['vence_7_dias']['quantidade']} itens, Total: R$ {alertas['vence_7_dias']['valor_total']}")
        logger.info(f"  Futuros (>7 dias): {alertas['futuros']['quantidade']} itens, Total: R$ {alertas['futuros']['valor_total']}")
        logger.info(f"--- [LOG] Alertas de vencimento calculados para obra {obra_id} ---")
        return jsonify(alertas), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] GET alertas vencimento: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500

# --- ENDPOINT PARA GERAR RELATÓRIO DO CRONOGRAMA FINANCEIRO (PDF) ---

# -----------------------------------------------------------------------------
# DELETAR Pagamento Futuro (rota exata do frontend)
# DELETE /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/servico-{id}
# -----------------------------------------------------------------------------
@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/servico-<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_pagamento_futuro_servico(obra_id, pagamento_id):
    """Deleta pagamento futuro - rota exata do frontend"""
    try:
        if request.method == 'OPTIONS':
            return '', 200
        
        logger.info(f"[LOG] DELETE pagamento futuro: obra_id={obra_id}, pagamento_id={pagamento_id}")
        
        # Buscar pagamento usando servico_id como filtro adicional
        pagamento = PagamentoFuturo.query.filter_by(
            id=pagamento_id,
            obra_id=obra_id
        ).first()
        
        if not pagamento:
            logger.error(f"[ERRO] Pagamento {pagamento_id} não encontrado na obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Deletar
        db.session.delete(pagamento)
        db.session.commit()
        
        logger.info(f"[LOG] ✅ Pagamento futuro {pagamento_id} deletado com sucesso")
        return jsonify({"mensagem": "Pagamento deletado com sucesso", "id": pagamento_id}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] deletar_pagamento_futuro_servico: {str(e)}\n{error_details}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


# -----------------------------------------------------------------------------
# EDITAR Pagamento Futuro (rota exata do frontend)
# PUT /sid/cronograma-financeiro/{obra_id}/pagamentos-futuros/servico-{id}
# -----------------------------------------------------------------------------
@sid_bp.route('/cronograma-financeiro/<int:obra_id>/pagamentos-futuros/servico-<int:pagamento_id>', methods=['PUT', 'PATCH', 'OPTIONS'])
@jwt_required()
def editar_pagamento_futuro_servico(obra_id, pagamento_id):
    """Edita pagamento futuro - rota exata do frontend"""
    try:
        if request.method == 'OPTIONS':
            return '', 200
        
        logger.info(f"[LOG] PUT pagamento futuro: obra_id={obra_id}, pagamento_id={pagamento_id}")
        
        pagamento = PagamentoFuturo.query.filter_by(
            id=pagamento_id,
            obra_id=obra_id
        ).first()
        
        if not pagamento:
            logger.error(f"[ERRO] Pagamento {pagamento_id} não encontrado na obra {obra_id}")
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json()
        
        if 'descricao' in data:
            pagamento.descricao = data['descricao']
        if 'valor' in data:
            pagamento.valor = float(data['valor'])
        if 'data_vencimento' in data:
            pagamento.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        if 'fornecedor' in data:
            pagamento.fornecedor = data['fornecedor']
        if 'pix' in data:
            pagamento.pix = data['pix']
        if 'observacoes' in data:
            pagamento.observacoes = data['observacoes']
        
        db.session.commit()
        
        logger.info(f"[LOG] ✅ Pagamento futuro {pagamento_id} editado com sucesso")
        return jsonify({"mensagem": "Pagamento atualizado com sucesso", "id": pagamento_id}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] editar_pagamento_futuro_servico: {str(e)}\n{error_details}")
        return jsonify({"erro": "Erro interno no servidor"}), 500


# ==================== ENDPOINTS AGENDA DE DEMANDAS ====================


