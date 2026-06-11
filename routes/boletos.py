import io
import re
import base64
import logging
import traceback
from datetime import datetime, date, timedelta

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models.obra import Obra
from models.boleto import Boleto
from services import get_current_user, user_has_access_to_obra, criar_notificacao
from utils import formatar_real

logger = logging.getLogger(__name__)

boletos_bp = Blueprint('boletos', __name__)


# Função para extrair dados do PDF do boleto
def extrair_dados_boleto_pdf(pdf_base64):
    """Extrai código de barras, vencimento e valor do PDF do boleto (suporta múltiplos boletos)"""
    try:
        import pdfplumber
        
        # Decodificar base64
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        pdf_bytes = base64.b64decode(pdf_base64)
        
        boletos_encontrados = []
        
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            logger.info(f"--- [BOLETO] PDF com {len(pdf.pages)} páginas ---")
            
            # Processar cada página separadamente
            for page_num, page in enumerate(pdf.pages, 1):
                texto = page.extract_text() or ""
                
                if not texto.strip():
                    continue
                
                logger.info(f"--- [BOLETO] Página {page_num}: {len(texto)} chars ---")
                
                boleto = {
                    'codigo_barras': None,
                    'data_vencimento': None,
                    'valor': None,
                    'beneficiario': None,
                    'pagina': page_num
                }
                
                # =====================================================
                # 1. CÓDIGO DE BARRAS (LINHA DIGITÁVEL)
                # =====================================================
                patterns_codigo = [
                    # Itaú: 34191.57007 00014.647382 59766.050005 1 12960001029833
                    r'(\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})',
                    # Inter: 07790.00116 12070.514091 03958.220455 4 11960000057900
                    r'(\d{5}\.\d{5}\s+\d{5}\.\d{6}\s+\d{5}\.\d{6}\s+\d\s+\d{14})',
                    # Genérico com pontos e espaços variados
                    r'(\d{5}\.?\d{5}\s*\d{5}\.?\d{6}\s*\d{5}\.?\d{6}\s*\d\s*\d{14})',
                ]
                
                for pattern in patterns_codigo:
                    match = re.search(pattern, texto)
                    if match:
                        codigo_raw = match.group(1)
                        codigo = re.sub(r'[\s\.]', '', codigo_raw)
                        if len(codigo) >= 47:
                            boleto['codigo_barras'] = codigo[:47] if len(codigo) >= 47 else codigo
                            break
                
                # =====================================================
                # 2. VALOR
                # =====================================================
                patterns_valor = [
                    # Itaú: (=) Valor do Documento\n157 R$ 10.298,33
                    r'\(=\)\s*[Vv]alor\s*(?:do\s*)?[Dd]ocumento\s*[\n\r]*\s*\d+\s*R\$\s*([\d.]+,\d{2})',
                    # Valor do Documento genérico
                    r'[Vv]alor\s*(?:do\s*)?[Dd]ocumento\s*[\n\r]*\s*([\d.]+,\d{2})',
                    r'\(=\)\s*[Vv]alor\s*(?:do\s*)?[Dd]ocumento\s*[\n\r]*\s*([\d.]+,\d{2})',
                    # R$ com milhar
                    r'R\$\s*([\d]{1,3}(?:\.\d{3})*,\d{2})',
                ]
                
                for pattern in patterns_valor:
                    matches = re.findall(pattern, texto)
                    for match_str in matches:
                        try:
                            valor_str = match_str.replace('.', '').replace(',', '.')
                            valor = float(valor_str)
                            if valor > 10 and valor < 10000000:  # Entre R$10 e R$10mi
                                boleto['valor'] = valor
                                break
                        except Exception:
                            continue
                    if boleto['valor']:
                        break
                
                # =====================================================
                # 3. DATA DE VENCIMENTO
                # =====================================================
                patterns_venc = [
                    r'[Vv]encimento\n.*?\n(\d{2}/\d{2}/\d{4})',  # Itaú: Vencimento\nLocal...\n15/12/2025
                    r'[Vv]encimento\s+(\d{2}/\d{2}/\d{4})',  # Inter: Vencimento 06/09/2025
                    r'[Vv]encimento.*?(\d{2}/\d{2}/\d{4})',  # Qualquer texto entre
                ]
                
                hoje = date.today()
                datas_encontradas = []
                
                for pattern in patterns_venc:
                    matches = re.findall(pattern, texto, re.DOTALL)
                    for data_str in matches:
                        try:
                            data_parsed = datetime.strptime(data_str, '%d/%m/%Y').date()
                            if data_parsed.year >= hoje.year:
                                datas_encontradas.append(data_parsed)
                        except Exception:
                            continue
                
                # Preferir data futura (vencimento) ao invés de data passada (emissão)
                if datas_encontradas:
                    # Ordenar: primeiro as datas >= hoje, depois as passadas
                    datas_futuras = [d for d in datas_encontradas if d >= hoje]
                    datas_passadas = [d for d in datas_encontradas if d < hoje]
                    
                    if datas_futuras:
                        # Pegar a data futura mais próxima
                        boleto['data_vencimento'] = min(datas_futuras).isoformat()
                    elif datas_passadas:
                        # Se só tiver passadas, pegar a mais recente
                        boleto['data_vencimento'] = max(datas_passadas).isoformat()
                
                # =====================================================
                # 4. BENEFICIÁRIO
                # =====================================================
                patterns_benef = [
                    r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\s*-\s*([A-Z][A-Za-zÀ-ÿ\s]+(?:LTDA|ALUMINIO|S\.?A\.?|ME|EPP|EIRELI))',  # CNPJ - NOME LTDA
                    r'[Bb]enefici[áa]rio\s*[\n\r]*\s*([A-Z][A-Za-zÀ-ÿ\s]+(?:LTDA|ALUMINIO|S\.?A\.?|ME|EPP|EIRELI))',
                ]
                
                for pattern in patterns_benef:
                    match = re.search(pattern, texto)
                    if match:
                        benef = match.group(1).strip()
                        benef = re.sub(r'\s+', ' ', benef).strip()
                        if len(benef) > 3:
                            boleto['beneficiario'] = benef[:100]
                            break
                
                # Verificar se encontrou dados válidos nesta página
                tem_dados = any([
                    boleto['codigo_barras'],
                    boleto['data_vencimento'],
                    boleto['valor']
                ])
                
                if tem_dados:
                    # Verificar se não é duplicata (mesmo código de barras)
                    codigo_existente = any(
                        b['codigo_barras'] == boleto['codigo_barras'] 
                        for b in boletos_encontrados 
                        if boleto['codigo_barras']
                    )
                    if not codigo_existente:
                        boletos_encontrados.append(boleto)
                        logger.info(f"--- [BOLETO] Página {page_num}: Código={boleto['codigo_barras'][:20] if boleto['codigo_barras'] else 'N/A'}..., Valor={boleto['valor']}, Venc={boleto['data_vencimento']} ---")
        
        # Retornar resultado
        if len(boletos_encontrados) == 0:
            return {
                'sucesso': False,
                'multiplos': False,
                'quantidade': 0,
                'boletos': [],
                'codigo_barras': None,
                'data_vencimento': None,
                'valor': None,
                'beneficiario': None
            }
        elif len(boletos_encontrados) == 1:
            # Boleto único - manter compatibilidade
            b = boletos_encontrados[0]
            return {
                'sucesso': True,
                'multiplos': False,
                'quantidade': 1,
                'boletos': boletos_encontrados,
                'codigo_barras': b['codigo_barras'],
                'data_vencimento': b['data_vencimento'],
                'valor': b['valor'],
                'beneficiario': b['beneficiario']
            }
        else:
            # Múltiplos boletos encontrados
            logger.info(f"--- [BOLETO] {len(boletos_encontrados)} boletos encontrados no PDF ---")
            return {
                'sucesso': True,
                'multiplos': True,
                'quantidade': len(boletos_encontrados),
                'boletos': boletos_encontrados,
                # Dados do primeiro boleto para compatibilidade
                'codigo_barras': boletos_encontrados[0]['codigo_barras'],
                'data_vencimento': boletos_encontrados[0]['data_vencimento'],
                'valor': boletos_encontrados[0]['valor'],
                'beneficiario': boletos_encontrados[0]['beneficiario']
            }
        
    except ImportError as e:
        logger.exception(f"--- [BOLETO] pdfplumber não instalado: {e} ---")
        return {'sucesso': False, 'multiplos': False, 'quantidade': 0, 'boletos': [], 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}
    except Exception as e:
        logger.exception(f"--- [BOLETO] Erro: {e} ---")
        traceback.print_exc()
        return {'sucesso': False, 'multiplos': False, 'quantidade': 0, 'boletos': [], 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}


