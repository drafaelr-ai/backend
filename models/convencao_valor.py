from extensions import db
from sqlalchemy.dialects.postgresql import JSONB


class ConvencaoValor(db.Model):
    """Piso salarial + benefícios por (convenção × categoria).

    É o que faz o funcionário herdar o piso novo quando a CCT do ano seguinte
    é confirmada.

    Formato de `beneficios` (JSONB): lista de
        {"tipo": "vale_refeicao", "descricao": "Vale-refeição",
         "valor": 22.0, "unidade": "dia"}
    onde unidade ∈ {mes, dia, unico}.
    """
    __tablename__ = 'convencao_valor'

    id = db.Column(db.Integer, primary_key=True)
    convencao_id = db.Column(
        db.Integer,
        db.ForeignKey('convencao_coletiva.id', ondelete='CASCADE'),
        nullable=False,
    )
    categoria_id = db.Column(
        db.Integer, db.ForeignKey('categoria_mo.id'), nullable=False,
    )
    piso_salarial = db.Column(db.Numeric(12, 2), nullable=False)
    beneficios = db.Column(JSONB, default=list)

    categoria = db.relationship('CategoriaMO', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'convencao_id': self.convencao_id,
            'categoria_id': self.categoria_id,
            'categoria_nome': self.categoria.nome if self.categoria else None,
            'piso_salarial': float(self.piso_salarial) if self.piso_salarial is not None else None,
            'beneficios': self.beneficios or [],
        }
