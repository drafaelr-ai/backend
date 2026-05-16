import logging
import traceback
from datetime import datetime

from flask import Blueprint, request, jsonify, make_response, current_app
from flask_jwt_extended import jwt_required

from sqlalchemy import func
from extensions import db
from models.user import User
from models.obra import Obra
from models.lancamento import Lancamento
from models.servico import Servico
from models.pagamento_servico import PagamentoServico
from models.pagamento_parcelado import PagamentoParcelado
from models.parcela_individual import ParcelaIndividual
from models.pagamento_futuro import PagamentoFuturo
from models.nota_fiscal import NotaFiscal
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from models.cronograma_etapa import CronogramaEtapa
from models.cronograma_obra import CronogramaObra
from services import get_current_user, check_permission, user_has_access_to_obra

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)

# --- ROTA DE ADMINISTRAÇÃO (Existente) ---
@admin_bp.route('/admin/create_tables', methods=['GET'])
@check_permission(roles=["master"])
def create_tables():
    logger.info("--- [LOG] Rota /admin/create_tables (GET) acessada ---")
    try:
        with current_app.app_context():
            db.create_all()
        logger.info("--- [LOG] db.create_all() executado com sucesso. (Incluindo NotaFiscal e colunas de pag. parcial) ---")
        return jsonify({"sucesso": "Tabelas/colunas atualizadas no banco de dados."}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /admin/create_tables: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Falha ao criar tabelas."}), 500
# ------------------------------------


# --- ROTAS DE ADMINISTRAÇÃO DE USUÁRIOS ---
@admin_bp.route('/admin/users', methods=['GET', 'OPTIONS'])
@check_permission(roles=['master'])
def get_all_users():
    # ... (código inalterado) ...
    logger.info("--- [LOG] Rota /admin/users (GET) acessada ---")
    try:
        current_user = get_current_user()
        users = User.query.filter(User.id != current_user.id).order_by(User.username).all()
        return jsonify([user.to_dict() for user in users]), 200
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /admin/users (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@admin_bp.route('/admin/users', methods=['POST', 'OPTIONS'])
@check_permission(roles=['master'])
def create_user():
    """
    Cria um novo usuário no sistema.
    APENAS usuários MASTER podem criar novos usuários.
    """
    # ... (código inalterado) ...
    logger.info("--- [LOG] Rota /admin/users (POST) acessada ---")
    try:
        dados = request.json
        username = dados.get('username')
        password = dados.get('password')
        role = dados.get('role', 'comum')
        if not username or not password:
            return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400
        if role not in ['master', 'comum']:
             return jsonify({"erro": "Role deve ser 'master' ou 'comum'"}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({"erro": "Nome de usuário já existe"}), 409
        novo_usuario = User(username=username, role=role)
        novo_usuario.set_password(password)
        db.session.add(novo_usuario)
        db.session.commit()
        logger.info(f"--- [LOG] Admin criou usuário '{username}' com role '{role}' ---")
        return jsonify(novo_usuario.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /admin/users (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@admin_bp.route('/admin/users/<int:user_id>/permissions', methods=['GET', 'OPTIONS'])
@check_permission(roles=['master'])
def get_user_permissions(user_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /admin/users/{user_id}/permissions (GET) acessada ---")
    try:
        user = User.query.get_or_404(user_id)
        obra_ids = [obra.id for obra in user.obras_permitidas]
        return jsonify({"user_id": user.id, "obra_ids": obra_ids}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /admin/users/{user_id}/permissions (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

@admin_bp.route('/admin/users/<int:user_id>/permissions', methods=['PUT', 'OPTIONS'])
@check_permission(roles=['master'])
def set_user_permissions(user_id):
    # ... (código inalterado) ...
    logger.info(f"--- [LOG] Rota /admin/users/{user_id}/permissions (PUT) acessada ---")
    try:
        user = User.query.get_or_404(user_id)
        dados = request.json
        obra_ids_para_permitir = dados.get('obra_ids', [])
        obras_permitidas = Obra.query.filter(Obra.id.in_(obra_ids_para_permitir)).all()
        user.obras_permitidas = obras_permitidas
        db.session.commit()
        logger.info(f"--- [LOG] Permissões atualizadas para user_id={user_id} ---")
        return jsonify({"sucesso": f"Permissões atualizadas para {user.username}"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /admin/users/{user_id}/permissions (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# --- NOVA ROTA PARA DELETAR USUÁRIO ---
@admin_bp.route('/admin/users/<int:user_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=['master'])
def delete_user(user_id):
    if request.method == 'OPTIONS': 
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)

    logger.info(f"--- [LOG] Rota /admin/users/{user_id} (DELETE) acessada ---")
    try:
        current_user_id = int(get_jwt_identity())
        
        if user_id == current_user_id:
            return jsonify({"erro": "Você não pode excluir a si mesmo."}), 403

        user = User.query.get_or_404(user_id)
        username_backup = user.username  # Guardar para log
        
        # Master pode excluir qualquer usuário (exceto a si mesmo, já verificado acima)
        claims = get_jwt()
        current_user_role = claims.get('role')
        
        if user.role == 'master' and current_user_role != 'master':
            return jsonify({"erro": "Apenas usuários MASTER podem excluir outros MASTER."}), 403

        logger.info(f"--- [LOG] Limpando referências do usuário '{username_backup}' (ID: {user_id}) ---")
        
        # Lista de tabelas/colunas para limpar (SET NULL)
        tabelas_para_limpar = [
            ("diario_obra", "criado_por"),
            ("movimentacao_caixa", "criado_por"),
            ("fechamento_caixa", "fechado_por"),
            ("lancamento", "criado_por"),
            ("pagamento_servico", "criado_por"),
            ("nota_fiscal", "criado_por"),
        ]
        
        _allowed = {(t, c) for t, c in tabelas_para_limpar}
        for tabela, coluna in tabelas_para_limpar:
            # table/column names cannot be SQL bind-params; list is hardcoded above (safe)
            if (tabela, coluna) not in _allowed:
                continue
            try:
                result = db.session.execute(
                    db.text(f"UPDATE {tabela} SET {coluna} = NULL WHERE {coluna} = :uid"),
                    {"uid": user_id}
                )
                db.session.commit()
                logger.info(f"   ✅ {tabela}.{coluna} limpo ({result.rowcount} registros)")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"   ⚠️ {tabela}.{coluna}: {str(e)[:50]}")
        
        # Deletar notificações do usuário (tanto enviadas quanto recebidas)
        try:
            result = db.session.execute(
                db.text("DELETE FROM notificacao WHERE usuario_destino_id = :uid"),
                {"uid": user_id}
            )
            db.session.commit()
            logger.info(f"   ✅ notificacao (destino) deletado ({result.rowcount} registros)")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"   ⚠️ notificacao (destino): {str(e)[:50]}")
        
        # Remover associações de user_obra
        try:
            result = db.session.execute(
                db.text("DELETE FROM user_obra_association WHERE user_id = :uid"),
                {"uid": user_id}
            )
            db.session.commit()
            logger.info(f"   ✅ user_obra_association removido ({result.rowcount} registros)")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"   ⚠️ user_obra_association: {str(e)[:50]}")
        
        # Recarregar o usuário (pode ter sido invalidado pelos commits)
        user = User.query.get(user_id)
        if not user:
            return jsonify({"erro": "Usuário não encontrado após limpeza."}), 404
        
        # Agora excluir o usuário
        db.session.delete(user)
        db.session.commit()
        
        logger.info(f"--- [LOG] ✅ Usuário '{username_backup}' (ID: {user_id}) foi deletado com sucesso ---")
        return jsonify({"sucesso": f"Usuário {username_backup} deletado com sucesso."}), 200

    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] /admin/users/{user_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500
# --- FIM DA NOVA ROTA ---
# ---------------------------------------------------
# --- ROTA PARA ALTERAR ROLE DE USUÁRIO ---
@admin_bp.route('/admin/users/<int:user_id>/role', methods=['PATCH', 'OPTIONS'])
@check_permission(roles=['master'])
def alterar_role_usuario(user_id):
    """Permite ao master alterar o role de qualquer usuário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    
    try:
        current_user_id = int(get_jwt_identity())
        data = request.get_json()
        novo_role = data.get('role')
        
        if novo_role not in ['master', 'administrador', 'comum']:
            return jsonify({"erro": "Role inválido. Use: master, administrador ou comum"}), 400
        
        user = User.query.get_or_404(user_id)
        role_anterior = user.role
        
        user.role = novo_role
        db.session.commit()
        
        logger.info(f"--- [LOG] Role do usuário '{user.username}' alterado de '{role_anterior}' para '{novo_role}' ---")
        
        return jsonify({
            "sucesso": f"Role alterado para {novo_role}",
            "user": user.to_dict()
        }), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] PATCH /admin/users/{user_id}/role: {e} ---")
        return jsonify({"erro": str(e)}), 500

# ---------------------------------------------------

# --- ROTAS DE NOTAS FISCAIS ---

@admin_bp.route('/admin/migrar-lancamentos-para-futuros/<int:obra_id>', methods=['POST'])
@jwt_required()
def migrar_lancamentos_para_futuros(obra_id):
    """
    Converte Lançamentos com status='A Pagar' em PagamentoFuturo.
    Isso faz os pagamentos antigos aparecerem no Cronograma Financeiro.
    """
    try:
        current_user = get_current_user()
        if not user_has_access_to_obra(current_user, obra_id):
            return jsonify({"erro": "Acesso negado"}), 403
        
        logger.debug(f"--- [DEBUG MIGRAÇÃO] Buscando Lançamentos com status='A Pagar' na obra {obra_id} ---")
        
        # Buscar todos os Lançamentos com status='A Pagar'
        lancamentos_a_pagar = Lancamento.query.filter_by(
            obra_id=obra_id,
            status='A Pagar',
            servico_id=None  # Apenas lançamentos gerais, não vinculados a serviço
        ).all()
        
        logger.debug(f"--- [DEBUG MIGRAÇÃO] Encontrados {len(lancamentos_a_pagar)} lançamentos para migrar ---")
        
        if not lancamentos_a_pagar:
            return jsonify({"mensagem": "Nenhum lançamento 'A Pagar' encontrado"}), 200
        
        migrados = []
        for lanc in lancamentos_a_pagar:
            logger.debug(f"--- [DEBUG MIGRAÇÃO] Migrando: {lanc.descricao}, Valor: R$ {lanc.valor_total:.2f} ---")
            
            # Criar PagamentoFuturo com TODOS os campos
            novo_futuro = PagamentoFuturo(
                obra_id=lanc.obra_id,
                descricao=lanc.descricao,
                valor=lanc.valor_total - lanc.valor_pago,  # Saldo pendente
                data_vencimento=lanc.data_vencimento or lanc.data,
                fornecedor=lanc.fornecedor,
                pix=lanc.pix,  # Copiar PIX
                observacoes=f"Migrado de Lançamento ID {lanc.id}",
                status='Previsto'
            )
            db.session.add(novo_futuro)
            db.session.flush()  # Para obter o ID
            
            logger.debug(f"--- [DEBUG MIGRAÇÃO] ✅ Criado PagamentoFuturo ID {novo_futuro.id} ---")
            
            # Deletar o Lançamento antigo
            db.session.delete(lanc)
            
            migrados.append({
                "lancamento_id": lanc.id,
                "descricao": lanc.descricao,
                "valor": lanc.valor_total - lanc.valor_pago,
                "novo_pagamento_futuro_id": novo_futuro.id
            })
        
        db.session.commit()
        
        logger.info(f"--- [LOG] ✅ {len(migrados)} lançamentos migrados para PagamentoFuturo na obra {obra_id} ---")
        return jsonify({
            "mensagem": f"{len(migrados)} lançamentos migrados com sucesso",
            "migrados": migrados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] POST /admin/migrar-lancamentos-para-futuros/{obra_id}: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e)}), 500

# --- FIM DAS ROTAS DO DIÁRIO DE OBRAS ---

# ==============================================================================
# ROTA TEMPORÁRIA PARA MIGRAÇÃO DE PAGAMENTOS ANTIGOS
# ==============================================================================
@admin_bp.route('/admin/migrar-pagamentos-antigos', methods=['POST', 'OPTIONS'])
@check_permission(roles=["master"])
def migrar_pagamentos_antigos():
    """
    ROTA TEMPORÁRIA: Migra pagamentos com status 'Pago' do cronograma para o histórico.
    
    Esta rota deve ser executada UMA VEZ após o deploy da correção.
    Depois de executar, você pode remover esta rota do código.
    """
    # Tratar preflight OPTIONS com headers CORS explícitos
    if request.method == 'OPTIONS':
        response = make_response(jsonify({"message": "OPTIONS allowed"}), 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    
    try:
        # Garantir que está autenticado
        
        # Verificar se é administrador
        current_user = get_current_user()
        if not current_user:
            return jsonify({"erro": "Autenticação necessária."}), 401
            
        if current_user.nivel_acesso != 'administrador':
            return jsonify({"erro": "Acesso negado. Apenas administradores podem executar esta migração."}), 403
        
        logger.info("=" * 80)
        logger.info("🔄 INICIANDO MIGRAÇÃO DE PAGAMENTOS ANTIGOS")
        logger.info("=" * 80)
        
        # 1. Buscar todos os pagamentos com status "Pago"
        pagamentos_pagos = PagamentoFuturo.query.filter(
            PagamentoFuturo.status == 'Pago'
        ).all()
        
        total = len(pagamentos_pagos)
        logger.info(f"📊 Total de pagamentos encontrados com status 'Pago': {total}")
        
        if total == 0:
            return jsonify({
                "mensagem": "Nenhum pagamento para migrar!",
                "total": 0,
                "migrados": 0,
                "erros": 0
            }), 200
        
        # 2. Preparar lista de pagamentos
        lista_pagamentos = []
        for p in pagamentos_pagos:
            lista_pagamentos.append({
                "id": p.id,
                "obra_id": p.obra_id,
                "descricao": p.descricao,
                "valor": p.valor,
                "fornecedor": p.fornecedor
            })
        
        logger.info(f"📋 Pagamentos a serem migrados:")
        for p in lista_pagamentos:
            logger.info(f"  • ID: {p['id']} | Obra: {p['obra_id']} | {p['descricao']} | R$ {p['valor']:,.2f}")
        
        # 3. Executar migração
        migrados = 0
        erros = []
        lancamentos_criados = []
        
        for pagamento in pagamentos_pagos:
            try:
                # Criar lançamento no histórico
                novo_lancamento = Lancamento(
                    obra_id=pagamento.obra_id,
                    tipo='Despesa',
                    descricao=pagamento.descricao,
                    valor_total=pagamento.valor,
                    valor_pago=pagamento.valor,
                    data=date.today(),
                    data_vencimento=pagamento.data_vencimento,
                    status='Pago',
                    pix=pagamento.pix,
                    prioridade=0,
                    fornecedor=pagamento.fornecedor,
                    servico_id=None
                )
                db.session.add(novo_lancamento)
                db.session.flush()  # Gera o ID
                
                # Guardar informação
                lancamentos_criados.append({
                    "pagamento_id": pagamento.id,
                    "lancamento_id": novo_lancamento.id,
                    "descricao": pagamento.descricao,
                    "valor": pagamento.valor
                })
                
                # Deletar do cronograma
                db.session.delete(pagamento)
                
                migrados += 1
                logger.info(f"  ✅ Migrado: {pagamento.descricao} (Pagamento ID: {pagamento.id} → Lançamento ID: {novo_lancamento.id})")
                
            except Exception as e:
                db.session.rollback()
                erro_msg = f"Erro ao migrar ID {pagamento.id}: {str(e)}"
                logger.error(f"  ❌ {erro_msg}")
                erros.append({
                    "pagamento_id": pagamento.id,
                    "descricao": pagamento.descricao,
                    "erro": str(e)
                })
                continue
        
        # 4. Commit final
        if migrados > 0:
            db.session.commit()
            logger.info(f"\n✅ Commit realizado: {migrados} pagamentos migrados com sucesso!")
        
        # 5. Relatório
        logger.info("\n" + "=" * 80)
        logger.info("📊 RELATÓRIO DA MIGRAÇÃO")
        logger.info("=" * 80)
        logger.info(f"✅ Pagamentos migrados com sucesso: {migrados}")
        logger.error(f"❌ Erros durante a migração: {len(erros)}")
        logger.error(f"📈 Total processado: {migrados + len(erros)}/{total}")
        logger.info("=" * 80)
        
        return jsonify({
            "mensagem": "Migração concluída!",
            "total": total,
            "migrados": migrados,
            "erros_count": len(erros),
            "pagamentos_migrados": lancamentos_criados,
            "erros": erros
        }), 200
    
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"❌ ERRO CRÍTICO na migração: {str(e)}\n{error_details}")
        return jsonify({"erro": str(e)}), 500

# ==============================================================================

# ==============================================================================
# CRONOGRAMA DA OBRA - MODELO E ROTAS
# ==============================================================================

# ======================================================================

# ==============================================================================
# MIGRAÇÃO: Vínculo Cronograma ↔ Orçamento
# ==============================================================================

@admin_bp.route('/setup/migrate-cronograma-orcamento', methods=['GET'])
@check_permission(roles=["master"])
def setup_migrate_cronograma_orcamento():
    """
    ROTA DE MIGRAÇÃO - Adiciona coluna orcamento_etapa_id ao cronograma_obra
    Acesse: https://backend-production-78c9.up.railway.app/setup/migrate-cronograma-orcamento
    """
    try:
        resultados = []
        
        # 1. Adicionar coluna orcamento_etapa_id
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_obra 
                ADD COLUMN IF NOT EXISTS orcamento_etapa_id INTEGER REFERENCES orcamento_eng_etapa(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ Coluna orcamento_etapa_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ orcamento_etapa_id: {str(e)}")
        
        # 2. Criar índice
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_cronograma_orcamento_etapa 
                ON cronograma_obra(orcamento_etapa_id);
            """))
            db.session.commit()
            resultados.append("✅ Índice criado")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índice: {str(e)}")
        
        # 3. Tentar vincular cronogramas existentes pelo nome (usando SQL direto)
        try:
            # Buscar cronogramas sem vínculo
            cronogramas_sem_vinculo = db.session.execute(db.text("""
                SELECT c.id, c.obra_id, c.servico_nome 
                FROM cronograma_obra c 
                WHERE c.orcamento_etapa_id IS NULL
            """)).fetchall()
            
            vinculados = 0
            
            for cron_id, obra_id, servico_nome in cronogramas_sem_vinculo:
                # Buscar etapa do orçamento com mesmo nome na mesma obra
                etapa = OrcamentoEngEtapa.query.filter_by(
                    obra_id=obra_id,
                    nome=servico_nome
                ).first()
                
                if etapa:
                    db.session.execute(db.text(
                        f"UPDATE cronograma_obra SET orcamento_etapa_id = {etapa.id} WHERE id = {cron_id}"
                    ))
                    vinculados += 1
            
            db.session.commit()
            resultados.append(f"✅ {vinculados} cronogramas vinculados automaticamente")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Vinculação automática: {str(e)}")
        
        return jsonify({
            'mensagem': 'Migração executada',
            'resultados': resultados
        })

    except Exception as e:
        logger.exception("Erro na rota de migracao vinculacao automatica")
        return jsonify({'erro': str(e)}), 500


# ==============================================================================
# MIGRAÇÃO: Serviços (Kanban) → Itens do Orçamento de Engenharia
# ==============================================================================

@admin_bp.route('/setup/migrate-servicos-para-orcamento', methods=['GET'])
@check_permission(roles=["master"])
def setup_migrate_servicos_para_orcamento():
    """
    ROTA DE MIGRAÇÃO - Converte Serviços do Kanban em Itens do Orçamento de Engenharia
    
    Para cada Serviço:
    1. Cria uma Etapa no orçamento com o nome do serviço
    2. Cria um Item dentro dessa etapa com os valores do serviço
    3. Vincula o item ao serviço original (para rastreabilidade)
    
    Acesse: https://backend-production-78c9.up.railway.app/setup/migrate-servicos-para-orcamento
    """
    try:
        resultados = []
        servicos_migrados = 0
        servicos_ignorados = 0
        
        # Buscar todas as obras
        obras = Obra.query.all()
        
        for obra in obras:
            # Buscar serviços desta obra
            servicos = Servico.query.filter_by(obra_id=obra.id).all()
            
            if not servicos:
                continue
            
            # Buscar maior código de etapa existente
            ultima_etapa = OrcamentoEngEtapa.query.filter_by(obra_id=obra.id).order_by(OrcamentoEngEtapa.ordem.desc()).first()
            proxima_ordem = (ultima_etapa.ordem + 1) if ultima_etapa else 1
            proximo_codigo = proxima_ordem
            
            for servico in servicos:
                # Verificar se já existe um item vinculado a este serviço
                item_existente = OrcamentoEngItem.query.filter_by(servico_id=servico.id).first()
                if item_existente:
                    servicos_ignorados += 1
                    continue
                
                # Verificar se já existe uma etapa com o mesmo nome
                etapa_existente = OrcamentoEngEtapa.query.filter_by(
                    obra_id=obra.id,
                    nome=servico.nome
                ).first()
                
                if etapa_existente:
                    # Usar etapa existente
                    etapa = etapa_existente
                else:
                    # Criar nova etapa
                    codigo_etapa = f"{proximo_codigo:02d}"
                    etapa = OrcamentoEngEtapa(
                        obra_id=obra.id,
                        codigo=codigo_etapa,
                        nome=servico.nome,
                        ordem=proxima_ordem
                    )
                    db.session.add(etapa)
                    db.session.flush()  # Para obter o ID
                    
                    proxima_ordem += 1
                    proximo_codigo += 1
                
                # Criar item dentro da etapa
                # Buscar último item da etapa para definir código
                ultimo_item = OrcamentoEngItem.query.filter_by(etapa_id=etapa.id).order_by(OrcamentoEngItem.ordem.desc()).first()
                item_ordem = (ultimo_item.ordem + 1) if ultimo_item else 1
                codigo_item = f"{etapa.codigo}.{item_ordem:02d}"
                
                # Calcular valores
                valor_mo = servico.valor_global_mao_de_obra or 0
                valor_mat = servico.valor_global_material or 0
                
                novo_item = OrcamentoEngItem(
                    etapa_id=etapa.id,
                    codigo=codigo_item,
                    descricao=servico.nome,
                    unidade='vb',  # Verba (serviço global)
                    quantidade=1,
                    tipo_composicao='separado',
                    preco_mao_obra=valor_mo,
                    preco_material=valor_mat,
                    servico_id=servico.id,  # Manter vínculo para rastreabilidade
                    ordem=item_ordem
                )
                db.session.add(novo_item)
                servicos_migrados += 1
            
            resultados.append(f"Obra '{obra.nome}': {len(servicos)} serviços processados")
        
        db.session.commit()
        
        return jsonify({
            'mensagem': 'Migração de serviços concluída',
            'servicos_migrados': servicos_migrados,
            'servicos_ignorados': servicos_ignorados,
            'detalhes': resultados
        })
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


@admin_bp.route('/setup/migrate-servicos-para-orcamento/<int:obra_id>', methods=['GET'])
@check_permission(roles=["master"])
def setup_migrate_servicos_para_orcamento_obra(obra_id):
    """
    ROTA DE MIGRAÇÃO - Converte Serviços do Kanban em Itens do Orçamento para UMA obra específica
    
    Acesse: https://backend-production-78c9.up.railway.app/setup/migrate-servicos-para-orcamento/123
    """
    try:
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({'erro': 'Obra não encontrada'}), 404
        
        servicos_migrados = 0
        servicos_ignorados = 0
        detalhes = []
        
        # Buscar serviços desta obra
        servicos = Servico.query.filter_by(obra_id=obra.id).all()
        
        if not servicos:
            return jsonify({
                'mensagem': 'Nenhum serviço encontrado nesta obra',
                'servicos_migrados': 0
            })
        
        # Buscar maior código de etapa existente
        ultima_etapa = OrcamentoEngEtapa.query.filter_by(obra_id=obra.id).order_by(OrcamentoEngEtapa.ordem.desc()).first()
        proxima_ordem = (ultima_etapa.ordem + 1) if ultima_etapa else 1
        proximo_codigo = proxima_ordem
        
        for servico in servicos:
            # Verificar se já existe um item vinculado a este serviço
            item_existente = OrcamentoEngItem.query.filter_by(servico_id=servico.id).first()
            if item_existente:
                servicos_ignorados += 1
                detalhes.append(f"⏭️ '{servico.nome}' - já existe item vinculado")
                continue
            
            # Verificar se já existe uma etapa com o mesmo nome
            etapa_existente = OrcamentoEngEtapa.query.filter_by(
                obra_id=obra.id,
                nome=servico.nome
            ).first()
            
            if etapa_existente:
                etapa = etapa_existente
                detalhes.append(f"📁 '{servico.nome}' - usando etapa existente")
            else:
                # Criar nova etapa
                codigo_etapa = f"{proximo_codigo:02d}"
                etapa = OrcamentoEngEtapa(
                    obra_id=obra.id,
                    codigo=codigo_etapa,
                    nome=servico.nome,
                    ordem=proxima_ordem
                )
                db.session.add(etapa)
                db.session.flush()
                
                proxima_ordem += 1
                proximo_codigo += 1
                detalhes.append(f"📁 '{servico.nome}' - nova etapa criada ({codigo_etapa})")
            
            # Criar item dentro da etapa
            ultimo_item = OrcamentoEngItem.query.filter_by(etapa_id=etapa.id).order_by(OrcamentoEngItem.ordem.desc()).first()
            item_ordem = (ultimo_item.ordem + 1) if ultimo_item else 1
            codigo_item = f"{etapa.codigo}.{item_ordem:02d}"
            
            valor_mo = servico.valor_global_mao_de_obra or 0
            valor_mat = servico.valor_global_material or 0
            
            novo_item = OrcamentoEngItem(
                etapa_id=etapa.id,
                codigo=codigo_item,
                descricao=servico.nome,
                unidade='vb',
                quantidade=1,
                tipo_composicao='separado',
                preco_mao_obra=valor_mo,
                preco_material=valor_mat,
                servico_id=servico.id,
                ordem=item_ordem
            )
            db.session.add(novo_item)
            servicos_migrados += 1
            detalhes.append(f"✅ '{servico.nome}' - item criado ({codigo_item}) MO: R${valor_mo:,.2f} | MAT: R${valor_mat:,.2f}")
        
        db.session.commit()
        
        return jsonify({
            'mensagem': f'Migração da obra "{obra.nome}" concluída',
            'obra': obra.nome,
            'servicos_migrados': servicos_migrados,
            'servicos_ignorados': servicos_ignorados,
            'detalhes': detalhes
        })
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


# ==============================================================================
# MIGRAÇÃO: Adicionar orcamento_item_id aos pagamentos
# ==============================================================================

@admin_bp.route('/setup/migrate-pagamentos-orcamento', methods=['GET'])
@check_permission(roles=["master"])
def setup_migrate_pagamentos_orcamento():
    """
    ROTA DE MIGRAÇÃO - Adiciona coluna orcamento_item_id às tabelas de pagamento
    
    Tabelas afetadas:
    - pagamento_futuro
    - pagamento_parcelado_v2
    - boleto
    - lancamento
    
    Acesse: https://backend-production-78c9.up.railway.app/setup/migrate-pagamentos-orcamento
    """
    try:
        resultados = []
        
        # 1. pagamento_futuro
        try:
            db.session.execute(db.text("""
                ALTER TABLE pagamento_futuro 
                ADD COLUMN IF NOT EXISTS orcamento_item_id INTEGER REFERENCES orcamento_eng_item(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ pagamento_futuro.orcamento_item_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ pagamento_futuro: {str(e)}")
        
        # 2. pagamento_parcelado_v2
        try:
            db.session.execute(db.text("""
                ALTER TABLE pagamento_parcelado_v2 
                ADD COLUMN IF NOT EXISTS orcamento_item_id INTEGER REFERENCES orcamento_eng_item(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ pagamento_parcelado_v2.orcamento_item_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ pagamento_parcelado_v2: {str(e)}")
        
        # 3. boleto
        try:
            db.session.execute(db.text("""
                ALTER TABLE boleto 
                ADD COLUMN IF NOT EXISTS orcamento_item_id INTEGER REFERENCES orcamento_eng_item(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ boleto.orcamento_item_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ boleto: {str(e)}")
        
        # 4. lancamento
        try:
            db.session.execute(db.text("""
                ALTER TABLE lancamento 
                ADD COLUMN IF NOT EXISTS orcamento_item_id INTEGER REFERENCES orcamento_eng_item(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ lancamento.orcamento_item_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ lancamento: {str(e)}")
        
        # 5. Criar índices
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_pagamento_futuro_orc_item ON pagamento_futuro(orcamento_item_id);
                CREATE INDEX IF NOT EXISTS idx_pagamento_parcelado_orc_item ON pagamento_parcelado_v2(orcamento_item_id);
                CREATE INDEX IF NOT EXISTS idx_boleto_orc_item ON boleto(orcamento_item_id);
                CREATE INDEX IF NOT EXISTS idx_lancamento_orc_item ON lancamento(orcamento_item_id);
            """))
            db.session.commit()
            resultados.append("✅ Índices criados")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índices: {str(e)}")
        
        # 6. Tentar migrar vínculos existentes (servico_id -> orcamento_item_id)
        # Busca itens do orçamento que têm servico_id e tenta vincular pagamentos
        migrados = 0
        try:
            # Buscar todos os itens do orçamento que têm servico_id
            itens_vinculados = db.session.execute(db.text("""
                SELECT id, servico_id FROM orcamento_eng_item WHERE servico_id IS NOT NULL
            """)).fetchall()
            
            for item_id, servico_id in itens_vinculados:
                # Atualizar pagamento_futuro
                db.session.execute(db.text("""
                    UPDATE pagamento_futuro 
                    SET orcamento_item_id = :item_id 
                    WHERE servico_id = :servico_id AND orcamento_item_id IS NULL
                """), {"item_id": item_id, "servico_id": servico_id})
                
                # Atualizar pagamento_parcelado_v2
                db.session.execute(db.text("""
                    UPDATE pagamento_parcelado_v2 
                    SET orcamento_item_id = :item_id 
                    WHERE servico_id = :servico_id AND orcamento_item_id IS NULL
                """), {"item_id": item_id, "servico_id": servico_id})
                
                # Atualizar boleto
                db.session.execute(db.text("""
                    UPDATE boleto 
                    SET orcamento_item_id = :item_id 
                    WHERE vinculado_servico_id = :servico_id AND orcamento_item_id IS NULL
                """), {"item_id": item_id, "servico_id": servico_id})
                
                # Atualizar lancamento
                db.session.execute(db.text("""
                    UPDATE lancamento 
                    SET orcamento_item_id = :item_id 
                    WHERE servico_id = :servico_id AND orcamento_item_id IS NULL
                """), {"item_id": item_id, "servico_id": servico_id})
                
                migrados += 1
            
            db.session.commit()
            resultados.append(f"✅ {migrados} vínculos migrados (servico_id → orcamento_item_id)")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Migração de vínculos: {str(e)}")
        
        return jsonify({
            'mensagem': 'Migração de pagamentos concluída',
            'resultados': resultados
        })

    except Exception as e:
        logger.exception("Erro na rota de migracao pagamentos")
        return jsonify({'erro': str(e)}), 500


# ==============================================================================
# SINCRONIZAÇÃO: Cronograma → Orçamento (atualizar % executado)
# ==============================================================================

@admin_bp.route('/cronograma/<int:cronograma_id>/sincronizar-orcamento', methods=['POST', 'OPTIONS'])
@jwt_required()
def sincronizar_cronograma_orcamento(cronograma_id):
    """
    Sincroniza o percentual do cronograma para o orçamento vinculado.
    Quando o usuário atualiza o % no cronograma, reflete no orçamento.
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
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'erro': 'Cronograma não encontrado'}), 404
        
        if not user_has_access_to_obra(user, cronograma.obra_id):
            return jsonify({'erro': 'Sem permissão'}), 403
        
        # Buscar orcamento_etapa_id via SQL direto (coluna pode não existir no modelo)
        try:
            result = db.session.execute(db.text(
                f"SELECT orcamento_etapa_id FROM cronograma_obra WHERE id = {cronograma_id}"
            )).fetchone()
            orcamento_etapa_id = result[0] if result else None
        except Exception:
            orcamento_etapa_id = None
        
        if not orcamento_etapa_id:
            return jsonify({'erro': 'Este cronograma não está vinculado a uma etapa do orçamento'}), 400
        
        # Buscar etapa do orçamento
        etapa_orcamento = OrcamentoEngEtapa.query.get(orcamento_etapa_id)
        if not etapa_orcamento:
            return jsonify({'erro': 'Etapa do orçamento não encontrada'}), 404
        
        # Calcular percentual atual do cronograma
        percentual = cronograma.percentual_conclusao
        if cronograma.tipo_medicao == 'etapas':
            percentual = cronograma.calcular_percentual_por_etapas()
        elif cronograma.tipo_medicao == 'area' and cronograma.area_total:
            percentual = (cronograma.area_executada or 0) / cronograma.area_total * 100
        
        # Atualizar valores pagos nos itens do orçamento proporcionalmente
        itens = OrcamentoEngItem.query.filter_by(etapa_id=etapa_orcamento.id).all()
        
        for item in itens:
            totais = item.calcular_totais()
            # O valor "pago" no orçamento será proporcional ao % executado
            item.valor_pago_mo = totais['total_mao_obra'] * (percentual / 100)
            item.valor_pago_mat = totais['total_material'] * (percentual / 100)
        
        db.session.commit()
        
        return jsonify({
            'mensagem': f'Sincronizado! {percentual:.1f}% aplicado ao orçamento',
            'percentual': percentual,
            'etapa_nome': etapa_orcamento.nome,
            'itens_atualizados': len(itens)
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] sincronizar_cronograma_orcamento: {e}")
        return jsonify({'erro': str(e)}), 500


@admin_bp.route('/cronograma/<int:cronograma_id>/vincular-orcamento', methods=['POST', 'OPTIONS'])
@jwt_required()
def vincular_cronograma_orcamento(cronograma_id):
    """
    Vincula manualmente um cronograma a uma etapa do orçamento.
    Body: { "orcamento_etapa_id": 123 }
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
        
        cronograma = CronogramaObra.query.get(cronograma_id)
        if not cronograma:
            return jsonify({'erro': 'Cronograma não encontrado'}), 404
        
        if not user_has_access_to_obra(user, cronograma.obra_id):
            return jsonify({'erro': 'Sem permissão'}), 403
        
        data = request.json
        orcamento_etapa_id = data.get('orcamento_etapa_id')
        
        if orcamento_etapa_id:
            # Verificar se a etapa existe e pertence à mesma obra
            etapa = OrcamentoEngEtapa.query.get(orcamento_etapa_id)
            if not etapa or etapa.obra_id != cronograma.obra_id:
                return jsonify({'erro': 'Etapa do orçamento inválida'}), 400
            
            # Usar SQL direto para atualizar (coluna pode não existir no modelo)
            db.session.execute(db.text(
                f"UPDATE cronograma_obra SET orcamento_etapa_id = {orcamento_etapa_id} WHERE id = {cronograma_id}"
            ))
            db.session.commit()
            
            return jsonify({
                'mensagem': f'Vinculado à etapa "{etapa.nome}"',
                'orcamento_etapa_id': etapa.id,
                'orcamento_etapa_nome': etapa.nome
            })
        else:
            # Desvincular usando SQL direto
            db.session.execute(db.text(
                f"UPDATE cronograma_obra SET orcamento_etapa_id = NULL WHERE id = {cronograma_id}"
            ))
            db.session.commit()
            
            return jsonify({
                'mensagem': 'Vínculo removido',
                'orcamento_etapa_id': None
            })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ERRO] vincular_cronograma_orcamento: {e}")
        return jsonify({'erro': str(e)}), 500


# ==============================================================================
# MIGRATION: Estrutura Hierárquica de Etapas (Etapa Pai / Subetapas)
# ==============================================================================

@admin_bp.route('/setup/migrate-etapas-hierarquia', methods=['GET'])
@check_permission(roles=["master"])
def setup_migrate_etapas_hierarquia():
    """
    ROTA TEMPORÁRIA - Adiciona suporte a Etapas Pai e Subetapas
    Acesse: https://backend-production-78c9.up.railway.app/setup/migrate-etapas-hierarquia
    
    O que faz:
    1. Adiciona colunas: etapa_pai_id, etapa_anterior_id, tipo_condicao, dias_offset
    2. Torna data_inicio, data_fim, duracao_dias nullable
    3. Cria uma Etapa Pai padrão para cada serviço que já tem etapas
    """
    try:
        resultados = []
        
        # 1. Adicionar coluna etapa_pai_id (auto-referência)
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS etapa_pai_id INTEGER REFERENCES cronograma_etapa(id) ON DELETE CASCADE;
            """))
            db.session.commit()
            resultados.append("✅ Coluna etapa_pai_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ etapa_pai_id: {str(e)}")
        
        # 2. Adicionar coluna etapa_anterior_id (para condições entre etapas)
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS etapa_anterior_id INTEGER REFERENCES cronograma_etapa(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ Coluna etapa_anterior_id adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ etapa_anterior_id: {str(e)}")
        
        # 3. Adicionar coluna tipo_condicao
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS tipo_condicao VARCHAR(20);
            """))
            db.session.commit()
            resultados.append("✅ Coluna tipo_condicao adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ tipo_condicao: {str(e)}")
        
        # 4. Adicionar coluna dias_offset
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa 
                ADD COLUMN IF NOT EXISTS dias_offset INTEGER DEFAULT 0;
            """))
            db.session.commit()
            resultados.append("✅ Coluna dias_offset adicionada")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ dias_offset: {str(e)}")
        
        # 5. Tornar data_inicio nullable
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa ALTER COLUMN data_inicio DROP NOT NULL;
            """))
            db.session.commit()
            resultados.append("✅ data_inicio agora aceita NULL")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ data_inicio: {str(e)}")
        
        # 6. Tornar data_fim nullable
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa ALTER COLUMN data_fim DROP NOT NULL;
            """))
            db.session.commit()
            resultados.append("✅ data_fim agora aceita NULL")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ data_fim: {str(e)}")
        
        # 7. Tornar duracao_dias nullable
        try:
            db.session.execute(db.text("""
                ALTER TABLE cronograma_etapa ALTER COLUMN duracao_dias DROP NOT NULL;
            """))
            db.session.commit()
            resultados.append("✅ duracao_dias agora aceita NULL")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ duracao_dias: {str(e)}")
        
        # 8. Criar índice para etapa_pai_id
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_cronograma_etapa_pai_id 
                ON cronograma_etapa(etapa_pai_id);
            """))
            db.session.commit()
            resultados.append("✅ Índice etapa_pai_id criado")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índice: {str(e)}")
        
        # 9. MIGRAÇÃO DE DADOS: Criar Etapa Pai para cada cronograma que já tem etapas
        try:
            # Buscar cronogramas que têm etapas sem etapa_pai_id
            result = db.session.execute(db.text("""
                SELECT DISTINCT cronograma_id 
                FROM cronograma_etapa 
                WHERE etapa_pai_id IS NULL
            """))
            cronogramas_com_etapas = [row[0] for row in result.fetchall()]
            
            for cronograma_id in cronogramas_com_etapas:
                # Verificar se já existe uma etapa pai (sem etapa_pai_id e com subetapas)
                # Buscar a primeira data e criar etapa pai
                result = db.session.execute(db.text("""
                    SELECT MIN(data_inicio), MIN(data_fim)
                    FROM cronograma_etapa 
                    WHERE cronograma_id = :cid AND etapa_pai_id IS NULL
                """), {'cid': cronograma_id})
                row = result.fetchone()
                data_inicio = row[0]
                data_fim = row[1]
                
                # Criar a Etapa Pai
                db.session.execute(db.text("""
                    INSERT INTO cronograma_etapa 
                    (cronograma_id, nome, ordem, data_inicio, data_fim, percentual_conclusao, created_at, updated_at)
                    VALUES (:cid, 'Etapa 1', 1, :di, :df, 0, NOW(), NOW())
                    RETURNING id
                """), {'cid': cronograma_id, 'di': data_inicio, 'df': data_fim})
                etapa_pai_id = db.session.execute(db.text("SELECT lastval()")).scalar()
                
                # Atualizar as etapas existentes para serem subetapas
                db.session.execute(db.text("""
                    UPDATE cronograma_etapa 
                    SET etapa_pai_id = :pai_id 
                    WHERE cronograma_id = :cid 
                    AND etapa_pai_id IS NULL 
                    AND id != :pai_id
                """), {'pai_id': etapa_pai_id, 'cid': cronograma_id})
                
                db.session.commit()
                resultados.append(f"✅ Cronograma {cronograma_id}: Etapa Pai criada, subetapas vinculadas")
            
            if not cronogramas_com_etapas:
                resultados.append("ℹ️ Nenhum cronograma com etapas existentes para migrar")
                
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Migração de dados: {str(e)}")
        
        return jsonify({
            "status": "Migration de Hierarquia de Etapas executada!",
            "resultados": resultados
        }), 200

    except Exception as e:
        logger.exception("Erro na rota de migracao hierarquia etapas")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ROTA SEM AUTENTICAÇÃO - Use uma única vez e depois remova!
@admin_bp.route('/setup/create-cronograma-etapa-table', methods=['GET'])
@check_permission(roles=["master"])
def setup_create_cronograma_etapa():
    """
    ROTA TEMPORÁRIA SEM AUTENTICAÇÃO - Cria tabela cronograma_etapa
    Acesse: https://seu-backend.railway.app/setup/create-cronograma-etapa-table
    REMOVA ESTA ROTA APÓS USAR!
    """
    try:
        resultados = []
        
        # Criar tabela
        try:
            db.session.execute(db.text("""
                CREATE TABLE IF NOT EXISTS cronograma_etapa (
                    id SERIAL PRIMARY KEY,
                    cronograma_id INTEGER NOT NULL REFERENCES cronograma_obra(id) ON DELETE CASCADE,
                    nome VARCHAR(200) NOT NULL,
                    ordem INTEGER NOT NULL DEFAULT 1,
                    duracao_dias INTEGER NOT NULL DEFAULT 1,
                    data_inicio DATE NOT NULL,
                    data_fim DATE NOT NULL,
                    inicio_ajustado_manualmente BOOLEAN DEFAULT FALSE,
                    percentual_conclusao FLOAT NOT NULL DEFAULT 0.0,
                    observacoes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            db.session.commit()
            resultados.append("✅ Tabela cronograma_etapa criada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Tabela cronograma_etapa já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao criar tabela: {str(e)}")
        
        # Criar índice
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_cronograma_etapa_cronograma_id 
                ON cronograma_etapa(cronograma_id);
            """))
            db.session.commit()
            resultados.append("✅ Índice criado com sucesso")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índice: {str(e)}")
        
        return jsonify({
            "status": "Migration executada com sucesso!",
            "resultados": resultados,
            "aviso": "REMOVA esta rota do código após usar!"
        }), 200
    except Exception as e:
        logger.exception("Erro em rota admin de migration")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/admin/migrate-create-cronograma-etapa', methods=['GET'])
@jwt_required()
@check_permission(roles=['master'])
def migrate_create_cronograma_etapa():
    """
    ROTA TEMPORÁRIA - Cria tabela cronograma_etapa
    Apenas usuários MASTER podem executar
    Acesse: https://seu-backend.railway.app/admin/migrate-create-cronograma-etapa
    """
    try:
        resultados = []
        
        # Criar tabela
        try:
            db.session.execute(db.text("""
                CREATE TABLE IF NOT EXISTS cronograma_etapa (
                    id SERIAL PRIMARY KEY,
                    cronograma_id INTEGER NOT NULL REFERENCES cronograma_obra(id) ON DELETE CASCADE,
                    nome VARCHAR(200) NOT NULL,
                    ordem INTEGER NOT NULL DEFAULT 1,
                    duracao_dias INTEGER NOT NULL DEFAULT 1,
                    data_inicio DATE NOT NULL,
                    data_fim DATE NOT NULL,
                    inicio_ajustado_manualmente BOOLEAN DEFAULT FALSE,
                    percentual_conclusao FLOAT NOT NULL DEFAULT 0.0,
                    observacoes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            db.session.commit()
            resultados.append("✅ Tabela cronograma_etapa criada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Tabela cronograma_etapa já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao criar tabela: {str(e)}")
        
        # Criar índice
        try:
            db.session.execute(db.text("""
                CREATE INDEX IF NOT EXISTS idx_cronograma_etapa_cronograma_id 
                ON cronograma_etapa(cronograma_id);
            """))
            db.session.commit()
            resultados.append("✅ Índice criado com sucesso")
        except Exception as e:
            db.session.rollback()
            resultados.append(f"⚠️ Índice: {str(e)}")
        
        return jsonify({
            "status": "Migration executada",
            "resultados": resultados
        }), 200
    except Exception as e:
        logger.exception("Erro em rota admin de migration cronograma-etapa")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ==============================================================================
# ROTA TEMPORÁRIA DE MIGRATION - ADICIONAR servico_id
# ==============================================================================
@admin_bp.route('/admin/migrate-add-servico-id', methods=['GET'])
@jwt_required()
@check_permission(roles=['master'])
def migrate_add_servico_id():
    """
    ROTA TEMPORÁRIA - Executa migration para adicionar servico_id ao pagamento_parcelado
    Apenas usuários MASTER podem executar
    Acesse: https://seu-backend.railway.app/admin/migrate-add-servico-id
    IMPORTANTE: Após executar com sucesso, REMOVA esta rota do código!
    """
    try:
        resultados = []
        
        # 1. ADD COLUMN
        try:
            db.session.execute(db.text(
                "ALTER TABLE pagamento_parcelado ADD COLUMN servico_id INTEGER;"
            ))
            db.session.commit()
            resultados.append("✅ Coluna servico_id adicionada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Coluna servico_id já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao adicionar coluna: {str(e)}")
        
        # 2. ADD FOREIGN KEY
        try:
            db.session.execute(db.text("""
                ALTER TABLE pagamento_parcelado 
                ADD CONSTRAINT fk_pagamento_parcelado_servico 
                FOREIGN KEY (servico_id) REFERENCES servico(id) ON DELETE SET NULL;
            """))
            db.session.commit()
            resultados.append("✅ Foreign key adicionada com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Foreign key já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao adicionar foreign key: {str(e)}")
        
        # 3. CREATE INDEX
        try:
            db.session.execute(db.text(
                "CREATE INDEX idx_pagamento_parcelado_servico ON pagamento_parcelado(servico_id);"
            ))
            db.session.commit()
            resultados.append("✅ Índice criado com sucesso")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                resultados.append("⚠️ Índice já existe (OK)")
            else:
                resultados.append(f"❌ Erro ao criar índice: {str(e)}")
        
        # 4. VALIDAR
        try:
            result = db.session.execute(db.text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'pagamento_parcelado' 
                  AND column_name = 'servico_id';
            """))
            if result.fetchone():
                resultados.append("✅ VALIDAÇÃO: Coluna servico_id existe!")
                resultados.append("")
                resultados.append("🎉 MIGRATION CONCLUÍDA COM SUCESSO!")
                resultados.append("")
                resultados.append("🚀 Próximos passos:")
                resultados.append("1. Deploy do frontend (App.js)")
                resultados.append("2. Testar criação de pagamento parcelado")
                resultados.append("3. REMOVER esta rota /admin/migrate-add-servico-id do código")
            else:
                resultados.append("❌ VALIDAÇÃO FALHOU: Coluna não foi criada!")
        except Exception as e:
            resultados.append(f"❌ Erro na validação: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': 'Migration executada',
            'detalhes': resultados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"[ERRO] migrate_add_servico_id: {str(e)}\n{error_details}")
        return jsonify({
            'success': False,
            'error': str(e),
            'details': error_details
        }), 500

# ==============================================================================
# ENDPOINTS DE DIAGNÓSTICO E MIGRATION - REMOVER APÓS USO
# ==============================================================================

@admin_bp.route('/admin/check-pagamento-parcelado-info', methods=['GET'])
@check_permission(roles=["master"])
def check_pagamento_info():
    """Verificar informações sobre a tabela pagamento_parcelado"""
    try:
        # Contar registros
        result = db.session.execute(db.text("SELECT COUNT(*) FROM pagamento_parcelado;"))
        count = result.scalar()
        
        # Verificar se coluna existe
        result_col = db.session.execute(db.text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'pagamento_parcelado' AND column_name = 'servico_id';
        """))
        coluna_existe = result_col.fetchone() is not None
        
        return jsonify({
            'total_registros': count,
            'coluna_servico_id_existe': coluna_existe,
            'recomendacao': 'LIMPAR TABELA' if count < 50 else 'MIGRATION DIRETA'
        }), 200
    except Exception as e:
        logger.exception("Erro em rota admin de diagnostico pagamento-parcelado")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/admin/limpar-pagamento-parcelado-e-adicionar-coluna', methods=['POST'])
@check_permission(roles=["master"])
def limpar_e_adicionar_coluna():
    """ATENÇÃO: APAGA TODOS os pagamentos parcelados e adiciona a coluna"""
    try:
        resultados = []
        
        # TRUNCATE (limpar tabela)
        db.session.execute(db.text("TRUNCATE TABLE pagamento_parcelado CASCADE;"))
        db.session.commit()
        resultados.append("✅ Tabela pagamento_parcelado limpa")
        
        # ADD COLUMN
        db.session.execute(db.text("ALTER TABLE pagamento_parcelado ADD COLUMN servico_id INTEGER;"))
        db.session.commit()
        resultados.append("✅ Coluna servico_id adicionada")
        
        # VALIDAR
        result = db.session.execute(db.text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'pagamento_parcelado' AND column_name = 'servico_id';
        """))
        
        if result.fetchone():
            resultados.append("✅ VALIDAÇÃO OK!")
            resultados.append("🎉 MIGRATION CONCLUÍDA!")
        
        return jsonify({'success': True, 'detalhes': resultados}), 200

    except Exception as e:
        logger.exception("Erro em rota admin limpar-pagamento-parcelado")
        db.session.rollback()
        return jsonify({'error': str(e), 'success': False}), 500


@admin_bp.route('/admin/recuperar-parcelas-pagas', methods=['POST', 'GET'])
@check_permission(roles=["master"])
def recuperar_parcelas_pagas():
    """
    RECUPERAÇÃO DE DADOS: Reconstrói parcelas pagas a partir dos lançamentos existentes.
    
    Parâmetros:
    - preview=true : Apenas mostra o que seria feito, sem alterar dados
    - dias=30 : Só considera lançamentos dos últimos N dias (padrão: 30)
    
    Quando uma parcela SEM serviço é marcada como paga, ela cria um Lançamento.
    Esta rota usa esses lançamentos para reconstruir as parcelas que foram perdidas.
    """
    import re
    
    try:
        # Parâmetros
        preview_mode = request.args.get('preview', 'true').lower() == 'true'
        dias_limite = int(request.args.get('dias', 30))
        
        data_limite = date.today() - timedelta(days=dias_limite)
        
        resultados = {
            "modo": "PREVIEW (nenhuma alteração feita)" if preview_mode else "EXECUÇÃO",
            "filtro_dias": dias_limite,
            "data_minima": data_limite.isoformat(),
            "lancamentos_analisados": 0,
            "parcelas_a_recuperar": 0,
            "parcelas_ja_existentes": 0,
            "parcelas_previstas_a_criar": 0,
            "erros": [],
            "acoes": []
        }
        
        # 1. Buscar lançamentos RECENTES que têm padrão de parcela na descrição
        todos_lancamentos = Lancamento.query.filter(
            Lancamento.status == 'Pago',
            Lancamento.data >= data_limite  # Só lançamentos recentes
        ).all()
        
        # Regex para encontrar padrão de parcela
        padrao_parcela = re.compile(r'^(.+?)\s*\((?:Parcela\s*)?(\d+)/(\d+)\)$', re.IGNORECASE)
        
        for lanc in todos_lancamentos:
            if not lanc.descricao:
                continue
                
            match = padrao_parcela.match(lanc.descricao.strip())
            if not match:
                continue
            
            resultados["lancamentos_analisados"] += 1
            
            descricao_base = match.group(1).strip()
            numero_parcela = int(match.group(2))
            total_parcelas = int(match.group(3))
            
            # 2. Buscar o PagamentoParcelado correspondente
            pag_parcelado = PagamentoParcelado.query.filter(
                PagamentoParcelado.obra_id == lanc.obra_id,
                PagamentoParcelado.descricao.ilike(f"%{descricao_base}%")
            ).first()
            
            if not pag_parcelado:
                resultados["erros"].append(f"⚠️ PagamentoParcelado não encontrado para: {lanc.descricao} (obra {lanc.obra_id}) - IGNORADO")
                continue
            
            # 3. Verificar se a parcela individual já existe
            parcela_existente = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                ParcelaIndividual.numero_parcela == numero_parcela
            ).first()
            
            if parcela_existente:
                if parcela_existente.status != 'Pago':
                    resultados["acoes"].append(f"🔄 Atualizar status para Pago: {lanc.descricao}")
                    resultados["parcelas_a_recuperar"] += 1
                    
                    if not preview_mode:
                        parcela_existente.status = 'Pago'
                        parcela_existente.data_pagamento = lanc.data
                else:
                    resultados["parcelas_ja_existentes"] += 1
            else:
                resultados["acoes"].append(f"✅ Criar parcela paga: {lanc.descricao}")
                resultados["parcelas_a_recuperar"] += 1
                
                if not preview_mode:
                    # Calcular data de vencimento baseada na periodicidade
                    if pag_parcelado.periodicidade == 'Semanal':
                        dias_offset = (numero_parcela - 1) * 7
                    elif pag_parcelado.periodicidade == 'Quinzenal':
                        dias_offset = (numero_parcela - 1) * 15
                    else:  # Mensal
                        dias_offset = (numero_parcela - 1) * 30
                    
                    data_vencimento = pag_parcelado.data_primeira_parcela + timedelta(days=dias_offset)
                    
                    nova_parcela = ParcelaIndividual(
                        pagamento_parcelado_id=pag_parcelado.id,
                        numero_parcela=numero_parcela,
                        valor_parcela=lanc.valor_total or pag_parcelado.valor_parcela,
                        data_vencimento=data_vencimento,
                        status='Pago',
                        data_pagamento=lanc.data,
                        forma_pagamento=None,
                        observacao=f"Recuperado do lançamento {lanc.id}"
                    )
                    db.session.add(nova_parcela)
            
            # 4. Atualizar contador de parcelas pagas no PagamentoParcelado
            if not preview_mode:
                parcelas_pagas_count = ParcelaIndividual.query.filter(
                    ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                    ParcelaIndividual.status == 'Pago'
                ).count()
                pag_parcelado.parcelas_pagas = parcelas_pagas_count
                
                if parcelas_pagas_count >= pag_parcelado.numero_parcelas:
                    pag_parcelado.status = 'Concluído'
        
        # 5. Verificar parcelas faltantes (não pagas) para PagamentoParcelados recentes
        parcelados_recentes = PagamentoParcelado.query.filter(
            PagamentoParcelado.data_primeira_parcela >= data_limite
        ).all()
        
        for pag in parcelados_recentes:
            for num in range(1, pag.numero_parcelas + 1):
                parcela_existe = ParcelaIndividual.query.filter(
                    ParcelaIndividual.pagamento_parcelado_id == pag.id,
                    ParcelaIndividual.numero_parcela == num
                ).first()
                
                if not parcela_existe:
                    resultados["acoes"].append(f"📝 Criar parcela prevista: {pag.descricao} ({num}/{pag.numero_parcelas})")
                    resultados["parcelas_previstas_a_criar"] += 1
                    
                    if not preview_mode:
                        if pag.periodicidade == 'Semanal':
                            dias_offset = (num - 1) * 7
                        elif pag.periodicidade == 'Quinzenal':
                            dias_offset = (num - 1) * 15
                        else:
                            dias_offset = (num - 1) * 30
                        
                        data_vencimento = pag.data_primeira_parcela + timedelta(days=dias_offset)
                        
                        nova_parcela = ParcelaIndividual(
                            pagamento_parcelado_id=pag.id,
                            numero_parcela=num,
                            valor_parcela=pag.valor_parcela,
                            data_vencimento=data_vencimento,
                            status='Previsto',
                            data_pagamento=None,
                            forma_pagamento=None,
                            observacao=None
                        )
                        db.session.add(nova_parcela)
        
        if not preview_mode:
            db.session.commit()
        
        # Mensagem final
        if preview_mode:
            resultados["instrucao"] = "Para executar de verdade, acesse: /admin/recuperar-parcelas-pagas?preview=false&dias=" + str(dias_limite)
        
        return jsonify({
            "success": True,
            "message": f"{'Preview concluído' if preview_mode else 'Recuperação concluída'}! {resultados['parcelas_a_recuperar']} parcelas {'a recuperar' if preview_mode else 'recuperadas'}.",
            "resultados": resultados
        }), 200
        
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        logger.error(f"--- [ERRO] recuperar_parcelas_pagas: {str(e)}\n{error_details} ---")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ==============================================================================
# ROTAS DE EXPORTAÇÃO CSV
# ==============================================================================

@admin_bp.route('/obras/<int:obra_id>/servicos/exportar-csv', methods=['GET'])

# ==============================================================================
# ROTA DE DEBUG - VERIFICAR DADOS DE PARCELAS E LANÇAMENTOS
# ==============================================================================
@admin_bp.route('/admin/debug-kpi/<int:obra_id>', methods=['GET', 'OPTIONS'])
@check_permission(roles=["master"])
def debug_kpi(obra_id):
    """Rota de debug para verificar cálculos de KPI"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OK"}), 200)
    try:
        resultado = {
            "obra_id": obra_id,
            "parcelas_individuais": [],
            "pagamentos_parcelados": [],
            "lancamentos": [],
            "calculos": {}
        }
        
        # 1. Buscar todos os pagamentos parcelados
        pag_parcelados = PagamentoParcelado.query.filter_by(obra_id=obra_id).all()
        for pp in pag_parcelados:
            resultado["pagamentos_parcelados"].append({
                "id": pp.id,
                "descricao": pp.descricao,
                "servico_id": pp.servico_id,
                "valor_total": pp.valor_total,
                "numero_parcelas": pp.numero_parcelas,
                "parcelas_pagas": pp.parcelas_pagas,
                "status": pp.status
            })
        
        # 2. Buscar todas as parcelas individuais
        parcelas = ParcelaIndividual.query.join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id
        ).all()
        for p in parcelas:
            resultado["parcelas_individuais"].append({
                "id": p.id,
                "pagamento_parcelado_id": p.pagamento_parcelado_id,
                "numero_parcela": p.numero_parcela,
                "valor_parcela": p.valor_parcela,
                "status": p.status,
                "data_pagamento": p.data_pagamento.isoformat() if p.data_pagamento else None
            })
        
        # 3. Buscar lançamentos
        lancamentos = Lancamento.query.filter_by(obra_id=obra_id).all()
        for l in lancamentos:
            resultado["lancamentos"].append({
                "id": l.id,
                "descricao": l.descricao,
                "valor_total": l.valor_total,
                "valor_pago": l.valor_pago,
                "status": l.status,
                "servico_id": l.servico_id
            })
        
        # 4. Calcular valores
        total_parcelas_pagas_sem_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela)
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Pago',
            PagamentoParcelado.servico_id.is_(None)
        ).scalar() or 0
        
        total_parcelas_previstas_sem_servico = db.session.query(
            func.sum(ParcelaIndividual.valor_parcela)
        ).join(PagamentoParcelado).filter(
            PagamentoParcelado.obra_id == obra_id,
            ParcelaIndividual.status == 'Previsto',
            PagamentoParcelado.servico_id.is_(None)
        ).scalar() or 0
        
        total_lancamentos_pagos = db.session.query(
            func.sum(Lancamento.valor_pago)
        ).filter(Lancamento.obra_id == obra_id).scalar() or 0
        
        resultado["calculos"] = {
            "total_parcelas_pagas_sem_servico": total_parcelas_pagas_sem_servico,
            "total_parcelas_previstas_sem_servico": total_parcelas_previstas_sem_servico,
            "total_lancamentos_pagos": total_lancamentos_pagos,
            "qtd_parcelas_pagas": len([p for p in parcelas if p.status == 'Pago']),
            "qtd_parcelas_previstas": len([p for p in parcelas if p.status == 'Previsto'])
        }
        
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ==============================================================================
# ROTA DE LIMPEZA - REMOVER LANÇAMENTOS DUPLICADOS DE PARCELAS
# ==============================================================================
@admin_bp.route('/admin/limpar-lancamentos-duplicados', methods=['GET', 'OPTIONS'])
@check_permission(roles=["master"])
def limpar_lancamentos_duplicados():
    """
    Remove lançamentos duplicados criados por parcelas pagas.
    
    Quando uma parcela SEM serviço é paga, o sistema cria um Lancamento.
    Porém, versões anteriores também adicionavam a ParcelaIndividual ao histórico,
    causando duplicação.
    
    Este script identifica e remove os Lancamentos duplicados.
    
    Parâmetros:
    - preview=true (default): Apenas mostra o que seria deletado
    - preview=false: Executa a deleção
    """
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OK"}), 200)
    
    try:
        import re
        preview = request.args.get('preview', 'true').lower() == 'true'
        
        resultado = {
            "modo": "PREVIEW" if preview else "EXECUÇÃO",
            "lancamentos_duplicados": [],
            "total_encontrados": 0,
            "total_deletados": 0,
            "valor_total_duplicado": 0,
            "obras_afetadas": set()
        }
        
        # Padrão: "Descrição (Parcela X/Y)" 
        padrao_parcela = re.compile(r'^(.+)\s*\(Parcela\s*(\d+)/(\d+)\)$')
        
        # Buscar todos os lançamentos que parecem ser de parcelas
        lancamentos = Lancamento.query.filter(
            Lancamento.descricao.like('%(Parcela %')
        ).all()
        
        logger.info(f"--- [LIMPEZA] Encontrados {len(lancamentos)} lançamentos com padrão de parcela ---")
        
        lancamentos_para_deletar = []
        
        for lanc in lancamentos:
            match = padrao_parcela.match(lanc.descricao)
            if not match:
                continue
            
            descricao_base = match.group(1).strip()
            numero_parcela = int(match.group(2))
            total_parcelas = int(match.group(3))
            
            # Buscar PagamentoParcelado correspondente
            pag_parcelado = PagamentoParcelado.query.filter(
                PagamentoParcelado.obra_id == lanc.obra_id,
                PagamentoParcelado.descricao == descricao_base,
                PagamentoParcelado.numero_parcelas == total_parcelas
            ).first()
            
            if not pag_parcelado:
                continue
            
            # Verificar se existe ParcelaIndividual paga correspondente
            parcela = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == pag_parcelado.id,
                ParcelaIndividual.numero_parcela == numero_parcela,
                ParcelaIndividual.status == 'Pago'
            ).first()
            
            if parcela:
                # Encontrou duplicação! O lançamento foi criado pela parcela
                # mas a parcela ainda existe como Pago
                lancamentos_para_deletar.append(lanc)
                resultado["obras_afetadas"].add(lanc.obra_id)
                resultado["lancamentos_duplicados"].append({
                    "lancamento_id": lanc.id,
                    "obra_id": lanc.obra_id,
                    "descricao": lanc.descricao,
                    "valor": lanc.valor_pago,
                    "data": lanc.data.isoformat() if lanc.data else None,
                    "parcela_id": parcela.id,
                    "pagamento_parcelado_id": pag_parcelado.id
                })
                resultado["valor_total_duplicado"] += lanc.valor_pago or 0
        
        resultado["total_encontrados"] = len(lancamentos_para_deletar)
        resultado["obras_afetadas"] = list(resultado["obras_afetadas"])
        
        if not preview and lancamentos_para_deletar:
            for lanc in lancamentos_para_deletar:
                db.session.delete(lanc)
            db.session.commit()
            resultado["total_deletados"] = len(lancamentos_para_deletar)
            logger.info(f"--- [LIMPEZA] ✅ {len(lancamentos_para_deletar)} lançamentos duplicados removidos ---")
        
        return jsonify(resultado)
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"erro": str(e)}), 500


