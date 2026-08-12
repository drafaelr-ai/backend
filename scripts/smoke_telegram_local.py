"""
Smoke test local do vínculo Telegram — sem banco real (SQLite in-memory) e
sem rede: a Bot API é substituída por um stub em telegram_service.requests.

Uso: cd backend && python scripts/smoke_telegram_local.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401 — registra todos os models no metadata
from models import User, Notificacao, TelegramVinculo
from routes.telegram import telegram_bp
from services import telegram_service
from services.notificacao_service import criar_notificacao

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(telegram_bp)

TABELAS = ['user', 'user_obra_association', 'obra', 'notificacao', 'telegram_vinculo']

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


class _Resp:
    def __init__(self, payload):
        self.ok = True
        self._payload = payload

    def json(self):
        return self._payload


class BotStub:
    """Simula api.telegram.org: getMe, getUpdates e sendMessage."""

    def __init__(self):
        self.updates = []
        self.enviadas = []   # (chat_id, texto)

    def get(self, url, params=None, timeout=None):
        if url.endswith('/getMe'):
            return _Resp({'ok': True, 'result': {'username': 'obraly_smoke_bot'}})
        if url.endswith('/getUpdates'):
            return _Resp({'ok': True, 'result': self.updates})
        return _Resp({'ok': False})

    def post(self, url, json=None, timeout=None):
        if url.endswith('/sendMessage'):
            self.enviadas.append((json['chat_id'], json['text']))
            return _Resp({'ok': True, 'result': {}})
        return _Resp({'ok': False})

    def dar_start(self, code, chat_id=777, nome='Diego'):
        self.updates.append({'message': {
            'text': f'/start {code}',
            'chat': {'id': chat_id, 'first_name': nome},
        }})


stub = BotStub()
telegram_service.requests = stub

# Envio síncrono no smoke: a thread daemon do enviar_async não dá pra
# aguardar de forma determinística.
telegram_service.enviar_async = telegram_service.enviar_sync

with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])
    u1 = User(username='diego_smoke', role='master')
    u1.set_password('smoke123')
    u2 = User(username='sem_telegram_smoke', role='comum')
    u2.set_password('smoke123')
    db.session.add_all([u1, u2])
    db.session.commit()
    u1_id, u2_id = u1.id, u2.id
    h1 = {'Authorization': f'Bearer {create_access_token(identity=str(u1_id))}'}

    with app.test_client() as c:
        print('\n=== kill switch (sem TELEGRAM_BOT_TOKEN) ===')
        os.environ.pop('TELEGRAM_BOT_TOKEN', None)
        r = c.get('/telegram/status', headers=h1)
        check('status sem token -> configurado false', r.status_code == 200
              and json.loads(r.data) == {'configurado': False, 'vinculado': False})
        r = c.post('/telegram/vincular', headers=h1)
        check('vincular sem token -> 400', r.status_code == 400)
        criar_notificacao(u1_id, 'teste', 'Sem bot')
        check('notificação sem bot não tenta enviar', len(stub.enviadas) == 0)

        print('\n=== vínculo ===')
        os.environ['TELEGRAM_BOT_TOKEN'] = 'smoke-token-123'
        r = c.get('/telegram/status', headers=h1)
        st = json.loads(r.data)
        check('status com token -> configurado + bot', st['configurado'] is True
              and st['bot'] == 'obraly_smoke_bot' and st['vinculado'] is False)
        r = c.get('/telegram/status')
        check('status sem JWT -> 401', r.status_code == 401)

        r = c.post('/telegram/confirmar', headers=h1)
        check('confirmar sem gerar link -> 400', r.status_code == 400)

        r = c.post('/telegram/vincular', headers=h1)
        vinc = json.loads(r.data)
        check('vincular -> 200 com deep-link', r.status_code == 200
              and vinc['link'].startswith('https://t.me/obraly_smoke_bot?start='))
        code = vinc['link'].split('start=')[1]

        r = c.post('/telegram/confirmar', headers=h1)
        check('confirmar antes do Start -> 400', r.status_code == 400)

        stub.dar_start('codigo-errado')
        r = c.post('/telegram/confirmar', headers=h1)
        check('Start com código errado -> 400', r.status_code == 400)

        stub.dar_start(code)
        r = c.post('/telegram/confirmar', headers=h1)
        conf = json.loads(r.data)
        check('confirmar após Start -> 200 vinculado', r.status_code == 200
              and conf['vinculado'] is True and conf['chat_nome'] == 'Diego',
              f'got {r.status_code}: {r.data[:200]}')
        check('boas-vindas enviada no chat', len(stub.enviadas) == 1
              and stub.enviadas[0][0] == '777' and 'conectado' in stub.enviadas[0][1])
        r = c.get('/telegram/status', headers=h1)
        st = json.loads(r.data)
        check('status vinculado com chat_nome', st['vinculado'] is True
              and st['chat_nome'] == 'Diego')

        print('\n=== espelho das notificações do sino ===')
        stub.enviadas.clear()
        n = criar_notificacao(u1_id, 'solicitacao_mencao',
                              '💬 Diego mencionou você na solicitação #8',
                              'Confere a medida?')
        check('notificação criada no sino', n is not None
              and Notificacao.query.count() == 2)
        check('notificação espelhada no Telegram', len(stub.enviadas) == 1
              and stub.enviadas[0][0] == '777'
              and 'mencionou você' in stub.enviadas[0][1]
              and 'Confere a medida?' in stub.enviadas[0][1])
        criar_notificacao(u2_id, 'teste', 'Sem vínculo')
        check('usuário sem vínculo não recebe no Telegram', len(stub.enviadas) == 1)

        print('\n=== desvincular ===')
        r = c.delete('/telegram/vincular', headers=h1)
        check('desvincular -> 200', r.status_code == 200)
        check('vínculo removido do banco',
              TelegramVinculo.query.filter_by(user_id=u1_id).count() == 0)
        stub.enviadas.clear()
        criar_notificacao(u1_id, 'teste', 'Após desvincular')
        check('sem espelho após desvincular', len(stub.enviadas) == 0)
        r = c.delete('/telegram/vincular', headers=h1)
        check('desvincular 2x -> 200 idempotente', r.status_code == 200)

print(f'\n{"=" * 40}')
print(f'PASS: {len(PASS)}  FAIL: {len(FAIL)}')
if FAIL:
    print('FALHAS:')
    for f in FAIL:
        print(f'  - {f}')
    sys.exit(1)
else:
    print('Todos os cenarios passaram.')
    sys.exit(0)
