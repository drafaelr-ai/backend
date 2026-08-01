import logging
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from extensions import db
from models.superlink import Superlink
from services import get_current_user, user_has_access_to_obra

logger = logging.getLogger(__name__)

superlink_bp = Blueprint('superlink', __name__, url_prefix='/superlink')

# Whitelist explícita — protege contra qualquer dado malicioso que chegue em refs
_TABELAS_PERMITIDAS = {'pagamento_futuro', 'boleto', 'parcela_individual'}

# Consultas fixas por tipo de referência. Além de não interpolar nomes de
# tabelas, todas vinculam a cobrança à obra do Superlink antes de expô-la.
_STATUS_SQL_POR_TABELA = {
    'pagamento_futuro': """
        SELECT status FROM pagamento_futuro
        WHERE id = :id AND obra_id = :obra_id
    """,
    'boleto': """
        SELECT status FROM boleto
        WHERE id = :id AND obra_id = :obra_id
    """,
    'parcela_individual': """
        SELECT parcela.status
        FROM parcela_individual AS parcela
        JOIN pagamento_parcelado_v2 AS pagamento
          ON pagamento.id = parcela.pagamento_parcelado_id
        WHERE parcela.id = :id
          AND pagamento.obra_id = :obra_id
          AND LOWER(COALESCE(pagamento.status, '')) NOT IN ('cancelado', 'concluido', 'concluído')
    """,
}


def _gerar_token():
    return secrets.token_urlsafe(24)


def _status_referencia_na_obra(tabela, rid, obra_id):
    """Retorna o status da referência somente se ela pertence à obra."""
    if tabela not in _TABELAS_PERMITIDAS or obra_id is None:
        return None

    try:
        referencia_id = int(rid)
        if referencia_id <= 0:
            return None
        row = db.session.execute(
            db.text(_STATUS_SQL_POR_TABELA[tabela]),
            {'id': referencia_id, 'obra_id': int(obra_id)},
        ).fetchone()
        return row[0] if row else None
    except (TypeError, ValueError):
        return None


def _itens_dinamicos(grupo_id, refs, itens_snapshot):
    """Resolve itens ao vivo do superlink de obras.

    LISTA FIXA: apenas os itens SELECIONADOS na geração (itens_snapshot,
    alinhado posicionalmente com refs). grupo_id NÃO é usado para listar —
    a seleção é o que define o que aparece. Isso impede que a rota pública
    vaze boletos não selecionados da obra.

    STATUS AO VIVO: cada item é re-consultado pelo seu ref {tabela, id};
    se virou pago/cancelado após a geração, é removido (não apenas marcado).
    """
    itens_snapshot = itens_snapshot or []
    refs = refs or []

    # Legado: link sem refs → não há como checar status ao vivo; devolve o
    # snapshot filtrado (que já contém SÓ os selecionados).
    if not refs:
        return [dict(i) for i in itens_snapshot if not i.get('pago')]

    resultado = []
    for idx, item in enumerate(itens_snapshot):
        ref = refs[idx] if idx < len(refs) else None

        # Item sem ref de banco (ex: pix avulso) → mantém; sem status ao vivo.
        if not ref:
            if not item.get('pago'):
                resultado.append(dict(item))
            continue

        tabela = ref.get('tabela')
        rid = ref.get('id')

        # ref inválido / fora da whitelist → a seleção manda; mantém snapshot.
        if not tabela or not rid or tabela not in _TABELAS_PERMITIDAS:
            if not item.get('pago'):
                resultado.append(dict(item))
            continue

        try:
            status = _status_referencia_na_obra(tabela, rid, grupo_id)
            if status is None:
                continue  # sumiu, foi desvinculado ou não pertence à obra
            if str(status).lower() in ('pago', 'cancelado'):
                continue  # pago/cancelado → remove do resultado
            resultado.append(dict(item))
        except Exception:
            logger.warning("Live status falhou: tabela=%s id=%s", tabela, rid)
            # Erro de leitura: preserva o item selecionado (nunca vaza extra).
            if not item.get('pago'):
                resultado.append(dict(item))

    return resultado


