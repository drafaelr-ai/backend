import io
import re
import json
import csv
import zipfile
import logging
import traceback
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta

from flask import Blueprint, jsonify, request, make_response, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import func, case
from sqlalchemy.orm import joinedload

from extensions import db
from models.obra import Obra
from models.user import user_obra_association
from models.servico import Servico
from models.servico_usuario import ServicoUsuario
from models.servico_base import ServicoBase
from models.pagamento_servico import PagamentoServico
from models.pagamento_futuro import PagamentoFuturo
from models.lancamento import Lancamento
from models.nota_fiscal import NotaFiscal
from models.orcamento import Orcamento
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from models.boleto import Boleto
from models.caixa_obra import CaixaObra
from models.parcela_individual import ParcelaIndividual
from models.pagamento_parcelado import PagamentoParcelado
from models.anexo_orcamento import AnexoOrcamento
from services import (
    get_current_user,
    user_has_access_to_obra,
    check_permission,
    notificar_masters,
    notificar_operadores_obra,
    notificar_administradores,
    criar_notificacao,
)
from utils import formatar_real

logger = logging.getLogger(__name__)
obras_bp = Blueprint('obras', __name__)


# --- ROTAS DA API ---

# --- ROTA /obras (Tela inicial) ---
@obras_bp.route('/obras', methods=['GET', 'OPTIONS'])
@jwt_required() 
def get_obras():
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    logger.info("--- [LOG] Rota /obras (GET) acessada (4 KPIs Completos) ---")
    try:
        user = get_current_user() 
        if not user: return jsonify({"erro": "Usuário não encontrado"}), 404

        # 1. Lançamentos (Custo total e Custo pago)
        lancamentos_sum = db.session.query(
            Lancamento.obra_id,
            func.sum(Lancamento.valor_total).label('total_geral_lanc'),
            func.sum(Lancamento.valor_pago).label('total_pago_lanc'),
            func.sum(
                case(
                    (Lancamento.valor_total > Lancamento.valor_pago, 
                     Lancamento.valor_total - Lancamento.valor_pago),
                    else_=0
                )
            ).label('total_pendente_lanc')
        ).group_by(Lancamento.obra_id).subquery()

        # 2. Orçamento de Mão de Obra E Material (Custo total)
        servico_budget_sum = db.session.query(
            Servico.obra_id,
            func.sum(Servico.valor_global_mao_de_obra).label('total_budget_mo'),
            func.sum(Servico.valor_global_material).label('total_budget_mat')
        ).group_by(Servico.obra_id).subquery()

        # 3. Pagamentos de Serviço (Custo pago e pendente)
        pagamentos_sum = db.session.query(
            Servico.obra_id,
            func.sum(PagamentoServico.valor_pago).label('total_pago_pag'),
            func.sum(
                case(
                    (PagamentoServico.valor_total > PagamentoServico.valor_pago,
                     PagamentoServico.valor_total - PagamentoServico.valor_pago),
                    else_=0
                )
            ).label('total_pendente_pag')
        ).select_from(PagamentoServico) \
         .join(Servico, PagamentoServico.servico_id == Servico.id) \
         .group_by(Servico.obra_id) \
         .subquery()
        
        # CORREÇÃO: 4. Pagamentos Futuros (Cronograma Financeiro) - TODOS com status Previsto OU Pendente
        pagamentos_futuros_sum = db.session.query(
            PagamentoFuturo.obra_id,
            func.sum(PagamentoFuturo.valor).label('total_futuro')
        ).filter(
            PagamentoFuturo.status.in_(['Previsto', 'Pendente'])
        ).group_by(PagamentoFuturo.obra_id).subquery()
        
        # NOVO: 4b. Pagamentos Futuros SEM serviço (Despesas Extras)
        pagamentos_futuros_extra_sum = db.session.query(
            PagamentoFuturo.obra_id,
            func.sum(PagamentoFuturo.valor).label('total_futuro_extra')
        ).filter(
            PagamentoFuturo.status.in_(['Previsto', 'Pendente']),
            PagamentoFuturo.servico_id.is_(None)
        ).group_by(PagamentoFuturo.obra_id).subquery()
        
        # CORREÇÃO: 5. Parcelas Previstas (Cronograma Financeiro) - TODAS
        parcelas_previstas_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(ParcelaIndividual.status == 'Previsto') \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 5b. Parcelas SEM serviço (Despesas Extras)
        parcelas_extra_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_extra')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(
             ParcelaIndividual.status == 'Previsto',
             PagamentoParcelado.servico_id.is_(None)
         ) \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 5c. Parcelas PAGAS com serviço (para somar em valores pagos)
        parcelas_pagas_com_servico_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_pagas')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(
             ParcelaIndividual.status == 'Pago',
             PagamentoParcelado.servico_id.isnot(None)  # COM serviço
         ) \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 5d. Parcelas PAGAS SEM serviço (despesas extras pagas)
        parcelas_pagas_sem_servico_sum = db.session.query(
            PagamentoParcelado.obra_id,
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_pagas_sem')
        ).select_from(ParcelaIndividual) \
         .join(PagamentoParcelado, ParcelaIndividual.pagamento_parcelado_id == PagamentoParcelado.id) \
         .filter(
             ParcelaIndividual.status == 'Pago',
             PagamentoParcelado.servico_id.is_(None)  # SEM serviço
         ) \
         .group_by(PagamentoParcelado.obra_id) \
         .subquery()
        
        # NOVO: 6a. Orçamento de Engenharia TOTAL por obra
        orcamento_eng_sum = db.session.query(
            OrcamentoEngEtapa.obra_id,
            func.sum(
                db.case(
                    (OrcamentoEngItem.tipo_composicao == 'separado',
                     OrcamentoEngItem.quantidade * (
                         func.coalesce(OrcamentoEngItem.preco_mao_obra, 0) +
                         func.coalesce(OrcamentoEngItem.preco_material, 0)
                     )),
                    else_=OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_unitario, 0)
                )
            ).label('total_orcamento_eng')
        ).select_from(OrcamentoEngItem) \
         .join(OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id) \
         .group_by(OrcamentoEngEtapa.obra_id) \
         .subquery()
        
        # NOVO: 6b. Valores de Serviços vinculados ao Orçamento de Engenharia (para evitar duplicação)
        servicos_orcamento_sum = db.session.query(
            OrcamentoEngEtapa.obra_id,
            func.sum(Servico.valor_global_mao_de_obra + Servico.valor_global_material).label('total_servicos_orcamento')
        ).select_from(OrcamentoEngItem) \
         .join(OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id) \
         .join(Servico, OrcamentoEngItem.servico_id == Servico.id) \
         .group_by(OrcamentoEngEtapa.obra_id) \
         .subquery()

        # 6. Query Principal
        obras_query = db.session.query(
            Obra,
            func.coalesce(lancamentos_sum.c.total_geral_lanc, 0).label('lanc_geral'),
            func.coalesce(lancamentos_sum.c.total_pago_lanc, 0).label('lanc_pago'),
            func.coalesce(lancamentos_sum.c.total_pendente_lanc, 0).label('lanc_pendente'),
            func.coalesce(servico_budget_sum.c.total_budget_mo, 0).label('serv_budget_mo'),
            func.coalesce(servico_budget_sum.c.total_budget_mat, 0).label('serv_budget_mat'),
            func.coalesce(pagamentos_sum.c.total_pago_pag, 0).label('pag_pago'),
            func.coalesce(pagamentos_sum.c.total_pendente_pag, 0).label('pag_pendente'),
            func.coalesce(pagamentos_futuros_sum.c.total_futuro, 0).label('futuro_previsto'),
            func.coalesce(parcelas_previstas_sum.c.total_parcelas, 0).label('parcelas_previstas'),
            func.coalesce(pagamentos_futuros_extra_sum.c.total_futuro_extra, 0).label('futuro_extra'),
            func.coalesce(parcelas_extra_sum.c.total_parcelas_extra, 0).label('parcelas_extra'),
            func.coalesce(parcelas_pagas_com_servico_sum.c.total_parcelas_pagas, 0).label('parcelas_pagas_com_servico'),
            func.coalesce(parcelas_pagas_sem_servico_sum.c.total_parcelas_pagas_sem, 0).label('parcelas_pagas_sem_servico'),
            func.coalesce(orcamento_eng_sum.c.total_orcamento_eng, 0).label('orcamento_eng'),
            func.coalesce(servicos_orcamento_sum.c.total_servicos_orcamento, 0).label('servicos_orcamento')
        ).outerjoin(
            lancamentos_sum, Obra.id == lancamentos_sum.c.obra_id
        ).outerjoin(
            servico_budget_sum, Obra.id == servico_budget_sum.c.obra_id
        ).outerjoin(
            pagamentos_sum, Obra.id == pagamentos_sum.c.obra_id
        ).outerjoin(
            pagamentos_futuros_sum, Obra.id == pagamentos_futuros_sum.c.obra_id
        ).outerjoin(
            parcelas_previstas_sum, Obra.id == parcelas_previstas_sum.c.obra_id
        ).outerjoin(
            pagamentos_futuros_extra_sum, Obra.id == pagamentos_futuros_extra_sum.c.obra_id
        ).outerjoin(
            parcelas_extra_sum, Obra.id == parcelas_extra_sum.c.obra_id
        ).outerjoin(
            parcelas_pagas_com_servico_sum, Obra.id == parcelas_pagas_com_servico_sum.c.obra_id
        ).outerjoin(
            parcelas_pagas_sem_servico_sum, Obra.id == parcelas_pagas_sem_servico_sum.c.obra_id
        ).outerjoin(
            orcamento_eng_sum, Obra.id == orcamento_eng_sum.c.obra_id
        ).outerjoin(
            servicos_orcamento_sum, Obra.id == servicos_orcamento_sum.c.obra_id
        )

        # 7. Filtra permissões E status de conclusão/arquivamento
        mostrar_concluidas = request.args.get('mostrar_concluidas', 'false').lower() == 'true'
        incluir_arquivadas = request.args.get('incluir_arquivadas', 'false').lower() == 'true'

        if not incluir_arquivadas:
            obras_query = obras_query.filter(
                db.or_(Obra.arquivada == False, Obra.arquivada.is_(None))
            )

        if user.role == 'administrador':
            if mostrar_concluidas:
                obras_com_totais = obras_query.order_by(Obra.nome).all()
            else:
                obras_com_totais = obras_query.filter(
                    db.or_(Obra.concluida == False, Obra.concluida.is_(None))
                ).order_by(Obra.nome).all()
        else:
            base_query = obras_query.join(
                user_obra_association, Obra.id == user_obra_association.c.obra_id
            ).filter(
                user_obra_association.c.user_id == user.id
            )
            if mostrar_concluidas:
                obras_com_totais = base_query.order_by(Obra.nome).all()
            else:
                obras_com_totais = base_query.filter(
                    db.or_(Obra.concluida == False, Obra.concluida.is_(None))
                ).order_by(Obra.nome).all()

        # 8. Formata a Saída com os 4 KPIs
        resultados = []
        for obra, lanc_geral, lanc_pago, lanc_pendente, serv_budget_mo, serv_budget_mat, pag_pago, pag_pendente, futuro_previsto, parcelas_previstas, futuro_extra, parcelas_extra, parcelas_pagas_com_servico, parcelas_pagas_sem_servico, orcamento_eng, servicos_orcamento in obras_com_totais:
            
            # Calcular valores COM serviço
            futuro_com_servico = float(futuro_previsto) - float(futuro_extra)
            parcelas_com_servico = float(parcelas_previstas) - float(parcelas_extra)
            
            # KPI 1: Orçamento Total
            # = Serviços do Kanban (não vinculados ao orçamento) + Orçamento de Engenharia completo
            # Lógica: Subtrair do Kanban os serviços que vieram do orçamento para não duplicar
            total_servicos = float(serv_budget_mo) + float(serv_budget_mat)
            total_servicos_ajustado = max(0, total_servicos - float(servicos_orcamento))
            orcamento_total = total_servicos_ajustado + float(orcamento_eng)
            
            # KPI 2: Total Pago (Valores Efetivados)
            # Inclui: lançamentos + pagamentos de serviço + parcelas pagas COM serviço
            # NOTA: Parcelas pagas SEM serviço já estão em lanc_pago (Lancamento criado ao pagar)
            total_pago = float(lanc_pago) + float(pag_pago) + float(parcelas_pagas_com_servico)
            
            # KPI 3: Liberado para Pagamento (Fila) - Incluindo Cronograma Financeiro
            liberado_pagamento = (
                float(lanc_pendente) + 
                float(pag_pendente) + 
                float(futuro_previsto) + 
                float(parcelas_previstas)
            )
            
            # KPI 4: Despesas Extras (Pagamentos Fora da Planilha)
            despesas_extras = float(futuro_extra) + float(parcelas_extra)
            
            resultados.append({
                "id": obra.id,
                "nome": obra.nome,
                "cliente": obra.cliente,
                "concluida": obra.concluida or False,
                "arquivada": obra.arquivada or False,
                "orcamento_total": orcamento_total,
                "total_pago": total_pago,
                "liberado_pagamento": liberado_pagamento,
                "despesas_extras": despesas_extras
            })
        
        return jsonify(resultados)

    except Exception as e:
        logger.exception("--- [ERRO] /obras (GET): falha ao listar obras ---")
        return jsonify({"erro": "Erro ao listar obras", "detalhe": str(e)}), 500
# --- FIM DA ROTA ---


