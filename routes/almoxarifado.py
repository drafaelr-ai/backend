"""Módulo Almoxarifado — catálogo, saldo e movimentações de estoque.

O almoxarifado é externo às obras: itens e histórico são centralizados, mas uma
saída pode ser vinculada à obra e/ou ao funcionário que recebeu o material.
"""
import logging
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, verify_jwt_in_request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from extensions import db
from models.almoxarifado_item import AlmoxarifadoItem
from models.almoxarifado_movimentacao import AlmoxarifadoMovimentacao
from models.funcionario import Funcionario
from models.obra import Obra
from services import get_current_user, user_has_access_to_obra, user_tem_modulo
from services.almoxarifado_service import (
    TIPOS_SAIDA_ESTOQUE,
    resumo_estoque,
    saldo_item as _saldo_item,
    saldos_itens as _saldos_itens,
)
from services.locacao_financeira_service import (
    criar_pagamentos_locacao,
    fornecedor_da_locacao,
    validar_item_orcamento_da_obra,
)

logger = logging.getLogger(__name__)

almoxarifado_bp = Blueprint('almoxarifado', __name__, url_prefix='/almoxarifado')

_CATEGORIAS = {'fardamento', 'epi', 'equipamento', 'ferramenta', 'material', 'outro'}
_TIPOS_MOVIMENTACAO = {
    'entrada', 'saida', 'ajuste',
    'locacao_entrada', 'locacao_saida', 'alocacao_obra', 'devolucao_obra',
}
_MODALIDADES = {'proprio', 'locacao'}
_PREFIXOS_CODIGO = {
    'fardamento': 'FD',
    'epi': 'EP',
    'ferramenta': 'FR',
    'equipamento': 'EQ',
    'material': 'MT',
    'outro': 'OT',
}


@almoxarifado_bp.before_request
def _gate_modulo_almoxarifado():
    if request.method == 'OPTIONS':
        return None
    verify_jwt_in_request()
    if not user_tem_modulo(get_current_user(), 'almoxarifado'):
        return jsonify({'erro': 'Acesso negado: você não tem permissão para o módulo Almoxarifado.'}), 403


def _to_num(valor):
    if valor is None or valor == '':
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace('R$', '').strip()
    if ',' in texto and '.' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif ',' in texto:
        texto = texto.replace(',', '.')
    try:
        return float(texto)
    except (TypeError, ValueError):
        return None


def _to_int(valor):
    if valor is None or valor == '':
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _parse_date(valor):
    if not valor:
        return date.today()
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)[:10]).date()
    except (TypeError, ValueError):
        return None


def _item_nao_encontrado(item_id):
    item = db.session.get(AlmoxarifadoItem, item_id)
    if not item:
        return None, (jsonify({'erro': 'Item não encontrado'}), 404)
    return item, None


def _codigo_automatico(item):
    """Código estável, único e legível, definido somente pelo servidor."""
    prefixo = _PREFIXOS_CODIGO[item.categoria]
    return f'{prefixo}-{item.id:05d}'


