"""
Smoke test local do push nativo — sem banco real (SQLite in-memory) e sem rede.

Cobre o caminho que estava quebrado: o APK registrava no FCM e mandava o token
para POST /notificacoes/dispositivos, que não existia — 404, token perdido,
nenhum aviso na bandeja do aparelho. E o aviso de "aguarda aprovação" ia só
para cfg.aprovadores_ids, deixando os masters de fora.

Nenhum cenário toca a rede: o envio é interceptado.

Uso: cd backend && python scripts/smoke_push_local.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401 — registra todos os models no metadata
from models import User, Obra, Notificacao, DispositivoPush
from routes.notificacoes import notificacoes_bp
from services import push_service

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(notificacoes_bp)

TABELAS = ['user', 'user_obra_association', 'obra', 'notificacao', 'dispositivo_push']

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


def auth(uid):
    with app.app_context():
        return {'Authorization': f'Bearer {create_access_token(identity=str(uid))}'}


# Service account de mentira, só para exercitar a montagem do envio. A chave
# é gerada na hora: nada de segredo de verdade em arquivo de teste.
def _credencial_falsa():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    return json.dumps({
        'type': 'service_account',
        'project_id': 'projeto-de-teste',
        'private_key': pem,
        'client_email': 'smoke@projeto-de-teste.iam.gserviceaccount.com',
    })


with app.app_context():
    # Só as tabelas deste smoke: o metadata inteiro tem colunas JSONB, que o
    # SQLite não compila.
    db.metadata.create_all(bind=db.engine,
                           tables=[db.metadata.tables[t] for t in TABELAS])
    u_master = User(username='diego', role='master')
    u_master.set_password('smoke123')
    u_comum = User(username='joao', role='comum')
    u_comum.set_password('smoke123')
    db.session.add_all([u_master, u_comum])
    db.session.commit()
    ID_MASTER, ID_COMUM = u_master.id, u_comum.id

cli = app.test_client()

print('\n--- 1. Kill switch: sem credencial, tudo vira no-op ---')
os.environ.pop('FCM_CREDENTIALS_JSON', None)
check('sem FCM_CREDENTIALS_JSON: configurado() e False', push_service.configurado() is False)
with app.app_context():
    push_service.notificar_usuario(ID_MASTER, 'oi', 'corpo', tipo='teste')
check('notificar_usuario sem credencial nao levanta', True)

print('\n--- 2. Credencial invalida nao derruba nada ---')
os.environ['FCM_CREDENTIALS_JSON'] = 'isto nao e json nem caminho'
check('credencial lixo: configurado() e False', push_service.configurado() is False)
os.environ['FCM_CREDENTIALS_JSON'] = '{"project_id": "x"}'
check('credencial incompleta: configurado() e False', push_service.configurado() is False)

print('\n--- 3. Credencial valida liga o servico ---')
os.environ['FCM_CREDENTIALS_JSON'] = _credencial_falsa()
check('credencial completa: configurado() e True', push_service.configurado() is True)

print('\n--- 4. Registro do aparelho (a rota que faltava) ---')
r = cli.post('/notificacoes/dispositivos',
             json={'token': 'token-aparelho-diego', 'plataforma': 'android'},
             headers=auth(ID_MASTER))
check('POST /notificacoes/dispositivos responde 200', r.status_code == 200, f'{r.status_code} {r.data[:120]}')
check('resposta nao devolve o token', 'token' not in (r.get_json() or {}),
      f'resposta: {r.get_json()}')
with app.app_context():
    d = DispositivoPush.query.filter_by(token='token-aparelho-diego').first()
    check('aparelho gravado e vinculado ao usuario', d is not None and d.usuario_id == ID_MASTER)
    check('plataforma gravada', d is not None and d.plataforma == 'android')

r = cli.post('/notificacoes/dispositivos', json={'token': '  '}, headers=auth(ID_MASTER))
check('token vazio responde 400 (nunca 422)', r.status_code == 400, f'{r.status_code}')
r = cli.post('/notificacoes/dispositivos',
             json={'token': 'x', 'plataforma': 'symbian'}, headers=auth(ID_MASTER))
check('plataforma invalida responde 400', r.status_code == 400, f'{r.status_code}')
r = cli.post('/notificacoes/dispositivos', json={'token': 'y'})
check('sem JWT responde 401', r.status_code == 401, f'{r.status_code}')

print('\n--- 5. Mesmo aparelho, outro login: reatribui em vez de duplicar ---')
r = cli.post('/notificacoes/dispositivos',
             json={'token': 'token-aparelho-diego', 'plataforma': 'android'},
             headers=auth(ID_COMUM))
check('re-registro responde 200', r.status_code == 200, f'{r.status_code}')
with app.app_context():
    todos = DispositivoPush.query.filter_by(token='token-aparelho-diego').all()
    check('continua existindo um unico registro para o token', len(todos) == 1, f'{len(todos)}')
    check('token passou para o novo usuario', todos[0].usuario_id == ID_COMUM)
    # devolve para o diego para os proximos cenarios
    todos[0].usuario_id = ID_MASTER
    db.session.commit()

print('\n--- 6. Diagnostico ---')
r = cli.get('/notificacoes/push/status', headers=auth(ID_MASTER))
j = r.get_json() or {}
check('GET /notificacoes/push/status responde 200', r.status_code == 200, f'{r.status_code}')
check('status diz configurado=True', j.get('configurado') is True, f'{j}')
check('status lista 1 aparelho do usuario', len(j.get('aparelhos') or []) == 1, f'{j}')
r = cli.get('/notificacoes/push/status', headers=auth(ID_COMUM))
check('status do outro usuario nao vaza aparelho alheio',
      len((r.get_json() or {}).get('aparelhos') or []) == 0)

print('\n--- 7. Envio: monta a mensagem certa, sem tocar a rede ---')
enviados = []


def _fake_enviar(token_acesso, project_id, token_aparelho, titulo, corpo, dados):
    enviados.append({'project_id': project_id, 'token': token_aparelho,
                     'titulo': titulo, 'corpo': corpo, 'dados': dados})
    return True, None


push_service._enviar_um = _fake_enviar
push_service._access_token = lambda: 'access-token-de-teste'

with app.app_context():
    push_service._despachar(app, ID_MASTER, 'Solicitação #7 aguarda aprovação',
                            'Cimento — obra Alfa', 'solicitacao_aguardando_aprovacao',
                            42, 3)
check('um envio para o aparelho do usuario', len(enviados) == 1, f'{enviados}')
if enviados:
    e = enviados[0]
    check('vai para o token certo', e['token'] == 'token-aparelho-diego')
    check('usa o project_id da credencial', e['project_id'] == 'projeto-de-teste')
    d = e['dados']
    check('data traz notificacao_id, que o app usa para marcar lida',
          d.get('notificacao_id') == 42, f'{d}')
    check('data traz modulo solicitacoes', d.get('modulo') == 'solicitacoes', f'{d}')
    check('data traz caminho', 'caminho' in d, f'{d}')

print('\n--- 8. Roteamento do toque por tipo ---')
casos = [
    ('solicitacao_aguardando_aprovacao', 'solicitacoes'),
    ('solicitacao_qualquer_coisa_nova', 'solicitacoes'),
    ('frota_multa', 'frota'),
    ('boleto_vencendo', 'admin'),
    ('pagamento_inserido', 'obras'),
    (None, 'obras'),
]
for tipo, esperado in casos:
    modulo, _ = push_service._modulo_e_caminho(tipo)
    check(f'tipo {tipo!r} abre o modulo {esperado}', modulo == esperado, f'veio {modulo}')

print('\n--- 9. Token morto sai do banco sozinho ---')
with app.app_context():
    db.session.add(DispositivoPush(usuario_id=ID_COMUM, token='token-morto', plataforma='android'))
    db.session.commit()

push_service._enviar_um = lambda *a, **k: (False, 'UNREGISTERED')
with app.app_context():
    push_service._despachar(app, ID_COMUM, 't', 'c', 'teste', 1, None)
    restou = DispositivoPush.query.filter_by(token='token-morto').first()
    check('aparelho UNREGISTERED removido', restou is None)

push_service._enviar_um = lambda *a, **k: (False, None)
with app.app_context():
    db.session.add(DispositivoPush(usuario_id=ID_COMUM, token='token-instavel', plataforma='android'))
    db.session.commit()
    push_service._despachar(app, ID_COMUM, 't', 'c', 'teste', 1, None)
    restou = DispositivoPush.query.filter_by(token='token-instavel').first()
    check('falha passageira NAO remove o aparelho', restou is not None)

print('\n--- 10. Remocao no logout ---')
r = cli.delete('/notificacoes/dispositivos',
               json={'token': 'token-aparelho-diego'}, headers=auth(ID_COMUM))
check('outro usuario nao consegue remover aparelho alheio',
      (r.get_json() or {}).get('removidos') == 0, f'{r.get_json()}')
r = cli.delete('/notificacoes/dispositivos',
               json={'token': 'token-aparelho-diego'}, headers=auth(ID_MASTER))
check('dono remove o proprio aparelho', (r.get_json() or {}).get('removidos') == 1, f'{r.get_json()}')

print('\n--- 11. criar_notificacao continua funcionando se o push explodir ---')
from services import notificacao_service


def _push_explode(*a, **k):
    raise RuntimeError('FCM fora do ar')


push_service.notificar_usuario = _push_explode
with app.app_context():
    antes = Notificacao.query.count()
    n = notificacao_service.criar_notificacao(
        usuario_destino_id=ID_MASTER, tipo='solicitacao_aguardando_aprovacao',
        titulo='Solicitação #9 aguarda aprovação')
    depois = Notificacao.query.count()
check('sino gravado mesmo com push quebrado', depois == antes + 1, f'{antes} -> {depois}')
check('criar_notificacao devolveu a notificacao', n is not None)

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
