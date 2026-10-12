"""Cadastro e resolução de postos usados nos abastecimentos da frota."""

import re
import unicodedata

from sqlalchemy.exc import IntegrityError

from extensions import db
from models.frota_posto_combustivel import FrotaPostoCombustivel


class PostoInvalido(ValueError):
    pass


def normalizar_nome(nome):
    """Normaliza caixa e espaços sem remover acentos do nome exibido."""
    texto = unicodedata.normalize('NFKC', str(nome or '')).strip()
    return re.sub(r'\s+', ' ', texto).lower()


def limpar_nome(nome):
    texto = unicodedata.normalize('NFKC', str(nome or '')).strip()
    texto = re.sub(r'\s+', ' ', texto)
    if not texto:
        return None
    if len(texto) < 2:
        raise PostoInvalido('Informe um nome de posto com pelo menos 2 caracteres')
    if len(texto) > 160:
        raise PostoInvalido('O nome do posto deve ter no máximo 160 caracteres')
    return texto


def resolver_posto(dados, *, permitir_criar=True):
    """Resolve seleção existente ou cria o posto informado no próprio fluxo.

    `posto` permanece aceito para clientes antigos e também passa a alimentar
    o cadastro. O chamador controla commit/rollback da transação.
    """
    valor_id = dados.get('posto_id')
    if valor_id not in (None, ''):
        try:
            posto_id = int(valor_id)
        except (TypeError, ValueError):
            raise PostoInvalido('posto_id inválido') from None
        posto = db.session.get(FrotaPostoCombustivel, posto_id)
        if not posto or not posto.ativo:
            raise PostoInvalido('Posto de combustível não encontrado')
        return posto

    nome = limpar_nome(dados.get('novo_posto_nome') or dados.get('posto'))
    if not nome:
        return None

    normalizado = normalizar_nome(nome)
    existente = FrotaPostoCombustivel.query.filter_by(
        nome_normalizado=normalizado,
    ).first()
    if existente:
        return existente
    if not permitir_criar:
        raise PostoInvalido('Selecione um posto cadastrado')

    posto = FrotaPostoCombustivel(
        nome=nome,
        nome_normalizado=normalizado,
    )
    try:
        # O savepoint cobre dois abastecimentos simultâneos tentando cadastrar
        # o mesmo posto. Um deles cria; o outro reaproveita o registro vencedor
        # sem perder o restante da própria transação.
        with db.session.begin_nested():
            db.session.add(posto)
            db.session.flush()
        return posto
    except IntegrityError:
        existente = FrotaPostoCombustivel.query.filter_by(
            nome_normalizado=normalizado,
        ).first()
        if existente:
            return existente
        raise