def _validar_item(dados, item=None):
    nome = (dados.get('nome') or (item.nome if item else '')).strip()
    if not nome:
        return None, (jsonify({'erro': 'nome é obrigatório'}), 400)

    categoria = (dados.get('categoria') or (item.categoria if item else 'outro')).strip().lower()
    if categoria not in _CATEGORIAS:
        return None, (jsonify({'erro': f'categoria inválida. Use: {sorted(_CATEGORIAS)}'}), 400)

    unidade = (dados.get('unidade') or (item.unidade if item else 'un')).strip().lower()[:20]
    if not unidade:
        return None, (jsonify({'erro': 'unidade é obrigatória'}), 400)

    estoque_minimo = _to_num(dados.get('estoque_minimo'))
    if estoque_minimo is None:
        estoque_minimo = float(item.estoque_minimo or 0) if item else 0
    if estoque_minimo < 0:
        return None, (jsonify({'erro': 'estoque_minimo não pode ser negativo'}), 400)

    modalidade = (dados.get('modalidade') or (item.modalidade if item else 'proprio')).strip().lower()
    if categoria != 'equipamento':
        modalidade = 'proprio'
    if modalidade not in _MODALIDADES:
        return None, (jsonify({'erro': 'modalidade inválida. Use proprio ou locacao'}), 400)

    valor_unitario = _to_num(dados.get('valor_unitario'))
    if valor_unitario is None:
        valor_unitario = float(item.valor_unitario or 0) if item else 0
    valor_locacao = _to_num(dados.get('valor_locacao_mensal'))
    if valor_locacao is None:
        valor_locacao = float(item.valor_locacao_mensal or 0) if item else 0
    if valor_unitario < 0 or valor_locacao < 0:
        return None, (jsonify({'erro': 'valores não podem ser negativos'}), 400)
    if modalidade == 'locacao' and valor_locacao <= 0:
        return None, (jsonify({'erro': 'Informe um valor mensal maior que zero para o equipamento locado'}), 400)
    if modalidade != 'locacao':
        valor_locacao = 0

    # Código é um identificador interno imutável. Ignorar valores enviados no
    # cliente impede colisões, adulteração de prefixo e quebra de rastreio.
    codigo = item.codigo if item else None
    tamanho = (str(dados.get('tamanho')).strip()[:30]
               if dados.get('tamanho') is not None else (item.tamanho if item else None))
    if categoria not in {'fardamento', 'epi'}:
        tamanho = None
    return {
        'nome': nome,
        'categoria': categoria,
        'unidade': unidade,
        'estoque_minimo': estoque_minimo,
        'modalidade': modalidade,
        'valor_unitario': valor_unitario,
        'valor_locacao_mensal': valor_locacao,
        'codigo': codigo,
        'tamanho': tamanho,
        'descricao': (str(dados.get('descricao')).strip()
                      if dados.get('descricao') is not None else (item.descricao if item else None)),
    }, None


@almoxarifado_bp.route('/itens', methods=['GET'])
@jwt_required()
def listar_itens():
    try:
        query = AlmoxarifadoItem.query
        if request.args.get('incluir_inativos') != 'true':
            query = query.filter(AlmoxarifadoItem.ativo.is_(True))
        categoria = (request.args.get('categoria') or '').strip().lower()
        if categoria:
            query = query.filter(AlmoxarifadoItem.categoria == categoria)
        busca = (request.args.get('busca') or '').strip()
        if busca:
            termo = f'%{busca}%'
            query = query.filter(
                or_(AlmoxarifadoItem.nome.ilike(termo), AlmoxarifadoItem.codigo.ilike(termo)),
            )
        itens = query.order_by(AlmoxarifadoItem.nome).all()
        saldos = _saldos_itens([i.id for i in itens])
        return jsonify([i.to_dict(saldos.get(i.id, 0)) for i in itens]), 200
    except Exception:
        logger.exception('Erro em GET /almoxarifado/itens')
        return jsonify({'erro': 'Erro ao listar itens'}), 500


@almoxarifado_bp.route('/itens', methods=['POST'])
@jwt_required()
def criar_item():
    try:
        valores, erro = _validar_item(request.get_json(silent=True) or {})
        if erro:
            return erro
        item = AlmoxarifadoItem(**valores)
        db.session.add(item)
        db.session.flush()
        item.codigo = _codigo_automatico(item)
        db.session.commit()
        return jsonify(item.to_dict(0)), 201
    except IntegrityError:
        db.session.rollback()
        return jsonify({'erro': 'Já existe um item com este código'}), 400
    except Exception:
        db.session.rollback()
        logger.exception('Erro em POST /almoxarifado/itens')
        return jsonify({'erro': 'Erro ao cadastrar item'}), 500


@almoxarifado_bp.route('/itens/<int:item_id>', methods=['PUT'])
@jwt_required()
def editar_item(item_id):
    try:
        item, erro = _item_nao_encontrado(item_id)
        if erro:
            return erro
        valores, erro = _validar_item(request.get_json(silent=True) or {}, item)
        if erro:
            return erro
        for campo, valor in valores.items():
            setattr(item, campo, valor)
        db.session.commit()
        return jsonify(item.to_dict(_saldo_item(item.id))), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({'erro': 'Já existe um item com este código'}), 400
    except Exception:
        db.session.rollback()
        logger.exception('Erro em PUT /almoxarifado/itens/<id>')
        return jsonify({'erro': 'Erro ao editar item'}), 500


