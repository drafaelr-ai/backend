from datetime import datetime

from extensions import db


class SolicitacaoEntrega(db.Model):
    """Superlink de entrega da solicitação — compartilhado com o motorista
    via WhatsApp, sem login.

    Um link por solicitação (UNIQUE em solicitacao_id): gerar de novo troca o
    token e invalida o anterior. O motorista vê itens/fornecedor/obra (nunca
    valores), confirma a entrega com observação e pode mandar mensagem pro
    comprador (vira comentário na conversa da solicitação).
    """
    __tablename__ = 'solicitacao_entrega'

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer, db.ForeignKey('solicitacao_compra.id', ondelete='CASCADE'),
        nullable=False, unique=True,
    )
    token = db.Column(db.String(64), nullable=False, unique=True)
    criado_por_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    entregue_em = db.Column(db.DateTime, nullable=True)
    observacao_entrega = db.Column(db.String(500), nullable=True)

    solicitacao = db.relationship(
        'SolicitacaoCompra', lazy=True,
        backref=db.backref('entrega', uselist=False, lazy=True),
    )

    def to_dict(self):
        return {
            'token': self.token,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'entregue_em': self.entregue_em.isoformat() if self.entregue_em else None,
            'observacao_entrega': self.observacao_entrega,
        }
