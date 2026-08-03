"""Regressão local da classificação automática do planejamento."""
import os
import sys
from datetime import date
from types import SimpleNamespace


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.planejamento_service import automatic_status


def activity(**overrides):
    values = {
        'restricoes': [],
        'quantidade_planejada': 0,
        'quantidade_executada': 0,
        'data_inicio': None,
        'data_fim': None,
        'responsavel': None,
        'equipe': None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def check(label, actual, expected):
    if actual != expected:
        raise AssertionError(f'{label}: esperado {expected}, recebido {actual}')
    print(f'  PASS  {label}')


today = date.today()
check(
    'datas sem responsável e equipe continuam a planejar',
    automatic_status(activity(data_inicio=today, data_fim=today)),
    'a_planejar',
)
check(
    'atividade completa fica programada',
    automatic_status(activity(
        data_inicio=today,
        data_fim=today,
        responsavel='Responsável',
        equipe='Equipe A',
    )),
    'pronto',
)
check(
    'produção iniciada prevalece sobre dados de preparação ausentes',
    automatic_status(activity(quantidade_planejada=10, quantidade_executada=1)),
    'em_andamento',
)
check(
    'produção completa conclui atividade',
    automatic_status(activity(quantidade_planejada=10, quantidade_executada=10)),
    'concluido',
)
check(
    'impedimento aberto prevalece sobre os demais estados',
    automatic_status(activity(
        restricoes=[SimpleNamespace(status='aberta')],
        quantidade_planejada=10,
        quantidade_executada=10,
    )),
    'impedido',
)

print('OK: classificação automática do planejamento validada.')
