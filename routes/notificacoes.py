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
