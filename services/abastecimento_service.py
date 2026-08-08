"""Regras de abastecimento compartilhadas entre a rota autenticada da Frota e
a rota pública do link do motorista.

Duas responsabilidades: gravar o abastecimento (com snapshot do local do
veículo e atualização do km) e derivar o consumo histórico de um veículo.

Consumo é calculado no padrão de frota: os km rodados desde o abastecimento
anterior divididos pelos litros do abastecimento atual (pressupõe tanque
cheio). O primeiro registro de cada veículo nunca tem consumo — não há km
anterior contra o qual medir.
"""
import logging

from extensions import db
from models.frota_abastecimento import FrotaAbastecimento

logger = logging.getLogger(__name__)


def snapshot_local(veiculo):
    """Local atual do veículo, congelado no lançamento do custo. Mover o
    veículo depois não reescreve o histórico."""
    return {
        'local_tipo': veiculo.local_tipo,
        'obra_id': veiculo.obra_id,
        'imovel_id': veiculo.imovel_id,
        'local_nome': veiculo.local_nome(),
    }


def registrar_abastecimento(veiculo, dados, origem='manual', solicitacao_id=None,
                            comprovante_url=None):
    """Cria o FrotaAbastecimento e sobe o km do veículo. NÃO commita — quem
    chama decide a transação (a rota pública grava solicitação + abastecimento
    de uma vez só).

    `dados` já vem validado: data (date), valor (float), litros/km/preco_litro
    opcionais.
    """
    km = dados.get('km')
    abast = FrotaAbastecimento(
        veiculo_id=veiculo.id,
        data=dados['data'],
        litros=dados.get('litros'),
        valor=dados['valor'],
        km=km,
        preco_litro=dados.get('preco_litro'),
        combustivel=(dados.get('combustivel') or None),
        posto=(dados.get('posto') or None),
        condutor_id=dados.get('condutor_id'),
        observacao=(dados.get('observacao') or None),
        comprovante_url=comprovante_url,
        origem=origem,
        solicitacao_id=solicitacao_id,
        **snapshot_local(veiculo),
    )
    if km and (veiculo.km_atual is None or km > veiculo.km_atual):
        veiculo.km_atual = km
    db.session.add(abast)
    return abast


def _media(valores):
    return round(sum(valores) / len(valores), 2) if valores else None


def historico_consumo(veiculo_id, de=None, ate=None):
    """Histórico de abastecimentos do veículo com consumo entre eles.

    Ordena por km (com a data como desempate) porque é o km que define a
    distância percorrida; abastecimento lançado fora de ordem cronológica
    não embaralha o cálculo. Registros sem km ficam na lista, mas não
    participam do consumo — não dá pra medir distância sem odômetro.
    """
    query = FrotaAbastecimento.query.filter(FrotaAbastecimento.veiculo_id == veiculo_id)
    if de:
        query = query.filter(FrotaAbastecimento.data >= de)
    if ate:
        query = query.filter(FrotaAbastecimento.data <= ate)
    itens = query.order_by(FrotaAbastecimento.data.asc(), FrotaAbastecimento.id.asc()).all()

    com_km = sorted(
        [a for a in itens if a.km],
        key=lambda a: (a.km, a.data, a.id),
    )
    consumo_por_id = {}
    km_anterior_por_id = {}
    anterior = None
    for abast in com_km:
        if anterior is not None:
            distancia = abast.km - anterior.km
            km_anterior_por_id[abast.id] = anterior.km
            litros = float(abast.litros) if abast.litros else 0
            # Distância zero/negativa = km repetido ou digitado errado; litros
            # zerados idem. Nos dois casos o consumo fica indefinido em vez de
            # virar um número inventado.
            if distancia > 0 and litros > 0:
                consumo_por_id[abast.id] = round(distancia / litros, 2)
        anterior = abast

    registros = []
    for abast in itens:
        out = abast.to_dict()
        out['km_anterior'] = km_anterior_por_id.get(abast.id)
        out['km_rodados'] = (
            abast.km - km_anterior_por_id[abast.id]
            if abast.id in km_anterior_por_id and abast.km else None
        )
        out['consumo_km_l'] = consumo_por_id.get(abast.id)
        valor = float(abast.valor) if abast.valor is not None else 0
        out['custo_por_km'] = (
            round(valor / out['km_rodados'], 2)
            if out['km_rodados'] and out['km_rodados'] > 0 else None
        )
        registros.append(out)
    registros.reverse()  # mais recente primeiro, como as demais listas da Frota

    consumos = list(consumo_por_id.values())
    total_litros = sum(float(a.litros) for a in itens if a.litros)
    total_valor = sum(float(a.valor) for a in itens if a.valor is not None)
    km_rodados = sum(r['km_rodados'] for r in registros if r['km_rodados'])

    return {
        'registros': registros,
        'resumo': {
            'abastecimentos': len(itens),
            'total_litros': round(total_litros, 2),
            'total_valor': round(total_valor, 2),
            'km_rodados': km_rodados or None,
            'consumo_medio_km_l': _media(consumos),
            'preco_medio_litro': (round(total_valor / total_litros, 3)
                                  if total_litros else None),
            'custo_por_km': (round(total_valor / km_rodados, 2)
                             if km_rodados else None),
            'melhor_consumo_km_l': max(consumos) if consumos else None,
            'pior_consumo_km_l': min(consumos) if consumos else None,
        },
    }
