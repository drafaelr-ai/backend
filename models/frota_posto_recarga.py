from datetime import datetime

from extensions import db


class FrotaPostoRecarga(db.Model):
    """Crédito que o operador coloca na conta de um posto (pré-pago).

    O saldo não tem coluna própria de propósito: ele é sempre
    `soma das recargas - soma dos abastecimentos` daquele posto. Um saldo
    gravado ficaria desatualizado no primeiro abastecimento lançado por
    fora desta tela (link do motorista, lançamento manual, e-Frotas).
    """

    __tablename__ = 'frota_posto_recarga'

    id = db.Column(db.Integer, primary_key=True)
    posto_id = db.Column(
        db.Integer,
        db.ForeignKey('frota_posto_combustivel.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    data = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    observacao = db.Column(db.String(300), nullable=True)
    # Referência fraca ao usuário: o histórico financeiro sobrevive à
    # exclusão da conta de quem lançou.
    criado_por_id = db.Column(db.Integer, nullable=True)
    data_criacao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    posto = db.relationship('FrotaPostoCombustivel', lazy=True)

    def to_dict(self, criado_por_nome=None):
        return {
            'id': self.id,
            'posto_id': self.posto_id,
            'posto_nome': self.posto.nome if self.posto else None,
            'data': self.data.isoformat() if self.data else None,
            'valor': float(self.valor) if self.valor is not None else None,
            'observacao': self.observacao,
            'criado_por_id': self.criado_por_id,
            'criado_por_nome': criado_por_nome,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
