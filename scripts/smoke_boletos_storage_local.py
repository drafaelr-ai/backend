"""
Smoke test local do PDF de boleto no Storage (B-04) — sem banco real e sem rede.

O que se testa: o caminho novo (PDF vai para o Storage e o banco guarda só o
path) e o caminho LEGADO (registro antigo com base64 na coluna continua sendo
servido dela). A migração do banco pode acontecer depois justamente porque os
dois caminhos coexistem — este smoke é o que garante isso.

O Storage é substituído por stub: o que se valida é o fluxo e o contrato da
resposta, não a REST do Supabase.

Uso: cd backend && python scripts/smoke_boletos_storage_local.py
"""
import base64
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
import models  # noqa: F401 — registra os models no metadata
from models import User, Obra, Boleto
from routes.boletos import boletos_bp
from services import storage_service

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-boletos-secret'
app.config['RATELIMIT_ENABLED'] = False
db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)
app.register_blueprint(boletos_bp)

TABELAS = ['user', 'user_obra_association', 'obra', 'categoria_mo', 'boleto']

# ---- stubs: nada de rede
UPLOADS = []
FALHAR_UPLOAD = [None]


def _fake_upload_base64(conteudo, pasta, nome='arquivo', bucket=None,
                        content_type=None, max_bytes=None):
    if FALHAR_UPLOAD[0]:
        raise FALHAR_UPLOAD[0]
    # Reproduz as validações reais que importam para o contrato da rota.
    texto = str(conteudo)
    if ',' in texto[:120]:
        texto = texto.split(',', 1)[1]
    dados = base64.b64decode(texto, validate=False)
    if not dados:
        raise ValueError('Conteúdo vazio após decodificar')
    if max_bytes and len(dados) > max_bytes:
        raise ValueError(f'Arquivo tem {len(dados) // 1024} KB, acima do limite')
    UPLOADS.append((pasta, bucket, nome, len(dados)))
    return f'{pasta}/fake_{len(UPLOADS)}_{nome}'


def _fake_signed_url(path, expires=3600, bucket=None):
    return f'https://storage.smoke/{bucket}/{path}?exp={expires}'


storage_service.upload_base64 = _fake_upload_base64
storage_service.signed_url = _fake_signed_url

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


PDF_FAKE = base64.b64encode(b'%PDF-1.4 smoke boleto').decode()

