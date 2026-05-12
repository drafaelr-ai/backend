from sqlalchemy.sql import func
from extensions import db


class FechamentoCaixa(db.Model):
    """Fechamento mensal do caixa com relatório"""
    __tablename__ = 'fechamento_caixa'

    id = db.Column(db.Integer, primary_key=True)
    caixa_id = db.Column(db.Integer, db.ForeignKey('caixa_obra.id'), nullable=False)
    mes = db.Column(db.Integer, nullable=False)  # 1-12
    ano = db.Column(db.Integer, nullable=False)  # 2025
    saldo_inicial = db.Column(db.Float, nullable=False)
    total_entradas = db.Column(db.Float, nullable=False)
    total_saidas = db.Column(db.Float, nullable=False)
    saldo_final = db.Column(db.Float, nullable=False)
    quantidade_movimentacoes = db.Column(db.Integer, nullable=False)
    quantidade_comprovantes = db.Column(db.Integer, nullable=False)
    pdf_url = db.Column(db.String(500), nullable=True)
    fechado_em = db.Column(db.DateTime, nullable=False, default=func.now())
    fechado_por = db.Column(db.Integer, nullable=True)  # Sem FK para permitir exclusão de usuários

    # Índice composto para consulta rápida por período
    __table_args__ = (
        db.Index('idx_fechamento_periodo', 'caixa_id', 'ano', 'mes'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'caixa_id': self.caixa_id,
            'mes': self.mes,
            'ano': self.ano,
            'saldo_inicial': self.saldo_inicial,
            'total_entradas': self.total_entradas,
            'total_saidas': self.total_saidas,
            'saldo_final': self.saldo_final,
            'quantidade_movimentacoes': self.quantidade_movimentacoes,
            'quantidade_comprovantes': self.quantidade_comprovantes,
            'pdf_url': self.pdf_url,
            'fechado_em': self.fechado_em.isoformat() if self.fechado_em else None,
            'fechado_por': self.fechado_por
        }
