from decimal import Decimal

from sqlalchemy.sql import func

from extensions import db


class PlanejamentoApontamento(db.Model):
    __tablename__ = 'planejamento_apontamento'
    __table_args__ = (
        db.CheckConstraint('quantidade > 0', name='ck_planejamento_apontamento_quantidade'),
        db.Index(
            'idx_planejamento_apontamento_atividade_data',
            'atividade_id',
            'data_apontamento',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    atividade_id = db.Column(
        db.Integer,
        db.ForeignKey('planejamento_atividade.id', ondelete='CASCADE'),
        nullable=False,
    )
    quantidade = db.Column(db.Numeric(14, 3), nullable=False)
    data_apontamento = db.Column(db.Date, nullable=False)
    observacao = db.Column(db.Text, nullable=True)
    registrado_por_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())

    atividade = db.relationship('PlanejamentoAtividade', back_populates='apontamentos')

    def to_dict(self):
        quantidade = self.quantidade
        if isinstance(quantidade, Decimal):
            quantidade = float(quantidade)
        return {
            'id': self.id,
            'atividade_id': self.atividade_id,
            'quantidade': float(quantidade or 0),
            'data_apontamento': (
                self.data_apontamento.isoformat() if self.data_apontamento else None
            ),
            'observacao': self.observacao,
            'registrado_por_user_id': self.registrado_por_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
