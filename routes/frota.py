"""Blueprint do módulo Frota — veículos, movimentações (alocação obra/imóvel),
condutores, documentos, manutenções, abastecimentos e multas. Todas as rotas
exigem JWT.

Alocação: denormalizada em frota_veiculo (local_tipo/obra_id/imovel_id/imovel_nome);
toda mudança passa por POST /veiculos/<id>/movimentacoes, que grava o histórico e
atualiza o veículo na MESMA transação. Imóvel é referência fraca ao banco admin
(snapshot de nome); a lista vem de /frota/imoveis-admin.

Visibilidade: master/administrador veem tudo. Usuário comum vê veículos sem obra
(em imóvel do patrimônio ou sem local) e veículos de suas obras permitidas.
Erros de validação são SEMPRE 400 — nunca 422 (fetchWithAuth desloga em 401/422).
"""
import re
import logging
from calendar import monthrange
from datetime import datetime, date, timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from models.frota_condutor import FrotaCondutor
from models.frota_veiculo import FrotaVeiculo
from models.frota_movimentacao import FrotaMovimentacao
from models.frota_documento import FrotaDocumento
from models.frota_manutencao import FrotaManutencao
from models.frota_abastecimento import FrotaAbastecimento
from models.frota_multa import FrotaMulta
from models.funcionario import Funcionario
from models.obra import Obra
from services import storage_service, admin_read_service
from services import get_current_user, user_has_access_to_obra

logger = logging.getLogger(__name__)

frota_bp = Blueprint('frota', __name__, url_prefix='/frota')

BUCKET_FROTA = 'frota-arquivos'

_VEICULO_TIPOS = {'carro', 'caminhonete', 'caminhao', 'moto', 'maquina', 'outro'}
_VEICULO_STATUS = {'ativo', 'em_manutencao', 'vendido', 'inativo'}
_DOC_TIPOS = {'crlv', 'seguro', 'ipva', 'licenciamento', 'outro'}
_MANUT_TIPOS = {'preventiva', 'corretiva'}
_MULTA_STATUS = {'pendente', 'paga', 'contestada'}
_DESTINO_TIPOS = {'obra', 'imovel', 'sem_local'}


# ---------------------------------------------------------------- helpers

def _parse_date(valor):
    """Aceita 'YYYY-MM-DD' (ou ISO com hora) → date; None se vazio/ inválido."""
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)[:10]).date()
    except Exception:
        return None


