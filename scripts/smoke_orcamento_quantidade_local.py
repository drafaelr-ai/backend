"""Regressão local das regras de quantidade do orçamento."""
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.orcamento_service import normalizar_quantidade_item_orcamento


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f'  PASS  {label}')


check(
    'verba com quantidade zero assume uma unidade',
    normalizar_quantidade_item_orcamento('vb', 0, preco_mao_obra=4000, preco_material=4000) == 1,
)
check(
    'verba preserva quantidade positiva',
    normalizar_quantidade_item_orcamento('vb', 2, preco_unitario=100) == 2,
)
check(
    'item sem preço pode permanecer a planejar',
    normalizar_quantidade_item_orcamento('un', 0) == 0,
)

try:
    normalizar_quantidade_item_orcamento('m²', 0, preco_material=150)
except ValueError as exc:
    check('item com preço exige quantidade positiva', 'quantidade maior que zero' in str(exc))
else:
    raise AssertionError('item com preço e quantidade zero deveria ser rejeitado')

try:
    normalizar_quantidade_item_orcamento('un', -1)
except ValueError:
    check('quantidade negativa é rejeitada', True)
else:
    raise AssertionError('quantidade negativa deveria ser rejeitada')

print('OK: regras de quantidade do orçamento validadas.')