@almoxarifado_bp.route('/itens/<int:item_id>', methods=['DELETE'])
@jwt_required()
def inativar_item(item_id):
    try:
        item, erro = _item_nao_encontrado(item_id)
        if erro:
            return erro
        item.ativo = False
        db.session.commit()
        return jsonify({'ok': True}), 200
    except Exception:
        db.session.rollback()
        logger.exception('Erro em DELETE /almoxarifado/itens/<id>')
        return jsonify({'erro': 'Erro ao inativar item'}), 500


@almoxarifado_bp.route('/movimentacoes', methods=['GET'])
@jwt_required()
def listar_movimentacoes():
    try:
        query = AlmoxarifadoMovimentacao.query.options(
            joinedload(AlmoxarifadoMovimentacao.item),
            joinedload(AlmoxarifadoMovimentacao.funcionario),
            joinedload(AlmoxarifadoMovimentacao.obra),
            joinedload(AlmoxarifadoMovimentacao.usuario),
        )
        item_id = _to_int(request.args.get('item_id'))
        if item_id:
            query = query.filter(AlmoxarifadoMovimentacao.item_id == item_id)
        data_inicio = _parse_date(request.args.get('data_inicio')) if request.args.get('data_inicio') else None
        data_fim = _parse_date(request.args.get('data_fim')) if request.args.get('data_fim') else None
        if data_inicio:
            query = query.filter(AlmoxarifadoMovimentacao.data_movimentacao >= data_inicio)
        if data_fim:
            query = query.filter(AlmoxarifadoMovimentacao.data_movimentacao <= data_fim)
        movs = query.order_by(
            AlmoxarifadoMovimentacao.data_movimentacao.desc(),
            AlmoxarifadoMovimentacao.id.desc(),
        ).limit(300).all()
        return jsonify([m.to_dict() for m in movs]), 200
    except Exception:
        logger.exception('Erro em GET /almoxarifado/movimentacoes')
        return jsonify({'erro': 'Erro ao listar movimentações'}), 500


