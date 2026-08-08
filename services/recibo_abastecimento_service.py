"""Leitura do comprovante de abastecimento (cupom fiscal do posto) via Anthropic.

Recebe a foto ou o PDF que o motorista anexou no link público e devolve os
campos do cupom em JSON — litros, preço por litro, valor total, posto, data e
combustível. O motorista confere e corrige antes de enviar; nada aqui persiste.

Saída garantida por structured outputs (`output_config.format`), então o JSON
volta no formato do schema sem cerca de markdown. O parse tolerante fica como
rede de segurança para respostas de modelos que não honrem o schema.

O `valor_total` é sempre reconferido contra litros × preço no chamador — cupom
amassado ou foto tremida erra dígito com facilidade.
"""
import os
import io
import re
import json
import base64
import logging

logger = logging.getLogger(__name__)

_MODEL = os.environ.get('FROTA_RECIBO_MODEL', 'claude-opus-5')
_MAX_BYTES = 8 * 1024 * 1024          # cupom é foto de celular; 8 MB é folga
_IMAGENS = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
_PDF = 'application/pdf'

_SCHEMA = {
    'type': 'object',
    'properties': {
        'litros': {'type': ['number', 'null'], 'description': 'Litros abastecidos'},
        'preco_litro': {'type': ['number', 'null'], 'description': 'Preço por litro em R$'},
        'valor_total': {'type': ['number', 'null'], 'description': 'Valor total pago em R$'},
        'posto': {'type': ['string', 'null'], 'description': 'Nome do posto ou razão social'},
        'data': {'type': ['string', 'null'], 'description': 'Data do abastecimento (YYYY-MM-DD)'},
        'combustivel': {
            'type': ['string', 'null'],
            'description': "Combustível: gasolina, etanol, diesel, diesel s10, gnv ou null",
        },
        'km': {'type': ['integer', 'null'], 'description': 'KM/odômetro, se impresso no cupom'},
        'confianca': {'type': 'string', 'enum': ['alta', 'baixa']},
    },
    'required': ['litros', 'preco_litro', 'valor_total', 'posto', 'data',
                 'combustivel', 'km', 'confianca'],
    'additionalProperties': False,
}

_PROMPT = (
    "Este é o comprovante de um abastecimento de veículo (cupom fiscal ou "
    "recibo de posto de combustível brasileiro). Extraia os dados do "
    "abastecimento.\n"
    "Regras: números em ponto decimal, sem 'R$' e sem separador de milhar "
    "(2.640,50 vira 2640.50). data no formato YYYY-MM-DD. Use null em todo "
    "campo que não estiver legível ou não aparecer no comprovante — nunca "
    "estime, calcule ou invente um valor ausente. Se o cupom tiver vários "
    "abastecimentos, use o de maior valor. confianca é 'baixa' quando a "
    "imagem estiver cortada, borrada ou com dígitos ambíguos."
)


def media_type(arquivo):
    """mimetype do upload, com fallback pela extensão (celular às vezes manda
    application/octet-stream)."""
    mt = (getattr(arquivo, 'mimetype', None) or '').lower().split(';')[0].strip()
    if mt in _IMAGENS or mt == _PDF:
        return mt
    nome = (getattr(arquivo, 'filename', '') or '').lower()
    if nome.endswith('.pdf'):
        return _PDF
    if nome.endswith('.png'):
        return 'image/png'
    if nome.endswith('.webp'):
        return 'image/webp'
    if nome.endswith(('.jpg', '.jpeg')):
        return 'image/jpeg'
    return mt or 'application/octet-stream'


def ler_bytes(arquivo):
    """Lê o upload e devolve os bytes, rebobinando para o upload seguinte."""
    dados = arquivo.read()
    try:
        arquivo.seek(0)
    except Exception:
        pass
    return dados


def _parse_json(raw):
    """Parse tolerante: remove cercas ```json e recorta o primeiro objeto."""
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


