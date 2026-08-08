"""
Smoke test local do abastecimento por link do motorista — sem banco real
(SQLite in-memory) e sem chamar a API de leitura do comprovante.

O OCR e o Storage são substituídos por stubs: o que se testa aqui é o fluxo
(autorização → link público → comprovante → envio → abastecimento gravado),
as validações e o cálculo de consumo — não a acurácia do modelo.

Uso: cd backend && python scripts/smoke_abastecimento_local.py
"""
import os
import io
import sys
import json
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from flask_jwt_extended import create_access_token

from extensions import db, jwt, limiter
import models  # noqa: F401 — registra todos os models no metadata
from models import User, Obra, FrotaVeiculo, FrotaCondutor, FrotaAbastecimento
from models import FrotaAbastecimentoSolicitacao
from routes.frota import frota_bp
from routes.abastecimento_publico import abastecimento_publico_bp
from services import storage_service, recibo_abastecimento_service

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'smoke-test-secret'
app.config['RATELIMIT_ENABLED'] = False  # o rate limit real é testado em prod
db.init_app(app)
jwt.init_app(app)
limiter.init_app(app)
app.register_blueprint(frota_bp)
app.register_blueprint(abastecimento_publico_bp)

TABELAS = [
    'user', 'user_obra_association', 'obra', 'categoria_mo', 'funcionario',
    'frota_condutor', 'frota_veiculo', 'frota_movimentacao', 'frota_documento',
    'frota_manutencao', 'frota_abastecimento', 'frota_multa',
    'frota_abastecimento_solicitacao',
]

# ---- stubs: nada de rede no smoke
UPLOADS = []
OCR_CHAMADAS = []
OCR_RESPOSTA = {
    'litros': 42.5, 'preco_litro': 5.89, 'valor_total': 250.33,
    'posto': 'Posto Smoke Ltda', 'data': date.today().isoformat(),
    'combustivel': 'diesel s10', 'km': None, 'confianca': 'alta',
}
OCR_ERRO = [None]  # quando setado, extrair_dados_recibo levanta


def _fake_upload(arquivo, pasta, bucket=None):
    UPLOADS.append((pasta, bucket, getattr(arquivo, 'filename', None)))
    return f'{pasta}/fake_{len(UPLOADS)}.jpg'


def _fake_ocr(arquivo):
    OCR_CHAMADAS.append(getattr(arquivo, 'filename', None))
    if OCR_ERRO[0]:
        raise OCR_ERRO[0]
    return dict(OCR_RESPOSTA)


storage_service.upload_arquivo = _fake_upload
recibo_abastecimento_service.extrair_dados_recibo = _fake_ocr

PASS = []
FAIL = []


def check(label, condition, detail=''):
    if condition:
        PASS.append(label)
        print(f'  PASS  {label}')
    else:
        FAIL.append(label)
        print(f'  FAIL  {label}  {detail}')


def arquivo_fake(nome='cupom.jpg'):
    return {'arquivo': (io.BytesIO(b'\xff\xd8\xff\xe0 conteudo de cupom'), nome)}


