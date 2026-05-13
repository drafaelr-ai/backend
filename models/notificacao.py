from extensions import db
from datetime import datetime


class Notificacao(db.Model):
    __tablename__ = 'notificacao'
    id = db.Column(db.Integer, primary_key=True)
    usuario_destino_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    usuario_origem_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    tipo = db.Column(db.String(50), nullable=False)  # 'servico_criado', 'pagamento_inserido', 'orcamento_aprovado'
    titulo = db.Column(db.String(255), nullable=False)
    mensagem = db.Column(db.Text, nullable=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=True)
    item_id = db.Column(db.Integer, nullable=True)
    item_type = db.Column(db.String(50), nullable=True)  # 'servico', 'lancamento', 'orcamento'
    lida = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamentos
    usuario_destino = db.relationship('User', foreign_keys=[usuario_destino_id], backref='notificacoes_recebidas')
    usuario_origem = db.relationship('User', foreign_keys=[usuario_origem_id], backref='notificacoes_enviadas')
    obra = db.relationship('Obra', backref='notificacoes')

    def to_dict(self):
        return {
            "id": self.id,
            "usuario_destino_id": self.usuario_destino_id,
            "usuario_origem_id": self.usuario_origem_id,
            "usuario_origem_nome": self.usuario_origem.username if self.usuario_origem else None,
            "tipo": self.tipo,
            "titulo": self.titulo,
            "mensagem": self.mensagem,
            "obra_id": self.obra_id,
            "obra_nome": self.obra.nome if self.obra else None,
            "item_id": self.item_id,
            "item_type": self.item_type,
            "lida": self.lida,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
