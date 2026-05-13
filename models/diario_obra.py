from sqlalchemy.sql import func
from extensions import db


class DiarioObra(db.Model):
    __tablename__ = 'diario_obra'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text, nullable=True)
    clima = db.Column(db.String(50), nullable=True)
    temperatura = db.Column(db.String(50), nullable=True)
    equipe_presente = db.Column(db.Text, nullable=True)
    atividades_realizadas = db.Column(db.Text, nullable=True)
    materiais_utilizados = db.Column(db.Text, nullable=True)
    equipamentos_utilizados = db.Column(db.Text, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    criado_por = db.Column(db.Integer, nullable=True)
    criado_em = db.Column(db.DateTime, default=func.now())
    atualizado_em = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    # Relacionamentos
    imagens = db.relationship('DiarioImagem', backref='entrada', lazy=True, cascade='all, delete-orphan')
    # criador = db.relationship('User', backref='entradas_diario', foreign_keys=[criado_por])

    def to_dict(self, include_images_base64=False):
        """Retorna dict. Por padrao NAO inclui base64 das imagens"""
        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'data': self.data.isoformat() if self.data else None,
            'titulo': self.titulo,
            'descricao': self.descricao,
            'clima': self.clima,
            'temperatura': self.temperatura,
            'equipe_presente': self.equipe_presente,
            'atividades_realizadas': self.atividades_realizadas,
            'materiais_utilizados': self.materiais_utilizados,
            'equipamentos_utilizados': self.equipamentos_utilizados,
            'observacoes': self.observacoes,
            'criado_por': self.criado_por,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'atualizado_em': self.atualizado_em.isoformat() if self.atualizado_em else None,
            'fotos': [img.to_dict(include_base64=include_images_base64) for img in self.imagens],
            'imagens': [img.to_dict(include_base64=include_images_base64) for img in self.imagens]
        }
