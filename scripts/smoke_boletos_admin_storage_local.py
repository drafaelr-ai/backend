"""
Smoke test local do PDF de boleto do ADMIN no Storage (B-11).

Três caminhos, e o terceiro é o que torna o deploy seguro:

1. Storage CONFIGURADO: o PDF sobe para o bucket e o banco guarda o path;
   a leitura devolve URL assinada.
2. Registro LEGADO: quem já tem base64 na coluna continua sendo servido dela.
3. Storage NÃO CONFIGURADO: o comportamento é idêntico ao de antes desta
   mudança (base64 na coluna). É isto que permite esta mudança viver na main
   esperando o deploy manual do admin sem alterar nada em produção.

Uso: cd backend && PYTHONPATH=. python scripts/smoke_boletos_admin_storage_local.py
"""
import base64
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('JWT_SECRET_KEY_ADMIN', 'smoke-admin-storage')
os.environ.setdefault('DATABASE_URL_ADMIN', 'sqlite:///:memory:')

from app_admin import create_app
from config_admin import DevelopmentConfig
from extensions_admin import db
from models_admin import Usuario, Imovel, AdminBoleto
from services_admin import storage_admin_service

app = create_app(DevelopmentConfig.from_env())

# ---- stubs: nada de rede
UPLOADS = []
STORAGE_LIGADO = [True]


def _fake_disponivel():
    return STORAGE_LIGADO[0]


def _fake_upload(conteudo, pasta, nome='arquivo', bucket=None,
                 content_type='application/pdf', max_bytes=None):
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


def _fake_signed(path, expires=3600, bucket=None):
    return f'https://storage.smoke/{bucket}/{path}?exp={expires}'


storage_admin_service.disponivel = _fake_disponivel
storage_admin_service.upload_base64 = _fake_upload
storage_admin_service.signed_url = _fake_signed

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


PDF_FAKE = base64.b64encode(b'%PDF-1.4 smoke boleto admin').decode()

with app.app_context():
    db.create_all()
    admin = Usuario(username='admin_storage', nome='Admin', role='admin')
    admin.set_password('smoke123')
    db.session.add(admin)
    db.session.commit()
    imovel = Imovel(nome='Imovel Smoke', tipo='apartamento', usuario_id=admin.id)
    db.session.add(imovel)
    db.session.commit()
    imovel_id = imovel.id

    from flask_jwt_extended import create_access_token
    h = {'Authorization': f'Bearer {create_access_token(identity=str(admin.id))}'}
    venc = (date.today() + timedelta(days=20)).isoformat()

    def corpo(cod, **extra):
        base = {'codigo_barras': cod, 'descricao': 'Boleto admin',
                'valor': 800.0, 'data_vencimento': venc}
        base.update(extra)
        return base

    with app.test_client() as c:
        print('\n=== 1) Storage configurado ===')
        STORAGE_LIGADO[0] = True
        r = c.post(f'/imoveis/{imovel_id}/boletos', json=corpo(
            '1' * 47, arquivo_nome='admin-abril.pdf', arquivo_base64=PDF_FAKE,
        ), headers=h)
        check('cria boleto com PDF -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:200]}')
        criado = json.loads(r.data)
        check('to_dict diz que tem PDF', criado['tem_pdf'] is True)
        check('subiu para o bucket do admin',
              len(UPLOADS) == 1 and UPLOADS[0][1] == 'admin-boletos', f'got {UPLOADS}')
        bol = db.session.get(AdminBoleto, criado['id'])
        check('banco guarda o path, nao o base64',
              bool(bol.arquivo_path) and bol.arquivo_pdf is None,
              f'path={bol.arquivo_path} pdf={(bol.arquivo_pdf or "")[:20]}')
        r = c.get(f"/imoveis/{imovel_id}/boletos/{criado['id']}/arquivo", headers=h)
        resposta = json.loads(r.data)
        check('leitura devolve URL assinada', 'arquivo_url' in resposta, f'got {resposta}')
        check('e nao traz base64', 'arquivo_base64' not in resposta)

        print('\n=== 2) registro legado com base64 na coluna ===')
        antigo = AdminBoleto(
            imovel_id=imovel_id, usuario_id=admin.id, codigo_barras='2' * 47,
            descricao='Antigo', valor=500.0,
            data_vencimento=date.today() + timedelta(days=5),
            arquivo_nome='antigo.pdf', arquivo_pdf=PDF_FAKE,
        )
        db.session.add(antigo)
        db.session.commit()
        r = c.get(f'/imoveis/{imovel_id}/boletos/{antigo.id}/arquivo', headers=h)
        resposta = json.loads(r.data)
        check('legado continua sendo servido -> 200', r.status_code == 200)
        check('e vem do base64 da coluna', resposta.get('arquivo_base64') == PDF_FAKE)
        check('sem tocar o Storage', len(UPLOADS) == 1, f'got {UPLOADS}')

        print('\n=== 3) Storage NAO configurado: comportamento de antes ===')
        STORAGE_LIGADO[0] = False
        uploads_antes = len(UPLOADS)
        r = c.post(f'/imoveis/{imovel_id}/boletos', json=corpo(
            '3' * 47, arquivo_nome='sem-storage.pdf', arquivo_base64=PDF_FAKE,
        ), headers=h)
        check('cria boleto normalmente -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:200]}')
        sem_storage = json.loads(r.data)
        check('to_dict diz que tem PDF', sem_storage['tem_pdf'] is True)
        check('NAO tentou subir nada', len(UPLOADS) == uploads_antes, f'got {UPLOADS}')
        bol = db.session.get(AdminBoleto, sem_storage['id'])
        check('grava base64 na coluna, como antes',
              bol.arquivo_pdf == PDF_FAKE and bol.arquivo_path is None,
              f'path={bol.arquivo_path}')
        r = c.get(f"/imoveis/{imovel_id}/boletos/{sem_storage['id']}/arquivo", headers=h)
        check('e a leitura devolve base64',
              json.loads(r.data).get('arquivo_base64') == PDF_FAKE)
        STORAGE_LIGADO[0] = True

        print('\n=== validacao e acesso ===')
        r = c.post(f'/imoveis/{imovel_id}/boletos', json=corpo(
            '4' * 47, arquivo_nome='x.pdf', arquivo_base64='???',
        ), headers=h)
        check('base64 invalido -> 400 (nao 500)', r.status_code == 400,
              f'got {r.status_code}: {r.data[:160]}')
        grande = base64.b64encode(b'x' * (11 * 1024 * 1024)).decode()
        r = c.post(f'/imoveis/{imovel_id}/boletos', json=corpo(
            '5' * 47, arquivo_nome='grande.pdf', arquivo_base64=grande,
        ), headers=h)
        check('PDF acima do teto -> 400', r.status_code == 400, f'got {r.status_code}')

        outro = Usuario(username='operador_storage', nome='Operador', role='operador')
        outro.set_password('smoke123')
        db.session.add(outro)
        db.session.commit()
        h_outro = {'Authorization': f'Bearer {create_access_token(identity=str(outro.id))}'}
        r = c.get(f"/imoveis/{imovel_id}/boletos/{criado['id']}/arquivo", headers=h_outro)
        check('operador de outro imovel nao abre o PDF -> 403', r.status_code == 403,
              f'got {r.status_code}')
        r = c.get(f"/imoveis/{imovel_id}/boletos/{criado['id']}/arquivo")
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
