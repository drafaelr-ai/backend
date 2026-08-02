from decimal import Decimal

from sqlalchemy.sql import func

from extensions import db


STATUS_PLANEJAMENTO = (
    'a_planejar',
    'pronto',
    'em_andamento',
    'impedido',
    'concluido',
)
ORIGENS_PLANEJAMENTO = ('manual', 'orcamento', 'planilha')
PRIORIDADES_PLANEJAMENTO = ('baixa', 'normal', 'alta', 'critica')


class PlanejamentoAtividade(db.Model):
    __tablename__ = 'planejamento_atividade'
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('a_planejar','pronto','em_andamento','impedido','concluido')",
            name='ck_planejamento_atividade_status',
        ),
        db.CheckConstraint(
            "origem IN ('manual','orcamento','planilha')",
            name='ck_planejamento_atividade_origem',
        ),
        db.CheckConstraint(
            "prioridade IN ('baixa','normal','alta','critica')",
            name='ck_planejamento_atividade_prioridade',
        ),
        db.CheckConstraint(
            'quantidade_planejada >= 0 AND quantidade_executada >= 0',
            name='ck_planejamento_atividade_quantidades',
        ),
        db.CheckConstraint(
            'data_fim IS NULL OR data_inicio IS NULL OR data_fim >= data_inicio',
            name='ck_planejamento_atividade_datas',
        ),
        db.Index(
            'uq_planejamento_atividade_orcamento_item',
            'obra_id',
            'orcamento_item_id',
            unique=True,
            postgresql_where=db.text('orcamento_item_id IS NOT NULL'),
            sqlite_where=db.text('orcamento_item_id IS NOT NULL'),
        ),
        db.Index('idx_planejamento_atividade_obra_status', 'obra_id', 'status'),
        db.Index(
            'idx_planejamento_atividade_obra_periodo',
            'obra_id',
            'data_inicio',
            'data_fim',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(
        db.Integer,
        db.ForeignKey('obra.id', ondelete='CASCADE'),
        nullable=False,
    )
    orcamento_item_id = db.Column(
        db.Integer,
        db.ForeignKey('orcamento_eng_item.id', ondelete='SET NULL'),
        nullable=True,
    )
    cronograma_id = db.Column(
        db.Integer,
        db.ForeignKey('cronograma_obra.id', ondelete='SET NULL'),
        nullable=True,
    )

    titulo = db.Column(db.String(240), nullable=False)
    descricao = db.Column(db.Text, nullable=True)
    etapa_nome = db.Column(db.String(200), nullable=True)
    origem = db.Column(db.String(20), nullable=False, default='manual')
    status = db.Column(db.String(20), nullable=False, default='a_planejar')
    prioridade = db.Column(db.String(20), nullable=False, default='normal')

    responsavel = db.Column(db.String(160), nullable=True)
    equipe = db.Column(db.String(160), nullable=True)
    data_inicio = db.Column(db.Date, nullable=True)
    data_fim = db.Column(db.Date, nullable=True)

    quantidade_planejada = db.Column(db.Numeric(14, 3), nullable=False, default=0)
    quantidade_executada = db.Column(db.Numeric(14, 3), nullable=False, default=0)
    unidade = db.Column(db.String(24), nullable=False, default='un')
    observacoes = db.Column(db.Text, nullable=True)

    criado_por_user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='SET NULL'),
        nullable=True,
    )
    versao = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    obra = db.relationship('Obra', lazy='joined')
    orcamento_item = db.relationship('OrcamentoEngItem', lazy='joined')
    cronograma = db.relationship('CronogramaObra', lazy='joined')
    apontamentos = db.relationship(
        'PlanejamentoApontamento',
        back_populates='atividade',
        lazy='selectin',
        cascade='all, delete-orphan',
        passive_deletes=True,
        order_by='PlanejamentoApontamento.created_at.desc()',
    )
    restricoes = db.relationship(
        'PlanejamentoRestricao',
        back_populates='atividade',
        lazy='selectin',
        cascade='all, delete-orphan',
        passive_deletes=True,
        order_by='PlanejamentoRestricao.created_at.desc()',
    )

    @staticmethod
    def _number(value):
        if value is None:
            return 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    @property
    def percentual_conclusao(self):
        planejada = self._number(self.quantidade_planejada)
        executada = self._number(self.quantidade_executada)
        if planejada <= 0:
            return 100.0 if self.status == 'concluido' else 0.0
        return round(min(100.0, max(0.0, executada / planejada * 100)), 1)

    def to_dict(self, include_relations=True):
        item = self.orcamento_item
        result = {
            'id': self.id,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'orcamento_item_id': self.orcamento_item_id,
            'orcamento_codigo': item.codigo if item else None,
            'cronograma_id': self.cronograma_id,
            'titulo': self.titulo,
            'descricao': self.descricao,
            'etapa_nome': self.etapa_nome,
            'origem': self.origem,
            'status': self.status,
            'prioridade': self.prioridade,
            'responsavel': self.responsavel,
            'equipe': self.equipe,
            'data_inicio': self.data_inicio.isoformat() if self.data_inicio else None,
            'data_fim': self.data_fim.isoformat() if self.data_fim else None,
            'quantidade_planejada': self._number(self.quantidade_planejada),
            'quantidade_executada': self._number(self.quantidade_executada),
            'percentual_conclusao': self.percentual_conclusao,
            'unidade': self.unidade,
            'observacoes': self.observacoes,
            'criado_por_user_id': self.criado_por_user_id,
            'versao': self.versao,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_relations:
            result['apontamentos'] = [a.to_dict() for a in self.apontamentos]
            result['restricoes'] = [r.to_dict() for r in self.restricoes]
            result['restricoes_abertas'] = sum(
                1 for restricao in self.restricoes if restricao.status == 'aberta'
            )
        return result
