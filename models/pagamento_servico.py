from extensions import db


class PagamentoServico(db.Model):
    __tablename__ = 'pagamento_servico'
    id = db.Column(db.Integer, primary_key=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=True)

    valor_total = db.Column(db.Float, nullable=False)
    valor_pago = db.Column(db.Float, nullable=False, default=0.0)

    status = db.Column(db.String(20), nullable=False, default='Pago')
    tipo_pagamento = db.Column(db.String(20), nullable=False)
    forma_pagamento = db.Column(db.String(20), nullable=True)
    pix = db.Column(db.String(100), nullable=True)  # Chave PIX do pagamento
    prioridade = db.Column(db.Integer, nullable=False, default=0)
    fornecedor = db.Column(db.String(150), nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "data": self.data.isoformat(),
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "valor_total": self.valor_total,
            "valor_pago": self.valor_pago,
            "status": self.status,
            "tipo_pagamento": self.tipo_pagamento,
            "forma_pagamento": self.forma_pagamento,
            "pix": self.pix,
            "prioridade": self.prioridade,
            "fornecedor": self.fornecedor,
            "pagamento_id": self.id
        }
