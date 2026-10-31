"""Cadastro, resolução e saldo pré-pago dos postos usados na frota.

Saldo de um posto = soma das recargas - soma dos abastecimentos daquele
posto. Nada de saldo em coluna: o abastecimento entra por três vias
(link do motorista, lançamento manual e e-Frotas) e um número gravado
ficaria desatualizado na primeira que não passasse por aqui.
"""

import logging
import re
import unicodedata
from datetime import date

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from extensions import db
from models.frota_abastecimento import FrotaAbastecimento
from models.frota_posto_combustivel import FrotaPostoCombustivel
from models.frota_posto_recarga import FrotaPostoRecarga

logger = logging.getLogger(__name__)

_VALOR_MAX = 10_000_000.0


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


# --------------------------------------------------------------- recargas

def limpar_valor(valor):
    """Aceita número ou string BR ('1.250,00', '1.250') e devolve float > 0."""
    if valor is None or valor == '':
        raise PostoInvalido('Informe o valor da recarga')
    if isinstance(valor, bool):
        raise PostoInvalido('Valor da recarga inválido')
    if isinstance(valor, (int, float)):
        numero = float(valor)
    else:
        texto = str(valor).strip().replace('R$', '').strip()
        # Vírgula presente: o ponto só pode ser separador de milhar ('1.250,50').
        if ',' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        # Sem vírgula, grupos de 3 dígitos são milhar BR: '1.250' é mil duzentos
        # e cinquenta, não um e vinte e cinco. Sem isto o float() lê o ponto como
        # decimal e a recarga entra mil vezes menor, sem erro nenhum.
        elif re.fullmatch(r'\d{1,3}(\.\d{3})+', texto):
            texto = texto.replace('.', '')
        try:
            numero = float(texto)
        except ValueError:
            raise PostoInvalido('Valor da recarga inválido') from None
    if numero != numero or numero in (float('inf'), float('-inf')):
        raise PostoInvalido('Valor da recarga inválido')
    if numero <= 0:
        raise PostoInvalido('O valor da recarga deve ser maior que zero')
    if numero > _VALOR_MAX:
        raise PostoInvalido('O valor da recarga está acima do limite permitido')
    return round(numero, 2)


def limpar_observacao(valor):
    texto = re.sub(r'\s+', ' ', str(valor or '').strip())
    if not texto:
        return None
    return texto[:300]


def criar_recarga(posto, dados, criado_por_id=None):
    """Monta a recarga sem commitar — o chamador controla a transação."""
    recarga = FrotaPostoRecarga(
        posto_id=posto.id,
        data=dados.get('data') or date.today(),
        valor=limpar_valor(dados.get('valor')),
        observacao=limpar_observacao(dados.get('observacao')),
        criado_por_id=criado_por_id,
    )
    db.session.add(recarga)
    return recarga


# ------------------------------------------------------------ saldo/extrato

def _indice_por_nome(postos):
    """Mapa nome normalizado -> posto_id, para casar abastecimento antigo.

    O casamento acontece em Python, com `normalizar_nome` — a MESMA função do
    índice único `nome_normalizado` e do backfill do auto_migration. Comparar
    com uma expressão SQL equivalente não funciona: `lower()` do Postgres
    entende acento e o do SQLite não, então a regra do teste divergiria da de
    produção, e nenhum dos dois normaliza a forma Unicode (NFC/NFD).

    Nome normalizado repetido sai do mapa: dois cadastros com a mesma chave
    não permitem decidir de quem é o gasto, e chutar debita no posto errado.
    """
    por_nome = {}
    repetidos = set()
    for posto in postos:
        chave = normalizar_nome(posto.nome)
        if chave in por_nome:
            repetidos.add(chave)
            continue
        por_nome[chave] = posto.id
    for chave in repetidos:
        por_nome.pop(chave, None)
        logger.warning(
            'Postos com nome normalizado repetido (%s): abastecimento antigo '
            'sem posto_id não foi atribuído a nenhum deles', chave,
        )
    return por_nome


def _nomes_legados(posto):
    """Nomes digitados (posto_id nulo) que pertencem a este cadastro."""
    chave = normalizar_nome(posto.nome)
    brutos = (db.session.query(FrotaAbastecimento.posto)
              .filter(FrotaAbastecimento.posto_id.is_(None),
                      FrotaAbastecimento.posto.isnot(None))
              .distinct().all())
    return [nome for (nome,) in brutos if normalizar_nome(nome) == chave]


