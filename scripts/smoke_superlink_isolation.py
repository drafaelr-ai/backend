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

# =====================================================================
# HTTP-LEVEL (Main) — rota pública REAL via test_client.
# Prova: (4) GET /superlink/<token> SEM token funciona e não vaza;
#        (5) gerar com 3 boletos -> página mostra EXATAMENTE 3, não todos
#            os boletos da obra; e isolamento entre dois tokens distintos.
# Sem DB real: monkeypatch do model (registry token->superlink) + db stub.
# =====================================================================
from flask import Flask


class _FakeSL:
    """Stand-in de uma linha Superlink (só o que a rota lê)."""
    def __init__(self, token, grupo_id, refs, itens):
        self.token = token
        self.grupo_id = grupo_id
        self.refs = refs
        self.itens = itens
        self.titulo = 'Cobrancas'
        from datetime import datetime, timedelta
        self.expira_em = datetime.utcnow() + timedelta(days=5)

    def is_expirado(self):
        return False


def _install_fake_model(module, registry):
    """Substitui module.Superlink por um fake cujo .query.filter_by(token=).first()
    devolve o superlink do registry (ou None)."""
    class _Query:
        def filter_by(self, **kw):
            self._tok = kw.get('token')
            return self

        def first(self):
            return registry.get(self._tok)

    class _FakeModel:
        query = _Query()

    module.Superlink = _FakeModel


# Obra 7: boletos 100,101,102 pendentes no banco; só 100,101,102? Não —
# o link gera com 3 selecionados (700,701,702). A obra tem AINDA outros
# boletos (800,801) que NÃO entram no link. Provamos que só os 3 aparecem.
status_http = {
    ('boleto', 700): 'Pendente',
    ('boleto', 701): 'Pendente',
    ('boleto', 702): 'Pendente',
    ('boleto', 800): 'Pendente',   # existe na obra 7, NÃO selecionado
    ('boleto', 801): 'Pendente',   # existe na obra 7, NÃO selecionado
    ('boleto', 110): 'Pendente',   # boleto da OBRA 1 (link da obra 1)
    ('boleto', 900): 'Pendente',   # boleto da OBRA 2 — NUNCA deve aparecer no link da obra 1
}
sl_main.db = make_fake_db(status_http)

link3_itens = [
    {'descricao': 'Boleto 700', 'valor': 70.0, 'forma': 'boleto', 'codigo_barras': 'C700'},
    {'descricao': 'Boleto 701', 'valor': 71.0, 'forma': 'boleto', 'codigo_barras': 'C701'},
    {'descricao': 'Boleto 702', 'valor': 72.0, 'forma': 'boleto', 'codigo_barras': 'C702'},
]
link3_refs = [{'tabela': 'boleto', 'id': 700}, {'tabela': 'boleto', 'id': 701}, {'tabela': 'boleto', 'id': 702}]

# Segundo link na MESMA obra com seleção diferente (só 800)
linkOutro_itens = [{'descricao': 'Boleto 800', 'valor': 80.0, 'forma': 'boleto', 'codigo_barras': 'C800'}]
linkOutro_refs = [{'tabela': 'boleto', 'id': 800}]

# Link da OBRA 1: selecionou só o boleto 110 (da obra 1). O boleto 900 (obra 2)
# existe no banco mas é de outra obra — não pode vazar pra cá de jeito nenhum.
linkObra1_itens = [{'descricao': 'Boleto 110', 'valor': 11.0, 'forma': 'boleto', 'codigo_barras': 'C110'}]
linkObra1_refs = [{'tabela': 'boleto', 'id': 110}]

registry = {
    'TOKEN_3BOLETOS': _FakeSL('TOKEN_3BOLETOS', 7, link3_refs, link3_itens),
    'TOKEN_OUTRO':    _FakeSL('TOKEN_OUTRO', 7, linkOutro_refs, linkOutro_itens),
    'TOKEN_OBRA1':    _FakeSL('TOKEN_OBRA1', 1, linkObra1_refs, linkObra1_itens),
}
_install_fake_model(sl_main, registry)

app = Flask(__name__)
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
from flask_jwt_extended import JWTManager
JWTManager(app)  # necessário p/ @jwt_required() no POST devolver 401 (não 500)
app.register_blueprint(sl_main.superlink_bp)
client = app.test_client()

# (4) GET SEM token (sem header Authorization) deve funcionar (200)
r3 = client.get('/superlink/TOKEN_3BOLETOS')
check('HTTP GET público SEM token responde 200', r3.status_code == 200)

body3 = r3.get_json() or {}
descr3 = sorted(i.get('descricao') for i in body3.get('itens', []))

# (5) gerou com 3 boletos → mostra EXATAMENTE 3
check('HTTP gerou 3 boletos -> mostra exatamente 3', len(body3.get('itens', [])) == 3)
check('HTTP os 3 são {700,701,702}', descr3 == ['Boleto 700', 'Boleto 701', 'Boleto 702'])
check('HTTP NÃO vaza boletos 800/801 da obra',
      all(('800' not in d and '801' not in d) for d in descr3))
check('HTTP valor_total = soma só dos 3', abs(body3.get('valor_total', 0) - 213.0) < 0.01)

# Isolamento entre tokens: o outro link mostra só o seu item
rO = client.get('/superlink/TOKEN_OUTRO')
descrO = sorted(i.get('descricao') for i in (rO.get_json() or {}).get('itens', []))
check('HTTP token distinto isola seleção (só 800)', descrO == ['Boleto 800'])

# (3) CROSS-OBRA: link da obra 1 retorna SÓ o boleto 110 e NUNCA o 900 da obra 2
rObra1 = client.get('/superlink/TOKEN_OBRA1')
descrObra1 = sorted(i.get('descricao') for i in (rObra1.get_json() or {}).get('itens', []))
check('HTTP cross-obra: link da obra 1 mostra só {110}', descrObra1 == ['Boleto 110'])
check('HTTP cross-obra: link da obra 1 NÃO vaza boleto 900 da obra 2',
      all('900' not in d for d in descrObra1))

# Token inexistente → 404 (não vaza nada)
r404 = client.get('/superlink/NAO_EXISTE')
check('HTTP token inexistente -> 404', r404.status_code == 404)

# POST (geração) continua exigindo token → 401 sem auth
rPost = client.post('/superlink', json={'titulo': 'x', 'itens': []})
check('HTTP POST geração SEM token -> 401 (continua protegido)', rPost.status_code == 401)

print(f"\n{'='*44}")
print(f"Resultado: {PASS} PASS  {FAIL} FAIL")
print('='*44)
sys.exit(0 if FAIL == 0 else 1)
