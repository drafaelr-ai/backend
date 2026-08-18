"""Blueprint do módulo Solicitações — solicitação de compras de materiais,
insumos e equipamentos por obra.

Fluxo: Aberta → Em cotação (1ª cotação) → Aguardando aprovação → Aprovada →
Atendida (| Rejeitada | Cancelada). Ao aprovar (ou efetivar direto, quando a
cotação escolhida está dentro do limite configurado) é criado um PagamentoFuturo
('Previsto') na obra — o financeiro completa depois pelos fluxos existentes.

'Atendida' é a baixa do comprador: compra feita/entregue. Solicitações
atendidas somem da lista de compras (`GET /solicitacoes`) e passam a viver no
histórico (`GET /solicitacoes?historico=true`).

Config (linha única id=1): usuários alertados na criação (pesquisa de preços),
aprovadores e limite de valor. Limite ausente = toda compra exige aprovador.

`GET /solicitacoes/publico/<token>` é PÚBLICA (sem JWT/módulo) — snapshot
read-only da solicitação, compartilhável via WhatsApp. Nunca expõe cotações.

Visibilidade: master/administrador veem tudo; comum vê solicitações de suas
obras permitidas e as que ele mesmo criou.
Erros de validação são SEMPRE 400 — nunca 422 (fetchWithAuth desloga em 401/422).
"""
import io
import json
import logging
import secrets
from datetime import datetime, date, timedelta

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import jwt_required, verify_jwt_in_request
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from extensions import db
from models.solicitacao_compra import SolicitacaoCompra
from models.solicitacao_item import SolicitacaoItem
from models.solicitacao_cotacao import SolicitacaoCotacao
from models.solicitacao_comentario import SolicitacaoComentario
from models.solicitacao_config import SolicitacaoConfig
from models.solicitacao_entrega import SolicitacaoEntrega
from models.pagamento_futuro import PagamentoFuturo
from models.obra import Obra
from models.user import User
from services import storage_service
from services import get_current_user, user_has_access_to_obra, user_tem_modulo
from services.notificacao_service import criar_notificacao
from services.solicitacao_document_service import (
    PedidoLeituraError,
    gerar_pdf,
    gerar_xlsx,
    ler_pedido,
)

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
    if request.endpoint in ('solicitacoes.publico_solicitacao',
                            'solicitacoes.publico_entrega',
                            'solicitacoes.publico_entrega_confirmar',
                            'solicitacoes.publico_entrega_mensagem',
                            'solicitacoes.publico_entrega_pdf'):
        return None
    verify_jwt_in_request()
    if not user_tem_modulo(get_current_user(), 'solicitacoes'):
        return jsonify({"erro": "Acesso negado: você não tem permissão para o módulo Solicitações."}), 403

BUCKET_SOLICITACOES = 'solicitacoes-arquivos'

_TIPOS = {'Material', 'Equipamentos', 'Mão de Obra', 'Despesa'}
_STATUS_ABERTOS = {'Aberta', 'Em cotação', 'Aguardando aprovação'}
_STATUS_DECIDIDOS = {'Aprovada', 'Rejeitada', 'Cancelada', 'Atendida'}
# Status que saem da lista de compras e vão para o histórico.
_STATUS_HISTORICO = ('Atendida',)
_MAX_ANEXOS_COTACAO = 5
_MAX_ANEXO_COTACAO_BYTES = 10 * 1024 * 1024
_MAX_TOTAL_ANEXOS_COTACAO_BYTES = 30 * 1024 * 1024
_TIPOS_ANEXO_COTACAO = {
    'application/pdf',
    'image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif',
}


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


def _dados_e_arquivos_cotacao():
    """Multipart com vários `arquivos`; aceita o campo legado `arquivo`."""
    if not (request.content_type and 'multipart/form-data' in request.content_type):
        return (request.get_json(silent=True) or {}), []

    arquivos = list(request.files.getlist('arquivos'))
    for campo in ('arquivo', 'file'):
        legado = request.files.get(campo)
        if legado and legado not in arquivos:
            arquivos.append(legado)
    return request.form, [arquivo for arquivo in arquivos if arquivo and arquivo.filename]


def _tamanho_arquivo(arquivo):
    stream = arquivo.stream
    posicao = stream.tell()
    stream.seek(0, 2)
    tamanho = stream.tell()
    stream.seek(posicao)
    return tamanho


