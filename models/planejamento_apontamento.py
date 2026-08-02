from decimal import Decimal

from sqlalchemy.sql import func

from extensions import db


class PlanejamentoApontamento(db.Model):
    __tablename__ = 'planejamento_apontamento'
    __table_args__ = (
        db.CheckConstraint('quantidade > 0', name='ck_planejamento_apontamento_quantidade'),
        db.CheckConstraint(
            "tipo_apontamento IN ('quantidade','percentual')",
            name='ck_planejamento_apontamento_tipo',
        ),
        db.CheckConstraint(
            'percentual IS NULL OR (percentual > 0 AND percentual <= 100)',
            name='ck_planejamento_apontamento_percentual',
        ),
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
    tipo_apontamento = db.Column(
        db.String(20), nullable=False, default='quantidade', server_default='quantidade'
    )
    percentual = db.Column(db.Numeric(5, 2), nullable=True)
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
            'tipo_apontamento': self.tipo_apontamento or 'quantidade',
            'percentual': float(self.percentual) if self.percentual is not None else None,
            'data_apontamento': (
                self.data_apontamento.isoformat() if self.data_apontamento else None
            ),
            'observacao': self.observacao,
            'registrado_por_user_id': self.registrado_por_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
