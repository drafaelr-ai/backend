import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from extensions import db, limiter
from models.obra import Obra
from models.orcamento_eng_etapa import OrcamentoEngEtapa
from models.orcamento_eng_item import OrcamentoEngItem
from models.planejamento_apontamento import PlanejamentoApontamento
from models.planejamento_atividade import PlanejamentoAtividade
from models.planejamento_fechamento import PlanejamentoFechamento
from models.planejamento_restricao import (
    STATUS_RESTRICAO,
    TIPOS_RESTRICAO,
    PlanejamentoRestricao,
)
from services.auth_service import (
    get_current_user,
    user_has_access_to_obra,
    user_tem_modulo,
)
from services.planejamento_service import (
    PlanejamentoValidationError,
    activity_overlaps_period,
    apply_activity_fields,
    automatic_status,
    clean_text,
    import_budget_items,
    normalize_activity_payload,
    normalize_enum,
    parse_decimal,
    parse_int,
    parse_iso_date,
    parse_spreadsheet,
    serialize_preview,
    start_of_week,
    summarize_activities,
    validate_cronograma_belongs_to_obra,
)


logger = logging.getLogger(__name__)
planejamento_bp = Blueprint('planejamento', __name__)
MAX_PLANEJAMENTO_REQUEST_BYTES = 3 * 1024 * 1024
PLANEJAMENTO_TIMEZONE = ZoneInfo('America/Fortaleza')


@planejamento_bp.before_request
def limit_planejamento_request_size():
    """Recusa corpos grandes antes do parser JSON/multipart alocar memória."""
    if (
        request.method in ('POST', 'PUT', 'PATCH')
        and request.content_length
        and request.content_length > MAX_PLANEJAMENTO_REQUEST_BYTES
    ):
        return _error('A requisição excede o limite de 3 MB.', 413)


def _error(message, status=400, field=None, details=None):
    body = {'erro': message}
    if field:
        body['campo'] = field
    if details:
        body['detalhes'] = details
    return jsonify(body), status


def _validation_error(exc):
    return _error(str(exc), 400, exc.field, exc.details)


def _user_for_obra(obra_id):
    user = get_current_user()
    if not user:
        return None, _error('Usuário não encontrado.', 401)
    if not user_tem_modulo(user, 'obras'):
        return None, _error('Módulo Obras não permitido.', 403)
    if not user_has_access_to_obra(user, obra_id):
        return None, _error('Acesso negado para esta obra.', 403)
    return user, None


def _accessible_obra_ids(user):
    if user.role in ('master', 'administrador'):
        return [obra_id for (obra_id,) in db.session.query(Obra.id).filter(
            Obra.arquivada.is_(False)
        ).all()]
    return [obra.id for obra in user.obras_permitidas if not obra.arquivada]


def _activity_for_user(activity_id):
    activity = (
        PlanejamentoAtividade.query
        .options(
            selectinload(PlanejamentoAtividade.apontamentos),
            selectinload(PlanejamentoAtividade.restricoes),
        )
        .filter_by(id=activity_id)
        .first()
    )
    if not activity:
        return None, None, _error('Atividade não encontrada.', 404)
    user, error = _user_for_obra(activity.obra_id)
    return activity, user, error


def _activity_query():
    return PlanejamentoAtividade.query.options(
        selectinload(PlanejamentoAtividade.apontamentos),
        selectinload(PlanejamentoAtividade.restricoes),
    )


