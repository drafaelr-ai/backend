import logging
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity

from extensions import db
from models.superlink import Superlink

logger = logging.getLogger(__name__)

superlink_bp = Blueprint('superlink', __name__, url_prefix='/superlink')

# Whitelist explícita — protege contra qualquer dado malicioso que chegue em refs
_TABELAS_PERMITIDAS = {'pagamento_futuro', 'boleto', 'parcela_individual'}


def _gerar_token():
    return secrets.token_urlsafe(24)


def _itens_dinamicos(grupo_id, refs, itens_snapshot):
    """Resolve itens ao vivo do superlink de obras.

    - grupo_id (obra_id): re-query todos os boletos não pagos da obra
    - refs pagamento_futuro / parcela_individual: re-query cada um, filtra pagos
    Itens pagos são removidos do resultado (não apenas marcados).
    """
    resultado = []

    # 1. Boletos da obra ao vivo (dinâmico — aparece/some conforme status)
    if grupo_id:
        obra_nome = ''
        try:
            row = db.session.execute(
                db.text("SELECT nome FROM obra WHERE id = :id"),
                {'id': int(grupo_id)},
            ).fetchone()
            if row:
                obra_nome = row[0] or ''
        except Exception:
            logger.warning("Falha ao buscar nome da obra: id=%s", grupo_id)

        try:
            rows = db.session.execute(
                db.text("""
                    SELECT descricao, beneficiario, valor, data_vencimento, codigo_barras
                    FROM boleto
                    WHERE obra_id = :oid
                      AND status != 'Pago'
                      AND codigo_barras IS NOT NULL
                      AND codigo_barras != ''
                    ORDER BY data_vencimento ASC NULLS LAST
                """),
                {'oid': int(grupo_id)},
            ).fetchall()
            for r in rows:
                resultado.append({
                    'descricao':       r[0] or r[1] or 'Boleto',
                    'valor':           float(r[2] or 0),
                    'contexto':        obra_nome,
                    'forma':           'boleto',
                    'codigo_barras':   r[4],
                    'data_vencimento': r[3].isoformat() if r[3] else None,
                })
        except Exception:
            logger.exception("Re-query boletos obras falhou: obra_id=%s", grupo_id)

    # 2. Lançamentos por refs — usa snapshot para preservar pix_chave, filtra pagos
    for idx, ref in enumerate(refs or []):
        if not ref:
            continue
        tabela = ref.get('tabela')
        rid = ref.get('id')
        if not tabela or not rid or tabela not in _TABELAS_PERMITIDAS:
            continue
        if tabela == 'boleto':
            continue  # boletos já tratados acima via grupo_id
        try:
            row = db.session.execute(
                db.text(f"SELECT status FROM {tabela} WHERE id = :id"),
                {'id': int(rid)},
            ).fetchone()
            if row and str(row[0]).lower() in ('pago', 'cancelado'):
                continue  # item pago → remove do resultado
            if idx < len(itens_snapshot or []):
                resultado.append(dict(itens_snapshot[idx]))
        except Exception:
            logger.warning("Live status falhou: tabela=%s id=%s", tabela, rid)

    # 3. Fallback: sem grupo_id e resultado vazio → snapshot filtrado (legado)
    if not grupo_id and not resultado:
        resultado = [i for i in (itens_snapshot or []) if not i.get('pago')]

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

        if not titulo:
            return jsonify({'erro': 'titulo obrigatório'}), 400
        if not itens or not isinstance(itens, list):
            return jsonify({'erro': 'itens deve ser lista não vazia'}), 400

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
            grupo_id=int(obra_id) if obra_id else None,
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