with app.app_context():
    db.metadata.create_all(bind=db.engine, tables=[db.metadata.tables[t] for t in TABELAS])

    obra1 = Obra(nome='Obra Abast 1')
    obra2 = Obra(nome='Obra Abast 2')
    master = User(username='master_abast', role='master', modulos_permitidos=['obras'])
    master.set_password('smoke123')
    outro = User(username='outro_abast', role='comum', modulos_permitidos=['frota'])
    outro.set_password('smoke123')
    outro.obras_permitidas.append(obra2)
    db.session.add_all([obra1, obra2, master, outro])
    db.session.commit()
    obra1_id, obra2_id = obra1.id, obra2.id

    condutor = FrotaCondutor(nome='Motorista Smoke')
    veiculo = FrotaVeiculo(placa='ABC1D23', modelo='Hilux', tipo='caminhonete',
                           combustivel='diesel', km_atual=80000,
                           local_tipo='obra', obra_id=obra1_id)
    veiculo_outra_obra = FrotaVeiculo(placa='XYZ9K88', modelo='Strada', tipo='carro',
                                      local_tipo='obra', obra_id=obra2_id)
    veiculo_inativo = FrotaVeiculo(placa='OLD0A11', modelo='Kombi', tipo='carro',
                                   status='vendido')
    db.session.add_all([condutor, veiculo, veiculo_outra_obra, veiculo_inativo])
    db.session.commit()
    veic_id, veic_outra_id, veic_inativo_id = (
        veiculo.id, veiculo_outra_obra.id, veiculo_inativo.id)
    condutor_id = condutor.id
    veiculo.condutor_atual_id = condutor_id
    db.session.commit()

    h_master = {'Authorization': f'Bearer {create_access_token(identity=str(master.id))}'}
    h_outro = {'Authorization': f'Bearer {create_access_token(identity=str(outro.id))}'}

    with app.test_client() as c:
        print('\n=== gerar autorização (compradora) ===')
        r = c.post('/frota/abastecimento-solicitacoes', json={}, headers=h_master)
        check('POST sem veiculo_id -> 400', r.status_code == 400)
        r = c.post('/frota/abastecimento-solicitacoes', json={'veiculo_id': 999999}, headers=h_master)
        check('POST veículo inexistente -> 404', r.status_code == 404)
        r = c.post('/frota/abastecimento-solicitacoes', json={'veiculo_id': veic_outra_id}, headers=h_outro)
        check('POST veículo de outra obra (comum sem acesso) -> 201',
              r.status_code == 201, f'got {r.status_code}')  # obra2 é permitida a `outro`
        r = c.post('/frota/abastecimento-solicitacoes', json={'veiculo_id': veic_id}, headers=h_outro)
        check('POST veículo de obra não permitida -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.post('/frota/abastecimento-solicitacoes', json={'veiculo_id': veic_inativo_id}, headers=h_master)
        check('POST veículo vendido -> 400', r.status_code == 400)
        r = c.post('/frota/abastecimento-solicitacoes',
                   json={'veiculo_id': veic_id, 'limite_valor': 0}, headers=h_master)
        check('POST limite_valor zero -> 400', r.status_code == 400)
        r = c.post('/frota/abastecimento-solicitacoes',
                   json={'veiculo_id': veic_id, 'validade_horas': 9999}, headers=h_master)
        check('POST validade_horas absurda -> 400', r.status_code == 400)
        r = c.post('/frota/abastecimento-solicitacoes',
                   json={'veiculo_id': veic_id, 'condutor_id': 999999}, headers=h_master)
        check('POST condutor inexistente -> 400', r.status_code == 400)

        r = c.post('/frota/abastecimento-solicitacoes', json={
            'veiculo_id': veic_id, 'limite_valor': '500,00',
            'observacao': 'Abastecer no posto da BR-101',
        }, headers=h_master)
        check('POST autorização ok -> 201', r.status_code == 201, f'got {r.status_code}: {r.data[:300]}')
        sol = json.loads(r.data)
        token = sol['token']
        sol_id = sol['id']
        check('autorização: token longo', len(token) > 20)
        check('autorização: url do link', sol['url'].endswith(f'/abastecimento/{token}'))
        check('autorização: limite BR parseado', sol['limite_valor'] == 500.0)
        check('autorização: herda condutor atual do veículo', sol['condutor_id'] == condutor_id)
        check('autorização: herda combustível do veículo', sol['combustivel'] == 'diesel')
        check('autorização: status pendente', sol['status'] == 'pendente')

        print('\n=== listagem (compradora) ===')
        r = c.get('/frota/abastecimento-solicitacoes', headers=h_master)
        check('GET solicitações -> 200', r.status_code == 200)
        check('listagem inclui a nova', any(s['id'] == sol_id for s in json.loads(r.data)))
        r = c.get('/frota/abastecimento-solicitacoes?status=concluida', headers=h_master)
        check('filtro status=concluida vazio', json.loads(r.data) == [])
        r = c.get(f'/frota/abastecimento-solicitacoes?veiculo_id={veic_id}', headers=h_master)
        check('filtro por veículo', all(s['veiculo_id'] == veic_id for s in json.loads(r.data)))
        r = c.get('/frota/abastecimento-solicitacoes', headers=h_outro)
        check('comum não vê solicitação de obra alheia',
              all(s['veiculo_id'] != veic_id for s in json.loads(r.data)))

        print('\n=== página pública do motorista ===')
        r = c.get(f'/abastecimento/{token}')
        check('GET público sem token JWT -> 200', r.status_code == 200, f'got {r.status_code}')
        pub = json.loads(r.data)
        check('público: placa do veículo', pub['veiculo_placa'] == 'ABC1D23')
        check('público: km anterior do painel', pub['km_anterior'] == 80000)
        check('público: limite autorizado', pub['limite_valor'] == 500.0)
        check('público: instrução da compradora', 'BR-101' in (pub['observacao'] or ''))
        check('público: NÃO expõe ids internos',
              'veiculo_id' not in pub and 'condutor_id' not in pub and 'id' not in pub)
        check('público: NÃO expõe o token', 'token' not in pub)
        r = c.get('/abastecimento/token-que-nao-existe')
        check('GET público token inválido -> 404', r.status_code == 404)

        print('\n=== leitura do comprovante ===')
        r = c.post(f'/abastecimento/{token}/comprovante', data={},
                   content_type='multipart/form-data')
        check('POST comprovante sem arquivo -> 400', r.status_code == 400)
        r = c.post(f'/abastecimento/{token}/comprovante',
                   data={'arquivo': (io.BytesIO(b'x' * (11 * 1024 * 1024)), 'gigante.jpg')},
                   content_type='multipart/form-data')
        check('POST comprovante acima de 10 MB -> 413', r.status_code == 413,
              f'got {r.status_code}')
        r = c.post(f'/abastecimento/{token}/comprovante', data=arquivo_fake(),
                   content_type='multipart/form-data')
        check('POST comprovante -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        leitura = json.loads(r.data)
        check('comprovante: litros lidos', leitura['dados']['litros'] == 42.5)
        check('comprovante: preço lido', leitura['dados']['preco_litro'] == 5.89)
        check('comprovante: posto lido', leitura['dados']['posto'] == 'Posto Smoke Ltda')
        check('comprovante: arquivo subiu ao Storage', leitura['comprovante_recebido'] is True)
        check('comprovante: ocr_status ok', leitura['ocr_status'] == 'ok')
        check('comprovante: tentativas restantes contadas',
              leitura['tentativas_restantes'] == 5)
        check('storage recebeu upload na pasta da solicitação',
              UPLOADS and UPLOADS[-1][0] == f'abastecimentos/{sol_id}')
        check('valores coerentes não geram aviso', leitura.get('aviso') is None,
              f"aviso: {leitura.get('aviso')}")

        # Cupom borrado: o modelo lê um dígito a mais no total. O motorista
        # precisa ser avisado antes de mandar o número errado pro financeiro.
        OCR_RESPOSTA.update({'litros': 40.0, 'preco_litro': 5.0, 'valor_total': 500.0})
        r = c.post(f'/abastecimento/{token}/comprovante', data=arquivo_fake('borrado.jpg'),
                   content_type='multipart/form-data')
        check('leitura incoerente -> 200 com aviso', r.status_code == 200)
        check('avisa que litros x preço não fecha com o total',
              'não fecham' in (json.loads(r.data).get('aviso') or ''),
              f"aviso: {json.loads(r.data).get('aviso')}")

        # Total ausente no cupom: o serviço deriva de litros × preço.
        OCR_RESPOSTA.update({'litros': 40.0, 'preco_litro': 5.0, 'valor_total': None})
        r = c.post(f'/abastecimento/{token}/comprovante', data=arquivo_fake('sem_total.jpg'),
                   content_type='multipart/form-data')
        check('total ausente é derivado de litros x preço',
              json.loads(r.data)['dados']['valor_total'] == 200.0,
              f"got {json.loads(r.data)['dados']['valor_total']}")
        OCR_RESPOSTA.update({'litros': 42.5, 'preco_litro': 5.89, 'valor_total': 250.33})

        OCR_ERRO[0] = RuntimeError('modelo indisponível')
        r = c.post(f'/abastecimento/{token}/comprovante', data=arquivo_fake('outro.jpg'),
                   content_type='multipart/form-data')
        check('comprovante com OCR fora do ar -> 200 com aviso', r.status_code == 200)
        falha = json.loads(r.data)
        check('OCR falhou não bloqueia o envio', falha['ocr_status'] == 'falhou')
        check('OCR falhou avisa o motorista', 'manualmente' in (falha.get('aviso') or '').lower()
              or 'à mão' in (falha.get('aviso') or ''), f"aviso: {falha.get('aviso')}")
        check('comprovante segue anexado mesmo com OCR fora do ar',
              falha['comprovante_recebido'] is True)
        OCR_ERRO[0] = None

        print('\n=== envio do abastecimento ===')
        r = c.post(f'/abastecimento/{token}', json={'litros': 40, 'valor_total': 250})
        check('envio sem km -> 400', r.status_code == 400)
        r = c.post(f'/abastecimento/{token}', json={'km': 79000, 'litros': 40, 'valor_total': 250})
        check('envio com km menor que o do veículo -> 400', r.status_code == 400,
              f'got {r.status_code}: {r.data[:200]}')
        r = c.post(f'/abastecimento/{token}', json={'km': 80500, 'valor_total': 250})
        check('envio sem litros -> 400', r.status_code == 400)
        r = c.post(f'/abastecimento/{token}', json={'km': 80500, 'litros': 40})
        check('envio sem valor nem preço -> 400', r.status_code == 400)
        r = c.post(f'/abastecimento/{token}', json={'km': 80500, 'litros': 40, 'valor_total': 900})
        check('envio acima do limite autorizado -> 400', r.status_code == 400,
              f'got {r.status_code}: {r.data[:200]}')
        r = c.post(f'/abastecimento/{token}', json={
            'km': 80500, 'litros': 40, 'valor_total': 250,
            'data': (date.today() + timedelta(days=2)).isoformat(),
        })
        check('envio com data futura -> 400', r.status_code == 400)

        r = c.post(f'/abastecimento/{token}', json={
            'km': '80.500', 'litros': '42,5', 'preco_litro': '5,89',
            'posto': 'Posto Smoke Ltda', 'observacao': 'Tanque cheio',
        })
        check('envio ok (valor derivado de litros x preço) -> 201', r.status_code == 201,
              f'got {r.status_code}: {r.data[:300]}')
        envio = json.loads(r.data)
        check('envio: km BR parseado', envio['resumo']['km'] == 80500)
        check('envio: litros BR parseados', envio['resumo']['litros'] == 42.5)
        check('envio: valor total calculado de litros x preço',
              abs(envio['resumo']['valor_total'] - 42.5 * 5.89) < 0.01,
              f"got {envio['resumo']['valor_total']}")
        check('envio: comprovante anexado', envio['resumo']['comprovante_recebido'] is True)

        abast = FrotaAbastecimento.query.filter_by(solicitacao_id=sol_id).first()
        check('abastecimento gravado na frota', abast is not None)
        check('abastecimento: origem superlink', abast.origem == 'superlink')
        check('abastecimento: preço por litro gravado', float(abast.preco_litro) == 5.89)
        check('abastecimento: condutor da autorização', abast.condutor_id == condutor_id)
        check('abastecimento: snapshot do local do veículo',
              abast.obra_id == obra1_id and abast.local_tipo == 'obra')
        check('abastecimento: comprovante vinculado', bool(abast.comprovante_url))
        veic = db.session.get(FrotaVeiculo, veic_id)
        check('km do veículo atualizado', veic.km_atual == 80500)
        sol_db = db.session.get(FrotaAbastecimentoSolicitacao, sol_id)
        check('solicitação concluída', sol_db.status == 'concluida')
        check('solicitação aponta para o abastecimento', sol_db.abastecimento_id == abast.id)

        print('\n=== link não reutilizável ===')
        r = c.post(f'/abastecimento/{token}', json={'km': 81000, 'litros': 10, 'valor_total': 60})
        check('reenvio -> 400', r.status_code == 400)
        check('reenvio explica o motivo', 'já foi registrado' in json.loads(r.data)['erro'])
        r = c.post(f'/abastecimento/{token}/comprovante', data=arquivo_fake(),
                   content_type='multipart/form-data')
        check('comprovante em link concluído -> 400', r.status_code == 400)
        check('abastecimento único', FrotaAbastecimento.query.filter_by(solicitacao_id=sol_id).count() == 1)
        r = c.patch(f'/frota/abastecimento-solicitacoes/{sol_id}/cancelar', headers=h_master)
        check('cancelar concluída -> 400', r.status_code == 400)

        print('\n=== cancelamento e expiração ===')
        r = c.post('/frota/abastecimento-solicitacoes', json={'veiculo_id': veic_id}, headers=h_master)
        cancelada = json.loads(r.data)
        r = c.patch(f'/frota/abastecimento-solicitacoes/{cancelada["id"]}/cancelar', headers=h_master)
        check('cancelar pendente -> 200', r.status_code == 200)
        check('status cancelada', json.loads(r.data)['status'] == 'cancelada')
        r = c.post(f'/abastecimento/{cancelada["token"]}',
                   json={'km': 81000, 'litros': 10, 'valor_total': 60})
        check('envio em link cancelado -> 400', r.status_code == 400)
        check('cancelado explica o motivo', 'cancelado' in json.loads(r.data)['erro'])

        r = c.post('/frota/abastecimento-solicitacoes', json={'veiculo_id': veic_id}, headers=h_master)
        expirada = json.loads(r.data)
        sol_exp = db.session.get(FrotaAbastecimentoSolicitacao, expirada['id'])
        sol_exp.expira_em = sol_exp.criado_em - timedelta(hours=1)
        db.session.commit()
        r = c.get(f'/abastecimento/{expirada["token"]}')
        check('link expirado ainda carrega (status expirada)',
              r.status_code == 200 and json.loads(r.data)['status'] == 'expirada')
        r = c.post(f'/abastecimento/{expirada["token"]}',
                   json={'km': 81000, 'litros': 10, 'valor_total': 60})
        check('envio em link expirado -> 400', r.status_code == 400)
        r = c.get('/frota/abastecimento-solicitacoes?status=expirada', headers=h_master)
        check('filtro status=expirada acha a vencida',
              any(s['id'] == expirada['id'] for s in json.loads(r.data)))
        r = c.get('/frota/abastecimento-solicitacoes?status=pendente', headers=h_master)
        check('filtro status=pendente exclui a vencida',
              all(s['id'] != expirada['id'] for s in json.loads(r.data)))

        print('\n=== consumo do veículo ===')
        # 80500 (do link) → 80900 com 40 L = 10 km/l → 81300 com 50 L = 8 km/l
        for km, litros, valor in ((80900, 40, 280), (81300, 50, 350)):
            r = c.post('/frota/abastecimentos', json={
                'veiculo_id': veic_id, 'data': date.today().isoformat(),
                'km': km, 'litros': litros, 'valor': valor,
            }, headers=h_master)
            check(f'POST abastecimento manual km={km} -> 201', r.status_code == 201,
                  f'got {r.status_code}: {r.data[:200]}')

        r = c.get(f'/frota/veiculos/{veic_id}/consumo', headers=h_master)
        check('GET consumo -> 200', r.status_code == 200, f'got {r.status_code}: {r.data[:300]}')
        consumo = json.loads(r.data)
        resumo = consumo['resumo']
        check('consumo: 3 abastecimentos', resumo['abastecimentos'] == 3)
        check('consumo: km rodados somados', resumo['km_rodados'] == 800,
              f"got {resumo['km_rodados']}")
        check('consumo: média km/l', resumo['consumo_medio_km_l'] == 9.0,
              f"got {resumo['consumo_medio_km_l']}")
        check('consumo: melhor e pior', resumo['melhor_consumo_km_l'] == 10.0
              and resumo['pior_consumo_km_l'] == 8.0)
        check('consumo: total de litros', resumo['total_litros'] == 132.5)
        check('consumo: custo por km', resumo['custo_por_km'] is not None)
        check('consumo: mais recente primeiro',
              consumo['registros'][0]['km'] == 81300)
        check('consumo: 1º abastecimento sem km/l (não há km anterior)',
              consumo['registros'][-1]['consumo_km_l'] is None)
        check('consumo: registro traz km rodados', consumo['registros'][0]['km_rodados'] == 400)
        check('consumo: payload traz o veículo', consumo['veiculo']['placa'] == 'ABC1D23')
        r = c.get(f'/frota/veiculos/{veic_outra_id}/consumo', headers=h_outro)
        check('consumo de veículo permitido -> 200', r.status_code == 200)
        r = c.get(f'/frota/veiculos/{veic_id}/consumo', headers=h_outro)
        check('consumo de veículo de obra alheia -> 403', r.status_code == 403)
        r = c.get('/frota/veiculos/999999/consumo', headers=h_master)
        check('consumo de veículo inexistente -> 404', r.status_code == 404)

        print('\n=== comprovante sob auth (compradora) ===')
        r = c.get(f'/frota/arquivo/abastecimento/{abast.id}', headers=h_outro)
        check('comprovante de obra alheia -> 403', r.status_code == 403, f'got {r.status_code}')
        r = c.get('/frota/arquivo/banana/1', headers=h_master)
        check('tipo de arquivo inválido -> 400', r.status_code == 400)

        print('\n=== isolamento do módulo ===')
        rotas_publicas = [str(r) for r in app.url_map.iter_rules()
                          if str(r).startswith('/abastecimento')]
        check('blueprint público: 3 rotas', len(rotas_publicas) == 3, f'{rotas_publicas}')
        check('nenhuma rota /frota exposta sem JWT',
              all(not p.startswith('/frota') for p in rotas_publicas))

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
