from datetime import datetime
from extensions import db


class ServicoUsuario(db.Model):
    """
    Serviços personalizados salvos pelo usuário
    Compartilhados por conta (todos os usuários da mesma empresa)
    """
    __tablename__ = 'servico_usuario'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Criador

    categoria = db.Column(db.String(100), nullable=True)
    descricao = db.Column(db.String(500), nullable=False)
    unidade = db.Column(db.String(20), nullable=False)

    # Tipo de composição
    tipo_composicao = db.Column(db.String(20), default='separado')

    # Se separado
    preco_mao_obra = db.Column(db.Float, nullable=True)
    preco_material = db.Column(db.Float, nullable=True)

    # Se composto
    preco_unitario = db.Column(db.Float, nullable=True)
    rateio_mo = db.Column(db.Float, default=50)
    rateio_mat = db.Column(db.Float, default=50)

    # Estatísticas de uso
    vezes_usado = db.Column(db.Integer, default=0)
    ultima_utilizacao = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'categoria': self.categoria,
            'descricao': self.descricao,
            'unidade': self.unidade,
            'tipo_composicao': self.tipo_composicao,
            'preco_mao_obra': self.preco_mao_obra,
            'preco_material': self.preco_material,
            'preco_unitario': self.preco_unitario,
            'rateio_mo': self.rateio_mo,
            'rateio_mat': self.rateio_mat,
            'vezes_usado': self.vezes_usado,
            'ultima_utilizacao': self.ultima_utilizacao.isoformat() if self.ultima_utilizacao else None,
            'fonte': 'usuario'
        }
