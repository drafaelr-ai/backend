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
from models.frota_posto_combustivel import UNIDADES, FrotaPostoCombustivel
from models.frota_posto_recarga import FrotaPostoRecarga

logger = logging.getLogger(__name__)

_VALOR_MAX = 10_000_000.0
_LITROS_MAX = 1_000_000.0


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
        unidade=limpar_unidade(dados.get('unidade')),
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


# ------------------------------------------------------- unidade do posto

def limpar_unidade(valor):
    """'reais' (crédito em dinheiro) ou 'litros' (litros pré-comprados)."""
    texto = str(valor or '').strip().lower()
    if not texto:
        return 'reais'
    if texto in ('r$', 'real', 'reais', 'dinheiro', 'valor'):
        return 'reais'
    if texto in ('l', 'litro', 'litros'):
        return 'litros'
    raise PostoInvalido("Unidade do posto deve ser 'reais' ou 'litros'")


def unidade_de(posto):
    return (getattr(posto, 'unidade', None) or 'reais')


# --------------------------------------------------------------- movimentos

def _numero_br(valor, rotulo):
    """Aceita número ou string BR ('1.250,50', '1.250') e devolve float."""
    if valor is None or valor == '':
        raise PostoInvalido(f'Informe {rotulo}')
    if isinstance(valor, bool):
        raise PostoInvalido(f'{rotulo.capitalize()} inválido')
    if isinstance(valor, (int, float)):
        numero = float(valor)
    else:
        texto = str(valor).strip().replace('R$', '').strip()
        # Vírgula presente: o ponto só pode ser separador de milhar ('1.250,50').
        if ',' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        # Sem vírgula, grupos de 3 dígitos são milhar BR: '1.250' é mil duzentos
        # e cinquenta, não um e vinte e cinco. Sem isto o float() lê o ponto como
        # decimal e o lançamento entra mil vezes menor, sem erro nenhum.
        elif re.fullmatch(r'-?\d{1,3}(\.\d{3})+', texto):
            texto = texto.replace('.', '')
        try:
            numero = float(texto)
        except ValueError:
            raise PostoInvalido(f'{rotulo.capitalize()} inválido') from None
    if numero != numero or numero in (float('inf'), float('-inf')):
        raise PostoInvalido(f'{rotulo.capitalize()} inválido')
    return numero


def limpar_valor(valor):
    """Valor em reais de uma recarga: positivo e dentro do teto."""
    numero = _numero_br(valor, 'o valor da recarga')
    if numero <= 0:
        raise PostoInvalido('O valor da recarga deve ser maior que zero')
    if numero > _VALOR_MAX:
        raise PostoInvalido('O valor da recarga está acima do limite permitido')
    return round(numero, 2)


def limpar_litros(valor):
    """Litros de uma recarga: positivo e dentro do teto."""
    numero = _numero_br(valor, 'os litros da recarga')
    if numero <= 0:
        raise PostoInvalido('Os litros da recarga devem ser maior que zero')
    if numero > _LITROS_MAX:
        raise PostoInvalido('Os litros da recarga estão acima do limite permitido')
    return round(numero, 2)


def limpar_saldo_informado(valor, unidade):
    """Saldo que o operador digitou. Pode ser negativo (posto no vermelho)."""
    rotulo = 'o saldo' if unidade == 'reais' else 'o saldo em litros'
    numero = _numero_br(valor, rotulo)
    teto = _VALOR_MAX if unidade == 'reais' else _LITROS_MAX
    if abs(numero) > teto:
        raise PostoInvalido('O saldo informado está acima do limite permitido')
    return round(numero, 2)


def limpar_observacao(valor):
    texto = re.sub(r'\s+', ' ', str(valor or '').strip())
    if not texto:
        return None
    return texto[:300]


