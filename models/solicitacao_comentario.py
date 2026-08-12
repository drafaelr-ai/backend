from datetime import datetime

from extensions import db


class SolicitacaoComentario(db.Model):
    """Comentário na solicitação de compra — conversa entre solicitante e
    comprador (dúvidas, esclarecimentos) com menções @usuario.

    `mencionados_ids` guarda os ids citados no texto — cada um recebe uma
    notificação 'solicitacao_mencao' no sino. A exclusão da solicitação
    remove os comentários via CASCADE no banco (padrão do módulo).
    """
    __tablename__ = 'solicitacao_comentario'

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer, db.ForeignKey('solicitacao_compra.id', ondelete='CASCADE'), nullable=False,
    )
    autor_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    texto = db.Column(db.String(1000), nullable=False)
    mencionados_ids = db.Column(db.JSON, nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    autor = db.relationship('User', foreign_keys=[autor_id], lazy=True)
    solicitacao = db.relationship(
        'SolicitacaoCompra', lazy=True,
        backref=db.backref('comentarios', lazy=True, order_by='SolicitacaoComentario.id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'solicitacao_id': self.solicitacao_id,
            'autor_id': self.autor_id,
            'autor_nome': self.autor.username if self.autor else 'usuário removido',
            'texto': self.texto,
            'mencionados_ids': self.mencionados_ids or [],
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