def _validar_arquivos_cotacao(arquivos):
    if len(arquivos) > _MAX_ANEXOS_COTACAO:
        return f'Envie no máximo {_MAX_ANEXOS_COTACAO} anexos por cotação.'

    total = 0
    for arquivo in arquivos:
        tipo = (arquivo.mimetype or '').lower().split(';')[0]
        nome = (arquivo.filename or '').lower()
        if tipo not in _TIPOS_ANEXO_COTACAO:
            # Alguns celulares enviam application/octet-stream. Só aceitamos
            # esse fallback quando a extensão também é conhecida.
            extensao_valida = nome.endswith(('.pdf', '.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'))
            if tipo != 'application/octet-stream' or not extensao_valida:
                return f'Formato não aceito em {arquivo.filename}. Use PDF, JPG, PNG, WEBP ou HEIC.'
        tamanho = _tamanho_arquivo(arquivo)
        if tamanho <= 0:
            return f'O arquivo {arquivo.filename} está vazio.'
        if tamanho > _MAX_ANEXO_COTACAO_BYTES:
            return f'O arquivo {arquivo.filename} ultrapassa 10 MB.'
        total += tamanho
    if total > _MAX_TOTAL_ANEXOS_COTACAO_BYTES:
        return 'Os anexos da cotação ultrapassam 30 MB no total.'
    return None


def _upload_arquivos_cotacao(arquivos, pasta):
    anexos, falhas = [], 0
    for arquivo in arquivos:
        path, falhou = _upload_best_effort(arquivo, pasta)
        if path:
            anexos.append({
                'path': path,
                'nome': secure_filename(arquivo.filename) or 'anexo',
            })
        if falhou:
            falhas += 1
    return anexos, falhas


def _config():
    return SolicitacaoConfig.get()


def _eh_aprovador(user, cfg):
    if user.role == 'master':
        return True
    return bool(cfg and user.id in (cfg.aprovadores_ids or []))


def _eh_comprador(user, cfg, s=None):
    """Quem dá baixa na compra: master, aprovadores, os compradores
    configurados (alertados — quem faz a pesquisa de preços) e quem registrou
    a cotação escolhida (foi quem comprou).

    Sem comprador nem aprovador configurado, qualquer usuário do módulo com
    acesso à solicitação pode dar baixa — senão a baixa fica impossível em
    instalações que nunca abriram a tela de configuração."""
    if user.role == 'master':
        return True
    if cfg and (user.id in (cfg.alertados_ids or [])
                or user.id in (cfg.aprovadores_ids or [])):
        return True
    if s is not None and s.cotacao_aprovada_id:
        cot = next((c for c in s.cotacoes if c.id == s.cotacao_aprovada_id), None)
        if cot and cot.criado_por_id == user.id:
            return True
    return not (cfg and ((cfg.alertados_ids or []) or (cfg.aprovadores_ids or [])))


def _pode_atender(s, user, cfg):
    """Só compra aprovada é atendida — antes disso não há o que dar baixa."""
    return s.status == 'Aprovada' and _eh_comprador(user, cfg, s)


def _pode_reabrir(s, user):
    """Desfazer a baixa: só o master ou quem marcou como atendida (evita que
    um clique errado enterre a compra no histórico sem volta)."""
    return s.status == 'Atendida' and (user.role == 'master'
                                       or s.atendida_por_id == user.id)


def _pode_editar(s, user):
    """A lista pode mudar somente antes de existir pesquisa de preços.

    Depois da primeira cotação, alterar quantidade/itens invalidaria os valores
    recebidos e, após a aprovação, poderia divergir do financeiro.
    """
    return s.status == 'Aberta' and (user.role == 'master'
                                     or s.solicitante_id == user.id)


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


def _usuarios_do_modulo():
    """Usuários mencionáveis (@usuario): qualquer um com o módulo liberado."""
    return [u for u in User.query.order_by(User.username).all()
            if user_tem_modulo(u, 'solicitacoes')]


def _resolver_mencionados(texto, ids_payload):
    """União do que o front marcou (ids) com @username achado no texto —
    menção digitada à mão sem o autocomplete também vale. Só usuários do
    módulo contam; ids desconhecidos são descartados em silêncio."""
    usuarios = _usuarios_do_modulo()
    por_id = {u.id: u for u in usuarios}
    texto_lower = (texto or '').lower()
    ids = set()
    for v in (ids_payload or []):
        uid = _to_int(v)
        if uid and uid in por_id:
            ids.add(uid)
    for u in usuarios:
        if f'@{u.username}'.lower() in texto_lower:
            ids.add(u.id)
    return sorted(ids)


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

@solicitacoes_bp.route('/ler-pedido', methods=['POST'])
@jwt_required()
def ler_pedido_solicitacao():
    """Extrai somente os campos úteis de um Excel/PDF para revisão no formulário."""
    arquivo = request.files.get('arquivo') or request.files.get('file')
    if not arquivo:
        return jsonify({"erro": "Selecione um arquivo Excel (.xlsx) ou PDF."}), 400
    try:
        return jsonify(ler_pedido(arquivo)), 200
    except PedidoLeituraError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:
        logger.exception("Solicitações: erro inesperado ao ler pedido: %s", exc)
        return jsonify({"erro": "Não foi possível ler o pedido enviado."}), 500