# ==============================================================================
# ROTAS DE GESTÃO DE BOLETOS
# ==============================================================================
# NOTA: A função extrair_dados_boleto_pdf está definida no início do arquivo (linha ~485)
# e suporta extração de múltiplos boletos de PDFs com várias páginas


@boletos_bp.route('/obras/<int:obra_id>/boletos', methods=['GET'])
@jwt_required()
def listar_boletos(obra_id):
    """Lista todos os boletos de uma obra"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        # Parâmetros de filtro
        status_filtro = request.args.get('status', None)  # Pendente, Pago, Vencido
        dias = request.args.get('dias', None)  # Filtrar por dias até vencimento
        
        query = Boleto.query.filter_by(obra_id=obra_id)
        
        if status_filtro:
            query = query.filter_by(status=status_filtro)
        
        if dias:
            dias_int = int(dias)
            data_limite = date.today() + timedelta(days=dias_int)
            query = query.filter(Boleto.data_vencimento <= data_limite)
        
        boletos = query.order_by(Boleto.data_vencimento.asc()).all()
        
        # Atualizar status de vencidos
        hoje = date.today()
        for boleto in boletos:
            if boleto.status == 'Pendente' and boleto.data_vencimento < hoje:
                boleto.status = 'Vencido'
        db.session.commit()
        
        return jsonify([b.to_dict() for b in boletos]), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] listar_boletos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/obras/<int:obra_id>/boletos', methods=['POST'])
@jwt_required()
def criar_boleto(obra_id):
    """Cria um novo boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json()
        
        # Validar campos obrigatórios
        if not data.get('descricao'):
            return jsonify({"erro": "Descrição é obrigatória"}), 400
        if not data.get('valor'):
            return jsonify({"erro": "Valor é obrigatório"}), 400
        if not data.get('data_vencimento'):
            return jsonify({"erro": "Data de vencimento é obrigatória"}), 400
        
        # Verificar duplicidade por código de barras
        codigo_barras = data.get('codigo_barras')
        if codigo_barras:
            boleto_existente = Boleto.query.filter_by(
                obra_id=obra_id, 
                codigo_barras=codigo_barras
            ).first()
            if boleto_existente:
                logger.info(f"--- [LOG] Boleto duplicado ignorado: código {codigo_barras[:20]}... já existe ---")
                return jsonify({"erro": "Boleto com este código de barras já existe", "duplicado": True}), 409
        
        novo_boleto = Boleto(
            obra_id=obra_id,
            usuario_id=user.id,
            codigo_barras=codigo_barras,
            descricao=data.get('descricao'),
            beneficiario=data.get('beneficiario'),
            valor=float(data.get('valor')),
            data_vencimento=datetime.strptime(data.get('data_vencimento'), '%Y-%m-%d').date(),
            status='Pendente',
            vinculado_servico_id=data.get('vinculado_servico_id'),
            orcamento_item_id=data.get('orcamento_item_id') or None,
            arquivo_nome=data.get('arquivo_nome'),
            arquivo_pdf=data.get('arquivo_pdf') or data.get('arquivo_base64')
        )
        
        db.session.add(novo_boleto)
        db.session.commit()
        
        logger.info(f"--- [LOG] Boleto criado: ID {novo_boleto.id} na obra {obra_id} ---")
        return jsonify(novo_boleto.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] criar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/obras/<int:obra_id>/boletos/extrair-pdf', methods=['POST'])
