"""Supabase Storage — upload de arquivos a buckets privados.

Buckets: `rh-arquivos` (RH, default de compatibilidade) e `frota-arquivos` (Frota).
Não armazena blob no Postgres (lição B-04): sobe pro Storage e guarda só o path.
Usa a REST do Storage direto via `requests` (SUPABASE_URL + SUPABASE_SERVICE_KEY),
evitando a dependência pesada do supabase-py.
"""
import os
import uuid
import logging

import requests
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

BUCKET = 'rh-arquivos'
_DEFAULT_URL = 'https://kwmuiviyqjcxawuiqkrl.supabase.co'


def _base_url():
    return (os.environ.get('SUPABASE_URL') or _DEFAULT_URL).rstrip('/')


def _service_key():
    key = os.environ.get('SUPABASE_SERVICE_KEY')
    if not key:
        raise RuntimeError(
            'SUPABASE_SERVICE_KEY não configurada — necessária para o Storage do RH.'
        )
    return key


def _headers(extra=None):
    key = _service_key()
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}
    if extra:
        h.update(extra)
    return h


def ensure_bucket(bucket=BUCKET):
    """Cria o bucket privado se ainda não existir (idempotente)."""
    url = f'{_base_url()}/storage/v1/bucket'
    try:
        resp = requests.post(
            url,
            headers=_headers({'Content-Type': 'application/json'}),
            json={'id': bucket, 'name': bucket, 'public': False},
            timeout=20,
        )
        if resp.status_code in (200, 201):
            logger.info("storage: bucket '%s' criado", bucket)
            return True
        # 400/409 = já existe → idempotente
        if resp.status_code in (400, 409) and 'exist' in resp.text.lower():
            logger.info("storage: bucket '%s' já existe", bucket)
            return True
        logger.warning("storage: ensure_bucket status %s: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.exception("storage: falha ao garantir bucket: %s", e)
        return False


def upload_arquivo(file, pasta, bucket=BUCKET):
    """Sobe um arquivo (werkzeug FileStorage) ao bucket, retorna o path salvo.

    `pasta` é o subdiretório lógico (ex.: 'convencoes', 'comprovantes', 'guias').
    Se o bucket ainda não existir (módulos novos, criação lazy), cria e tenta
    uma segunda vez.
    """
    filename = secure_filename(getattr(file, 'filename', '') or 'arquivo')
    path = f'{pasta}/{uuid.uuid4().hex}_{filename}'
    content_type = getattr(file, 'mimetype', None) or 'application/octet-stream'

    data = file.read()
    if hasattr(file, 'seek'):
        try:
            file.seek(0)
        except Exception:
            pass

    url = f'{_base_url()}/storage/v1/object/{bucket}/{path}'
    headers = _headers({'Content-Type': content_type, 'x-upsert': 'true'})
    resp = requests.post(url, headers=headers, data=data, timeout=60)
    if resp.status_code == 404 and 'bucket' in resp.text.lower():
        # Bucket ainda não existe — criação lazy + 1 retry.
        if ensure_bucket(bucket):
            resp = requests.post(url, headers=headers, data=data, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f'Upload falhou ({resp.status_code}): {resp.text[:200]}')
    logger.info("storage: upload OK -> %s/%s", bucket, path)
    return path


def signed_url(path, expires=3600, bucket=BUCKET):
    """Gera URL assinada de curta duração para um path do bucket."""
    if not path:
        return None
    url = f'{_base_url()}/storage/v1/object/sign/{bucket}/{path}'
    resp = requests.post(
        url,
        headers=_headers({'Content-Type': 'application/json'}),
        json={'expiresIn': expires},
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'signed_url falhou ({resp.status_code}): {resp.text[:200]}')
    signed = resp.json().get('signedURL') or resp.json().get('signedUrl')
    if not signed:
        raise RuntimeError('signed_url: resposta sem signedURL')
    return f'{_base_url()}/storage/v1{signed}'
