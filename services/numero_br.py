"""Leitura de número escrito à brasileira, em um lugar só.

O mesmo parser estava copiado em doze arquivos, e todas as cópias tinham o
mesmo ponto cego: sem vírgula no texto, o ponto era tratado como decimal.
Resultado — `'1.250'`, que é como se digita mil duzentos e cinquenta,
virava `1.25`. Sem erro nenhum, com o valor mil vezes menor no banco.

A regra depende do que o número significa, e é por isso que `milhar` existe:

- Dinheiro (`milhar=True`, padrão): `'1.250'` é 1250. Ninguém digita
  R$ 1,25 como "1.250", e um valor de mil reais escrito assim é comum.
- Preço unitário com três decimais (`milhar=False`): `'5.899'` é 5.899.
  Combustível a R$ 5,899 por litro é o normal; R$ 5.899 por litro é
  absurdo. Aqui o ponto é sempre decimal.

Com vírgula presente o caso é inequívoco nos dois modos: a vírgula é o
decimal e o ponto é separador de milhar.
"""

import re

_MILHAR_BR = re.compile(r'-?\d{1,3}(\.\d{3})+')


def para_numero(valor, *, milhar=True):
    """Devolve float, ou None se vazio ou impossível de interpretar.

    Devolver None (em vez de levantar) é de propósito: é o contrato que os
    `_to_num` das rotas já tinham, e quem chama decide se None é erro.
    """
    if valor is None or valor == '':
        return None
    # bool é int em Python: sem esta guarda, `True` viraria 1.0.
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
        return numero if numero == numero and abs(numero) != float('inf') else None

    texto = str(valor).strip().replace('R$', '').replace('r$', '').strip()
    if not texto:
        return None
    texto = texto.replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif milhar and _MILHAR_BR.fullmatch(texto):
        texto = texto.replace('.', '')
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None
    # NaN e infinito passam pelo float() e envenenariam qualquer soma.
    if numero != numero or abs(numero) == float('inf'):
        return None
    return numero


def para_inteiro(valor):
    """Inteiro a partir de texto BR. None se vazio ou inválido."""
    numero = para_numero(valor)
    if numero is None:
        return None
    try:
        return int(numero)
    except (OverflowError, ValueError):
        return None