@almoxarifado_bp.route('/movimentacoes', methods=['POST'])
@jwt_required()
def criar_movimentacao():
    try:
        dados = request.get_json(silent=True) or {}
        item_id = _to_int(dados.get('item_id'))
        item, erro = _item_nao_encontrado(item_id)
        if erro:
            return erro
        if not item.ativo:
            return jsonify({'erro': 'Não é possível movimentar um item inativo'}), 400

        tipo = (dados.get('tipo') or '').strip().lower()
        if tipo not in _TIPOS_MOVIMENTACAO:
            return jsonify({'erro': 'tipo de movimentação inválido'}), 400
        quantidade = _to_num(dados.get('quantidade'))
        if quantidade is None or quantidade == 0:
            return jsonify({'erro': 'quantidade deve ser diferente de zero'}), 400
        if tipo != 'ajuste' and quantidade < 0:
            return jsonify({'erro': 'Use quantidade positiva para esta movimentação'}), 400
        if tipo in TIPOS_SAIDA_ESTOQUE and quantidade > _saldo_item(item.id):
            return jsonify({'erro': 'Estoque insuficiente para registrar esta saída'}), 400

        data_movimentacao = _parse_date(dados.get('data_movimentacao'))
        if not data_movimentacao:
            return jsonify({'erro': 'data_movimentacao inválida'}), 400
        funcionario_id = _to_int(dados.get('funcionario_id'))
        obra_id = _to_int(dados.get('obra_id'))
        funcionario = db.session.get(Funcionario, funcionario_id) if funcionario_id else None
        if funcionario_id and not funcionario:
            return jsonify({'erro': 'Funcionário não encontrado'}), 400
        if obra_id and not db.session.get(Obra, obra_id):
            return jsonify({'erro': 'Obra não encontrada'}), 400

        # Fardamento só entra como reposição ou sai definitivamente para um
        # colaborador. Não existe fluxo de devolução para reinserir uniforme
        # já entregue no estoque.
        if item.categoria == 'fardamento':
            if tipo not in {'entrada', 'saida'}:
                return jsonify({'erro': 'Fardamento aceita apenas reposição ou entrega definitiva'}), 400
            if tipo == 'saida' and not funcionario_id:
                return jsonify({'erro': 'A entrega definitiva de fardamento exige o colaborador responsável'}), 400

        # Equipamento locado tem ciclo próprio: chega do fornecedor, pode ser
        # alocado/devolvido pela obra e só então retorna ao fornecedor.
        tipos_locacao = {'locacao_entrada', 'locacao_saida'}
        tipos_alocacao = {'alocacao_obra', 'devolucao_obra'}
        if tipo in tipos_locacao and (item.categoria != 'equipamento' or item.modalidade != 'locacao'):
            return jsonify({'erro': 'Movimentações de locação só podem ser usadas em equipamento locado'}), 400
        if tipo in tipos_alocacao and item.categoria != 'equipamento':
            return jsonify({'erro': 'Alocação e devolução são exclusivas de equipamentos'}), 400
        if tipo in tipos_alocacao and not obra_id:
            return jsonify({'erro': 'Informe a obra para alocar ou devolver o equipamento'}), 400

        fornecedor = (dados.get('fornecedor') or '').strip()[:160] or None
        if tipo == 'locacao_entrada' and not fornecedor:
            return jsonify({'erro': 'Informe o fornecedor na entrada de equipamento locado'}), 400

        # O catálogo é externo e centralizado, mas os destinos de uma saída
        # continuam sujeitos ao escopo de obras do usuário logado. Sem esta
        # checagem, seria possível enviar material para uma obra não liberada
        # apenas informando seu ID na requisição.
        usuario = get_current_user()
        if obra_id and not user_has_access_to_obra(usuario, obra_id):
            return jsonify({'erro': 'Você não tem acesso à obra informada'}), 403
        if funcionario and funcionario.obra_id and not user_has_access_to_obra(usuario, funcionario.obra_id):
            return jsonify({'erro': 'Você não tem acesso à obra do funcionário informado'}), 403
        if funcionario and not funcionario.obra_id and usuario.role not in {'master', 'administrador'}:
            return jsonify({'erro': 'Apenas administradores podem movimentar material para funcionário sem obra vinculada'}), 403

        # Alocar equipamento locado também gera o compromisso financeiro da
        # obra. Exigimos o mesmo perfil que pode baixar o financeiro e um item
        # de orçamento da própria obra, evitando despesa sem conciliação.
        dias_locacao = None
        data_vencimento = None
        orcamento_item_id = None
        criar_financeiro = tipo == 'alocacao_obra' and item.modalidade == 'locacao'
        if criar_financeiro:
            if usuario.role not in {'master', 'administrador'}:
                return jsonify({'erro': 'A alocacao de equipamento locado exige perfil administrador ou master'}), 403
            dias_locacao = _to_int(dados.get('dias_locacao'))
            if not dias_locacao or dias_locacao < 1:
                return jsonify({'erro': 'Informe a quantidade de dias da locacao'}), 400
            if not dados.get('data_vencimento'):
                return jsonify({'erro': 'Informe o primeiro vencimento da locacao'}), 400
            data_vencimento = _parse_date(dados.get('data_vencimento'))
            if not data_vencimento or data_vencimento < data_movimentacao:
                return jsonify({'erro': 'O vencimento deve ser igual ou posterior a data de alocacao'}), 400
            orcamento_item_id = _to_int(dados.get('orcamento_item_id'))
            if not orcamento_item_id:
                return jsonify({'erro': 'Selecione o item do orcamento que recebera a baixa da locacao'}), 400
            if not validar_item_orcamento_da_obra(obra_id, orcamento_item_id):
                return jsonify({'erro': 'O item do orcamento nao pertence a obra informada'}), 400
            # O fornecedor do compromisso financeiro é sempre o que entrou
            # no almoxarifado com este equipamento. Não aceitamos um nome
            # enviado pelo cliente nesta etapa porque isso quebraria a trilha
            # de auditoria entre entrada, alocação e financeiro.
            fornecedor = fornecedor_da_locacao(item.id)
            if not fornecedor:
                return jsonify({'erro': 'Nao ha fornecedor registrado na entrada deste equipamento locado'}), 400

        mov = AlmoxarifadoMovimentacao(
            item_id=item.id,
            tipo=tipo,
            quantidade=quantidade,
            data_movimentacao=data_movimentacao,
            funcionario_id=funcionario_id,
            obra_id=obra_id,
            usuario_id=usuario.id,
            fornecedor=fornecedor,
            dias_locacao=dias_locacao,
            data_vencimento=data_vencimento,
            orcamento_item_id=orcamento_item_id,
            observacao=(dados.get('observacao') or '').strip()[:300] or None,
        )
        db.session.add(mov)
        pagamentos_financeiros = []
        if criar_financeiro:
            db.session.flush()
            pagamentos_financeiros = criar_pagamentos_locacao(
                mov, item, dias_locacao, data_vencimento, orcamento_item_id,
            )
            mov.valor_financeiro = sum(float(pagamento.valor or 0) for pagamento in pagamentos_financeiros)
        db.session.commit()
        return jsonify({
            'movimentacao': mov.to_dict(),
            'estoque_atual': _saldo_item(item.id),
            'financeiro': [pagamento.to_dict() for pagamento in pagamentos_financeiros],
        }), 201
    except Exception:
        db.session.rollback()
        logger.exception('Erro em POST /almoxarifado/movimentacoes')
        return jsonify({'erro': 'Erro ao registrar movimentação'}), 500