def criar_recarga(posto, dados, criado_por_id=None):
    """Monta a recarga sem commitar — o chamador controla a transação.

    A quantidade obrigatória é a da unidade do posto. Em posto de litros o
    valor pago continua aceito, mas é informativo: não entra no saldo.
    """
    unidade = unidade_de(posto)
    if unidade == 'litros':
        litros = limpar_litros(dados.get('litros'))
        valor = None
        if dados.get('valor') not in (None, ''):
            valor = limpar_valor(dados.get('valor'))
    else:
        valor = limpar_valor(dados.get('valor'))
        litros = None
        if dados.get('litros') not in (None, ''):
            litros = limpar_litros(dados.get('litros'))

    recarga = FrotaPostoRecarga(
        posto_id=posto.id,
        data=dados.get('data') or date.today(),
        tipo='recarga',
        valor=valor,
        litros=litros,
        observacao=limpar_observacao(dados.get('observacao')),
        criado_por_id=criado_por_id,
    )
    db.session.add(recarga)
    return recarga


def ajustar_saldo(posto, saldo_informado, criado_por_id=None, observacao=None):
    """Grava a DIFERENÇA entre o saldo informado e o saldo calculado.

    É isto que acontece quando o operador digita o saldo que o posto diz que
    ele tem. Gravar a diferença, e não o número cru, mantém o saldo derivado
    (movimentos menos abastecimentos) — ele continua caindo sozinho a cada
    abastecimento — e preserva o histórico de quem lançou o quê.

    A diferença é calculada aqui dentro, a partir do saldo do momento, para
    dois operadores ajustando ao mesmo tempo não se sobrescreverem.
    """
    unidade = unidade_de(posto)
    alvo = limpar_saldo_informado(saldo_informado, unidade)
    atual = saldo_do_posto(posto)
    diferenca = round(alvo - atual, 2)
    if diferenca == 0:
        return None, atual

    nota = limpar_observacao(observacao)
    if not nota:
        nota = f'Ajuste para o saldo informado de {alvo:.2f}'.replace('.', ',')

    ajuste = FrotaPostoRecarga(
        posto_id=posto.id,
        data=date.today(),
        tipo='ajuste',
        valor=diferenca if unidade == 'reais' else None,
        litros=diferenca if unidade == 'litros' else None,
        observacao=nota,
        criado_por_id=criado_por_id,
    )
    db.session.add(ajuste)
    return ajuste, alvo


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


def _zero_consumo():
    return {'valor': 0.0, 'litros': 0.0, 'qtd': 0, 'sem_litros': 0, 'ultima': None}


def _consumo_por_posto(postos):
    """Gasto por posto, nas duas unidades: casa por posto_id e, nos registros
    antigos que só tinham o nome digitado, pelo nome normalizado em Python.

    `sem_litros` conta abastecimentos sem litros informados. Em posto de
    litros eles não podem ser descontados do saldo, e o operador precisa
    saber disso em vez de confiar num número silenciosamente otimista.
    """
    totais = {p.id: _zero_consumo() for p in postos}

    def acumular(posto_id, valor, litros, qtd, sem_litros, ultima):
        alvo = totais.get(posto_id)
        if alvo is None:
            return
        alvo['valor'] += float(valor or 0)
        alvo['litros'] += float(litros or 0)
        alvo['qtd'] += int(qtd or 0)
        alvo['sem_litros'] += int(sem_litros or 0)
        if ultima and (alvo['ultima'] is None or ultima > alvo['ultima']):
            alvo['ultima'] = ultima

    colunas = (
        func.coalesce(func.sum(FrotaAbastecimento.valor), 0),
        func.coalesce(func.sum(FrotaAbastecimento.litros), 0),
        func.count(FrotaAbastecimento.id),
        func.count().filter(FrotaAbastecimento.litros.is_(None)),
        func.max(FrotaAbastecimento.data),
    )

    por_id = (db.session.query(FrotaAbastecimento.posto_id, *colunas)
              .filter(FrotaAbastecimento.posto_id.isnot(None))
              .group_by(FrotaAbastecimento.posto_id).all())
    for posto_id, valor, litros, qtd, sem_litros, ultima in por_id:
        acumular(posto_id, valor, litros, qtd, sem_litros, ultima)

    por_nome = _indice_por_nome(postos)
    if por_nome:
        legados = (db.session.query(FrotaAbastecimento.posto, *colunas)
                   .filter(FrotaAbastecimento.posto_id.is_(None),
                           FrotaAbastecimento.posto.isnot(None))
                   .group_by(FrotaAbastecimento.posto).all())
        # acumular() é aditivo, então vários nomes digitados que caem no mesmo
        # cadastro ('Posto X', 'POSTO  X') somam no mesmo saldo.
        for nome_cru, valor, litros, qtd, sem_litros, ultima in legados:
            acumular(por_nome.get(normalizar_nome(nome_cru)),
                     valor, litros, qtd, sem_litros, ultima)

    return totais