@obras_bp.route('/obras', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def add_obra():
    """Cria uma nova obra e associa automaticamente o usuário criador"""
    logger.info("--- [LOG] Rota /obras (POST) acessada ---")
    try:
        # Obter usuário atual
        current_user = get_current_user()
        if not current_user:
            return jsonify({"erro": "Usuário não encontrado"}), 404
        
        dados = request.json
        nova_obra = Obra(nome=dados['nome'], cliente=dados.get('cliente'))
        db.session.add(nova_obra)
        db.session.flush()  # Gera o ID da obra sem fazer commit final
        
        # CORREÇÃO: Associar automaticamente o usuário criador à obra
        if nova_obra not in current_user.obras_permitidas:
            current_user.obras_permitidas.append(nova_obra)
        
        db.session.commit()
        
        logger.info(f"--- [LOG] Obra '{nova_obra.nome}' (ID={nova_obra.id}) criada e associada ao usuário {current_user.username} ---")
        return jsonify(nova_obra.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# --- ROTA /obras/<id> (Dashboard Interno) ---
@obras_bp.route('/obras/<int:obra_id>', methods=['GET', 'OPTIONS'])
@jwt_required() 
def get_obra_detalhes(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    logger.info(f"--- [LOG] Rota /obras/{obra_id} (GET) acessada (Novos KPIs v3) ---")
    
    try:
        from sqlalchemy.orm import joinedload
        user = get_current_user()
        if not user: return jsonify({"erro": "Usuário não encontrado"}), 404
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        obra = Obra.query.get_or_404(obra_id)
        
        # --- Lógica de KPIs (ATUALIZADA - Corrigida) ---
        
        # Orçamentos de Serviços (MO + Material)
        servico_budget_sum = db.session.query(
            func.sum(Servico.valor_global_mao_de_obra).label('total_budget_mo'),
            func.sum(Servico.valor_global_material).label('total_budget_mat')
        ).filter(Servico.obra_id == obra_id).first()
        
        total_budget_mo = float(servico_budget_sum.total_budget_mo or 0.0)
        total_budget_mat = float(servico_budget_sum.total_budget_mat or 0.0)
        
        # Total de Lançamentos SEM serviço vinculado (para evitar duplicação com orçamento de serviços)
        # Lançamentos COM serviço_id já estão contabilizados no orçamento do serviço (MO + Material)
        total_lancamentos_query = db.session.query(
            func.sum(Lancamento.valor_total).label('total_lanc')
        ).filter(
            Lancamento.obra_id == obra_id,
            Lancamento.servico_id.is_(None)  # CORREÇÃO: Apenas lançamentos SEM serviço
        ).first()
        total_lancamentos = float(total_lancamentos_query.total_lanc or 0.0)
        
        # Valor pago dos lançamentos (soma de valor_pago)
        lancamentos_valor_pago = db.session.query(
            func.sum(Lancamento.valor_pago).label('valor_pago_lanc')
        ).filter(Lancamento.obra_id == obra_id).first()
        total_pago_lancamentos = float(lancamentos_valor_pago.valor_pago_lanc or 0.0)
        
        # Valor pago dos pagamentos de serviço (soma de valor_pago)
        pagamentos_servico_valor_pago = db.session.query(
            func.sum(PagamentoServico.valor_pago).label('valor_pago_serv')
        ).join(Servico).filter(
            Servico.obra_id == obra_id
        ).first()
        total_pago_servicos = float(pagamentos_servico_valor_pago.valor_pago_serv or 0.0)
        
        # CORREÇÃO: Calcular totais de Pagamentos Futuros e Parcelas ANTES do KPI
        # Pagamentos Futuros com status='Previsto' OU 'Pendente' (TODOS)
        pagamentos_futuros_previstos = db.session.query(
            func.sum(PagamentoFuturo.valor).label('total_futuro')
        ).filter(
            PagamentoFuturo.obra_id == obra_id,
            PagamentoFuturo.status.in_(['Previsto', 'Pendente'])
        ).first()
        
        # Pagamentos Futuros SEM serviço (Despesas Extras)
        pagamentos_futuros_sem_servico = db.session.query(
            func.sum(PagamentoFuturo.valor).label('total_futuro_extra')
        ).filter(
            PagamentoFuturo.obra_id == obra_id,
            PagamentoFuturo.status.in_(['Previsto', 'Pendente']),
            PagamentoFuturo.servico_id.is_(None)
        ).first()
        
        # Parcelas Individuais com status='Previsto' (TODAS)
        parcelas_previstas = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas')
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).first()
        
        # Parcelas SEM serviço (Despesas Extras)
        parcelas_sem_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_extra')
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto',
            PagamentoParcelado.servico_id.is_(None)
        ).first()
        
        total_futuros = float(pagamentos_futuros_previstos.total_futuro or 0.0)
        total_parcelas_previstas = float(parcelas_previstas.total_parcelas or 0.0)
        total_futuros_extra = float(pagamentos_futuros_sem_servico.total_futuro_extra or 0.0)
        total_parcelas_extra = float(parcelas_sem_servico.total_parcelas_extra or 0.0)
        
        # Calcular valores COM serviço (para somar ao orçamento)
        total_futuros_com_servico = total_futuros - total_futuros_extra
        total_parcelas_com_servico = total_parcelas_previstas - total_parcelas_extra
        
        # Logs de DEBUG para rastreamento
        logger.debug(f"--- [DEBUG KPI] obra_id={obra_id} ---")
        logger.debug(f"--- [DEBUG KPI] total_lancamentos: R$ {total_lancamentos:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_budget_mo: R$ {total_budget_mo:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_budget_mat: R$ {total_budget_mat:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_futuros (PagamentoFuturo): R$ {total_futuros:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_parcelas_previstas: R$ {total_parcelas_previstas:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_futuros_com_servico: R$ {total_futuros_com_servico:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_parcelas_com_servico: R$ {total_parcelas_com_servico:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_futuros_extra (sem serviço): R$ {total_futuros_extra:.2f} ---")
        logger.debug(f"--- [DEBUG KPI] total_parcelas_extra (sem serviço): R$ {total_parcelas_extra:.2f} ---")
        
        # CORREÇÃO: Buscar parcelas PAGAS com serviço vinculado ANTES dos KPIs
        # Parcelas sem serviço NÃO devem ser somadas aqui pois já são contabilizadas via Lancamento criado
        parcelas_pagas_com_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela).label('total_parcelas_pagas')
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Pago',
            PagamentoParcelado.servico_id.isnot(None)  # COM serviço
        ).first()
        total_parcelas_pagas_com_servico = float(parcelas_pagas_com_servico.total_parcelas_pagas or 0.0)
        logger.debug(f"--- [DEBUG KPI] total_parcelas_pagas_com_servico: R$ {total_parcelas_pagas_com_servico:.2f} ---")
        
        # NOTA: Parcelas PAGAS SEM serviço NÃO são mais contadas aqui
        # Elas já são contabilizadas via Lancamento criado em marcar_parcela_paga()
        # Isso evita DUPLICAÇÃO
        logger.debug(f"--- [DEBUG KPI] parcelas_pagas_sem_servico: NÃO SOMADO (já está no Lancamento) ---")
        
        # === ORÇAMENTO DE ENGENHARIA ===
        # CORREÇÃO: Sempre usar os valores do Orçamento de Engenharia como fonte primária
        # Os serviços do Kanban vinculados são apenas para controle de pagamentos
        try:
            # Total COMPLETO do Orçamento de Engenharia (todos os itens)
            orcamento_eng_total = db.session.query(
                func.sum(
                    db.case(
                        (OrcamentoEngItem.tipo_composicao == 'separado',
                         OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_mao_obra, 0)),
                        else_=OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_unitario, 0) * 
                              func.coalesce(OrcamentoEngItem.rateio_mo, 50) / 100
                    )
                ).label('total_mo'),
                func.sum(
                    db.case(
                        (OrcamentoEngItem.tipo_composicao == 'separado',
                         OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_material, 0)),
                        else_=OrcamentoEngItem.quantidade * func.coalesce(OrcamentoEngItem.preco_unitario, 0) * 
                              func.coalesce(OrcamentoEngItem.rateio_mat, 50) / 100
                    )
                ).label('total_mat')
            ).join(OrcamentoEngEtapa).filter(
                OrcamentoEngEtapa.obra_id == obra_id
            ).first()
            
            total_orcamento_eng_mo = float(orcamento_eng_total.total_mo or 0.0)
            total_orcamento_eng_mat = float(orcamento_eng_total.total_mat or 0.0)
            total_orcamento_eng = total_orcamento_eng_mo + total_orcamento_eng_mat
            logger.debug(f"--- [DEBUG KPI] ORÇAMENTO ENG TOTAL: MO R$ {total_orcamento_eng_mo:.2f}, MAT R$ {total_orcamento_eng_mat:.2f} = R$ {total_orcamento_eng:.2f} ---")
            
            # Verificar serviços vinculados ao orçamento de engenharia
            # Para evitar duplicação, subtraímos do total_budget os valores de serviços que vieram do Orçamento
            servicos_do_orcamento = db.session.query(
                func.sum(Servico.valor_global_mao_de_obra).label('total_mo'),
                func.sum(Servico.valor_global_material).label('total_mat')
            ).join(OrcamentoEngItem, OrcamentoEngItem.servico_id == Servico.id).join(OrcamentoEngEtapa).filter(
                OrcamentoEngEtapa.obra_id == obra_id
            ).first()
            
            servicos_orcamento_mo = float(servicos_do_orcamento.total_mo or 0.0) if servicos_do_orcamento else 0.0
            servicos_orcamento_mat = float(servicos_do_orcamento.total_mat or 0.0) if servicos_do_orcamento else 0.0
            logger.debug(f"--- [DEBUG KPI] Serviços vinculados ao Orçamento: MO R$ {servicos_orcamento_mo:.2f}, MAT R$ {servicos_orcamento_mat:.2f} ---")
            
            # Remover dos totais do Kanban os valores que vieram do Orçamento de Engenharia
            # para não duplicar, já que vamos usar os valores do Orçamento como fonte primária
            total_budget_mo_ajustado = max(0, total_budget_mo - servicos_orcamento_mo)
            total_budget_mat_ajustado = max(0, total_budget_mat - servicos_orcamento_mat)
            logger.debug(f"--- [DEBUG KPI] Kanban ajustado (sem orçamento eng): MO R$ {total_budget_mo_ajustado:.2f}, MAT R$ {total_budget_mat_ajustado:.2f} ---")
            
        except Exception as e:
            logger.exception(f"--- [DEBUG KPI] Erro ao buscar Orçamento de Engenharia: {e} ---")
            traceback.print_exc()
            total_orcamento_eng = 0.0
            total_orcamento_eng_mo = 0.0
            total_orcamento_eng_mat = 0.0
            total_budget_mo_ajustado = total_budget_mo
            total_budget_mat_ajustado = total_budget_mat
        
        # KPI 1: ORÇAMENTO TOTAL
        # = Serviços do Kanban (não vinculados ao orçamento) + Orçamento de Engenharia completo
        kpi_orcamento_total = total_budget_mo_ajustado + total_budget_mat_ajustado + total_orcamento_eng
        logger.debug(f"--- [DEBUG KPI] ✅ ORÇAMENTO TOTAL = Kanban({total_budget_mo_ajustado + total_budget_mat_ajustado:.2f}) + OrcEng({total_orcamento_eng:.2f}) = R$ {kpi_orcamento_total:.2f} ---")
        
        # KPI 2: VALORES EFETIVADOS/PAGOS
        # Inclui: lançamentos pagos + pagamentos de serviço + parcelas pagas COM serviço
        # NOTA: Parcelas sem serviço já estão em total_pago_lancamentos (Lancamento criado ao pagar)
        kpi_valores_pagos = total_pago_lancamentos + total_pago_servicos + total_parcelas_pagas_com_servico
        logger.debug(f"--- [DEBUG KPI] ✅ VALORES PAGOS = R$ {kpi_valores_pagos:.2f} ---")
        
        # KPI 3: LIBERADO PARA PAGAMENTO (Valores pendentes = valor_total - valor_pago)
        # Lançamentos com saldo pendente (valor_total - valor_pago > 0)
        lancamentos_pendentes = db.session.query(
            func.sum(Lancamento.valor_total - Lancamento.valor_pago).label('total_pendente')
        ).filter(
            Lancamento.obra_id == obra_id,
            Lancamento.valor_total > Lancamento.valor_pago,
            Lancamento.status != 'A Pagar'  # NOVO: Exclui 'A Pagar' (agora usa PagamentoFuturo)
        ).first()
        
        # Pagamentos de Serviço com saldo pendente (valor_total - valor_pago > 0)
        pagamentos_servico_pendentes = db.session.query(
            func.sum(PagamentoServico.valor_total - PagamentoServico.valor_pago).label('total_pendente')
        ).join(Servico).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_total > PagamentoServico.valor_pago
        ).first()
        
        # Usar valores já calculados de Pagamentos Futuros e Parcelas
        kpi_liberado_pagamento = (
            float(lancamentos_pendentes.total_pendente or 0.0) + 
            float(pagamentos_servico_pendentes.total_pendente or 0.0) +
            total_futuros +
            total_parcelas_previstas
        )
        
        # KPI 4: DESPESAS EXTRAS (Pagamentos Fora da Planilha de Custos)
        # Pagamentos futuros e parcelas SEM serviço vinculado
        kpi_despesas_extras = total_futuros_extra + total_parcelas_extra
        logger.debug(f"--- [DEBUG KPI] ✅ DESPESAS EXTRAS (fora da planilha) = R$ {kpi_despesas_extras:.2f} ---")
        
        # --- BOLETOS ---
        boletos_obra = Boleto.query.filter_by(obra_id=obra_id).all()
        
        # Boletos COM serviço vinculado = são forma de PAGAMENTO do serviço, NÃO orçamento adicional
        # O orçamento do serviço já está em valor_global_mao_de_obra + valor_global_material
        total_boletos_com_servico = sum(b.valor or 0 for b in boletos_obra if b.vinculado_servico_id)
        total_boletos_com_servico_pendentes = sum(b.valor or 0 for b in boletos_obra if b.vinculado_servico_id and b.status in ['Pendente', 'Vencido'])
        total_boletos_com_servico_pagos = sum(b.valor or 0 for b in boletos_obra if b.vinculado_servico_id and b.status == 'Pago')
        
        # Boletos SEM serviço vinculado = despesas extras
        total_boletos_sem_servico_pendentes = sum(b.valor or 0 for b in boletos_obra if not b.vinculado_servico_id and b.status in ['Pendente', 'Vencido'])
        total_boletos_sem_servico_pagos = sum(b.valor or 0 for b in boletos_obra if not b.vinculado_servico_id and b.status == 'Pago')
        
        # Atualizar KPIs com boletos
        # CORREÇÃO: Boletos com serviço NÃO aumentam orçamento - são forma de pagamento do serviço
        kpi_valores_pagos += total_boletos_com_servico_pagos + total_boletos_sem_servico_pagos  # TODOS boletos pagos vão para valores pagos
        kpi_liberado_pagamento += total_boletos_com_servico_pendentes  # Boletos pendentes com serviço vão para liberado
        kpi_despesas_extras += total_boletos_sem_servico_pendentes + total_boletos_sem_servico_pagos  # Boletos sem serviço vão para despesas extras
        
        logger.debug(f"--- [DEBUG KPI] 📄 BOLETOS: com_servico={total_boletos_com_servico:.2f} (pend={total_boletos_com_servico_pendentes:.2f}, pago={total_boletos_com_servico_pagos:.2f}), sem_servico_pend={total_boletos_sem_servico_pendentes:.2f}, sem_servico_pago={total_boletos_sem_servico_pagos:.2f} ---")

        # Sumário de Segmentos (Apenas Lançamentos Gerais)
        total_por_segmento = db.session.query(
            Lancamento.tipo,
            func.sum(Lancamento.valor_total)
        ).filter(
            Lancamento.obra_id == obra_id, 
            Lancamento.servico_id.is_(None)
        ).group_by(Lancamento.tipo).all()
        
        # <--- Enviando os 4 KPIs corretos (ATUALIZADO v2) -->
        sumarios_dict = {
            "orcamento_total": kpi_orcamento_total,        # Card 1 - Orçamento Total (Vermelho)
            "valores_pagos": kpi_valores_pagos,            # Card 2 - Valores Pagos (Azul/Índigo)
            "liberado_pagamento": kpi_liberado_pagamento,  # Card 3 - Liberado p/ Pagamento (Verde)
            "despesas_extras": kpi_despesas_extras,        # Card 4 - Despesas Extras (Roxo/Amarelo)
            
            # Totais para o gráfico de distribuição de custos
            # Inclui: Kanban ajustado + Orçamento de Engenharia
            "total_mao_obra": total_budget_mo_ajustado + total_orcamento_eng_mo,
            "total_material": total_budget_mat_ajustado + total_orcamento_eng_mat,
            
            # Mantendo este para o Gráfico
            "total_por_segmento_geral": {tipo: float(valor or 0.0) for tipo, valor in total_por_segmento},
        }
        
        # --- HISTÓRICO UNIFICADO ---
        historico_unificado = []
        
        # OTIMIZAÇÃO: Buscar todos os lançamentos com orcamento_item_id em uma única query
        lancamentos_com_item = db.session.execute(db.text("""
            SELECT l.id, l.obra_id, l.tipo, l.descricao, l.valor_total, l.valor_pago, 
                   l.data, l.data_vencimento, l.status, l.pix, l.prioridade, l.fornecedor, l.servico_id,
                   l.orcamento_item_id, s.nome as servico_nome,
                   oei.codigo || ' - ' || oei.descricao as orcamento_item_nome
            FROM lancamento l
            LEFT JOIN servico s ON l.servico_id = s.id
            LEFT JOIN orcamento_eng_item oei ON l.orcamento_item_id = oei.id
            WHERE l.obra_id = :obra_id
        """), {"obra_id": obra_id}).fetchall()
        
        for lanc in lancamentos_com_item:
            descricao = lanc.descricao or "Sem descrição"
            if lanc.servico_nome:
                descricao = f"{descricao} (Serviço: {lanc.servico_nome})"
            
            historico_unificado.append({
                "id": f"lanc-{lanc.id}", "tipo_registro": "lancamento", "data": lanc.data, 
                "data_vencimento": lanc.data_vencimento,
                "descricao": descricao, "tipo": lanc.tipo, 
                "valor_total": float(lanc.valor_total or 0.0), 
                "valor_pago": float(lanc.valor_pago or 0.0), 
                "status": lanc.status, "pix": lanc.pix, "lancamento_id": lanc.id,
                "prioridade": lanc.prioridade,
                "fornecedor": lanc.fornecedor,
                "orcamento_item_id": lanc.orcamento_item_id,
                "orcamento_item_nome": lanc.orcamento_item_nome
            })
        
        # OTIMIZAÇÃO: Buscar pagamentos de serviços com uma única query
        pagamentos_servicos = db.session.execute(db.text("""
            SELECT ps.id, ps.tipo_pagamento, ps.valor_total, ps.valor_pago, ps.data, 
                   ps.data_vencimento, ps.status, ps.prioridade, ps.fornecedor,
                   s.id as servico_id, s.nome as servico_nome, s.pix
            FROM pagamento_servico ps
            JOIN servico s ON ps.servico_id = s.id
            WHERE s.obra_id = :obra_id
        """), {"obra_id": obra_id}).fetchall()
        
        for pag in pagamentos_servicos:
            desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
            historico_unificado.append({
                "id": f"serv-pag-{pag.id}", "tipo_registro": "pagamento_servico", "data": pag.data,
                "data_vencimento": pag.data_vencimento,
                "descricao": f"Pag. {desc_tipo}: {pag.servico_nome}", "tipo": "Serviço", 
                "valor_total": float(pag.valor_total or 0.0), 
                "valor_pago": float(pag.valor_pago or 0.0), 
                "status": pag.status, "pix": pag.pix, "servico_id": pag.servico_id,
                "pagamento_id": pag.id,
                "prioridade": pag.prioridade,
                "fornecedor": pag.fornecedor 
            })
        
        historico_unificado.sort(key=lambda x: x['data'] if x['data'] else datetime.date(1900, 1, 1), reverse=True)
        
        # OTIMIZAÇÃO: Buscar parcelas pagas com serviço OU vinculadas ao orçamento em uma única query
        parcelas_pagas_query = db.session.execute(db.text("""
            SELECT pi.id, pi.numero_parcela, pi.valor_parcela, pi.data_vencimento, pi.data_pagamento,
                   pp.id as pagamento_parcelado_id, pp.descricao, pp.numero_parcelas, pp.segmento, pp.fornecedor,
                   pp.servico_id, s.nome as servico_nome,
                   pp.orcamento_item_id,
                   oei.codigo || ' - ' || oei.descricao as orcamento_item_nome
            FROM parcela_individual pi
            JOIN pagamento_parcelado_v2 pp ON pi.pagamento_parcelado_id = pp.id
            LEFT JOIN servico s ON pp.servico_id = s.id
            LEFT JOIN orcamento_eng_item oei ON pp.orcamento_item_id = oei.id
            WHERE pp.obra_id = :obra_id AND pi.status = 'Pago' 
            AND (pp.servico_id IS NOT NULL OR pp.orcamento_item_id IS NOT NULL)
        """), {"obra_id": obra_id}).fetchall()
        
        logger.debug(f"--- [DEBUG] Parcelas pagas COM serviço ou orcamento_item encontradas: {len(parcelas_pagas_query)} ---")
        
        for parcela in parcelas_pagas_query:
            historico_unificado.append({
                "id": f"parcela-{parcela.id}",
                "tipo_registro": "parcela_individual",
                "data": parcela.data_pagamento or parcela.data_vencimento,
                "data_vencimento": parcela.data_vencimento,
                "descricao": f"{parcela.descricao} ({parcela.numero_parcela}/{parcela.numero_parcelas})",
                "tipo": parcela.segmento or "Material",
                "valor_total": float(parcela.valor_parcela or 0.0),
                "valor_pago": float(parcela.valor_parcela or 0.0),
                "status": "Pago",
                "pix": None,
                "servico_id": parcela.servico_id,
                "servico_nome": parcela.servico_nome,
                "orcamento_item_id": parcela.orcamento_item_id,
                "orcamento_item_nome": parcela.orcamento_item_nome,
                "pagamento_parcelado_id": parcela.pagamento_parcelado_id,
                "parcela_id": parcela.id,
                "prioridade": 0,
                "fornecedor": parcela.fornecedor
            })
        
        # Bug F: Incluir parcelas órfãs (pagas sem serviço E sem orcamento_item, sem Lançamento)
        # Pode acontecer via bulk pay ou editar_parcela_individual que não criam Lançamento
        descricoes_lancamentos_existentes = {
            item['descricao'] for item in historico_unificado
            if item.get('tipo_registro') == 'lancamento'
        }

        parcelas_orfas_query = db.session.execute(db.text("""
            SELECT pi.id, pi.numero_parcela, pi.valor_parcela, pi.data_vencimento, pi.data_pagamento,
                   pp.id as pagamento_parcelado_id, pp.descricao, pp.numero_parcelas, pp.segmento, pp.fornecedor
            FROM parcela_individual pi
            JOIN pagamento_parcelado_v2 pp ON pi.pagamento_parcelado_id = pp.id
            WHERE pp.obra_id = :obra_id
              AND pi.status = 'Pago'
              AND pp.servico_id IS NULL
              AND pp.orcamento_item_id IS NULL
        """), {"obra_id": obra_id}).fetchall()

        orfas_adicionadas = 0
        for parcela in parcelas_orfas_query:
            descricao_esperada = f"{parcela.descricao} (Parcela {parcela.numero_parcela}/{parcela.numero_parcelas})"
            if descricao_esperada in descricoes_lancamentos_existentes:
                continue  # já está no histórico via Lançamento
            historico_unificado.append({
                "id": f"parcela-{parcela.id}",
                "tipo_registro": "parcela_individual",
                "data": parcela.data_pagamento or parcela.data_vencimento,
                "data_vencimento": parcela.data_vencimento,
                "descricao": descricao_esperada,
                "tipo": parcela.segmento or "Material",
                "valor_total": float(parcela.valor_parcela or 0.0),
                "valor_pago": float(parcela.valor_parcela or 0.0),
                "status": "Pago",
                "pix": None,
                "servico_id": None,
                "servico_nome": None,
                "orcamento_item_id": None,
                "orcamento_item_nome": None,
                "pagamento_parcelado_id": parcela.pagamento_parcelado_id,
                "parcela_id": parcela.id,
                "prioridade": 0,
                "fornecedor": parcela.fornecedor
            })
            orfas_adicionadas += 1
        logger.debug(f"--- [DEBUG Bug F] Parcelas órfãs adicionadas ao histórico: {orfas_adicionadas} ---")
        
        # --- INCLUIR BOLETOS PAGOS NO HISTÓRICO ---
        for boleto in boletos_obra:
            if boleto.status == 'Pago':
                servico_nome = None
                if boleto.vinculado_servico_id:
                    servico = db.session.get(Servico, boleto.vinculado_servico_id)
                    servico_nome = servico.nome if servico else None
                
                historico_unificado.append({
                    "id": f"boleto-{boleto.id}",
                    "tipo_registro": "boleto",
                    "data": boleto.data_pagamento or boleto.data_vencimento,
                    "data_vencimento": boleto.data_vencimento,
                    "descricao": f"📄 Boleto: {boleto.descricao or boleto.beneficiario or 'Sem descrição'}",
                    "tipo": "Boleto",
                    "valor_total": float(boleto.valor or 0.0),
                    "valor_pago": float(boleto.valor or 0.0),
                    "status": "Pago",
                    "pix": boleto.codigo_barras,
                    "servico_id": boleto.vinculado_servico_id,
                    "servico_nome": servico_nome,
                    "boleto_id": boleto.id,
                    "prioridade": 0,
                    "fornecedor": boleto.beneficiario
                })
        
        # Re-ordenar após incluir parcelas
        historico_unificado.sort(key=lambda x: x['data'] if x['data'] else datetime.date(1900, 1, 1), reverse=True)
        
        for item in historico_unificado:
            if item['data']:
                item['data'] = item['data'].isoformat()
            if item.get('data_vencimento'):
                item['data_vencimento'] = item['data_vencimento'].isoformat()
            
        # --- Cálculo dos totais de serviço ---
        # OTIMIZAÇÃO: Buscar lançamentos por serviço em uma única query
        lancamentos_por_servico = {}
        lancamentos_servico_query = db.session.execute(db.text("""
            SELECT id, servico_id, tipo, descricao, valor_total, valor_pago, data, status, fornecedor
            FROM lancamento 
            WHERE obra_id = :obra_id AND servico_id IS NOT NULL
        """), {"obra_id": obra_id}).fetchall()
        
        for l in lancamentos_servico_query:
            if l.servico_id not in lancamentos_por_servico:
                lancamentos_por_servico[l.servico_id] = []
            lancamentos_por_servico[l.servico_id].append(l)
        
        servicos_com_totais = []
        for s in obra.servicos:
            serv_dict = s.to_dict()
            
            # Lancamentos vinculados ao serviço
            lancamentos_servico = lancamentos_por_servico.get(s.id, [])
            
            # COMPROMETIDO (valor_total de todos os lancamentos)
            gastos_vinculados_mo = sum(
                float(l.valor_total or 0.0) for l in lancamentos_servico
                if l.tipo == 'Mão de Obra'
            )
            gastos_vinculados_mat = sum(
                float(l.valor_total or 0.0) for l in lancamentos_servico 
                if l.tipo == 'Material'
            )
            serv_dict['total_gastos_vinculados_mo'] = gastos_vinculados_mo
            serv_dict['total_gastos_vinculados_mat'] = gastos_vinculados_mat
            
            # NOVO: Incluir lancamentos pagos no histórico de pagamentos do serviço
            # (Esses valores já são contados no total_gastos, então só adicionamos ao histórico)
            lancamentos_pagos = [l for l in lancamentos_servico if l.status == 'Pago']
            for lanc in lancamentos_pagos:
                serv_dict['pagamentos'].append({
                    "id": f"lanc-{lanc.id}",
                    "data": lanc.data.isoformat() if lanc.data else None,
                    "tipo_pagamento": "mao_de_obra" if lanc.tipo == 'Mão de Obra' else "material",
                    "fornecedor": lanc.fornecedor,
                    "valor_total": lanc.valor_total,
                    "valor_pago": lanc.valor_pago,
                    "status": "Pago",
                    "descricao": lanc.descricao,
                    "is_lancamento": True
                })
            
            # Incluir parcelas pagas de pagamentos parcelados vinculados ao serviço
            parcelas_do_servico = ParcelaIndividual.query.join(PagamentoParcelado).filter(
                PagamentoParcelado.servico_id == s.id,
                ParcelaIndividual.status == 'Pago'
            ).all()
            
            parcelas_list = []
            for parcela in parcelas_do_servico:
                pag = parcela.pagamento_parcelado
                parcelas_list.append({
                    "id": parcela.id,
                    "data": (parcela.data_pagamento or parcela.data_vencimento).isoformat() if (parcela.data_pagamento or parcela.data_vencimento) else None,
                    "tipo_pagamento": "mao_de_obra" if pag.segmento == "Mão de Obra" else "material",
                    "fornecedor": pag.fornecedor,
                    "valor_total": parcela.valor_parcela,
                    "valor_pago": parcela.valor_parcela,
                    "status": "Pago",
                    "descricao": f"{pag.descricao} ({parcela.numero_parcela}/{pag.numero_parcelas})",
                    "is_parcela": True
                })
            
            # Adicionar parcelas ao histórico de pagamentos do serviço
            if parcelas_list:
                serv_dict['pagamentos'] = serv_dict.get('pagamentos', []) + parcelas_list
            
            servicos_com_totais.append(serv_dict)
            
        # Busca orçamentos pendentes
        orcamentos_pendentes = Orcamento.query.filter_by(
            obra_id=obra_id, 
            status='Pendente'
        ).options(
            joinedload(Orcamento.anexos)
        ).order_by(Orcamento.id.desc()).all()
        
        
        # Buscar lançamentos para o retorno (usando a query já feita)
        lancamentos_retorno = Lancamento.query.filter_by(obra_id=obra_id).all()
        
        return jsonify({
            "obra": obra.to_dict(),
            "lancamentos": [l.to_dict() for l in lancamentos_retorno],
            "servicos": servicos_com_totais,
            "historico_unificado": historico_unificado, 
            "sumarios": sumarios_dict,
            "orcamentos": [o.to_dict() for o in orcamentos_pendentes] 
        })
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO GERAL] /obras/{obra_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DA ROTA ---

@obras_bp.route('/obras/<int:obra_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def deletar_obra(obra_id):
    logger.info(f"--- [LOG] Rota /obras/{obra_id} (DELETE) acessada ---")
    try:
        obra = Obra.query.get_or_404(obra_id)
        
        # 1. Deletar parcelas individuais dos pagamentos parcelados desta obra
        pagamentos_parcelados_ids = [p.id for p in PagamentoParcelado.query.filter_by(obra_id=obra_id).all()]
        if pagamentos_parcelados_ids:
            ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id.in_(pagamentos_parcelados_ids)
            ).delete(synchronize_session=False)
            logger.info(f"--- [LOG] Parcelas individuais deletadas para obra {obra_id} ---")
        
        # 2. Deletar pagamentos parcelados
        PagamentoParcelado.query.filter_by(obra_id=obra_id).delete(synchronize_session=False)
        logger.info(f"--- [LOG] Pagamentos parcelados deletados para obra {obra_id} ---")
        
        # 3. Deletar CaixaObra associado (não tem cascade automático)
        CaixaObra.query.filter_by(obra_id=obra_id).delete(synchronize_session=False)
        
        # 4. Deletar a obra (cascade deleta o resto)
        db.session.delete(obra)
        db.session.commit()
        logger.info(f"--- [LOG] Obra {obra_id} deletada com sucesso ---")
        return jsonify({"sucesso": "Obra deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/obras/<int:obra_id>/concluir', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master']) 
def concluir_obra(obra_id):
    """Marca uma obra como concluída ou reabre"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/concluir (PATCH) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        dados = request.get_json() or {}
        
        # Se não passar 'concluida', alterna o estado atual
        if 'concluida' in dados:
            obra.concluida = dados['concluida']
        else:
            obra.concluida = not (obra.concluida or False)
        
        db.session.commit()
        
        status_texto = "concluída" if obra.concluida else "reaberta"
        logger.info(f"--- [LOG] Obra '{obra.nome}' marcada como {status_texto} ---")
        
        return jsonify({
            "sucesso": f"Obra {status_texto} com sucesso!",
            "obra": obra.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/concluir: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500



@obras_bp.route('/obras/<int:obra_id>/arquivar', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def arquivar_obra(obra_id):
    """Arquiva uma obra (arquivada=True)."""
    if request.method == 'OPTIONS':
        return ('', 204)

    logger.info(f"--- [LOG] Rota /obras/{obra_id}/arquivar (PATCH) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404

        if obra.arquivada:
            return jsonify({"erro": "Obra já está arquivada"}), 400

        obra.arquivada = True
        db.session.commit()

        logger.info(f"Obra {obra_id} arquivada pelo usuário {user.id}")
        return jsonify({
            "mensagem": "Obra arquivada com sucesso",
            "obra_id": obra_id,
            "arquivada": True
        }), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] /obras/{obra_id}/arquivar: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/obras/<int:obra_id>/desarquivar', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def desarquivar_obra(obra_id):
    """Desarquiva uma obra (arquivada=False)."""
    if request.method == 'OPTIONS':
        return ('', 204)

    logger.info(f"--- [LOG] Rota /obras/{obra_id}/desarquivar (PATCH) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404

        if not obra.arquivada:
            return jsonify({"erro": "Obra não está arquivada"}), 400

        obra.arquivada = False
        db.session.commit()

        logger.info(f"Obra {obra_id} desarquivada pelo usuário {user.id}")
        return jsonify({
            "mensagem": "Obra desarquivada com sucesso",
            "obra_id": obra_id,
            "arquivada": False
        }), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] /obras/{obra_id}/desarquivar: {str(e)} ---")
        return jsonify({"erro": str(e)}), 500


# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @obras_bp.route('/servicos/<int:servico_id>/pagamentos', methods=['POST', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master']) 
# def add_pagamento_servico(servico_id):
#     # ... (código atualizado para valor_total/valor_pago) ...
#     print(f"--- [LOG] Rota /servicos/{servico_id}/pagamentos (POST) acessada ---")
#     try:
#         user = get_current_user()
#         servico = Servico.query.get_or_404(servico_id)
# 
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
# 
#         dados = request.json
#         
#         tipo_pagamento = dados.get('tipo_pagamento')
#         if tipo_pagamento not in ['mao_de_obra', 'material']:
#             return jsonify({"erro": "O 'tipo_pagamento' é obrigatório e deve ser 'mao_de_obra' ou 'material'"}), 400
#             
#         valor_total = float(dados['valor'])
#         status = dados.get('status', 'Pago')
#         valor_pago = valor_total if status == 'Pago' else 0.0
# 
#         novo_pagamento = PagamentoServico(
#             servico_id=servico_id,
#             data=date.fromisoformat(dados['data']),
#             data_vencimento=date.fromisoformat(dados['data_vencimento']) if dados.get('data_vencimento') else None,
#             valor_total=valor_total, 
#             valor_pago=valor_pago, 
#             status=status,
#             tipo_pagamento=tipo_pagamento,
#             forma_pagamento=dados.get('forma_pagamento'),
#             pix=dados.get('pix'),  # Chave PIX do pagamento
#             prioridade=int(dados.get('prioridade', 0)),
#             fornecedor=dados.get('fornecedor') 
#         )
#         db.session.add(novo_pagamento)
#         db.session.commit()
#         servico_atualizado = Servico.query.get(servico_id)
#         return jsonify(servico_atualizado.to_dict())
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /servicos/{servico_id}/pagamentos (POST): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e)}), 500
# ===============================================================================

# ===== ROTA PARA LIMPAR PAGAMENTOS DUPLICADOS DE PARCELAS =====
@obras_bp.route('/obras/<int:obra_id>/limpar-pagamentos-parcelas-duplicados', methods=['POST'])
@jwt_required()
def limpar_pagamentos_parcelas_duplicados(obra_id):
    """
    Remove PagamentoServico que foram criados a partir de parcelas (antes da correção).
    Isso evita duplicação no histórico do serviço, já que as parcelas pagas
    agora aparecem via query de ParcelaIndividual.
    """
    try:
        user = get_current_user()
        
        if user.role not in ['master', 'administrador']:
            return jsonify({"erro": "Apenas administradores podem executar esta ação"}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão para esta obra"}), 403
        
        # Buscar todos os pagamentos parcelados COM serviço desta obra
        pagamentos_parcelados = PagamentoParcelado.query.filter(
            PagamentoParcelado.obra_id == obra_id,
            PagamentoParcelado.servico_id.isnot(None)
        ).all()
        
        pagamentos_removidos = 0
        detalhes = []
        
        for pag_parcelado in pagamentos_parcelados:
            # Para cada parcela PAGA, verificar se existe um PagamentoServico duplicado
            parcelas_pagas = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                ParcelaIndividual.status == 'Pago'
            ).all()
            
            for parcela in parcelas_pagas:
                # Buscar PagamentoServico com mesmo valor e serviço
                pagamentos_servico = PagamentoServico.query.filter(
                    PagamentoServico.servico_id == pag_parcelado.servico_id,
                    PagamentoServico.valor_total == parcela.valor_parcela
                ).all()
                
                for pag_serv in pagamentos_servico:
                    # Verificar se a data corresponde ou se é próxima
                    if pag_serv.data_pagamento and parcela.data_pagamento:
                        diff_dias = abs((pag_serv.data_pagamento - parcela.data_pagamento).days)
                        if diff_dias <= 1:  # Mesma data ou 1 dia de diferença
                            detalhes.append({
                                "id": pag_serv.id,
                                "valor": float(pag_serv.valor_total),
                                "data": pag_serv.data_pagamento.isoformat() if pag_serv.data_pagamento else None,
                                "parcela": f"{pag_parcelado.descricao} ({parcela.numero_parcela}/{pag_parcelado.numero_parcelas})"
                            })
                            db.session.delete(pag_serv)
                            pagamentos_removidos += 1
                            break
        
        db.session.commit()
        
        return jsonify({
            "mensagem": f"Limpeza concluída! {pagamentos_removidos} pagamentos duplicados removidos.",
            "pagamentos_removidos": pagamentos_removidos,
            "detalhes": detalhes
        })
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500



# Rota para deletar pagamento de serviço pelo ID (usado pelo histórico de pagamentos)
@obras_bp.route('/pagamentos-servico/<int:pagamento_id>', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def atualizar_pagamento_servico(pagamento_id):
    """Atualização parcial de pagamento de serviço (tipo_pagamento, orcamento_item_id)"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    try:
        pagamento = db.session.get(PagamentoServico, pagamento_id)
        if not pagamento:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        dados = request.json
        if 'tipo_pagamento' in dados:
            pagamento.tipo_pagamento = dados['tipo_pagamento']  # 'mao_de_obra' ou 'material'
        if 'orcamento_item_id' in dados:
            orcamento_item_id = dados['orcamento_item_id']
            try:
                db.session.execute(db.text(
                    f"UPDATE pagamento_servico SET orcamento_item_id = {'NULL' if not orcamento_item_id else orcamento_item_id} WHERE id = {pagamento_id}"
                ))
            except Exception as e:
                logger.exception(f"[AVISO] Erro ao atualizar orcamento_item_id em pagamento_servico: {e}")
        db.session.commit()
        return jsonify({"sucesso": True, "id": pagamento_id})
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /pagamentos-servico/{pagamento_id} (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@obras_bp.route('/pagamentos-servico/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_pagamento_servico_por_id(pagamento_id):
    """
    Deleta um pagamento de serviço pelo ID.
    Regras:
    - Pagamentos PAGOS só podem ser deletados por usuários MASTER
    - Pagamentos NÃO PAGOS podem ser deletados por ADMINISTRADOR ou MASTER
    - Remove também notas fiscais associadas ao pagamento
    """
    logger.info(f"--- [LOG] Rota /pagamentos-servico/{pagamento_id} (DELETE) acessada ---")
    
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    
    try:
        pagamento = db.session.get(PagamentoServico, pagamento_id)
        
        if not pagamento:
            return jsonify({"erro": "Pagamento não encontrado"}), 404
        
        # Obter o papel do usuário
        claims = get_jwt()
        user_role = claims.get('role')
        
        # Verificar se o pagamento está PAGO
        is_pago = (pagamento.valor_pago or 0) >= (pagamento.valor_total or 0)
        
        # REGRA: Se está PAGO, ADMINISTRADOR ou MASTER podem deletar
        if is_pago and user_role not in ['administrador', 'master']:
            logger.error(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Apenas administradores e masters podem excluir pagamentos já executados."
            }), 403
        
        # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
        if not is_pago and user_role not in ['administrador', 'master']:
            logger.error(f"--- [LOG] ❌ Tentativa de deletar pagamento por usuário {user_role} ---")
            return jsonify({
                "erro": "Acesso negado: Permissão insuficiente."
            }), 403
        
        # 1. Remover notas fiscais associadas a este pagamento
        notas_removidas = NotaFiscal.query.filter_by(
            item_id=pagamento_id,
            item_type='pagamento_servico'
        ).delete()
        if notas_removidas > 0:
            logger.info(f"--- [LOG] {notas_removidas} nota(s) fiscal(is) removida(s) do pagamento {pagamento_id} ---")
        
        # 2. Remover o pagamento
        db.session.delete(pagamento)
        db.session.commit()
        
        logger.info(f"--- [LOG] ✅ Pagamento de serviço {pagamento_id} deletado pelo usuário {user_role} ---")
        return jsonify({"sucesso": "Pagamento e dados associados deletados com sucesso"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /pagamentos-servico/{pagamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# Rota alternativa para deletar pagamento de serviço (usada pelo histórico)
# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @obras_bp.route('/obras/<int:obra_id>/servicos/pagamentos/<int:pagamento_id>', methods=['DELETE', 'OPTIONS'])
# @jwt_required()
# def deletar_pagamento_servico_alternativo(obra_id, pagamento_id):
#     """
#     Rota alternativa para deletar pagamento de serviço.
#     Busca o pagamento pelo ID e aplica as mesmas regras de segurança.
#     """
#     print(f"--- [LOG] Rota /obras/{obra_id}/servicos/pagamentos/{pagamento_id} (DELETE) acessada ---")
#     
#     if request.method == 'OPTIONS':
#         return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
#     
#     try:
#         # Buscar o pagamento pelo ID
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         
#         # Verificar se o pagamento pertence a um serviço da obra especificada
#         servico = Servico.query.get(pagamento.servico_id)
#         if not servico or servico.obra_id != obra_id:
#             return jsonify({"erro": "Pagamento não encontrado nesta obra"}), 404
#         
#         # Obter o papel do usuário
#         claims = get_jwt()
#         user_role = claims.get('role')
#         
#         # Verificar se o pagamento está PAGO (completamente executado)
#         is_pago = pagamento.valor_pago >= pagamento.valor_total
#         
#         # REGRA: Se está PAGO, apenas MASTER pode deletar
#         if is_pago and user_role != 'master':
#             print(f"--- [LOG] ❌ Tentativa de deletar pagamento PAGO de serviço por usuário {user_role} (não MASTER) ---")
#             return jsonify({
#                 "erro": "Acesso negado: Apenas usuários MASTER podem excluir pagamentos já executados (PAGOS)."
#             }), 403
#         
#         # REGRA: Se NÃO está pago, ADMINISTRADOR ou MASTER podem deletar
#         if not is_pago and user_role not in ['administrador', 'master']:
#             print(f"--- [LOG] ❌ Tentativa de deletar pagamento de serviço por usuário {user_role} (sem permissão) ---")
#             return jsonify({
#                 "erro": "Acesso negado: Permissão insuficiente para excluir este pagamento."
#             }), 403
#         
#         db.session.delete(pagamento)
#         db.session.commit()
#         
#         print(f"--- [LOG] ✅ Pagamento de serviço {pagamento_id} deletado com sucesso pelo usuário {user_role} ---")
#         return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /obras/.../servicos/pagamentos (DELETE): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e)}), 500
# ===============================================================================

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @obras_bp.route('/servicos/pagamentos/<int:pagamento_id>/status', methods=['PATCH', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master'])
# def toggle_pagamento_servico_status(pagamento_id):
#     # ... (código atualizado para valor_total/valor_pago) ...
#     print(f"--- [LOG] Rota /servicos/pagamentos/{pagamento_id}/status (PATCH) acessada ---")
#     try:
#         user = get_current_user()
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         servico = Servico.query.get(pagamento.servico_id)
#         
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
#         
#         if pagamento.status == 'Pago':
#             pagamento.status = 'A Pagar'
#             pagamento.valor_pago = 0.0
#         else:
#             pagamento.status = 'Pago'
#             pagamento.valor_pago = pagamento.valor_total
#             
#         db.session.commit()
#         return jsonify(pagamento.to_dict()), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /servicos/pagamentos/.../status (PATCH): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e)}), 500
# ===============================================================================

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @obras_bp.route('/servicos/pagamentos/<int:pagamento_id>/prioridade', methods=['PATCH', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master'])
# def editar_pagamento_servico_prioridade(pagamento_id):
#     # ... (código inalterado) ...
#     print(f"--- [LOG] Rota /servicos/pagamentos/{pagamento_id}/prioridade (PATCH) acessada ---")
#     if request.method == 'OPTIONS': 
#         return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
#         
#     try:
#         user = get_current_user()
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         servico = Servico.query.get(pagamento.servico_id)
#         
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
#         
#         dados = request.json
#         nova_prioridade = dados.get('prioridade')
#         
#         if nova_prioridade is None or not isinstance(nova_prioridade, int):
#             return jsonify({"erro": "Prioridade inválida. Deve ser um número."}), 400
#             
#         pagamento.prioridade = int(nova_prioridade)
#         db.session.commit()
#         
#         return jsonify(pagamento.to_dict()), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] /servicos/pagamentos/.../prioridade (PATCH): {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e)}), 500
# ===============================================================================

# ===== ROTA DESABILITADA - PAGAMENTOS AGORA SÓ VIA CRONOGRAMA FINANCEIRO =====
# @obras_bp.route('/servicos/pagamentos/<int:pagamento_id>', methods=['PUT', 'OPTIONS'])
# @check_permission(roles=['administrador', 'master'])
# def editar_pagamento_servico(pagamento_id):
#     """Edita um pagamento de serviço completo"""
#     print(f"--- [LOG] Rota /servicos/pagamentos/{pagamento_id} (PUT) acessada ---")
#     if request.method == 'OPTIONS':
#         return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
#     
#     try:
#         user = get_current_user()
#         pagamento = PagamentoServico.query.get_or_404(pagamento_id)
#         servico = Servico.query.get(pagamento.servico_id)
#         
#         if not user_has_access_to_obra(user, servico.obra_id):
#             return jsonify({"erro": "Acesso negado a esta obra."}), 403
#         
#         dados = request.json
#         
#         # Atualizar campos se fornecidos
#         if 'data' in dados:
#             pagamento.data = date.fromisoformat(dados['data'])
#         if 'data_vencimento' in dados:
#             pagamento.data_vencimento = date.fromisoformat(dados['data_vencimento']) if dados['data_vencimento'] else None
#         if 'valor' in dados:
#             pagamento.valor_total = float(dados['valor'])
#             # Se status = Pago, atualizar valor_pago também
#             if pagamento.status == 'Pago':
#                 pagamento.valor_pago = pagamento.valor_total
#         if 'tipo_pagamento' in dados:
#             if dados['tipo_pagamento'] not in ['mao_de_obra', 'material']:
#                 return jsonify({"erro": "tipo_pagamento deve ser 'mao_de_obra' ou 'material'"}), 400
#             pagamento.tipo_pagamento = dados['tipo_pagamento']
#         if 'forma_pagamento' in dados:
#             pagamento.forma_pagamento = dados['forma_pagamento']
#         if 'pix' in dados:
#             pagamento.pix = dados['pix']
#         if 'fornecedor' in dados:
#             pagamento.fornecedor = dados['fornecedor']
#         if 'prioridade' in dados:
#             pagamento.prioridade = int(dados['prioridade'])
#         if 'status' in dados:
#             pagamento.status = dados['status']
#             # Ajustar valor_pago conforme status
#             if dados['status'] == 'Pago':
#                 pagamento.valor_pago = pagamento.valor_total
#             elif dados['status'] == 'A Pagar':
#                 pagamento.valor_pago = 0.0
#         
#         db.session.commit()
#         return jsonify(pagamento.to_dict()), 200
#         
#     except Exception as e:
#         db.session.rollback()
#         error_details = traceback.format_exc()
#         print(f"--- [ERRO] PUT /servicos/pagamentos/{pagamento_id}: {str(e)}\n{error_details} ---")
#         return jsonify({"erro": str(e)}), 500
# ===============================================================================
# ---------------------------------------------------


# --- NOVA ROTA PARA PAGAMENTO PARCIAL ---
@obras_bp.route('/pagamentos/<string:item_type>/<int:item_id>/pagar', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def pagar_item_parcial(item_type, item_id):
    """
    Registra um pagamento (parcial ou total) para um item de despesa.
    item_type pode ser 'lancamento' ou 'pagamento_servico'.
    """
    if request.method == 'OPTIONS': 
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        user = get_current_user()
        dados = request.json
        valor_a_pagar = float(dados.get('valor_a_pagar', 0))

        if valor_a_pagar <= 0:
            return jsonify({"erro": "O valor a pagar deve ser positivo."}), 400

        item = None
        
        # 1. Encontrar o item e verificar permissões
        if item_type == 'lancamento':
            item = Lancamento.query.get_or_404(item_id)
            if not user_has_access_to_obra(user, item.obra_id):
                return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        elif item_type == 'pagamento_servico':
            item = PagamentoServico.query.get_or_404(item_id)
            servico = Servico.query.get(item.servico_id)
            if not user_has_access_to_obra(user, servico.obra_id):
                return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        else:
            return jsonify({"erro": "Tipo de item inválido."}), 400

        # 2. Validar o pagamento
        valor_restante = item.valor_total - item.valor_pago
        if valor_a_pagar > (valor_restante + 0.01): # 0.01 de margem para floats
            return jsonify({"erro": f"O valor a pagar (R$ {valor_a_pagar:.2f}) é maior que o valor restante (R$ {valor_restante:.2f})."}), 400

        # 3. Atualizar o item
        item.valor_pago += valor_a_pagar
        
        # 4. Atualizar o status
        if (item.valor_total - item.valor_pago) < 0.01: # Se estiver totalmente pago
            item.status = 'Pago'
            item.valor_pago = item.valor_total # Garante valor exato
        else:
            item.status = 'A Pagar' 

        db.session.commit()
        return jsonify(item.to_dict()), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /pagamentos/.../pagar (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DA NOVA ROTA ---


# --- ROTAS DE ORÇAMENTO (MODIFICADAS PARA ANEXOS) ---

@obras_bp.route('/obras/<int:obra_id>/orcamentos', methods=['GET', 'OPTIONS'])
@check_permission(roles=['administrador', 'master', 'comum'])
def get_orcamentos_obra(obra_id):
    """Lista todos os orçamentos de uma obra com seus anexos"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/orcamentos (GET) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        # Buscar obra para validar
        obra = Obra.query.get_or_404(obra_id)
        
        # Buscar todos os orçamentos da obra com eager loading dos anexos
        orcamentos = Orcamento.query.filter_by(obra_id=obra_id).options(
            joinedload(Orcamento.anexos),
            joinedload(Orcamento.servico)
        ).all()
        
        # Montar resposta com informações dos anexos
        orcamentos_data = []
        for orc in orcamentos:
            orc_dict = orc.to_dict()
            # Adicionar lista de anexos com detalhes
            orc_dict['anexos'] = [
                {
                    'id': anexo.id,
                    'filename': anexo.filename,
                    'mimetype': anexo.mimetype
                }
                for anexo in orc.anexos
            ]
            orcamentos_data.append(orc_dict)
        
        logger.info(f"--- [LOG] {len(orcamentos_data)} orçamentos encontrados para obra {obra_id} ---")
        return jsonify(orcamentos_data), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/orcamentos (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@obras_bp.route('/obras/<int:obra_id>/orcamentos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master', 'comum'])  # Operador e Admin podem cadastrar
def add_orcamento(obra_id):
    """Cria uma nova solicitação de compra"""
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/orcamentos (POST) acessada ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        dados = request.form
        
        # Processar data de vencimento
        data_vencimento = None
        if dados.get('data_vencimento'):
            try:
                data_vencimento = datetime.strptime(dados['data_vencimento'], '%Y-%m-%d').date()
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)
                pass
        
        novo_orcamento = Orcamento(
            obra_id=obra_id,
            descricao=dados['descricao'],
            fornecedor=dados.get('fornecedor') or None,
            valor=float(dados.get('valor', 0)),
            dados_pagamento=dados.get('dados_pagamento') or None,
            tipo=dados['tipo'],
            status='Pendente',
            observacoes=dados.get('observacoes') or None, 
            servico_id=int(dados['servico_id']) if dados.get('servico_id') else None,
            # NOVOS CAMPOS
            data_vencimento=data_vencimento,
            numero_parcelas=int(dados.get('numero_parcelas', 1)) if dados.get('numero_parcelas') else 1,
            periodicidade=dados.get('periodicidade') or 'Mensal'
        )
        db.session.add(novo_orcamento)
        db.session.commit() 

        files = request.files.getlist('anexos')
        for file in files:
            if file and file.filename:
                novo_anexo = AnexoOrcamento(
                    orcamento_id=novo_orcamento.id,
                    filename=file.filename,
                    mimetype=file.mimetype,
                    data=file.read()
                )
                db.session.add(novo_anexo)
        
        db.session.commit() 
        
        # --- NOTIFICAÇÃO PARA MASTERS ---
        obra = Obra.query.get(obra_id)
        obra_nome = obra.nome if obra else f"Obra {obra_id}"
        valor_formatado = f"R$ {novo_orcamento.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        
        notificar_masters(
            tipo='orcamento_pendente',
            titulo='📋 Nova solicitação aguardando aprovação',
            mensagem=f'{user.username} cadastrou "{novo_orcamento.descricao}" ({valor_formatado}) na obra {obra_nome}',
            obra_id=obra_id,
            item_id=novo_orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        return jsonify(novo_orcamento.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/orcamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@obras_bp.route('/orcamentos/<int:orcamento_id>', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def editar_orcamento(orcamento_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /orcamentos/{orcamento_id} (PUT) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        if orcamento.status != 'Pendente':
            return jsonify({"erro": "Não é possível editar um orçamento que já foi processado."}), 400

        dados = request.form
        
        orcamento.descricao = dados.get('descricao', orcamento.descricao)
        orcamento.fornecedor = dados.get('fornecedor') or None
        orcamento.valor = float(dados.get('valor', orcamento.valor))
        orcamento.dados_pagamento = dados.get('dados_pagamento') or None
        orcamento.tipo = dados.get('tipo', orcamento.tipo)
        orcamento.observacoes = dados.get('observacoes') or None
        orcamento.servico_id = int(dados['servico_id']) if dados.get('servico_id') else None
        
        db.session.commit()
        return jsonify(orcamento.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /orcamentos/{orcamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DA ROTA ---

@obras_bp.route('/orcamentos/<int:orcamento_id>/aprovar', methods=['POST', 'OPTIONS'])
@check_permission(roles=['master'])  # APENAS Master pode aprovar
def aprovar_orcamento(orcamento_id):
    """
    Master aprova a solicitação com 1 clique.
    Sistema cria automaticamente o Pagamento Futuro/Parcelado.
    Valores são somados ao serviço vinculado (se houver).
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /orcamentos/{orcamento_id}/aprovar (POST) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if orcamento.status != 'Pendente':
            return jsonify({"erro": f"Esta solicitação já foi processada. Status atual: {orcamento.status}"}), 400

        # 1. Marcar como aprovado
        orcamento.status = 'Aprovado'
        
        # 2. Se tem serviço vinculado, somar valor ao orçamento do serviço
        if orcamento.servico_id:
            servico = Servico.query.get(orcamento.servico_id)
            if servico:
                tipo_orcamento = orcamento.tipo or ''
                if 'material' in tipo_orcamento.lower():
                    servico.valor_global_material = (servico.valor_global_material or 0) + (orcamento.valor or 0)
                    logger.info(f"[LOG] ✅ Valor somado ao material do serviço {servico.id}: +R$ {orcamento.valor}")
                else:
                    servico.valor_global_mao_de_obra = (servico.valor_global_mao_de_obra or 0) + (orcamento.valor or 0)
                    logger.info(f"[LOG] ✅ Valor somado à MO do serviço {servico.id}: +R$ {orcamento.valor}")
        
        # 3. Criar Pagamento Futuro automaticamente
        valor_orcamento = orcamento.valor or 0.0
        descricao_pagamento = f"{orcamento.descricao}"
        
        # Usar dados de pagamento da solicitação (ou defaults)
        data_vencimento = orcamento.data_vencimento if hasattr(orcamento, 'data_vencimento') and orcamento.data_vencimento else date.today() + timedelta(days=30)
        numero_parcelas = orcamento.numero_parcelas if hasattr(orcamento, 'numero_parcelas') and orcamento.numero_parcelas else 1
        periodicidade = orcamento.periodicidade if hasattr(orcamento, 'periodicidade') and orcamento.periodicidade else 'Mensal'
        
        if numero_parcelas == 1:
            # Criar Pagamento Futuro Único
            pagamento_futuro = PagamentoFuturo(
                obra_id=orcamento.obra_id,
                descricao=descricao_pagamento,
                fornecedor=orcamento.fornecedor,
                valor=valor_orcamento,
                data_vencimento=data_vencimento,
                status='Previsto',
                servico_id=orcamento.servico_id,
                observacoes=f"Solicitação #{orcamento.id} aprovada"
            )
            db.session.add(pagamento_futuro)
            logger.info(f"[LOG] ✅ Pagamento Futuro criado: R$ {valor_orcamento:.2f} para {data_vencimento}")
            
        else:
            # Criar Pagamento Parcelado
            valor_parcela = valor_orcamento / numero_parcelas
            
            pagamento_parcelado = PagamentoParcelado(
                obra_id=orcamento.obra_id,
                descricao=descricao_pagamento,
                fornecedor=orcamento.fornecedor,
                servico_id=orcamento.servico_id,
                valor_total=valor_orcamento,
                numero_parcelas=numero_parcelas,
                valor_parcela=valor_parcela,
                data_primeira_parcela=data_vencimento,
                periodicidade=periodicidade,
                parcelas_pagas=0,
                status='Ativo',
                observacoes=f"Solicitação #{orcamento.id} aprovada"
            )
            db.session.add(pagamento_parcelado)
            db.session.flush()
            
            # Criar parcelas individuais
            for i in range(numero_parcelas):
                if periodicidade == 'Semanal':
                    data_parcela = data_vencimento + timedelta(weeks=i)
                elif periodicidade == 'Quinzenal':
                    data_parcela = data_vencimento + timedelta(weeks=i*2)
                else:  # Mensal
                    mes = data_vencimento.month + i
                    ano = data_vencimento.year + (mes - 1) // 12
                    mes = ((mes - 1) % 12) + 1
                    try:
                        data_parcela = data_vencimento.replace(year=ano, month=mes)
                    except ValueError:
                        import calendar
                        ultimo_dia = calendar.monthrange(ano, mes)[1]
                        data_parcela = data_vencimento.replace(year=ano, month=mes, day=min(data_vencimento.day, ultimo_dia))
                
                parcela = ParcelaIndividual(
                    pagamento_parcelado_id=pagamento_parcelado.id,
                    numero_parcela=i + 1,
                    valor_parcela=valor_parcela,
                    data_vencimento=data_parcela,
                    status='Previsto'
                )
                db.session.add(parcela)
            
            logger.info(f"[LOG] ✅ Pagamento Parcelado criado: {numero_parcelas}x R$ {valor_parcela:.2f}")
        
        db.session.commit()
        
        # 4. NOTIFICAÇÕES
        obra = Obra.query.get(orcamento.obra_id)
        obra_nome = obra.nome if obra else f"Obra {orcamento.obra_id}"
        
        # Notificar operadores
        notificar_operadores_obra(
            obra_id=orcamento.obra_id,
            tipo='orcamento_aprovado',
            titulo='✅ Solicitação aprovada',
            mensagem=f'A solicitação "{orcamento.descricao}" foi aprovada e enviada para pagamento',
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar administradores
        notificar_administradores(
            tipo='orcamento_aprovado',
            titulo='💰 Nova compra autorizada',
            mensagem=f'Solicitação "{orcamento.descricao}" - R$ {valor_orcamento:,.2f} adicionada ao cronograma financeiro',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar masters
        notificar_masters(
            tipo='orcamento_aprovado',
            titulo='✅ Solicitação aprovada',
            mensagem=f'{user.username} aprovou "{orcamento.descricao}" na obra {obra_nome}',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        msg_sucesso = f"Solicitação aprovada! Pagamento de R$ {valor_orcamento:,.2f} adicionado ao cronograma."
        if numero_parcelas > 1:
            msg_sucesso = f"Solicitação aprovada! {numero_parcelas}x R$ {valor_orcamento/numero_parcelas:,.2f} adicionado ao cronograma."
        
        return jsonify({"sucesso": msg_sucesso}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /orcamentos/{orcamento_id}/aprovar (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@obras_bp.route('/orcamentos/<int:orcamento_id>/converter_para_servico', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def converter_orcamento_para_servico(orcamento_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    logger.info(f"--- [LOG] Rota /orcamentos/{orcamento_id}/converter_para_servico (POST) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        if orcamento.status != 'Pendente':
            return jsonify({"erro": "Este orçamento já foi processado."}), 400
            
        dados = request.json
        destino_valor = dados.get('destino_valor') 
        
        if destino_valor not in ['orcamento_mo', 'pagamento_vinculado']:
            return jsonify({"erro": "Destino do valor inválido."}), 400

        orcamento.status = 'Aprovado'
        
        novo_servico = Servico(
            obra_id=orcamento.obra_id,
            nome=orcamento.descricao,
            responsavel=orcamento.fornecedor,
            pix=orcamento.dados_pagamento,
            valor_global_mao_de_obra=0.0,
            valor_global_material=0.0
        )
        
        if destino_valor == 'orcamento_mo':
            if orcamento.tipo == 'Mão de Obra':
                novo_servico.valor_global_mao_de_obra = orcamento.valor
            else:
                novo_servico.valor_global_material = orcamento.valor

            db.session.add(novo_servico)
            db.session.commit()
            return jsonify({"sucesso": "Orçamento aprovado e novo serviço criado", "servico": novo_servico.to_dict()}), 200

        else: 
            db.session.add(novo_servico)
            db.session.commit() 

            novo_lancamento = Lancamento(
                obra_id=orcamento.obra_id,
                tipo=orcamento.tipo,
                descricao=orcamento.descricao,
                valor_total=orcamento.valor,
                valor_pago=0.0,
                data=date.today(),
                status='A Pagar',
                pix=orcamento.dados_pagamento,
                prioridade=0,
                fornecedor=orcamento.fornecedor, 
                servico_id=None  # ⚠️ Não vincular ao serviço - vincular apenas via PagamentoServico quando pago
            )
            db.session.add(novo_lancamento)
            db.session.commit()
            return jsonify({"sucesso": "Serviço criado e pendência gerada", "servico": novo_servico.to_dict(), "lancamento": novo_lancamento.to_dict()}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /orcamentos/{orcamento_id}/converter_para_servico (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@obras_bp.route('/orcamentos/<int:orcamento_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def rejeitar_orcamento(orcamento_id):
    # <-- MUDANÇA: Mudar status para 'Rejeitado' em vez de deletar
    logger.info(f"--- [LOG] Rota /orcamentos/{orcamento_id} (DELETE) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        # <-- MUDANÇA: Em vez de deletar, muda status para 'Rejeitado'
        orcamento.status = 'Rejeitado'
        db.session.commit()
        
        # --- NOTIFICAÇÕES ---
        obra = Obra.query.get(orcamento.obra_id)
        obra_nome = obra.nome if obra else f"Obra {orcamento.obra_id}"
        
        # Notificar operadores da obra
        notificar_operadores_obra(
            obra_id=orcamento.obra_id,
            tipo='orcamento_rejeitado',
            titulo='Orçamento rejeitado',
            mensagem=f'O orçamento "{orcamento.descricao}" foi rejeitado por {user.username}',
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar administradores
        notificar_administradores(
            tipo='orcamento_rejeitado',
            titulo='Orçamento rejeitado',
            mensagem=f'O orçamento "{orcamento.descricao}" foi rejeitado na obra {obra_nome}',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        # Notificar masters
        notificar_masters(
            tipo='orcamento_rejeitado',
            titulo='Orçamento rejeitado',
            mensagem=f'{user.username} rejeitou o orçamento "{orcamento.descricao}" na obra {obra_nome}',
            obra_id=orcamento.obra_id,
            item_id=orcamento.id,
            item_type='orcamento',
            usuario_origem_id=user.id
        )
        
        logger.info(f"--- [LOG] Orçamento {orcamento_id} marcado como Rejeitado ---")
        return jsonify({"sucesso": "Orçamento rejeitado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /orcamentos/{orcamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# ---------------------------------------------------

# <--- MUDANÇA: Novas Rotas para Anexos ---
@obras_bp.route('/orcamentos/<int:orcamento_id>/anexos', methods=['GET', 'OPTIONS'])
@check_permission(roles=['administrador', 'master', 'comum'])
def get_orcamento_anexos(orcamento_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /orcamentos/{orcamento_id}/anexos (GET) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        anexos = AnexoOrcamento.query.filter_by(orcamento_id=orcamento_id).all()
        return jsonify([anexo.to_dict() for anexo in anexos]), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /orcamentos/{orcamento_id}/anexos (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@obras_bp.route('/orcamentos/<int:orcamento_id>/anexos', methods=['POST', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def add_anexos_orcamento(orcamento_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /orcamentos/{orcamento_id}/anexos (POST) acessada ---")
    try:
        user = get_current_user()
        orcamento = Orcamento.query.get_or_404(orcamento_id)
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403

        files = request.files.getlist('anexos')
        novos_anexos = []
        for file in files:
            if file and file.filename:
                novo_anexo = AnexoOrcamento(
                    orcamento_id=orcamento.id,
                    filename=file.filename,
                    mimetype=file.mimetype,
                    data=file.read()
                )
                db.session.add(novo_anexo)
                novos_anexos.append(novo_anexo)
        
        db.session.commit()
        
        return jsonify([anexo.to_dict() for anexo in novos_anexos]), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /orcamentos/{orcamento_id}/anexos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/anexos/<int:anexo_id>', methods=['GET', 'OPTIONS'])
@jwt_required()
def get_anexo_data(anexo_id):
    # ... (código inalterado) ...
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)

    logger.info(f"--- [LOG] Rota /anexos/{anexo_id} (GET) acessada ---")
    try:
        user = get_current_user()
        anexo = AnexoOrcamento.query.get_or_404(anexo_id)
        orcamento = Orcamento.query.get(anexo.orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        return send_file(
            io.BytesIO(anexo.data),
            mimetype=anexo.mimetype,
            as_attachment=False, 
            download_name=anexo.filename 
        )
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /anexos/{anexo_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@obras_bp.route('/anexos/<int:anexo_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['administrador', 'master'])
def delete_anexo(anexo_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /anexos/{anexo_id} (DELETE) acessada ---")
    try:
        user = get_current_user()
        anexo = AnexoOrcamento.query.get_or_404(anexo_id)
        orcamento = Orcamento.query.get(anexo.orcamento_id)
        
        if not user_has_access_to_obra(user, orcamento.obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
            
        db.session.delete(anexo)
        db.session.commit()
        return jsonify({"sucesso": "Anexo deletado"}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /anexos/{anexo_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# ---------------------------------------------------


# --- ROTAS DE EXPORTAÇÃO (PROTEGIDAS) ---
@obras_bp.route('/obras/<int:obra_id>/export/csv', methods=['GET', 'OPTIONS'])
@jwt_required() 
def export_csv(obra_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    if request.method == 'OPTIONS': return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    logger.info(f"--- [LOG] Rota /export/csv (GET) para obra_id={obra_id} ---")
    try:
        verify_jwt_in_request() 
        user = get_current_user()
        if not user or not user_has_access_to_obra(user, obra_id):
           logger.warning(f"--- [AVISO] Tentativa de export CSV sem permissão ou token (obra_id={obra_id}) ---")
           pass
        obra = Obra.query.get_or_404(obra_id)
        items = obra.lancamentos
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['Data', 'Descricao', 'Tipo', 'ValorTotal', 'ValorPago', 'Status', 'PIX', 'ServicoID', 'Fornecedor'])
        for item in items:
            cw.writerow([
                item.data.isoformat(), item.descricao, item.tipo,
                item.valor_total, item.valor_pago, item.status, item.pix, item.servico_id,
                item.fornecedor
            ])
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename=relatorio_obra_{obra_id}.csv"
        output.headers["Content-type"] = "text/csv"
        return output
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /export/csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# MUDANÇA 4: Endpoint removido - Relatório de pendências substituído pelo Cronograma Financeiro
# @obras_bp.route('/obras/<int:obra_id>/export/pdf_pendentes', methods=['GET', 'OPTIONS'])
# @jwt_required() 
def export_pdf_pendentes_DESATIVADO(obra_id):
    # ... (código atualizado para valor_total/valor_pago) ...
    if request.method == 'OPTIONS': return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    logger.info(f"--- [LOG] Rota /export/pdf_pendentes (GET) para obra_id={obra_id} ---")
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        obra = Obra.query.get_or_404(obra_id)
        
        lancamentos_apagar = Lancamento.query.filter(
            Lancamento.obra_id == obra.id, 
            Lancamento.valor_pago < Lancamento.valor_total
        ).all()
        
        pagamentos_servico_apagar = PagamentoServico.query.join(Servico).filter(
            Servico.obra_id == obra.id,
            PagamentoServico.valor_pago < PagamentoServico.valor_total
        ).all()
        
        items = []
        for lanc in lancamentos_apagar:
            desc = lanc.descricao
            if lanc.servico:
                desc = f"{desc} (Serviço: {lanc.servico.nome})"
            items.append({
                "data": lanc.data, "tipo": lanc.tipo, "descricao": desc,
                "valor": lanc.valor_total - lanc.valor_pago,
                "pix": lanc.pix,
                "prioridade": lanc.prioridade 
            })
            
        for pag in pagamentos_servico_apagar:
            desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
            items.append({
                "data": pag.data, "tipo": "Serviço", 
                "descricao": f"Pag. {desc_tipo}: {pag.servico.nome}",
                "valor": pag.valor_total - pag.valor_pago,
                "pix": pag.pix if pag.pix else '-',  # Usar PIX do pagamento
                "prioridade": pag.prioridade 
            })
            
        items.sort(key=lambda x: (-x.get('prioridade', 0), x['data'] if x['data'] else datetime.date(1900, 1, 1)))

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm, leftMargin=2*cm, rightMargin=2*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        title_text = f"<b>Relatorio de Pagamentos Pendentes</b><br/><br/>Obra: {obra.nome}<br/>Cliente: {obra.cliente or 'N/A'}"
        title = Paragraph(title_text, styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 1*cm))
        
        if not items:
            elements.append(Paragraph("Nenhum pagamento pendente nesta obra.", styles['Normal']))
        else:
            data = [['Prior.', 'Data', 'Tipo', 'Descricao', 'Valor Restante', 'PIX']]
            total_pendente = 0
            for item in items:
                data.append([
                    item.get('prioridade', 0), 
                    item['data'].strftime('%d/%m/%Y'), item['tipo'][:15] if item['tipo'] else 'N/A',
                    item['descricao'][:35] if item['descricao'] else 'N/A', 
                    formatar_real(item['valor']),
                    (item['pix'] or 'Nao informado')[:20]
                ])
                total_pendente += item['valor']
            
            data.append(['', '', '', '', 'TOTAL A PAGAR', formatar_real(total_pendente)])
            
            table = Table(data, colWidths=[1.5*cm, 2.5*cm, 3*cm, 5.5*cm, 3*cm, 3.5*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#007bff')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke), ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12), ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white), ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'), ('ALIGN', (4, 1), (4, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'), ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#dc3545')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('FONTSIZE', (0, -1), (-1, -1), 11),
                ('ALIGN', (3, -1), (4, -1), 'RIGHT'), 
            ]))
            elements.append(table)
        
        elements.append(Spacer(1, 1*cm))
        data_geracao = f"Gerado em: {datetime.now().strftime('%d/%m/%Y as %H:%M')}"
        elements.append(Paragraph(data_geracao, styles['Normal']))
        
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=pagamentos_pendentes_obra_{obra.id}.pdf'
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.info(f"=" * 80)
        logger.error(f"ERRO ao gerar PDF para obra_id={obra_id}")
        logger.error(f"Erro: {str(e)}")
        logger.info(f"Traceback completo:")
        logger.error(error_details)
        logger.info(f"=" * 80)
        return jsonify({ "erro": "Erro ao gerar PDF", "mensagem": str(e), "obra_id": obra_id }), 500
        
# MUDANÇA 4: Endpoint removido - Relatório de pendências substituído pelo Cronograma Financeiro
# @obras_bp.route('/export/pdf_pendentes_todas_obras', methods=['GET', 'OPTIONS'])
# @jwt_required() 
def export_pdf_pendentes_todas_obras_DESATIVADO():
    # ... (código atualizado para valor_total/valor_pago) ...
    if request.method == 'OPTIONS': 
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info("--- [LOG] Rota /export/pdf_pendentes_todas_obras (GET) acessada ---")
    
    try:
        user = get_current_user()
        if not user:
            return jsonify({"erro": "Usuário não encontrado"}), 404
        
        prioridade_filtro = request.args.get('prioridade')
        logger.info(f"--- [LOG] Filtro de prioridade recebido: {prioridade_filtro} ---")
        
        titulo_relatorio = "<b>Relatório de Pagamentos Pendentes - Todas as Obras</b>"
        if prioridade_filtro and prioridade_filtro != 'todas':
            titulo_relatorio = f"<b>Relatório de Pendências (Prioridade {prioridade_filtro}) - Todas as Obras</b>"
        
        
        if user.role == 'administrador':
            obras = Obra.query.order_by(Obra.nome).all()
        else:
            obras = user.obras_permitidas
        
        if not obras:
            return jsonify({"erro": "Nenhuma obra encontrada"}), 404
        
        obras_com_pendencias = []
        total_geral_pendente = 0.0
        
        for obra in obras:
            
            lancamentos_query = Lancamento.query.filter(
                Lancamento.obra_id == obra.id, 
                Lancamento.valor_pago < Lancamento.valor_total
            )
            
            pagamentos_query = PagamentoServico.query.join(Servico).filter(
                Servico.obra_id == obra.id,
                PagamentoServico.valor_pago < PagamentoServico.valor_total
            )

            if prioridade_filtro and prioridade_filtro != 'todas':
                try:
                    p_int = int(prioridade_filtro)
                    lancamentos_query = lancamentos_query.filter_by(prioridade=p_int)
                    pagamentos_query = pagamentos_query.filter_by(prioridade=p_int)
                except ValueError:
                    pass 
            
            lancamentos_apagar = lancamentos_query.all()
            pagamentos_servico_apagar = pagamentos_query.all()
            
            items = []
            
            for lanc in lancamentos_apagar:
                desc = lanc.descricao
                if lanc.servico:
                    desc = f"{desc} (Serviço: {lanc.servico.nome})"
                items.append({
                    "data": lanc.data, 
                    "tipo": lanc.tipo, 
                    "descricao": desc,
                    "valor": lanc.valor_total - lanc.valor_pago,
                    "pix": lanc.pix,
                    "prioridade": lanc.prioridade 
                })
            
            for pag in pagamentos_servico_apagar:
                desc_tipo = "Mão de Obra" if pag.tipo_pagamento == 'mao_de_obra' else "Material"
                items.append({
                    "data": pag.data, 
                    "tipo": "Serviço", 
                    "descricao": f"Pag. {desc_tipo}: {pag.servico.nome}",
                    "valor": pag.valor_total - pag.valor_pago,
                    "pix": pag.pix if pag.pix else '-',  # Usar PIX do pagamento
                    "prioridade": pag.prioridade
                })
            
            if items:
                items.sort(key=lambda x: (-x.get('prioridade', 0), x['data'] if x['data'] else datetime.date(1900, 1, 1)))
                total_obra = sum(item['valor'] for item in items)
                total_geral_pendente += total_obra
                
                obras_com_pendencias.append({
                    "obra": obra,
                    "items": items,
                    "total": total_obra
                })
        
        if not obras_com_pendencias:
            return jsonify({"mensagem": "Nenhuma pendência encontrada para este filtro"}), 200
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, 
            pagesize=A4, 
            topMargin=2*cm, 
            bottomMargin=2*cm, 
            leftMargin=2*cm, 
            rightMargin=2*cm
        )
        elements = []
        styles = getSampleStyleSheet()
        
        title_text = f"{titulo_relatorio}<br/><br/>Total de Obras com Pendências: {len(obras_com_pendencias)}"
        title = Paragraph(title_text, styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 0.8*cm))
        
        for idx, obra_data in enumerate(obras_com_pendencias):
            obra = obra_data['obra']
            items = obra_data['items']
            total_obra = obra_data['total']
            
            obra_header = f"<b>Obra: {obra.nome}</b>"
            if obra.cliente:
                obra_header += f" | Cliente: {obra.cliente}"
            obra_header += f" | Total: {formatar_real(total_obra)}"
            
            elements.append(Paragraph(obra_header, styles['Heading2']))
            elements.append(Spacer(1, 0.3*cm))
            
            data = [['Prior.', 'Data', 'Tipo', 'Descrição', 'Valor Restante', 'PIX']]
            
            for item in items:
                data.append([
                    item.get('prioridade', 0), 
                    item['data'].strftime('%d/%m/%Y') if item['data'] else 'N/A',
                    item['tipo'][:15] if item['tipo'] else 'N/A',
                    item['descricao'][:30] if item['descricao'] else 'N/A',
                    formatar_real(item['valor']),
                    (item['pix'] or 'Não informado')[:15]
                ])
            
            data.append(['', '', '', '', 'SUBTOTAL', formatar_real(total_obra)])
            
            table = Table(data, colWidths=[1.5*cm, 2.5*cm, 2.5*cm, 5*cm, 2.5*cm, 3*cm])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
                ('TOPPADDING', (0, 0), (-1, 0), 10),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (4, 1), (4, -1), 'RIGHT'), 
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#10b981')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 10),
                ('ALIGN', (3, -1), (4, -1), 'RIGHT'), 
            ]))
            elements.append(table)
            
            if idx < len(obras_com_pendencias) - 1:
                elements.append(Spacer(1, 0.8*cm))
        
        elements.append(Spacer(1, 1*cm))
        total_geral_text = f"<b>TOTAL GERAL A PAGAR: {formatar_real(total_geral_pendente)}</b>"
        total_geral_para = Paragraph(total_geral_text, styles['Heading1'])
        elements.append(total_geral_para)
        
        elements.append(Spacer(1, 0.5*cm))
        data_geracao = f"Gerado em: {datetime.now().strftime('%d/%m/%Y às %H:%M')}"
        elements.append(Paragraph(data_geracao, styles['Normal']))
        
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=pagamentos_pendentes_todas_obras.pdf'
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.info(f"=" * 80)
        logger.error(f"ERRO ao gerar PDF de todas as obras")
        logger.error(f"Erro: {str(e)}")
        logger.info(f"Traceback completo:")
        logger.error(error_details)
        logger.info(f"=" * 80)
        return jsonify({
            "erro": "Erro ao gerar PDF",
            "mensagem": str(e)
        }), 500

@obras_bp.route('/obras/<int:obra_id>/notas-fiscais', methods=['POST', 'OPTIONS'])
@jwt_required()
def upload_nota_fiscal(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/notas-fiscais (POST) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        
        if 'file' not in request.files:
            return jsonify({"erro": "Nenhum arquivo enviado"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"erro": "Nome do arquivo vazio"}), 400
        
        item_id = request.form.get('item_id')
        item_type = request.form.get('item_type')
        
        if not item_id or not item_type:
            return jsonify({"erro": "item_id e item_type são obrigatórios"}), 400
        
        file_data = file.read()
        
        nota_fiscal = NotaFiscal(
            obra_id=obra_id,
            filename=file.filename,
            mimetype=file.mimetype,
            data=file_data,
            item_id=int(item_id),
            item_type=item_type
        )
        
        db.session.add(nota_fiscal)
        db.session.commit()
        
        logger.info(f"--- [LOG] Nota fiscal '{file.filename}' anexada ao item {item_type}:{item_id} da obra {obra_id} ---")
        return jsonify(nota_fiscal.to_dict()), 201
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/notas-fiscais (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/obras/<int:obra_id>/notas-fiscais', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_notas_fiscais(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/notas-fiscais (GET) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        notas = NotaFiscal.query.filter_by(obra_id=obra_id).all()
        return jsonify([nota.to_dict() for nota in notas]), 200
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/notas-fiscais (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/notas-fiscais/<int:nf_id>', methods=['GET', 'OPTIONS'])
@jwt_required()
def download_nota_fiscal(nf_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /notas-fiscais/{nf_id} (GET) acessada ---")
    try:
        nota = NotaFiscal.query.get_or_404(nf_id)
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, nota.obra_id):
            return jsonify({"erro": "Acesso negado a esta nota fiscal."}), 403
        
        return send_file(
            io.BytesIO(nota.data),
            mimetype=nota.mimetype,
            as_attachment=True,
            download_name=nota.filename
        )
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /notas-fiscais/{nf_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/notas-fiscais/<int:nf_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_nota_fiscal(nf_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /notas-fiscais/{nf_id} (DELETE) acessada ---")
    try:
        nota = NotaFiscal.query.get_or_404(nf_id)
        
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, nota.obra_id):
            return jsonify({"erro": "Acesso negado a esta nota fiscal."}), 403
        
        if current_user.role not in ['administrador', 'master']:
            return jsonify({"erro": "Apenas administradores e masters podem excluir notas fiscais"}), 403
        
        db.session.delete(nota)
        db.session.commit()
        
        logger.info(f"--- [LOG] Nota fiscal {nf_id} deletada ---")
        return jsonify({"sucesso": "Nota fiscal deletada com sucesso"}), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /notas-fiscais/{nf_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DAS ROTAS DE NOTAS FISCAIS ---


# --- ROTAS DE RELATÓRIOS ---
@obras_bp.route('/obras/<int:obra_id>/notas-fiscais/export/zip', methods=['GET', 'OPTIONS'])
@jwt_required()
def export_notas_fiscais_zip(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/notas-fiscais/export/zip (GET) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        notas = NotaFiscal.query.filter_by(obra_id=obra_id).all()
        
        if not notas:
            return jsonify({"erro": "Nenhuma nota fiscal encontrada para esta obra"}), 404
        
        # Criar ZIP em memória
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for idx, nota in enumerate(notas, 1):
                # Nome do arquivo com prefixo para organização
                filename = f"{idx:03d}_{nota.filename}"
                zip_file.writestr(filename, nota.data)
        
        zip_buffer.seek(0)
        
        response = make_response(zip_buffer.read())
        response.headers['Content-Type'] = 'application/zip'
        response.headers['Content-Disposition'] = f'attachment; filename=notas_fiscais_{obra.nome.replace(" ", "_")}.zip'
        
        logger.info(f"--- [LOG] ZIP com {len(notas)} notas fiscais gerado para obra {obra_id} ---")
        return response
    
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/notas-fiscais/export/zip (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/obras/<int:obra_id>/relatorio/resumo-completo', methods=['GET', 'OPTIONS'])
@jwt_required()
def relatorio_resumo_completo(obra_id):
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/relatorio/resumo-completo (GET) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        
        # Buscar todos os dados necessários
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).all()
        servicos = Servico.query.filter_by(obra_id=obra_id).options(joinedload(Servico.pagamentos)).all()
        orcamentos = Orcamento.query.filter_by(obra_id=obra_id).all()
        
        # CORREÇÃO: Buscar também PagamentoFuturo e Parcelas
        pagamentos_futuros = PagamentoFuturo.query.filter(PagamentoFuturo.obra_id == obra_id, PagamentoFuturo.status.in_(['Previsto', 'Pendente'])).all()
        parcelas_previstas = db.session.query(ParcelaIndividual).join(
            PagamentoParcelado
        ).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).all()
        
        # Calcular sumários
        orcamento_total_lancamentos = sum((l.valor_total or 0) for l in lancamentos)
        
        orcamento_total_servicos = sum(
            (s.valor_global_mao_de_obra or 0) + (s.valor_global_material or 0)
            for s in servicos
        )
        
        # Calcular pagamentos futuros/parcelas COM serviço
        futuros_com_servico = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is not None)
        parcelas_com_servico = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                   if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is not None)
        
        # Orçamento total inclui serviços + pagamentos COM serviço
        orcamento_total = orcamento_total_servicos + futuros_com_servico + parcelas_com_servico
        
        valores_pagos_lancamentos = sum((l.valor_pago or 0) for l in lancamentos)
        valores_pagos_servicos = sum(
            sum((p.valor_pago or 0) for p in s.pagamentos)
            for s in servicos
        )
        valores_pagos = valores_pagos_lancamentos + valores_pagos_servicos
        
        # Despesas extras = futuros/parcelas SEM serviço
        despesas_extras_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is None)
        despesas_extras_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                       if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is None)
        
        despesas_extras_total = despesas_extras_futuros + despesas_extras_parcelas
        custo_real_previsto = orcamento_total + despesas_extras_total
        falta_pagar = custo_real_previsto - valores_pagos
        
        # Criar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Título
        titulo = f"<b>RESUMO COMPLETO DA OBRA</b><br/>{obra.nome}"
        elements.append(Paragraph(titulo, styles['Title']))
        elements.append(Spacer(1, 0.5*cm))
        
        # Informações da Obra
        info_text = f"<b>Cliente:</b> {obra.cliente or 'N/A'}<br/>"
        info_text += f"<b>Data de Geração:</b> {datetime.now().strftime('%d/%m/%Y às %H:%M')}"
        elements.append(Paragraph(info_text, styles['Normal']))
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 1: RESUMO FINANCEIRO COMPLETO ===
        elements.append(Paragraph("<b>1. RESUMO FINANCEIRO COMPLETO</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        # Subtítulo: Orçamento e Custos
        elements.append(Paragraph("<b>ORÇAMENTO E CUSTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        data_orcamento = [
            ['Descrição', 'Valor'],
            ['Orçamento Original (Serviços)', formatar_real(orcamento_total)],
            ['Despesas Extras (Fora da Planilha)', formatar_real(despesas_extras_total)],
        ]
        
        table_orcamento = Table(data_orcamento, colWidths=[10*cm, 6*cm])
        table_orcamento.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_orcamento)
        
        # Linha de total (Custo Real Previsto)
        data_custo_real = [
            ['CUSTO REAL PREVISTO', formatar_real(custo_real_previsto)]
        ]
        table_custo_real = Table(data_custo_real, colWidths=[10*cm, 6*cm])
        table_custo_real.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_custo_real)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Situação de Pagamentos
        elements.append(Paragraph("<b>SITUAÇÃO DE PAGAMENTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        # Calcular liberado (TODAS as parcelas/pagamentos previstos, com ou sem serviço)
        liberado_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros)
        liberado_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas)
        liberado_pagamento = liberado_futuros + liberado_parcelas
        
        data_pagamentos = [
            ['Descrição', 'Valor'],
            ['Valores Já Pagos', formatar_real(valores_pagos)],
            ['Liberado p/ Pagamento (Previsto)', formatar_real(liberado_pagamento)],
        ]
        
        table_pagamentos = Table(data_pagamentos, colWidths=[10*cm, 6*cm])
        table_pagamentos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_pagamentos)
        
        # Linha de total (Falta Pagar)
        data_falta = [
            ['FALTA PAGAR PARA CONCLUIR', formatar_real(falta_pagar)]
        ]
        table_falta = Table(data_falta, colWidths=[10*cm, 6*cm])
        table_falta.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_falta)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Análise de Execução
        elements.append(Paragraph("<b>ANÁLISE DE EXECUÇÃO</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        perc_executado = (valores_pagos / custo_real_previsto * 100) if custo_real_previsto > 0 else 0
        perc_sobre_orcamento = (valores_pagos / orcamento_total * 100) if orcamento_total > 0 else 0
        variacao_extras = (despesas_extras_total / orcamento_total * 100) if orcamento_total > 0 else 0
        
        data_analise = [
            ['Indicador', 'Valor'],
            ['Percentual Executado (sobre custo real)', f"{perc_executado:.1f}%"],
            ['Percentual sobre Orçamento Original', f"{perc_sobre_orcamento:.1f}%"],
            ['Variação (Despesas Extras)', f"+{variacao_extras:.1f}%"],
        ]
        
        table_analise = Table(data_analise, colWidths=[10*cm, 6*cm])
        table_analise.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_analise)
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 2: SERVIÇOS ===
        elements.append(Paragraph("<b>2. SERVIÇOS (EMPREITADAS)</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        if servicos:
            for serv in servicos:
                elements.append(Paragraph(f"<b>{serv.nome}</b>", styles['Heading3']))
                
                valor_global_mo = serv.valor_global_mao_de_obra or 0
                valor_global_mat = serv.valor_global_material or 0
                valor_global_total = valor_global_mo + valor_global_mat
                
                pagamentos_mo = [p for p in serv.pagamentos if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_mat = [p for p in serv.pagamentos if p.tipo_pagamento == 'material']
                
                valor_pago_mo = sum((p.valor_pago or 0) for p in pagamentos_mo)
                valor_pago_mat = sum((p.valor_pago or 0) for p in pagamentos_mat)
                valor_pago_total = valor_pago_mo + valor_pago_mat
                
                percentual_mo = (valor_pago_mo / valor_global_mo * 100) if valor_global_mo > 0 else 0
                percentual_mat = (valor_pago_mat / valor_global_mat * 100) if valor_global_mat > 0 else 0
                percentual_total = (valor_pago_total / valor_global_total * 100) if valor_global_total > 0 else 0
                
                status = "✓ PAGO 100%" if percentual_total >= 99.9 else f"⏳ EM ANDAMENTO ({percentual_total:.1f}%)"
                
                data_servico = [
                    ['', 'Orçado', 'Pago', '% Executado'],
                    ['Mão de Obra', formatar_real(valor_global_mo), formatar_real(valor_pago_mo), f"{percentual_mo:.1f}%"],
                    ['Material', formatar_real(valor_global_mat), formatar_real(valor_pago_mat), f"{percentual_mat:.1f}%"],
                    ['TOTAL', formatar_real(valor_global_total), formatar_real(valor_pago_total), f"{percentual_total:.1f}%"]
                ]
                
                table_servico = Table(data_servico, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
                table_servico.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                    ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0f0f0')),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ]))
                elements.append(table_servico)
                elements.append(Paragraph(f"<b>Status:</b> {status}", styles['Normal']))
                elements.append(Spacer(1, 0.5*cm))
        else:
            elements.append(Paragraph("Nenhum serviço cadastrado.", styles['Normal']))
            elements.append(Spacer(1, 0.5*cm))
        
        # === SEÇÃO 3: PENDÊNCIAS VENCIDAS ===
        elements.append(Paragraph("<b>3. PENDÊNCIAS VENCIDAS ⚠️</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        hoje = date.today()
        
        pendencias_lanc_vencidas = []
        pendencias_lanc_a_pagar = []
        
        for l in lancamentos:
            if (l.valor_total or 0) > (l.valor_pago or 0):
                if l.data_vencimento and l.data_vencimento < hoje:
                    pendencias_lanc_vencidas.append(l)
                else:
                    pendencias_lanc_a_pagar.append(l)
        
        pendencias_serv_vencidas = []
        pendencias_serv_a_pagar = []
        
        for serv in servicos:
            for pag in serv.pagamentos:
                if (pag.valor_total or 0) > (pag.valor_pago or 0):
                    if pag.data_vencimento and pag.data_vencimento < hoje:
                        pendencias_serv_vencidas.append((serv.nome, pag))
                    else:
                        pendencias_serv_a_pagar.append((serv.nome, pag))
        
        total_vencido = 0
        
        if pendencias_lanc_vencidas or pendencias_serv_vencidas:
            data_vencidas = [['Descrição', 'Tipo', 'Valor Pendente']]
            
            for lanc in pendencias_lanc_vencidas:
                valor_pendente = (lanc.valor_total or 0) - (lanc.valor_pago or 0)
                total_vencido += valor_pendente
                data_vencidas.append([
                    lanc.descricao[:40],
                    lanc.tipo,
                    formatar_real(valor_pendente)
                ])
            
            for serv_nome, pag in pendencias_serv_vencidas:
                valor_pendente = (pag.valor_total or 0) - (pag.valor_pago or 0)
                total_vencido += valor_pendente
                tipo_pag_display = pag.tipo_pagamento.replace('_', ' ').title() if pag.tipo_pagamento else 'Serviço'
                data_vencidas.append([
                    f"{serv_nome} - {tipo_pag_display}"[:40],
                    "Serviço",
                    formatar_real(valor_pendente)
                ])
            
            data_vencidas.append(['', 'TOTAL VENCIDO ⚠️', formatar_real(total_vencido)])
            
            table_vencidas = Table(data_vencidas, colWidths=[9*cm, 3.5*cm, 3.5*cm])
            table_vencidas.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d32f2f')),  # Vermelho escuro
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#ffcdd2')),  # Vermelho claro
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d32f2f')),  # Linha total em vermelho
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ]))
            elements.append(table_vencidas)
        else:
            elements.append(Paragraph("✓ Nenhuma pendência vencida!", styles['Normal']))
        
        elements.append(Spacer(1, 0.5*cm))
        
        # === SEÇÃO 4: PENDÊNCIAS A PAGAR ===
        elements.append(Paragraph("<b>4. PENDÊNCIAS A PAGAR (No Prazo)</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        total_a_pagar = 0
        
        if pendencias_lanc_a_pagar or pendencias_serv_a_pagar:
            data_a_pagar = [['Descrição', 'Tipo', 'Valor Pendente']]
            
            for lanc in pendencias_lanc_a_pagar:
                valor_pendente = (lanc.valor_total or 0) - (lanc.valor_pago or 0)
                total_a_pagar += valor_pendente
                data_a_pagar.append([
                    lanc.descricao[:40],
                    lanc.tipo,
                    formatar_real(valor_pendente)
                ])
            
            for serv_nome, pag in pendencias_serv_a_pagar:
                valor_pendente = (pag.valor_total or 0) - (pag.valor_pago or 0)
                total_a_pagar += valor_pendente
                tipo_pag_display = pag.tipo_pagamento.replace('_', ' ').title() if pag.tipo_pagamento else 'Serviço'
                data_a_pagar.append([
                    f"{serv_nome} - {tipo_pag_display}"[:40],
                    "Serviço",
                    formatar_real(valor_pendente)
                ])
            
            data_a_pagar.append(['', 'TOTAL A PAGAR', formatar_real(total_a_pagar)])
            
            table_a_pagar = Table(data_a_pagar, colWidths=[9*cm, 3.5*cm, 3.5*cm])
            table_a_pagar.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2196f3')),  # Azul
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0f0f0')),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
            ]))
            elements.append(table_a_pagar)
        else:
            elements.append(Paragraph("✓ Nenhuma pendência a pagar no momento!", styles['Normal']))
        
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 5: ORÇAMENTOS ===
        elements.append(Paragraph("<b>5. ORÇAMENTOS</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        if orcamentos:
            # <-- MUDANÇA: Log de debug para verificar status
            logger.debug(f"--- [DEBUG] Total de orçamentos: {len(orcamentos)}")
            for orc in orcamentos:
                logger.debug(f"--- [DEBUG] Orçamento: {orc.descricao} | Status: '{orc.status}'")
            
            orcamentos_pendentes = [o for o in orcamentos if o.status == 'Pendente']
            orcamentos_aprovados = [o for o in orcamentos if o.status == 'Aprovado']
            orcamentos_rejeitados = [o for o in orcamentos if o.status == 'Rejeitado']
            
            logger.debug(f"--- [DEBUG] Pendentes: {len(orcamentos_pendentes)} | Aprovados: {len(orcamentos_aprovados)} | Rejeitados: {len(orcamentos_rejeitados)}")
            
            if orcamentos_pendentes:
                elements.append(Paragraph("<b>5.1. Orçamentos Pendentes de Aprovação</b>", styles['Heading3']))
                data_orc_pend = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_pendentes:
                    data_orc_pend.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_pend = Table(data_orc_pend, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_pend.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f59e0b')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_pend)
                elements.append(Spacer(1, 0.5*cm))
            
            if orcamentos_aprovados:
                elements.append(Paragraph("<b>5.2. Orçamentos Aprovados</b>", styles['Heading3']))
                data_orc_apr = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_aprovados:
                    data_orc_apr.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_apr = Table(data_orc_apr, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_apr.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_apr)
                elements.append(Spacer(1, 0.5*cm))
            
            # <-- NOVO: Seção de Orçamentos Rejeitados
            if orcamentos_rejeitados:
                elements.append(Paragraph("<b>5.3. Orçamentos Rejeitados (Histórico)</b>", styles['Heading3']))
                data_orc_rej = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_rejeitados:
                    data_orc_rej.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_rej = Table(data_orc_rej, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_rej.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_rej)
                elements.append(Spacer(1, 0.5*cm))
        else:
            elements.append(Paragraph("Nenhum orçamento cadastrado.", styles['Normal']))
        
        # Gerar PDF
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=resumo_completo_{obra.nome.replace(" ", "_")}.pdf'
        
        logger.info(f"--- [LOG] Relatório completo gerado para obra {obra_id} ---")
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/relatorio/resumo-completo (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


# --- RELATÓRIO DE PAGAMENTOS PDF ---
@obras_bp.route('/obras/<int:obra_id>/relatorio/pagamentos-pdf', methods=['GET', 'OPTIONS'])
@jwt_required()
def gerar_relatorio_pagamentos_pdf(obra_id):
    """Gera relatório PDF completo com análise financeira da obra"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    logger.info(f"--- [LOG] Rota /obras/{obra_id}/relatorio/pagamentos-pdf (GET) acessada ---")
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        obra = Obra.query.get_or_404(obra_id)
        
        # Buscar todos os dados necessários
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).all()
        servicos = Servico.query.filter_by(obra_id=obra_id).options(joinedload(Servico.pagamentos)).all()
        orcamentos = Orcamento.query.filter_by(obra_id=obra_id).all()
        
        # CORREÇÃO: Buscar também PagamentoFuturo e Parcelas
        pagamentos_futuros = PagamentoFuturo.query.filter(PagamentoFuturo.obra_id == obra_id, PagamentoFuturo.status.in_(['Previsto', 'Pendente'])).all()
        parcelas_previstas = db.session.query(ParcelaIndividual).join(
            PagamentoParcelado
        ).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto'
        ).all()
        
        # Calcular sumários
        orcamento_total_lancamentos = sum((l.valor_total or 0) for l in lancamentos)
        
        orcamento_total_servicos = sum(
            (s.valor_global_mao_de_obra or 0) + (s.valor_global_material or 0)
            for s in servicos
        )
        
        # Calcular pagamentos futuros/parcelas COM serviço
        futuros_com_servico = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is not None)
        parcelas_com_servico = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                   if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is not None)
        
        # Orçamento total inclui serviços + pagamentos COM serviço
        orcamento_total = orcamento_total_servicos + futuros_com_servico + parcelas_com_servico
        
        valores_pagos_lancamentos = sum((l.valor_pago or 0) for l in lancamentos)
        valores_pagos_servicos = sum(
            sum((p.valor_pago or 0) for p in s.pagamentos)
            for s in servicos
        )
        valores_pagos = valores_pagos_lancamentos + valores_pagos_servicos
        
        # Despesas extras = futuros/parcelas SEM serviço
        despesas_extras_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros if pf.servico_id is None)
        despesas_extras_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas 
                                       if db.session.query(PagamentoParcelado).get(p.pagamento_parcelado_id).servico_id is None)
        
        despesas_extras_total = despesas_extras_futuros + despesas_extras_parcelas
        custo_real_previsto = orcamento_total + despesas_extras_total
        falta_pagar = custo_real_previsto - valores_pagos
        
        # Criar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm)
        elements = []
        styles = getSampleStyleSheet()
        
        # Título
        titulo = f"<b>RESUMO COMPLETO DA OBRA</b><br/>{obra.nome}"
        elements.append(Paragraph(titulo, styles['Title']))
        elements.append(Spacer(1, 0.5*cm))
        
        # Informações da Obra
        info_text = f"<b>Cliente:</b> {obra.cliente or 'N/A'}<br/>"
        info_text += f"<b>Data de Geração:</b> {datetime.now().strftime('%d/%m/%Y às %H:%M')}"
        elements.append(Paragraph(info_text, styles['Normal']))
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 1: RESUMO FINANCEIRO COMPLETO ===
        elements.append(Paragraph("<b>1. RESUMO FINANCEIRO COMPLETO</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        # Subtítulo: Orçamento e Custos
        elements.append(Paragraph("<b>ORÇAMENTO E CUSTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        data_orcamento = [
            ['Descrição', 'Valor'],
            ['Orçamento Original (Serviços)', formatar_real(orcamento_total)],
            ['Despesas Extras (Fora da Planilha)', formatar_real(despesas_extras_total)],
        ]
        
        table_orcamento = Table(data_orcamento, colWidths=[10*cm, 6*cm])
        table_orcamento.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_orcamento)
        
        # Linha de total (Custo Real Previsto)
        data_custo_real = [
            ['CUSTO REAL PREVISTO', formatar_real(custo_real_previsto)]
        ]
        table_custo_real = Table(data_custo_real, colWidths=[10*cm, 6*cm])
        table_custo_real.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_custo_real)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Situação de Pagamentos
        elements.append(Paragraph("<b>SITUAÇÃO DE PAGAMENTOS</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        # Calcular liberado (TODAS as parcelas/pagamentos previstos, com ou sem serviço)
        liberado_futuros = sum((pf.valor or 0) for pf in pagamentos_futuros)
        liberado_parcelas = sum((p.valor_parcela or 0) for p in parcelas_previstas)
        liberado_pagamento = liberado_futuros + liberado_parcelas
        
        data_pagamentos = [
            ['Descrição', 'Valor'],
            ['Valores Já Pagos', formatar_real(valores_pagos)],
            ['Liberado p/ Pagamento (Previsto)', formatar_real(liberado_pagamento)],
        ]
        
        table_pagamentos = Table(data_pagamentos, colWidths=[10*cm, 6*cm])
        table_pagamentos.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_pagamentos)
        
        # Linha de total (Falta Pagar)
        data_falta = [
            ['FALTA PAGAR PARA CONCLUIR', formatar_real(falta_pagar)]
        ]
        table_falta = Table(data_falta, colWidths=[10*cm, 6*cm])
        table_falta.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
        ]))
        elements.append(table_falta)
        elements.append(Spacer(1, 0.5*cm))
        
        # Subtítulo: Análise de Execução
        elements.append(Paragraph("<b>ANÁLISE DE EXECUÇÃO</b>", styles['Heading3']))
        elements.append(Spacer(1, 0.2*cm))
        
        perc_executado = (valores_pagos / custo_real_previsto * 100) if custo_real_previsto > 0 else 0
        perc_sobre_orcamento = (valores_pagos / orcamento_total * 100) if orcamento_total > 0 else 0
        variacao_extras = (despesas_extras_total / orcamento_total * 100) if orcamento_total > 0 else 0
        
        data_analise = [
            ['Indicador', 'Valor'],
            ['Percentual Executado (sobre custo real)', f"{perc_executado:.1f}%"],
            ['Percentual sobre Orçamento Original', f"{perc_sobre_orcamento:.1f}%"],
            ['Variação (Despesas Extras)', f"+{variacao_extras:.1f}%"],
        ]
        
        table_analise = Table(data_analise, colWidths=[10*cm, 6*cm])
        table_analise.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4f46e5')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ]))
        elements.append(table_analise)
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 2: SERVIÇOS ===
        elements.append(Paragraph("<b>2. SERVIÇOS (EMPREITADAS)</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        if servicos:
            for serv in servicos:
                elements.append(Paragraph(f"<b>{serv.nome}</b>", styles['Heading3']))
                
                valor_global_mo = serv.valor_global_mao_de_obra or 0
                valor_global_mat = serv.valor_global_material or 0
                valor_global_total = valor_global_mo + valor_global_mat
                
                pagamentos_mo = [p for p in serv.pagamentos if p.tipo_pagamento == 'mao_de_obra']
                pagamentos_mat = [p for p in serv.pagamentos if p.tipo_pagamento == 'material']
                
                valor_pago_mo = sum((p.valor_pago or 0) for p in pagamentos_mo)
                valor_pago_mat = sum((p.valor_pago or 0) for p in pagamentos_mat)
                valor_pago_total = valor_pago_mo + valor_pago_mat
                
                percentual_mo = (valor_pago_mo / valor_global_mo * 100) if valor_global_mo > 0 else 0
                percentual_mat = (valor_pago_mat / valor_global_mat * 100) if valor_global_mat > 0 else 0
                percentual_total = (valor_pago_total / valor_global_total * 100) if valor_global_total > 0 else 0
                
                status = "✓ PAGO 100%" if percentual_total >= 99.9 else f"⏳ EM ANDAMENTO ({percentual_total:.1f}%)"
                
                data_servico = [
                    ['', 'Orçado', 'Pago', '% Executado'],
                    ['Mão de Obra', formatar_real(valor_global_mo), formatar_real(valor_pago_mo), f"{percentual_mo:.1f}%"],
                    ['Material', formatar_real(valor_global_mat), formatar_real(valor_pago_mat), f"{percentual_mat:.1f}%"],
                    ['TOTAL', formatar_real(valor_global_total), formatar_real(valor_pago_total), f"{percentual_total:.1f}%"]
                ]
                
                table_servico = Table(data_servico, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
                table_servico.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                    ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0f0f0')),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ]))
                elements.append(table_servico)
                elements.append(Paragraph(f"<b>Status:</b> {status}", styles['Normal']))
                elements.append(Spacer(1, 0.5*cm))
        else:
            elements.append(Paragraph("Nenhum serviço cadastrado.", styles['Normal']))
            elements.append(Spacer(1, 0.5*cm))
        
        # === SEÇÃO 3: PENDÊNCIAS VENCIDAS ===
        elements.append(Paragraph("<b>3. PENDÊNCIAS VENCIDAS ⚠️</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        hoje = date.today()
        
        pendencias_lanc_vencidas = []
        pendencias_lanc_a_pagar = []
        
        for l in lancamentos:
            if (l.valor_total or 0) > (l.valor_pago or 0):
                if l.data_vencimento and l.data_vencimento < hoje:
                    pendencias_lanc_vencidas.append(l)
                else:
                    pendencias_lanc_a_pagar.append(l)
        
        pendencias_serv_vencidas = []
        pendencias_serv_a_pagar = []
        
        for serv in servicos:
            for pag in serv.pagamentos:
                if (pag.valor_total or 0) > (pag.valor_pago or 0):
                    if pag.data_vencimento and pag.data_vencimento < hoje:
                        pendencias_serv_vencidas.append((serv.nome, pag))
                    else:
                        pendencias_serv_a_pagar.append((serv.nome, pag))
        
        total_vencido = 0
        
        if pendencias_lanc_vencidas or pendencias_serv_vencidas:
            data_vencidas = [['Descrição', 'Tipo', 'Valor Pendente']]
            
            for lanc in pendencias_lanc_vencidas:
                valor_pendente = (lanc.valor_total or 0) - (lanc.valor_pago or 0)
                total_vencido += valor_pendente
                data_vencidas.append([
                    lanc.descricao[:40],
                    lanc.tipo,
                    formatar_real(valor_pendente)
                ])
            
            for serv_nome, pag in pendencias_serv_vencidas:
                valor_pendente = (pag.valor_total or 0) - (pag.valor_pago or 0)
                total_vencido += valor_pendente
                tipo_pag_display = pag.tipo_pagamento.replace('_', ' ').title() if pag.tipo_pagamento else 'Serviço'
                data_vencidas.append([
                    f"{serv_nome} - {tipo_pag_display}"[:40],
                    "Serviço",
                    formatar_real(valor_pendente)
                ])
            
            data_vencidas.append(['', 'TOTAL VENCIDO ⚠️', formatar_real(total_vencido)])
            
            table_vencidas = Table(data_vencidas, colWidths=[9*cm, 3.5*cm, 3.5*cm])
            table_vencidas.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d32f2f')),  # Vermelho escuro
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#ffcdd2')),  # Vermelho claro
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d32f2f')),  # Linha total em vermelho
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ]))
            elements.append(table_vencidas)
        else:
            elements.append(Paragraph("✓ Nenhuma pendência vencida!", styles['Normal']))
        
        elements.append(Spacer(1, 0.5*cm))
        
        # === SEÇÃO 4: PENDÊNCIAS A PAGAR ===
        elements.append(Paragraph("<b>4. PENDÊNCIAS A PAGAR (No Prazo)</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        total_a_pagar = 0
        
        if pendencias_lanc_a_pagar or pendencias_serv_a_pagar:
            data_a_pagar = [['Descrição', 'Tipo', 'Valor Pendente']]
            
            for lanc in pendencias_lanc_a_pagar:
                valor_pendente = (lanc.valor_total or 0) - (lanc.valor_pago or 0)
                total_a_pagar += valor_pendente
                data_a_pagar.append([
                    lanc.descricao[:40],
                    lanc.tipo,
                    formatar_real(valor_pendente)
                ])
            
            for serv_nome, pag in pendencias_serv_a_pagar:
                valor_pendente = (pag.valor_total or 0) - (pag.valor_pago or 0)
                total_a_pagar += valor_pendente
                tipo_pag_display = pag.tipo_pagamento.replace('_', ' ').title() if pag.tipo_pagamento else 'Serviço'
                data_a_pagar.append([
                    f"{serv_nome} - {tipo_pag_display}"[:40],
                    "Serviço",
                    formatar_real(valor_pendente)
                ])
            
            data_a_pagar.append(['', 'TOTAL A PAGAR', formatar_real(total_a_pagar)])
            
            table_a_pagar = Table(data_a_pagar, colWidths=[9*cm, 3.5*cm, 3.5*cm])
            table_a_pagar.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2196f3')),  # Azul
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f0f0f0')),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
            ]))
            elements.append(table_a_pagar)
        else:
            elements.append(Paragraph("✓ Nenhuma pendência a pagar no momento!", styles['Normal']))
        
        elements.append(Spacer(1, 0.8*cm))
        
        # === SEÇÃO 5: ORÇAMENTOS ===
        elements.append(Paragraph("<b>5. ORÇAMENTOS</b>", styles['Heading2']))
        elements.append(Spacer(1, 0.3*cm))
        
        if orcamentos:
            # <-- MUDANÇA: Log de debug para verificar status
            logger.debug(f"--- [DEBUG] Total de orçamentos: {len(orcamentos)}")
            for orc in orcamentos:
                logger.debug(f"--- [DEBUG] Orçamento: {orc.descricao} | Status: '{orc.status}'")
            
            orcamentos_pendentes = [o for o in orcamentos if o.status == 'Pendente']
            orcamentos_aprovados = [o for o in orcamentos if o.status == 'Aprovado']
            orcamentos_rejeitados = [o for o in orcamentos if o.status == 'Rejeitado']
            
            logger.debug(f"--- [DEBUG] Pendentes: {len(orcamentos_pendentes)} | Aprovados: {len(orcamentos_aprovados)} | Rejeitados: {len(orcamentos_rejeitados)}")
            
            if orcamentos_pendentes:
                elements.append(Paragraph("<b>5.1. Orçamentos Pendentes de Aprovação</b>", styles['Heading3']))
                data_orc_pend = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_pendentes:
                    data_orc_pend.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_pend = Table(data_orc_pend, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_pend.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f59e0b')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_pend)
                elements.append(Spacer(1, 0.5*cm))
            
            if orcamentos_aprovados:
                elements.append(Paragraph("<b>5.2. Orçamentos Aprovados</b>", styles['Heading3']))
                data_orc_apr = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_aprovados:
                    data_orc_apr.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_apr = Table(data_orc_apr, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_apr.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10b981')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_apr)
                elements.append(Spacer(1, 0.5*cm))
            
            # <-- NOVO: Seção de Orçamentos Rejeitados
            if orcamentos_rejeitados:
                elements.append(Paragraph("<b>5.3. Orçamentos Rejeitados (Histórico)</b>", styles['Heading3']))
                data_orc_rej = [['Descrição', 'Fornecedor', 'Valor', 'Tipo']]
                for orc in orcamentos_rejeitados:
                    data_orc_rej.append([
                        orc.descricao[:35],
                        orc.fornecedor or 'N/A',
                        formatar_real(orc.valor),
                        orc.tipo
                    ])
                
                table_orc_rej = Table(data_orc_rej, colWidths=[7*cm, 4*cm, 3*cm, 2*cm])
                table_orc_rej.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ef4444')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                elements.append(table_orc_rej)
                elements.append(Spacer(1, 0.5*cm))
        else:
            elements.append(Paragraph("Nenhum orçamento cadastrado.", styles['Normal']))
        
        # Gerar PDF
        doc.build(elements)
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=relatorio_pagamentos_{obra.nome.replace(" ", "_")}.pdf'
        
        logger.info(f"--- [LOG] Relatório de pagamentos (completo) gerado para obra {obra_id} ---")
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /obras/{obra_id}/relatorio/pagamentos-pdf (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# --- FIM DAS ROTAS DE RELATÓRIOS ---


# --- NOVO ENDPOINT: BUSCAR PAGAMENTOS DE SERVIÇO PENDENTES ---
@obras_bp.route('/obras/<int:obra_id>/pagamentos-servico-pendentes', methods=['GET', 'OPTIONS'])
@jwt_required()
def get_pagamentos_servico_pendentes(obra_id):
    """
    Retorna todos os pagamentos de serviço com valor_pago < valor_total
    para exibir no Cronograma Financeiro
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra."}), 403
        
        # Buscar pagamentos de serviço pendentes
        pagamentos_pendentes = db.session.query(PagamentoServico, Servico).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_total > PagamentoServico.valor_pago
        ).all()
        
        resultado = []
        for pagamento, servico in pagamentos_pendentes:
            descricao = pagamento.fornecedor or f"Pagamento - {servico.nome}"
            resultado.append({
                'id': pagamento.id,
                'servico_id': servico.id,
                'servico_nome': servico.nome,
                'descricao': descricao,
                'tipo_pagamento': 'Mão de Obra' if pagamento.tipo_pagamento == 'mao_de_obra' else 'Material',
                'valor_total': pagamento.valor_total,
                'valor_pago': pagamento.valor_pago,
                'valor_restante': pagamento.valor_total - pagamento.valor_pago,
                'data': pagamento.data.isoformat() if pagamento.data else None,
                'prioridade': pagamento.prioridade
            })
        
        return jsonify(resultado), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /pagamentos-servico-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---




# --- NOVO ENDPOINT: EXCLUIR PAGAMENTOS DE SERVIÇO PENDENTES (UMA OBRA) ---
@obras_bp.route('/obras/<int:obra_id>/pagamentos-servico/excluir-todos-pendentes', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_pagamentos_servico_pendentes(obra_id):
    """
    Exclui TODOS os pagamentos de serviço com saldo pendente de uma obra.
    Remove completamente do banco de dados.
    
    ⚠️ ATENÇÃO: Esta operação não pode ser desfeita!
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        
        # Buscar pagamentos de serviço com saldo pendente
        pagamentos = db.session.query(PagamentoServico).join(
            Servico, PagamentoServico.servico_id == Servico.id
        ).filter(
            Servico.obra_id == obra_id,
            PagamentoServico.valor_total > PagamentoServico.valor_pago
        ).all()
        
        if not pagamentos:
            return jsonify({"mensagem": "Nenhum pagamento de serviço pendente encontrado"}), 200
        
        excluidos = []
        valor_total_removido = 0
        
        for pagamento in pagamentos:
            valor_restante = pagamento.valor_total - pagamento.valor_pago
            
            # Buscar nome do serviço
            servico = Servico.query.get(pagamento.servico_id)
            descricao = pagamento.fornecedor or (servico.nome if servico else f"Pagamento ID {pagamento.id}")
            
            excluidos.append({
                'pagamento_id': pagamento.id,
                'servico_id': pagamento.servico_id,
                'descricao': descricao,
                'tipo': pagamento.tipo_pagamento,
                'valor_pendente_removido': valor_restante
            })
            valor_total_removido += valor_restante
            
            # Excluir do banco
            db.session.delete(pagamento)
        
        db.session.commit()
        
        logger.info(f"--- [LOG] {len(excluidos)} pagamentos de serviço pendentes excluídos da obra {obra_id}. Total: R$ {valor_total_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"{len(excluidos)} pagamentos de serviço pendentes excluídos com sucesso",
            "quantidade_excluida": len(excluidos),
            "valor_total_removido": round(valor_total_removido, 2),
            "pagamentos_excluidos": excluidos
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /pagamentos-servico/excluir-todos-pendentes: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT GLOBAL: EXCLUIR PAGAMENTOS DE SERVIÇO PENDENTES (TODAS AS OBRAS) ---
@obras_bp.route('/pagamentos-servico/excluir-todos-pendentes-global', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def excluir_pagamentos_servico_pendentes_global():
    """
    Exclui TODOS os pagamentos de serviço com saldo pendente de TODAS as obras.
    
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
            # Buscar pagamentos de serviço pendentes desta obra
            pagamentos = db.session.query(PagamentoServico).join(
                Servico, PagamentoServico.servico_id == Servico.id
            ).filter(
                Servico.obra_id == obra.id,
                PagamentoServico.valor_total > PagamentoServico.valor_pago
            ).all()
            
            if pagamentos:
                excluidos = []
                valor_total_obra = 0
                
                for pagamento in pagamentos:
                    valor_restante = pagamento.valor_total - pagamento.valor_pago
                    
                    # Buscar nome do serviço
                    servico = Servico.query.get(pagamento.servico_id)
                    descricao = pagamento.fornecedor or (servico.nome if servico else f"Pagamento ID {pagamento.id}")
                    
                    excluidos.append({
                        'pagamento_id': pagamento.id,
                        'descricao': descricao,
                        'tipo': pagamento.tipo_pagamento,
                        'valor_pendente': valor_restante
                    })
                    valor_total_obra += valor_restante
                    
                    # Excluir do banco
                    db.session.delete(pagamento)
                
                total_geral_excluido += len(excluidos)
                total_geral_removido += valor_total_obra
                
                resultado_por_obra.append({
                    'obra_id': obra.id,
                    'obra_nome': obra.nome,
                    'quantidade_excluida': len(excluidos),
                    'valor_removido': round(valor_total_obra, 2),
                    'pagamentos': excluidos
                })
        
        db.session.commit()
        
        logger.info(f"--- [LOG] LIMPEZA GLOBAL PAGAMENTOS: {total_geral_excluido} pagamentos de serviço excluídos em {len(resultado_por_obra)} obras. Total: R$ {total_geral_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"Limpeza de pagamentos concluída! {total_geral_excluido} pagamentos excluídos em {len(resultado_por_obra)} obras",
            "total_obras_processadas": len(obras),
            "obras_com_pendencias": len(resultado_por_obra),
            "total_pagamentos_excluidos": total_geral_excluido,
            "valor_total_removido": round(total_geral_removido, 2),
            "detalhes_por_obra": resultado_por_obra
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /pagamentos-servico/excluir-todos-pendentes-global: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---


# --- NOVO ENDPOINT: LIMPEZA TOTAL (LANÇAMENTOS + PAGAMENTOS DE SERVIÇO) ---
@obras_bp.route('/limpar-tudo-pendente-global', methods=['DELETE'])
@check_permission(roles=['administrador', 'master'])
def limpar_tudo_pendente_global():
    """
    SUPER LIMPEZA: Exclui TODOS os lançamentos E pagamentos de serviço pendentes de TODAS as obras.
    
    Este é o endpoint mais poderoso - limpa TUDO que contribui para "Liberado p/ Pagamento":
    - Lançamentos com saldo pendente
    - Pagamentos de Serviço com saldo pendente
    
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
        total_lancamentos_excluidos = 0
        total_pagamentos_excluidos = 0
        total_valor_removido = 0.0
        
        for obra in obras:
            lancamentos_obra = []
            pagamentos_obra = []
            valor_obra = 0
            
            # 1. Lançamentos pendentes
            lancamentos = Lancamento.query.filter_by(obra_id=obra.id).filter(
                Lancamento.valor_total > Lancamento.valor_pago
            ).all()
            
            for lancamento in lancamentos:
                valor_restante = lancamento.valor_total - lancamento.valor_pago
                lancamentos_obra.append({
                    'id': lancamento.id,
                    'tipo': 'Lançamento',
                    'descricao': lancamento.descricao,
                    'valor': valor_restante
                })
                valor_obra += valor_restante
                db.session.delete(lancamento)
            
            # 2. Pagamentos de Serviço pendentes
            pagamentos = db.session.query(PagamentoServico).join(
                Servico, PagamentoServico.servico_id == Servico.id
            ).filter(
                Servico.obra_id == obra.id,
                PagamentoServico.valor_total > PagamentoServico.valor_pago
            ).all()
            
            for pagamento in pagamentos:
                valor_restante = pagamento.valor_total - pagamento.valor_pago
                
                # Buscar nome do serviço
                servico = Servico.query.get(pagamento.servico_id)
                descricao = pagamento.fornecedor or (servico.nome if servico else f"Pagamento ID {pagamento.id}")
                
                pagamentos_obra.append({
                    'id': pagamento.id,
                    'tipo': 'Pagamento de Serviço',
                    'descricao': descricao,
                    'valor': valor_restante
                })
                valor_obra += valor_restante
                db.session.delete(pagamento)
            
            if lancamentos_obra or pagamentos_obra:
                total_lancamentos_excluidos += len(lancamentos_obra)
                total_pagamentos_excluidos += len(pagamentos_obra)
                total_valor_removido += valor_obra
                
                resultado_por_obra.append({
                    'obra_id': obra.id,
                    'obra_nome': obra.nome,
                    'lancamentos_excluidos': len(lancamentos_obra),
                    'pagamentos_excluidos': len(pagamentos_obra),
                    'total_excluido': len(lancamentos_obra) + len(pagamentos_obra),
                    'valor_removido': round(valor_obra, 2),
                    'detalhes': {
                        'lancamentos': lancamentos_obra,
                        'pagamentos': pagamentos_obra
                    }
                })
        
        db.session.commit()
        
        logger.info(f"--- [LOG] SUPER LIMPEZA: {total_lancamentos_excluidos} lançamentos + {total_pagamentos_excluidos} pagamentos excluídos. Total: R$ {total_valor_removido:.2f} ---")
        
        return jsonify({
            "mensagem": f"SUPER LIMPEZA concluída! {total_lancamentos_excluidos + total_pagamentos_excluidos} itens excluídos em {len(resultado_por_obra)} obras",
            "total_obras_processadas": len(obras),
            "obras_com_pendencias": len(resultado_por_obra),
            "total_lancamentos_excluidos": total_lancamentos_excluidos,
            "total_pagamentos_excluidos": total_pagamentos_excluidos,
            "total_itens_excluidos": total_lancamentos_excluidos + total_pagamentos_excluidos,
            "valor_total_removido": round(total_valor_removido, 2),
            "detalhes_por_obra": resultado_por_obra
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /limpar-tudo-pendente-global: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DO ENDPOINT ---



@obras_bp.route('/popular-servicos-base', methods=['GET', 'POST'])
def popular_servicos_base():
    """
    Popula a base de serviços de referência (executar apenas uma vez)
    GET: Acesso direto pelo navegador (sem autenticação, apenas para setup inicial)
    POST: Acesso autenticado
    """
    try:
        # Verificar se já está populado
        if ServicoBase.query.count() > 0:
            return jsonify({"mensagem": "Base já populada", "total": ServicoBase.query.count()})
        
        # Base de serviços comuns
        servicos = [
            # SERVIÇOS PRELIMINARES
            {"categoria": "preliminares", "descricao": "Limpeza de terreno", "unidade": "m²", "tipo": "composto", "preco": 5.50, "rateio_mo": 80},
            {"categoria": "preliminares", "descricao": "Locação de obra", "unidade": "m²", "tipo": "composto", "preco": 8.00, "rateio_mo": 90},
            {"categoria": "preliminares", "descricao": "Tapume em chapa compensada", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "preliminares", "descricao": "Barracão de obra", "unidade": "m²", "tipo": "composto", "preco": 320.00, "rateio_mo": 40},
            {"categoria": "preliminares", "descricao": "Placa de obra", "unidade": "m²", "tipo": "separado", "mo": 50.00, "mat": 120.00},
            {"categoria": "preliminares", "descricao": "Instalações provisórias (água/luz)", "unidade": "vb", "tipo": "composto", "preco": 2500.00, "rateio_mo": 30},
            
            # FUNDAÇÃO
            {"categoria": "fundacao", "descricao": "Escavação manual até 1,5m", "unidade": "m³", "tipo": "composto", "preco": 65.00, "rateio_mo": 95},
            {"categoria": "fundacao", "descricao": "Escavação mecânica", "unidade": "m³", "tipo": "composto", "preco": 28.00, "rateio_mo": 30},
            {"categoria": "fundacao", "descricao": "Apiloamento manual", "unidade": "m²", "tipo": "composto", "preco": 8.00, "rateio_mo": 100},
            {"categoria": "fundacao", "descricao": "Lastro de concreto magro", "unidade": "m³", "tipo": "separado", "mo": 80.00, "mat": 200.00},
            {"categoria": "fundacao", "descricao": "Forma para sapata", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 20.00},
            {"categoria": "fundacao", "descricao": "Forma para baldrame", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 22.00},
            {"categoria": "fundacao", "descricao": "Armação CA-50", "unidade": "kg", "tipo": "separado", "mo": 4.50, "mat": 8.00},
            {"categoria": "fundacao", "descricao": "Armação CA-60", "unidade": "kg", "tipo": "separado", "mo": 4.50, "mat": 8.50},
            {"categoria": "fundacao", "descricao": "Concreto fck 20 MPa", "unidade": "m³", "tipo": "separado", "mo": 70.00, "mat": 250.00},
            {"categoria": "fundacao", "descricao": "Concreto fck 25 MPa", "unidade": "m³", "tipo": "separado", "mo": 80.00, "mat": 300.00},
            {"categoria": "fundacao", "descricao": "Concreto fck 30 MPa", "unidade": "m³", "tipo": "separado", "mo": 90.00, "mat": 330.00},
            {"categoria": "fundacao", "descricao": "Impermeabilização de baldrame", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 20.00},
            {"categoria": "fundacao", "descricao": "Estaca broca d=25cm", "unidade": "m", "tipo": "separado", "mo": 35.00, "mat": 45.00},
            {"categoria": "fundacao", "descricao": "Estaca broca d=30cm", "unidade": "m", "tipo": "separado", "mo": 40.00, "mat": 55.00},
            
            # ESTRUTURA
            {"categoria": "estrutura", "descricao": "Forma para pilar", "unidade": "m²", "tipo": "separado", "mo": 30.00, "mat": 25.00},
            {"categoria": "estrutura", "descricao": "Forma para viga", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 24.00},
            {"categoria": "estrutura", "descricao": "Forma para laje", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 23.00},
            {"categoria": "estrutura", "descricao": "Escoramento de laje", "unidade": "m²", "tipo": "separado", "mo": 8.00, "mat": 10.00},
            {"categoria": "estrutura", "descricao": "Laje pré-moldada h=12cm", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 60.00},
            {"categoria": "estrutura", "descricao": "Laje pré-moldada h=16cm", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 72.00},
            {"categoria": "estrutura", "descricao": "Laje pré-moldada h=20cm", "unidade": "m²", "tipo": "separado", "mo": 32.00, "mat": 85.00},
            {"categoria": "estrutura", "descricao": "Verga/contraverga concreto", "unidade": "m", "tipo": "separado", "mo": 20.00, "mat": 25.00},
            {"categoria": "estrutura", "descricao": "Cinta de amarração", "unidade": "m", "tipo": "separado", "mo": 18.00, "mat": 22.00},
            
            # ALVENARIA
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco cerâmico 9x19x19", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 30.00},
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco cerâmico 14x19x39", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 40.00},
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco concreto 14x19x39", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 47.00},
            {"categoria": "alvenaria", "descricao": "Alvenaria bloco concreto 19x19x39", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 55.00},
            {"categoria": "alvenaria", "descricao": "Encunhamento de alvenaria", "unidade": "m", "tipo": "separado", "mo": 8.00, "mat": 4.00},
            {"categoria": "alvenaria", "descricao": "Fixação de batente", "unidade": "un", "tipo": "composto", "preco": 85.00, "rateio_mo": 70},
            
            # INSTALAÇÕES HIDRÁULICAS
            {"categoria": "hidraulica", "descricao": "Ponto de água fria PVC", "unidade": "pt", "tipo": "separado", "mo": 85.00, "mat": 100.00},
            {"categoria": "hidraulica", "descricao": "Ponto de água quente CPVC", "unidade": "pt", "tipo": "separado", "mo": 90.00, "mat": 130.00},
            {"categoria": "hidraulica", "descricao": "Ponto de água quente PPR", "unidade": "pt", "tipo": "separado", "mo": 95.00, "mat": 140.00},
            {"categoria": "hidraulica", "descricao": "Ponto de esgoto PVC", "unidade": "pt", "tipo": "separado", "mo": 75.00, "mat": 90.00},
            {"categoria": "hidraulica", "descricao": "Caixa sifonada 100x100", "unidade": "un", "tipo": "separado", "mo": 45.00, "mat": 50.00},
            {"categoria": "hidraulica", "descricao": "Caixa de gordura", "unidade": "un", "tipo": "separado", "mo": 120.00, "mat": 180.00},
            {"categoria": "hidraulica", "descricao": "Caixa de inspeção", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 200.00},
            {"categoria": "hidraulica", "descricao": "Vaso sanitário com caixa acoplada", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 700.00},
            {"categoria": "hidraulica", "descricao": "Lavatório com coluna", "unidade": "un", "tipo": "separado", "mo": 120.00, "mat": 400.00},
            {"categoria": "hidraulica", "descricao": "Tanque de louça", "unidade": "un", "tipo": "separado", "mo": 100.00, "mat": 380.00},
            {"categoria": "hidraulica", "descricao": "Pia de cozinha inox", "unidade": "un", "tipo": "separado", "mo": 120.00, "mat": 450.00},
            {"categoria": "hidraulica", "descricao": "Registro de gaveta 3/4\"", "unidade": "un", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "hidraulica", "descricao": "Registro de pressão 3/4\"", "unidade": "un", "tipo": "separado", "mo": 35.00, "mat": 65.00},
            
            # INSTALAÇÕES ELÉTRICAS
            {"categoria": "eletrica", "descricao": "Ponto de luz", "unidade": "pt", "tipo": "separado", "mo": 55.00, "mat": 70.00},
            {"categoria": "eletrica", "descricao": "Ponto de tomada 2P+T", "unidade": "pt", "tipo": "separado", "mo": 40.00, "mat": 55.00},
            {"categoria": "eletrica", "descricao": "Ponto de tomada alta", "unidade": "pt", "tipo": "separado", "mo": 45.00, "mat": 65.00},
            {"categoria": "eletrica", "descricao": "Ponto de interruptor simples", "unidade": "pt", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "eletrica", "descricao": "Ponto de interruptor duplo", "unidade": "pt", "tipo": "separado", "mo": 40.00, "mat": 60.00},
            {"categoria": "eletrica", "descricao": "Ponto de ar condicionado", "unidade": "pt", "tipo": "separado", "mo": 85.00, "mat": 120.00},
            {"categoria": "eletrica", "descricao": "Ponto de chuveiro elétrico", "unidade": "pt", "tipo": "separado", "mo": 75.00, "mat": 95.00},
            {"categoria": "eletrica", "descricao": "Quadro distribuição 12 circuitos", "unidade": "un", "tipo": "separado", "mo": 200.00, "mat": 450.00},
            {"categoria": "eletrica", "descricao": "Quadro distribuição 24 circuitos", "unidade": "un", "tipo": "separado", "mo": 250.00, "mat": 700.00},
            {"categoria": "eletrica", "descricao": "Ponto de telefone/internet", "unidade": "pt", "tipo": "separado", "mo": 45.00, "mat": 55.00},
            {"categoria": "eletrica", "descricao": "Ponto de TV/antena", "unidade": "pt", "tipo": "separado", "mo": 45.00, "mat": 50.00},
            
            # REVESTIMENTOS
            {"categoria": "revestimento", "descricao": "Chapisco interno", "unidade": "m²", "tipo": "separado", "mo": 5.50, "mat": 3.00},
            {"categoria": "revestimento", "descricao": "Chapisco externo", "unidade": "m²", "tipo": "separado", "mo": 6.50, "mat": 3.50},
            {"categoria": "revestimento", "descricao": "Reboco interno e=2cm", "unidade": "m²", "tipo": "separado", "mo": 22.00, "mat": 10.00},
            {"categoria": "revestimento", "descricao": "Reboco externo e=2,5cm", "unidade": "m²", "tipo": "separado", "mo": 26.00, "mat": 12.00},
            {"categoria": "revestimento", "descricao": "Reboco paulista", "unidade": "m²", "tipo": "separado", "mo": 28.00, "mat": 14.00},
            {"categoria": "revestimento", "descricao": "Gesso liso", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 8.00},
            {"categoria": "revestimento", "descricao": "Forro de gesso", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 20.00},
            {"categoria": "revestimento", "descricao": "Forro de PVC", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 35.00},
            {"categoria": "revestimento", "descricao": "Contrapiso e=5cm", "unidade": "m²", "tipo": "separado", "mo": 22.00, "mat": 26.00},
            {"categoria": "revestimento", "descricao": "Contrapiso e=7cm", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 32.00},
            {"categoria": "revestimento", "descricao": "Piso cerâmico PEI-4", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 60.00},
            {"categoria": "revestimento", "descricao": "Piso cerâmico PEI-5", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 75.00},
            {"categoria": "revestimento", "descricao": "Piso porcelanato polido", "unidade": "m²", "tipo": "separado", "mo": 45.00, "mat": 100.00},
            {"categoria": "revestimento", "descricao": "Piso porcelanato acetinado", "unidade": "m²", "tipo": "separado", "mo": 45.00, "mat": 85.00},
            {"categoria": "revestimento", "descricao": "Azulejo 30x60", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 65.00},
            {"categoria": "revestimento", "descricao": "Rodapé cerâmico h=10cm", "unidade": "m", "tipo": "separado", "mo": 10.00, "mat": 12.00},
            {"categoria": "revestimento", "descricao": "Soleira granito", "unidade": "m", "tipo": "separado", "mo": 35.00, "mat": 60.00},
            {"categoria": "revestimento", "descricao": "Peitoril granito", "unidade": "m", "tipo": "separado", "mo": 30.00, "mat": 55.00},
            {"categoria": "revestimento", "descricao": "Bancada granito", "unidade": "m²", "tipo": "separado", "mo": 80.00, "mat": 350.00},
            
            # PINTURA
            {"categoria": "pintura", "descricao": "Massa corrida PVA", "unidade": "m²", "tipo": "separado", "mo": 12.00, "mat": 6.00},
            {"categoria": "pintura", "descricao": "Massa acrílica", "unidade": "m²", "tipo": "separado", "mo": 14.00, "mat": 8.00},
            {"categoria": "pintura", "descricao": "Pintura látex PVA 2 demãos", "unidade": "m²", "tipo": "separado", "mo": 12.00, "mat": 6.00},
            {"categoria": "pintura", "descricao": "Pintura acrílica 2 demãos", "unidade": "m²", "tipo": "separado", "mo": 14.00, "mat": 8.00},
            {"categoria": "pintura", "descricao": "Pintura acrílica semi-brilho", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 10.00},
            {"categoria": "pintura", "descricao": "Textura acrílica", "unidade": "m²", "tipo": "separado", "mo": 16.00, "mat": 12.00},
            {"categoria": "pintura", "descricao": "Grafiato", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 20.00},
            {"categoria": "pintura", "descricao": "Pintura esmalte em madeira", "unidade": "m²", "tipo": "separado", "mo": 18.00, "mat": 12.00},
            {"categoria": "pintura", "descricao": "Pintura esmalte em ferro", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 15.00},
            {"categoria": "pintura", "descricao": "Verniz em madeira", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 10.00},
            
            # ESQUADRIAS
            {"categoria": "esquadria", "descricao": "Porta madeira 80x210 completa", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 600.00},
            {"categoria": "esquadria", "descricao": "Porta madeira 70x210 completa", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 530.00},
            {"categoria": "esquadria", "descricao": "Porta madeira 60x210 completa", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 480.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio correr 120x120", "unidade": "un", "tipo": "separado", "mo": 150.00, "mat": 700.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio correr 150x120", "unidade": "un", "tipo": "separado", "mo": 180.00, "mat": 850.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio correr 200x120", "unidade": "un", "tipo": "separado", "mo": 200.00, "mat": 1100.00},
            {"categoria": "esquadria", "descricao": "Janela alumínio maxim-ar 60x60", "unidade": "un", "tipo": "separado", "mo": 80.00, "mat": 300.00},
            {"categoria": "esquadria", "descricao": "Porta alumínio correr 200x210", "unidade": "un", "tipo": "separado", "mo": 300.00, "mat": 1500.00},
            {"categoria": "esquadria", "descricao": "Porta alumínio pivotante", "unidade": "un", "tipo": "separado", "mo": 350.00, "mat": 2000.00},
            {"categoria": "esquadria", "descricao": "Box vidro temperado", "unidade": "m²", "tipo": "separado", "mo": 80.00, "mat": 300.00},
            {"categoria": "esquadria", "descricao": "Espelho 4mm", "unidade": "m²", "tipo": "separado", "mo": 50.00, "mat": 120.00},
            
            # COBERTURA
            {"categoria": "cobertura", "descricao": "Estrutura madeira para telha", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 60.00},
            {"categoria": "cobertura", "descricao": "Estrutura metálica para telha", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 80.00},
            {"categoria": "cobertura", "descricao": "Telha cerâmica", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 40.00},
            {"categoria": "cobertura", "descricao": "Telha de concreto", "unidade": "m²", "tipo": "separado", "mo": 25.00, "mat": 45.00},
            {"categoria": "cobertura", "descricao": "Telha fibrocimento 6mm", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 35.00},
            {"categoria": "cobertura", "descricao": "Telha sanduíche", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 110.00},
            {"categoria": "cobertura", "descricao": "Cumeeira cerâmica", "unidade": "m", "tipo": "separado", "mo": 15.00, "mat": 30.00},
            {"categoria": "cobertura", "descricao": "Calha galvanizada", "unidade": "m", "tipo": "separado", "mo": 30.00, "mat": 55.00},
            {"categoria": "cobertura", "descricao": "Rufo galvanizado", "unidade": "m", "tipo": "separado", "mo": 25.00, "mat": 40.00},
            {"categoria": "cobertura", "descricao": "Manta subcobertura", "unidade": "m²", "tipo": "separado", "mo": 8.00, "mat": 12.00},
            
            # IMPERMEABILIZAÇÃO
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização manta asfáltica 3mm", "unidade": "m²", "tipo": "separado", "mo": 35.00, "mat": 50.00},
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização manta asfáltica 4mm", "unidade": "m²", "tipo": "separado", "mo": 40.00, "mat": 65.00},
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização argamassa polimérica", "unidade": "m²", "tipo": "separado", "mo": 20.00, "mat": 25.00},
            {"categoria": "impermeabilizacao", "descricao": "Impermeabilização acrílica", "unidade": "m²", "tipo": "separado", "mo": 15.00, "mat": 18.00},
            
            # LIMPEZA E ACABAMENTO
            {"categoria": "limpeza", "descricao": "Limpeza final da obra", "unidade": "m²", "tipo": "composto", "preco": 8.00, "rateio_mo": 90},
            {"categoria": "limpeza", "descricao": "Remoção de entulho", "unidade": "m³", "tipo": "composto", "preco": 95.00, "rateio_mo": 40},
            {"categoria": "limpeza", "descricao": "Regularização de terreno", "unidade": "m²", "tipo": "composto", "preco": 12.00, "rateio_mo": 80},
        ]
        
        # Inserir serviços
        for s in servicos:
            servico_base = ServicoBase(
                categoria=s['categoria'],
                descricao=s['descricao'],
                unidade=s['unidade'],
                tipo_composicao=s['tipo'],
                preco_mao_obra=s.get('mo'),
                preco_material=s.get('mat'),
                preco_unitario=s.get('preco'),
                rateio_mo=s.get('rateio_mo', 50),
                rateio_mat=100 - s.get('rateio_mo', 50) if s.get('rateio_mo') else 50
            )
            db.session.add(servico_base)
        
        db.session.commit()
        
        return jsonify({
            "mensagem": "Base populada com sucesso",
            "total": len(servicos)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@obras_bp.route('/categorias-servico', methods=['GET'])
@jwt_required()
def listar_categorias():
    """
    Lista todas as categorias de serviços disponíveis
    """
    categorias = [
        {"id": "preliminares", "nome": "Serviços Preliminares", "icone": "🏗️"},
        {"id": "fundacao", "nome": "Fundação", "icone": "🧱"},
        {"id": "estrutura", "nome": "Estrutura", "icone": "🏛️"},
        {"id": "alvenaria", "nome": "Alvenaria", "icone": "🧱"},
        {"id": "hidraulica", "nome": "Instalações Hidráulicas", "icone": "🚿"},
        {"id": "eletrica", "nome": "Instalações Elétricas", "icone": "⚡"},
        {"id": "revestimento", "nome": "Revestimentos", "icone": "🎨"},
        {"id": "pintura", "nome": "Pintura", "icone": "🖌️"},
        {"id": "esquadria", "nome": "Esquadrias", "icone": "🚪"},
        {"id": "cobertura", "nome": "Cobertura", "icone": "🏠"},
        {"id": "impermeabilizacao", "nome": "Impermeabilização", "icone": "💧"},
        {"id": "limpeza", "nome": "Limpeza e Acabamento", "icone": "🧹"},
    ]
    return jsonify(categorias)