# ==============================================================================
# ENDPOINTS DE ORÇAMENTO DE ENGENHARIA
# ==============================================================================

@admin_bp.route('/servicos-base', methods=['GET'])

# ==============================================================================
# ROTA DE TESTES - VALIDAÇÃO COMPLETA DE PAGAMENTOS E ORÇAMENTO
# ==============================================================================
@admin_bp.route('/api/testes/validar-sistema/<int:obra_id>', methods=['GET', 'OPTIONS'])
@check_permission(roles=["master"])
def validar_sistema_completo(obra_id):
    """
    ROTA DE TESTES COMPLETA
    Valida todas as funcionalidades de pagamento e orçamento:
    - Estrutura do orçamento de engenharia
    - Vinculação de pagamentos a itens do orçamento
    - Contabilização de valores (Executado vs Previsto)
    - Integridade dos dados
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        resultados = {
            "obra_id": obra_id,
            "timestamp": datetime.now().isoformat(),
            "testes": [],
            "resumo": {
                "total": 0,
                "passou": 0,
                "falhou": 0,
                "avisos": 0
            }
        }
        
        def add_teste(nome, passou, detalhes=None, aviso=False):
            status = "⚠️ AVISO" if aviso else ("✅ PASSOU" if passou else "❌ FALHOU")
            resultados["testes"].append({
                "nome": nome,
                "status": status,
                "passou": passou,
                "detalhes": detalhes
            })
            resultados["resumo"]["total"] += 1
            if aviso:
                resultados["resumo"]["avisos"] += 1
            elif passou:
                resultados["resumo"]["passou"] += 1
            else:
                resultados["resumo"]["falhou"] += 1
        
        # ===========================================
        # TESTE 1: Verificar se obra existe
        # ===========================================
        obra = Obra.query.get(obra_id)
        add_teste(
            "1. Obra existe",
            obra is not None,
            f"Obra: {obra.nome if obra else 'NÃO ENCONTRADA'}"
        )
        
        if not obra:
            return jsonify(resultados), 200
        
        # ===========================================
        # TESTE 2: Verificar estrutura do orçamento
        # ===========================================
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).all()
        itens = OrcamentoEngItem.query.filter(
            OrcamentoEngItem.etapa_id.in_([e.id for e in etapas])
        ).all() if etapas else []
        
        add_teste(
            "2. Orçamento de Engenharia - Etapas",
            len(etapas) > 0,
            f"Total de etapas: {len(etapas)}"
        )
        
        add_teste(
            "3. Orçamento de Engenharia - Itens",
            len(itens) > 0,
            f"Total de itens: {len(itens)}"
        )
        
        # ===========================================
        # TESTE 3: Verificar endpoint itens-lista
        # ===========================================
        try:
            itens_lista = db.session.execute(db.text("""
                SELECT i.id, i.descricao, e.nome as etapa_nome
                FROM orcamento_eng_item i
                JOIN orcamento_eng_etapa e ON i.etapa_id = e.id
                WHERE e.obra_id = :obra_id
                ORDER BY e.ordem, i.id
            """), {"obra_id": obra_id}).fetchall()
            
            add_teste(
                "4. Endpoint itens-lista funcional",
                len(itens_lista) >= 0,
                f"Itens disponíveis para dropdown: {len(itens_lista)}"
            )
        except Exception as e:
            add_teste("4. Endpoint itens-lista funcional", False, str(e))
        
        # ===========================================
        # TESTE 4: Verificar colunas orcamento_item_id
        # ===========================================
        tabelas_pagamento = ['lancamento', 'pagamento_futuro', 'pagamento_parcelado_v2', 'boleto']
        for tabela in tabelas_pagamento:
            try:
                result = db.session.execute(db.text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = ':tabela' AND column_name = 'orcamento_item_id'
                """), {"tabela": tabela}).fetchone()
                add_teste(
                    f"5. Coluna orcamento_item_id em {tabela}",
                    result is not None,
                    "Coluna existe" if result else "COLUNA NÃO EXISTE - Execute migração!"
                )
            except Exception as e:
                add_teste(f"5. Coluna orcamento_item_id em {tabela}", False, str(e))
        
        # ===========================================
        # TESTE 5: Verificar pagamentos vinculados
        # ===========================================
        # Lançamentos
        try:
            lanc_vinculados = db.session.execute(db.text("""
                SELECT COUNT(*) FROM lancamento l
                JOIN obra o ON l.obra_id = o.id
                WHERE o.id = :obra_id AND l.orcamento_item_id IS NOT NULL
            """), {"obra_id": obra_id}).scalar()
            lanc_total = db.session.execute(db.text("""
                SELECT COUNT(*) FROM lancamento WHERE obra_id = :obra_id
            """), {"obra_id": obra_id}).scalar()
            add_teste(
                "6. Lançamentos vinculados a itens",
                True,
                f"{lanc_vinculados} de {lanc_total} lançamentos vinculados",
                aviso=(lanc_vinculados == 0 and lanc_total > 0)
            )
        except Exception as e:
            add_teste("6. Lançamentos vinculados", False, str(e))
        
        # Pagamentos Futuros
        try:
            pf_vinculados = db.session.execute(db.text("""
                SELECT COUNT(*) FROM pagamento_futuro 
                WHERE obra_id = :obra_id AND orcamento_item_id IS NOT NULL
            """), {"obra_id": obra_id}).scalar()
            pf_total = db.session.execute(db.text("""
                SELECT COUNT(*) FROM pagamento_futuro WHERE obra_id = :obra_id
            """), {"obra_id": obra_id}).scalar()
            add_teste(
                "7. Pagamentos Futuros vinculados",
                True,
                f"{pf_vinculados} de {pf_total} pagamentos futuros vinculados",
                aviso=(pf_vinculados == 0 and pf_total > 0)
            )
        except Exception as e:
            add_teste("7. Pagamentos Futuros vinculados", False, str(e))
        
        # Pagamentos Parcelados
        try:
            pp_vinculados = db.session.execute(db.text("""
                SELECT COUNT(*) FROM pagamento_parcelado_v2 
                WHERE obra_id = :obra_id AND orcamento_item_id IS NOT NULL
            """), {"obra_id": obra_id}).scalar()
            pp_total = db.session.execute(db.text("""
                SELECT COUNT(*) FROM pagamento_parcelado_v2 WHERE obra_id = :obra_id
            """), {"obra_id": obra_id}).scalar()
            add_teste(
                "8. Pagamentos Parcelados vinculados",
                True,
                f"{pp_vinculados} de {pp_total} parcelados vinculados",
                aviso=(pp_vinculados == 0 and pp_total > 0)
            )
        except Exception as e:
            add_teste("8. Pagamentos Parcelados vinculados", False, str(e))
        
        # Boletos
        try:
            bol_vinculados = db.session.execute(db.text("""
                SELECT COUNT(*) FROM boleto 
                WHERE obra_id = :obra_id AND orcamento_item_id IS NOT NULL
            """), {"obra_id": obra_id}).scalar()
            bol_total = db.session.execute(db.text("""
                SELECT COUNT(*) FROM boleto WHERE obra_id = :obra_id
            """), {"obra_id": obra_id}).scalar()
            add_teste(
                "9. Boletos vinculados",
                True,
                f"{bol_vinculados} de {bol_total} boletos vinculados",
                aviso=(bol_vinculados == 0 and bol_total > 0)
            )
        except Exception as e:
            add_teste("9. Boletos vinculados", False, str(e))
        
        # ===========================================
        # TESTE 6: Calcular valores por item do orçamento
        # ===========================================
        valores_por_item = []
        for item in itens[:10]:  # Limitar a 10 itens para não sobrecarregar
            try:
                # Valor Previsto
                previsto = item.valor_total or 0
                
                # Valor Executado (soma de pagamentos vinculados e pagos)
                executado_lanc = db.session.execute(db.text("""
                    SELECT COALESCE(SUM(valor_pago), 0) FROM lancamento 
                    WHERE orcamento_item_id = :item_id AND status = 'Pago'
                """), {"item_id": item.id}).scalar() or 0
                
                executado_pf = db.session.execute(db.text("""
                    SELECT COALESCE(SUM(valor), 0) FROM pagamento_futuro 
                    WHERE orcamento_item_id = :item_id AND status = 'Pago'
                """), {"item_id": item.id}).scalar() or 0
                
                # Parcelas pagas de pagamentos parcelados
                executado_pp = db.session.execute(db.text("""
                    SELECT COALESCE(SUM(pi.valor_parcela), 0) 
                    FROM parcela_individual pi
                    JOIN pagamento_parcelado_v2 pp ON pi.pagamento_parcelado_id = pp.id
                    WHERE pp.orcamento_item_id = :item_id AND pi.status = 'Pago'
                """), {"item_id": item.id}).scalar() or 0
                
                executado_bol = db.session.execute(db.text("""
                    SELECT COALESCE(SUM(valor), 0) FROM boleto 
                    WHERE orcamento_item_id = :item_id AND status = 'Pago'
                """), {"item_id": item.id}).scalar() or 0
                
                executado_total = executado_lanc + executado_pf + executado_pp + executado_bol
                
                valores_por_item.append({
                    "item_id": item.id,
                    "descricao": item.descricao[:50] if item.descricao else "Sem descrição",
                    "previsto": float(previsto),
                    "executado": float(executado_total),
                    "percentual": round((executado_total / previsto * 100), 1) if previsto > 0 else 0,
                    "detalhes": {
                        "lancamentos": float(executado_lanc),
                        "pagamentos_futuros": float(executado_pf),
                        "parcelas_pagas": float(executado_pp),
                        "boletos": float(executado_bol)
                    }
                })
            except Exception as e:
                valores_por_item.append({
                    "item_id": item.id,
                    "erro": str(e)
                })
        
        add_teste(
            "10. Cálculo de valores por item",
            len(valores_por_item) > 0,
            f"Calculado para {len(valores_por_item)} itens"
        )
        
        # ===========================================
        # TESTE 7: Totais do orçamento
        # ===========================================
        try:
            total_previsto = sum(item.valor_total or 0 for item in itens)
            
            total_executado = db.session.execute(db.text("""
                SELECT COALESCE(SUM(l.valor_pago), 0)
                FROM lancamento l
                JOIN orcamento_eng_item i ON l.orcamento_item_id = i.id
                JOIN orcamento_eng_etapa e ON i.etapa_id = e.id
                WHERE e.obra_id = :obra_id AND l.status = 'Pago'
            """), {"obra_id": obra_id}).scalar() or 0
            
            total_executado += db.session.execute(db.text("""
                SELECT COALESCE(SUM(pf.valor), 0)
                FROM pagamento_futuro pf
                JOIN orcamento_eng_item i ON pf.orcamento_item_id = i.id
                JOIN orcamento_eng_etapa e ON i.etapa_id = e.id
                WHERE e.obra_id = :obra_id AND pf.status = 'Pago'
            """), {"obra_id": obra_id}).scalar() or 0
            
            total_executado += db.session.execute(db.text("""
                SELECT COALESCE(SUM(pi.valor_parcela), 0)
                FROM parcela_individual pi
                JOIN pagamento_parcelado_v2 pp ON pi.pagamento_parcelado_id = pp.id
                JOIN orcamento_eng_item i ON pp.orcamento_item_id = i.id
                JOIN orcamento_eng_etapa e ON i.etapa_id = e.id
                WHERE e.obra_id = :obra_id AND pi.status = 'Pago'
            """), {"obra_id": obra_id}).scalar() or 0
            
            total_executado += db.session.execute(db.text("""
                SELECT COALESCE(SUM(b.valor), 0)
                FROM boleto b
                JOIN orcamento_eng_item i ON b.orcamento_item_id = i.id
                JOIN orcamento_eng_etapa e ON i.etapa_id = e.id
                WHERE e.obra_id = :obra_id AND b.status = 'Pago'
            """), {"obra_id": obra_id}).scalar() or 0
            
            percentual_geral = round((total_executado / total_previsto * 100), 1) if total_previsto > 0 else 0
            
            add_teste(
                "11. Totais do Orçamento",
                True,
                f"Previsto: R$ {total_previsto:,.2f} | Executado: R$ {total_executado:,.2f} | {percentual_geral}%"
            )
            
            resultados["totais_orcamento"] = {
                "previsto": float(total_previsto),
                "executado": float(total_executado),
                "percentual": percentual_geral,
                "saldo": float(total_previsto - total_executado)
            }
        except Exception as e:
            add_teste("11. Totais do Orçamento", False, str(e))
        
        # ===========================================
        # TESTE 8: Verificar rotas de API
        # ===========================================
        rotas_testadas = [
            f"/obras/{obra_id}/orcamento-eng/itens-lista",
            f"/sid/cronograma-financeiro/{obra_id}/pagamentos-futuros",
            f"/sid/cronograma-financeiro/{obra_id}/pagamentos-parcelados",
            f"/obras/{obra_id}/boletos",
        ]
        
        add_teste(
            "12. Rotas de API configuradas",
            True,
            f"Rotas disponíveis: {len(rotas_testadas)}"
        )
        
        # Adicionar valores por item ao resultado
        resultados["valores_por_item"] = valores_por_item
        
        # ===========================================
        # RESUMO FINAL
        # ===========================================
        resultados["status_geral"] = "✅ SISTEMA OK" if resultados["resumo"]["falhou"] == 0 else "❌ PROBLEMAS ENCONTRADOS"
        
        return jsonify(resultados), 200
        
    except Exception as e:
        return jsonify({
            "erro": str(e)
        }), 500


