from datetime import datetime

from extensions import db


class FrotaAbastecimento(db.Model):
    """Abastecimento de veículo. Local é SNAPSHOT copiado do veículo no POST."""
    __tablename__ = 'frota_abastecimento'

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(
        db.Integer, db.ForeignKey('frota_veiculo.id', ondelete='CASCADE'), nullable=False,
    )
    data = db.Column(db.Date, nullable=False)
    litros = db.Column(db.Numeric(10, 2), nullable=True)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    km = db.Column(db.Integer, nullable=True)
    combustivel = db.Column(db.String(20), nullable=True)
    posto = db.Column(db.String(160), nullable=True)
    condutor_id = db.Column(
        db.Integer, db.ForeignKey('frota_condutor.id', ondelete='SET NULL'), nullable=True,
    )
    local_tipo = db.Column(db.String(10), nullable=True)  # snapshot: obra | imovel | NULL
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    imovel_id = db.Column(db.Integer, nullable=True)
    local_nome = db.Column(db.String(160), nullable=True)
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
            'data': self.data.isoformat() if self.data else None,
            'litros': float(self.litros) if self.litros is not None else None,
            'valor': float(self.valor) if self.valor is not None else None,
            'km': self.km,
            'combustivel': self.combustivel,
            'posto': self.posto,
            'condutor_id': self.condutor_id,
            'condutor_nome': self.condutor.nome if self.condutor else None,
            'local_tipo': self.local_tipo,
            'obra_id': self.obra_id,
            'imovel_id': self.imovel_id,
            'local_nome': self.local_nome,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
