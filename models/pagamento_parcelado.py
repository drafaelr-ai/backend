import logging
from datetime import date
from extensions import db

logger = logging.getLogger(__name__)


class PagamentoParcelado(db.Model):
    """Pagamentos parcelados (ex: 1/10, 2/10, etc)"""
    __tablename__ = 'pagamento_parcelado_v2'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    fornecedor = db.Column(db.String(150), nullable=True)

    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)

    segmento = db.Column(db.String(50), nullable=True, default='Material')

    valor_total = db.Column(db.Float, nullable=False)
    numero_parcelas = db.Column(db.Integer, nullable=False)
    valor_parcela = db.Column(db.Float, nullable=False)
    data_primeira_parcela = db.Column(db.Date, nullable=False)
    periodicidade = db.Column(db.String(10), nullable=False, default='Mensal')  # Semanal ou Mensal

    parcelas_pagas = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default='Ativo')  # Ativo/Concluído/Cancelado
    observacoes = db.Column(db.Text, nullable=True)
    pix = db.Column(db.String(255), nullable=True)
    forma_pagamento = db.Column(db.String(20), nullable=True, default='PIX')

    def to_dict(self):
        from models.parcela_individual import ParcelaIndividual
        from models.servico import Servico
        from models.orcamento_eng_item import OrcamentoEngItem

        def add_months_safe(source_date, months):
            import calendar
            month = source_date.month - 1 + months
            year = source_date.year + month // 12
            month = month % 12 + 1
            day = min(source_date.day, calendar.monthrange(year, month)[1])
            return date(year, month, day)

        proxima_parcela = None
        proxima_parcela_numero = None
        proxima_parcela_vencimento = None
        valor_proxima_parcela = self.valor_parcela

        try:
            proxima_parcela = ParcelaIndividual.query.filter(
                ParcelaIndividual.pagamento_parcelado_id == self.id,
                ParcelaIndividual.status != 'Pago'
            ).order_by(ParcelaIndividual.numero_parcela.asc()).first()

            if proxima_parcela:
                proxima_parcela_numero = proxima_parcela.numero_parcela
                proxima_parcela_vencimento = proxima_parcela.data_vencimento.isoformat() if proxima_parcela.data_vencimento else None
                valor_proxima_parcela = proxima_parcela.valor_parcela

                if proxima_parcela_numero == 0:
                    proxima_parcela_numero = 0
        except Exception as e:
            logger.exception(f"[AVISO] Erro ao buscar próxima parcela: {e}")
            proxima_parcela_numero = self.parcelas_pagas + 1
            if proxima_parcela_numero <= self.numero_parcelas:
                try:
                    if self.periodicidade == 'Semanal':
                        from datetime import timedelta
                        dias_incremento = (proxima_parcela_numero - 1) * 7
                        proxima_data = self.data_primeira_parcela + timedelta(days=dias_incremento)
                        proxima_parcela_vencimento = proxima_data.isoformat()
                    else:
                        proxima_data = add_months_safe(self.data_primeira_parcela, (proxima_parcela_numero - 1))
                        proxima_parcela_vencimento = proxima_data.isoformat()
                except Exception:
                    logger.warning("Excecao suprimida em ", exc_info=True)
                    pass

        servico_nome = None
        if self.servico_id:
            try:
                servico = Servico.query.get(self.servico_id)
                servico_nome = servico.nome if servico else None
            except Exception as e:
                logger.exception(f"[AVISO] Erro ao buscar serviço {self.servico_id}: {e}")
                servico_nome = None

        orcamento_item_id = None
        orcamento_item_nome = None
        try:
            result = db.session.execute(db.text(
                f"SELECT orcamento_item_id FROM pagamento_parcelado_v2 WHERE id = {self.id}"
            )).fetchone()
            if result and result[0]:
                orcamento_item_id = result[0]
                item = OrcamentoEngItem.query.get(orcamento_item_id)
                if item:
                    orcamento_item_nome = f"{item.codigo} - {item.descricao}"
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            pass

        try:
            segmento_value = self.segmento if hasattr(self, 'segmento') and self.segmento else 'Material'
        except Exception:
            segmento_value = 'Material'

        try:
            tem_entrada = ParcelaIndividual.query.filter_by(
                pagamento_parcelado_id=self.id,
                numero_parcela=0
            ).first() is not None
        except Exception:
            tem_entrada = False

        pix_value = None
        forma_pagamento_value = 'PIX'

        try:
            if hasattr(self, 'pix'):
                pix_value = self.pix
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            pass

        try:
            if hasattr(self, 'forma_pagamento'):
                forma_pagamento_value = self.forma_pagamento or 'PIX'
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            pass

        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "fornecedor": self.fornecedor,
            "segmento": segmento_value,
            "valor_total": self.valor_total,
            "numero_parcelas": self.numero_parcelas,
            "valor_parcela": self.valor_parcela,
            "valor_proxima_parcela": valor_proxima_parcela,
            "data_primeira_parcela": self.data_primeira_parcela.isoformat() if self.data_primeira_parcela else None,
            "periodicidade": self.periodicidade,
            "parcelas_pagas": self.parcelas_pagas,
            "status": self.status,
            "observacoes": self.observacoes,
            "pix": pix_value,
            "forma_pagamento": forma_pagamento_value,
            "proxima_parcela_numero": proxima_parcela_numero if proxima_parcela_numero is not None else None,
            "proxima_parcela_vencimento": proxima_parcela_vencimento,
            "tem_entrada": tem_entrada,
            "servico_id": self.servico_id,
            "servico_nome": servico_nome,
            "orcamento_item_id": orcamento_item_id,
            "orcamento_item_nome": orcamento_item_nome
        }