@jwt_required()
def extrair_pdf_boleto(obra_id):
    """Extrai dados de um PDF de boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        data = request.get_json()
        pdf_base64 = data.get('arquivo_base64')
        
        if not pdf_base64:
            return jsonify({"erro": "Arquivo PDF não enviado"}), 400
        
        # Remover prefixo data:application/pdf;base64, se existir
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        
        resultado = extrair_dados_boleto_pdf(pdf_base64)
        
        return jsonify(resultado), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] extrair_pdf_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/obras/<int:obra_id>/boletos/<int:boleto_id>', methods=['PUT'])
@jwt_required()
def editar_boleto(obra_id, boleto_id):
    """Edita um boleto existente"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        data = request.get_json()
        
        if 'descricao' in data:
            boleto.descricao = data['descricao']
        if 'beneficiario' in data:
            boleto.beneficiario = data['beneficiario']
        if 'codigo_barras' in data:
            boleto.codigo_barras = data['codigo_barras']
        if 'valor' in data:
            boleto.valor = float(data['valor'])
        if 'data_vencimento' in data:
            boleto.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        if 'status' in data:
            boleto.status = data['status']
        if 'vinculado_servico_id' in data:
            boleto.vinculado_servico_id = data['vinculado_servico_id']
        
        if 'orcamento_item_id' in data:
            boleto.orcamento_item_id = data['orcamento_item_id'] or None
        
        db.session.commit()
        
        logger.info(f"--- [LOG] Boleto {boleto_id} editado ---")
        return jsonify(boleto.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] editar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/obras/<int:obra_id>/boletos/<int:boleto_id>/pagar', methods=['POST'])
