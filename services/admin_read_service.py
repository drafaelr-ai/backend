"""Leitura READ-ONLY do banco admin (patrimônio) para o módulo Frota.

O banco admin é um Supabase separado; a conexão vem de DATABASE_URL_ADMIN
(mesma URL usada como secret no app obraly-admin-api). Somente SELECT em
admin_imovel — nunca escreve. Degradação graciosa: env ausente ou falha de
conexão retornam lista vazia + aviso, nunca exceção/500.
"""
import os
import time
import logging

import psycopg2

logger = logging.getLogger(__name__)

_AVISO_SEM_CONFIG = (
    'Lista de imóveis indisponível (integração com o patrimônio não configurada).'
)
_AVISO_FALHA = 'Não foi possível carregar os imóveis do patrimônio agora.'

# Cache simples em módulo (TTL 60s) — o dropdown abre várias vezes por sessão
# e a lista muda raramente; evita 1 conexão TCP ao Supabase por clique.
# Só cacheia sucesso (falha tenta de novo na próxima chamada).
_CACHE_TTL = 60
_cache = {'ts': 0.0, 'data': None}


def listar_imoveis():
    """Retorna (imoveis, aviso). `imoveis` é lista de dicts; `aviso` é None ou texto."""
    url = os.environ.get('DATABASE_URL_ADMIN')
    if not url:
        logger.warning('admin_read: DATABASE_URL_ADMIN não configurada')
        return [], _AVISO_SEM_CONFIG

    if _cache['data'] is not None and time.time() - _cache['ts'] < _CACHE_TTL:
        return _cache['data'], None

    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            cur = conn.cursor()
            cur.execute("SET statement_timeout = '10s';")
            cur.execute("""
                SELECT id, nome, endereco, cidade, estado, tipo, status
                FROM admin_imovel
                WHERE ativo = TRUE
                ORDER BY nome;
            """)
            colunas = [d[0] for d in cur.description]
            imoveis = [dict(zip(colunas, row)) for row in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
        _cache['data'] = imoveis
        _cache['ts'] = time.time()
        return imoveis, None
    except Exception:
        logger.exception('admin_read: falha ao listar imóveis do banco admin')
        return [], _AVISO_FALHA
