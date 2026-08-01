from decimal import Decimal

from sqlalchemy.sql import func

from extensions import db


class PlanejamentoFechamento(db.Model):
    __tablename__ = 'planejamento_fechamento'
    __table_args__ = (
        db.UniqueConstraint(
            'obra_id',
            'semana_inicio',
            name='uq_planejamento_fechamento_obra_semana',
        ),
        db.CheckConstraint(
            'planejadas >= 0 AND concluidas >= 0 AND ppc >= 0 AND ppc <= 100',
            name='ck_planejamento_fechamento_metricas',
        ),
        db.Index('idx_planejamento_fechamento_obra_semana', 'obra_id', 'semana_inicio'),
    )

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(
        db.Integer,
        db.ForeignKey('obra.id', ondelete='CASCADE'),
        nullable=False,
    )
    semana_inicio = db.Column(db.Date, nullable=False)
    semana_fim = db.Column(db.Date, nullable=False)
    planejadas = db.Column(db.Integer, nullable=False, default=0)
    concluidas = db.Column(db.Integer, nullable=False, default=0)
    ppc = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    motivos_nao_conclusao = db.Column(db.JSON, nullable=True)
    aprendizado = db.Column(db.Text, nullable=True)
    fechado_por_user_id = db.Column(
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

    obra = db.relationship('Obra', lazy='joined')

    def to_dict(self):
        ppc = float(self.ppc) if isinstance(self.ppc, Decimal) else float(self.ppc or 0)
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'semana_inicio': self.semana_inicio.isoformat() if self.semana_inicio else None,
            'semana_fim': self.semana_fim.isoformat() if self.semana_fim else None,
            'planejadas': self.planejadas,
            'concluidas': self.concluidas,
            'ppc': ppc,
            'motivos_nao_conclusao': self.motivos_nao_conclusao or {},
            'aprendizado': self.aprendizado,
            'fechado_por_user_id': self.fechado_por_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
