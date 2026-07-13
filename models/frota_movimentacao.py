from datetime import datetime

from extensions import db


class FrotaMovimentacao(db.Model):
    """Histórico de alocação de um veículo (obra, imóvel do patrimônio ou sem local).

    `destino_nome` é SNAPSHOT do nome do destino no momento da movimentação.
    """
    __tablename__ = 'frota_movimentacao'

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(
        db.Integer, db.ForeignKey('frota_veiculo.id', ondelete='CASCADE'), nullable=False,
    )
    destino_tipo = db.Column(db.String(10), nullable=False)  # obra | imovel | sem_local
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    imovel_id = db.Column(db.Integer, nullable=True)          # referência fraca (banco admin)
    destino_nome = db.Column(db.String(160), nullable=True)   # snapshot
    data_movimentacao = db.Column(db.Date, nullable=False)
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    veiculo = db.relationship('FrotaVeiculo', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'veiculo_id': self.veiculo_id,
            'veiculo_placa': self.veiculo.placa if self.veiculo else None,
            'destino_tipo': self.destino_tipo,
            'obra_id': self.obra_id,
            'imovel_id': self.imovel_id,
            'destino_nome': self.destino_nome,
            'data_movimentacao': self.data_movimentacao.isoformat() if self.data_movimentacao else None,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
