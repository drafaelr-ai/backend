import io
import unittest
from unittest.mock import patch

from services import storage_service


class _Resposta:
    def __init__(self, status_code, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _arquivo(nome='Photo 1.jpg', mimetype='image/jpeg'):
    arquivo = io.BytesIO(b'conteudo-do-arquivo')
    arquivo.filename = nome
    arquivo.mimetype = mimetype
    return arquivo


class StorageServiceTest(unittest.TestCase):
    @patch.dict('os.environ', {'SUPABASE_SERVICE_KEY': 'teste'}, clear=False)
    @patch.object(storage_service, 'ensure_bucket', return_value=True)
    @patch.object(storage_service.requests, 'post')
    def test_cria_bucket_quando_storage_retorna_http_400_com_nosuchbucket(
        self, post, ensure_bucket
    ):
        post.side_effect = [
            _Resposta(
                400,
                {
                    'statusCode': '404',
                    'error': 'Bucket not found',
                    'message': 'Bucket not found',
                    'code': 'NoSuchBucket',
                },
            ),
            _Resposta(200, {'Key': 'ok'}),
        ]

        path = storage_service.upload_arquivo(
            _arquivo(), 'solicitacoes/18', bucket='solicitacoes-arquivos'
        )

        self.assertTrue(path.startswith('solicitacoes/18/'))
        self.assertTrue(path.endswith('_Photo_1.jpg'))
        ensure_bucket.assert_called_once_with('solicitacoes-arquivos')
        self.assertEqual(post.call_count, 2)

    @patch.dict('os.environ', {'SUPABASE_SERVICE_KEY': 'teste'}, clear=False)
    @patch.object(storage_service, 'ensure_bucket', return_value=True)
    @patch.object(storage_service.requests, 'post')
    def test_nao_cria_bucket_para_erro_de_upload_diferente(self, post, ensure_bucket):
        post.return_value = _Resposta(
            400,
            {'statusCode': '400', 'code': 'InvalidMimeType'},
            'mime type not allowed',
        )

        with self.assertRaisesRegex(RuntimeError, 'Upload falhou'):
            storage_service.upload_arquivo(
                _arquivo(mimetype='image/heic'),
                'solicitacoes/18',
                bucket='solicitacoes-arquivos',
            )

        ensure_bucket.assert_not_called()
        self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
