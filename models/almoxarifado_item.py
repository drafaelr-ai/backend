from datetime import datetime

from extensions import db


class AlmoxarifadoItem(db.Model):
    """Item controlado no almoxarifado central.

    O saldo nunca é gravado nesta tabela: ele é sempre composto pelas
    movimentações. Isso preserva o histórico e impede ajustes silenciosos.
    """
    __tablename__ = 'almoxarifado_item'

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(60), nullable=True, unique=True)
    nome = db.Column(db.String(160), nullable=False)
    categoria = db.Column(db.String(30), nullable=False, default='outro')
    unidade = db.Column(db.String(20), nullable=False, default='un')
    tamanho = db.Column(db.String(30), nullable=True)
    estoque_minimo = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    descricao = db.Column(db.Text, nullable=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    movimentacoes = db.relationship(
        'AlmoxarifadoMovimentacao', lazy=True,
        cascade='all, delete-orphan', backref='item',
    )

    def to_dict(self, estoque_atual=None):
        return {
            'id': self.id,
            'codigo': self.codigo,
            'nome': self.nome,
            'categoria': self.categoria,
            'unidade': self.unidade,
            'tamanho': self.tamanho,
            'estoque_minimo': float(self.estoque_minimo or 0),
            'estoque_atual': float(estoque_atual) if estoque_atual is not None else None,
            'descricao': self.descricao,
            'ativo': bool(self.ativo),
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