@jwt_required()
def pagar_boleto(obra_id, boleto_id):
    """Marca um boleto como pago"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        data = request.get_json() or {}
        
        boleto.status = 'Pago'
        boleto.data_pagamento = datetime.strptime(data.get('data_pagamento', date.today().isoformat()), '%Y-%m-%d').date()
        
        db.session.commit()
        
        logger.info(f"--- [LOG] Boleto {boleto_id} marcado como pago ---")
        return jsonify(boleto.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] pagar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/obras/<int:obra_id>/boletos/<int:boleto_id>', methods=['DELETE'])
@jwt_required()
def deletar_boleto(obra_id, boleto_id):
    """Deleta um boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        db.session.delete(boleto)
        db.session.commit()
        
        logger.info(f"--- [LOG] Boleto {boleto_id} deletado ---")
        return jsonify({"sucesso": True}), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] deletar_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/obras/<int:obra_id>/boletos/<int:boleto_id>/arquivo', methods=['GET'])
@jwt_required()
def obter_arquivo_boleto(obra_id, boleto_id):
    """Retorna o arquivo PDF do boleto"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boleto = db.session.get(Boleto, boleto_id)
        if not boleto or boleto.obra_id != obra_id:
            return jsonify({"erro": "Boleto não encontrado"}), 404
        
        if not boleto.arquivo_pdf:
            return jsonify({"erro": "Boleto não possui arquivo anexado"}), 404
        
        return jsonify({
            "arquivo_nome": boleto.arquivo_nome,
            "arquivo_base64": boleto.arquivo_pdf
        }), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] obter_arquivo_boleto: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/boletos/verificar-alertas', methods=['POST'])
@jwt_required()
def verificar_alertas_boletos():
    """Verifica boletos próximos do vencimento e cria notificações"""
    try:
        user = get_current_user()
        hoje = date.today()
        
        # Buscar boletos pendentes de todas as obras que o usuário tem acesso
        if user.role == 'master':
            boletos = Boleto.query.filter_by(status='Pendente').all()
        else:
            # Buscar obras que o usuário tem acesso
            obras_ids = [obra.id for obra in user.obras_permitidas]
            boletos = Boleto.query.filter(
                Boleto.obra_id.in_(obras_ids),
                Boleto.status == 'Pendente'
            ).all()
        
        alertas_criados = 0
        
        for boleto in boletos:
            dias_para_vencer = (boleto.data_vencimento - hoje).days
            obra = Obra.query.get(boleto.obra_id)
            obra_nome = obra.nome if obra else f"Obra {boleto.obra_id}"
            
            # Alerta 7 dias
            if dias_para_vencer <= 7 and dias_para_vencer > 3 and not boleto.alerta_7dias:
                criar_notificacao(
                    usuario_destino_id=boleto.usuario_id or user.id,
                    tipo='boleto_vencendo',
                    titulo='Boleto vence em 7 dias',
                    mensagem=f'O boleto "{boleto.descricao}" de {formatar_real(boleto.valor)} vence em {dias_para_vencer} dias ({boleto.data_vencimento.strftime("%d/%m/%Y")})',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
                boleto.alerta_7dias = True
                alertas_criados += 1
            
            # Alerta 3 dias
            elif dias_para_vencer <= 3 and dias_para_vencer > 0 and not boleto.alerta_3dias:
                criar_notificacao(
                    usuario_destino_id=boleto.usuario_id or user.id,
                    tipo='boleto_vencendo',
                    titulo='⚠️ Boleto vence em 3 dias',
                    mensagem=f'URGENTE: O boleto "{boleto.descricao}" de {formatar_real(boleto.valor)} vence em {dias_para_vencer} dias!',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
                boleto.alerta_3dias = True
                alertas_criados += 1
            
            # Alerta hoje
            elif dias_para_vencer == 0 and not boleto.alerta_hoje:
                criar_notificacao(
                    usuario_destino_id=boleto.usuario_id or user.id,
                    tipo='boleto_vencendo',
                    titulo='🚨 Boleto vence HOJE',
                    mensagem=f'ATENÇÃO: O boleto "{boleto.descricao}" de {formatar_real(boleto.valor)} vence HOJE!',
                    obra_id=boleto.obra_id,
                    item_id=boleto.id,
                    item_type='boleto'
                )
                boleto.alerta_hoje = True
                alertas_criados += 1
            
            # Marcar como vencido
            elif dias_para_vencer < 0:
                boleto.status = 'Vencido'
        
        db.session.commit()
        
        return jsonify({
            "sucesso": True,
            "alertas_criados": alertas_criados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] verificar_alertas_boletos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500


@boletos_bp.route('/obras/<int:obra_id>/boletos/resumo', methods=['GET'])
@jwt_required()
def resumo_boletos(obra_id):
    """Retorna resumo dos boletos para relatório financeiro"""
    try:
        user = get_current_user()
        if not user_has_access_to_obra(user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        boletos = Boleto.query.filter_by(obra_id=obra_id).all()
        
        hoje = date.today()
        
        # Calcular totais
        total_pendente = sum(b.valor for b in boletos if b.status == 'Pendente')
        total_vencido = sum(b.valor for b in boletos if b.status == 'Vencido' or (b.status == 'Pendente' and b.data_vencimento < hoje))
        total_pago = sum(b.valor for b in boletos if b.status == 'Pago')
        
        # Boletos vencendo em 7 dias
        vencendo_7_dias = [b.to_dict() for b in boletos if b.status == 'Pendente' and 0 <= (b.data_vencimento - hoje).days <= 7]
        
        return jsonify({
            "total_pendente": total_pendente,
            "total_vencido": total_vencido,
            "total_pago": total_pago,
            "quantidade_pendente": len([b for b in boletos if b.status == 'Pendente']),
            "quantidade_vencido": len([b for b in boletos if b.status == 'Vencido']),
            "quantidade_pago": len([b for b in boletos if b.status == 'Pago']),
            "vencendo_7_dias": vencendo_7_dias
        }), 200
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] resumo_boletos: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