@superlink_bp.route('', methods=['POST'])
@jwt_required()
def criar_superlink():
    try:
        data = request.get_json() or {}

        titulo   = (data.get('titulo') or '').strip()
        itens    = data.get('itens', [])
        refs     = data.get('refs') or None
        obra_id  = data.get('obra_id')

        obra_id_int = None
        if obra_id not in (None, ''):
            try:
                obra_id_int = int(obra_id)
                if obra_id_int <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify({'erro': 'obra_id inválido'}), 400

            current_user = get_current_user()
            if not current_user or not user_has_access_to_obra(current_user, obra_id_int):
                return jsonify({'erro': 'Acesso negado a esta obra'}), 403

        if not titulo:
            return jsonify({'erro': 'titulo obrigatório'}), 400
        if not itens or not isinstance(itens, list):
            return jsonify({'erro': 'itens deve ser lista não vazia'}), 400

        if refs:
            if not isinstance(refs, list) or len(refs) != len(itens):
                return jsonify({'erro': 'refs deve corresponder aos itens do link'}), 400
            if obra_id_int is None:
                return jsonify({'erro': 'obra_id é obrigatório quando houver referências'}), 400

            for ref in refs:
                if ref is None:
                    continue
                if not isinstance(ref, dict):
                    return jsonify({'erro': 'referência inválida'}), 400

                tabela = ref.get('tabela')
                status = _status_referencia_na_obra(tabela, ref.get('id'), obra_id_int)
                if status is None:
                    return jsonify({'erro': 'referência não encontrada nesta obra'}), 400
                if str(status).lower() in ('pago', 'cancelado'):
                    return jsonify({'erro': 'não é possível gerar link para cobrança encerrada'}), 400

        for item in itens:
            descricao = (item.get('descricao') or '').strip()
            valor = item.get('valor')
            forma = (item.get('forma') or '').strip()
            if not descricao or valor is None or not forma:
                return jsonify({'erro': 'cada item precisa de descricao, valor e forma'}), 400
            if forma == 'pix' and not (item.get('pix_chave') or '').strip():
                return jsonify({'erro': f'item "{descricao}": forma=pix exige pix_chave'}), 400
            if forma == 'boleto' and not (item.get('codigo_barras') or '').strip():
                return jsonify({'erro': f'item "{descricao}": forma=boleto exige codigo_barras'}), 400

        valor_total = sum(float(i['valor']) for i in itens)

        for _ in range(5):
            token = _gerar_token()
            if not Superlink.query.filter_by(token=token).first():
                break

        agora = datetime.utcnow()
        sl = Superlink(
            token=token,
            grupo_id=obra_id_int,
            titulo=titulo,
            itens=itens,
            refs=refs,
            valor_total=valor_total,
            criado_em=agora,
            expira_em=agora + timedelta(days=5),
        )
        db.session.add(sl)
        db.session.commit()

        return jsonify({'token': token, 'url': f'https://obraly.uk/pagar/{token}'}), 201

    except Exception:
        logger.exception("Erro em POST /superlink")
        return jsonify({'erro': 'Erro ao criar superlink'}), 500


@superlink_bp.route('/<token>', methods=['GET'])
def obter_superlink(token):
    try:
        sl = Superlink.query.filter_by(token=token).first()
        if not sl:
            return jsonify({'erro': 'Link não encontrado'}), 404
        if sl.is_expirado():
            return jsonify({'erro': 'Link expirado'}), 410

        itens = _itens_dinamicos(sl.grupo_id, sl.refs, sl.itens)
        valor_total = sum(float(i.get('valor') or 0) for i in itens)

        return jsonify({
            'titulo':      sl.titulo,
            'itens':       itens,
            'valor_total': valor_total,
            'expira_em':   sl.expira_em.isoformat() + 'Z',
        }), 200

    except Exception:
        logger.exception("Erro em GET /superlink/<token>")
        return jsonify({'erro': 'Erro ao buscar superlink'}), 500