@planejamento_bp.route('/planejamento/painel', methods=['GET'])
@jwt_required()
def get_planejamento_painel():
    user = get_current_user()
    if not user:
        return _error('Usuário não encontrado.', 401)
    if not user_tem_modulo(user, 'obras'):
        return _error('Módulo Obras não permitido.', 403)
    obra_ids = _accessible_obra_ids(user)
    if not obra_ids:
        return jsonify({'resumo': summarize_activities([]), 'obras': [], 'atividades': []})
    try:
        inicio = parse_iso_date(request.args.get('inicio'), 'inicio')
        fim = parse_iso_date(request.args.get('fim'), 'fim')
        if not inicio:
            inicio = start_of_week() - timedelta(days=7)
        if not fim:
            fim = inicio + timedelta(days=37)
        if fim < inicio or (fim - inicio).days > 366:
            raise PlanejamentoValidationError(
                'O período deve ter no máximo 366 dias e fim não pode anteceder início.',
                'fim',
            )
        activities = (
            _activity_query()
            .filter(
                PlanejamentoAtividade.obra_id.in_(obra_ids),
                activity_overlaps_period(inicio, fim),
            )
            .order_by(
                PlanejamentoAtividade.data_inicio.asc().nulls_last(),
                PlanejamentoAtividade.prioridade.desc(),
                PlanejamentoAtividade.id.asc(),
            )
            .limit(2000)
            .all()
        )
        obras = Obra.query.filter(Obra.id.in_(obra_ids)).order_by(Obra.nome).all()
        summaries = []
        for obra in obras:
            own = [activity for activity in activities if activity.obra_id == obra.id]
            summaries.append({**obra.to_dict(), 'planejamento': summarize_activities(own)})
        return jsonify({
            'periodo': {'inicio': inicio.isoformat(), 'fim': fim.isoformat()},
            'resumo': summarize_activities(activities),
            'obras': summaries,
            'atividades': [activity.to_dict() for activity in activities],
            'truncado': len(activities) >= 2000,
        })
    except PlanejamentoValidationError as exc:
        return _validation_error(exc)


