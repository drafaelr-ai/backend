import io
import re
import json
import csv
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
from models.servico import Servico
from models.servico_usuario import ServicoUsuario
from models.servico_base import ServicoBase
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from services import (
    get_current_user,
    user_has_access_to_obra,
)
from utils import formatar_real

logger = logging.getLogger(__name__)
orcamento_eng_bp = Blueprint('orcamento_eng', __name__, url_prefix='/obras/<int:obra_id>/orcamento-eng')

@orcamento_eng_bp.route('/itens-lista', methods=['GET'])
@jwt_required()
def listar_itens_orcamento_simplificado(obra_id):
    """
    Retorna lista simplificada de itens do orçamento para uso em dropdowns.
    Formato: [{ id, codigo, descricao, etapa_nome, total }]
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar todos os itens com suas etapas
        itens = db.session.query(
            OrcamentoEngItem.id,
            OrcamentoEngItem.codigo,
            OrcamentoEngItem.descricao,
            OrcamentoEngEtapa.nome.label('etapa_nome'),
            OrcamentoEngEtapa.codigo.label('etapa_codigo')
        ).join(
            OrcamentoEngEtapa, OrcamentoEngItem.etapa_id == OrcamentoEngEtapa.id
        ).filter(
            OrcamentoEngEtapa.obra_id == obra_id
        ).order_by(
            OrcamentoEngEtapa.ordem, OrcamentoEngItem.ordem
        ).all()
        
        resultado = []
        for item in itens:
            resultado.append({
                'id': item.id,
                'codigo': item.codigo,
                'descricao': item.descricao,
                'etapa_nome': item.etapa_nome,
                'etapa_codigo': item.etapa_codigo,
                'nome_completo': f"{item.codigo} - {item.descricao}"
            })
        
        return jsonify(resultado)
        
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('', methods=['GET'])
@jwt_required()
def obter_orcamento_eng(obra_id):
    """
    Retorna o orçamento de engenharia completo da obra
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        # Verificar permissão - qualquer usuário com acesso à obra pode visualizar
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão para acessar esta obra"}), 403
        
        # Buscar etapas com itens
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).order_by(OrcamentoEngEtapa.ordem, OrcamentoEngEtapa.codigo).all()
        
        # OTIMIZAÇÃO: 4 queries bulk para todos os itens (elimina N+1 queries)
        # Separa MO vs Material pelo tipo real de cada pagamento
        ids_etapas = [e.id for e in etapas]
        if ids_etapas:
            todos_item_ids_rows = db.session.execute(db.text(
                "SELECT id FROM orcamento_eng_item WHERE etapa_id = ANY(:ids)"
            ), {"ids": ids_etapas}).fetchall()
        else:
            todos_item_ids_rows = []
        todos_item_ids = [r[0] for r in todos_item_ids_rows]

        pago_por_item = {}  # {item_id: {'mo': float, 'mat': float}}

        if todos_item_ids:

            # 1. Lançamentos pagos
            for item_id, tipo, valor in db.session.execute(db.text("""
                SELECT orcamento_item_id, tipo, COALESCE(SUM(valor_pago), 0)
                FROM lancamento
                WHERE orcamento_item_id = ANY(:ids) AND status = 'Pago'
                GROUP BY orcamento_item_id, tipo
            """), {"ids": todos_item_ids}).fetchall():
                d = pago_por_item.setdefault(item_id, {'mo': 0.0, 'mat': 0.0})
                if tipo and 'obra' in tipo.lower(): d['mo'] += float(valor or 0)
                else: d['mat'] += float(valor or 0)

            # 2. Pagamentos Futuros pagos
            for item_id, tipo, valor in db.session.execute(db.text("""
                SELECT orcamento_item_id, tipo, COALESCE(SUM(valor), 0)
                FROM pagamento_futuro
                WHERE orcamento_item_id = ANY(:ids) AND status = 'Pago'
                GROUP BY orcamento_item_id, tipo
            """), {"ids": todos_item_ids}).fetchall():
                d = pago_por_item.setdefault(item_id, {'mo': 0.0, 'mat': 0.0})
                if tipo and 'obra' in tipo.lower(): d['mo'] += float(valor or 0)
                else: d['mat'] += float(valor or 0)

            # 3. Parcelas pagas
            for item_id, segmento, valor in db.session.execute(db.text("""
                SELECT pp.orcamento_item_id, pp.segmento, COALESCE(SUM(pi.valor_parcela), 0)
                FROM parcela_individual pi
                JOIN pagamento_parcelado_v2 pp ON pi.pagamento_parcelado_id = pp.id
                WHERE pp.orcamento_item_id = ANY(:ids) AND pi.status = 'Pago'
                GROUP BY pp.orcamento_item_id, pp.segmento
            """), {"ids": todos_item_ids}).fetchall():
                d = pago_por_item.setdefault(item_id, {'mo': 0.0, 'mat': 0.0})
                if segmento and 'obra' in segmento.lower(): d['mo'] += float(valor or 0)
                else: d['mat'] += float(valor or 0)

            # 4. Boletos pagos (sem tipo → Material)
            for item_id, valor in db.session.execute(db.text("""
                SELECT orcamento_item_id, COALESCE(SUM(valor), 0)
                FROM boleto
                WHERE orcamento_item_id = ANY(:ids) AND status = 'Pago'
                GROUP BY orcamento_item_id
            """), {"ids": todos_item_ids}).fetchall():
                pago_por_item.setdefault(item_id, {'mo': 0.0, 'mat': 0.0})['mat'] += float(valor or 0)

        # Calcular totais
        total_mo = 0
        total_mat = 0
        total_servico = 0
        total_pago_mo = 0
        total_pago_mat = 0
        total_pago_servico = 0
        total_itens = 0
        itens_vinculados = 0
        
        etapas_dict = []
        for etapa in etapas:
            etapa_mo = 0
            etapa_mat = 0
            etapa_servico = 0
            etapa_pago_mo = 0
            etapa_pago_mat = 0
            
            itens_dict = []
            for item in etapa.itens:
                totais = item.calcular_totais()
                etapa_mo += totais['total_mao_obra']
                etapa_mat += totais['total_material']
                etapa_servico += totais.get('total_servico', 0)

                pago = pago_por_item.get(item.id, {'mo': 0.0, 'mat': 0.0})
                item_pago_mo = pago['mo']
                item_pago_mat = pago['mat']
                item_pago = item_pago_mo + item_pago_mat
                etapa_pago_mo += item_pago_mo
                etapa_pago_mat += item_pago_mat

                total_itens += 1
                if item.servico_id:
                    itens_vinculados += 1
                
                item_dict = item.to_dict()
                item_dict['total_pago'] = item_pago
                item_dict['valor_pago_mo'] = item_pago_mo
                item_dict['valor_pago_mat'] = item_pago_mat
                item_dict['percentual_executado'] = round((item_pago / totais['total'] * 100) if totais['total'] > 0 else 0, 1)
                itens_dict.append(item_dict)
            
            total_mo += etapa_mo
            total_mat += etapa_mat
            total_servico += etapa_servico
            etapa_total = etapa_mo + etapa_mat + etapa_servico
            etapa_pago = etapa_pago_mo + etapa_pago_mat

            etapa_pago_servico = (etapa_pago * (etapa_servico / etapa_total)) if etapa_total > 0 and etapa_servico > 0 else 0
            
            total_pago_mo += etapa_pago_mo
            total_pago_mat += etapa_pago_mat
            total_pago_servico += etapa_pago_servico
            
            etapas_dict.append({
                **etapa.to_dict(include_itens=False),
                'itens': itens_dict,
                'total_mao_obra': etapa_mo,
                'total_material': etapa_mat,
                'total_servico': etapa_servico,  # NOVO
                'total': etapa_total,
                'total_pago_mo': etapa_pago_mo,
                'total_pago_mat': etapa_pago_mat,
                'total_pago_servico': etapa_pago_servico,  # NOVO
                'total_pago': etapa_pago,
                'percentual': round((etapa_pago / etapa_total * 100) if etapa_total > 0 else 0, 1)
            })
        
        subtotal = total_mo + total_mat + total_servico  # MODIFICADO: incluir serviço
        total_pago = total_pago_mo + total_pago_mat + total_pago_servico  # MODIFICADO
        bdi = obra.bdi if hasattr(obra, 'bdi') else 0
        valor_bdi = subtotal * (bdi / 100) if bdi else 0
        total_geral = subtotal + valor_bdi
        
        return jsonify({
            'obra_id': obra_id,
            'obra_nome': obra.nome,
            'etapas': etapas_dict,
            'resumo': {
                'total_mao_obra': total_mo,
                'total_material': total_mat,
                'total_servico': total_servico,  # NOVO
                'subtotal': subtotal,
                'bdi': bdi,
                'valor_bdi': valor_bdi,
                'total_geral': total_geral,
                'total_pago_mo': total_pago_mo,
                'total_pago_mat': total_pago_mat,
                'total_pago_servico': total_pago_servico,  # NOVO
                'total_pago': total_pago,
                'percentual_executado': round((total_pago / subtotal * 100) if subtotal > 0 else 0, 1),
                'total_etapas': len(etapas),
                'total_itens': total_itens,
                'itens_vinculados': itens_vinculados
            }
        })
        
    except Exception as e:
        logger.exception(f"[ORCAMENTO-ENG] Erro: {e}")
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/etapas', methods=['POST'])
@jwt_required()
def criar_etapa_orcamento(obra_id):
    """
    Cria uma nova etapa no orçamento de engenharia
    ATUALIZADO: Sincroniza automaticamente com o cronograma de obras
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        
        # Gerar código automaticamente se não fornecido
        if not dados.get('codigo'):
            ultima_etapa = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).order_by(OrcamentoEngEtapa.codigo.desc()).first()
            if ultima_etapa:
                try:
                    ultimo_num = int(ultima_etapa.codigo)
                    dados['codigo'] = f"{ultimo_num + 1:02d}"
                except Exception:
                    dados['codigo'] = "01"
            else:
                dados['codigo'] = "01"
        
        # Calcular ordem
        max_ordem = db.session.query(db.func.max(OrcamentoEngEtapa.ordem)).filter_by(obra_id=obra_id).scalar() or 0
        
        etapa = OrcamentoEngEtapa(
            obra_id=obra_id,
            codigo=dados['codigo'],
            nome=dados['nome'].upper(),
            ordem=max_ordem + 1
        )
        
        db.session.add(etapa)
        db.session.flush()  # Para obter o ID antes do commit
        
        # ========================================
        # SINCRONIZAÇÃO AUTOMÁTICA COM CRONOGRAMA
        # ========================================
        # Import tardio: a função vive em routes.cronograma (import órfão da
        # extração fase-4). Lazy evita qualquer risco de import circular.
        from routes.cronograma import sincronizar_etapa_orcamento_para_cronograma
        cronograma_criado = sincronizar_etapa_orcamento_para_cronograma(etapa.id, obra_id)
        
        db.session.commit()
        
        resultado = etapa.to_dict()
        if cronograma_criado:
            resultado['cronograma_sincronizado'] = True
            resultado['cronograma_id'] = cronograma_criado.id
        
        return jsonify(resultado), 201

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /obras/%s/orcamento-eng/etapas", obra_id)
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/etapas/<int:etapa_id>', methods=['PUT'])
@jwt_required()
def editar_etapa_orcamento(obra_id, etapa_id):
    """
    Edita uma etapa do orçamento
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        etapa = OrcamentoEngEtapa.query.get_or_404(etapa_id)
        if etapa.obra_id != obra_id:
            return jsonify({"erro": "Etapa não pertence a esta obra"}), 404
        dados = request.json

        if 'codigo' in dados:
            etapa.codigo = dados['codigo']
        if 'nome' in dados:
            etapa.nome = dados['nome'].upper()
        if 'ordem' in dados:
            etapa.ordem = dados['ordem']
        
        db.session.commit()
        
        return jsonify(etapa.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/reordenar-etapas', methods=['POST'])
@jwt_required()
def reordenar_etapas_orcamento(obra_id):
    """
    Reordena as etapas do orçamento
    Recebe: { etapas: [{ id: 1, ordem: 0 }, { id: 2, ordem: 1 }, ...] }
    """
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        etapas_ordem = dados.get('etapas', [])
        
        for item in etapas_ordem:
            etapa = OrcamentoEngEtapa.query.get(item['id'])
            if etapa and etapa.obra_id == obra_id:
                etapa.ordem = item['ordem']
                # Atualizar código se fornecido
                if 'codigo' in item:
                    etapa.codigo = item['codigo']
        
        db.session.commit()
        
        return jsonify({"sucesso": True, "mensagem": "Etapas reordenadas com sucesso"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/etapas/<int:etapa_id>', methods=['DELETE'])
@jwt_required()
def deletar_etapa_orcamento(obra_id, etapa_id):
    """
    Deleta uma etapa e todos os seus itens (e serviços vinculados)
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        etapa = OrcamentoEngEtapa.query.get_or_404(etapa_id)
        if etapa.obra_id != obra_id:
            return jsonify({"erro": "Etapa não pertence a esta obra"}), 404

        # Deletar serviços vinculados aos itens (com tratamento de erro)
        for item in etapa.itens:
            try:
                if item.servico_id:
                    servico = Servico.query.get(item.servico_id)
                    if servico:
                        db.session.delete(servico)
            except Exception as e:
                logger.exception(f"[AVISO] Erro ao deletar serviço {item.servico_id}: {e}")
                # Continuar mesmo se falhar
        
        db.session.delete(etapa)
        db.session.commit()
        
        return jsonify({"mensagem": "Etapa deletada com sucesso"})
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/itens', methods=['POST'])
@jwt_required()
def criar_item_orcamento(obra_id):
    """
    Cria um novo item no orçamento de engenharia
    Pode criar serviço automaticamente no Kanban
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        etapa_id = dados['etapa_id']
        
        # Verificar se etapa existe
        etapa = OrcamentoEngEtapa.query.get_or_404(etapa_id)
        if etapa.obra_id != obra_id:
            return jsonify({"erro": "Etapa não pertence a esta obra"}), 400
        
        # Gerar código automaticamente se não fornecido
        if not dados.get('codigo'):
            ultimo_item = OrcamentoEngItem.query.filter_by(etapa_id=etapa_id).order_by(OrcamentoEngItem.codigo.desc()).first()
            if ultimo_item:
                try:
                    partes = ultimo_item.codigo.split('.')
                    ultimo_num = int(partes[-1])
                    dados['codigo'] = f"{etapa.codigo}.{ultimo_num + 1:02d}"
                except Exception:
                    dados['codigo'] = f"{etapa.codigo}.01"
            else:
                dados['codigo'] = f"{etapa.codigo}.01"
        
        # Calcular ordem
        max_ordem = db.session.query(db.func.max(OrcamentoEngItem.ordem)).filter_by(etapa_id=etapa_id).scalar() or 0
        
        # Criar item
        item = OrcamentoEngItem(
            etapa_id=etapa_id,
            codigo=dados['codigo'],
            descricao=dados['descricao'],
            unidade=dados['unidade'],
            quantidade=dados.get('quantidade', 0),
            tipo_composicao=dados.get('tipo_composicao', 'separado'),
            preco_mao_obra=dados.get('preco_mao_obra'),
            preco_material=dados.get('preco_material'),
            preco_unitario=dados.get('preco_unitario'),
            rateio_mo=dados.get('rateio_mo', 50),
            rateio_mat=dados.get('rateio_mat', 50),
            ordem=max_ordem + 1
        )
        
        db.session.add(item)
        db.session.flush()  # Para obter o ID do item
        
        # Opção de serviço
        opcao_servico = dados.get('opcao_servico', 'criar')  # criar | vincular | nao
        
        if opcao_servico == 'criar':
            # Criar serviço automaticamente no Kanban
            totais = item.calcular_totais()
            
            servico = Servico(
                obra_id=obra_id,
                nome=dados['descricao'],
                responsavel=dados.get('responsavel'),
                valor_global_mao_de_obra=totais['total_mao_obra'],
                valor_global_material=totais['total_material']
            )
            db.session.add(servico)
            db.session.flush()
            
            item.servico_id = servico.id
            
        elif opcao_servico == 'vincular' and dados.get('servico_id'):
            # Vincular a serviço existente
            servico_existente = Servico.query.get(dados['servico_id'])
            if servico_existente and servico_existente.obra_id == obra_id:
                item.servico_id = servico_existente.id
                
                # Atualizar valores do serviço (somar)
                totais = item.calcular_totais()
                servico_existente.valor_global_mao_de_obra += totais['total_mao_obra']
                servico_existente.valor_global_material += totais['total_material']
        
        # Salvar na biblioteca do usuário (opcional)
        if dados.get('salvar_biblioteca'):
            servico_usuario = ServicoUsuario.query.filter_by(
                user_id=user.id,
                descricao=dados['descricao'],
                unidade=dados['unidade']
            ).first()
            
            if servico_usuario:
                # Atualizar existente
                servico_usuario.vezes_usado += 1
                servico_usuario.ultima_utilizacao = datetime.utcnow()
                if dados.get('tipo_composicao') == 'separado':
                    servico_usuario.preco_mao_obra = dados.get('preco_mao_obra')
                    servico_usuario.preco_material = dados.get('preco_material')
                else:
                    servico_usuario.preco_unitario = dados.get('preco_unitario')
            else:
                # Criar novo
                novo_servico_usuario = ServicoUsuario(
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
                db.session.add(novo_servico_usuario)
        
        db.session.commit()
        
        return jsonify(item.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ORCAMENTO-ENG] Erro ao criar item: {e}")
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/itens/<int:item_id>', methods=['PUT'])
@jwt_required()
def editar_item_orcamento(obra_id, item_id):
    """
    Edita um item do orçamento
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        item = OrcamentoEngItem.query.get_or_404(item_id)
        if not item.etapa or item.etapa.obra_id != obra_id:
            return jsonify({"erro": "Item não pertence a esta obra"}), 404
        dados = request.json

        # Guardar totais antigos para atualizar serviço
        totais_antigos = item.calcular_totais() if item.servico_id else None
        
        # Atualizar campos
        if 'codigo' in dados:
            item.codigo = dados['codigo']
        if 'descricao' in dados:
            item.descricao = dados['descricao']
        if 'unidade' in dados:
            item.unidade = dados['unidade']
        if 'quantidade' in dados:
            item.quantidade = dados['quantidade']
        if 'tipo_composicao' in dados:
            item.tipo_composicao = dados['tipo_composicao']
        if 'preco_mao_obra' in dados:
            item.preco_mao_obra = dados['preco_mao_obra']
        if 'preco_material' in dados:
            item.preco_material = dados['preco_material']
        if 'preco_unitario' in dados:
            item.preco_unitario = dados['preco_unitario']
        if 'rateio_mo' in dados:
            item.rateio_mo = dados['rateio_mo']
        if 'rateio_mat' in dados:
            item.rateio_mat = dados['rateio_mat']
        
        # Atualizar serviço vinculado se existir
        if item.servico_id:
            servico = Servico.query.get(item.servico_id)
            if servico:
                totais_novos = item.calcular_totais()
                
                # Definir valores diretamente (não apenas diferença)
                # Se o serviço estava zerado, isso corrige o problema
                servico.valor_global_mao_de_obra = totais_novos['total_mao_obra']
                servico.valor_global_material = totais_novos['total_material']
                
                # Atualizar nome do serviço se descrição mudou
                if 'descricao' in dados:
                    servico.nome = dados['descricao']
                
                logger.info(f"--- [LOG] Serviço {servico.id} atualizado: MO={totais_novos['total_mao_obra']}, MAT={totais_novos['total_material']} ---")
        
        db.session.commit()
        
        return jsonify(item.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/itens/<int:item_id>', methods=['DELETE'])
@jwt_required()
def deletar_item_orcamento(obra_id, item_id):
    """
    Deleta um item do orçamento E o serviço vinculado (se houver)
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        item = OrcamentoEngItem.query.get_or_404(item_id)
        if not item.etapa or item.etapa.obra_id != obra_id:
            return jsonify({"erro": "Item não pertence a esta obra"}), 404

        # Deletar serviço vinculado (com tratamento de erro)
        try:
            if item.servico_id:
                servico = Servico.query.get(item.servico_id)
                if servico:
                    # Verificar se há outros itens usando este serviço
                    outros_itens = OrcamentoEngItem.query.filter(
                        OrcamentoEngItem.servico_id == item.servico_id,
                        OrcamentoEngItem.id != item_id
                    ).count()
                    
                    if outros_itens > 0:
                        # Outros itens usam este serviço, apenas desvincular
                        totais = item.calcular_totais()
                        servico.valor_global_mao_de_obra = max(0, (servico.valor_global_mao_de_obra or 0) - totais['total_mao_obra'])
                        servico.valor_global_material = max(0, (servico.valor_global_material or 0) - totais['total_material'])
                    else:
                        # Nenhum outro item usa, deletar serviço
                        db.session.delete(servico)
        except Exception as e:
            logger.exception(f"[AVISO] Erro ao processar serviço vinculado: {e}")
            # Continuar com a exclusão do item mesmo se falhar a exclusão do serviço
        
        db.session.delete(item)
        db.session.commit()
        
        return jsonify({"mensagem": "Item deletado com sucesso"})
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/sincronizar-servicos', methods=['POST'])
@jwt_required()
def sincronizar_servicos_com_orcamento(obra_id):
    """
    Sincroniza os valores de TODOS os serviços do Kanban com o Orçamento de Engenharia
    - Cria serviços para itens que não têm serviço vinculado
    - Atualiza valores de serviços existentes
    - Remove vínculos a serviços que não existem mais
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar todos os itens do orçamento
        itens = OrcamentoEngItem.query.join(OrcamentoEngEtapa).filter(
            OrcamentoEngEtapa.obra_id == obra_id
        ).all()
        
        servicos_atualizados = 0
        servicos_criados = 0
        vinculos_corrigidos = 0
        
        for item in itens:
            totais = item.calcular_totais()
            
            if item.servico_id:
                # Verificar se o serviço existe
                servico = Servico.query.get(item.servico_id)
                
                if servico:
                    # Atualizar valores do serviço existente
                    servico.valor_global_mao_de_obra = totais['total_mao_obra']
                    servico.valor_global_material = totais['total_material']
                    servicos_atualizados += 1
                else:
                    # Serviço não existe mais - criar novo
                    novo_servico = Servico(
                        obra_id=obra_id,
                        nome=item.descricao,
                        valor_global_mao_de_obra=totais['total_mao_obra'],
                        valor_global_material=totais['total_material']
                    )
                    db.session.add(novo_servico)
                    db.session.flush()
                    item.servico_id = novo_servico.id
                    vinculos_corrigidos += 1
                    servicos_criados += 1
            else:
                # Item não tem serviço vinculado - criar um
                novo_servico = Servico(
                    obra_id=obra_id,
                    nome=item.descricao,
                    valor_global_mao_de_obra=totais['total_mao_obra'],
                    valor_global_material=totais['total_material']
                )
                db.session.add(novo_servico)
                db.session.flush()
                item.servico_id = novo_servico.id
                servicos_criados += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": f"Sincronização concluída!",
            "servicos_atualizados": servicos_atualizados,
            "servicos_criados": servicos_criados,
            "vinculos_corrigidos": vinculos_corrigidos
        })
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/apagar-tudo', methods=['DELETE'])
@jwt_required()
def apagar_orcamento_completo(obra_id):
    """
    Apaga TODO o orçamento de engenharia da obra (etapas, itens e serviços vinculados)
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        # Apenas master e administrador podem apagar
        if user.role not in ['master', 'administrador']:
            return jsonify({"erro": "Apenas administradores podem apagar o orçamento completo"}), 403
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar todas as etapas
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).all()
        
        itens_deletados = 0
        etapas_deletadas = 0
        servicos_deletados = 0
        
        for etapa in etapas:
            for item in etapa.itens:
                # Deletar serviço vinculado se existir
                if item.servico_id:
                    servico = Servico.query.get(item.servico_id)
                    if servico:
                        # Verificar se o serviço tem pagamentos
                        if len(servico.pagamentos) > 0:
                            # Não deletar serviço com pagamentos, apenas desvincular
                            item.servico_id = None
                        else:
                            db.session.delete(servico)
                            servicos_deletados += 1
                
                db.session.delete(item)
                itens_deletados += 1
            
            db.session.delete(etapa)
            etapas_deletadas += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": f"Orçamento apagado com sucesso!",
            "etapas_deletadas": etapas_deletadas,
            "itens_deletados": itens_deletados,
            "servicos_deletados": servicos_deletados
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao apagar orçamento: {e}")
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/sincronizar-pagamentos', methods=['POST'])
@jwt_required()
def sincronizar_pagamentos_orcamento(obra_id):
    """
    Sincroniza os valores pagos dos itens do orçamento com os pagamentos do Kanban
    Deve ser chamado após registrar/deletar pagamentos
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        # Buscar todos os itens do orçamento desta obra
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).all()
        
        itens_atualizados = 0
        
        for etapa in etapas:
            for item in etapa.itens:
                if item.servico_id:
                    servico = Servico.query.get(item.servico_id)
                    if servico:
                        # Calcular total pago no serviço
                        valor_pago_mo = 0
                        valor_pago_mat = 0
                        
                        for pag in servico.pagamentos:
                            if pag.status == 'Pago':
                                if pag.tipo_pagamento == 'mao_de_obra':
                                    valor_pago_mo += pag.valor_pago or pag.valor_total or 0
                                else:
                                    valor_pago_mat += pag.valor_pago or pag.valor_total or 0
                        
                        # Verificar se há outros itens usando o mesmo serviço
                        itens_mesmo_servico = OrcamentoEngItem.query.filter_by(servico_id=item.servico_id).all()
                        
                        if len(itens_mesmo_servico) > 1:
                            # Ratear proporcionalmente entre os itens
                            total_mo_servico = sum(i.calcular_totais()['total_mao_obra'] for i in itens_mesmo_servico)
                            total_mat_servico = sum(i.calcular_totais()['total_material'] for i in itens_mesmo_servico)
                            
                            totais_item = item.calcular_totais()
                            
                            if total_mo_servico > 0:
                                proporcao_mo = totais_item['total_mao_obra'] / total_mo_servico
                                item.valor_pago_mo = valor_pago_mo * proporcao_mo
                            
                            if total_mat_servico > 0:
                                proporcao_mat = totais_item['total_material'] / total_mat_servico
                                item.valor_pago_mat = valor_pago_mat * proporcao_mat
                        else:
                            # Item único para este serviço
                            item.valor_pago_mo = valor_pago_mo
                            item.valor_pago_mat = valor_pago_mat
                        
                        itens_atualizados += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": "Pagamentos sincronizados",
            "itens_atualizados": itens_atualizados
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500
# ==============================================================================
# GERAÇÃO DE ORÇAMENTO POR PLANTA BAIXA (CLAUDE VISION)
# ==============================================================================

@orcamento_eng_bp.route('/gerar-por-planta', methods=['POST'])
@jwt_required()
def gerar_orcamento_por_planta(obra_id):
    """
    Recebe uma imagem de planta baixa e usa Claude Vision para gerar orçamento automaticamente
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        imagem_base64 = dados.get('imagem_base64')
        media_type = dados.get('media_type', 'image/jpeg')
        area_total = dados.get('area_total')
        padrao = dados.get('padrao', 'médio')
        pavimentos = dados.get('pavimentos', 1)
        tipo_construcao = dados.get('tipo_construcao', 'residencial')
        
        if not imagem_base64:
            return jsonify({"erro": "Imagem não fornecida"}), 400
        
        # Validar media_type (API Anthropic aceita imagens e PDF)
        tipos_imagem = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
        tipos_documento = ['application/pdf']
        tipos_validos = tipos_imagem + tipos_documento
        
        if media_type not in tipos_validos:
            return jsonify({"erro": f"Formato não suportado: {media_type}. Use JPG, PNG, GIF, WEBP ou PDF."}), 400
        
        # Determinar se é imagem ou documento (para estrutura da API)
        is_pdf = media_type in tipos_documento
        
        # Remover prefixo data:image se existir
        if ',' in imagem_base64:
            imagem_base64 = imagem_base64.split(',')[1]
        
        # Chave da API Anthropic (configurar como variável de ambiente)
        anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not anthropic_api_key:
            return jsonify({"erro": "API Key da Anthropic não configurada"}), 500
        
        logger.info(f"[PLANTA-IA] Analisando planta para obra {obra_id}...")
        
        # Montar prompt para análise
        prompt = f"""Analise esta planta baixa de uma construção e gere um orçamento detalhado.

INFORMAÇÕES FORNECIDAS:
- Área total informada: {area_total if area_total else 'não informada (estimar pela planta)'}
- Padrão de acabamento: {padrao}
- Número de pavimentos: {pavimentos}
- Tipo de construção: {tipo_construcao}

INSTRUÇÕES:
1. Identifique todos os ambientes visíveis na planta (quartos, salas, banheiros, cozinha, etc.)
2. Estime as dimensões e áreas de cada ambiente se possível ver escala ou cotas
3. Calcule quantitativos para cada serviço de construção
4. Use valores realistas baseados nas dimensões identificadas

IMPORTANTE: Retorne APENAS um JSON válido, sem markdown, sem explicações, seguindo EXATAMENTE esta estrutura:

{{
    "dados_identificados": {{
        "area_estimada": 120,
        "ambientes": [
            {{"nome": "Sala", "area_estimada": 20}},
            {{"nome": "Quarto 1", "area_estimada": 12}},
            {{"nome": "Banheiro 1", "area_estimada": 4}}
        ],
        "total_ambientes": 8,
        "banheiros": 2,
        "paredes_lineares_m": 85,
        "portas_estimadas": 8,
        "janelas_estimadas": 10,
        "observacoes": "Casa térrea com planta retangular"
    }},
    "etapas": [
        {{
            "codigo": "01",
            "nome": "SERVIÇOS PRELIMINARES",
            "itens": [
                {{
                    "codigo": "01.01",
                    "descricao": "Limpeza do terreno",
                    "unidade": "m²",
                    "quantidade": 150,
                    "justificativa": "Área do terreno estimada em 25% maior que área construída"
                }},
                {{
                    "codigo": "01.02",
                    "descricao": "Locação da obra",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída total"
                }}
            ]
        }},
        {{
            "codigo": "02",
            "nome": "FUNDAÇÃO",
            "itens": [
                {{
                    "codigo": "02.01",
                    "descricao": "Escavação manual até 1,5m",
                    "unidade": "m³",
                    "quantidade": 36,
                    "justificativa": "Perímetro 40m x profundidade 0.6m x largura 1.5m"
                }},
                {{
                    "codigo": "02.02",
                    "descricao": "Concreto fck 25 MPa",
                    "unidade": "m³",
                    "quantidade": 18,
                    "justificativa": "Volume de concreto para sapatas e baldrame"
                }}
            ]
        }},
        {{
            "codigo": "03",
            "nome": "ESTRUTURA",
            "itens": [
                {{
                    "codigo": "03.01",
                    "descricao": "Laje pré-moldada h=12cm",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }}
            ]
        }},
        {{
            "codigo": "04",
            "nome": "ALVENARIA",
            "itens": [
                {{
                    "codigo": "04.01",
                    "descricao": "Alvenaria bloco cerâmico 14x19x39",
                    "unidade": "m²",
                    "quantidade": 238,
                    "justificativa": "Perímetro 85m x pé-direito 2.8m"
                }}
            ]
        }},
        {{
            "codigo": "05",
            "nome": "INSTALAÇÕES HIDRÁULICAS",
            "itens": [
                {{
                    "codigo": "05.01",
                    "descricao": "Ponto de água fria PVC",
                    "unidade": "pt",
                    "quantidade": 18,
                    "justificativa": "2 banheiros (8pt) + cozinha (4pt) + área serviço (4pt) + jardim (2pt)"
                }},
                {{
                    "codigo": "05.02",
                    "descricao": "Ponto de esgoto PVC",
                    "unidade": "pt",
                    "quantidade": 12,
                    "justificativa": "2 banheiros (6pt) + cozinha (3pt) + área serviço (3pt)"
                }},
                {{
                    "codigo": "05.03",
                    "descricao": "Vaso sanitário com caixa acoplada",
                    "unidade": "un",
                    "quantidade": 2,
                    "justificativa": "1 por banheiro"
                }},
                {{
                    "codigo": "05.04",
                    "descricao": "Lavatório com coluna",
                    "unidade": "un",
                    "quantidade": 2,
                    "justificativa": "1 por banheiro"
                }}
            ]
        }},
        {{
            "codigo": "06",
            "nome": "INSTALAÇÕES ELÉTRICAS",
            "itens": [
                {{
                    "codigo": "06.01",
                    "descricao": "Ponto de luz",
                    "unidade": "pt",
                    "quantidade": 15,
                    "justificativa": "Média de 1-2 por ambiente"
                }},
                {{
                    "codigo": "06.02",
                    "descricao": "Ponto de tomada 2P+T",
                    "unidade": "pt",
                    "quantidade": 45,
                    "justificativa": "Média de 5-6 por ambiente"
                }},
                {{
                    "codigo": "06.03",
                    "descricao": "Quadro distribuição 12 circuitos",
                    "unidade": "un",
                    "quantidade": 1,
                    "justificativa": "Quadro principal"
                }}
            ]
        }},
        {{
            "codigo": "07",
            "nome": "REVESTIMENTOS",
            "itens": [
                {{
                    "codigo": "07.01",
                    "descricao": "Chapisco interno",
                    "unidade": "m²",
                    "quantidade": 476,
                    "justificativa": "Paredes internas 238m² x 2 faces"
                }},
                {{
                    "codigo": "07.02",
                    "descricao": "Reboco interno e=2cm",
                    "unidade": "m²",
                    "quantidade": 476,
                    "justificativa": "Paredes internas"
                }},
                {{
                    "codigo": "07.03",
                    "descricao": "Contrapiso e=5cm",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }},
                {{
                    "codigo": "07.04",
                    "descricao": "Piso cerâmico PEI-4",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }},
                {{
                    "codigo": "07.05",
                    "descricao": "Azulejo 30x60",
                    "unidade": "m²",
                    "quantidade": 28,
                    "justificativa": "Paredes dos banheiros até 1.8m de altura"
                }}
            ]
        }},
        {{
            "codigo": "08",
            "nome": "PINTURA",
            "itens": [
                {{
                    "codigo": "08.01",
                    "descricao": "Massa corrida PVA",
                    "unidade": "m²",
                    "quantidade": 448,
                    "justificativa": "Paredes - azulejos"
                }},
                {{
                    "codigo": "08.02",
                    "descricao": "Pintura acrílica 2 demãos",
                    "unidade": "m²",
                    "quantidade": 568,
                    "justificativa": "Paredes + teto"
                }}
            ]
        }},
        {{
            "codigo": "09",
            "nome": "ESQUADRIAS",
            "itens": [
                {{
                    "codigo": "09.01",
                    "descricao": "Porta madeira 80x210 completa",
                    "unidade": "un",
                    "quantidade": 5,
                    "justificativa": "Portas internas dos quartos e banheiros"
                }},
                {{
                    "codigo": "09.02",
                    "descricao": "Porta madeira 70x210 completa",
                    "unidade": "un",
                    "quantidade": 3,
                    "justificativa": "Portas menores"
                }},
                {{
                    "codigo": "09.03",
                    "descricao": "Janela alumínio correr 120x120",
                    "unidade": "un",
                    "quantidade": 10,
                    "justificativa": "Janelas dos ambientes"
                }}
            ]
        }},
        {{
            "codigo": "10",
            "nome": "COBERTURA",
            "itens": [
                {{
                    "codigo": "10.01",
                    "descricao": "Estrutura madeira para telha",
                    "unidade": "m²",
                    "quantidade": 140,
                    "justificativa": "Área construída + beiral"
                }},
                {{
                    "codigo": "10.02",
                    "descricao": "Telha cerâmica",
                    "unidade": "m²",
                    "quantidade": 140,
                    "justificativa": "Área de cobertura"
                }}
            ]
        }},
        {{
            "codigo": "11",
            "nome": "LIMPEZA E ACABAMENTO",
            "itens": [
                {{
                    "codigo": "11.01",
                    "descricao": "Limpeza final da obra",
                    "unidade": "m²",
                    "quantidade": 120,
                    "justificativa": "Área construída"
                }}
            ]
        }}
    ]
}}

Adapte os quantitativos conforme o que você identificar na planta. Se a planta mostrar mais ou menos ambientes, ajuste proporcionalmente."""

        # Chamar API da Anthropic
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': anthropic_api_key,
            'anthropic-version': '2023-06-01'
        }
        
        # Adicionar header beta para suporte a PDF
        if is_pdf:
            headers['anthropic-beta'] = 'pdfs-2024-09-25'
        
        # Estrutura diferente para PDF (document) vs imagem (image)
        if is_pdf:
            content_block = {
                'type': 'document',
                'source': {
                    'type': 'base64',
                    'media_type': media_type,
                    'data': imagem_base64
                }
            }
        else:
            content_block = {
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': media_type,
                    'data': imagem_base64
                }
            }
        
        payload = {
            'model': 'claude-sonnet-4-20250514',
            'max_tokens': 8000,
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        content_block,
                        {
                            'type': 'text',
                            'text': prompt
                        }
                    ]
                }
            ]
        }
        
        logger.info(f"[PLANTA-IA] Enviando para Claude Vision... (tipo: {'PDF' if is_pdf else 'imagem'})")
        
        # Usar urllib (nativo do Python) para chamar API
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method='POST'
        )
        
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                response_data = response.read().decode('utf-8')
                result = json.loads(response_data)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else str(e)
            logger.error(f"[PLANTA-IA] Erro da API: {e.code} - {error_body}")
            
            # Mensagens de erro mais claras
            erro_msg = f"Erro na API de IA: {e.code}"
            try:
                error_json = json.loads(error_body)
                error_type = error_json.get('error', {}).get('type', '')
                error_message = error_json.get('error', {}).get('message', '')
                
                if e.code == 400:
                    if 'credit' in error_message.lower() or 'billing' in error_message.lower():
                        erro_msg = "Créditos insuficientes na conta Anthropic. Adicione créditos em console.anthropic.com/billing"
                    elif 'invalid' in error_message.lower():
                        erro_msg = "API Key inválida. Verifique a configuração no Railway."
                    else:
                        erro_msg = f"Erro na requisição: {error_message}"
                elif e.code == 401:
                    erro_msg = "API Key inválida ou expirada. Crie uma nova chave em console.anthropic.com"
                elif e.code == 429:
                    erro_msg = "Limite de requisições excedido. Aguarde alguns minutos."
                elif e.code == 500:
                    erro_msg = "Erro interno da API Anthropic. Tente novamente."
                else:
                    erro_msg = f"Erro {e.code}: {error_message or error_body[:200]}"
            except Exception:
                logger.warning("Excecao ao parsear erro da API Anthropic", exc_info=True)
                pass

            return jsonify({"erro": erro_msg}), 500
        
        logger.info("[PLANTA-IA] Resposta recebida, processando...")
        
        # Extrair texto da resposta
        texto_resposta = result.get('content', [{}])[0].get('text', '')
        
        # Tentar parsear JSON
        try:
            # Limpar possíveis caracteres extras
            texto_limpo = texto_resposta.strip()
            if texto_limpo.startswith('```json'):
                texto_limpo = texto_limpo[7:]
            if texto_limpo.startswith('```'):
                texto_limpo = texto_limpo[3:]
            if texto_limpo.endswith('```'):
                texto_limpo = texto_limpo[:-3]
            texto_limpo = texto_limpo.strip()
            
            orcamento_gerado = json.loads(texto_limpo)
        except json.JSONDecodeError as e:
            logger.exception(f"[PLANTA-IA] Erro ao parsear JSON: {e}")
            logger.info(f"[PLANTA-IA] Texto recebido: {texto_resposta[:500]}...")
            return jsonify({
                "erro": "Erro ao processar resposta da IA",
                "detalhes": str(e),
                "resposta_raw": texto_resposta[:1000]
            }), 500
        
        # Enriquecer com preços da base de serviços
        logger.info("[PLANTA-IA] Enriquecendo com preços da base...")
        for etapa in orcamento_gerado.get('etapas', []):
            for item in etapa.get('itens', []):
                # Buscar serviço similar na base
                descricao = item.get('descricao', '')
                servico_base = ServicoBase.query.filter(
                    ServicoBase.descricao.ilike(f'%{descricao}%')
                ).first()
                
                if servico_base:
                    item['preco_mao_obra'] = servico_base.preco_mao_obra
                    item['preco_material'] = servico_base.preco_material
                    item['preco_unitario'] = servico_base.preco_unitario
                    item['tipo_composicao'] = servico_base.tipo_composicao
                    item['rateio_mo'] = servico_base.rateio_mo
                    item['rateio_mat'] = servico_base.rateio_mat
                    item['fonte_preco'] = 'base'
                else:
                    # Tentar busca mais flexível
                    palavras = descricao.split()[:2]  # Primeiras 2 palavras
                    if palavras:
                        servico_base = ServicoBase.query.filter(
                            ServicoBase.descricao.ilike(f'%{palavras[0]}%')
                        ).first()
                        if servico_base:
                            item['preco_mao_obra'] = servico_base.preco_mao_obra
                            item['preco_material'] = servico_base.preco_material
                            item['preco_unitario'] = servico_base.preco_unitario
                            item['tipo_composicao'] = servico_base.tipo_composicao
                            item['fonte_preco'] = 'base_aproximado'
                        else:
                            item['fonte_preco'] = 'nao_encontrado'
                            item['tipo_composicao'] = 'separado'
                    else:
                        item['fonte_preco'] = 'nao_encontrado'
                        item['tipo_composicao'] = 'separado'
        
        # Calcular totais
        total_geral = 0
        total_itens = 0
        for etapa in orcamento_gerado.get('etapas', []):
            etapa_total = 0
            for item in etapa.get('itens', []):
                qtd = item.get('quantidade', 0)
                if item.get('tipo_composicao') == 'composto' and item.get('preco_unitario'):
                    item_total = qtd * item.get('preco_unitario', 0)
                else:
                    mo = item.get('preco_mao_obra') or 0
                    mat = item.get('preco_material') or 0
                    item_total = qtd * (mo + mat)
                item['total_estimado'] = item_total
                etapa_total += item_total
                total_itens += 1
            etapa['total_etapa'] = etapa_total
            total_geral += etapa_total
        
        orcamento_gerado['resumo'] = {
            'total_geral': total_geral,
            'total_etapas': len(orcamento_gerado.get('etapas', [])),
            'total_itens': total_itens
        }
        
        logger.info(f"[PLANTA-IA] Orçamento gerado: {total_itens} itens, total R$ {total_geral:,.2f}")
        
        return jsonify(orcamento_gerado)
        
    except urllib.error.URLError as e:
        logger.exception(f"[PLANTA-IA] Erro de conexão: {e}")
        if hasattr(e, 'reason') and 'timed out' in str(e.reason).lower():
            return jsonify({"erro": "Timeout ao processar imagem. Tente novamente."}), 504
        return jsonify({"erro": f"Erro de conexão: {e.reason}"}), 500
    except Exception as e:
        logger.exception(f"[PLANTA-IA] Erro: {e}")
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


