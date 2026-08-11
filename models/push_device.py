from datetime import datetime

from extensions import db


class PushDevice(db.Model):
    __tablename__ = 'push_device'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    token = db.Column(db.Text, nullable=False, unique=True)
    plataforma = db.Column(db.String(20), nullable=False, default='android')
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    atualizado_em = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    ultimo_acesso_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship(
        'User',
        backref=db.backref('push_devices', lazy=True, cascade='all, delete-orphan'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'plataforma': self.plataforma,
            'ativo': self.ativo,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'ultimo_acesso_em': (
                self.ultimo_acesso_em.isoformat() if self.ultimo_acesso_em else None
            ),
        }
