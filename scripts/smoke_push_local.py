"""Regressão isolada do cadastro FCM e do espelho de notificações nativas."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt
import models  # noqa: F401
from models import Notificacao, Obra, PushDevice, User
from routes.notificacoes import notificacoes_bp
import services.notificacao_service as notificacao_service
from services.push_service import _destino_notificacao


app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JWT_SECRET_KEY='push-smoke-secret-with-at-least-32-bytes',
)
db.init_app(app)
jwt.init_app(app)
app.register_blueprint(notificacoes_bp)

passed = []
failed = []


def check(label, condition, detail=''):
    if condition:
        passed.append(label)
        print(f'  PASS  {label}')
    else:
        failed.append(label)
        print(f'  FAIL  {label}  {detail}')


with app.app_context():
    tables = [
        db.metadata.tables[name]
        for name in ('user', 'user_obra_association', 'obra', 'notificacao', 'push_device')
    ]
    db.metadata.create_all(bind=db.engine, tables=tables)

    user1 = User(username='push-user-1', role='master')
    user1.set_password('teste')
    user2 = User(username='push-user-2', role='comum')
    user2.set_password('teste')
    db.session.add_all([user1, user2])
    db.session.commit()

    token1 = create_access_token(identity=str(user1.id))
    token2 = create_access_token(identity=str(user2.id))
    h1 = {'Authorization': f'Bearer {token1}'}
    h2 = {'Authorization': f'Bearer {token2}'}
    fcm_token = 'fcm-' + ('A1b2C3d4' * 30)

    client = app.test_client()
    print('\n=== cadastro seguro do aparelho ===')
    response = client.post('/notificacoes/dispositivos', json={
        'token': fcm_token,
        'plataforma': 'android',
    })
    check('sem autenticação -> 401', response.status_code == 401, response.status_code)

    response = client.post('/notificacoes/dispositivos', headers=h1, json={
        'token': 'curto',
        'plataforma': 'android',
    })
    check('token curto -> 400', response.status_code == 400, response.data)

    response = client.post('/notificacoes/dispositivos', headers=h1, json={
        'token': fcm_token,
        'plataforma': 'web',
    })
    check('plataforma desconhecida -> 400', response.status_code == 400, response.data)

    response = client.post('/notificacoes/dispositivos', headers=h1, json={
        'token': fcm_token,
        'plataforma': 'android',
    })
    check('primeiro cadastro -> 201', response.status_code == 201, response.data)
    check('resposta não expõe token FCM', 'token' not in response.get_json(), response.get_json())
    device = PushDevice.query.one()
    check('token vinculado somente ao usuário logado',
          device.user_id == user1.id and device.ativo, device.to_dict())

    response = client.post('/notificacoes/dispositivos', headers=h1, json={
        'token': fcm_token,
        'plataforma': 'android',
    })
    check('repetição é idempotente -> 200',
          response.status_code == 200 and PushDevice.query.count() == 1,
          response.status_code)

    response = client.post('/notificacoes/dispositivos', headers=h2, json={
        'token': fcm_token,
        'plataforma': 'android',
    })
    device = PushDevice.query.one()
    check('troca de conta transfere o aparelho sem duplicar',
          response.status_code == 200 and device.user_id == user2.id,
          device.user_id)

    response = client.delete('/notificacoes/dispositivos', headers=h1, json={
        'token': fcm_token,
    })
    check('outra conta não desativa o aparelho',
          response.status_code == 200 and PushDevice.query.one().ativo)

    response = client.delete('/notificacoes/dispositivos', headers=h2, json={
        'token': fcm_token,
    })
    check('logout da conta vinculada desativa o aparelho',
          response.status_code == 200 and not PushDevice.query.one().ativo)

    print('\n=== espelho da notificação in-app ===')
    pushed = []
    original_sender = notificacao_service.enviar_push_usuario
    notificacao_service.enviar_push_usuario = lambda notif: pushed.append(notif.id)
    try:
        notif = notificacao_service.criar_notificacao(
            usuario_destino_id=user1.id,
            tipo='pagamento_inserido',
            titulo='Pagamento registrado',
            mensagem='Teste isolado',
            item_type='lancamento',
        )
    finally:
        notificacao_service.enviar_push_usuario = original_sender
    check('aviso é persistido antes do push',
          notif is not None and db.session.get(Notificacao, notif.id) is not None)
    check('cada aviso dispara exatamente um espelho nativo', pushed == [notif.id], pushed)
    check('pagamento abre módulo financeiro',
          _destino_notificacao(notif) == ('financeiro', '/'),
          _destino_notificacao(notif))

    notif_obra = Notificacao(
        usuario_destino_id=user1.id,
        tipo='servico_criado',
        titulo='Serviço criado',
        obra_id=42,
        item_type='servico',
    )
    check('serviço abre a obra correta',
          _destino_notificacao(notif_obra) == ('obras', '/?obra=42'),
          _destino_notificacao(notif_obra))

print('\n' + '=' * 40)
print(f'PASS: {len(passed)}  FAIL: {len(failed)}')
if failed:
    raise SystemExit(1)
print('Todos os cenários de push passaram.')
