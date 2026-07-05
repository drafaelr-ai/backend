"""Extração de convenção coletiva (CCT) de PDF via Anthropic.

Fluxo: pdfplumber extrai o texto → pré-filtro por palavras-chave se muito longo
→ Anthropic devolve SÓ JSON de categorias/pisos/benefícios → parse seguro.
NÃO persiste nada; só retorna o JSON para revisão no frontend.
"""
import io
import os
import re
import json
import logging

logger = logging.getLogger(__name__)

_MODEL = os.environ.get('RH_PARSER_MODEL', 'claude-sonnet-4-6')
_MAX_CHARS = 40000
_KEYWORDS = re.compile(
    r'piso|sal[aá]r|remuner|categoria|fun[cç][aã]o|vale|cesta|R\$',
    re.IGNORECASE,
)

_PROMPT = (
    "Você recebe o texto de uma convenção coletiva de trabalho (CCT) da "
    "construção civil. Extraia as categorias profissionais com seus pisos "
    "salariais mensais e benefícios. Responda SOMENTE com JSON válido, sem "
    "texto fora do JSON, no formato exato:\n"
    '{ "categorias": [ { "nome": "Pedreiro", "piso_salarial": 2640.00, '
    '"beneficios": [ {"tipo":"vale_refeicao","descricao":"Vale-refeição",'
    '"valor":22.0,"unidade":"dia"} ], "confianca": "alta" } ] }\n'
    "Regras: piso_salarial é número (sem 'R$', ponto decimal). unidade ∈ "
    "{mes, dia, unico}. confianca ∈ {alta, baixa} por categoria (use 'baixa' "
    "quando o piso não estiver claro no texto). Se não encontrar benefícios, "
    "use lista vazia. Não invente categorias que não estão no texto.\n\n"
    "TEXTO DA CONVENÇÃO:\n"
)


def extrair_texto(file):
    """Extrai texto do PDF (werkzeug FileStorage ou bytes)."""
    import pdfplumber
    data = file.read() if hasattr(file, 'read') else file
    if hasattr(file, 'seek'):
        try:
            file.seek(0)
        except Exception:
            pass
    texto = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            texto.append(page.extract_text() or '')
    return '\n'.join(texto)


def _prefiltrar(texto):
    """Se o texto for grande, retém só as linhas com palavras-chave (reduz custo)."""
    if len(texto) <= _MAX_CHARS:
        return texto
    linhas = [ln for ln in texto.splitlines() if _KEYWORDS.search(ln)]
    reduzido = '\n'.join(linhas)
    logger.info("cct_parser: pré-filtro %d -> %d chars", len(texto), len(reduzido))
    return reduzido[:_MAX_CHARS] if reduzido else texto[:_MAX_CHARS]


def _parse_json(raw):
    """Parse seguro: remove cercas ```json e recorta o primeiro objeto {...}."""
    txt = (raw or '').strip()
    if txt.startswith('```'):
        txt = re.sub(r'^```(?:json)?\s*', '', txt)
        txt = re.sub(r'\s*```$', '', txt)
    try:
        return json.loads(txt)
    except Exception:
        ini, fim = txt.find('{'), txt.rfind('}')
        if ini != -1 and fim != -1 and fim > ini:
            return json.loads(txt[ini:fim + 1])
        raise


def parse_cct(file):
    """Recebe o PDF, roda o parser e retorna {"categorias": [...]}. Não persiste."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY não configurada — necessária para o parser de CCT.')

    texto = _prefiltrar(extrair_texto(file))
    if not texto.strip():
        return {'categorias': []}

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=_MODEL,
        max_tokens=4000,
        messages=[{'role': 'user', 'content': _PROMPT + texto}],
    )
    raw = msg.content[0].text if msg.content else ''
    resultado = _parse_json(raw)

    if not isinstance(resultado, dict) or 'categorias' not in resultado:
        logger.warning("cct_parser: JSON sem 'categorias'; retornando vazio")
        return {'categorias': []}
    return resultado
