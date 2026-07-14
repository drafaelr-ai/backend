import logging
from extensions import db

logger = logging.getLogger(__name__)


class ParcelaIndividual(db.Model):
    """Modelo para armazenar valores individuais de cada parcela"""
    __tablename__ = 'parcela_individual'

    id = db.Column(db.Integer, primary_key=True)
    pagamento_parcelado_id = db.Column(db.Integer, db.ForeignKey('pagamento_parcelado_v2.id'), nullable=False, index=True)
    numero_parcela = db.Column(db.Integer, nullable=False)  # 1, 2, 3...
    valor_parcela = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='Previsto', index=True)
    data_pagamento = db.Column(db.Date, nullable=True)
    forma_pagamento = db.Column(db.String(50), nullable=True)
    codigo_barras = db.Column(db.String(60), nullable=True)  # boletos parcelados
    observacao = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        db.Index('idx_parcela_pagamento_numero', 'pagamento_parcelado_id', 'numero_parcela'),
    )

    pagamento_parcelado = db.relationship('PagamentoParcelado', backref=db.backref('parcelas_individuais', cascade='all, delete-orphan'))

    def to_dict(self):
        codigo_barras_value = self.codigo_barras

        return {
            "id": self.id,
            "pagamento_parcelado_id": self.pagamento_parcelado_id,
            "numero_parcela": self.numero_parcela,
            "valor_parcela": self.valor_parcela,
            "data_vencimento": self.data_vencimento.isoformat(),
            "status": self.status,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "forma_pagamento": self.forma_pagamento,
            "codigo_barras": codigo_barras_value,
            "observacao": self.observacao
        }