@orcamento_eng_bp.route('/importar-gerado', methods=['POST'])
@jwt_required()
def importar_orcamento_gerado(obra_id):
    """
    Importa o orçamento gerado pela IA para o banco de dados
    Recebe as etapas/itens selecionados pelo usuário após revisão
    """
    try:
        user = get_current_user()
        obra = Obra.query.get_or_404(obra_id)
        
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Sem permissão"}), 403
        
        dados = request.json
        etapas_importar = dados.get('etapas', [])
        criar_servicos = dados.get('criar_servicos', True)
        
        etapas_criadas = 0
        itens_criados = 0
        servicos_criados = 0
        
        for etapa_data in etapas_importar:
            # Verificar se etapa já existe
            etapa_existente = OrcamentoEngEtapa.query.filter_by(
                obra_id=obra_id,
                codigo=etapa_data.get('codigo')
            ).first()
            
            if etapa_existente:
                etapa = etapa_existente
            else:
                # Criar etapa
                max_ordem = db.session.query(db.func.max(OrcamentoEngEtapa.ordem)).filter_by(obra_id=obra_id).scalar() or 0
                etapa = OrcamentoEngEtapa(
                    obra_id=obra_id,
                    codigo=etapa_data.get('codigo'),
                    nome=etapa_data.get('nome', '').upper(),
                    ordem=max_ordem + 1
                )
                db.session.add(etapa)
                db.session.flush()
                etapas_criadas += 1
            
            # Criar itens
            for item_data in etapa_data.get('itens', []):
                if not item_data.get('selecionado', True):
                    continue
                
                # Calcular ordem do item
                max_ordem_item = db.session.query(db.func.max(OrcamentoEngItem.ordem)).filter_by(etapa_id=etapa.id).scalar() or 0
                
                item = OrcamentoEngItem(
                    etapa_id=etapa.id,
                    codigo=item_data.get('codigo'),
                    descricao=item_data.get('descricao'),
                    unidade=item_data.get('unidade'),
                    quantidade=item_data.get('quantidade', 0),
                    tipo_composicao=item_data.get('tipo_composicao', 'separado'),
                    preco_mao_obra=item_data.get('preco_mao_obra'),
                    preco_material=item_data.get('preco_material'),
                    preco_unitario=item_data.get('preco_unitario'),
                    rateio_mo=item_data.get('rateio_mo', 50),
                    rateio_mat=item_data.get('rateio_mat', 50),
                    ordem=max_ordem_item + 1
                )
                db.session.add(item)
                db.session.flush()
                itens_criados += 1
                
                # Criar serviço no Kanban se solicitado
                if criar_servicos and item_data.get('criar_servico', True):
                    totais = item.calcular_totais()
                    servico = Servico(
                        obra_id=obra_id,
                        nome=item_data.get('descricao'),
                        valor_global_mao_de_obra=totais['total_mao_obra'],
                        valor_global_material=totais['total_material']
                    )
                    db.session.add(servico)
                    db.session.flush()
                    item.servico_id = servico.id
                    servicos_criados += 1
        
        db.session.commit()
        
        return jsonify({
            "mensagem": "Orçamento importado com sucesso",
            "etapas_criadas": etapas_criadas,
            "itens_criados": itens_criados,
            "servicos_criados": servicos_criados
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[IMPORTAR-ORC] Erro: {e}")
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500