def _num(valor):
    """Aceita número ou string ('2.640,50' | 'R$ 90,00') → float. None se vazio."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip().replace('R$', '').replace(' ', '')
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        return float(s)
    except Exception:
        return None


def _positivo(valor, teto):
    """Descarta zero, negativo e absurdo de OCR (dígito a mais no cupom)."""
    n = _num(valor)
    if n is None or n <= 0 or n > teto:
        return None
    return n


def _data_iso(valor):
    """'2026-08-07' | '07/08/2026' → 'YYYY-MM-DD'. None se não reconhecer."""
    s = str(valor or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        return s
    m = re.fullmatch(r'(\d{2})[/-](\d{2})[/-](\d{4})', s)
    if m:
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    return None


def _normalizar(dados):
    """Sanitiza a saída do modelo — o motorista revisa, mas o payload que
    chega ao frontend não pode carregar lixo (string em campo numérico,
    valores negativos, data em formato livre)."""
    if not isinstance(dados, dict):
        return {}
    combustivel = (dados.get('combustivel') or '')
    if not isinstance(combustivel, str):
        combustivel = ''
    return {
        'litros': _positivo(dados.get('litros'), 2000),
        'preco_litro': _positivo(dados.get('preco_litro'), 100),
        'valor_total': _positivo(dados.get('valor_total'), 100000),
        'posto': (str(dados.get('posto')).strip()[:160]
                  if dados.get('posto') else None),
        'data': _data_iso(dados.get('data')),
        'combustivel': combustivel.strip().lower()[:20] or None,
        'km': int(dados['km']) if isinstance(dados.get('km'), int) and dados['km'] > 0 else None,
        'confianca': 'baixa' if dados.get('confianca') != 'alta' else 'alta',
    }


def _bloco_documento(media_type, dados_b64):
    if media_type == _PDF:
        return {'type': 'document',
                'source': {'type': 'base64', 'media_type': _PDF, 'data': dados_b64}}
    return {'type': 'image',
            'source': {'type': 'base64', 'media_type': media_type, 'data': dados_b64}}


def extrair_dados_recibo(arquivo):
    """Lê o comprovante e devolve os campos reconhecidos.

    Levanta ValueError para problema do arquivo (tipo/tamanho) — vira 400 na
    rota — e RuntimeError quando o serviço de leitura não está disponível.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY não configurada — leitura de comprovante indisponível.')

    tipo = media_type(arquivo)
    if tipo not in _IMAGENS and tipo != _PDF:
        raise ValueError('Envie uma foto (JPG, PNG ou WEBP) ou um PDF do comprovante.')

    dados = ler_bytes(arquivo)
    if not dados:
        raise ValueError('Arquivo vazio.')
    if len(dados) > _MAX_BYTES:
        raise ValueError('Arquivo muito grande (máximo 8 MB). Tire a foto com resolução menor.')

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=_MODEL,
        max_tokens=2000,
        output_config={
            'effort': 'low',  # leitura de cupom não exige raciocínio profundo
            'format': {'type': 'json_schema', 'schema': _SCHEMA},
        },
        messages=[{
            'role': 'user',
            'content': [
                _bloco_documento(tipo, base64.b64encode(dados).decode('ascii')),
                {'type': 'text', 'text': _PROMPT},
            ],
        }],
    )
    if msg.stop_reason == 'refusal':
        logger.warning("recibo: leitura recusada pelo modelo (%s)", msg.stop_reason)
        raise RuntimeError('Não foi possível ler este comprovante automaticamente.')

    raw = next((b.text for b in msg.content if getattr(b, 'type', None) == 'text'), '')
    return _normalizar(_parse_json(raw))


def comprimir_imagem(dados, media_type, max_lado=1600):
    """Reduz a foto antes de subir ao Storage (celular manda 4 MB por cupom).

    Best-effort: qualquer falha devolve o original — o comprovante nunca se
    perde por causa da compressão.
    """
    if media_type == _PDF:
        return dados, media_type
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(dados))
        img = img.convert('RGB')
        if max(img.size) > max_lado:
            escala = max_lado / max(img.size)
            img = img.resize((int(img.width * escala), int(img.height * escala)))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=82, optimize=True)
        return buf.getvalue(), 'image/jpeg'
    except Exception as e:
        logger.warning("recibo: compressão falhou, subindo original: %s", e)
        return dados, media_type
