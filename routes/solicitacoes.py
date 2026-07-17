"""Blueprint do módulo Solicitações — solicitação de compras de materiais,
insumos e equipamentos por obra.

Fluxo: Aberta → Em cotação (1ª cotação) → Aguardando aprovação → Aprovada |
Rejeitada | Cancelada. Ao aprovar (ou efetivar direto, quando a cotação
escolhida está dentro do limite configurado) é criado um PagamentoFuturo
('Previsto') na obra — o financeiro completa depois pelos fluxos existentes.

Config (linha única id=1): usuários alertados na criação (pesquisa de preços),
aprovadores e limite de valor. Limite ausente = toda compra exige aprovador.

`GET /solicitacoes/publico/<token>` é PÚBLICA (sem JWT/módulo) — snapshot
read-only da solicitação, compartilhável via WhatsApp. Nunca expõe cotações.

Visibilidade: master/administrador veem tudo; comum vê solicitações de suas
obras permitidas e as que ele mesmo criou.
Erros de validação são SEMPRE 400 — nunca 422 (fetchWithAuth desloga em 401/422).
"""
import logging
import secrets
from datetime import datetime, date, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, verify_jwt_in_request
from sqlalchemy import or_

from extensions import db
from models.solicitacao_compra import SolicitacaoCompra
from models.solicitacao_item import SolicitacaoItem
from models.solicitacao_cotacao import SolicitacaoCotacao
from models.solicitacao_config import SolicitacaoConfig
from models.pagamento_futuro import PagamentoFuturo
from models.obra import Obra
from models.user import User
from services import storage_service
from services import get_current_user, user_has_access_to_obra, user_tem_modulo
from services.notificacao_service import criar_notificacao

logger = logging.getLogger(__name__)

solicitacoes_bp = Blueprint('solicitacoes', __name__, url_prefix='/solicitacoes')


@solicitacoes_bp.before_request
def _gate_modulo_solicitacoes():
    """Acesso ao módulo exige o módulo liberado (master sempre passa).

    Exceção: a rota pública do link compartilhável fica FORA do gate — sem
    JWT e sem módulo (o bypass precisa vir antes do verify_jwt_in_request,
    senão o link morre com 401)."""
    if request.method == 'OPTIONS':
        return None
    if request.endpoint == 'solicitacoes.publico_solicitacao':
        return None
    verify_jwt_in_request()
    if not user_tem_modulo(get_current_user(), 'solicitacoes'):
        return jsonify({"erro": "Acesso negado: você não tem permissão para o módulo Solicitações."}), 403

BUCKET_SOLICITACOES = 'solicitacoes-arquivos'

_TIPOS = {'Material', 'Equipamentos', 'Mão de Obra', 'Despesa'}
_STATUS_ABERTOS = {'Aberta', 'Em cotação', 'Aguardando aprovação'}
_STATUS_DECIDIDOS = {'Aprovada', 'Rejeitada', 'Cancelada'}


# ---------------------------------------------------------------- helpers

def _parse_date(valor):
    """Aceita 'YYYY-MM-DD' (ou ISO com hora) → date; None se vazio/inválido."""
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)[:10]).date()
    except Exception:
        return None


