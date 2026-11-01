import logging
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from extensions_admin import db
from models_admin import Imovel, AdminBoleto
from services_admin import get_current_user, extrair_dados_boleto_pdf_admin
from services_admin import storage_admin_service

# Boleto bancario e 1-2 paginas.
_MAX_PDF_BYTES = 10 * 1024 * 1024

logger = logging.getLogger(__name__)

boletos_admin_bp = Blueprint('boletos_admin', __name__)


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos', methods=['GET'])
@jwt_required()
def listar_boletos_admin(imovel_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    imovel = Imovel.query.get_or_404(imovel_id)
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403

    try:
        status_filtro = request.args.get('status')
        query = AdminBoleto.query.filter_by(imovel_id=imovel_id)
        if status_filtro:
            query = query.filter_by(status=status_filtro)
        boletos = query.order_by(AdminBoleto.data_vencimento.asc()).all()

        hoje = date.today()
        for b in boletos:
            if b.status == 'Pendente' and b.data_vencimento < hoje:
                b.status = 'Vencido'
        db.session.commit()

        return jsonify([b.to_dict() for b in boletos])
    except Exception as e:
        logger.exception("Erro ao listar boletos")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos', methods=['POST'])
@jwt_required()
def criar_boleto_admin(imovel_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    imovel = Imovel.query.get_or_404(imovel_id)
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403

    try:
        data = request.get_json(silent=True) or {}
        if not data.get('descricao') or not data.get('valor') or not data.get('data_vencimento'):
            return jsonify({'erro': 'Descrição, valor e data de vencimento são obrigatórios'}), 400

        codigo_barras = data.get('codigo_barras')
        if codigo_barras:
            existente = AdminBoleto.query.filter_by(
                imovel_id=imovel_id, codigo_barras=codigo_barras
            ).first()
            if existente:
                return jsonify({'erro': 'Boleto com este código de barras já existe', 'duplicado': True}), 409

        boleto = AdminBoleto(
            imovel_id=imovel_id,
            usuario_id=user.id,
            codigo_barras=codigo_barras,
            descricao=data.get('descricao'),
            beneficiario=data.get('beneficiario'),
            valor=float(data.get('valor')),
            data_vencimento=datetime.strptime(data.get('data_vencimento'), '%Y-%m-%d').date(),
            orcamento_item_id=data.get('orcamento_item_id') or None,
            arquivo_nome=data.get('arquivo_nome'),
        )

        # B-11: o PDF vai para o Storage e no banco fica so o path. Enquanto o
        # bucket e as credenciais do admin nao estiverem configurados,
        # `disponivel()` e False e o comportamento segue identico ao de antes
        # (base64 na coluna) — e por isso que esta mudanca pode esperar o
        # deploy manual do admin sem mudar nada em producao.
        conteudo = data.get('arquivo_pdf') or data.get('arquivo_base64')
        if conteudo:
            if storage_admin_service.disponivel():
                try:
                    boleto.arquivo_path = storage_admin_service.upload_base64(
                        conteudo,
                        'boletos',
                        nome=data.get('arquivo_nome') or 'boleto.pdf',
                        bucket=storage_admin_service.BUCKET_BOLETOS_ADMIN,
                        max_bytes=_MAX_PDF_BYTES,
                    )
                except ValueError as exc:
                    return jsonify({'erro': str(exc)}), 400
                except Exception:
                    logger.exception('Falha ao subir o PDF do boleto admin')
                    return jsonify({
                        'erro': 'Nao foi possivel guardar o PDF do boleto. Tente de novo.',
                    }), 502
            else:
                boleto.arquivo_pdf = conteudo

        db.session.add(boleto)
        db.session.commit()
        logger.info(f"Boleto criado: {boleto.id} no imóvel {imovel_id}")
        return jsonify(boleto.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao criar boleto")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos/extrair-pdf', methods=['POST'])
@jwt_required()
def extrair_pdf_boleto_admin(imovel_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    try:
        data = request.get_json(silent=True) or {}
        pdf_base64 = data.get('arquivo_base64', '')
        if ',' in pdf_base64:
            pdf_base64 = pdf_base64.split(',')[1]
        if not pdf_base64:
            return jsonify({'erro': 'Arquivo PDF não enviado'}), 400
        resultado = extrair_dados_boleto_pdf_admin(pdf_base64)
        return jsonify(resultado)
    except Exception as e:
        logger.exception("Erro ao extrair PDF de boleto")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos/<int:boleto_id>', methods=['PUT'])
@jwt_required()
def editar_boleto_admin(imovel_id, boleto_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    boleto = AdminBoleto.query.get_or_404(boleto_id)
    if user.role != 'admin' and boleto.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        data = request.get_json(silent=True) or {}
        for campo in ['descricao', 'beneficiario', 'codigo_barras', 'status']:
            if campo in data:
                setattr(boleto, campo, data[campo])
        if 'valor' in data:
            boleto.valor = float(data['valor'])
        if 'data_vencimento' in data:
            boleto.data_vencimento = datetime.strptime(data['data_vencimento'], '%Y-%m-%d').date()
        if 'orcamento_item_id' in data:
            boleto.orcamento_item_id = data['orcamento_item_id'] or None
        db.session.commit()
        return jsonify(boleto.to_dict())
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao editar boleto")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos/<int:boleto_id>/pagar', methods=['POST'])
@jwt_required()
def pagar_boleto_admin(imovel_id, boleto_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    boleto = AdminBoleto.query.get_or_404(boleto_id)
    if user.role != 'admin' and boleto.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        data = request.get_json(silent=True) or {}
        boleto.status = 'Pago'
        boleto.data_pagamento = datetime.strptime(
            data.get('data_pagamento', date.today().isoformat()), '%Y-%m-%d'
        ).date()
        db.session.commit()
        return jsonify(boleto.to_dict())
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao pagar boleto")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos/<int:boleto_id>', methods=['DELETE'])
@jwt_required()
def deletar_boleto_admin(imovel_id, boleto_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    boleto = AdminBoleto.query.get_or_404(boleto_id)
    if user.role != 'admin' and boleto.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        db.session.delete(boleto)
        db.session.commit()
        return jsonify({'message': 'Boleto removido'})
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro ao deletar boleto")
        return jsonify({'erro': "Erro interno no servidor"}), 500


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos/<int:boleto_id>/arquivo', methods=['GET'])
@jwt_required()
def obter_arquivo_boleto_admin(imovel_id, boleto_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    boleto = AdminBoleto.query.get_or_404(boleto_id)
    if user.role != 'admin' and boleto.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    # Caminho novo: arquivo no Storage, devolvido como URL assinada de 1h.
    # `arquivo_base64` segue na resposta pelos registros antigos; o cliente
    # aceita os dois, entao a migracao do banco pode vir depois.
    if boleto.arquivo_path:
        try:
            url = storage_admin_service.signed_url(
                boleto.arquivo_path,
                expires=3600,
                bucket=storage_admin_service.BUCKET_BOLETOS_ADMIN,
            )
        except Exception:
            logger.exception('Falha ao assinar a URL do PDF do boleto admin')
            return jsonify({'erro': 'Nao foi possivel abrir o PDF agora.'}), 502
        return jsonify({'arquivo_nome': boleto.arquivo_nome, 'arquivo_url': url})

    if not boleto.arquivo_pdf:
        return jsonify({'erro': 'Boleto não possui arquivo'}), 404
    return jsonify({'arquivo_nome': boleto.arquivo_nome, 'arquivo_base64': boleto.arquivo_pdf})


@boletos_admin_bp.route('/imoveis/<int:imovel_id>/boletos/resumo', methods=['GET'])
@jwt_required()
def resumo_boletos_admin(imovel_id):
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    imovel = Imovel.query.get_or_404(imovel_id)
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    try:
        hoje = date.today()
        boletos = AdminBoleto.query.filter_by(imovel_id=imovel_id).all()
        pendentes = [b for b in boletos if b.status in ('Pendente', 'Vencido')]
        vencidos = [b for b in boletos if b.status == 'Pendente' and b.data_vencimento < hoje]
        pagos = [b for b in boletos if b.status == 'Pago']
        return jsonify({
            'total_pendente': sum(b.valor or 0 for b in pendentes),
            'quantidade_pendente': len(pendentes),
            'total_vencido': sum(b.valor or 0 for b in vencidos),
            'quantidade_vencido': len(vencidos),
            'total_pago': sum(b.valor or 0 for b in pagos),
            'quantidade_pago': len(pagos),
        })
    except Exception as e:
        logger.exception("Erro ao obter resumo de boletos")
        return jsonify({'erro': "Erro interno no servidor"}), 500