@solicitacoes_bp.route('', methods=['GET'])
@jwt_required()
def listar_solicitacoes():
    """Lista de compras (default) ou histórico (`?historico=true`).

    Compras atendidas saem da lista e só aparecem no histórico — ou quando
    pedidas explicitamente via `?status=Atendida`."""
    user = get_current_user()
    query = _filtro_visibilidade(SolicitacaoCompra.query, user)

    historico = (request.args.get('historico') or '').strip().lower() in ('1', 'true', 'sim')
    status = (request.args.get('status') or '').strip()
    if status:
        query = query.filter(SolicitacaoCompra.status == status)
    elif historico:
        query = query.filter(SolicitacaoCompra.status.in_(_STATUS_HISTORICO))
    else:
        query = query.filter(~SolicitacaoCompra.status.in_(_STATUS_HISTORICO))

    obra_id = _to_int(request.args.get('obra_id'))
    if obra_id:
        query = query.filter(SolicitacaoCompra.obra_id == obra_id)

    ordem = (SolicitacaoCompra.data_atendimento.desc() if historico
             else SolicitacaoCompra.data_criacao.desc())
    solicitacoes = query.order_by(ordem).all()

    cfg = _config()
    saida = []
    for s in solicitacoes:
        out = s.to_dict()
        out['pode_atender'] = _pode_atender(s, user, cfg)
        saida.append(out)
    return jsonify(saida), 200


@solicitacoes_bp.route('', methods=['POST'])
@jwt_required()
def criar_solicitacao():
    user = get_current_user()
    dados, arquivo = _dados_e_arquivo()

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
    if isinstance(itens_dados, str):
        # multipart/form-data: itens vem como JSON string no form
        try:
            itens_dados = json.loads(itens_dados)
        except ValueError:
            itens_dados = None
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

    # Anexo depois do commit (o id nomeia a pasta) — upload nunca desfaz o save.
    upload_falhou = False
    if arquivo:
        arquivo_url, upload_falhou = _upload_best_effort(arquivo, f'solicitacoes/{solicitacao.id}')
        if arquivo_url:
            try:
                solicitacao.arquivo_url = arquivo_url
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.exception("Solicitações: erro ao salvar anexo da solicitação: %s", e)
                upload_falhou = True

    cfg = _config()
    _notificar_ids(
        cfg.alertados_ids if cfg else [],
        tipo='solicitacao_criada',
        titulo=f"🛒 Nova solicitação de compra #{solicitacao.id}",
        mensagem=f"{user.username} solicitou {_resumo_itens(solicitacao)} para a obra {obra.nome}.",
        solicitacao=solicitacao, origem_id=user.id,
    )
    out = solicitacao.to_dict(incluir_detalhes=True)
    if upload_falhou:
        out['aviso'] = 'Solicitação criada, mas o upload do anexo falhou. Tente anexar novamente.'
    return jsonify(out), 201


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
    out['pode_atender'] = _pode_atender(s, user, cfg)
    out['pode_reabrir'] = _pode_reabrir(s, user)
    out['pode_editar'] = _pode_editar(s, user)
    out['pode_desaprovar'] = s.status == 'Aprovada' and _eh_aprovador(user, cfg)
    out['entrega'] = s.entrega.to_dict() if s.entrega else None
    return jsonify(out), 200


def _solicitacao_para_exportar(sol_id):
    user = get_current_user()
    solicitacao = SolicitacaoCompra.query.get(sol_id)
    if not solicitacao:
        return None, (jsonify({"erro": "Solicitação não encontrada."}), 404)
    if not _solicitacao_visivel(solicitacao, user):
        return None, (jsonify({"erro": "Acesso negado a esta solicitação."}), 403)
    return solicitacao, None


@solicitacoes_bp.route('/<int:sol_id>/exportar.xlsx', methods=['GET'])
@jwt_required()
def exportar_solicitacao_xlsx(sol_id):
    solicitacao, erro = _solicitacao_para_exportar(sol_id)
    if erro:
        return erro
    try:
        resposta = send_file(
            io.BytesIO(gerar_xlsx(solicitacao)),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'solicitacao_{solicitacao.id}.xlsx',
        )
        resposta.headers['Cache-Control'] = 'private, no-store'
        return resposta
    except Exception as exc:
        logger.exception("Solicitações: erro ao exportar Excel #%s: %s", sol_id, exc)
        return jsonify({"erro": "Não foi possível gerar o Excel da solicitação."}), 500


@solicitacoes_bp.route('/<int:sol_id>/exportar.pdf', methods=['GET'])
@jwt_required()
def exportar_solicitacao_pdf(sol_id):
    solicitacao, erro = _solicitacao_para_exportar(sol_id)
    if erro:
        return erro
    try:
        resposta = send_file(
            io.BytesIO(gerar_pdf(solicitacao)),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'solicitacao_{solicitacao.id}.pdf',
        )
        resposta.headers['Cache-Control'] = 'private, no-store'
        return resposta
    except Exception as exc:
        logger.exception("Solicitações: erro ao exportar PDF #%s: %s", sol_id, exc)
        return jsonify({"erro": "Não foi possível gerar o PDF da solicitação."}), 500


