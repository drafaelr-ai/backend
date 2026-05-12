from extensions import db
from datetime import datetime


class ServicoBase(db.Model):
    """
    Base de serviços de referência (estilo SINAPI/TCPO)
    Tabela readonly - populada com seed inicial
    """
    __tablename__ = 'servico_base'

    id = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(100), nullable=False)  # preliminares, fundacao, estrutura, etc
    codigo_ref = db.Column(db.String(50), nullable=True)  # Código SINAPI/TCPO se aplicável
    descricao = db.Column(db.String(500), nullable=False)
    unidade = db.Column(db.String(20), nullable=False)  # m², m³, m, kg, un, pt, vb

    # Tipo de composição
    tipo_composicao = db.Column(db.String(20), default='separado')  # separado | composto

    # Se separado
    preco_mao_obra = db.Column(db.Float, nullable=True)
    preco_material = db.Column(db.Float, nullable=True)

    # Se composto
    preco_unitario = db.Column(db.Float, nullable=True)
    rateio_mo = db.Column(db.Float, default=50)  # % estimado para MO
    rateio_mat = db.Column(db.Float, default=50)  # % estimado para Material

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'categoria': self.categoria,
            'codigo_ref': self.codigo_ref,
            'descricao': self.descricao,
            'unidade': self.unidade,
            'tipo_composicao': self.tipo_composicao,
            'preco_mao_obra': self.preco_mao_obra,
            'preco_material': self.preco_material,
            'preco_unitario': self.preco_unitario,
            'rateio_mo': self.rateio_mo,
            'rateio_mat': self.rateio_mat,
            'fonte': 'base'
        }
