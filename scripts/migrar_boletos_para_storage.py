"""
Backfill do B-04: move o PDF dos boletos antigos da coluna base64 para o Storage.

NÃO RODA SOZINHO. Nada no boot nem no CI chama este script — ele mexe em
registro financeiro e precisa de gente olhando, com backup feito antes.

Como funciona, e por que nesta ordem:

1. pega os boletos que ainda têm `arquivo_pdf` e não têm `arquivo_path`;
2. sobe o conteúdo para o bucket;
3. SÓ ENTÃO grava o path e limpa a coluna base64, na mesma transação.

Se o upload falhar, o registro fica exatamente como estava — o base64 nunca é
apagado antes de o arquivo existir no Storage. E rodar duas vezes é seguro: o
filtro exclui quem já tem path.

Uso:
    cd backend
    # 1) Ver o que seria feito, sem escrever nada:
    python scripts/migrar_boletos_para_storage.py --simular
    # 2) Rodar de verdade, em lotes pequenos:
    python scripts/migrar_boletos_para_storage.py --lote 25
    # 3) Manter o base64 (migra sem apagar), se quiser conferir antes:
    python scripts/migrar_boletos_para_storage.py --lote 25 --manter-base64

Antes de rodar, com o banco de produção:
    SELECT COUNT(*) FROM boleto WHERE arquivo_pdf IS NOT NULL;
    SELECT pg_size_pretty(pg_total_relation_size('boleto'));
e repetir depois, para ver o espaço liberado.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger('migrar_boletos')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lote', type=int, default=25,
                        help='quantos boletos por rodada (padrão 25)')
    parser.add_argument('--simular', action='store_true',
                        help='só lista o que seria migrado, sem escrever')
    parser.add_argument('--manter-base64', action='store_true',
                        help='grava o path mas não limpa a coluna antiga')
    args = parser.parse_args()

    if not os.environ.get('DB_PASSWORD'):
        logger.error('DB_PASSWORD não definida — abortando antes de tocar o banco.')
        return 2

    from flask import Flask
    from config import Config
    from extensions import db
    import models  # noqa: F401
    from models import Boleto
    from services import storage_service
    from routes.boletos import BUCKET_BOLETOS

    app = Flask(__name__)
    app.config.from_object(Config)
    app.config['SQLALCHEMY_DATABASE_URI'] = Config.SQLALCHEMY_DATABASE_URI
    db.init_app(app)

    with app.app_context():
        pendentes = (Boleto.query
                     .filter(Boleto.arquivo_pdf.isnot(None),
                             Boleto.arquivo_path.is_(None))
                     .order_by(Boleto.id.asc())
                     .limit(args.lote).all())
        total_restante = (Boleto.query
                          .filter(Boleto.arquivo_pdf.isnot(None),
                                  Boleto.arquivo_path.is_(None))
                          .count())
        logger.info('%s boleto(s) com base64 pendente; processando %s nesta rodada.',
                    total_restante, len(pendentes))
        if not pendentes:
            logger.info('Nada a fazer.')
            return 0

        migrados = falhas = 0
        for boleto in pendentes:
            tamanho = len(boleto.arquivo_pdf or '')
            rotulo = f'boleto {boleto.id} (obra {boleto.obra_id}, {tamanho // 1024} KB de base64)'
            if args.simular:
                logger.info('[simulação] migraria %s', rotulo)
                continue
            try:
                path = storage_service.upload_base64(
                    boleto.arquivo_pdf,
                    'boletos',
                    nome=boleto.arquivo_nome or f'boleto-{boleto.id}.pdf',
                    bucket=BUCKET_BOLETOS,
                    content_type='application/pdf',
                )
            except Exception:
                # Falha de upload não toca o registro: o base64 segue intacto.
                logger.exception('FALHA no upload de %s — registro inalterado', rotulo)
                falhas += 1
                continue
            boleto.arquivo_path = path
            if not args.manter_base64:
                boleto.arquivo_pdf = None
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                logger.exception('FALHA ao gravar %s — registro inalterado', rotulo)
                falhas += 1
                continue
            migrados += 1
            logger.info('OK %s -> %s', rotulo, path)

        if args.simular:
            logger.info('Simulação: %s boleto(s) seriam migrados.', len(pendentes))
            return 0
        logger.info('Migrados: %s | falhas: %s | ainda pendentes: %s',
                    migrados, falhas,
                    total_restante - migrados)
        if falhas:
            logger.warning('Houve falha: rode de novo para tentar os que sobraram.')
        return 1 if falhas else 0


if __name__ == '__main__':
    sys.exit(main())
