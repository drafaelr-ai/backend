from sqlalchemy.sql import func

from extensions import db


TIPOS_RESTRICAO = ('material', 'projeto', 'equipe', 'equipamento', 'predecessora', 'outro')
STATUS_RESTRICAO = ('aberta', 'resolvida', 'cancelada')


class PlanejamentoRestricao(db.Model):
    __tablename__ = 'planejamento_restricao'
    __table_args__ = (
        db.CheckConstraint(
            "tipo IN ('material','projeto','equipe','equipamento','predecessora','outro')",
            name='ck_planejamento_restricao_tipo',
        ),
        db.CheckConstraint(
            "status IN ('aberta','resolvida','cancelada')",
            name='ck_planejamento_restricao_status',
        ),
        db.Index(
            'idx_planejamento_restricao_atividade_status',
            'atividade_id',
            'status',
        ),
        db.Index(
            'idx_planejamento_restricao_aberta_limite',
            'data_limite',
            postgresql_where=db.text("status = 'aberta'"),
            sqlite_where=db.text("status = 'aberta'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    atividade_id = db.Column(
        db.Integer,
        db.ForeignKey('planejamento_atividade.id', ondelete='CASCADE'),
        nullable=False,
    )
    tipo = db.Column(db.String(24), nullable=False)
    descricao = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='aberta')
    responsavel = db.Column(db.String(160), nullable=True)
    data_limite = db.Column(db.Date, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    resolvida_em = db.Column(db.DateTime(timezone=True), nullable=True)
    criada_por_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    atividade = db.relationship('PlanejamentoAtividade', back_populates='restricoes')

    def to_dict(self):
        return {
            'id': self.id,
            'atividade_id': self.atividade_id,
            'tipo': self.tipo,
            'descricao': self.descricao,
            'status': self.status,
            'responsavel': self.responsavel,
            'data_limite': self.data_limite.isoformat() if self.data_limite else None,
            'observacoes': self.observacoes,
            'resolvida_em': self.resolvida_em.isoformat() if self.resolvida_em else None,
            'criada_por_user_id': self.criada_por_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