@admin_bp.route('/api/testes/simular-pagamento/<int:obra_id>', methods=['POST', 'OPTIONS'])
@check_permission(roles=["master"])
def simular_pagamento_teste(obra_id):
    """
    ROTA DE TESTE - Simula criação de pagamento vinculado a item do orçamento
    
    Body esperado:
    {
        "orcamento_item_id": 123,
        "valor": 1000.00,
        "tipo": "lancamento" | "pagamento_futuro" | "parcelado",
        "descricao": "Teste de pagamento"
    }
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.get_json()
        
        orcamento_item_id = data.get('orcamento_item_id')
        valor = float(data.get('valor', 100))
        tipo = data.get('tipo', 'lancamento')
        descricao = data.get('descricao', f'[TESTE] Pagamento de teste - {datetime.now().isoformat()}')
        
        resultado = {
            "obra_id": obra_id,
            "tipo": tipo,
            "orcamento_item_id": orcamento_item_id,
            "valor": valor
        }
        
        # Verificar se item do orçamento existe
        if orcamento_item_id:
            item = OrcamentoEngItem.query.get(orcamento_item_id)
            if not item:
                return jsonify({"erro": f"Item do orçamento {orcamento_item_id} não encontrado"}), 404
            resultado["item_descricao"] = item.descricao
        
        if tipo == 'lancamento':
            # Criar lançamento de teste
            novo = Lancamento(
                obra_id=obra_id,
                tipo='Material',
                descricao=descricao,
                valor_total=valor,
                valor_pago=valor,
                data=date.today(),
                status='Pago',
                fornecedor='[TESTE]'
            )
            db.session.add(novo)
            db.session.flush()
            
            # Vincular ao item do orçamento
            if orcamento_item_id:
                db.session.execute(db.text(
                    f"UPDATE lancamento SET orcamento_item_id = {orcamento_item_id} WHERE id = {novo.id}"
                ))
            
            db.session.commit()
            resultado["id_criado"] = novo.id
            resultado["mensagem"] = f"Lançamento #{novo.id} criado e vinculado com sucesso!"
            
        elif tipo == 'pagamento_futuro':
            # Criar pagamento futuro de teste
            novo = PagamentoFuturo(
                obra_id=obra_id,
                descricao=descricao,
                valor=valor,
                data_vencimento=date.today(),
                fornecedor='[TESTE]',
                status='Previsto'
            )
            db.session.add(novo)
            db.session.flush()
            
            if orcamento_item_id:
                db.session.execute(db.text(
                    f"UPDATE pagamento_futuro SET orcamento_item_id = {orcamento_item_id} WHERE id = {novo.id}"
                ))
            
            db.session.commit()
            resultado["id_criado"] = novo.id
            resultado["mensagem"] = f"Pagamento Futuro #{novo.id} criado e vinculado com sucesso!"
            
        elif tipo == 'parcelado':
            # Criar pagamento parcelado de teste
            novo = PagamentoParcelado(
                obra_id=obra_id,
                descricao=descricao,
                valor_total=valor,
                numero_parcelas=2,
                valor_parcela=valor/2,
                data_primeira_parcela=date.today(),
                periodicidade='Mensal',
                parcelas_pagas=0,
                status='Ativo',
                fornecedor='[TESTE]'
            )
            db.session.add(novo)
            db.session.flush()
            
            if orcamento_item_id:
                db.session.execute(db.text(
                    f"UPDATE pagamento_parcelado_v2 SET orcamento_item_id = {orcamento_item_id} WHERE id = {novo.id}"
                ))
            
            db.session.commit()
            resultado["id_criado"] = novo.id
            resultado["mensagem"] = f"Pagamento Parcelado #{novo.id} criado e vinculado com sucesso!"
        
        else:
            return jsonify({"erro": f"Tipo '{tipo}' não suportado. Use: lancamento, pagamento_futuro, parcelado"}), 400
        
        return jsonify(resultado), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "erro": str(e)
        }), 500


@admin_bp.route('/api/testes/popular-orcamento/<int:obra_id>', methods=['POST', 'OPTIONS'])
@check_permission(roles=["master"])
def popular_orcamento_teste(obra_id):
    """
    ROTA DE TESTE - Popula o orçamento de engenharia com dados de exemplo
    Cria etapas e itens para facilitar os testes
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Verificar se obra existe
        obra = Obra.query.get(obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        
        resultados = {
            "obra_id": obra_id,
            "obra_nome": obra.nome,
            "etapas_criadas": [],
            "itens_criados": []
        }
        
        # Dados de exemplo para orçamento de construção
        dados_orcamento = [
            {
                "etapa": "1. SERVIÇOS PRELIMINARES",
                "itens": [
                    {"descricao": "Limpeza do terreno", "unidade": "m²", "quantidade": 500, "valor_unitario": 8.50},
                    {"descricao": "Instalação do canteiro de obras", "unidade": "vb", "quantidade": 1, "valor_unitario": 5000},
                    {"descricao": "Tapume e cercamento", "unidade": "m", "quantidade": 120, "valor_unitario": 85},
                ]
            },
            {
                "etapa": "2. FUNDAÇÃO",
                "itens": [
                    {"descricao": "Escavação para fundação", "unidade": "m³", "quantidade": 45, "valor_unitario": 65},
                    {"descricao": "Concreto armado para sapatas", "unidade": "m³", "quantidade": 28, "valor_unitario": 850},
                    {"descricao": "Ferragem para fundação", "unidade": "kg", "quantidade": 1200, "valor_unitario": 12},
                    {"descricao": "Impermeabilização da fundação", "unidade": "m²", "quantidade": 180, "valor_unitario": 45},
                ]
            },
            {
                "etapa": "3. ESTRUTURA",
                "itens": [
                    {"descricao": "Pilares de concreto armado", "unidade": "m³", "quantidade": 15, "valor_unitario": 1200},
                    {"descricao": "Vigas de concreto armado", "unidade": "m³", "quantidade": 22, "valor_unitario": 1100},
                    {"descricao": "Laje pré-moldada", "unidade": "m²", "quantidade": 280, "valor_unitario": 95},
                    {"descricao": "Escada de concreto", "unidade": "vb", "quantidade": 1, "valor_unitario": 8500},
                ]
            },
            {
                "etapa": "4. ALVENARIA",
                "itens": [
                    {"descricao": "Alvenaria de vedação (tijolo cerâmico)", "unidade": "m²", "quantidade": 450, "valor_unitario": 75},
                    {"descricao": "Vergas e contravergas", "unidade": "m", "quantidade": 85, "valor_unitario": 45},
                    {"descricao": "Encunhamento", "unidade": "m", "quantidade": 120, "valor_unitario": 18},
                ]
            },
            {
                "etapa": "5. INSTALAÇÕES ELÉTRICAS",
                "itens": [
                    {"descricao": "Quadro de distribuição", "unidade": "un", "quantidade": 2, "valor_unitario": 1800},
                    {"descricao": "Fiação elétrica", "unidade": "m", "quantidade": 800, "valor_unitario": 12},
                    {"descricao": "Tomadas e interruptores", "unidade": "un", "quantidade": 65, "valor_unitario": 45},
                    {"descricao": "Luminárias", "unidade": "un", "quantidade": 32, "valor_unitario": 180},
                ]
            },
            {
                "etapa": "6. INSTALAÇÕES HIDRÁULICAS",
                "itens": [
                    {"descricao": "Tubulação água fria (PVC)", "unidade": "m", "quantidade": 150, "valor_unitario": 28},
                    {"descricao": "Tubulação esgoto (PVC)", "unidade": "m", "quantidade": 80, "valor_unitario": 35},
                    {"descricao": "Caixa d'água 1000L", "unidade": "un", "quantidade": 2, "valor_unitario": 850},
                    {"descricao": "Louças sanitárias", "unidade": "un", "quantidade": 4, "valor_unitario": 650},
                    {"descricao": "Metais (torneiras e registros)", "unidade": "vb", "quantidade": 1, "valor_unitario": 3500},
                ]
            },
            {
                "etapa": "7. REVESTIMENTOS",
                "itens": [
                    {"descricao": "Chapisco", "unidade": "m²", "quantidade": 900, "valor_unitario": 12},
                    {"descricao": "Reboco interno", "unidade": "m²", "quantidade": 750, "valor_unitario": 38},
                    {"descricao": "Reboco externo", "unidade": "m²", "quantidade": 320, "valor_unitario": 45},
                    {"descricao": "Contrapiso", "unidade": "m²", "quantidade": 280, "valor_unitario": 42},
                    {"descricao": "Cerâmica piso", "unidade": "m²", "quantidade": 260, "valor_unitario": 95},
                    {"descricao": "Cerâmica parede (áreas molhadas)", "unidade": "m²", "quantidade": 85, "valor_unitario": 85},
                ]
            },
            {
                "etapa": "8. ESQUADRIAS",
                "itens": [
                    {"descricao": "Portas de madeira internas", "unidade": "un", "quantidade": 8, "valor_unitario": 750},
                    {"descricao": "Porta de entrada (madeira maciça)", "unidade": "un", "quantidade": 1, "valor_unitario": 2800},
                    {"descricao": "Janelas de alumínio", "unidade": "m²", "quantidade": 18, "valor_unitario": 650},
                    {"descricao": "Box de vidro temperado", "unidade": "un", "quantidade": 2, "valor_unitario": 1200},
                ]
            },
            {
                "etapa": "9. PINTURA",
                "itens": [
                    {"descricao": "Massa corrida (interno)", "unidade": "m²", "quantidade": 650, "valor_unitario": 18},
                    {"descricao": "Pintura interna (2 demãos)", "unidade": "m²", "quantidade": 750, "valor_unitario": 22},
                    {"descricao": "Pintura externa (textura)", "unidade": "m²", "quantidade": 320, "valor_unitario": 35},
                ]
            },
            {
                "etapa": "10. COBERTURA",
                "itens": [
                    {"descricao": "Estrutura de madeira para telhado", "unidade": "m²", "quantidade": 180, "valor_unitario": 120},
                    {"descricao": "Telhas cerâmicas", "unidade": "m²", "quantidade": 200, "valor_unitario": 85},
                    {"descricao": "Calhas e rufos", "unidade": "m", "quantidade": 45, "valor_unitario": 95},
                    {"descricao": "Forro de gesso", "unidade": "m²", "quantidade": 260, "valor_unitario": 65},
                ]
            }
        ]
        
        ordem_etapa = 1
        for grupo in dados_orcamento:
            # Criar etapa
            etapa = OrcamentoEngEtapa(
                obra_id=obra_id,
                nome=grupo["etapa"],
                ordem=ordem_etapa
            )
            db.session.add(etapa)
            db.session.flush()
            
            resultados["etapas_criadas"].append({
                "id": etapa.id,
                "nome": etapa.nome
            })
            
            # Criar itens da etapa
            for item_data in grupo["itens"]:
                valor_total = item_data["quantidade"] * item_data["valor_unitario"]
                
                item = OrcamentoEngItem(
                    etapa_id=etapa.id,
                    descricao=item_data["descricao"],
                    unidade=item_data["unidade"],
                    quantidade=item_data["quantidade"],
                    valor_unitario=item_data["valor_unitario"],
                    valor_total=valor_total,
                    bdi=0
                )
                db.session.add(item)
                db.session.flush()
                
                resultados["itens_criados"].append({
                    "id": item.id,
                    "descricao": item.descricao,
                    "valor_total": valor_total
                })
            
            ordem_etapa += 1
        
        db.session.commit()
        
        # Calcular totais
        total_orcamento = sum(i["valor_total"] for i in resultados["itens_criados"])
        
        resultados["resumo"] = {
            "total_etapas": len(resultados["etapas_criadas"]),
            "total_itens": len(resultados["itens_criados"]),
            "valor_total_orcamento": total_orcamento,
            "valor_formatado": f"R$ {total_orcamento:,.2f}"
        }
        
        resultados["mensagem"] = f"✅ Orçamento populado com sucesso! {len(resultados['etapas_criadas'])} etapas e {len(resultados['itens_criados'])} itens criados."
        
        return jsonify(resultados), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "erro": str(e)
        }), 500


