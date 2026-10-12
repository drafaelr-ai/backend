from datetime import datetime

from extensions import db


class FrotaPostoCombustivel(db.Model):
    """Posto reutilizável nos lançamentos de abastecimento da frota."""

    __tablename__ = 'frota_posto_combustivel'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    nome_normalizado = db.Column(db.String(160), nullable=False, unique=True, index=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    data_criacao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    data_atualizacao = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'ativo': bool(self.ativo),
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
            'data_atualizacao': (
                self.data_atualizacao.isoformat() if self.data_atualizacao else None
            ),
        }

    def to_dict_publico(self):
        return {'id': self.id, 'nome': self.nome}
