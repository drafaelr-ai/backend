from sqlalchemy.sql import func
from extensions import db


class AgendaDemanda(db.Model):
    """
    Agenda de Eventos - Acompanhamento de entregas, visitas, inícios de serviço, etc.
    Pode ser importado de Pagamentos, Orçamento, Cronograma ou cadastrado manualmente.
    Eventos passados somem automaticamente (comportamento de agenda).
    """
    __tablename__ = 'agenda_demanda'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)

    descricao = db.Column(db.String(255), nullable=False)
    tipo = db.Column(db.String(50), nullable=False, default='material')  # material, servico, visita, outro
    fornecedor = db.Column(db.String(255), nullable=True)
    telefone = db.Column(db.String(50), nullable=True)

    valor = db.Column(db.Float, nullable=True)

    data_prevista = db.Column(db.Date, nullable=False)
    horario = db.Column(db.String(10), nullable=True)
    data_conclusao = db.Column(db.Date, nullable=True)

    status = db.Column(db.String(50), nullable=False, default='aguardando')

    origem = db.Column(db.String(50), nullable=False, default='manual')

    pagamento_servico_id = db.Column(db.Integer, db.ForeignKey('pagamento_servico.id'), nullable=True)
    orcamento_item_id = db.Column(db.Integer, db.ForeignKey('orcamento_eng_item.id'), nullable=True)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)

    observacoes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    obra = db.relationship('Obra', backref=db.backref('agenda_demandas', lazy='dynamic'))

    def to_dict(self):
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'descricao': self.descricao,
            'tipo': self.tipo,
            'fornecedor': self.fornecedor,
            'telefone': self.telefone,
            'valor': float(self.valor) if self.valor else None,
            'data_prevista': self.data_prevista.isoformat() if self.data_prevista else None,
            'horario': self.horario,
            'data_conclusao': self.data_conclusao.isoformat() if self.data_conclusao else None,
            'status': self.status,
            'origem': self.origem,
            'pagamento_servico_id': self.pagamento_servico_id,
            'orcamento_item_id': self.orcamento_item_id,
            'servico_id': self.servico_id,
            'observacoes': self.observacoes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
