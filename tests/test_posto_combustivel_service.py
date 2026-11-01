import unicodedata
import unittest

from services.posto_combustivel_service import (
    PostoInvalido,
    limpar_nome,
    limpar_observacao,
    limpar_valor,
    normalizar_nome,
)


class PostoCombustivelServiceTest(unittest.TestCase):
    def test_limpa_espacos_sem_alterar_nome_exibido(self):
        self.assertEqual(limpar_nome('  Posto   São   José  '), 'Posto São José')

    def test_normaliza_caixa_espacos_e_unicode_para_evitar_duplicatas(self):
        composto = 'Posto São José'
        decomposto = 'POSTO SA\u0303O   JOSE\u0301'
        self.assertEqual(normalizar_nome(composto), normalizar_nome(decomposto))

    def test_nome_vazio_e_opcional(self):
        self.assertIsNone(limpar_nome('   '))

    def test_recusa_nome_curto_ou_longo(self):
        with self.assertRaises(PostoInvalido):
            limpar_nome('P')
        with self.assertRaises(PostoInvalido):
            limpar_nome('P' * 161)


class RecargaValorTest(unittest.TestCase):
    def test_aceita_numero_e_formato_br(self):
        self.assertEqual(limpar_valor(1250.5), 1250.5)
        self.assertEqual(limpar_valor('1.250,50'), 1250.5)
        self.assertEqual(limpar_valor('R$ 1.250,50'), 1250.5)
        self.assertEqual(limpar_valor('250,33'), 250.33)

    def test_arredonda_para_centavos(self):
        self.assertEqual(limpar_valor('10,005'), 10.01)

    def test_recusa_vazio_zero_e_negativo(self):
        for valor in (None, '', 0, '0', -1, '-50', '0,00'):
            with self.subTest(valor=valor), self.assertRaises(PostoInvalido):
                limpar_valor(valor)

    def test_recusa_texto_e_booleano(self):
        # True vira 1.0 em float: sem a checagem explicita um JSON com
        # "valor": true lancaria uma recarga de R$ 1,00.
        for valor in ('abc', 'R$', True, False, float('nan'), float('inf')):
            with self.subTest(valor=valor), self.assertRaises(PostoInvalido):
                limpar_valor(valor)

    def test_recusa_valor_acima_do_teto(self):
        with self.assertRaises(PostoInvalido):
            limpar_valor(10_000_001)

    def test_milhar_br_sem_centavos(self):
        # O campo do modal e texto livre: '1.250' digitado e mil duzentos e
        # cinquenta. Lido como decimal, a recarga entrava mil vezes menor.
        self.assertEqual(limpar_valor('1.250'), 1250.0)
        self.assertEqual(limpar_valor('10.000'), 10000.0)
        self.assertEqual(limpar_valor('R$ 2.500'), 2500.0)
        self.assertEqual(limpar_valor('1.000.000'), 1000000.0)

    def test_ponto_decimal_continua_valendo(self):
        self.assertEqual(limpar_valor('250.50'), 250.5)
        self.assertEqual(limpar_valor('1250.50'), 1250.5)
        self.assertEqual(limpar_valor('12.34'), 12.34)
        self.assertEqual(limpar_valor('1.25'), 1.25)

    def test_observacao_normaliza_espacos_e_corta_no_limite(self):
        self.assertEqual(limpar_observacao('  Deposito   em  dinheiro '),
                         'Deposito em dinheiro')
        self.assertIsNone(limpar_observacao('   '))
        self.assertIsNone(limpar_observacao(None))
        self.assertEqual(len(limpar_observacao('x' * 400)), 300)


class CasamentoLegadoTest(unittest.TestCase):
    """normalizar_nome e a UNICA chave do casamento de abastecimento antigo
    (posto_id nulo). O saldo e o extrato dependem dela; comparar com uma
    expressao SQL equivalente divergia entre Postgres e SQLite."""

    def test_casa_nome_digitado_com_espacos_sobrando(self):
        self.assertEqual(normalizar_nome('Posto   Ipiranga  Centro'),
                         normalizar_nome(' posto ipiranga centro '))

    def test_espaco_interno_distingue_postos(self):
        # Chave sem espaco colapsava estes dois cadastros validos e debitava
        # o abastecimento no posto errado.
        self.assertNotEqual(normalizar_nome('Auto Posto Sol'),
                            normalizar_nome('AutoPosto Sol'))

    def test_casa_acento_em_qualquer_forma_unicode(self):
        # lower() do banco nao normaliza NFD: nome colado de outro sistema
        # nunca casava e o gasto desaparecia do saldo.
        self.assertEqual(
            normalizar_nome('POSTO SÃO JOSÉ'),
            normalizar_nome(unicodedata.normalize('NFD', 'Posto São José')),
        )

    def test_postos_diferentes_nao_colidem(self):
        self.assertNotEqual(normalizar_nome('Posto Centro'),
                            normalizar_nome('Posto Norte'))


if __name__ == '__main__':
    unittest.main()
