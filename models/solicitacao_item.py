from extensions import db


class SolicitacaoItem(db.Model):
    """Item de uma solicitação de compra (ex.: 50 sc cimento CP-II)."""
    __tablename__ = 'solicitacao_item'

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer, db.ForeignKey('solicitacao_compra.id', ondelete='CASCADE'), nullable=False,
    )
    descricao = db.Column(db.String(300), nullable=False)
    quantidade = db.Column(db.Numeric(12, 2), nullable=False)
    unidade = db.Column(db.String(20), nullable=True)  # un, sc, m³, kg...
    observacao = db.Column(db.String(300), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'solicitacao_id': self.solicitacao_id,
            'descricao': self.descricao,
            'quantidade': float(self.quantidade) if self.quantidade is not None else None,
            'unidade': self.unidade,
            'observacao': self.observacao,
        }
