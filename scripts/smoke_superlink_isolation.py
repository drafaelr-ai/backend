"""Smoke test — Superlink ISOLAMENTO DA SELEÇÃO (Main + Admin)

Prova o fix do vazamento: a rota pública mostra SOMENTE os itens selecionados
na geração (lista fixa via refs/snapshot), relendo apenas o STATUS ao vivo —
NUNCA puxa todos os boletos da obra/imóvel.

Não depende de dados reais: faz stub de db.session.execute para controlar o
status retornado por (tabela, id). Zero FAIL = seleção isolada e sem vazamento.
"""
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import routes.superlink as sl_main
import routes_admin.superlink_admin as sl_admin

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if condition:
        PASS += 1
    else:
        FAIL += 1


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def make_fake_db(status_by_key):
    """status_by_key: dict[(tabela, id)] -> status string (ou None = não existe).

    Retorna um objeto com .text() e .session.execute() compatíveis com o uso real.
    """
    class _Sess:
        def execute(self, textclause, params):
            sql = str(textclause)
            # extrai "FROM <tabela>"
            tabela = sql.split('FROM', 1)[1].strip().split()[0]
            rid = params.get('id')
            status = status_by_key.get((tabela, rid), '__MISSING__')
            if status == '__MISSING__':
                return _FakeResult(None)        # linha não existe no banco
            return _FakeResult((status,))

    class _DB:
        session = _Sess()

        @staticmethod
        def text(s):
            return s

    return _DB()


def descricoes(itens):
    return sorted(i.get('descricao') for i in itens)


# =====================================================================
# MAIN — dois links na MESMA obra, seleções diferentes
# =====================================================================
# Obra 7 tem boletos id 100 (pendente), 101 (pendente), 102 (pendente, NÃO selecionado)
status_main = {
    ('boleto', 100): 'Pendente',
    ('boleto', 101): 'Pendente',
    ('boleto', 102): 'Pendente',   # existe na obra mas NÃO está em nenhum link
}
sl_main.db = make_fake_db(status_main)

# Link A: selecionou só boletos 100 e 101
linkA_itens = [
    {'descricao': 'Boleto 100', 'valor': 100.0, 'forma': 'boleto', 'codigo_barras': 'A100'},
    {'descricao': 'Boleto 101', 'valor': 200.0, 'forma': 'boleto', 'codigo_barras': 'A101'},
]
linkA_refs = [{'tabela': 'boleto', 'id': 100}, {'tabela': 'boleto', 'id': 101}]

# Link B: mesma obra, selecionou só boleto 102
linkB_itens = [
    {'descricao': 'Boleto 102', 'valor': 300.0, 'forma': 'boleto', 'codigo_barras': 'B102'},
]
linkB_refs = [{'tabela': 'boleto', 'id': 102}]

resA = sl_main._itens_dinamicos(7, linkA_refs, linkA_itens)
resB = sl_main._itens_dinamicos(7, linkB_refs, linkB_itens)

check('MAIN link A mostra só {100,101}', descricoes(resA) == ['Boleto 100', 'Boleto 101'])
check('MAIN link A NÃO vaza boleto 102 da obra', all('102' not in d for d in descricoes(resA)))
check('MAIN link B mostra só {102}', descricoes(resB) == ['Boleto 102'])
check('MAIN link B NÃO vaza 100/101', descricoes(resB) == ['Boleto 102'])

# Status ao vivo: boleto 101 vira Pago → some do link A
status_main[('boleto', 101)] = 'Pago'
resA2 = sl_main._itens_dinamicos(7, linkA_refs, linkA_itens)
check('MAIN status ao vivo: boleto pago some da lista', descricoes(resA2) == ['Boleto 100'])

# Item de outra obra nunca entra (ref aponta para id de obra diferente, mas
# a lista é a seleção — provamos que grupo_id não puxa nada extra):
resA3 = sl_main._itens_dinamicos(999, linkA_refs, linkA_itens)
check('MAIN grupo_id diferente não altera a lista (sem re-query por obra)',
      descricoes(resA3) == ['Boleto 100'])  # 101 ainda Pago

# Legado: link sem refs → snapshot filtrado (só selecionados, sem 'pago')
legado = sl_main._itens_dinamicos(7, None,
                                  [{'descricao': 'L1', 'valor': 1}, {'descricao': 'L2', 'valor': 2, 'pago': True}])
check('MAIN legado sem refs: snapshot filtrado (remove pago)', descricoes(legado) == ['L1'])

# =====================================================================
# ADMIN — mesma prova com admin_boleto / admin_lancamento
# =====================================================================
status_admin = {
    ('admin_boleto', 500): 'Pendente',
    ('admin_lancamento', 600): 'pendente',
    ('admin_boleto', 599): 'Pendente',   # outro boleto do imóvel, não selecionado
}
sl_admin.db = make_fake_db(status_admin)

admin_itens = [
    {'descricao': 'AdmBoleto 500', 'valor': 50.0, 'forma': 'boleto', 'codigo_barras': 'X500'},
    {'descricao': 'AdmLanc 600', 'valor': 60.0, 'forma': 'pix', 'pix_chave': 'k@x'},
]
admin_refs = [{'tabela': 'admin_boleto', 'id': 500}, {'tabela': 'admin_lancamento', 'id': 600}]

resAdmin = sl_admin._itens_dinamicos_admin(42, admin_refs, admin_itens)
check('ADMIN mostra só selecionados {500,600}', descricoes(resAdmin) == ['AdmBoleto 500', 'AdmLanc 600'])
check('ADMIN NÃO vaza admin_boleto 599 do imóvel',
      all('599' not in d for d in descricoes(resAdmin)))

# Status ao vivo: lançamento 600 cancelado → some
status_admin[('admin_lancamento', 600)] = 'cancelado'
resAdmin2 = sl_admin._itens_dinamicos_admin(42, admin_refs, admin_itens)
check('ADMIN status ao vivo: cancelado some da lista', descricoes(resAdmin2) == ['AdmBoleto 500'])

print(f"\n{'='*44}")
print(f"Resultado: {PASS} PASS  {FAIL} FAIL")
print('='*44)
sys.exit(0 if FAIL == 0 else 1)