def _movimentos_por_posto(posto_ids):
    """Recargas e ajustes somados por posto, nas duas unidades."""
    base = {pid: {'valor': 0.0, 'litros': 0.0, 'qtd': 0, 'ultima': None}
            for pid in posto_ids}
    for posto_id, valor, litros, qtd, ultima in (db.session.query(
            FrotaPostoRecarga.posto_id,
            func.coalesce(func.sum(FrotaPostoRecarga.valor), 0),
            func.coalesce(func.sum(FrotaPostoRecarga.litros), 0),
            func.count(FrotaPostoRecarga.id),
            func.max(FrotaPostoRecarga.data),
    ).group_by(FrotaPostoRecarga.posto_id).all()):
        if posto_id in base:
            base[posto_id] = {
                'valor': float(valor or 0), 'litros': float(litros or 0),
                'qtd': int(qtd or 0), 'ultima': ultima,
            }
    return base


def _linha_saldo(posto, mov, con):
    unidade = unidade_de(posto)
    campo = 'litros' if unidade == 'litros' else 'valor'
    recarregado = round(mov[campo], 2)
    consumido = round(con[campo], 2)
    return {
        'posto_id': posto.id,
        'posto_nome': posto.nome,
        'unidade': unidade,
        'ativo': bool(posto.ativo),
        'total_recarregado': recarregado,
        'recargas': mov['qtd'],
        'ultima_recarga': mov['ultima'].isoformat() if mov['ultima'] else None,
        'total_consumido': consumido,
        'abastecimentos': con['qtd'],
        # Só pesa em posto de litros: sem litros informados o abastecimento
        # não tem como ser descontado de um saldo em litros.
        'abastecimentos_sem_litros': con['sem_litros'] if unidade == 'litros' else 0,
        'ultimo_abastecimento': con['ultima'].isoformat() if con['ultima'] else None,
        'saldo': round(recarregado - consumido, 2),
        # Em posto de litros o dinheiro é informativo, e vice-versa.
        'total_pago': round(mov['valor'], 2) if unidade == 'litros' else None,
        'litros_consumidos': round(con['litros'], 2) if unidade == 'reais' else None,
    }


def saldo_do_posto(posto):
    """Saldo atual de um posto só, na unidade dele."""
    mov = _movimentos_por_posto([posto.id])[posto.id]
    con = _consumo_por_posto([posto])[posto.id]
    return _linha_saldo(posto, mov, con)['saldo']


