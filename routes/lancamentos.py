import logging
import traceback
from datetime import date

from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import jwt_required, get_jwt
from sqlalchemy import func

from extensions import db
from models.lancamento import Lancamento
from models.pagamento_futuro import PagamentoFuturo
from models.nota_fiscal import NotaFiscal
from models.obra import Obra
from services import (
    get_current_user,
    user_has_access_to_obra,
    check_permission,
    notificar_masters,
)
from services.orcamento_service import resolver_orcamento_item_id

logger = logging.getLogger(__name__)

lancamentos_bp = Blueprint('lancamentos', __name__)


# --- Rotas de Lançamento (Geral) ---
@lancamentos_bp.route('/obras/<int:obra_id>/lancamentos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def add_lancamento(obra_id):
    """
    LÓGICA CORRIGIDA:
    - Se status == 'A Pagar' → Cria PagamentoFuturo (aparece no cronograma)
    - Se status == 'Pago' → Cria Lançamento (vai direto pro histórico)
    """
    logger.info("--- [LOG] Rota /obras/{obra_id}/lancamentos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.get_json()  # CORREÇÃO: Usar get_json() ao invés de request.json
        
        if not dados:
            return jsonify({"erro": "Dados inválidos ou ausentes"}), 400
        
        # Validar campos obrigatórios
        if 'valor' not in dados:
            return jsonify({"erro": "Campo 'valor' é obrigatório"}), 400
        if 'status' not in dados:
            return jsonify({"erro": "Campo 'status' é obrigatório"}), 400
        if 'descricao' not in dados:
            return jsonify({"erro": "Campo 'descricao' é obrigatório"}), 400
        
        valor_total = float(dados['valor'])
        status = dados['status']
        
        # PROCESSAR DATAS COM SEGURANÇA
        data_registro = None
        data_vencimento_obj = None
        
        try:
            # Tentar pegar data_vencimento primeiro
            if dados.get('data_vencimento'):
                data_vencimento_obj = date.fromisoformat(dados['data_vencimento'])
            
            # Se não tiver data_vencimento, tentar 'data'
            if not data_vencimento_obj and dados.get('data'):
                data_vencimento_obj = date.fromisoformat(dados['data'])
            
            # Se não tiver nenhuma, usar hoje
            if not data_vencimento_obj:
                data_vencimento_obj = date.today()
            
            # Para lançamentos, precisamos de data_registro
            if dados.get('data'):
                data_registro = date.fromisoformat(dados['data'])
            else:
                data_registro = date.today()
                
        except ValueError as e:
            return jsonify({"erro": f"Formato de data inválido: {str(e)}"}), 400
        
        logger.info(f"--- [LOG] Status='{status}', Valor={valor_total}, Data Vencimento={data_vencimento_obj} ---")

        # =====================================================================
        # ANTI-DUPLICAÇÃO (BUG #2): bloquear lançamento idêntico já existente
        # Critério: mesmo obra_id + descricao + valor_total + data
        # Não criamos UNIQUE constraint para preservar dados históricos.
        # =====================================================================
        descricao_norm = (dados.get('descricao') or '').strip()
        data_dup_check = data_registro

        lanc_duplicado = Lancamento.query.filter(
            Lancamento.obra_id == obra_id,
            Lancamento.valor_total == valor_total,
            Lancamento.data == data_dup_check,
            func.lower(func.trim(Lancamento.descricao)) == descricao_norm.lower()
        ).first()
        if lanc_duplicado:
            logger.warning(f"--- [LOG] ⚠️ Duplicidade detectada (Lancamento existente ID {lanc_duplicado.id}) — abortando criação ---")
            return jsonify({
                "erro": "Lançamento duplicado: já existe um lançamento com mesma descrição, valor e data nesta obra.",
                "lancamento_id_existente": lanc_duplicado.id
            }), 409

        # Para fluxo 'A Pagar', verificar também na tabela PagamentoFuturo
        if status == 'A Pagar':
            futuro_duplicado = PagamentoFuturo.query.filter(
                PagamentoFuturo.obra_id == obra_id,
                PagamentoFuturo.valor == valor_total,
                PagamentoFuturo.data_vencimento == data_vencimento_obj,
                func.lower(func.trim(PagamentoFuturo.descricao)) == descricao_norm.lower()
            ).first()
            if futuro_duplicado:
                logger.warning(f"--- [LOG] ⚠️ Duplicidade detectada (PagamentoFuturo existente ID {futuro_duplicado.id}) — abortando criação ---")
                return jsonify({
                    "erro": "Pagamento futuro duplicado: já existe um pagamento agendado com mesma descrição, valor e data de vencimento nesta obra.",
                    "pagamento_id_existente": futuro_duplicado.id
                }), 409

        # LÓGICA PRINCIPAL: Se é "A Pagar", cria PagamentoFuturo
        if status == 'A Pagar':
            logger.info(f"--- [LOG] Status='A Pagar' → Criando PagamentoFuturo ---")

            novo_pagamento_futuro = PagamentoFuturo(
                obra_id=obra_id,
                descricao=dados['descricao'],
                valor=valor_total,
                data_vencimento=data_vencimento_obj,
                fornecedor=dados.get('fornecedor'),
                pix=dados.get('pix'),
                observacoes=None,
                status='Previsto'
            )
            db.session.add(novo_pagamento_futuro)
            db.session.commit()
            
            # --- NOTIFICAÇÃO PARA MASTERS ---
            obra = Obra.query.get(obra_id)
            obra_nome = obra.nome if obra else f"Obra {obra_id}"
            notificar_masters(
                tipo='pagamento_inserido',
                titulo='Novo pagamento agendado',
                mensagem=f'{user.username} agendou pagamento "{dados["descricao"]}" de R$ {valor_total:.2f} na obra {obra_nome}',
                obra_id=obra_id,
                item_id=novo_pagamento_futuro.id,
                item_type='pagamento_futuro',
                usuario_origem_id=user.id
            )
            
            logger.info(f"--- [LOG] ✅ PagamentoFuturo criado: ID {novo_pagamento_futuro.id} ---")
            return jsonify(novo_pagamento_futuro.to_dict()), 201
        
        # Se status == 'Pago', cria Lançamento normalmente
        else:
            logger.info(f"--- [LOG] Status='Pago' → Criando Lançamento ---")
            
            # Se é gasto avulso do histórico, força status="Pago"
            is_gasto_avulso_historico = dados.get('is_gasto_avulso_historico', False)
            if is_gasto_avulso_historico:
                status = 'Pago'
            
            valor_pago = valor_total if status == 'Pago' else 0.0
            
            novo_lancamento = Lancamento(
                obra_id=obra_id, 
                tipo=dados.get('tipo', 'Saída'), 
                descricao=dados['descricao'],
                valor_total=valor_total,
                valor_pago=valor_pago,
                data=data_registro,
                data_vencimento=data_vencimento_obj if dados.get('data_vencimento') else None,
                status=status, 
                pix=dados.get('pix'),
                prioridade=int(dados.get('prioridade', 0)),
                fornecedor=dados.get('fornecedor'), 
                servico_id=dados.get('servico_id')
            )
            
            db.session.add(novo_lancamento)
            db.session.flush()  # Para obter o ID

            # Vínculo com item do orçamento — via ORM, com validação explícita.
            oid, erro = resolver_orcamento_item_id(dados.get('orcamento_item_id'))
            if erro:
                db.session.rollback()
                logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (novo lancamento): {erro} ---")
                return jsonify({"erro": erro}), 400
            novo_lancamento.orcamento_item_id = oid

            db.session.commit()
            
            # --- NOTIFICAÇÃO PARA MASTERS ---
            obra = Obra.query.get(obra_id)
            obra_nome = obra.nome if obra else f"Obra {obra_id}"
            notificar_masters(
                tipo='pagamento_inserido',
                titulo='Novo pagamento registrado',
                mensagem=f'{user.username} registrou pagamento "{dados["descricao"]}" de R$ {valor_total:.2f} na obra {obra_nome}',
                obra_id=obra_id,
                item_id=novo_lancamento.id,
                item_type='lancamento',
                usuario_origem_id=user.id
            )
            
            logger.info(f"--- [LOG] ✅ Lançamento criado: ID {novo_lancamento.id} ---")
            return jsonify(novo_lancamento.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/lancamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@lancamentos_bp.route('/lancamentos/<int:lancamento_id>/pago', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def marcar_como_pago(lancamento_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    logger.info(f"--- [LOG] Rota /lancamentos/{lancamento_id}/pago (PATCH) acessada ---")
    try:
        user = get_current_user()
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if lancamento.status == 'Pago':
            lancamento.status = 'A Pagar'
            lancamento.valor_pago = 0.0
        else:
            lancamento.status = 'Pago'
            lancamento.valor_pago = lancamento.valor_total
        
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /lancamentos/{lancamento_id}/pago (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@lancamentos_bp.route('/lancamentos/<int:lancamento_id>', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def editar_lancamento(lancamento_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    logger.info(f"--- [LOG] Rota /lancamentos/{lancamento_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        dados = request.json
        lancamento.data = date.fromisoformat(dados['data'])
        lancamento.data_vencimento = date.fromisoformat(dados['data_vencimento']) if dados.get('data_vencimento') else None
        lancamento.descricao = dados['descricao']
        lancamento.valor_total = float(dados['valor_total']) 
        lancamento.valor_pago = float(dados.get('valor_pago', lancamento.valor_pago)) 
        lancamento.tipo = dados['tipo']
        lancamento.status = dados['status']
        lancamento.pix = dados.get('pix')
        lancamento.prioridade = int(dados.get('prioridade', lancamento.prioridade))
        lancamento.fornecedor = dados.get('fornecedor', lancamento.fornecedor) 
        lancamento.servico_id = dados.get('servico_id')
        
        # Vínculo com item do orçamento — via ORM, com validação explícita.
        if 'orcamento_item_id' in dados:
            oid, erro = resolver_orcamento_item_id(dados.get('orcamento_item_id'))
            if erro:
                db.session.rollback()
                logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (lancamento {lancamento_id}): {erro} ---")
                return jsonify({"erro": erro}), 400
            lancamento.orcamento_item_id = oid
        
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /lancamentos/{lancamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@lancamentos_bp.route('/lancamentos/<int:lancamento_id>', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def atualizar_lancamento_parcial(lancamento_id):
    """Atualização parcial de lançamento (ex: vincular serviço)"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        user = get_current_user()
        claims = get_jwt()
        user_role = claims.get('role')
        
        if user_role not in ['administrador', 'master']:
            return jsonify({"erro": "Acesso negado"}), 403
        
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        if not user_has_access_to_obra(user, lancamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        dados = request.json
        
        # Atualizar apenas os campos fornecidos
        if 'servico_id' in dados:
            lancamento.servico_id = dados['servico_id'] if dados['servico_id'] else None
        if 'fornecedor' in dados:
            lancamento.fornecedor = dados['fornecedor']
        if 'prioridade' in dados:
            lancamento.prioridade = int(dados['prioridade'])
        if 'tipo' in dados:
            lancamento.tipo = dados['tipo']  # 'Mão de Obra' ou 'Material'
        
        # Vínculo com item do orçamento — via ORM, com validação explícita.
        if 'orcamento_item_id' in dados:
            oid, erro = resolver_orcamento_item_id(dados.get('orcamento_item_id'))
            if erro:
                db.session.rollback()
                logger.warning(f"--- [VINCULO] orcamento_item_id rejeitado (lancamento {lancamento_id}): {erro} ---")
                return jsonify({"erro": erro}), 400
            lancamento.orcamento_item_id = oid
        
        db.session.commit()
        logger.info(f"--- [LOG] Lançamento {lancamento_id} atualizado parcialmente ---")
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /lancamentos/{lancamento_id} (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@lancamentos_bp.route('/lancamentos/<int:lancamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_lancamento(lancamento_id):
    """
    Deleta um lançamento com regras específicas:
    - Lançamentos PAGOS só podem ser deletados por usuários MASTER
    - Lançamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
    - Remove também notas fiscais associadas ao lançamento
    """
    logger.info(f"--- [LOG] Rota /lancamentos/{lancamento_id} (DELETE) acessada ---")
    
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        # Buscar o lançamento
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        
        # Obter o papel do usuário
        claims = get_jwt()
        user_role = claims.get('role')
        
        # Verificar se o lançamento está PAGO (executado)
        is_pago = lancamento.status == 'Pago'
        
        # REGRA: Se está PAGO, ADMINISTRADOR ou MASTER podem deletar
        if is_pago and user_role not in ['administrador', 'master']:
            logger.error(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Apenas administradores e masters podem excluir pagamentos já executados (PAGOS)."
            }), 403
        
        # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
        if not is_pago and user_role not in ['administrador', 'master']:
            logger.error(f"--- [LOG] ❌ Tentativa de deletar lançamento por usuário {user_role} (sem permissão) ---")
            return jsonify({
                "erro": "Acesso negado: Permissão insuficiente para excluir este lançamento."
            }), 403
        
        # 1. Remover notas fiscais associadas a este lançamento
        notas_removidas = NotaFiscal.query.filter_by(
            item_id=lancamento_id,
            item_type='lancamento'
        ).delete()
        if notas_removidas > 0:
            logger.info(f"--- [LOG] {notas_removidas} nota(s) fiscal(is) removida(s) do lançamento {lancamento_id} ---")
        
        # 2. Deletar o lançamento
        db.session.delete(lancamento)
        db.session.commit()
        
        logger.info(f"--- [LOG] ✅ Lançamento {lancamento_id} (Status: {lancamento.status}) e dados associados deletados com sucesso pelo usuário {user_role} ---")
        return jsonify({"sucesso": "Lançamento e dados associados deletados"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /lancamentos/{lancamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500



# --- NOVO ENDPOINT: LISTAR LANÇAMENTOS COM SALDO PENDENTE ---
@lancamentos_bp.route('/obras/<int:obra_id>/lancamentos-pendentes', methods=['GET'])
@jwt_required()
def listar_lancamentos_pendentes(obra_id):
    """
    Lista todos os lançamentos com saldo pendente (valor_total > valor_pago).
    Esses são os lançamentos "fantasmas" que contribuem para o KPI "Liberado p/ Pagamento"
    mas não aparecem mais no quadro de pendências (que foi removido).
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar lançamentos com saldo pendente
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).filter(
            Lancamento.valor_total > Lancamento.valor_pago
        ).order_by(Lancamento.data).all()
        
        resultado = []
        for lanc in lancamentos:
            valor_restante = lanc.valor_total - lanc.valor_pago
            resultado.append({
                'id': lanc.id,
                'tipo': lanc.tipo,
                'descricao': lanc.descricao,
                'fornecedor': lanc.fornecedor,
                'valor_total': lanc.valor_total,
                'valor_pago': lanc.valor_pago,
                'valor_restante': valor_restante,
                'data': lanc.data.isoformat() if lanc.data else None,
                'data_vencimento': lanc.data_vencimento.isoformat() if lanc.data_vencimento else None,
                'status': lanc.status,
                'prioridade': lanc.prioridade,
                'pix': lanc.pix,
                'servico_id': lanc.servico_id,
                'servico_nome': lanc.servico.nome if lanc.servico else None
            })
        
        total_pendente = sum(lanc.valor_total - lanc.valor_pago for lanc in lancamentos)
        
        logger.info(f"--- [LOG] Encontrados {len(resultado)} lançamentos pendentes na obra {obra_id}. Total: R$ {total_pendente:.2f} ---")
        
        return jsonify({
            'lancamentos': resultado,
            'total_lancamentos': len(resultado),
            'total_pendente': round(total_pendente, 2)
        }), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /lancamentos-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: EXCLUIR LANÇAMENTO PENDENTE ---
@lancamentos_bp.route('/obras/<int:obra_id>/lancamentos/<int:lancamento_id>/excluir-pendente', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_lancamento_pendente(obra_id, lancamento_id):
    """
    Exclui um lançamento com saldo pendente.
    Remove completamente do banco de dados.
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar o lançamento
        lancamento = Lancamento.query.filter_by(id=lancamento_id, obra_id=obra_id).first()
        if not lancamento:
            return jsonify({"erro": "Lançamento não encontrado"}), 404
        
        # Guardar info antes de excluir
        descricao = lancamento.descricao
        valor_restante = lancamento.valor_total - lancamento.valor_pago
        
        # Excluir o lançamento
        db.session.delete(lancamento)
        db.session.commit()
        
        logger.info(f"--- [LOG] Lançamento {lancamento_id} excluído. Valor restante era: R$ {valor_restante:.2f} ---")
        
        return jsonify({
            "mensagem": "Lançamento excluído com sucesso",
            "lancamento_id": lancamento_id,
            "descricao": descricao,
            "valor_que_estava_pendente": valor_restante
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /excluir-pendente: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: EXCLUIR TODOS OS LANÇAMENTOS PENDENTES ---
@lancamentos_bp.route('/obras/<int:obra_id>/lancamentos/excluir-todos-pendentes', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_todos_lancamentos_pendentes(obra_id):
    """
    Exclui TODOS os lançamentos pendentes de uma obra de uma vez.
    Remove completamente do banco de dados - limpa os valores "fantasmas".
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar todos os lançamentos com saldo pendente
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).filter(
            Lancamento.valor_total > Lancamento.valor_pago
        ).all()
        
        if not lancamentos:
            return jsonify({"mensagem": "Nenhum lançamento pendente encontrado"}), 200
        
        excluidos = []
        valor_total_removido = 0
        
        for lancamento in lancamentos:
            valor_restante = lancamento.valor_total - lancamento.valor_pago
            
            excluidos.append({
                'lancamento_id': lancamento.id,
                'descricao': lancamento.descricao,
                'valor_pendente_removido': valor_restante
            })
            valor_total_removido += valor_restante
            
            # Excluir do banco
            db.session.delete(lancamento)
        
        db.session.commit()
        
        logger.info(f"--- [LOG] {len(excluidos)} lançamentos pendentes excluídos. Total removido: R$ {valor_total_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"{len(excluidos)} lançamentos pendentes excluídos com sucesso",
            "quantidade_excluida": len(excluidos),
            "valor_total_removido": round(valor_total_removido, 2),
            "lancamentos_excluidos": excluidos
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /excluir-todos-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT GLOBAL: EXCLUIR PENDENTES DE TODAS AS OBRAS ---
@lancamentos_bp.route('/lancamentos/excluir-todos-pendentes-global', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_todos_lancamentos_pendentes_global():
    """
    Exclui TODOS os lançamentos pendentes de TODAS as obras acessíveis pelo usuário.
    
    Administrador: Limpa todas as obras do sistema
    Master: Limpa apenas as obras que tem acesso
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        
        # Determinar quais obras o usuário pode acessar
        if current_user.role == 'administrador':
            obras = Obra.query.all()
        else:
            obras = current_user.obras_permitidas
        
        if not obras:
            return jsonify({"mensagem": "Nenhuma obra acessível encontrada"}), 200
        
        resultado_por_obra = []
        total_geral_excluido = 0
        total_geral_removido = 0.0
        
        for obra in obras:
            # Buscar lançamentos pendentes desta obra
            lancamentos = Lancamento.query.filter_by(obra_id=obra.id).filter(
                Lancamento.valor_total > Lancamento.valor_pago
            ).all()
            
            if lancamentos:
                excluidos = []
                valor_total_obra = 0
                
                for lancamento in lancamentos:
                    valor_restante = lancamento.valor_total - lancamento.valor_pago
                    
                    excluidos.append({
                        'lancamento_id': lancamento.id,
                        'descricao': lancamento.descricao,
                        'valor_pendente': valor_restante
                    })
                    valor_total_obra += valor_restante
                    
                    # Excluir do banco
                    db.session.delete(lancamento)
                
                total_geral_excluido += len(excluidos)
                total_geral_removido += valor_total_obra
                
                resultado_por_obra.append({
                    'obra_id': obra.id,
                    'obra_nome': obra.nome,
                    'quantidade_excluida': len(excluidos),
                    'valor_removido': round(valor_total_obra, 2),
                    'lancamentos': excluidos
                })
        
        db.session.commit()
        
        logger.info(f"--- [LOG] LIMPEZA GLOBAL: {total_geral_excluido} lançamentos excluídos em {len(resultado_por_obra)} obras. Total: R$ {total_geral_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"Limpeza concluída! {total_geral_excluido} lançamentos excluídos em {len(resultado_por_obra)} obras",
            "total_obras_processadas": len(obras),
            "obras_com_pendencias": len(resultado_por_obra),
            "total_lancamentos_excluidos": total_geral_excluido,
            "valor_total_removido": round(total_geral_removido, 2),
            "detalhes_por_obra": resultado_por_obra
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /excluir-todos-pendentes-global: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---
