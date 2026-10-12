import unittest

from services.posto_combustivel_service import (
    PostoInvalido,
    limpar_nome,
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


if __name__ == '__main__':
    unittest.main()
