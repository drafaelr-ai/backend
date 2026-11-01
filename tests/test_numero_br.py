import unittest

from services.numero_br import para_inteiro, para_numero


class ParaNumeroDinheiroTest(unittest.TestCase):
    """milhar=True (padrao): o caso de dinheiro."""

    def test_milhar_sem_centavos(self):
        # O bug que isto conserta: '1.250' virava 1.25 em doze lugares.
        self.assertEqual(para_numero('1.250'), 1250.0)
        self.assertEqual(para_numero('10.000'), 10000.0)
        self.assertEqual(para_numero('1.000.000'), 1000000.0)
        self.assertEqual(para_numero('R$ 2.500'), 2500.0)
        self.assertEqual(para_numero('-1.250'), -1250.0)

    def test_milhar_com_centavos(self):
        self.assertEqual(para_numero('2.640,00'), 2640.0)
        self.assertEqual(para_numero('1.250,50'), 1250.5)
        self.assertEqual(para_numero('R$ 1.250,50'), 1250.5)

    def test_virgula_sozinha(self):
        self.assertEqual(para_numero('2640,00'), 2640.0)
        self.assertEqual(para_numero('250,33'), 250.33)

    def test_ponto_decimal_nao_e_quebrado(self):
        # Nao e milhar: o grupo depois do ponto nao tem 3 digitos.
        self.assertEqual(para_numero('1250.50'), 1250.5)
        self.assertEqual(para_numero('12.34'), 12.34)
        self.assertEqual(para_numero('1.25'), 1.25)
        self.assertEqual(para_numero('0.5'), 0.5)

    def test_numero_nativo_passa_direto(self):
        self.assertEqual(para_numero(1250), 1250.0)
        self.assertEqual(para_numero(12.5), 12.5)
        self.assertEqual(para_numero(0), 0.0)

    def test_vazio_e_invalido_viram_none(self):
        for v in (None, '', '   ', 'abc', 'R$', '--', 'R$ abc'):
            with self.subTest(v=v):
                self.assertIsNone(para_numero(v))

    def test_booleano_nao_e_numero(self):
        # bool e int em Python: sem a guarda, True entraria como R$ 1,00.
        self.assertIsNone(para_numero(True))
        self.assertIsNone(para_numero(False))

    def test_nan_e_infinito_viram_none(self):
        self.assertIsNone(para_numero(float('nan')))
        self.assertIsNone(para_numero(float('inf')))
        self.assertIsNone(para_numero('nan'))
        self.assertIsNone(para_numero('inf'))


class ParaNumeroPrecoUnitarioTest(unittest.TestCase):
    """milhar=False: tres decimais, como preco por litro."""

    def test_ponto_e_sempre_decimal(self):
        # R$ 5,899 por litro e normal; R$ 5.899 por litro e absurdo.
        self.assertEqual(para_numero('5.899', milhar=False), 5.899)
        self.assertEqual(para_numero('1.250', milhar=False), 1.25)

    def test_virgula_continua_inequivoca(self):
        self.assertEqual(para_numero('5,899', milhar=False), 5.899)
        self.assertEqual(para_numero('1.250,50', milhar=False), 1250.5)


class ParaInteiroTest(unittest.TestCase):
    def test_trunca_e_aceita_formato_br(self):
        self.assertEqual(para_inteiro('80.000'), 80000)
        self.assertEqual(para_inteiro('80000,9'), 80000)
        self.assertEqual(para_inteiro(123.9), 123)

    def test_invalido_vira_none(self):
        self.assertIsNone(para_inteiro('abc'))
        self.assertIsNone(para_inteiro(None))
        self.assertIsNone(para_inteiro(''))


if __name__ == '__main__':
    unittest.main()
