import logging
from datetime import date, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import extract

from extensions_admin import db
from models_admin import Imovel, Lancamento
from services_admin import get_current_user

logger = logging.getLogger(__name__)

lancamentos_admin_bp = Blueprint('lancamentos_admin', __name__)


@lancamentos_admin_bp.route('/lancamentos', methods=['GET'])
@jwt_required()
def listar_lancamentos():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Nao autorizado'}), 401

    imovel_id = request.args.get('imovel_id', type=int)
    tipo = request.args.get('tipo')
    status = request.args.get('status')
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)

    query = Lancamento.query.join(Imovel)
    if user.role != 'admin':
        query = query.filter(Imovel.usuario_id == user.id)
    if imovel_id:
        query = query.filter(Lancamento.imovel_id == imovel_id)
    if tipo:
        query = query.filter(Lancamento.tipo == tipo)
    if status:
        query = query.filter(Lancamento.status == status)
    if mes:
        query = query.filter(extract('month', Lancamento.data_lancamento) == mes)
    if ano:
        query = query.filter(extract('year', Lancamento.data_lancamento) == ano)

    lancamentos = query.order_by(Lancamento.data_lancamento.desc()).all()
    return jsonify([l.to_dict() for l in lancamentos])


@lancamentos_admin_bp.route('/lancamentos', methods=['POST'])
@jwt_required()
def criar_lancamento():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Nao autorizado'}), 401
    try:
        dados = request.get_json(silent=True)

        imovel = Imovel.query.get(dados.get('imovel_id'))
        if not imovel:
            return jsonify({'erro': 'Imovel nao encontrado'}), 404
        if user.role != 'admin' and imovel.usuario_id != user.id:
            return jsonify({'erro': 'Acesso negado ao imovel'}), 403

        data_lanc = date.fromisoformat(dados.get('data_lancamento', date.today().isoformat()))
        data_venc = date.fromisoformat(dados['data_vencimento']) if dados.get('data_vencimento') else None
        recorrente = dados.get('recorrente', False)
        recorrencia_meses = int(dados.get('recorrencia_meses', 1))
        qtd_parcelas = int(dados.get('qtd_parcelas', 1)) if recorrente else 1

        lancamentos_criados = []

        for i in range(qtd_parcelas):
            if i > 0:
                mes_offset = i * recorrencia_meses
                ano_offset = mes_offset // 12
                mes_novo = data_lanc.month + (mes_offset % 12)
                if mes_novo > 12:
                    mes_novo -= 12
                    ano_offset += 1
                try:
                    data_lanc_parcela = data_lanc.replace(
                        year=data_lanc.year + ano_offset, month=mes_novo
                    )
                except ValueError:
                    import calendar
                    ultimo_dia = calendar.monthrange(data_lanc.year + ano_offset, mes_novo)[1]
                    data_lanc_parcela = data_lanc.replace(
                        year=data_lanc.year + ano_offset,
                        month=mes_novo,
                        day=min(data_lanc.day, ultimo_dia)
                    )

                if data_venc:
                    try:
                        data_venc_parcela = data_venc.replace(
                            year=data_venc.year + ano_offset, month=mes_novo
                        )
                    except ValueError:
                        import calendar
                        ultimo_dia = calendar.monthrange(data_venc.year + ano_offset, mes_novo)[1]
                        data_venc_parcela = data_venc.replace(
                            year=data_venc.year + ano_offset,
                            month=mes_novo,
                            day=min(data_venc.day, ultimo_dia)
                        )
                else:
                    data_venc_parcela = None
            else:
                data_lanc_parcela = data_lanc
                data_venc_parcela = data_venc

            descricao = dados.get('descricao')
            if recorrente and qtd_parcelas > 1:
                descricao = f"{descricao} ({i+1}/{qtd_parcelas})"

            lancamento = Lancamento(
                imovel_id=dados.get('imovel_id'),
                categoria_id=dados.get('categoria_id'),
                descricao=descricao,
                valor=float(dados.get('valor', 0)),
                tipo=dados.get('tipo', 'despesa'),
                data_lancamento=data_lanc_parcela,
                data_vencimento=data_venc_parcela,
                data_pagamento=date.fromisoformat(dados['data_pagamento']) if dados.get('data_pagamento') and i == 0 else None,
                status=dados.get('status', 'pendente') if i == 0 else 'pendente',
                recorrente=recorrente,
                recorrencia_meses=recorrencia_meses,
                observacoes=dados.get('observacoes'),
                pix_chave=dados.get('pix_chave'),
                codigo_barras=dados.get('codigo_barras')
            )
            db.session.add(lancamento)
            lancamentos_criados.append(lancamento)

        db.session.commit()
        logger.info(f"{len(lancamentos_criados)} lancamento(s) criado(s): {dados.get('descricao')}")

        if len(lancamentos_criados) == 1:
            return jsonify(lancamentos_criados[0].to_dict()), 201
        return jsonify({
            'message': f'{len(lancamentos_criados)} lancamentos criados',
            'lancamentos': [l.to_dict() for l in lancamentos_criados]
        }), 201

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao criar lancamento")
        return jsonify({'erro': str(e)}), 500