def _to_num(valor):
    """Converte número ou string BR ('2.640,00') em float. None se vazio."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip().replace('R$', '').strip()
    if ',' in s and '.' in s:          # 2.640,00 → 2640.00
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:                     # 2640,00 → 2640.00
        s = s.replace(',', '.')
    try:
        return float(s)
    except Exception:
        return None


def _to_int(valor):
    if valor is None or valor == '':
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _obra_ids_permitidos(user):
    """None = sem restrição (master/administrador). Lista = só essas obras."""
    if user and user.role in ('master', 'administrador'):
        return None
    return [o.id for o in user.obras_permitidas] if user else []


def _filtro_visibilidade(query, user):
    """Comum vê solicitações de suas obras permitidas + as que ele criou."""
    obra_ids = _obra_ids_permitidos(user)
    if obra_ids is None:
        return query
    return query.filter(or_(SolicitacaoCompra.obra_id.in_(obra_ids),
                            SolicitacaoCompra.solicitante_id == user.id))


def _solicitacao_visivel(s, user):
    obra_ids = _obra_ids_permitidos(user)
    return obra_ids is None or s.obra_id in obra_ids or s.solicitante_id == user.id


def _dados_e_arquivo():
    """Suporta JSON e multipart/form-data (campo 'arquivo' opcional)."""
    if request.files:
        return request.form, (request.files.get('arquivo') or request.files.get('file'))
    if request.content_type and 'multipart/form-data' in request.content_type:
        return request.form, None
    return (request.get_json(silent=True) or {}), None


def _upload_best_effort(arquivo, pasta):
    """Upload que nunca bloqueia o save: retorna (path|None, falhou: bool)."""
    if not arquivo:
        return None, False
    try:
        return storage_service.upload_arquivo(arquivo, pasta, bucket=BUCKET_SOLICITACOES), False
    except Exception as e:
        logger.exception("Solicitações: upload falhou (segue sem arquivo): %s", e)
        return None, True


def _config():
    return SolicitacaoConfig.get()


def _eh_aprovador(user, cfg):
    if user.role == 'master':
        return True
    return bool(cfg and user.id in (cfg.aprovadores_ids or []))


def _pode_efetivar(cfg, valor):
    """Efetivação direta (sem aprovador) só quando há limite configurado e a
    cotação escolhida está dentro dele."""
    return bool(cfg and cfg.limite_valor is not None and valor is not None
                and float(valor) <= float(cfg.limite_valor))


def _notificar_ids(user_ids, tipo, titulo, mensagem, solicitacao, origem_id):
    """Notifica uma lista de user ids (pula a origem). SEMPRE chamar depois do
    commit da transação principal — criar_notificacao commita internamente."""
    for uid in (user_ids or []):
        if uid and uid != origem_id:
            criar_notificacao(
                usuario_destino_id=uid, tipo=tipo, titulo=titulo,
                mensagem=mensagem, obra_id=solicitacao.obra_id,
                item_id=solicitacao.id, item_type='solicitacao_compra',
                usuario_origem_id=origem_id,
            )


def _resumo_itens(s, limite=180):
    """'50x cimento CP-II (+3 itens)' — para a descrição do PagamentoFuturo."""
    if not s.itens:
        return f"solicitação #{s.id}"
    primeiro = s.itens[0]
    resumo = primeiro.descricao
    if len(s.itens) > 1:
        resumo += f" (+{len(s.itens) - 1} itens)"
    return resumo[:limite]


# ---------------------------------------------------------------- solicitações

@solicitacoes_bp.route('', methods=['GET'])
@jwt_required()
def listar_solicitacoes():
    user = get_current_user()
    query = _filtro_visibilidade(SolicitacaoCompra.query, user)

    status = (request.args.get('status') or '').strip()
    if status:
        query = query.filter(SolicitacaoCompra.status == status)
    obra_id = _to_int(request.args.get('obra_id'))
    if obra_id:
        query = query.filter(SolicitacaoCompra.obra_id == obra_id)

    solicitacoes = query.order_by(SolicitacaoCompra.data_criacao.desc()).all()
    return jsonify([s.to_dict() for s in solicitacoes]), 200


@solicitacoes_bp.route('', methods=['POST'])
@jwt_required()
def criar_solicitacao():
    user = get_current_user()
    dados = request.get_json(silent=True) or {}

    obra_id = _to_int(dados.get('obra_id'))
    if not obra_id:
        return jsonify({"erro": "obra_id é obrigatório."}), 400
    obra = Obra.query.get(obra_id)
    if not obra:
        return jsonify({"erro": "Obra não encontrada."}), 400
    if not user_has_access_to_obra(user, obra_id):
        return jsonify({"erro": "Você não tem acesso a esta obra."}), 403
    if getattr(obra, 'arquivada', False):
        return jsonify({"erro": "Obra arquivada — não é possível criar solicitações."}), 400

    tipo = (dados.get('tipo') or 'Material').strip()
    if tipo not in _TIPOS:
        return jsonify({"erro": f"tipo inválido (use {sorted(_TIPOS)})"}), 400

    itens_dados = dados.get('itens')
    if not isinstance(itens_dados, list) or not itens_dados:
        return jsonify({"erro": "Informe ao menos um item na solicitação."}), 400
    itens = []
    for idx, item in enumerate(itens_dados, start=1):
        descricao = (item.get('descricao') or '').strip() if isinstance(item, dict) else ''
        quantidade = _to_num(item.get('quantidade')) if isinstance(item, dict) else None
        if not descricao:
            return jsonify({"erro": f"Item {idx}: descrição é obrigatória."}), 400
        if not quantidade or quantidade <= 0:
            return jsonify({"erro": f"Item {idx}: quantidade deve ser maior que zero."}), 400
        itens.append(SolicitacaoItem(
            descricao=descricao[:300],
            quantidade=quantidade,
            unidade=(item.get('unidade') or '').strip()[:20] or None,
            observacao=(item.get('observacao') or '').strip()[:300] or None,
        ))

    try:
        solicitacao = SolicitacaoCompra(
            obra_id=obra_id,
            solicitante_id=user.id,
            data_necessidade=_parse_date(dados.get('data_necessidade')),
            tipo=tipo,
            observacao=(dados.get('observacao') or '').strip() or None,
            token_publico=secrets.token_urlsafe(24),
            itens=itens,
        )
        db.session.add(solicitacao)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao criar solicitação: %s", e)
        return jsonify({"erro": "Erro interno ao criar solicitação."}), 500

    cfg = _config()
    _notificar_ids(
        cfg.alertados_ids if cfg else [],
        tipo='solicitacao_criada',
        titulo=f"🛒 Nova solicitação de compra #{solicitacao.id}",
        mensagem=f"{user.username} solicitou {_resumo_itens(solicitacao)} para a obra {obra.nome}.",
        solicitacao=solicitacao, origem_id=user.id,
    )
    return jsonify(solicitacao.to_dict(incluir_detalhes=True)), 201


@solicitacoes_bp.route('/<int:sol_id>', methods=['GET'])
@jwt_required()
def detalhe_solicitacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403

    cfg = _config()
    out = s.to_dict(incluir_detalhes=True)
    out['pode_aprovar'] = _eh_aprovador(user, cfg)
    out['limite_valor'] = cfg.limite_valor if cfg else None
    out['pode_cancelar'] = (s.status in _STATUS_ABERTOS
                            and (user.role == 'master' or s.solicitante_id == user.id))
    return jsonify(out), 200


@solicitacoes_bp.route('/<int:sol_id>/cancelar', methods=['PATCH'])
@jwt_required()
def cancelar_solicitacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if user.role != 'master' and s.solicitante_id != user.id:
        return jsonify({"erro": "Só o solicitante ou o master podem cancelar."}), 403
    if s.status in _STATUS_DECIDIDOS:
        return jsonify({"erro": f"Solicitação {s.status.lower()} não pode ser cancelada."}), 400
    try:
        s.status = 'Cancelada'
        s.data_decisao = datetime.utcnow()
        db.session.commit()
        return jsonify(s.to_dict(incluir_detalhes=True)), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao cancelar: %s", e)
        return jsonify({"erro": "Erro interno ao cancelar solicitação."}), 500


# ---------------------------------------------------------------- cotações

@solicitacoes_bp.route('/<int:sol_id>/cotacoes', methods=['POST'])
@jwt_required()
def criar_cotacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    if s.status in _STATUS_DECIDIDOS:
        return jsonify({"erro": f"Solicitação {s.status.lower()} não aceita novas cotações."}), 400

    dados, arquivo = _dados_e_arquivo()
    fornecedor = (dados.get('fornecedor') or '').strip()
    if not fornecedor:
        return jsonify({"erro": "fornecedor é obrigatório."}), 400
    valor_total = _to_num(dados.get('valor_total'))
    if not valor_total or valor_total <= 0:
        return jsonify({"erro": "valor_total deve ser maior que zero."}), 400

    arquivo_url, upload_falhou = _upload_best_effort(arquivo, f'cotacoes/{s.id}')

    try:
        cotacao = SolicitacaoCotacao(
            solicitacao_id=s.id,
            fornecedor=fornecedor[:150],
            valor_total=valor_total,
            condicao_pagamento=(dados.get('condicao_pagamento') or '').strip()[:200] or None,
            prazo_entrega=(dados.get('prazo_entrega') or '').strip()[:100] or None,
            observacao=(dados.get('observacao') or '').strip()[:300] or None,
            arquivo_url=arquivo_url,
            criado_por_id=user.id,
        )
        db.session.add(cotacao)
        if s.status == 'Aberta':
            s.status = 'Em cotação'
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao criar cotação: %s", e)
        return jsonify({"erro": "Erro interno ao registrar cotação."}), 500

    out = cotacao.to_dict()
    if upload_falhou:
        out['aviso'] = 'Cotação salva, mas o upload do anexo falhou. Tente anexar novamente.'
    return jsonify(out), 201


@solicitacoes_bp.route('/<int:sol_id>/cotacoes/<int:cot_id>', methods=['DELETE'])
@jwt_required()
def remover_cotacao(sol_id, cot_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    cotacao = SolicitacaoCotacao.query.filter_by(id=cot_id, solicitacao_id=sol_id).first()
    if not cotacao:
        return jsonify({"erro": "Cotação não encontrada."}), 404
    if user.role != 'master' and cotacao.criado_por_id != user.id:
        return jsonify({"erro": "Só quem registrou a cotação (ou o master) pode removê-la."}), 403
    if s.status == 'Aprovada':
        return jsonify({"erro": "Solicitação aprovada — cotações não podem ser removidas."}), 400
    try:
        db.session.delete(cotacao)
        db.session.commit()
        return jsonify({"mensagem": "Cotação removida."}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao remover cotação: %s", e)
        return jsonify({"erro": "Erro interno ao remover cotação."}), 500


@solicitacoes_bp.route('/<int:sol_id>/cotacoes/<int:cot_id>/arquivo', methods=['GET'])
@jwt_required()
def arquivo_cotacao(sol_id, cot_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    cotacao = SolicitacaoCotacao.query.filter_by(id=cot_id, solicitacao_id=sol_id).first()
    if not cotacao or not cotacao.arquivo_url:
        return jsonify({"erro": "Cotação sem arquivo."}), 404
    try:
        url = storage_service.signed_url(cotacao.arquivo_url, bucket=BUCKET_SOLICITACOES)
        return jsonify({"url": url}), 200
    except Exception as e:
        logger.exception("Solicitações: erro ao gerar URL do arquivo: %s", e)
        return jsonify({"erro": "Erro ao gerar link do arquivo."}), 500


# ---------------------------------------------------------------- fluxo de decisão

@solicitacoes_bp.route('/<int:sol_id>/enviar-aprovacao', methods=['PATCH'])
@jwt_required()
def enviar_aprovacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    if s.status != 'Em cotação':
        return jsonify({"erro": "Só solicitações em cotação podem ser enviadas para aprovação."}), 400
    if not s.cotacoes:
        return jsonify({"erro": "Registre ao menos uma cotação antes de enviar para aprovação."}), 400

    try:
        s.status = 'Aguardando aprovação'
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao enviar para aprovação: %s", e)
        return jsonify({"erro": "Erro interno ao enviar para aprovação."}), 500

    cfg = _config()
    _notificar_ids(
        cfg.aprovadores_ids if cfg else [],
        tipo='solicitacao_aguardando_aprovacao',
        titulo=f"⏳ Solicitação #{s.id} aguarda aprovação",
        mensagem=f"{_resumo_itens(s)} — obra {s.obra.nome if s.obra else ''} ({len(s.cotacoes)} cotação(ões)).",
        solicitacao=s, origem_id=user.id,
    )
    return jsonify(s.to_dict(incluir_detalhes=True)), 200


@solicitacoes_bp.route('/<int:sol_id>/aprovar', methods=['POST'])
@jwt_required()
def aprovar_solicitacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403

    # Anti-duplicação: duplo clique / duas abas criaria conta a pagar em dobro.
    if s.status == 'Aprovada' or s.pagamento_futuro_id:
        return jsonify({"erro": "Solicitação já aprovada."}), 400
    if s.status not in ('Em cotação', 'Aguardando aprovação'):
        return jsonify({"erro": f"Solicitação {s.status.lower()} não pode ser aprovada."}), 400

    dados = request.get_json(silent=True) or {}
    cot_id = _to_int(dados.get('cotacao_id'))
    if not cot_id:
        return jsonify({"erro": "cotacao_id é obrigatório (escolha a cotação vencedora)."}), 400
    cotacao = SolicitacaoCotacao.query.filter_by(id=cot_id, solicitacao_id=s.id).first()
    if not cotacao:
        return jsonify({"erro": "Cotação não pertence a esta solicitação."}), 400

    cfg = _config()
    valor = float(cotacao.valor_total)
    if not _eh_aprovador(user, cfg) and not _pode_efetivar(cfg, valor):
        if cfg and cfg.limite_valor is not None:
            return jsonify({"erro": "Valor acima do limite de "
                                    f"R$ {cfg.limite_valor:.2f} — exige um aprovador."}), 403
        return jsonify({"erro": "Toda compra exige aprovação de um aprovador configurado."}), 403

    try:
        vencimento = s.data_necessidade or (date.today() + timedelta(days=7))
        observacoes = f"Gerado pela Solicitação de compra #{s.id}"
        if cotacao.condicao_pagamento:
            observacoes += f" — Condição: {cotacao.condicao_pagamento}"
        pf = PagamentoFuturo(
            obra_id=s.obra_id,
            descricao=f"Compra: {_resumo_itens(s)} (Solicitação #{s.id})"[:255],
            valor=valor,
            data_vencimento=vencimento,
            status='Previsto',
            fornecedor=cotacao.fornecedor,
            tipo=s.tipo,
            observacoes=observacoes,
        )
        db.session.add(pf)
        db.session.flush()  # garante pf.id antes do commit

        s.pagamento_futuro_id = pf.id
        s.cotacao_aprovada_id = cotacao.id
        s.status = 'Aprovada'
        s.aprovador_id = user.id
        s.data_decisao = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao aprovar: %s", e)
        return jsonify({"erro": "Erro interno ao aprovar solicitação."}), 500

    # Notificações só DEPOIS do commit (criar_notificacao commita internamente).
    destinos = {s.solicitante_id}
    if cfg:
        destinos.update(cfg.alertados_ids or [])
    _notificar_ids(
        list(destinos),
        tipo='solicitacao_aprovada',
        titulo=f"✅ Compra da solicitação #{s.id} aprovada",
        mensagem=(f"{_resumo_itens(s)} — {cotacao.fornecedor}, R$ {valor:.2f}. "
                  f"Lançada no financeiro da obra {s.obra.nome if s.obra else ''}."),
        solicitacao=s, origem_id=user.id,
    )
    out = s.to_dict(incluir_detalhes=True)
    out['pagamento_futuro'] = pf.to_dict()
    return jsonify(out), 200


@solicitacoes_bp.route('/<int:sol_id>/rejeitar', methods=['POST'])
@jwt_required()
def rejeitar_solicitacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    cfg = _config()
    if not _eh_aprovador(user, cfg):
        return jsonify({"erro": "Só aprovadores podem rejeitar solicitações."}), 403
    if s.status not in ('Em cotação', 'Aguardando aprovação'):
        return jsonify({"erro": f"Solicitação {s.status.lower()} não pode ser rejeitada."}), 400

    dados = request.get_json(silent=True) or {}
    motivo = (dados.get('motivo') or '').strip()
    if not motivo:
        return jsonify({"erro": "motivo é obrigatório para rejeitar."}), 400

    try:
        s.status = 'Rejeitada'
        s.motivo_rejeicao = motivo[:300]
        s.aprovador_id = user.id
        s.data_decisao = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao rejeitar: %s", e)
        return jsonify({"erro": "Erro interno ao rejeitar solicitação."}), 500

    _notificar_ids(
        [s.solicitante_id],
        tipo='solicitacao_rejeitada',
        titulo=f"❌ Solicitação #{s.id} rejeitada",
        mensagem=f"Motivo: {motivo[:200]}",
        solicitacao=s, origem_id=user.id,
    )
    return jsonify(s.to_dict(incluir_detalhes=True)), 200


# ---------------------------------------------------------------- config (master)

@solicitacoes_bp.route('/config', methods=['GET'])
@jwt_required()
def obter_config():
    user = get_current_user()
    if user.role != 'master':
        return jsonify({"erro": "Apenas o master pode ver a configuração."}), 403
    cfg = _config()
    if not cfg:
        return jsonify({"alertados_ids": [], "aprovadores_ids": [], "limite_valor": None}), 200
    return jsonify(cfg.to_dict()), 200


@solicitacoes_bp.route('/config', methods=['PUT'])
@jwt_required()
def salvar_config():
    user = get_current_user()
    if user.role != 'master':
        return jsonify({"erro": "Apenas o master pode alterar a configuração."}), 403

    dados = request.get_json(silent=True) or {}

    def _validar_ids(campo):
        ids = dados.get(campo)
        if ids is None:
            return [], None
        if not isinstance(ids, list):
            return None, f"{campo} deve ser uma lista de ids."
        limpos = []
        for v in ids:
            uid = _to_int(v)
            if not uid:
                return None, f"{campo}: id inválido ({v})."
            if not User.query.get(uid):
                return None, f"{campo}: usuário {uid} não existe."
            limpos.append(uid)
        return limpos, None

    alertados, erro = _validar_ids('alertados_ids')
    if erro:
        return jsonify({"erro": erro}), 400
    aprovadores, erro = _validar_ids('aprovadores_ids')
    if erro:
        return jsonify({"erro": erro}), 400

    limite = None
    if dados.get('limite_valor') not in (None, ''):
        limite = _to_num(dados.get('limite_valor'))
        if limite is None or limite < 0:
            return jsonify({"erro": "limite_valor inválido."}), 400

    try:
        cfg = _config()
        if not cfg:
            cfg = SolicitacaoConfig(id=1)
            db.session.add(cfg)
        cfg.alertados_ids = alertados
        cfg.aprovadores_ids = aprovadores
        cfg.limite_valor = limite
        db.session.commit()
        return jsonify(cfg.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao salvar config: %s", e)
        return jsonify({"erro": "Erro interno ao salvar configuração."}), 500


# ---------------------------------------------------------------- rota pública

@solicitacoes_bp.route('/publico/<token>', methods=['GET'])
def publico_solicitacao(token):
    """Snapshot público da solicitação (link compartilhável no WhatsApp).

    SEM auth — o bypass está no before_request. Nunca expõe cotações,
    valores ou ids de usuários."""
    s = SolicitacaoCompra.query.filter_by(token_publico=token).first()
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    return jsonify(s.to_dict_publico()), 200
