import logging
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions_admin import db
from models_admin.superlink_admin import SuperlinkAdmin

logger = logging.getLogger(__name__)

superlink_admin_bp = Blueprint('superlink_admin', __name__)

# Whitelist explícita — tabelas do banco admin que podem ser consultadas ao vivo
_TABELAS_PERMITIDAS_ADMIN = {'admin_lancamento', 'admin_boleto'}


def _gerar_token():
    return secrets.token_urlsafe(24)


def _itens_dinamicos_admin(grupo_id, refs, itens_snapshot):
    """Resolve itens ao vivo do superlink admin.

    LISTA FIXA: apenas os itens SELECIONADOS na geração (itens_snapshot,
    alinhado posicionalmente com refs). grupo_id NÃO é usado para listar —
    a seleção é o que define o que aparece. Isso impede que a rota pública
    vaze boletos não selecionados do imóvel.

    STATUS AO VIVO: cada item é re-consultado pelo seu ref {tabela, id};
    se virou pago/cancelado após a geração, é removido (não apenas marcado).
    grupo_id É usado para validar que o registro referenciado pertence ao
    mesmo imóvel do superlink — refs para registros de outro imóvel são
    descartados como oráculo de status (mantém apenas o snapshot).
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
        if not tabela or not rid or tabela not in _TABELAS_PERMITIDAS_ADMIN:
            if not item.get('pago'):
                resultado.append(dict(item))
            continue

        try:
            row = db.session.execute(
                db.text(f"SELECT status, imovel_id FROM {tabela} WHERE id = :id"),
                {'id': int(rid)},
            ).fetchone()
            if not row:
                continue  # sumiu do banco → não exibe
            # ref aponta para um registro de outro imóvel → não confiável como
            # oráculo de status; descarta o ref e usa apenas o snapshot.
            if grupo_id is not None and row[1] != grupo_id:
                if not item.get('pago'):
                    resultado.append(dict(item))
                continue
            if str(row[0]).lower() in ('pago', 'cancelado'):
                continue  # pago/cancelado → remove do resultado
            resultado.append(dict(item))
        except Exception:
            logger.warning("Lancamento ao vivo falhou: tabela=%s id=%s", tabela, rid)
            # Erro de leitura: preserva o item selecionado (nunca vaza extra).
            if not item.get('pago'):
                resultado.append(dict(item))

    return resultado


@superlink_admin_bp.route('/admin/superlink', methods=['POST'])
@jwt_required()
def criar_superlink_admin():
    try:
        data = request.get_json() or {}

        titulo    = (data.get('titulo') or '').strip()
        itens     = data.get('itens', [])
        refs      = data.get('refs') or None
        imovel_id = data.get('imovel_id')

        if not titulo:
            return jsonify({'erro': 'titulo obrigatório'}), 400
        if not itens or not isinstance(itens, list):
            return jsonify({'erro': 'itens deve ser lista não vazia'}), 400

        for item in itens:
            descricao = (item.get('descricao') or '').strip()
            valor     = item.get('valor')
            forma     = (item.get('forma') or '').strip()
            if not descricao or valor is None or not forma:
                return jsonify({'erro': 'cada item precisa de descricao, valor e forma'}), 400
            if forma == 'pix' and not (item.get('pix_chave') or '').strip():
                return jsonify({'erro': f'item "{descricao}": forma=pix exige pix_chave'}), 400
            if forma == 'boleto' and not (item.get('codigo_barras') or '').strip():
                return jsonify({'erro': f'item "{descricao}": forma=boleto exige codigo_barras'}), 400

        valor_total = sum(float(i['valor']) for i in itens)

        for _ in range(5):
            token = _gerar_token()
            if not SuperlinkAdmin.query.filter_by(token=token).first():
                break

        agora = datetime.utcnow()
        sl = SuperlinkAdmin(
            token=token,
            grupo_id=int(imovel_id) if imovel_id else None,
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
        logger.exception("Erro em POST /admin/superlink")
        return jsonify({'erro': 'Erro ao criar superlink'}), 500


@superlink_admin_bp.route('/admin/superlink/<token>', methods=['GET'])
def obter_superlink_admin(token):
    try:
        sl = SuperlinkAdmin.query.filter_by(token=token).first()
        if not sl:
            return jsonify({'erro': 'Link não encontrado'}), 404
        if sl.is_expirado():
            return jsonify({'erro': 'Link expirado'}), 410

        itens = _itens_dinamicos_admin(sl.grupo_id, sl.refs, sl.itens)
        valor_total = sum(float(i.get('valor') or 0) for i in itens)

        return jsonify({
            'titulo':      sl.titulo,
            'itens':       itens,
            'valor_total': valor_total,
            'expira_em':   sl.expira_em.isoformat() + 'Z',
        }), 200

    except Exception:
        logger.exception("Erro em GET /admin/superlink/<token>")
        return jsonify({'erro': 'Erro ao buscar superlink'}), 500