@solicitacoes_bp.route('/<int:sol_id>', methods=['PATCH'])
@jwt_required()
def editar_solicitacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    if not _pode_editar(s, user):
        if s.status != 'Aberta':
            return jsonify({"erro": "Só é possível editar uma solicitação enquanto ela está Aberta, antes das cotações."}), 400
        return jsonify({"erro": "Só o solicitante ou o master podem editar esta solicitação."}), 403

    dados, arquivo = _dados_e_arquivo()
    obra_id = _to_int(dados.get('obra_id'))
    if not obra_id:
        return jsonify({"erro": "obra_id é obrigatório."}), 400
    obra = Obra.query.get(obra_id)
    if not obra:
        return jsonify({"erro": "Obra não encontrada."}), 400
    if not user_has_access_to_obra(user, obra_id):
        return jsonify({"erro": "Você não tem acesso a esta obra."}), 403
    if getattr(obra, 'arquivada', False):
        return jsonify({"erro": "Obra arquivada — não é possível mover a solicitação para ela."}), 400

    tipo = (dados.get('tipo') or 'Material').strip()
    if tipo not in _TIPOS:
        return jsonify({"erro": f"tipo inválido (use {sorted(_TIPOS)})"}), 400

    itens_dados = dados.get('itens')
    if isinstance(itens_dados, str):
        try:
            itens_dados = json.loads(itens_dados)
        except ValueError:
            itens_dados = None
    if not isinstance(itens_dados, list) or not itens_dados:
        return jsonify({"erro": "Informe ao menos um item na solicitação."}), 400

    novos_itens = []
    for idx, item in enumerate(itens_dados, start=1):
        descricao = (item.get('descricao') or '').strip() if isinstance(item, dict) else ''
        quantidade = _to_num(item.get('quantidade')) if isinstance(item, dict) else None
        if not descricao:
            return jsonify({"erro": f"Item {idx}: descrição é obrigatória."}), 400
        if not quantidade or quantidade <= 0:
            return jsonify({"erro": f"Item {idx}: quantidade deve ser maior que zero."}), 400
        novos_itens.append(SolicitacaoItem(
            descricao=descricao[:300],
            quantidade=quantidade,
            unidade=(item.get('unidade') or '').strip()[:20] or None,
            observacao=(item.get('observacao') or '').strip()[:300] or None,
        ))

    try:
        s.obra_id = obra_id
        s.tipo = tipo
        s.data_necessidade = _parse_date(dados.get('data_necessidade'))
        s.observacao = (dados.get('observacao') or '').strip() or None
        s.itens = novos_itens
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao editar solicitação: %s", e)
        return jsonify({"erro": "Erro interno ao editar solicitação."}), 500

    upload_falhou = False
    if arquivo:
        arquivo_url, upload_falhou = _upload_best_effort(arquivo, f'solicitacoes/{s.id}')
        if arquivo_url:
            try:
                s.arquivo_url = arquivo_url
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.exception("Solicitações: erro ao substituir anexo: %s", e)
                upload_falhou = True

    cfg = _config()
    _notificar_ids(
        cfg.alertados_ids if cfg else [],
        tipo='solicitacao_editada',
        titulo=f"✏️ Solicitação de compra #{s.id} atualizada",
        mensagem=f"{user.username} atualizou {_resumo_itens(s)} para a obra {obra.nome}.",
        solicitacao=s, origem_id=user.id,
    )
    out = s.to_dict(incluir_detalhes=True)
    out['pode_editar'] = True
    if upload_falhou:
        out['aviso'] = 'Alterações salvas, mas o upload do novo anexo falhou.'
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


