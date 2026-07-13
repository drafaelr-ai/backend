from datetime import datetime

from extensions import db


class FrotaMulta(db.Model):
    """Multa de trânsito de um veículo, com condutor responsável opcional."""
    __tablename__ = 'frota_multa'

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(
        db.Integer, db.ForeignKey('frota_veiculo.id', ondelete='CASCADE'), nullable=False,
    )
    data_infracao = db.Column(db.Date, nullable=False)
    descricao = db.Column(db.String(300), nullable=True)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    pontos = db.Column(db.Integer, nullable=True)
    condutor_id = db.Column(
        db.Integer, db.ForeignKey('frota_condutor.id', ondelete='SET NULL'), nullable=True,
    )
    status_pagamento = db.Column(db.String(20), nullable=False, default='pendente')
    # pendente | paga | contestada
    data_pagamento = db.Column(db.Date, nullable=True)
    arquivo_url = db.Column(db.String(500), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    veiculo = db.relationship('FrotaVeiculo', lazy=True)
    condutor = db.relationship('FrotaCondutor', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'veiculo_id': self.veiculo_id,
            'veiculo_placa': self.veiculo.placa if self.veiculo else None,
            'veiculo_modelo': self.veiculo.modelo if self.veiculo else None,
            'data_infracao': self.data_infracao.isoformat() if self.data_infracao else None,
            'descricao': self.descricao,
            'valor': float(self.valor) if self.valor is not None else None,
            'pontos': self.pontos,
            'condutor_id': self.condutor_id,
            'condutor_nome': self.condutor.nome if self.condutor else None,
            'status_pagamento': self.status_pagamento,
            'data_pagamento': self.data_pagamento.isoformat() if self.data_pagamento else None,
            'arquivo_url': self.arquivo_url,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