def saldos(incluir_inativos=False):
    """Saldo atual de cada posto, do menor saldo para o maior.

    O resumo soma SÓ os postos em reais: juntar litro com real daria um
    número sem significado. Os postos em litros entram no resumo pelo
    próprio total em litros, em campo separado.
    """
    query = FrotaPostoCombustivel.query
    if not incluir_inativos:
        query = query.filter(FrotaPostoCombustivel.ativo.is_(True))
    postos = query.order_by(FrotaPostoCombustivel.nome.asc()).all()
    if not postos:
        return {'postos': [], 'resumo': {
            'total_recarregado': 0.0, 'total_consumido': 0.0, 'saldo': 0.0,
            'postos_negativos': 0, 'saldo_litros': 0.0, 'postos_em_litros': 0,
        }}

    movimentos = _movimentos_por_posto([p.id for p in postos])
    consumo = _consumo_por_posto(postos)
    saida = [_linha_saldo(p, movimentos[p.id], consumo[p.id]) for p in postos]
    saida.sort(key=lambda p: (p['saldo'], p['posto_nome']))

    em_reais = [p for p in saida if p['unidade'] == 'reais']
    em_litros = [p for p in saida if p['unidade'] == 'litros']
    total_recarregado = round(sum(p['total_recarregado'] for p in em_reais), 2)
    total_consumido = round(sum(p['total_consumido'] for p in em_reais), 2)
    return {
        'postos': saida,
        'resumo': {
            'total_recarregado': total_recarregado,
            'total_consumido': total_consumido,
            'saldo': round(total_recarregado - total_consumido, 2),
            'postos_negativos': sum(1 for p in saida if p['saldo'] < 0),
            'saldo_litros': round(sum(p['saldo'] for p in em_litros), 2),
            'postos_em_litros': len(em_litros),
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

    Tudo na unidade do posto: em posto de litros os movimentos andam em
    litros e o valor pago fica como informação ao lado.
    """
    unidade = unidade_de(posto)
    em_litros = unidade == 'litros'
    col_mov = FrotaPostoRecarga.litros if em_litros else FrotaPostoRecarga.valor
    col_abast = FrotaAbastecimento.litros if em_litros else FrotaAbastecimento.valor

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
        quantidade = r.litros if em_litros else r.valor
        movimentos.append({
            'tipo': r.tipo or 'recarga', 'id': r.id,
            'data': r.data.isoformat() if r.data else None,
            'valor': float(quantidade) if quantidade is not None else 0.0,
            'descricao': r.observacao or (
                'Ajuste de saldo' if (r.tipo == 'ajuste') else 'Recarga de crédito'),
            'veiculo_placa': None, 'litros': None,
            'valor_pago': float(r.valor) if (em_litros and r.valor is not None) else None,
        })
    for a in abastecimentos:
        quantidade = a.litros if em_litros else a.valor
        movimentos.append({
            'tipo': 'abastecimento', 'id': a.id,
            'data': a.data.isoformat() if a.data else None,
            'valor': -float(quantidade) if quantidade is not None else 0.0,
            'descricao': (a.veiculo.modelo if a.veiculo else None) or 'Abastecimento',
            'veiculo_placa': a.veiculo.placa if a.veiculo else None,
            'litros': float(a.litros) if a.litros is not None else None,
            # Abastecimento sem litros em posto de litros não desconta nada:
            # marcado para a tela poder avisar em vez de esconder.
            'sem_quantidade': bool(em_litros and a.litros is None),
            'valor_pago': float(a.valor) if (em_litros and a.valor is not None) else None,
        })
    movimentos.sort(key=lambda m: (m['data'] or '', m['tipo'], m['id']))

    # Saldo anterior ao período: sem ele a coluna de saldo recomeçaria do zero
    # a cada filtro de data e contradiria o resumo, que é do posto inteiro.
    saldo_inicial = 0.0
    if de:
        mov_antes = db.session.query(func.coalesce(func.sum(col_mov), 0)).filter(
            FrotaPostoRecarga.posto_id == posto.id,
            FrotaPostoRecarga.data < de).scalar()
        abast_antes = db.session.query(func.coalesce(func.sum(col_abast), 0)).filter(
            cond_abast, FrotaAbastecimento.data < de).scalar()
        # As colunas são Numeric: func.sum devolve Decimal, precisa virar float.
        saldo_inicial = round(float(mov_antes or 0) - float(abast_antes or 0), 2)

    acumulado = saldo_inicial
    for mov in movimentos:
        acumulado = round(acumulado + mov['valor'], 2)
        mov['saldo_acumulado'] = acumulado
    movimentos.reverse()

    totais = next(
        (p for p in saldos(incluir_inativos=True)['postos'] if p['posto_id'] == posto.id),
        None,
    )
    return {'posto': posto.to_dict(), 'unidade': unidade,
            'saldo_inicial': saldo_inicial,
            'movimentos': movimentos, 'resumo': totais}
