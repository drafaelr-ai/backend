"""Helpers para vínculo de pagamentos/lançamentos a itens de orçamento (orcamento_eng_item)."""
import logging

from models.orcamento_eng_item import OrcamentoEngItem

logger = logging.getLogger(__name__)


def resolver_orcamento_item_id(valor):
    """Valida o orcamento_item_id recebido do cliente antes de gravar.

    Retorna uma tupla (id_normalizado, mensagem_erro):
      - valor vazio/None  -> (None, None)   # desvincula, sem erro
      - valor inválido     -> (None, "msg")  # não-inteiro OU item inexistente -> chamador deve retornar 400
      - valor válido       -> (int, None)

    Substitui o antigo UPDATE cru com f-string (SQL injection + erro engolido).
    Aqui o erro é EXPLÍCITO: o handler pode retornar 400 em vez de 200 silencioso.
    """
    if valor in (None, '', 'null'):
        return None, None
    try:
        oid = int(valor)
    except (ValueError, TypeError):
        # Ex.: frontend mandando o codigo "18.01" em vez do id -> rejeita explicitamente
        return None, f"orcamento_item_id inválido: {valor!r} (esperado id inteiro de item de orçamento)"
    if OrcamentoEngItem.query.get(oid) is None:
        return None, f"Item de orçamento {oid} não existe"
    return oid, None
