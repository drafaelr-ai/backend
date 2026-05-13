from datetime import datetime
from extensions import db


class OrcamentoEngEtapa(db.Model):
    """
    Etapas do orçamento de engenharia (ex: Fundação, Estrutura, Alvenaria)
    """
    __tablename__ = 'orcamento_eng_etapa'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)

    codigo = db.Column(db.String(20))  # 01, 02, 03...
    nome = db.Column(db.String(200), nullable=False)  # FUNDAÇÃO, ESTRUTURA...
    ordem = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamento com itens
    itens = db.relationship('OrcamentoEngItem', backref='etapa', lazy=True, cascade="all, delete-orphan")

    def to_dict(self, include_itens=True):
        result = {
            'id': self.id,
            'obra_id': self.obra_id,
            'codigo': self.codigo,
            'nome': self.nome,
            'ordem': self.ordem
        }
        if include_itens:
            result['itens'] = [item.to_dict() for item in self.itens]
        return result