def _to_num(valor):
    """Converte número ou string BR ('2.640,00') em float. None se vazio."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip().replace('R$', '').strip()
    if ',' in s and '.' in s:          # 2.640,00 → 2640.00
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:                     # 2640,00 → 2640.00
        s = s.replace(',', '.')
    try:
        return float(s)
    except Exception:
        return None


def _to_int(valor):
    if valor is None or valor == '':
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _obra_ids_permitidos(user):
    """None = sem restrição (master/administrador, vê tudo). Lista = só essas
    obras (usuário comum); lista vazia = nenhuma obra liberada."""
    if user and user.role in ('master', 'administrador'):
        return None
    return [o.id for o in user.obras_permitidas] if user else []


def _filtro_visibilidade(query, coluna_obra_id, user):
    """Frota é gestão centralizada: usuário comum vê registros sem obra (imóvel
    do patrimônio ou sem local) e os de suas obras permitidas."""
    obra_ids = _obra_ids_permitidos(user)
    if obra_ids is None:
        return query
    return query.filter(or_(coluna_obra_id.is_(None), coluna_obra_id.in_(obra_ids)))


def _veiculo_visivel(veiculo, user):
    obra_ids = _obra_ids_permitidos(user)
    return obra_ids is None or veiculo.obra_id is None or veiculo.obra_id in obra_ids


def _normalizar_placa(placa):
    """Uppercase, só letras/dígitos (aceita formato antigo e Mercosul)."""
    return re.sub(r'[^A-Z0-9]', '', str(placa or '').upper())


def _placa_em_uso(placa, ignorar_id=None):
    """Checagem de unicidade em nível de aplicação (o índice único no banco
    fica como backstop de corrida — IntegrityError também vira 400)."""
    query = FrotaVeiculo.query.filter(db.func.upper(FrotaVeiculo.placa) == placa)
    if ignorar_id:
        query = query.filter(FrotaVeiculo.id != ignorar_id)
    return query.first() is not None


def _dados_e_arquivo():
    """Suporta JSON e multipart/form-data (campo 'arquivo' opcional)."""
    if request.files:
        return request.form, (request.files.get('arquivo') or request.files.get('file'))
    if request.content_type and 'multipart/form-data' in request.content_type:
        return request.form, None
    return (request.get_json(silent=True) or {}), None


def _upload_best_effort(arquivo, pasta):
    """Upload que nunca bloqueia o save: retorna (path|None, falhou: bool)."""
    if not arquivo:
        return None, False
    try:
        return storage_service.upload_arquivo(arquivo, pasta, bucket=BUCKET_FROTA), False
    except Exception as e:
        logger.exception("Frota: upload falhou (segue sem arquivo): %s", e)
        return None, True


def _resolver_destino(dados, user):
    """Valida o destino de alocação (obra | imovel | sem_local).

    Retorna (destino: dict, erro: (response, status) | None). `destino` traz
    local_tipo/obra_id/imovel_id/nome já prontos para gravar."""
    destino_tipo = (dados.get('destino_tipo') or dados.get('local_tipo') or '').strip()
    if destino_tipo not in _DESTINO_TIPOS:
        return None, (jsonify({"erro": f"destino_tipo inválido (use {sorted(_DESTINO_TIPOS)})"}), 400)

    if destino_tipo == 'obra':
        obra_id = _to_int(dados.get('obra_id'))
        if not obra_id:
            return None, (jsonify({"erro": "obra_id é obrigatório para destino obra"}), 400)
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return None, (jsonify({"erro": "Obra não encontrada"}), 400)
        if not user_has_access_to_obra(user, obra_id):
            return None, (jsonify({"erro": "Acesso negado a esta obra"}), 403)
        return {'local_tipo': 'obra', 'obra_id': obra_id, 'imovel_id': None,
                'imovel_nome': None, 'nome': obra.nome}, None

    if destino_tipo == 'imovel':
        imovel_id = _to_int(dados.get('imovel_id'))
        imovel_nome = (dados.get('imovel_nome') or '').strip() or None
        if not imovel_id:
            return None, (jsonify({"erro": "imovel_id é obrigatório para destino imovel"}), 400)
        return {'local_tipo': 'imovel', 'obra_id': None, 'imovel_id': imovel_id,
                'imovel_nome': imovel_nome, 'nome': imovel_nome}, None

    return {'local_tipo': None, 'obra_id': None, 'imovel_id': None,
            'imovel_nome': None, 'nome': None}, None


def _aplicar_destino(veiculo, destino):
    veiculo.local_tipo = destino['local_tipo']
    veiculo.obra_id = destino['obra_id']
    veiculo.imovel_id = destino['imovel_id']
    veiculo.imovel_nome = destino['imovel_nome']


def _snapshot_local(veiculo):
    """Snapshot do local atual do veículo p/ custos (manutenção/abastecimento)."""
    return {
        'local_tipo': veiculo.local_tipo,
        'obra_id': veiculo.obra_id,
        'imovel_id': veiculo.imovel_id,
        'local_nome': veiculo.local_nome(),
    }


def _competencia_range(competencia):
    """'YYYY-MM' → (primeiro_dia, ultimo_dia). Levanta ValueError se inválida."""
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    return date(ano, mes, 1), date(ano, mes, monthrange(ano, mes)[1])


# ---------------------------------------------------------------- veículos

@frota_bp.route('/veiculos', methods=['GET'])
@jwt_required()
def listar_veiculos():
    try:
        user = get_current_user()
        query = _filtro_visibilidade(FrotaVeiculo.query, FrotaVeiculo.obra_id, user)

        status = request.args.get('status')
        if status:
            query = query.filter(FrotaVeiculo.status == status)
        tipo = request.args.get('tipo')
        if tipo:
            query = query.filter(FrotaVeiculo.tipo == tipo)
        local_tipo = request.args.get('local_tipo')
        if local_tipo == 'sem_local':
            query = query.filter(FrotaVeiculo.local_tipo.is_(None))
        elif local_tipo:
            query = query.filter(FrotaVeiculo.local_tipo == local_tipo)
        obra_id = request.args.get('obra_id')
        if obra_id:
            query = query.filter(FrotaVeiculo.obra_id == _to_int(obra_id))
        q = (request.args.get('q') or '').strip()
        if q:
            like = f'%{q}%'
            query = query.filter(or_(
                FrotaVeiculo.placa.ilike(f'%{_normalizar_placa(q)}%'),
                FrotaVeiculo.modelo.ilike(like),
                FrotaVeiculo.marca.ilike(like),
            ))

        veiculos = query.order_by(FrotaVeiculo.placa).all()
        return jsonify([v.to_dict() for v in veiculos]), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/veiculos")
        return jsonify({"erro": "Erro ao listar veículos", "detalhe": str(e)}), 500


@frota_bp.route('/veiculos', methods=['POST'])
@jwt_required()
def criar_veiculo():
    try:
        user = get_current_user()
        dados, _ = _dados_e_arquivo()

        placa = _normalizar_placa(dados.get('placa'))
        modelo = (dados.get('modelo') or '').strip()
        if not placa or not modelo:
            return jsonify({"erro": "placa e modelo são obrigatórios"}), 400
        if _placa_em_uso(placa):
            return jsonify({"erro": "Placa já cadastrada"}), 400
        tipo = (dados.get('tipo') or 'carro').strip()
        if tipo not in _VEICULO_TIPOS:
            return jsonify({"erro": f"tipo inválido (use {sorted(_VEICULO_TIPOS)})"}), 400

        veiculo = FrotaVeiculo(
            placa=placa,
            renavam=(dados.get('renavam') or '').strip() or None,
            chassi=(dados.get('chassi') or '').strip() or None,
            marca=(dados.get('marca') or '').strip() or None,
            modelo=modelo,
            ano_fabricacao=_to_int(dados.get('ano_fabricacao')),
            ano_modelo=_to_int(dados.get('ano_modelo')),
            tipo=tipo,
            cor=(dados.get('cor') or '').strip() or None,
            combustivel=(dados.get('combustivel') or '').strip() or None,
            km_atual=_to_int(dados.get('km_atual')),
            observacao=(dados.get('observacao') or '').strip() or None,
        )

        # Local inicial opcional — grava também a 1ª movimentação.
        movimentacao = None
        if dados.get('destino_tipo') or dados.get('local_tipo'):
            destino, erro = _resolver_destino(dados, user)
            if erro:
                return erro
            _aplicar_destino(veiculo, destino)
            if destino['local_tipo']:
                movimentacao = FrotaMovimentacao(
                    destino_tipo=destino['local_tipo'],
                    obra_id=destino['obra_id'],
                    imovel_id=destino['imovel_id'],
                    destino_nome=destino['nome'],
                    data_movimentacao=_parse_date(dados.get('data_movimentacao')) or date.today(),
                    observacao='Alocação inicial',
                )

        db.session.add(veiculo)
        db.session.flush()
        if movimentacao:
            movimentacao.veiculo_id = veiculo.id
            db.session.add(movimentacao)
        db.session.commit()
        return jsonify(veiculo.to_dict()), 201
    except IntegrityError:
        db.session.rollback()
        return jsonify({"erro": "Placa já cadastrada"}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /frota/veiculos")
        return jsonify({"erro": "Erro ao criar veículo", "detalhe": str(e)}), 500


@frota_bp.route('/veiculos/<int:veiculo_id>', methods=['GET'])
@jwt_required()
def obter_veiculo(veiculo_id):
    try:
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        return jsonify(veiculo.to_dict()), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/veiculos/<id>")
        return jsonify({"erro": "Erro ao obter veículo", "detalhe": str(e)}), 500


@frota_bp.route('/veiculos/<int:veiculo_id>', methods=['PUT'])
@jwt_required()
def editar_veiculo(veiculo_id):
    """Edita dados cadastrais. NÃO altera alocação (use /movimentacoes)."""
    try:
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados, _ = _dados_e_arquivo()
        if 'placa' in dados:
            placa = _normalizar_placa(dados.get('placa'))
            if not placa:
                return jsonify({"erro": "placa não pode ficar vazia"}), 400
            if _placa_em_uso(placa, ignorar_id=veiculo.id):
                return jsonify({"erro": "Placa já cadastrada"}), 400
            veiculo.placa = placa
        if 'modelo' in dados:
            modelo = (dados.get('modelo') or '').strip()
            if not modelo:
                return jsonify({"erro": "modelo não pode ficar vazio"}), 400
            veiculo.modelo = modelo
        if 'tipo' in dados:
            tipo = (dados.get('tipo') or '').strip()
            if tipo not in _VEICULO_TIPOS:
                return jsonify({"erro": f"tipo inválido (use {sorted(_VEICULO_TIPOS)})"}), 400
            veiculo.tipo = tipo
        if 'status' in dados:
            status = (dados.get('status') or '').strip()
            if status not in _VEICULO_STATUS:
                return jsonify({"erro": f"status inválido (use {sorted(_VEICULO_STATUS)})"}), 400
            veiculo.status = status
        for campo in ('renavam', 'chassi', 'marca', 'cor', 'combustivel', 'observacao'):
            if campo in dados:
                setattr(veiculo, campo, (dados.get(campo) or '').strip() or None)
        for campo in ('ano_fabricacao', 'ano_modelo', 'km_atual'):
            if campo in dados:
                setattr(veiculo, campo, _to_int(dados.get(campo)))

        db.session.commit()
        return jsonify(veiculo.to_dict()), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({"erro": "Placa já cadastrada"}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em PUT /frota/veiculos/<id>")
        return jsonify({"erro": "Erro ao editar veículo", "detalhe": str(e)}), 500


@frota_bp.route('/veiculos/<int:veiculo_id>', methods=['DELETE'])
@jwt_required()
def remover_veiculo(veiculo_id):
    """Soft delete (status='inativo') — preserva histórico de custos."""
    try:
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        veiculo.status = 'inativo'
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em DELETE /frota/veiculos/<id>")
        return jsonify({"erro": "Erro ao remover veículo", "detalhe": str(e)}), 500


@frota_bp.route('/veiculos/<int:veiculo_id>/condutor', methods=['PATCH'])
@jwt_required()
def atribuir_condutor(veiculo_id):
    try:
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados = request.get_json(silent=True) or {}
        condutor_id = _to_int(dados.get('condutor_id'))
        if dados.get('condutor_id') and not condutor_id:
            return jsonify({"erro": "condutor_id inválido"}), 400
        if condutor_id:
            condutor = db.session.get(FrotaCondutor, condutor_id)
            if not condutor:
                return jsonify({"erro": "Condutor não encontrado"}), 400
            if condutor.status != 'ativo':
                return jsonify({"erro": "Condutor está inativo"}), 400
        veiculo.condutor_atual_id = condutor_id
        db.session.commit()
        return jsonify(veiculo.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em PATCH /frota/veiculos/<id>/condutor")
        return jsonify({"erro": "Erro ao atribuir condutor", "detalhe": str(e)}), 500


# ---------------------------------------------------------------- movimentações

@frota_bp.route('/veiculos/<int:veiculo_id>/movimentacoes', methods=['GET'])
@jwt_required()
def listar_movimentacoes(veiculo_id):
    try:
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        movs = (FrotaMovimentacao.query
                .filter_by(veiculo_id=veiculo_id)
                .order_by(FrotaMovimentacao.data_movimentacao.desc(),
                          FrotaMovimentacao.id.desc())
                .all())
        return jsonify([m.to_dict() for m in movs]), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/veiculos/<id>/movimentacoes")
        return jsonify({"erro": "Erro ao listar movimentações", "detalhe": str(e)}), 500


@frota_bp.route('/veiculos/<int:veiculo_id>/movimentacoes', methods=['POST'])
@jwt_required()
def criar_movimentacao(veiculo_id):
    """Move o veículo: grava histórico + atualiza denormalizado, 1 transação."""
    try:
        user = get_current_user()
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, user):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados = request.get_json(silent=True) or {}
        destino, erro = _resolver_destino(dados, user)
        if erro:
            return erro

        mov = FrotaMovimentacao(
            veiculo_id=veiculo.id,
            destino_tipo=(dados.get('destino_tipo') or '').strip(),
            obra_id=destino['obra_id'],
            imovel_id=destino['imovel_id'],
            destino_nome=destino['nome'],
            data_movimentacao=_parse_date(dados.get('data_movimentacao')) or date.today(),
            observacao=(dados.get('observacao') or '').strip() or None,
        )
        _aplicar_destino(veiculo, destino)
        db.session.add(mov)
        db.session.commit()
        return jsonify({"movimentacao": mov.to_dict(), "veiculo": veiculo.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /frota/veiculos/<id>/movimentacoes")
        return jsonify({"erro": "Erro ao movimentar veículo", "detalhe": str(e)}), 500


# ---------------------------------------------------------------- condutores

@frota_bp.route('/condutores', methods=['GET'])
@jwt_required()
def listar_condutores():
    try:
        query = FrotaCondutor.query
        status = request.args.get('status')
        if status:
            query = query.filter(FrotaCondutor.status == status)
        condutores = query.order_by(FrotaCondutor.nome).all()

        # Veículo atual de cada condutor (para a tela de condutores).
        veiculos = dict(
            db.session.query(FrotaVeiculo.condutor_atual_id, FrotaVeiculo.placa)
            .filter(FrotaVeiculo.condutor_atual_id.isnot(None),
                    FrotaVeiculo.status != 'inativo')
            .all()
        )
        out = []
        for c in condutores:
            d = c.to_dict()
            d['veiculo_atual_placa'] = veiculos.get(c.id)
            out.append(d)
        return jsonify(out), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/condutores")
        return jsonify({"erro": "Erro ao listar condutores", "detalhe": str(e)}), 500


@frota_bp.route('/condutores', methods=['POST'])
@jwt_required()
def criar_condutor():
    try:
        dados = request.get_json(silent=True) or {}
        nome = (dados.get('nome') or '').strip()
        if not nome:
            return jsonify({"erro": "nome é obrigatório"}), 400
        funcionario_id = _to_int(dados.get('funcionario_id'))
        if funcionario_id and not db.session.get(Funcionario, funcionario_id):
            return jsonify({"erro": "Funcionário não encontrado"}), 400

        condutor = FrotaCondutor(
            nome=nome,
            cpf=(dados.get('cpf') or '').strip() or None,
            telefone=(dados.get('telefone') or '').strip() or None,
            cnh_numero=(dados.get('cnh_numero') or '').strip() or None,
            cnh_categoria=(dados.get('cnh_categoria') or '').strip().upper() or None,
            cnh_validade=_parse_date(dados.get('cnh_validade')),
            funcionario_id=funcionario_id,
            observacao=(dados.get('observacao') or '').strip() or None,
        )
        db.session.add(condutor)
        db.session.commit()
        return jsonify(condutor.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /frota/condutores")
        return jsonify({"erro": "Erro ao criar condutor", "detalhe": str(e)}), 500


@frota_bp.route('/condutores/<int:condutor_id>', methods=['PUT'])
@jwt_required()
def editar_condutor(condutor_id):
    try:
        condutor = db.session.get(FrotaCondutor, condutor_id)
        if not condutor:
            return jsonify({"erro": "Condutor não encontrado"}), 404

        dados = request.get_json(silent=True) or {}
        if 'nome' in dados:
            nome = (dados.get('nome') or '').strip()
            if not nome:
                return jsonify({"erro": "nome não pode ficar vazio"}), 400
            condutor.nome = nome
        if 'funcionario_id' in dados:
            funcionario_id = _to_int(dados.get('funcionario_id'))
            if funcionario_id and not db.session.get(Funcionario, funcionario_id):
                return jsonify({"erro": "Funcionário não encontrado"}), 400
            condutor.funcionario_id = funcionario_id
        if 'status' in dados:
            status = (dados.get('status') or '').strip()
            if status not in ('ativo', 'inativo'):
                return jsonify({"erro": "status inválido (use ativo|inativo)"}), 400
            condutor.status = status
        for campo in ('cpf', 'telefone', 'cnh_numero', 'observacao'):
            if campo in dados:
                setattr(condutor, campo, (dados.get(campo) or '').strip() or None)
        if 'cnh_categoria' in dados:
            condutor.cnh_categoria = (dados.get('cnh_categoria') or '').strip().upper() or None
        if 'cnh_validade' in dados:
            condutor.cnh_validade = _parse_date(dados.get('cnh_validade'))

        db.session.commit()
        return jsonify(condutor.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em PUT /frota/condutores/<id>")
        return jsonify({"erro": "Erro ao editar condutor", "detalhe": str(e)}), 500


@frota_bp.route('/condutores/<int:condutor_id>', methods=['DELETE'])
@jwt_required()
def remover_condutor(condutor_id):
    """Soft delete. Bloqueia se for condutor atual de veículo não-inativo."""
    try:
        condutor = db.session.get(FrotaCondutor, condutor_id)
        if not condutor:
            return jsonify({"erro": "Condutor não encontrado"}), 404
        em_uso = (FrotaVeiculo.query
                  .filter(FrotaVeiculo.condutor_atual_id == condutor_id,
                          FrotaVeiculo.status != 'inativo')
                  .first())
        if em_uso:
            return jsonify({"erro": f"Condutor é o atual do veículo {em_uso.placa} — "
                                    "remova a atribuição antes de inativar"}), 400
        condutor.status = 'inativo'
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em DELETE /frota/condutores/<id>")
        return jsonify({"erro": "Erro ao remover condutor", "detalhe": str(e)}), 500


# ---------------------------------------------------------------- documentos

@frota_bp.route('/veiculos/<int:veiculo_id>/documentos', methods=['GET'])
@jwt_required()
def listar_documentos(veiculo_id):
    try:
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        docs = (FrotaDocumento.query
                .filter_by(veiculo_id=veiculo_id)
                .order_by(FrotaDocumento.data_vencimento.asc().nullslast())
                .all())
        return jsonify([d.to_dict() for d in docs]), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/veiculos/<id>/documentos")
        return jsonify({"erro": "Erro ao listar documentos", "detalhe": str(e)}), 500


@frota_bp.route('/veiculos/<int:veiculo_id>/documentos', methods=['POST'])
@jwt_required()
def criar_documento(veiculo_id):
    try:
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados, arquivo = _dados_e_arquivo()
        tipo = (dados.get('tipo') or '').strip()
        if tipo not in _DOC_TIPOS:
            return jsonify({"erro": f"tipo inválido (use {sorted(_DOC_TIPOS)})"}), 400

        arquivo_url, anexo_falhou = _upload_best_effort(arquivo, 'documentos')
        doc = FrotaDocumento(
            veiculo_id=veiculo.id,
            tipo=tipo,
            descricao=(dados.get('descricao') or '').strip() or None,
            data_vencimento=_parse_date(dados.get('data_vencimento')),
            valor=_to_num(dados.get('valor')),
            arquivo_url=arquivo_url,
            observacao=(dados.get('observacao') or '').strip() or None,
        )
        db.session.add(doc)
        db.session.commit()
        out = doc.to_dict()
        if anexo_falhou:
            out['aviso'] = 'Documento salvo, mas o upload do arquivo falhou.'
        return jsonify(out), 201
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /frota/veiculos/<id>/documentos")
        return jsonify({"erro": "Erro ao criar documento", "detalhe": str(e)}), 500


@frota_bp.route('/documentos/<int:doc_id>', methods=['PUT'])
@jwt_required()
def editar_documento(doc_id):
    try:
        doc = db.session.get(FrotaDocumento, doc_id)
        if not doc:
            return jsonify({"erro": "Documento não encontrado"}), 404
        if not _veiculo_visivel(doc.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados, arquivo = _dados_e_arquivo()
        if 'tipo' in dados:
            tipo = (dados.get('tipo') or '').strip()
            if tipo not in _DOC_TIPOS:
                return jsonify({"erro": f"tipo inválido (use {sorted(_DOC_TIPOS)})"}), 400
            doc.tipo = tipo
        if 'descricao' in dados:
            doc.descricao = (dados.get('descricao') or '').strip() or None
        if 'data_vencimento' in dados:
            doc.data_vencimento = _parse_date(dados.get('data_vencimento'))
        if 'valor' in dados:
            doc.valor = _to_num(dados.get('valor'))
        if 'observacao' in dados:
            doc.observacao = (dados.get('observacao') or '').strip() or None
        aviso = None
        if arquivo:
            arquivo_url, anexo_falhou = _upload_best_effort(arquivo, 'documentos')
            if arquivo_url:
                doc.arquivo_url = arquivo_url
            if anexo_falhou:
                aviso = 'Documento salvo, mas o upload do arquivo falhou.'

        db.session.commit()
        out = doc.to_dict()
        if aviso:
            out['aviso'] = aviso
        return jsonify(out), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em PUT /frota/documentos/<id>")
        return jsonify({"erro": "Erro ao editar documento", "detalhe": str(e)}), 500


@frota_bp.route('/documentos/<int:doc_id>', methods=['DELETE'])
@jwt_required()
def remover_documento(doc_id):
    try:
        doc = db.session.get(FrotaDocumento, doc_id)
        if not doc:
            return jsonify({"erro": "Documento não encontrado"}), 404
        if not _veiculo_visivel(doc.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        db.session.delete(doc)
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em DELETE /frota/documentos/<id>")
        return jsonify({"erro": "Erro ao remover documento", "detalhe": str(e)}), 500


# ---------------------------------------------------------------- manutenções

def _filtros_custo(query, modelo):
    """Filtros comuns de manutenções/abastecimentos (?veiculo_id=&obra_id=&local_tipo=&de=&ate=)."""
    veiculo_id = request.args.get('veiculo_id')
    if veiculo_id:
        query = query.filter(modelo.veiculo_id == _to_int(veiculo_id))
    obra_id = request.args.get('obra_id')
    if obra_id:
        query = query.filter(modelo.obra_id == _to_int(obra_id))
    local_tipo = request.args.get('local_tipo')
    if local_tipo == 'sem_local':
        query = query.filter(modelo.local_tipo.is_(None))
    elif local_tipo:
        query = query.filter(modelo.local_tipo == local_tipo)
    campo_data = modelo.data if hasattr(modelo, 'data') else None
    if campo_data is not None:
        de = _parse_date(request.args.get('de'))
        if de:
            query = query.filter(campo_data >= de)
        ate = _parse_date(request.args.get('ate'))
        if ate:
            query = query.filter(campo_data <= ate)
    return query


@frota_bp.route('/manutencoes', methods=['GET'])
@jwt_required()
def listar_manutencoes():
    try:
        user = get_current_user()
        query = _filtro_visibilidade(FrotaManutencao.query, FrotaManutencao.obra_id, user)
        query = _filtros_custo(query, FrotaManutencao)
        itens = query.order_by(FrotaManutencao.data.desc(), FrotaManutencao.id.desc()).all()
        return jsonify([m.to_dict() for m in itens]), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/manutencoes")
        return jsonify({"erro": "Erro ao listar manutenções", "detalhe": str(e)}), 500


@frota_bp.route('/manutencoes', methods=['POST'])
@jwt_required()
def criar_manutencao():
    try:
        dados, arquivo = _dados_e_arquivo()
        veiculo_id = _to_int(dados.get('veiculo_id'))
        if not veiculo_id:
            return jsonify({"erro": "veiculo_id é obrigatório"}), 400
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        tipo = (dados.get('tipo') or '').strip()
        if tipo not in _MANUT_TIPOS:
            return jsonify({"erro": f"tipo inválido (use {sorted(_MANUT_TIPOS)})"}), 400
        data = _parse_date(dados.get('data'))
        custo = _to_num(dados.get('custo'))
        if not data or custo is None:
            return jsonify({"erro": "data e custo são obrigatórios"}), 400

        arquivo_url, anexo_falhou = _upload_best_effort(arquivo, 'manutencoes')
        km = _to_int(dados.get('km'))
        manut = FrotaManutencao(
            veiculo_id=veiculo.id,
            tipo=tipo,
            descricao=(dados.get('descricao') or '').strip() or None,
            data=data,
            km=km,
            custo=custo,
            oficina=(dados.get('oficina') or '').strip() or None,
            arquivo_url=arquivo_url,
            observacao=(dados.get('observacao') or '').strip() or None,
            **_snapshot_local(veiculo),
        )
        if km and (veiculo.km_atual is None or km > veiculo.km_atual):
            veiculo.km_atual = km
        db.session.add(manut)
        db.session.commit()
        out = manut.to_dict()
        if anexo_falhou:
            out['aviso'] = 'Manutenção salva, mas o upload do arquivo falhou.'
        return jsonify(out), 201
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /frota/manutencoes")
        return jsonify({"erro": "Erro ao criar manutenção", "detalhe": str(e)}), 500


@frota_bp.route('/manutencoes/<int:item_id>', methods=['PUT'])
@jwt_required()
def editar_manutencao(item_id):
    try:
        manut = db.session.get(FrotaManutencao, item_id)
        if not manut:
            return jsonify({"erro": "Manutenção não encontrada"}), 404
        if not _veiculo_visivel(manut.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados, arquivo = _dados_e_arquivo()
        if 'tipo' in dados:
            tipo = (dados.get('tipo') or '').strip()
            if tipo not in _MANUT_TIPOS:
                return jsonify({"erro": f"tipo inválido (use {sorted(_MANUT_TIPOS)})"}), 400
            manut.tipo = tipo
        if 'data' in dados:
            data = _parse_date(dados.get('data'))
            if not data:
                return jsonify({"erro": "data inválida (YYYY-MM-DD)"}), 400
            manut.data = data
        if 'custo' in dados:
            custo = _to_num(dados.get('custo'))
            if custo is None:
                return jsonify({"erro": "custo inválido"}), 400
            manut.custo = custo
        if 'km' in dados:
            manut.km = _to_int(dados.get('km'))
        for campo in ('descricao', 'oficina', 'observacao'):
            if campo in dados:
                setattr(manut, campo, (dados.get(campo) or '').strip() or None)
        aviso = None
        if arquivo:
            arquivo_url, anexo_falhou = _upload_best_effort(arquivo, 'manutencoes')
            if arquivo_url:
                manut.arquivo_url = arquivo_url
            if anexo_falhou:
                aviso = 'Manutenção salva, mas o upload do arquivo falhou.'

        db.session.commit()
        out = manut.to_dict()
        if aviso:
            out['aviso'] = aviso
        return jsonify(out), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em PUT /frota/manutencoes/<id>")
        return jsonify({"erro": "Erro ao editar manutenção", "detalhe": str(e)}), 500


@frota_bp.route('/manutencoes/<int:item_id>', methods=['DELETE'])
@jwt_required()
def remover_manutencao(item_id):
    try:
        manut = db.session.get(FrotaManutencao, item_id)
        if not manut:
            return jsonify({"erro": "Manutenção não encontrada"}), 404
        if not _veiculo_visivel(manut.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        db.session.delete(manut)
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em DELETE /frota/manutencoes/<id>")
        return jsonify({"erro": "Erro ao remover manutenção", "detalhe": str(e)}), 500


# ---------------------------------------------------------------- abastecimentos

@frota_bp.route('/abastecimentos', methods=['GET'])
@jwt_required()
def listar_abastecimentos():
    try:
        user = get_current_user()
        query = _filtro_visibilidade(FrotaAbastecimento.query, FrotaAbastecimento.obra_id, user)
        query = _filtros_custo(query, FrotaAbastecimento)
        itens = query.order_by(FrotaAbastecimento.data.desc(), FrotaAbastecimento.id.desc()).all()
        return jsonify([a.to_dict() for a in itens]), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/abastecimentos")
        return jsonify({"erro": "Erro ao listar abastecimentos", "detalhe": str(e)}), 500


@frota_bp.route('/abastecimentos', methods=['POST'])
@jwt_required()
def criar_abastecimento():
    try:
        dados, _ = _dados_e_arquivo()
        veiculo_id = _to_int(dados.get('veiculo_id'))
        if not veiculo_id:
            return jsonify({"erro": "veiculo_id é obrigatório"}), 400
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        data = _parse_date(dados.get('data'))
        valor = _to_num(dados.get('valor'))
        if not data or valor is None:
            return jsonify({"erro": "data e valor são obrigatórios"}), 400
        condutor_id = _to_int(dados.get('condutor_id'))
        if condutor_id and not db.session.get(FrotaCondutor, condutor_id):
            return jsonify({"erro": "Condutor não encontrado"}), 400

        km = _to_int(dados.get('km'))
        abast = FrotaAbastecimento(
            veiculo_id=veiculo.id,
            data=data,
            litros=_to_num(dados.get('litros')),
            valor=valor,
            km=km,
            combustivel=(dados.get('combustivel') or '').strip() or None,
            posto=(dados.get('posto') or '').strip() or None,
            condutor_id=condutor_id,
            observacao=(dados.get('observacao') or '').strip() or None,
            **_snapshot_local(veiculo),
        )
        if km and (veiculo.km_atual is None or km > veiculo.km_atual):
            veiculo.km_atual = km
        db.session.add(abast)
        db.session.commit()
        return jsonify(abast.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /frota/abastecimentos")
        return jsonify({"erro": "Erro ao criar abastecimento", "detalhe": str(e)}), 500


@frota_bp.route('/abastecimentos/<int:item_id>', methods=['PUT'])
@jwt_required()
def editar_abastecimento(item_id):
    try:
        abast = db.session.get(FrotaAbastecimento, item_id)
        if not abast:
            return jsonify({"erro": "Abastecimento não encontrado"}), 404
        if not _veiculo_visivel(abast.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados = request.get_json(silent=True) or {}
        if 'data' in dados:
            data = _parse_date(dados.get('data'))
            if not data:
                return jsonify({"erro": "data inválida (YYYY-MM-DD)"}), 400
            abast.data = data
        if 'valor' in dados:
            valor = _to_num(dados.get('valor'))
            if valor is None:
                return jsonify({"erro": "valor inválido"}), 400
            abast.valor = valor
        if 'litros' in dados:
            abast.litros = _to_num(dados.get('litros'))
        if 'km' in dados:
            abast.km = _to_int(dados.get('km'))
        if 'condutor_id' in dados:
            condutor_id = _to_int(dados.get('condutor_id'))
            if condutor_id and not db.session.get(FrotaCondutor, condutor_id):
                return jsonify({"erro": "Condutor não encontrado"}), 400
            abast.condutor_id = condutor_id
        for campo in ('combustivel', 'posto', 'observacao'):
            if campo in dados:
                setattr(abast, campo, (dados.get(campo) or '').strip() or None)

        db.session.commit()
        return jsonify(abast.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em PUT /frota/abastecimentos/<id>")
        return jsonify({"erro": "Erro ao editar abastecimento", "detalhe": str(e)}), 500


@frota_bp.route('/abastecimentos/<int:item_id>', methods=['DELETE'])
@jwt_required()
def remover_abastecimento(item_id):
    try:
        abast = db.session.get(FrotaAbastecimento, item_id)
        if not abast:
            return jsonify({"erro": "Abastecimento não encontrado"}), 404
        if not _veiculo_visivel(abast.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        db.session.delete(abast)
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em DELETE /frota/abastecimentos/<id>")
        return jsonify({"erro": "Erro ao remover abastecimento", "detalhe": str(e)}), 500


# ---------------------------------------------------------------- multas

@frota_bp.route('/multas', methods=['GET'])
@jwt_required()
def listar_multas():
    try:
        user = get_current_user()
        # Multas não têm snapshot de obra — visibilidade herda do veículo.
        query = FrotaMulta.query.join(FrotaVeiculo, FrotaMulta.veiculo_id == FrotaVeiculo.id)
        query = _filtro_visibilidade(query, FrotaVeiculo.obra_id, user)
        veiculo_id = request.args.get('veiculo_id')
        if veiculo_id:
            query = query.filter(FrotaMulta.veiculo_id == _to_int(veiculo_id))
        status = request.args.get('status')
        if status:
            query = query.filter(FrotaMulta.status_pagamento == status)
        condutor_id = request.args.get('condutor_id')
        if condutor_id:
            query = query.filter(FrotaMulta.condutor_id == _to_int(condutor_id))
        itens = query.order_by(FrotaMulta.data_infracao.desc(), FrotaMulta.id.desc()).all()
        return jsonify([m.to_dict() for m in itens]), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/multas")
        return jsonify({"erro": "Erro ao listar multas", "detalhe": str(e)}), 500


@frota_bp.route('/multas', methods=['POST'])
@jwt_required()
def criar_multa():
    try:
        dados, arquivo = _dados_e_arquivo()
        veiculo_id = _to_int(dados.get('veiculo_id'))
        if not veiculo_id:
            return jsonify({"erro": "veiculo_id é obrigatório"}), 400
        veiculo = db.session.get(FrotaVeiculo, veiculo_id)
        if not veiculo:
            return jsonify({"erro": "Veículo não encontrado"}), 404
        if not _veiculo_visivel(veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        data_infracao = _parse_date(dados.get('data_infracao'))
        valor = _to_num(dados.get('valor'))
        if not data_infracao or valor is None:
            return jsonify({"erro": "data_infracao e valor são obrigatórios"}), 400
        status = (dados.get('status_pagamento') or 'pendente').strip()
        if status not in _MULTA_STATUS:
            return jsonify({"erro": f"status_pagamento inválido (use {sorted(_MULTA_STATUS)})"}), 400
        condutor_id = _to_int(dados.get('condutor_id'))
        if condutor_id and not db.session.get(FrotaCondutor, condutor_id):
            return jsonify({"erro": "Condutor não encontrado"}), 400

        arquivo_url, anexo_falhou = _upload_best_effort(arquivo, 'multas')
        multa = FrotaMulta(
            veiculo_id=veiculo.id,
            data_infracao=data_infracao,
            descricao=(dados.get('descricao') or '').strip() or None,
            valor=valor,
            pontos=_to_int(dados.get('pontos')),
            condutor_id=condutor_id,
            status_pagamento=status,
            data_pagamento=_parse_date(dados.get('data_pagamento')),
            arquivo_url=arquivo_url,
            observacao=(dados.get('observacao') or '').strip() or None,
        )
        db.session.add(multa)
        db.session.commit()
        out = multa.to_dict()
        if anexo_falhou:
            out['aviso'] = 'Multa salva, mas o upload do arquivo falhou.'
        return jsonify(out), 201
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em POST /frota/multas")
        return jsonify({"erro": "Erro ao criar multa", "detalhe": str(e)}), 500


@frota_bp.route('/multas/<int:item_id>', methods=['PUT'])
@jwt_required()
def editar_multa(item_id):
    try:
        multa = db.session.get(FrotaMulta, item_id)
        if not multa:
            return jsonify({"erro": "Multa não encontrada"}), 404
        if not _veiculo_visivel(multa.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403

        dados, arquivo = _dados_e_arquivo()
        if 'data_infracao' in dados:
            data_infracao = _parse_date(dados.get('data_infracao'))
            if not data_infracao:
                return jsonify({"erro": "data_infracao inválida (YYYY-MM-DD)"}), 400
            multa.data_infracao = data_infracao
        if 'valor' in dados:
            valor = _to_num(dados.get('valor'))
            if valor is None:
                return jsonify({"erro": "valor inválido"}), 400
            multa.valor = valor
        if 'status_pagamento' in dados:
            status = (dados.get('status_pagamento') or '').strip()
            if status not in _MULTA_STATUS:
                return jsonify({"erro": f"status_pagamento inválido (use {sorted(_MULTA_STATUS)})"}), 400
            multa.status_pagamento = status
        if 'data_pagamento' in dados:
            multa.data_pagamento = _parse_date(dados.get('data_pagamento'))
        if 'pontos' in dados:
            multa.pontos = _to_int(dados.get('pontos'))
        if 'condutor_id' in dados:
            condutor_id = _to_int(dados.get('condutor_id'))
            if condutor_id and not db.session.get(FrotaCondutor, condutor_id):
                return jsonify({"erro": "Condutor não encontrado"}), 400
            multa.condutor_id = condutor_id
        for campo in ('descricao', 'observacao'):
            if campo in dados:
                setattr(multa, campo, (dados.get(campo) or '').strip() or None)
        aviso = None
        if arquivo:
            arquivo_url, anexo_falhou = _upload_best_effort(arquivo, 'multas')
            if arquivo_url:
                multa.arquivo_url = arquivo_url
            if anexo_falhou:
                aviso = 'Multa salva, mas o upload do arquivo falhou.'

        db.session.commit()
        out = multa.to_dict()
        if aviso:
            out['aviso'] = aviso
        return jsonify(out), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em PUT /frota/multas/<id>")
        return jsonify({"erro": "Erro ao editar multa", "detalhe": str(e)}), 500


@frota_bp.route('/multas/<int:item_id>', methods=['DELETE'])
@jwt_required()
def remover_multa(item_id):
    try:
        multa = db.session.get(FrotaMulta, item_id)
        if not multa:
            return jsonify({"erro": "Multa não encontrada"}), 404
        if not _veiculo_visivel(multa.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        db.session.delete(multa)
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Erro em DELETE /frota/multas/<id>")
        return jsonify({"erro": "Erro ao remover multa", "detalhe": str(e)}), 500


# ---------------------------------------------------------------- imóveis / arquivo / dashboard

@frota_bp.route('/imoveis-admin', methods=['GET'])
@jwt_required()
def listar_imoveis_admin():
    """Lista de imóveis do patrimônio (banco admin, read-only). SEMPRE 200 —
    integração indisponível degrada para lista vazia + aviso."""
    imoveis, aviso = admin_read_service.listar_imoveis()
    return jsonify({"imoveis": imoveis, "aviso": aviso}), 200


@frota_bp.route('/arquivo/<tipo>/<int:item_id>', methods=['GET'])
@jwt_required()
def obter_arquivo(tipo, item_id):
    """Retorna signed URL do arquivo (documento | manutencao | multa) sob auth."""
    try:
        modelos = {
            'documento': FrotaDocumento,
            'manutencao': FrotaManutencao,
            'multa': FrotaMulta,
        }
        modelo = modelos.get(tipo)
        if not modelo:
            return jsonify({"erro": "tipo inválido (use documento|manutencao|multa)"}), 400
        obj = db.session.get(modelo, item_id)
        if obj and not _veiculo_visivel(obj.veiculo, get_current_user()):
            return jsonify({"erro": "Acesso negado a esta obra"}), 403
        path = obj.arquivo_url if obj else None
        if not path:
            return jsonify({"erro": "Arquivo não encontrado"}), 404
        return jsonify({"url": storage_service.signed_url(path, bucket=BUCKET_FROTA)}), 200
    except RuntimeError as e:
        return jsonify({"erro": str(e)}), 503
    except Exception as e:
        logger.exception("Erro em GET /frota/arquivo/<tipo>/<id>")
        return jsonify({"erro": "Erro ao obter arquivo", "detalhe": str(e)}), 500


@frota_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def obter_dashboard():
    try:
        user = get_current_user()
        competencia = request.args.get('competencia') or date.today().strftime('%Y-%m')
        try:
            inicio, fim = _competencia_range(competencia)
        except Exception:
            return jsonify({"erro": "competencia inválida (use YYYY-MM)"}), 400
        hoje = date.today()
        limite_30d = hoje + timedelta(days=30)

        veiculos = _filtro_visibilidade(
            FrotaVeiculo.query, FrotaVeiculo.obra_id, user
        ).filter(FrotaVeiculo.status != 'inativo').all()
        veiculo_ids = [v.id for v in veiculos]

        def _soma(modelo, campo_data, campo_valor, filtros=()):
            if not veiculo_ids:
                return 0.0
            q = (db.session.query(db.func.coalesce(db.func.sum(campo_valor), 0))
                 .filter(modelo.veiculo_id.in_(veiculo_ids),
                         campo_data >= inicio, campo_data <= fim))
            for f in filtros:
                q = q.filter(f)
            return float(q.scalar() or 0)

        total_manutencoes = _soma(FrotaManutencao, FrotaManutencao.data, FrotaManutencao.custo)
        total_abastecimentos = _soma(FrotaAbastecimento, FrotaAbastecimento.data,
                                     FrotaAbastecimento.valor)
        total_multas = _soma(FrotaMulta, FrotaMulta.data_pagamento, FrotaMulta.valor,
                             (FrotaMulta.status_pagamento == 'paga',))

        # Documentos vencidos / a vencer (30d) dos veículos visíveis não-inativos.
        docs_alerta = []
        if veiculo_ids:
            docs_alerta = (FrotaDocumento.query
                           .filter(FrotaDocumento.veiculo_id.in_(veiculo_ids),
                                   FrotaDocumento.data_vencimento.isnot(None),
                                   FrotaDocumento.data_vencimento <= limite_30d)
                           .order_by(FrotaDocumento.data_vencimento)
                           .all())
        docs_vencidos = [d for d in docs_alerta if d.data_vencimento < hoje]
        docs_a_vencer = [d for d in docs_alerta if d.data_vencimento >= hoje]

        cnhs_alerta = (FrotaCondutor.query
                       .filter(FrotaCondutor.status == 'ativo',
                               FrotaCondutor.cnh_validade.isnot(None),
                               FrotaCondutor.cnh_validade <= limite_30d)
                       .order_by(FrotaCondutor.cnh_validade)
                       .all())

        # Custo por local (snapshot) na competência: manutenções + abastecimentos.
        custo_local = {}
        if veiculo_ids:
            for modelo, campo_valor, campo_data in (
                (FrotaManutencao, FrotaManutencao.custo, FrotaManutencao.data),
                (FrotaAbastecimento, FrotaAbastecimento.valor, FrotaAbastecimento.data),
            ):
                linhas = (db.session.query(
                              modelo.local_tipo, modelo.local_nome,
                              db.func.coalesce(db.func.sum(campo_valor), 0))
                          .filter(modelo.veiculo_id.in_(veiculo_ids),
                                  campo_data >= inicio, campo_data <= fim)
                          .group_by(modelo.local_tipo, modelo.local_nome)
                          .all())
                for local_tipo, local_nome, total in linhas:
                    chave = (local_tipo or 'sem_local', local_nome or 'Sem local')
                    custo_local[chave] = custo_local.get(chave, 0.0) + float(total or 0)
        custo_por_local = sorted(
            [{'local_tipo': lt, 'local_nome': ln, 'total': round(t, 2)}
             for (lt, ln), t in custo_local.items()],
            key=lambda x: -x['total'],
        )

        return jsonify({
            'competencia': competencia,
            'veiculos_ativos': len([v for v in veiculos if v.status == 'ativo']),
            'veiculos_em_manutencao': len([v for v in veiculos if v.status == 'em_manutencao']),
            'custo_mes': {
                'manutencoes': round(total_manutencoes, 2),
                'abastecimentos': round(total_abastecimentos, 2),
                'multas_pagas': round(total_multas, 2),
                'total': round(total_manutencoes + total_abastecimentos + total_multas, 2),
            },
            'documentos_vencidos': [d.to_dict() for d in docs_vencidos],
            'documentos_a_vencer': [d.to_dict() for d in docs_a_vencer],
            'cnhs_a_vencer': [c.to_dict() for c in cnhs_alerta],
            'custo_por_local': custo_por_local,
        }), 200
    except Exception as e:
        logger.exception("Erro em GET /frota/dashboard")
        return jsonify({"erro": "Erro ao montar dashboard", "detalhe": str(e)}), 500