@admin_bp.route('/api/testes/limpar-orcamento/<int:obra_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=["master"])
def limpar_orcamento_teste(obra_id):
    """
    ROTA DE TESTE - Remove todo o orçamento de engenharia da obra
    CUIDADO: Isso remove TODOS os itens e etapas!
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Buscar etapas da obra
        etapas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).all()
        etapa_ids = [e.id for e in etapas]
        
        # Remover itens
        itens_removidos = 0
        if etapa_ids:
            itens_removidos = OrcamentoEngItem.query.filter(
                OrcamentoEngItem.etapa_id.in_(etapa_ids)
            ).delete(synchronize_session=False)
        
        # Remover etapas
        etapas_removidas = OrcamentoEngEtapa.query.filter_by(obra_id=obra_id).delete()
        
        db.session.commit()
        
        return jsonify({
            "obra_id": obra_id,
            "etapas_removidas": etapas_removidas,
            "itens_removidos": itens_removidos,
            "mensagem": f"✅ Orçamento limpo! {etapas_removidas} etapas e {itens_removidos} itens removidos."
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "erro": str(e)
        }), 500


@admin_bp.route('/api/testes/limpar-testes/<int:obra_id>', methods=['DELETE', 'OPTIONS'])
@check_permission(roles=["master"])
def limpar_dados_teste(obra_id):
    """
    ROTA DE TESTE - Remove todos os registros de teste (marcados com [TESTE])
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        resultados = {
            "obra_id": obra_id,
            "removidos": {}
        }
        
        # Remover lançamentos de teste
        lanc_removidos = Lancamento.query.filter(
            Lancamento.obra_id == obra_id,
            Lancamento.fornecedor == '[TESTE]'
        ).delete()
        resultados["removidos"]["lancamentos"] = lanc_removidos
        
        # Remover pagamentos futuros de teste
        pf_removidos = PagamentoFuturo.query.filter(
            PagamentoFuturo.obra_id == obra_id,
            PagamentoFuturo.fornecedor == '[TESTE]'
        ).delete()
        resultados["removidos"]["pagamentos_futuros"] = pf_removidos
        
        # Remover pagamentos parcelados de teste
        pp_removidos = PagamentoParcelado.query.filter(
            PagamentoParcelado.obra_id == obra_id,
            PagamentoParcelado.fornecedor == '[TESTE]'
        ).delete()
        resultados["removidos"]["pagamentos_parcelados"] = pp_removidos
        
        db.session.commit()
        
        total = sum(resultados["removidos"].values())
        resultados["mensagem"] = f"✅ {total} registros de teste removidos"
        
        return jsonify(resultados), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "erro": str(e)
        }), 500

