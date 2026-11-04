import logging
from flask import Blueprint, request, jsonify, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity
from extensions import db
from models.notificacao import Notificacao

logger = logging.getLogger(__name__)

notificacoes_bp = Blueprint('notificacoes', __name__, url_prefix='/notificacoes')


@notificacoes_bp.route('', methods=['GET', 'OPTIONS'])
@jwt_required()
def listar_notificacoes():
    """Lista notificações do usuário logado"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        current_user_id = int(get_jwt_identity())
        apenas_nao_lidas = request.args.get('apenas_nao_lidas', 'false').lower() == 'true'
        limite = request.args.get('limite', 50, type=int)
        query = Notificacao.query.filter_by(usuario_destino_id=current_user_id)
        if apenas_nao_lidas:
            query = query.filter_by(lida=False)
        notificacoes = query.order_by(Notificacao.created_at.desc()).limit(limite).all()
        return jsonify([n.to_dict() for n in notificacoes]), 200
    except Exception as e:
        logger.exception(f"--- [ERRO] GET /notificacoes: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/count', methods=['GET', 'OPTIONS'])
@jwt_required()
def contar_notificacoes():
    """Retorna apenas o contador de notificações não lidas"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        current_user_id = int(get_jwt_identity())
        count = Notificacao.query.filter_by(
            usuario_destino_id=current_user_id,
            lida=False
        ).count()
        return jsonify({"count": count}), 200
    except Exception as e:
        logger.exception(f"--- [ERRO] GET /notificacoes/count: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/<int:notificacao_id>/lida', methods=['PATCH', 'OPTIONS'])
@jwt_required()
def marcar_notificacao_lida(notificacao_id):
    """Marca uma notificação como lida ou não lida"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        current_user_id = int(get_jwt_identity())
        notificacao = Notificacao.query.get_or_404(notificacao_id)
        if notificacao.usuario_destino_id != current_user_id:
            return jsonify({"erro": "Acesso negado"}), 403
        data = request.get_json() or {}
        notificacao.lida = data.get('lida', True)
        db.session.commit()
        return jsonify(notificacao.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] PATCH /notificacoes/{notificacao_id}/lida: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/marcar-todas-lidas', methods=['POST', 'OPTIONS'])
@jwt_required()
def marcar_todas_lidas():
    """Marca todas as notificações do usuário como lidas"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        current_user_id = int(get_jwt_identity())
        Notificacao.query.filter_by(
            usuario_destino_id=current_user_id,
            lida=False
        ).update({'lida': True})
        db.session.commit()
        return jsonify({"sucesso": "Todas as notificações foram marcadas como lidas"}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] POST /notificacoes/marcar-todas-lidas: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/limpar-lidas', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def limpar_notificacoes_lidas():
    """Remove todas as notificações lidas do usuário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        current_user_id = int(get_jwt_identity())
        deleted = Notificacao.query.filter_by(
            usuario_destino_id=current_user_id,
            lida=True
        ).delete()
        db.session.commit()
        return jsonify({"sucesso": f"{deleted} notificações removidas"}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] DELETE /notificacoes/limpar-lidas: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/limpar-todas', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def limpar_todas_notificacoes():
    """Remove TODAS as notificações do usuário (lidas e não lidas)"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        current_user_id = int(get_jwt_identity())
        deleted = Notificacao.query.filter_by(
            usuario_destino_id=current_user_id
        ).delete()
        db.session.commit()
        return jsonify({"sucesso": f"{deleted} notificações removidas"}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] DELETE /notificacoes/limpar-todas: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/<int:notificacao_id>', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def deletar_notificacao(notificacao_id):
    """Remove uma notificação específica"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        current_user_id = int(get_jwt_identity())
        notificacao = Notificacao.query.get_or_404(notificacao_id)
        if notificacao.usuario_destino_id != current_user_id:
            return jsonify({"erro": "Acesso negado"}), 403
        db.session.delete(notificacao)
        db.session.commit()
        return jsonify({"sucesso": "Notificação removida"}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"--- [ERRO] DELETE /notificacoes/{notificacao_id}: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


# ─── Aparelhos para push nativo ──────────────────────────────────────────────
# O APK já chamava estas duas rotas desde que o push nativo foi escrito no
# frontend (frontend/src/pwa/nativePush.js), mas elas nunca existiram aqui: o
# POST voltava 404 e o token do FCM se perdia, então nenhum aviso jamais saiu
# para a bandeja do aparelho.

@notificacoes_bp.route('/dispositivos', methods=['POST', 'OPTIONS'])
@jwt_required()
def registrar_dispositivo_push():
    """Vincula o token do FCM deste aparelho ao usuário logado."""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        dados = request.get_json(silent=True) or {}
        token = (dados.get('token') or '').strip()
        plataforma = (dados.get('plataforma') or '').strip().lower() or None

        # 400, nunca 422: o fetchWithAuth do front desloga em 401/422.
        if not token:
            return jsonify({"erro": "Informe o token do aparelho."}), 400
        if len(token) > 255:
            return jsonify({"erro": "Token do aparelho inválido."}), 400
        if plataforma and plataforma not in ('android', 'ios', 'web'):
            return jsonify({"erro": "Plataforma inválida."}), 400

        from services.push_service import registrar_dispositivo
        dispositivo = registrar_dispositivo(int(get_jwt_identity()), token, plataforma)
        return jsonify(dispositivo.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        logger.exception(f"--- [ERRO] POST /notificacoes/dispositivos: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/dispositivos', methods=['DELETE', 'OPTIONS'])
@jwt_required()
def remover_dispositivo_push():
    """Desfaz o vínculo deste aparelho no logout."""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        dados = request.get_json(silent=True) or {}
        token = (dados.get('token') or '').strip()
        if not token:
            return jsonify({"erro": "Informe o token do aparelho."}), 400

        from services.push_service import remover_dispositivo
        removidos = remover_dispositivo(int(get_jwt_identity()), token)
        return jsonify({"removidos": removidos}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception(f"--- [ERRO] DELETE /notificacoes/dispositivos: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500


@notificacoes_bp.route('/push/status', methods=['GET', 'OPTIONS'])
@jwt_required()
def status_push():
    """Diagnóstico: o push está configurado no servidor, e este usuário tem
    algum aparelho vinculado? Serve para responder "por que não chega" sem
    precisar abrir o banco."""
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS allowed"}), 200)
    try:
        from services.push_service import configurado
        from models.dispositivo_push import DispositivoPush
        uid = int(get_jwt_identity())
        aparelhos = DispositivoPush.query.filter_by(usuario_id=uid).all()
        return jsonify({
            "configurado": configurado(),
            "aparelhos": [a.to_dict() for a in aparelhos],
        }), 200
    except Exception as e:
        logger.exception(f"--- [ERRO] GET /notificacoes/push/status: {e} ---")
        return jsonify({"erro": "Erro interno no servidor"}), 500
