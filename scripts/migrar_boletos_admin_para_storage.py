"""
Backfill do B-11: move o PDF dos boletos do ADMIN da coluna base64 para o Storage.

NÃO RODA SOZINHO. Nada no boot nem no CI chama este script — ele mexe em
registro financeiro e precisa de gente olhando, com backup feito antes.

Como funciona, e por que nesta ordem:

1. pega os boletos que ainda têm `arquivo_pdf` e não têm `arquivo_path`;
2. sobe o conteúdo para o bucket;
3. SÓ ENTÃO grava o path e limpa a coluna base64, na mesma transação.

Se o upload falhar, o registro fica exatamente como estava — o base64 nunca é
apagado antes de o arquivo existir no Storage. E rodar duas vezes é seguro: o
filtro exclui quem já tem path.

Este é o banco do ADMIN, que tem dados financeiros reais e deploy manual.
O ritual do DEPLOY.md vale aqui: pg_dump antes, auditoria de contagens antes
e depois.

Uso:
    cd backend
    # 1) Ver o que seria feito, sem escrever nada:
    PYTHONPATH=. python scripts/migrar_boletos_admin_para_storage.py --simular
    # 2) Rodar de verdade, em lotes pequenos:
    PYTHONPATH=. python scripts/migrar_boletos_admin_para_storage.py --lote 25
    # 3) Manter o base64 (migra sem apagar), se quiser conferir antes:
    PYTHONPATH=. python scripts/migrar_boletos_admin_para_storage.py --lote 25 --manter-base64

Antes de rodar, com o banco admin:
    SELECT COUNT(*) FROM admin_boleto WHERE arquivo_pdf IS NOT NULL;
    SELECT pg_size_pretty(pg_total_relation_size('admin_boleto'));
e repetir depois, para ver o espaço liberado.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger('migrar_boletos_admin')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lote', type=int, default=25,
                        help='quantos boletos por rodada (padrão 25)')
    parser.add_argument('--simular', action='store_true',
                        help='só lista o que seria migrado, sem escrever')
    parser.add_argument('--manter-base64', action='store_true',
                        help='grava o path mas não limpa a coluna antiga')
    args = parser.parse_args()

    if not os.environ.get('DATABASE_URL_ADMIN'):
        logger.error('DATABASE_URL_ADMIN não definida — abortando antes de tocar o banco.')
        return 2

    from app_admin import create_app
    from config_admin import ProductionConfig
    from extensions_admin import db
    from models_admin import AdminBoleto as Boleto
    from services_admin import storage_admin_service as storage_service

    if not storage_service.disponivel():
        logger.error(
            'Storage do admin não configurado (SUPABASE_URL_ADMIN / '
            'SUPABASE_SERVICE_KEY_ADMIN). Sem isso não há para onde migrar.'
        )
        return 2
    BUCKET_BOLETOS = storage_service.BUCKET_BOLETOS_ADMIN

    app = create_app(ProductionConfig.from_env())

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
            rotulo = f'boleto admin {boleto.id} (imovel {boleto.imovel_id}, {tamanho // 1024} KB de base64)'
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
