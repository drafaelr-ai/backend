from datetime import datetime

from extensions import db


class DispositivoPush(db.Model):
    """Aparelho registrado para receber notificação push (FCM).

    Um usuário pode ter vários aparelhos; um token pertence a um aparelho só.
    O token é a chave natural — o FCM pode reemitir o mesmo token para outro
    login no mesmo aparelho, então o vínculo é reatribuído em vez de duplicado.
    """
    __tablename__ = 'dispositivo_push'

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    token = db.Column(db.String(255), nullable=False, unique=True)
    plataforma = db.Column(db.String(20), nullable=True)   # android | ios | web
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    visto_em = db.Column(db.DateTime, default=datetime.utcnow)

    usuario = db.relationship('User', backref='dispositivos_push')

    def to_dict(self):
        return {
            'id': self.id,
            'usuario_id': self.usuario_id,
            'plataforma': self.plataforma,
            # O token nunca sai numa resposta: quem o tem pode receber as
            # notificações daquele aparelho.
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'visto_em': self.visto_em.isoformat() if self.visto_em else None,
        }
