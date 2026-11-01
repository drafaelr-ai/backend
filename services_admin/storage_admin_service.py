"""Supabase Storage do app ADMIN — upload a bucket privado.

Por que é um módulo separado de `services/storage_service.py`, e não um
import: o `Dockerfile.admin` copia só `app_admin.py`, `auto_migration_admin.py`,
`config_admin.py`, `extensions_admin.py`, `logging_setup.py` e as pastas
`services_admin/`, `models_admin/`, `routes_admin/`. A pasta `services/` não
entra na imagem, então o app admin não consegue importar de lá. A duplicação
é imposta pelo empacotamento, não escolhida.

E o banco é outro: o admin vive num projeto Supabase separado, com
`SUPABASE_URL_ADMIN` e `SUPABASE_SERVICE_KEY_ADMIN` próprias.

`disponivel()` existe para o deploy ser seguro: enquanto o bucket e as
credenciais não estiverem configurados, quem chama continua gravando base64
como antes. Deployar esta mudança sem configurar nada não muda comportamento
nenhum — e é isso que permite ela viver na main esperando o deploy manual do
admin, que exige pg_dump e auditoria de contagens.
"""

import base64
import binascii
import logging
import os
import uuid

import requests
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

BUCKET_BOLETOS_ADMIN = 'admin-boletos'


def _base_url():
    url = os.environ.get('SUPABASE_URL_ADMIN') or os.environ.get('SUPABASE_URL')
    return url.rstrip('/') if url else None


def _service_key():
    return (os.environ.get('SUPABASE_SERVICE_KEY_ADMIN')
            or os.environ.get('SUPABASE_SERVICE_KEY'))


def disponivel():
    """True só quando há URL e chave para o Storage do admin."""
    return bool(_base_url() and _service_key())


def _headers(extra=None):
    key = _service_key()
    if not key:
        raise RuntimeError(
            'SUPABASE_SERVICE_KEY_ADMIN não configurada — Storage do admin indisponível.'
        )
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}
    if extra:
        h.update(extra)
    return h


def ensure_bucket(bucket=BUCKET_BOLETOS_ADMIN):
    """Cria o bucket PRIVADO se ainda não existir. Idempotente."""
    try:
        resp = requests.post(
            f'{_base_url()}/storage/v1/bucket',
            headers=_headers({'Content-Type': 'application/json'}),
            json={'name': bucket, 'id': bucket, 'public': False},
            timeout=20,
        )
    except requests.RequestException:
        logger.exception('storage admin: falha ao criar o bucket %s', bucket)
        return False
    if resp.status_code in (200, 201):
        logger.info('storage admin: bucket %s criado', bucket)
        return True
    # 409 = já existe, que é sucesso para o nosso propósito.
    if resp.status_code == 409 or 'already exists' in resp.text.lower():
        return True
    logger.error('storage admin: bucket %s não criado (%s): %s',
                 bucket, resp.status_code, resp.text[:200])
    return False


def _bucket_nao_encontrado(resp):
    return resp.status_code == 404 or 'bucket not found' in resp.text.lower()


def _decodificar(conteudo, max_bytes=None):
    texto = str(conteudo)
    if ',' in texto and texto.strip().lower().startswith('data:'):
        texto = texto.split(',', 1)[1]
    elif ',' in texto[:120]:
        texto = texto.split(',', 1)[1]
    try:
        dados = base64.b64decode(texto, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError('Base64 inválido') from exc
    if not dados:
        raise ValueError('Conteúdo vazio após decodificar')
    if max_bytes and len(dados) > max_bytes:
        raise ValueError(
            f'Arquivo tem {len(dados) // 1024} KB, acima do limite de '
            f'{max_bytes // 1024} KB',
        )
    return dados


def upload_base64(conteudo, pasta, nome='arquivo', bucket=BUCKET_BOLETOS_ADMIN,
                  content_type='application/pdf', max_bytes=None):
    """Sobe conteúdo base64 (inclusive data URL) e devolve o path no bucket."""
    if not conteudo:
        raise ValueError('Conteúdo vazio')
    dados = _decodificar(conteudo, max_bytes=max_bytes)

    filename = secure_filename(nome or 'arquivo') or 'arquivo'
    path = f'{pasta}/{uuid.uuid4().hex}_{filename}'
    url = f'{_base_url()}/storage/v1/object/{bucket}/{path}'
    headers = _headers({'Content-Type': content_type, 'x-upsert': 'true'})
    resp = requests.post(url, headers=headers, data=dados, timeout=60)
    if _bucket_nao_encontrado(resp) and ensure_bucket(bucket):
        resp = requests.post(url, headers=headers, data=dados, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f'Upload falhou ({resp.status_code}): {resp.text[:200]}')
    logger.info('storage admin: upload OK -> %s/%s (%s bytes)', bucket, path, len(dados))
    return path


def signed_url(path, expires=3600, bucket=BUCKET_BOLETOS_ADMIN):
    """URL assinada de curta duração para um path do bucket."""
    if not path:
        return None
    resp = requests.post(
        f'{_base_url()}/storage/v1/object/sign/{bucket}/{path}',
        headers=_headers({'Content-Type': 'application/json'}),
        json={'expiresIn': expires},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'signed_url falhou ({resp.status_code}): {resp.text[:200]}')
    corpo = resp.json()
    assinada = corpo.get('signedURL') or corpo.get('signedUrl')
    if not assinada:
        raise RuntimeError('signed_url: resposta sem signedURL')
    return f'{_base_url()}/storage/v1{assinada}'