with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])

    obra = Obra(nome='Obra Boleto')
    user = User(username='master_boleto', role='master', modulos_permitidos=['obras'])
    user.set_password('smoke123')
    db.session.add_all([obra, user])
    db.session.commit()
    obra_id = obra.id

    h = {'Authorization': f'Bearer {create_access_token(identity=str(user.id))}'}
    venc = (date.today() + timedelta(days=30)).isoformat()

    def corpo(**extra):
        base = {
            'codigo_barras': '0' * 47,
            'descricao': 'Boleto smoke',
            'valor': 1500.0,
            'data_vencimento': venc,
        }
        base.update(extra)
        return base

    with app.test_client() as c:
        print('\n=== caminho novo: PDF vai para o Storage ===')
        r = c.post(f'/obras/{obra_id}/boletos', json=corpo(
            arquivo_nome='boleto-abril.pdf', arquivo_base64=PDF_FAKE,
        ), headers=h)
        check('cria boleto com PDF -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:200]}')
        criado = json.loads(r.data)
        check('to_dict diz que tem PDF', criado['tem_pdf'] is True, f'got {criado}')
        check('o PDF subiu para o bucket de boletos',
              len(UPLOADS) == 1 and UPLOADS[0][1] == 'boletos-arquivos',
              f'got {UPLOADS}')
        boleto = db.session.get(Boleto, criado['id'])
        check('o banco guarda o path, nao o base64',
              bool(boleto.arquivo_path) and boleto.arquivo_pdf is None,
              f'path={boleto.arquivo_path} pdf={(boleto.arquivo_pdf or "")[:20]}')

        r = c.get(f"/obras/{obra_id}/boletos/{criado['id']}/arquivo", headers=h)
        resposta = json.loads(r.data)
        check('GET arquivo devolve URL assinada -> 200', r.status_code == 200,
              f'got {r.status_code}')
        check('a resposta traz arquivo_url', 'arquivo_url' in resposta, f'got {resposta}')
        check('e NAO traz base64', 'arquivo_base64' not in resposta)
        check('o nome do arquivo e preservado',
              resposta['arquivo_nome'] == 'boleto-abril.pdf')

        print('\n=== caminho legado: registro antigo com base64 na coluna ===')
        antigo = Boleto(
            obra_id=obra_id, usuario_id=user.id, codigo_barras='1' * 47,
            descricao='Boleto antigo', valor=900.0,
            data_vencimento=date.today() + timedelta(days=10),
            arquivo_nome='antigo.pdf', arquivo_pdf=PDF_FAKE,
        )
        db.session.add(antigo)
        db.session.commit()
        r = c.get(f'/obras/{obra_id}/boletos/{antigo.id}/arquivo', headers=h)
        resposta = json.loads(r.data)
        check('registro antigo continua sendo servido -> 200', r.status_code == 200,
              f'got {r.status_code}')
        check('e vem do base64 da coluna',
              resposta.get('arquivo_base64') == PDF_FAKE, f'got {list(resposta)}')
        check('sem tocar o Storage', len(UPLOADS) == 1, f'got {UPLOADS}')

        print('\n=== boleto sem arquivo ===')
        r = c.post(f'/obras/{obra_id}/boletos', json=corpo(codigo_barras='2' * 47),
                   headers=h)
        sem_pdf = json.loads(r.data)
        check('cria boleto sem PDF -> 201', r.status_code == 201)
        check('to_dict diz que nao tem PDF', sem_pdf['tem_pdf'] is False)
        r = c.get(f"/obras/{obra_id}/boletos/{sem_pdf['id']}/arquivo", headers=h)
        check('GET arquivo sem anexo -> 404', r.status_code == 404, f'got {r.status_code}')

        print('\n=== falha no upload nao cria boleto meio pronto ===')
        antes = Boleto.query.count()
        FALHAR_UPLOAD[0] = RuntimeError('Storage fora do ar')
        r = c.post(f'/obras/{obra_id}/boletos', json=corpo(
            codigo_barras='3' * 47, arquivo_nome='x.pdf', arquivo_base64=PDF_FAKE,
        ), headers=h)
        check('upload falhando -> 502', r.status_code == 502, f'got {r.status_code}')
        check('e o boleto NAO foi criado', Boleto.query.count() == antes,
              f'antes={antes} depois={Boleto.query.count()}')
        FALHAR_UPLOAD[0] = None

        print('\n=== base64 invalido e PDF grande ===')
        r = c.post(f'/obras/{obra_id}/boletos', json=corpo(
            codigo_barras='4' * 47, arquivo_nome='x.pdf', arquivo_base64='???',
        ), headers=h)
        check('base64 invalido -> 400 (nao 500)', r.status_code == 400,
              f'got {r.status_code}: {r.data[:160]}')
        grande = base64.b64encode(b'x' * (11 * 1024 * 1024)).decode()
        r = c.post(f'/obras/{obra_id}/boletos', json=corpo(
            codigo_barras='5' * 47, arquivo_nome='grande.pdf', arquivo_base64=grande,
        ), headers=h)
        check('PDF acima do teto -> 400', r.status_code == 400, f'got {r.status_code}')

        print('\n=== acesso ===')
        outra = Obra(nome='Obra Alheia')
        comum = User(username='comum_boleto', role='comum', modulos_permitidos=['obras'])
        comum.set_password('smoke123')
        db.session.add_all([outra, comum])
        db.session.commit()
        h_comum = {'Authorization': f'Bearer {create_access_token(identity=str(comum.id))}'}
        r = c.get(f"/obras/{obra_id}/boletos/{criado['id']}/arquivo", headers=h_comum)
        check('sem acesso a obra nao abre o PDF -> 403', r.status_code == 403,
              f'got {r.status_code}')
        r = c.get(f"/obras/{obra_id}/boletos/{criado['id']}/arquivo")
        check('sem JWT nao abre o PDF', r.status_code in (401, 422), f'got {r.status_code}')

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