@planejamento_bp.route('/obras/<int:obra_id>/planejamento/atividades', methods=['GET'])
@jwt_required()
def list_planejamento_atividades(obra_id):
    _, error = _user_for_obra(obra_id)
    if error:
        return error
    try:
        limit = parse_int(request.args.get('limit', 200), 'limit', 1, 200)
        offset = parse_int(request.args.get('offset', 0), 'offset', 0, 100000)
        query = _activity_query().filter(PlanejamentoAtividade.obra_id == obra_id)
        status = request.args.get('status')
        if status:
            query = query.filter(
                PlanejamentoAtividade.status == normalize_enum(
                    status,
                    'status',
                    ('a_planejar', 'pronto', 'em_andamento', 'impedido', 'concluido'),
                )
            )
        origem = request.args.get('origem')
        if origem:
            query = query.filter(
                PlanejamentoAtividade.origem == normalize_enum(
                    origem, 'origem', ('manual', 'orcamento', 'planilha')
                )
            )
        inicio = parse_iso_date(request.args.get('inicio'), 'inicio')
        fim = parse_iso_date(request.args.get('fim'), 'fim')
        if inicio and fim:
            if fim < inicio or (fim - inicio).days > 366:
                raise PlanejamentoValidationError('Período inválido.', 'fim')
            query = query.filter(activity_overlaps_period(inicio, fim))
        search = clean_text(request.args.get('busca'), 'busca', 100)
        if search:
            query = query.filter(PlanejamentoAtividade.titulo.ilike(f'%{search}%'))
        total = query.count()
        activities = (
            query.order_by(
                PlanejamentoAtividade.data_inicio.asc().nulls_last(),
                PlanejamentoAtividade.id.asc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        return jsonify({
            'itens': [activity.to_dict() for activity in activities],
            'total': total,
            'limit': limit,
            'offset': offset,
            'resumo': summarize_activities(activities),
        })
    except PlanejamentoValidationError as exc:
        return _validation_error(exc)


@planejamento_bp.route('/obras/<int:obra_id>/planejamento/atividades', methods=['POST'])
@jwt_required()
@limiter.limit('60 per minute')
def create_planejamento_atividade(obra_id):
    user, error = _user_for_obra(obra_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True)
        fields = normalize_activity_payload(data, origem='manual', partial=False)
        validate_cronograma_belongs_to_obra(fields.get('cronograma_id'), obra_id)
        activity = PlanejamentoAtividade(
            obra_id=obra_id,
            criado_por_user_id=user.id,
        )
        apply_activity_fields(activity, fields)
        db.session.add(activity)
        db.session.flush()
        activity.status = automatic_status(activity)
        db.session.commit()
        return jsonify(activity.to_dict()), 201
    except PlanejamentoValidationError as exc:
        db.session.rollback()
        return _validation_error(exc)
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao criar atividade de planejamento')
        return _error('Erro ao criar atividade.', 500)


@planejamento_bp.route('/planejamento/atividades/<int:activity_id>', methods=['GET'])
@jwt_required()
def get_planejamento_atividade(activity_id):
    activity, _, error = _activity_for_user(activity_id)
    if error:
        return error
    return jsonify(activity.to_dict())


@planejamento_bp.route('/planejamento/atividades/<int:activity_id>', methods=['PUT'])
@jwt_required()
@limiter.limit('120 per minute')
def update_planejamento_atividade(activity_id):
    activity, _, error = _activity_for_user(activity_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise PlanejamentoValidationError('O corpo deve ser um objeto JSON.')
        expected_version = data.get('versao')
        if expected_version is not None and int(expected_version) != activity.versao:
            return _error(
                'A atividade foi alterada por outra pessoa. Recarregue antes de salvar.',
                409,
                'versao',
            )
        fields = normalize_activity_payload(data, origem=activity.origem, partial=True)
        validate_cronograma_belongs_to_obra(fields.get('cronograma_id'), activity.obra_id)
        next_start = fields.get('data_inicio', activity.data_inicio)
        next_end = fields.get('data_fim', activity.data_fim)
        if next_start and next_end and next_end < next_start:
            raise PlanejamentoValidationError(
                'data_fim não pode ser anterior a data_inicio.', 'data_fim'
            )
        apply_activity_fields(activity, fields)
        activity.status = automatic_status(activity)
        db.session.commit()
        return jsonify(activity.to_dict())
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        if isinstance(exc, PlanejamentoValidationError):
            return _validation_error(exc)
        return _error('versao deve ser um inteiro.', 400, 'versao')
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao atualizar atividade de planejamento')
        return _error('Erro ao atualizar atividade.', 500)


@planejamento_bp.route('/planejamento/atividades/<int:activity_id>', methods=['DELETE'])
@jwt_required()
@limiter.limit('30 per minute')
def delete_planejamento_atividade(activity_id):
    activity, user, error = _activity_for_user(activity_id)
    if error:
        return error
    if user.role not in ('master', 'administrador'):
        return _error('Somente administradores podem excluir atividades.', 403)
    try:
        db.session.delete(activity)
        db.session.commit()
        return jsonify({'mensagem': 'Atividade excluída.', 'id': activity_id})
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao excluir atividade de planejamento')
        return _error('Erro ao excluir atividade.', 500)


@planejamento_bp.route(
    '/obras/<int:obra_id>/planejamento/orcamento-disponivel', methods=['GET']
)
@jwt_required()
def get_orcamento_disponivel(obra_id):
    _, error = _user_for_obra(obra_id)
    if error:
        return error
    imported_ids = {
        item_id for (item_id,) in db.session.query(PlanejamentoAtividade.orcamento_item_id)
        .filter(
            PlanejamentoAtividade.obra_id == obra_id,
            PlanejamentoAtividade.orcamento_item_id.isnot(None),
        ).all()
    }
    stages = (
        OrcamentoEngEtapa.query
        .options(selectinload(OrcamentoEngEtapa.itens))
        .filter_by(obra_id=obra_id)
        .order_by(OrcamentoEngEtapa.ordem, OrcamentoEngEtapa.id)
        .all()
    )
    result = []
    for stage in stages:
        items = [item.to_dict() for item in stage.itens if item.id not in imported_ids]
        if items:
            result.append({
                'id': stage.id,
                'codigo': stage.codigo,
                'nome': stage.nome,
                'ordem': stage.ordem,
                'itens': items,
            })
    return jsonify({'etapas': result, 'importados': sorted(imported_ids)})


@planejamento_bp.route(
    '/obras/<int:obra_id>/planejamento/importar-orcamento', methods=['POST']
)
@jwt_required()
@limiter.limit('20 per minute')
def post_importar_orcamento(obra_id):
    user, error = _user_for_obra(obra_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise PlanejamentoValidationError('O corpo deve ser um objeto JSON.')
        created, skipped = import_budget_items(
            obra_id=obra_id,
            item_ids=data.get('item_ids'),
            defaults=data.get('padroes') or {},
            complements=data.get('complementos') or {},
            user_id=user.id,
        )
        for activity in created:
            activity.status = automatic_status(activity)
        db.session.commit()
        return jsonify({
            'criados': [activity.to_dict() for activity in created],
            'ignorados': skipped,
        }), 201
    except PlanejamentoValidationError as exc:
        db.session.rollback()
        return _validation_error(exc)
    except IntegrityError:
        db.session.rollback()
        return _error('Um dos itens já foi importado por outra operação.', 409)
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao importar orçamento para planejamento')
        return _error('Erro ao importar itens do orçamento.', 500)


@planejamento_bp.route(
    '/obras/<int:obra_id>/planejamento/importar-planilha', methods=['POST']
)
@jwt_required()
@limiter.limit('10 per minute')
def post_importar_planilha(obra_id):
    user, error = _user_for_obra(obra_id)
    if error:
        return error
    try:
        payloads = parse_spreadsheet(request.files.get('arquivo'))
        confirmar = str(request.form.get('confirmar', '')).lower() in ('1', 'true', 'sim')
        if not confirmar:
            return jsonify({
                'total': len(payloads),
                'preview': serialize_preview(payloads),
                'valido': True,
            })
        activities = []
        for fields in payloads:
            validate_cronograma_belongs_to_obra(fields.get('cronograma_id'), obra_id)
            activity = PlanejamentoAtividade(
                obra_id=obra_id,
                origem='planilha',
                criado_por_user_id=user.id,
            )
            apply_activity_fields(activity, fields)
            db.session.add(activity)
            activities.append(activity)
        db.session.flush()
        for activity in activities:
            activity.status = automatic_status(activity)
        db.session.commit()
        return jsonify({
            'total': len(activities),
            'criados': [activity.to_dict() for activity in activities],
        }), 201
    except PlanejamentoValidationError as exc:
        db.session.rollback()
        return _validation_error(exc)
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao importar planilha de planejamento')
        return _error('Erro ao importar planilha.', 500)


@planejamento_bp.route(
    '/planejamento/atividades/<int:activity_id>/apontamentos', methods=['POST']
)
@jwt_required()
@limiter.limit('120 per minute')
def post_planejamento_apontamento(activity_id):
    activity, user, error = _activity_for_user(activity_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise PlanejamentoValidationError('O corpo deve ser um objeto JSON.')
        has_quantity = data.get('quantidade') not in (None, '')
        has_percentage = data.get('percentual') not in (None, '')
        if has_quantity == has_percentage:
            raise PlanejamentoValidationError(
                'Informe somente quantidade ou percentual.',
                'quantidade',
            )

        percentage = None
        note_type = 'quantidade'
        if has_percentage:
            percentage = parse_decimal(
                data.get('percentual'),
                'percentual',
                minimum=Decimal('0.001'),
                maximum=Decimal('100'),
            )
            current_percentage = Decimal(str(activity.percentual_conclusao))
            if percentage <= current_percentage:
                raise PlanejamentoValidationError(
                    'percentual deve ser maior que o avanço atual.',
                    'percentual',
                )

            planned = Decimal(str(activity.quantidade_planejada or 0))
            executed = Decimal(str(activity.quantidade_executada or 0))
            if planned <= 0:
                # Atividades sem medição física passam a usar uma base percentual.
                activity.quantidade_planejada = Decimal('100')
                activity.quantidade_executada = percentage
                activity.unidade = '%'
                quantity = percentage - current_percentage
            else:
                target_executed = (planned * percentage / Decimal('100')).quantize(
                    Decimal('0.001')
                )
                quantity = target_executed - executed
                if quantity <= 0:
                    raise PlanejamentoValidationError(
                        'percentual deve gerar avanço maior que o atual.',
                        'percentual',
                    )
                activity.quantidade_executada = target_executed
            note_type = 'percentual'
        else:
            quantity = parse_decimal(
                data.get('quantidade'),
                'quantidade',
                minimum=Decimal('0.001'),
            )
            if quantity <= 0:
                raise PlanejamentoValidationError(
                    'quantidade deve ser maior que zero.', 'quantidade'
                )
            activity.quantidade_executada = Decimal(
                str(activity.quantidade_executada or 0)
            ) + quantity

        # A data é definida no servidor para impedir retrodatação pelo navegador.
        note_date = datetime.now(PLANEJAMENTO_TIMEZONE).date()
        observation = clean_text(data.get('observacao'), 'observacao', 2000)
        note = PlanejamentoApontamento(
            atividade_id=activity.id,
            quantidade=quantity,
            tipo_apontamento=note_type,
            percentual=percentage,
            data_apontamento=note_date,
            observacao=observation,
            registrado_por_user_id=user.id,
        )
        db.session.add(note)
        activity.versao = (activity.versao or 0) + 1
        db.session.flush()
        activity.status = automatic_status(activity)
        db.session.commit()
        return jsonify({'atividade': activity.to_dict(), 'apontamento': note.to_dict()}), 201
    except PlanejamentoValidationError as exc:
        db.session.rollback()
        return _validation_error(exc)
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao registrar apontamento de planejamento')
        return _error('Erro ao registrar produção.', 500)


@planejamento_bp.route(
    '/planejamento/atividades/<int:activity_id>/restricoes', methods=['POST']
)
@jwt_required()
@limiter.limit('60 per minute')
def post_planejamento_restricao(activity_id):
    activity, user, error = _activity_for_user(activity_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise PlanejamentoValidationError('O corpo deve ser um objeto JSON.')
        restriction_type = normalize_enum(data.get('tipo'), 'tipo', TIPOS_RESTRICAO)
        if not restriction_type:
            raise PlanejamentoValidationError('tipo é obrigatório.', 'tipo')
        restriction = PlanejamentoRestricao(
            atividade_id=activity.id,
            tipo=restriction_type,
            descricao=clean_text(
                data.get('descricao'), 'descricao', 500, required=True
            ),
            responsavel=clean_text(data.get('responsavel'), 'responsavel', 160),
            data_limite=parse_iso_date(data.get('data_limite'), 'data_limite'),
            observacoes=clean_text(data.get('observacoes'), 'observacoes', 2000),
            criada_por_user_id=user.id,
        )
        db.session.add(restriction)
        activity.status = 'impedido'
        activity.versao = (activity.versao or 0) + 1
        db.session.commit()
        return jsonify({'atividade': activity.to_dict(), 'restricao': restriction.to_dict()}), 201
    except PlanejamentoValidationError as exc:
        db.session.rollback()
        return _validation_error(exc)
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao criar impedimento de planejamento')
        return _error('Erro ao criar impedimento.', 500)


@planejamento_bp.route('/planejamento/restricoes/<int:restriction_id>', methods=['PATCH'])
@jwt_required()
@limiter.limit('120 per minute')
def patch_planejamento_restricao(restriction_id):
    restriction = db.session.get(PlanejamentoRestricao, restriction_id)
    if not restriction:
        return _error('Impedimento não encontrado.', 404)
    activity, _, error = _activity_for_user(restriction.atividade_id)
    if error:
        return error
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise PlanejamentoValidationError('O corpo deve ser um objeto JSON.')
        if 'status' in data:
            restriction.status = normalize_enum(
                data.get('status'), 'status', STATUS_RESTRICAO
            )
            restriction.resolvida_em = (
                datetime.now(timezone.utc) if restriction.status == 'resolvida' else None
            )
        if 'responsavel' in data:
            restriction.responsavel = clean_text(
                data.get('responsavel'), 'responsavel', 160
            )
        if 'data_limite' in data:
            restriction.data_limite = parse_iso_date(
                data.get('data_limite'), 'data_limite'
            )
        if 'observacoes' in data:
            restriction.observacoes = clean_text(
                data.get('observacoes'), 'observacoes', 2000
            )
        db.session.flush()
        activity.status = automatic_status(activity)
        activity.versao = (activity.versao or 0) + 1
        db.session.commit()
        return jsonify({'atividade': activity.to_dict(), 'restricao': restriction.to_dict()})
    except PlanejamentoValidationError as exc:
        db.session.rollback()
        return _validation_error(exc)
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao atualizar impedimento de planejamento')
        return _error('Erro ao atualizar impedimento.', 500)


@planejamento_bp.route(
    '/obras/<int:obra_id>/planejamento/fechamentos', methods=['GET']
)
@jwt_required()
def get_planejamento_fechamentos(obra_id):
    _, error = _user_for_obra(obra_id)
    if error:
        return error
    query = PlanejamentoFechamento.query.filter_by(obra_id=obra_id)
    week_value = request.args.get('semana_inicio')
    try:
        if week_value:
            week = start_of_week(week_value)
            query = query.filter_by(semana_inicio=week)
        rows = query.order_by(PlanejamentoFechamento.semana_inicio.desc()).limit(52).all()
        return jsonify([row.to_dict() for row in rows])
    except PlanejamentoValidationError as exc:
        return _validation_error(exc)


@planejamento_bp.route(
    '/obras/<int:obra_id>/planejamento/fechamentos', methods=['POST']
)
@jwt_required()
@limiter.limit('20 per minute')
def post_planejamento_fechamento(obra_id):
    user, error = _user_for_obra(obra_id)
    if error:
        return error
    if user.role not in ('master', 'administrador'):
        return _error('Somente administradores podem fechar a semana.', 403)
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise PlanejamentoValidationError('O corpo deve ser um objeto JSON.')
        week_start = start_of_week(data.get('semana_inicio'))
        week_end = week_start + timedelta(days=6)
        activities = (
            _activity_query()
            .filter(
                PlanejamentoAtividade.obra_id == obra_id,
                PlanejamentoAtividade.data_inicio.isnot(None),
                PlanejamentoAtividade.data_fim.isnot(None),
                activity_overlaps_period(week_start, week_end),
            )
            .all()
        )
        planned = len(activities)
        completed = sum(1 for activity in activities if activity.status == 'concluido')
        ppc = Decimal(str(round(completed / planned * 100, 2) if planned else 0))
        reasons = data.get('motivos_nao_conclusao') or {}
        if not isinstance(reasons, dict) or len(reasons) > 10:
            raise PlanejamentoValidationError(
                'motivos_nao_conclusao deve ser um objeto com até 10 itens.',
                'motivos_nao_conclusao',
            )
        safe_reasons = {}
        for key, value in reasons.items():
            safe_key = clean_text(key, 'motivo', 100, required=True)
            safe_value = parse_int(value, f'motivo.{safe_key}', 0, 10000)
            safe_reasons[safe_key] = safe_value
        learning = clean_text(data.get('aprendizado'), 'aprendizado', 4000)
        closing = PlanejamentoFechamento.query.filter_by(
            obra_id=obra_id, semana_inicio=week_start
        ).first()
        if not closing:
            closing = PlanejamentoFechamento(
                obra_id=obra_id,
                semana_inicio=week_start,
                semana_fim=week_end,
            )
            db.session.add(closing)
        closing.planejadas = planned
        closing.concluidas = completed
        closing.ppc = ppc
        closing.motivos_nao_conclusao = safe_reasons
        closing.aprendizado = learning
        closing.fechado_por_user_id = user.id
        db.session.commit()
        return jsonify(closing.to_dict()), 201
    except PlanejamentoValidationError as exc:
        db.session.rollback()
        return _validation_error(exc)
    except Exception:
        db.session.rollback()
        logger.exception('Erro ao fechar semana de planejamento')
        return _error('Erro ao fechar semana.', 500)
