from datetime import datetime

from extensions import db


class SolicitacaoCotacao(db.Model):
    """Cotação (pesquisa de preços) de uma solicitação de compra."""
    __tablename__ = 'solicitacao_cotacao'

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer, db.ForeignKey('solicitacao_compra.id', ondelete='CASCADE'), nullable=False,
    )
    fornecedor = db.Column(db.String(150), nullable=False)
    valor_total = db.Column(db.Numeric(12, 2), nullable=False)
    condicao_pagamento = db.Column(db.String(200), nullable=True)
    prazo_entrega = db.Column(db.String(100), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    arquivo_url = db.Column(db.String(500), nullable=True)
    criado_por_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    criado_por = db.relationship('User', foreign_keys=[criado_por_id], lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'solicitacao_id': self.solicitacao_id,
            'fornecedor': self.fornecedor,
            'valor_total': float(self.valor_total) if self.valor_total is not None else None,
            'condicao_pagamento': self.condicao_pagamento,
            'prazo_entrega': self.prazo_entrega,
            'observacao': self.observacao,
            'tem_arquivo': bool(self.arquivo_url),
            'criado_por_id': self.criado_por_id,
            'criado_por_nome': self.criado_por.username if self.criado_por else None,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