def _condicao_abastecimento(posto):
    """Abastecimentos que pesam na conta do posto: por id e, nos registros
    antigos, pelo nome digitado resolvido em Python."""
    condicao = FrotaAbastecimento.posto_id == posto.id
    nomes = _nomes_legados(posto)
    if nomes:
        condicao = or_(condicao, and_(
            FrotaAbastecimento.posto_id.is_(None),
            FrotaAbastecimento.posto.in_(nomes),
        ))
    return condicao


def _consumo_por_posto(postos):
    """Gasto por posto: casa por posto_id e, nos registros antigos que só
    tinham o nome digitado, pelo nome normalizado em Python."""
    totais = {p.id: {'total': 0.0, 'qtd': 0, 'ultima': None} for p in postos}

    def acumular(posto_id, total, qtd, ultima):
        alvo = totais.get(posto_id)
        if alvo is None:
            return
        alvo['total'] += float(total or 0)
        alvo['qtd'] += int(qtd or 0)
        if ultima and (alvo['ultima'] is None or ultima > alvo['ultima']):
            alvo['ultima'] = ultima

    por_id = (db.session.query(
        FrotaAbastecimento.posto_id,
        func.coalesce(func.sum(FrotaAbastecimento.valor), 0),
        func.count(FrotaAbastecimento.id),
        func.max(FrotaAbastecimento.data),
    ).filter(FrotaAbastecimento.posto_id.isnot(None))
     .group_by(FrotaAbastecimento.posto_id).all())
    for posto_id, total, qtd, ultima in por_id:
        acumular(posto_id, total, qtd, ultima)

    por_nome = _indice_por_nome(postos)
    if por_nome:
        legados = (db.session.query(
            FrotaAbastecimento.posto,
            func.coalesce(func.sum(FrotaAbastecimento.valor), 0),
            func.count(FrotaAbastecimento.id),
            func.max(FrotaAbastecimento.data),
        ).filter(FrotaAbastecimento.posto_id.is_(None),
                 FrotaAbastecimento.posto.isnot(None))
         .group_by(FrotaAbastecimento.posto).all())
        # acumular() é aditivo, então vários nomes digitados que caem no mesmo
        # cadastro ('Posto X', 'POSTO  X') somam no mesmo saldo.
        for nome_cru, total, qtd, ultima in legados:
            acumular(por_nome.get(normalizar_nome(nome_cru)), total, qtd, ultima)

    return totais


def saldos(incluir_inativos=False):
    """Saldo atual de cada posto, do menor saldo para o maior."""
    query = FrotaPostoCombustivel.query
    if not incluir_inativos:
        query = query.filter(FrotaPostoCombustivel.ativo.is_(True))
    postos = query.order_by(FrotaPostoCombustivel.nome.asc()).all()
    if not postos:
        return {'postos': [], 'resumo': {
            'total_recarregado': 0.0, 'total_consumido': 0.0, 'saldo': 0.0,
            'postos_negativos': 0,
        }}

    recargas = {p.id: {'total': 0.0, 'qtd': 0, 'ultima': None} for p in postos}
    for posto_id, total, qtd, ultima in (db.session.query(
            FrotaPostoRecarga.posto_id,
            func.coalesce(func.sum(FrotaPostoRecarga.valor), 0),
            func.count(FrotaPostoRecarga.id),
            func.max(FrotaPostoRecarga.data),
    ).group_by(FrotaPostoRecarga.posto_id).all()):
        if posto_id in recargas:
            recargas[posto_id] = {
                'total': float(total or 0), 'qtd': int(qtd or 0), 'ultima': ultima,
            }

    consumo = _consumo_por_posto(postos)

    saida = []
    for posto in postos:
        rec = recargas[posto.id]
        con = consumo[posto.id]
        saida.append({
            'posto_id': posto.id,
            'posto_nome': posto.nome,
            'ativo': bool(posto.ativo),
            'total_recarregado': round(rec['total'], 2),
            'recargas': rec['qtd'],
            'ultima_recarga': rec['ultima'].isoformat() if rec['ultima'] else None,
            'total_consumido': round(con['total'], 2),
            'abastecimentos': con['qtd'],
            'ultimo_abastecimento': con['ultima'].isoformat() if con['ultima'] else None,
            'saldo': round(rec['total'] - con['total'], 2),
        })
    saida.sort(key=lambda p: (p['saldo'], p['posto_nome']))

    total_recarregado = round(sum(p['total_recarregado'] for p in saida), 2)
    total_consumido = round(sum(p['total_consumido'] for p in saida), 2)
    return {
        'postos': saida,
        'resumo': {
            'total_recarregado': total_recarregado,
            'total_consumido': total_consumido,
            'saldo': round(total_recarregado - total_consumido, 2),
            'postos_negativos': sum(1 for p in saida if p['saldo'] < 0),
        },
    }


