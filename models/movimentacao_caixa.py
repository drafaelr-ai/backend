from sqlalchemy.sql import func
from extensions import db


class MovimentacaoCaixa(db.Model):
    """Movimentações (entradas e saídas) do caixa"""
    __tablename__ = 'movimentacao_caixa'

    id = db.Column(db.Integer, primary_key=True)
    caixa_id = db.Column(db.Integer, db.ForeignKey('caixa_obra.id'), nullable=False, index=True)
    data = db.Column(db.DateTime, nullable=False, default=func.now(), index=True)
    tipo = db.Column(db.String(10), nullable=False, index=True)  # 'Entrada' ou 'Saída'
    valor = db.Column(db.Float, nullable=False)
    descricao = db.Column(db.String(500), nullable=False)
    comprovante_url = db.Column(db.Text, nullable=True)  # Base64 da imagem do comprovante
    observacoes = db.Column(db.Text, nullable=True)
    criado_por = db.Column(db.Integer, nullable=True)  # Sem FK para permitir exclusão de usuários
    criado_em = db.Column(db.DateTime, default=func.now())
    atualizado_em = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            'id': self.id,
            'caixa_id': self.caixa_id,
            'data': self.data.isoformat() if self.data else None,
            'tipo': self.tipo,
            'valor': self.valor,
            'descricao': self.descricao,
            'comprovante_url': self.comprovante_url,
            'observacoes': self.observacoes,
            'criado_por': self.criado_por,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None
        }
