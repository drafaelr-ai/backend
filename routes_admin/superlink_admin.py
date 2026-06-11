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

    - grupo_id (imovel_id): re-query todos os boletos não pagos do imóvel
    - refs admin_lancamento: re-query cada um, filtra pagos/cancelados
    Itens pagos são removidos do resultado (não apenas marcados).
    """
    resultado = []

    # 1. Boletos do imóvel ao vivo (dinâmico — aparece/some conforme status)
    if grupo_id:
        imovel_nome = ''
        try:
            row = db.session.execute(
                db.text("SELECT nome FROM admin_imovel WHERE id = :id"),
                {'id': int(grupo_id)},
            ).fetchone()
            if row:
                imovel_nome = row[0] or ''
        except Exception:
            logger.warning("Falha ao buscar nome do imóvel: id=%s", grupo_id)

        try:
            rows = db.session.execute(
                db.text("""
                    SELECT descricao, beneficiario, valor, data_vencimento, codigo_barras
                    FROM admin_boleto
                    WHERE imovel_id = :iid
                      AND status != 'Pago'
                      AND codigo_barras IS NOT NULL
                      AND codigo_barras != ''
                    ORDER BY data_vencimento ASC NULLS LAST
                """),
                {'iid': int(grupo_id)},
            ).fetchall()
            for r in rows:
                resultado.append({
                    'descricao':       r[0] or r[1] or 'Boleto',
                    'valor':           float(r[2] or 0),
                    'contexto':        imovel_nome,
                    'forma':           'boleto',
                    'codigo_barras':   r[4],
                    'data_vencimento': r[3].isoformat() if r[3] else None,
                })
        except Exception:
            logger.exception("Re-query boletos admin falhou: imovel_id=%s", grupo_id)

    # 2. Lançamentos por refs — usa snapshot para preservar pix_chave, filtra pagos
    for idx, ref in enumerate(refs or []):
        if not ref:
            continue
        tabela = ref.get('tabela')
        rid = ref.get('id')
        if not tabela or not rid or tabela not in _TABELAS_PERMITIDAS_ADMIN:
            continue
        if tabela == 'admin_boleto':
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
            logger.warning("Lancamento ao vivo falhou: tabela=%s id=%s", tabela, rid)

    # 3. Fallback: sem grupo_id e resultado vazio → snapshot filtrado (legado)
    if not grupo_id and not resultado:
        resultado = [i for i in (itens_snapshot or []) if not i.get('pago')]

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