def extrato(posto, de=None, ate=None):
    """Recargas e abastecimentos do posto na mesma linha do tempo.

    O saldo do resumo é sempre o total do posto (não o do período): é o
    número que o operador compara com o extrato do posto.

    O acumulado dos movimentos parte de `saldo_inicial` — o saldo do posto
    imediatamente antes de `de` — e não de zero. Sem isso a coluna de saldo
    recomeçaria a cada filtro de data e contradiria o resumo. Com `ate`
    preenchido e movimento posterior a ele, o acumulado da linha mais
    recente é o saldo no fim do período, e aí difere do resumo de propósito.
    """
    # Mesma regra de saldos(). Fica em variável porque o saldo anterior ao
    # período reusa a condição sem os filtros de data.
    cond_abast = _condicao_abastecimento(posto)
    filtros_abast = [cond_abast]
    filtros_recarga = [FrotaPostoRecarga.posto_id == posto.id]
    if de:
        filtros_abast.append(FrotaAbastecimento.data >= de)
        filtros_recarga.append(FrotaPostoRecarga.data >= de)
    if ate:
        filtros_abast.append(FrotaAbastecimento.data <= ate)
        filtros_recarga.append(FrotaPostoRecarga.data <= ate)

    recargas = (FrotaPostoRecarga.query.filter(*filtros_recarga)
                .order_by(FrotaPostoRecarga.data.asc(),
                          FrotaPostoRecarga.id.asc()).all())
    abastecimentos = (FrotaAbastecimento.query.filter(*filtros_abast)
                      .order_by(FrotaAbastecimento.data.asc(),
                                FrotaAbastecimento.id.asc()).all())

    movimentos = []
    for r in recargas:
        movimentos.append({
            'tipo': 'recarga', 'id': r.id,
            'data': r.data.isoformat() if r.data else None,
            'valor': float(r.valor or 0),
            'descricao': r.observacao or 'Recarga de crédito',
            'veiculo_placa': None, 'litros': None,
        })
    for a in abastecimentos:
        movimentos.append({
            'tipo': 'abastecimento', 'id': a.id,
            'data': a.data.isoformat() if a.data else None,
            'valor': -float(a.valor or 0),
            'descricao': (a.veiculo.modelo if a.veiculo else None) or 'Abastecimento',
            'veiculo_placa': a.veiculo.placa if a.veiculo else None,
            'litros': float(a.litros) if a.litros is not None else None,
        })
    movimentos.sort(key=lambda m: (m['data'] or '', m['tipo'], m['id']))

    # Saldo anterior ao período: sem ele a coluna de saldo recomeçaria do zero
    # a cada filtro de data e contradiria o resumo, que é do posto inteiro.
    saldo_inicial = 0.0
    if de:
        recargas_antes = db.session.query(
            func.coalesce(func.sum(FrotaPostoRecarga.valor), 0),
        ).filter(FrotaPostoRecarga.posto_id == posto.id,
                 FrotaPostoRecarga.data < de).scalar()
        abast_antes = db.session.query(
            func.coalesce(func.sum(FrotaAbastecimento.valor), 0),
        ).filter(cond_abast, FrotaAbastecimento.data < de).scalar()
        # valor é Numeric: func.sum devolve Decimal, precisa virar float.
        saldo_inicial = round(float(recargas_antes or 0) - float(abast_antes or 0), 2)

    acumulado = saldo_inicial
    for mov in movimentos:
        acumulado = round(acumulado + mov['valor'], 2)
        mov['saldo_acumulado'] = acumulado
    movimentos.reverse()

    totais = next(
        (p for p in saldos(incluir_inativos=True)['postos'] if p['posto_id'] == posto.id),
        None,
    )
    return {'posto': posto.to_dict(), 'saldo_inicial': saldo_inicial,
            'movimentos': movimentos, 'resumo': totais}