@solicitacoes_bp.route('/<int:sol_id>/arquivo', methods=['GET'])
@jwt_required()
def arquivo_solicitacao(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    if not s.arquivo_url:
        return jsonify({"erro": "Solicitação sem arquivo."}), 404
    try:
        url = storage_service.signed_url(s.arquivo_url, bucket=BUCKET_SOLICITACOES)
        return jsonify({"url": url}), 200
    except Exception as e:
        logger.exception("Solicitações: erro ao gerar URL do arquivo: %s", e)
        return jsonify({"erro": "Erro ao gerar link do arquivo."}), 500


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

    dados, arquivos = _dados_e_arquivos_cotacao()
    fornecedor = (dados.get('fornecedor') or '').strip()
    if not fornecedor:
        return jsonify({"erro": "fornecedor é obrigatório."}), 400
    valor_total = _to_num(dados.get('valor_total'))
    if not valor_total or valor_total <= 0:
        return jsonify({"erro": "valor_total deve ser maior que zero."}), 400

    erro_arquivos = _validar_arquivos_cotacao(arquivos)
    if erro_arquivos:
        return jsonify({"erro": erro_arquivos}), 400

    anexos, uploads_falhos = _upload_arquivos_cotacao(arquivos, f'cotacoes/{s.id}')

    try:
        cotacao = SolicitacaoCotacao(
            solicitacao_id=s.id,
            fornecedor=fornecedor[:150],
            valor_total=valor_total,
            condicao_pagamento=(dados.get('condicao_pagamento') or '').strip()[:200] or None,
            prazo_entrega=(dados.get('prazo_entrega') or '').strip()[:100] or None,
            observacao=(dados.get('observacao') or '').strip()[:300] or None,
            arquivo_url=anexos[0]['path'] if anexos else None,
            arquivos_json=anexos or None,
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
    if uploads_falhos:
        out['aviso'] = (
            f'Cotação salva com {len(anexos)} de {len(arquivos)} anexos. '
            'Tente enviar novamente os arquivos que faltaram.'
        )
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
    if s.status in ('Aprovada', 'Atendida'):
        return jsonify({"erro": f"Solicitação {s.status.lower()} — cotações não podem ser removidas."}), 400
    try:
        db.session.delete(cotacao)
        db.session.commit()
        return jsonify({"mensagem": "Cotação removida."}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao remover cotação: %s", e)
        return jsonify({"erro": "Erro interno ao remover cotação."}), 500


@solicitacoes_bp.route('/<int:sol_id>/cotacoes/<int:cot_id>/arquivo', methods=['GET'])
@solicitacoes_bp.route('/<int:sol_id>/cotacoes/<int:cot_id>/arquivos/<int:arquivo_indice>', methods=['GET'])
@jwt_required()
def arquivo_cotacao(sol_id, cot_id, arquivo_indice=0):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    cotacao = SolicitacaoCotacao.query.filter_by(id=cot_id, solicitacao_id=sol_id).first()
    if not cotacao:
        return jsonify({"erro": "Cotação não encontrada."}), 404
    anexos = cotacao.anexos_storage()
    if not anexos:
        return jsonify({"erro": "Cotação sem arquivo."}), 404
    if arquivo_indice < 0 or arquivo_indice >= len(anexos):
        return jsonify({"erro": "Anexo não encontrado."}), 404
    try:
        anexo = anexos[arquivo_indice]
        url = storage_service.signed_url(anexo['path'], bucket=BUCKET_SOLICITACOES)
        return jsonify({"url": url, "nome": anexo['nome'], "indice": arquivo_indice}), 200
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


@solicitacoes_bp.route('/<int:sol_id>/devolver', methods=['PATCH'])
@jwt_required()
def devolver_solicitacao(sol_id):
    """Desfaz a aprovação (Aprovada → Em cotação): o comprador volta a mexer
    nas cotações e uma nova decisão pode ser tomada. Só aprovador/master.

    A conta a pagar 'Previsto' criada na aprovação é removida junto; se o
    financeiro já mexeu nela (status != Previsto), bloqueia — desfazer a
    compra sem desfazer o dinheiro divergiria os dois módulos."""
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    cfg = _config()
    if not _eh_aprovador(user, cfg):
        return jsonify({"erro": "Só um aprovador (ou o master) pode devolver a compra para cotação."}), 403
    if s.status == 'Atendida':
        return jsonify({"erro": "Compra atendida — reabra a compra antes de devolvê-la para cotação."}), 400
    if s.status != 'Aprovada':
        return jsonify({"erro": f"Solicitação {s.status.lower()} não pode ser devolvida para cotação."}), 400

    pf = PagamentoFuturo.query.get(s.pagamento_futuro_id) if s.pagamento_futuro_id else None
    if pf and (pf.status or '') != 'Previsto':
        return jsonify({"erro": "A conta a pagar desta compra já foi movimentada no financeiro "
                                f"(status: {pf.status}) — ajuste lá antes de devolver."}), 400

    try:
        if pf:
            db.session.delete(pf)
        # O link de entrega aponta pra uma compra que deixou de existir —
        # some junto (se a compra for re-aprovada, gera-se outro).
        if s.entrega:
            db.session.delete(s.entrega)
        s.status = 'Em cotação'
        s.cotacao_aprovada_id = None
        s.pagamento_futuro_id = None
        s.aprovador_id = None
        s.data_decisao = None
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao devolver para cotação: %s", e)
        return jsonify({"erro": "Erro interno ao devolver a compra para cotação."}), 500

    destinos = {s.solicitante_id}
    if cfg:
        destinos.update(cfg.alertados_ids or [])
    _notificar_ids(
        list(destinos),
        tipo='solicitacao_devolvida',
        titulo=f"↩️ Compra da solicitação #{s.id} devolvida para cotação",
        mensagem=(f"{user.username} desfez a aprovação de {_resumo_itens(s)} — "
                  "a conta a pagar prevista foi removida do financeiro."),
        solicitacao=s, origem_id=user.id,
    )
    out = s.to_dict(incluir_detalhes=True)
    out['pode_desaprovar'] = False
    return jsonify(out), 200


# ---------------------------------------------------------------- atendimento (comprador)

@solicitacoes_bp.route('/<int:sol_id>/atender', methods=['PATCH'])
@jwt_required()
def atender_solicitacao(sol_id):
    """Baixa do comprador: compra feita/entregue.

    A solicitação sai da lista de compras e passa para o histórico. Não mexe
    no PagamentoFuturo — a conta a pagar segue seu ciclo no financeiro."""
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    if s.status == 'Atendida':
        return jsonify({"erro": "Solicitação já atendida."}), 400
    if s.status != 'Aprovada':
        return jsonify({"erro": "Só compras aprovadas podem ser marcadas como atendidas."}), 400

    cfg = _config()
    if not _eh_comprador(user, cfg, s):
        return jsonify({"erro": "Só o comprador ou um aprovador pode dar baixa na compra."}), 403

    dados = request.get_json(silent=True) or {}
    data_atendimento = _parse_date(dados.get('data_atendimento'))
    if dados.get('data_atendimento') and not data_atendimento:
        return jsonify({"erro": "data_atendimento inválida."}), 400
    if data_atendimento and data_atendimento > date.today():
        return jsonify({"erro": "A data do atendimento não pode estar no futuro."}), 400
    if (data_atendimento and s.data_criacao
            and data_atendimento < s.data_criacao.date()):
        return jsonify({"erro": "A data do atendimento não pode ser anterior à solicitação."}), 400

    try:
        agora = datetime.utcnow()
        s.status = 'Atendida'
        s.atendida_por_id = user.id
        s.data_atendimento = (datetime.combine(data_atendimento, agora.time())
                              if data_atendimento else agora)
        s.observacao_atendimento = (dados.get('observacao') or '').strip()[:300] or None
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao atender: %s", e)
        return jsonify({"erro": "Erro interno ao marcar a compra como atendida."}), 500

    destinos = {s.solicitante_id}
    if cfg:
        destinos.update(cfg.alertados_ids or [])
    _notificar_ids(
        list(destinos),
        tipo='solicitacao_atendida',
        titulo=f"📦 Compra da solicitação #{s.id} atendida",
        mensagem=(f"{_resumo_itens(s)} — obra {s.obra.nome if s.obra else ''}. "
                  f"Atendida em {s.data_atendimento.strftime('%d/%m/%Y')} por {user.username}."),
        solicitacao=s, origem_id=user.id,
    )
    out = s.to_dict(incluir_detalhes=True)
    out['pode_atender'] = False
    out['pode_reabrir'] = _pode_reabrir(s, user)
    return jsonify(out), 200


@solicitacoes_bp.route('/<int:sol_id>/reabrir', methods=['PATCH'])
@jwt_required()
def reabrir_solicitacao(sol_id):
    """Desfaz a baixa (Atendida → Aprovada) — volta da lista de histórico
    para a lista de compras. Só master ou quem marcou como atendida."""
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    if s.status != 'Atendida':
        return jsonify({"erro": "Só solicitações atendidas podem ser reabertas."}), 400
    if not _pode_reabrir(s, user):
        return jsonify({"erro": "Só quem deu a baixa (ou o master) pode reabrir a compra."}), 403

    try:
        s.status = 'Aprovada'
        s.atendida_por_id = None
        s.data_atendimento = None
        s.observacao_atendimento = None
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao reabrir: %s", e)
        return jsonify({"erro": "Erro interno ao reabrir a compra."}), 500

    out = s.to_dict(incluir_detalhes=True)
    out['pode_atender'] = _pode_atender(s, user, _config())
    out['pode_reabrir'] = False
    return jsonify(out), 200


# ---------------------------------------------------------------- comentários (@menção)

@solicitacoes_bp.route('/usuarios-mencao', methods=['GET'])
@jwt_required()
def usuarios_mencao():
    """Usuários mencionáveis no @ — qualquer usuário do módulo pode consultar
    (a lista de config usa /admin/users, que é só do master). Expõe apenas
    id/username/role — nada sensível."""
    return jsonify([
        {'id': u.id, 'username': u.username, 'role': u.role}
        for u in _usuarios_do_modulo()
    ]), 200


@solicitacoes_bp.route('/<int:sol_id>/comentarios', methods=['GET'])
@jwt_required()
def listar_comentarios(sol_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    return jsonify([c.to_dict() for c in s.comentarios]), 200


@solicitacoes_bp.route('/<int:sol_id>/comentarios', methods=['POST'])
@jwt_required()
def criar_comentario(sol_id):
    """Comentário na conversa da solicitação. Cada @usuario citado recebe
    notificação de menção; o solicitante é avisado de qualquer comentário
    de terceiros (mesmo sem menção), como dono da solicitação."""
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403

    dados = request.get_json(silent=True) or {}
    texto = (dados.get('texto') or '').strip()
    if not texto:
        return jsonify({"erro": "Escreva o comentário antes de enviar."}), 400
    if len(texto) > 1000:
        return jsonify({"erro": "Comentário muito longo (máximo 1000 caracteres)."}), 400

    mencionados = _resolver_mencionados(texto, dados.get('mencionados_ids'))

    try:
        comentario = SolicitacaoComentario(
            solicitacao_id=s.id,
            autor_id=user.id,
            texto=texto,
            mencionados_ids=mencionados,
        )
        db.session.add(comentario)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao comentar: %s", e)
        return jsonify({"erro": "Erro interno ao salvar o comentário."}), 500

    # Notificações só DEPOIS do commit (criar_notificacao commita internamente).
    obra_nome = s.obra.nome if s.obra else ''
    _notificar_ids(
        mencionados,
        tipo='solicitacao_mencao',
        titulo=f"💬 {user.username} mencionou você na solicitação #{s.id}",
        mensagem=f"\"{texto[:200]}\" — obra {obra_nome}.",
        solicitacao=s, origem_id=user.id,
    )
    if s.solicitante_id and s.solicitante_id not in mencionados:
        _notificar_ids(
            [s.solicitante_id],
            tipo='solicitacao_comentario',
            titulo=f"💬 Novo comentário na solicitação #{s.id}",
            mensagem=f"{user.username}: \"{texto[:200]}\" — obra {obra_nome}.",
            solicitacao=s, origem_id=user.id,
        )
    return jsonify(comentario.to_dict()), 201


@solicitacoes_bp.route('/<int:sol_id>/comentarios/<int:com_id>', methods=['DELETE'])
@jwt_required()
def remover_comentario(sol_id, com_id):
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    comentario = SolicitacaoComentario.query.filter_by(id=com_id, solicitacao_id=sol_id).first()
    if not comentario:
        return jsonify({"erro": "Comentário não encontrado."}), 404
    if user.role != 'master' and comentario.autor_id != user.id:
        return jsonify({"erro": "Só o autor do comentário (ou o master) pode removê-lo."}), 403
    try:
        db.session.delete(comentario)
        db.session.commit()
        return jsonify({"mensagem": "Comentário removido."}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao remover comentário: %s", e)
        return jsonify({"erro": "Erro interno ao remover o comentário."}), 500


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


# ---------------------------------------------------------------- entrega (superlink)

@solicitacoes_bp.route('/<int:sol_id>/entrega', methods=['POST'])
@jwt_required()
def gerar_entrega(sol_id):
    """Gera (ou regenera) o superlink de entrega pro motorista.

    Só para compra Aprovada — antes disso não há fornecedor definido; depois
    de Atendida a entrega já aconteceu. Regenerar troca o token e invalida o
    link anterior (compartilhou com o motorista errado? gera outro)."""
    user = get_current_user()
    s = SolicitacaoCompra.query.get(sol_id)
    if not s:
        return jsonify({"erro": "Solicitação não encontrada."}), 404
    if not _solicitacao_visivel(s, user):
        return jsonify({"erro": "Acesso negado a esta solicitação."}), 403
    cfg = _config()
    if not _eh_comprador(user, cfg, s):
        return jsonify({"erro": "Só o comprador ou um aprovador pode gerar o link de entrega."}), 403
    if s.status != 'Aprovada':
        return jsonify({"erro": "O link de entrega só existe para compras aprovadas."}), 400

    try:
        entrega = SolicitacaoEntrega.query.filter_by(solicitacao_id=s.id).first()
        if not entrega:
            entrega = SolicitacaoEntrega(solicitacao_id=s.id)
            db.session.add(entrega)
        entrega.token = secrets.token_urlsafe(24)
        entrega.criado_por_id = user.id
        entrega.criado_em = datetime.utcnow()
        db.session.commit()
        return jsonify(entrega.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao gerar link de entrega: %s", e)
        return jsonify({"erro": "Erro interno ao gerar o link de entrega."}), 500


def _entrega_por_token(token):
    return SolicitacaoEntrega.query.filter_by(token=token).first()


@solicitacoes_bp.route('/entrega/<token>', methods=['GET'])
def publico_entrega(token):
    """Snapshot público da entrega (motorista, sem login).

    Mostra itens, fornecedor da cotação escolhida e obra — NUNCA valores
    nem cotações (mesma regra do link público da solicitação)."""
    entrega = _entrega_por_token(token)
    if not entrega:
        return jsonify({"erro": "Link de entrega não encontrado."}), 404
    s = entrega.solicitacao
    cot = next((c for c in s.cotacoes if c.id == s.cotacao_aprovada_id), None)
    return jsonify({
        'numero': s.id,
        'status': s.status,
        'obra_nome': s.obra.nome if s.obra else None,
        'tipo': s.tipo,
        'data_necessidade': s.data_necessidade.isoformat() if s.data_necessidade else None,
        'observacao': s.observacao,
        'fornecedor': cot.fornecedor if cot else None,
        'prazo_entrega': cot.prazo_entrega if cot else None,
        'itens': [i.to_dict() for i in s.itens],
        'entregue_em': entrega.entregue_em.isoformat() if entrega.entregue_em else None,
        'observacao_entrega': entrega.observacao_entrega,
    }), 200


@solicitacoes_bp.route('/entrega/<token>/confirmar', methods=['POST'])
def publico_entrega_confirmar(token):
    """Motorista confirma a entrega na obra (com observação opcional).

    Não dá a baixa oficial — a compra segue Aprovada até o comprador marcar
    como Atendida; a confirmação registra e avisa comprador + solicitante."""
    entrega = _entrega_por_token(token)
    if not entrega:
        return jsonify({"erro": "Link de entrega não encontrado."}), 404
    if entrega.entregue_em:
        return jsonify({"erro": "Entrega já confirmada."}), 400
    s = entrega.solicitacao
    if s.status not in ('Aprovada', 'Atendida'):
        return jsonify({"erro": f"Solicitação {s.status.lower()} — não há entrega em aberto."}), 400

    dados = request.get_json(silent=True) or {}
    try:
        entrega.entregue_em = datetime.utcnow()
        entrega.observacao_entrega = (dados.get('observacao') or '').strip()[:500] or None
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro ao confirmar entrega: %s", e)
        return jsonify({"erro": "Erro interno ao confirmar a entrega."}), 500

    cfg = _config()
    destinos = {s.solicitante_id}
    if cfg:
        destinos.update(cfg.alertados_ids or [])
    obs = f" Obs.: {entrega.observacao_entrega}" if entrega.observacao_entrega else ""
    _notificar_ids(
        list(destinos),
        tipo='solicitacao_entregue',
        titulo=f"🚚 Entrega confirmada — solicitação #{s.id}",
        mensagem=f"{_resumo_itens(s)} entregue na obra {s.obra.nome if s.obra else ''}.{obs}",
        solicitacao=s, origem_id=None,
    )
    return jsonify({
        'entregue_em': entrega.entregue_em.isoformat(),
        'observacao_entrega': entrega.observacao_entrega,
    }), 200


@solicitacoes_bp.route('/entrega/<token>/mensagem', methods=['POST'])
def publico_entrega_mensagem(token):
    """Mensagem do motorista pro comprador (dificuldade na retirada/entrega).

    Vira comentário na conversa da solicitação como 'Motorista (entrega)' e
    notifica comprador + solicitante no sino/Telegram."""
    entrega = _entrega_por_token(token)
    if not entrega:
        return jsonify({"erro": "Link de entrega não encontrado."}), 404
    s = entrega.solicitacao

    dados = request.get_json(silent=True) or {}
    texto = (dados.get('texto') or '').strip()
    if not texto:
        return jsonify({"erro": "Escreva a mensagem antes de enviar."}), 400
    if len(texto) > 500:
        return jsonify({"erro": "Mensagem muito longa (máximo 500 caracteres)."}), 400

    try:
        comentario = SolicitacaoComentario(
            solicitacao_id=s.id,
            autor_id=None,
            autor_nome_publico='Motorista (entrega)',
            texto=texto,
        )
        db.session.add(comentario)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Solicitações: erro na mensagem do motorista: %s", e)
        return jsonify({"erro": "Erro interno ao enviar a mensagem."}), 500

    cfg = _config()
    destinos = {s.solicitante_id}
    if cfg:
        destinos.update(cfg.alertados_ids or [])
    _notificar_ids(
        list(destinos),
        tipo='solicitacao_mensagem_motorista',
        titulo=f"🚚 Mensagem do motorista — solicitação #{s.id}",
        mensagem=f"\"{texto[:200]}\" — obra {s.obra.nome if s.obra else ''}.",
        solicitacao=s, origem_id=None,
    )
    return jsonify({"mensagem": "Mensagem enviada ao comprador."}), 201


@solicitacoes_bp.route('/entrega/<token>/pedido.pdf', methods=['GET'])
def publico_entrega_pdf(token):
    """Pedido em PDF pelo link de entrega — lista completa pra conferência
    na retirada (itens grandes ficam melhores no papel). Sem valores."""
    entrega = _entrega_por_token(token)
    if not entrega:
        return jsonify({"erro": "Link de entrega não encontrado."}), 404
    s = entrega.solicitacao
    try:
        resposta = send_file(
            io.BytesIO(gerar_pdf(s)),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'pedido_solicitacao_{s.id}.pdf',
        )
        resposta.headers['Cache-Control'] = 'private, no-store'
        return resposta
    except Exception as exc:
        logger.exception("Solicitações: erro no PDF do link de entrega #%s: %s", s.id, exc)
        return jsonify({"erro": "Não foi possível gerar o PDF do pedido."}), 500


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
