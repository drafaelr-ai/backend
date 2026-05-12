from sqlalchemy.sql import func
from extensions import db


class CaixaObra(db.Model):
    """Caixa principal da obra para pequenas despesas"""
    __tablename__ = 'caixa_obra'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False, unique=True)
    saldo_inicial = db.Column(db.Float, default=0, nullable=False)
    saldo_atual = db.Column(db.Float, default=0, nullable=False)
    mes_atual = db.Column(db.Integer, nullable=False)  # 1-12
    ano_atual = db.Column(db.Integer, nullable=False)  # 2025
    status = db.Column(db.String(20), default='Ativo', nullable=False)  # Ativo, Fechado
    criado_em = db.Column(db.DateTime, default=func.now())
    atualizado_em = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    # Relacionamentos
    obra = db.relationship('Obra', backref='caixa')
    movimentacoes = db.relationship('MovimentacaoCaixa', backref='caixa', lazy=True, cascade='all, delete-orphan')
    fechamentos = db.relationship('FechamentoCaixa', backref='caixa', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'saldo_inicial': self.saldo_inicial,
            'saldo_atual': self.saldo_atual,
            'mes_atual': self.mes_atual,
            'ano_atual': self.ano_atual,
            'status': self.status,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None
        }