@lancamentos_admin_bp.route('/alertas-vencimento', methods=['GET'])
@jwt_required()
def alertas_vencimento():
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Nao autorizado'}), 401
    try:
        dias_alerta = request.args.get('dias', type=int, default=7)
        hoje = date.today()
        data_limite = hoje + timedelta(days=dias_alerta)

        if user.role == 'admin':
            imoveis_ids = [i.id for i in Imovel.query.filter_by(ativo=True).all()]
        else:
            imoveis_ids = [i.id for i in Imovel.query.filter_by(usuario_id=user.id, ativo=True).all()]

        lancamentos = Lancamento.query.filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.status == 'pendente',
            Lancamento.data_vencimento.isnot(None),
            Lancamento.data_vencimento <= data_limite
        ).order_by(Lancamento.data_vencimento.asc()).all()

        vencidos = []
        a_vencer = []

        for lanc in lancamentos:
            lanc_dict = lanc.to_dict()
            dias_para_vencer = (lanc.data_vencimento - hoje).days
            lanc_dict['dias_para_vencer'] = dias_para_vencer

            if dias_para_vencer < 0:
                lanc_dict['status_alerta'] = 'vencido'
                lanc_dict['dias_vencido'] = abs(dias_para_vencer)
                vencidos.append(lanc_dict)
            else:
                lanc_dict['status_alerta'] = 'a_vencer'
                a_vencer.append(lanc_dict)

        return jsonify({
            'vencidos': vencidos,
            'a_vencer': a_vencer,
            'resumo': {
                'qtd_vencidos': len(vencidos),
                'qtd_a_vencer': len(a_vencer),
                'total_vencido': sum(l['valor'] for l in vencidos),
                'total_a_vencer': sum(l['valor'] for l in a_vencer),
                'total_geral': sum(l['valor'] for l in vencidos) + sum(l['valor'] for l in a_vencer)
            }
        })

    except Exception as e:
        logger.exception("Erro ao buscar alertas")
        return jsonify({'erro': str(e)}), 500


@lancamentos_admin_bp.route('/lancamentos/<int:lancamento_id>', methods=['PUT'])
@jwt_required()
def atualizar_lancamento(lancamento_id):
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        dados = request.get_json(silent=True)

        lancamento.categoria_id = dados.get('categoria_id', lancamento.categoria_id)
        lancamento.descricao = dados.get('descricao', lancamento.descricao)
        lancamento.valor = float(dados.get('valor', lancamento.valor))
        lancamento.tipo = dados.get('tipo', lancamento.tipo)
        lancamento.status = dados.get('status', lancamento.status)
        lancamento.observacoes = dados.get('observacoes', lancamento.observacoes)
        lancamento.pix_chave = dados.get('pix_chave', lancamento.pix_chave)
        lancamento.codigo_barras = dados.get('codigo_barras', lancamento.codigo_barras)

        if dados.get('data_lancamento'):
            lancamento.data_lancamento = date.fromisoformat(dados['data_lancamento'])
        if dados.get('data_vencimento'):
            lancamento.data_vencimento = date.fromisoformat(dados['data_vencimento'])
        if dados.get('data_pagamento'):
            lancamento.data_pagamento = date.fromisoformat(dados['data_pagamento'])

        if 'comprovante_base64' in dados:
            comprovante = dados.get('comprovante_base64')
            if comprovante is None:
                lancamento.comprovante_url = None
            elif len(comprovante) > 7000000:
                return jsonify({'erro': 'Arquivo muito grande. Maximo 5MB.'}), 400
            else:
                lancamento.comprovante_url = comprovante
        elif 'comprovante_url' in dados:
            lancamento.comprovante_url = dados.get('comprovante_url')

        db.session.commit()
        logger.info(f"Lancamento {lancamento_id} atualizado")
        return jsonify(lancamento.to_dict())

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao atualizar lancamento")
        return jsonify({'erro': str(e)}), 500


@lancamentos_admin_bp.route('/lancamentos/<int:lancamento_id>', methods=['DELETE'])
@jwt_required()
def deletar_lancamento(lancamento_id):
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        db.session.delete(lancamento)
        db.session.commit()
        return jsonify({'message': 'Lancamento removido com sucesso'})

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao deletar lancamento")
        return jsonify({'erro': str(e)}), 500


@lancamentos_admin_bp.route('/lancamentos/<int:lancamento_id>/pagar', methods=['POST'])
@jwt_required()
def marcar_pago(lancamento_id):
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        dados = request.get_json(silent=True) or {}
        lancamento.status = 'pago'
        lancamento.data_pagamento = date.fromisoformat(
            dados.get('data_pagamento', date.today().isoformat())
        )
        if dados.get('comprovante_url'):
            lancamento.comprovante_url = dados.get('comprovante_url')
        db.session.commit()
        return jsonify(lancamento.to_dict())

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao marcar pago")
        return jsonify({'erro': str(e)}), 500


@lancamentos_admin_bp.route('/lancamentos/<int:lancamento_id>/comprovante', methods=['POST'])
@jwt_required()
def upload_comprovante(lancamento_id):
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        dados = request.get_json(silent=True)
        if not dados.get('comprovante_base64'):
            return jsonify({'erro': 'Comprovante nao enviado'}), 400

        comprovante_base64 = dados.get('comprovante_base64')
        if len(comprovante_base64) > 7000000:
            return jsonify({'erro': 'Arquivo muito grande. Maximo 5MB.'}), 400

        lancamento.comprovante_url = comprovante_base64
        db.session.commit()
        logger.info(f"Comprovante salvo para lancamento {lancamento_id}")
        return jsonify({'message': 'Comprovante salvo com sucesso', 'lancamento': lancamento.to_dict()})

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao salvar comprovante")
        return jsonify({'erro': str(e)}), 500


@lancamentos_admin_bp.route('/lancamentos/<int:lancamento_id>/comprovante', methods=['DELETE'])
@jwt_required()
def remover_comprovante(lancamento_id):
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        lancamento.comprovante_url = None
        db.session.commit()
        return jsonify({'message': 'Comprovante removido com sucesso'})

    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao remover comprovante")
        return jsonify({'erro': str(e)}), 500
