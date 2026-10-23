import unittest
from datetime import date
from decimal import Decimal
import importlib.util
from unittest.mock import Mock, patch
from pathlib import Path

_ARQUIVO = Path(__file__).resolve().parents[1] / 'services' / 'efrotas_service.py'
_SPEC = importlib.util.spec_from_file_location('efrotas_service_testado', _ARQUIVO)
_MODULO = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULO)
EventoEFrotasInvalido = _MODULO.EventoEFrotasInvalido
EFrotasConsultaErro = _MODULO.EFrotasConsultaErro
interpretar_evento_infracao = _MODULO.interpretar_evento_infracao
consultar_infracoes_por_placa = _MODULO.consultar_infracoes_por_placa
status_consulta_efrotas = _MODULO.status_consulta_efrotas


class EFrotasServiceTest(unittest.TestCase):
    def test_normaliza_payload_documentado_de_infracao(self):
        resultado = interpretar_evento_infracao({
            'tipoEvento': 42,
            'idRastreamento': 'dbcf7b24-2230-4fcb-9f8b-1d1e4d2e2c77',
            'placa': 'abc-1d23',
            'codigoOrgaoAutuador': '123',
            'numeroAutoInfracao': 'A000456',
            'codigoInfracao': '74550',
            'dataAutuacao': '2026-10-20T10:30:00',
            'valorOriginal': '293,47',
            'descricaoInfracao': 'Transitar em velocidade superior à máxima',
            'gravidade': 'Grave',
            'dataLimiteDefesaAutuacao': '2026-11-20',
        })

        self.assertEqual(resultado['placa'], 'ABC1D23')
        self.assertEqual(resultado['valor'], Decimal('293.47'))
        self.assertEqual(resultado['data_infracao'], date(2026, 10, 20))
        self.assertEqual(resultado['data_limite_defesa'], date(2026, 11, 20))
        self.assertEqual(resultado['tipo_evento'], '42')

    def test_aceita_infracao_aninhada(self):
        resultado = interpretar_evento_infracao({
            'idRastreamento': 'evento-1',
            'infracao': {
                'placa': 'BRA2E19',
                'codigoOrgao': '900',
                'numeroAit': 'XYZ-9',
                'codigoInfracao': '5002-0',
                'dataInfracao': '2026-10-21',
                'valor': 195.23,
            },
        })

        self.assertEqual(resultado['codigo_orgao_autuador'], '900')
        self.assertEqual(resultado['numero_auto_infracao'], 'XYZ-9')
        self.assertEqual(resultado['valor'], Decimal('195.23'))

    def test_evento_de_alteracao_usa_metadado_da_raiz(self):
        resultado = interpretar_evento_infracao({
            'tipoEvento': 10,
            'idRastreamento': 'alteracao-10',
            'infracao': {
                'tipoEvento': 0,
                'placa': 'BRA2E19',
                'codigoOrgaoAutuador': '900',
                'numeroAutoInfracao': 'XYZ-9',
                'codigoInfracao': '5002-0',
                'dataAutuacao': '21/10/2026',
                'valorOriginal': '195,23',
            },
        })

        self.assertEqual(resultado['tipo_evento'], '10')
        self.assertEqual(resultado['id_rastreamento'], 'alteracao-10')
        self.assertEqual(resultado['data_infracao'], date(2026, 10, 21))

    def test_evento_de_penalidade_le_vencimento_documentado(self):
        resultado = interpretar_evento_infracao({
            'tipoEvento': 2,
            'placa': 'BRA2E19',
            'codigoOrgaoAutuador': '900',
            'numeroAutoInfracao': 'XYZ-9',
            'codigoInfracao': '5002-0',
            'dataAutuacao': '2026-10-21',
            'valorOriginal': 195.23,
            'dataVencimentoNotificacaoPenalidade': '2026-12-01',
        })

        self.assertEqual(resultado['data_vencimento_penalidade'], date(2026, 12, 1))

    def test_resposta_historica_documentada_mantem_campos_oficiais(self):
        resultado = interpretar_evento_infracao({
            'placa': 'BRA2E19',
            'codigoOrgaoAutuador': '900',
            'numeroAutoInfracao': 'XYZ-9',
            'codigoInfracao': '5002-0',
            'dataAutuacao': '2026-10-21',
            'valorIntegralInfracao': 195.23,
            'dataVencimentoPenalidade': '2026-12-01',
            'dataPagamento': '2026-11-01',
            'indicadorCanceladaSuspensa': True,
        })

        self.assertEqual(resultado['valor'], Decimal('195.23'))
        self.assertEqual(resultado['data_vencimento_penalidade'], date(2026, 12, 1))
        self.assertEqual(resultado['data_pagamento'], date(2026, 11, 1))
        self.assertIs(resultado['cancelada_suspensa'], True)

    def test_consulta_historica_usa_endpoint_por_placa(self):
        resposta = Mock(ok=True)
        resposta.json.return_value = [{'placa': 'ABC1D23'}]
        with patch.dict('os.environ', {
            'EFROTAS_API_BASE_URL': 'https://hom-efrotas.exemplo.gov.br/efrotas/api',
            'EFROTAS_API_TOKEN': 'token-de-homologacao',
        }, clear=True), patch.object(_MODULO.requests, 'get', return_value=resposta) as get:
            dados = consultar_infracoes_por_placa('abc-1d23', date(2025, 1, 1), date(2025, 12, 31))

        self.assertEqual(dados, [{'placa': 'ABC1D23'}])
        self.assertEqual(
            get.call_args.args[0],
            'https://hom-efrotas.exemplo.gov.br/efrotas/api/consultas/v1/infracoes/placa/ABC1D23?dataInicial=2025-01-01&dataFinal=2025-12-31',
        )
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer token-de-homologacao')

    def test_consulta_historica_recusa_resposta_que_nao_e_lista(self):
        resposta = Mock(ok=True)
        resposta.json.return_value = {'items': []}
        with patch.dict('os.environ', {
            'EFROTAS_API_BASE_URL': 'https://hom-efrotas.exemplo.gov.br/efrotas/api',
            'EFROTAS_API_TOKEN': 'token-de-homologacao',
        }, clear=True), patch.object(_MODULO.requests, 'get', return_value=resposta):
            with self.assertRaisesRegex(EFrotasConsultaErro, 'formato incompatível'):
                consultar_infracoes_por_placa('ABC1D23', date(2025, 1, 1), date(2025, 12, 31))

    def test_secret_pem_habilita_consulta_de_producao_sem_arquivo_montado(self):
        with patch.dict('os.environ', {
            'EFROTAS_API_BASE_URL': 'https://efrotas.estaleiro.serpro.gov.br/efrotas/api',
            'EFROTAS_CLIENT_CERT_PEM': '-----BEGIN CERTIFICATE-----\ncertificado\n-----END CERTIFICATE-----',
            'EFROTAS_CLIENT_KEY_PEM': '-----BEGIN PRIVATE KEY-----\nchave\n-----END PRIVATE KEY-----',
        }, clear=True):
            status = status_consulta_efrotas()

        self.assertTrue(status['habilitada'])
        self.assertEqual(status['modo'], 'certificado')

    def test_recusa_evento_sem_identificador_oficial(self):
        with self.assertRaisesRegex(EventoEFrotasInvalido, 'numeroAutoInfracao'):
            interpretar_evento_infracao({
                'placa': 'BRA2E19',
                'codigoOrgaoAutuador': '900',
                'codigoInfracao': '5002-0',
                'dataAutuacao': '2026-10-21',
                'valorOriginal': 195.23,
            })


if __name__ == '__main__':
    unittest.main()