@almoxarifado_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    try:
        itens = AlmoxarifadoItem.query.filter(AlmoxarifadoItem.ativo.is_(True)).order_by(AlmoxarifadoItem.nome).all()
        resumo = resumo_estoque(itens)
        saldos = resumo['saldos']
        abaixo_minimo = [
            i.to_dict(saldos.get(i.id, 0)) for i in itens
            if float(i.estoque_minimo or 0) > 0 and saldos.get(i.id, 0) <= float(i.estoque_minimo)
        ]
        recentes = (AlmoxarifadoMovimentacao.query.options(
            joinedload(AlmoxarifadoMovimentacao.item),
            joinedload(AlmoxarifadoMovimentacao.funcionario),
            joinedload(AlmoxarifadoMovimentacao.obra),
            joinedload(AlmoxarifadoMovimentacao.usuario),
        ).order_by(AlmoxarifadoMovimentacao.id.desc()).limit(8).all())
        return jsonify({
            'total_itens': len(itens),
            'itens_abaixo_minimo': len(abaixo_minimo),
            'abaixo_minimo': abaixo_minimo,
            'recentes': [m.to_dict() for m in recentes],
            'resumo': {
                chave: resumo[chave] for chave in (
                    'quantidade_estoque', 'itens_com_estoque', 'valor_estoque',
                    'equipamentos_estoque', 'valor_equipamentos',
                    'locacoes_ativas', 'valor_locacao_mensal',
                    'locacoes_financeiro_pendente', 'locacoes_financeiro_pago',
                    'locacoes_financeiro_total',
                )
            },
        }), 200
    except Exception:
        logger.exception('Erro em GET /almoxarifado/dashboard')
        return jsonify({'erro': 'Erro ao carregar painel do almoxarifado'}), 500


@almoxarifado_bp.route('/referencias', methods=['GET'])
@jwt_required()
def referencias():
    """Lista de destinos para entrega, sem exigir permissão no módulo RH."""
    try:
        usuario = get_current_user()
        if usuario.role in {'master', 'administrador'}:
            obras = Obra.query.order_by(Obra.nome).all()
            funcionarios = Funcionario.query.filter(Funcionario.status == 'ativo').order_by(Funcionario.nome).all()
        else:
            obra_ids = [obra.id for obra in usuario.obras_permitidas]
            obras = Obra.query.filter(Obra.id.in_(obra_ids)).order_by(Obra.nome).all() if obra_ids else []
            funcionarios = (Funcionario.query.filter(
                Funcionario.status == 'ativo', Funcionario.obra_id.in_(obra_ids),
            ).order_by(Funcionario.nome).all()) if obra_ids else []
        return jsonify({
            'obras': [{'id': obra.id, 'nome': obra.nome} for obra in obras],
            'funcionarios': [{'id': funcionario.id, 'nome': funcionario.nome} for funcionario in funcionarios],
        }), 200
    except Exception:
        logger.exception('Erro em GET /almoxarifado/referencias')
        return jsonify({'erro': 'Erro ao carregar referências do almoxarifado'}), 500
