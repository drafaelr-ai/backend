from datetime import datetime

from extensions import db


class AlmoxarifadoMovimentacao(db.Model):
    """Entrada, saída ou ajuste de um item do almoxarifado."""
    __tablename__ = 'almoxarifado_movimentacao'

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(
        db.Integer, db.ForeignKey('almoxarifado_item.id', ondelete='CASCADE'), nullable=False,
    )
    tipo = db.Column(db.String(20), nullable=False)  # entrada | saida | ajuste
    quantidade = db.Column(db.Numeric(12, 2), nullable=False)
    data_movimentacao = db.Column(db.Date, nullable=False)
    funcionario_id = db.Column(
        db.Integer, db.ForeignKey('funcionario.id', ondelete='SET NULL'), nullable=True,
    )
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    usuario_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', lazy=True)
    obra = db.relationship('Obra', lazy=True)
    usuario = db.relationship('User', lazy=True)

    def variacao(self):
        quantidade = float(self.quantidade or 0)
        if self.tipo == 'saida':
            return -quantidade
        return quantidade

    def to_dict(self):
        return {
            'id': self.id,
            'item_id': self.item_id,
            'item_nome': self.item.nome if self.item else None,
            'tipo': self.tipo,
            'quantidade': float(self.quantidade or 0),
            'variacao': self.variacao(),
            'unidade': self.item.unidade if self.item else 'un',
            'data_movimentacao': self.data_movimentacao.isoformat() if self.data_movimentacao else None,
            'funcionario_id': self.funcionario_id,
            'funcionario_nome': self.funcionario.nome if self.funcionario else None,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'usuario_nome': self.usuario.username if self.usuario else None,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
