from datetime import datetime

from extensions_admin import db


class SuperlinkAdmin(db.Model):
    __tablename__ = 'superlink'

    id          = db.Column(db.Integer, primary_key=True)
    token       = db.Column(db.String(64), nullable=False, unique=True, index=True)
    grupo_id    = db.Column(db.Integer, nullable=True)
    titulo      = db.Column(db.String(255), nullable=False)
    itens       = db.Column(db.JSON, nullable=False)
    refs        = db.Column(db.JSON, nullable=True)
    valor_total = db.Column(db.Float, nullable=False, default=0)
    criado_em   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expira_em   = db.Column(db.DateTime, nullable=False)

    def is_expirado(self):
        return datetime.utcnow() > self.expira_em

    def to_dict_publico(self):
        return {
            'titulo':      self.titulo,
            'itens':       self.itens,
            'valor_total': self.valor_total,
            'expira_em':   self.expira_em.isoformat() + 'Z',
        }

    def to_dict(self):
        d = self.to_dict_publico()
        d.update({
            'id':        self.id,
            'token':     self.token,
            'grupo_id':  self.grupo_id,
            'criado_em': self.criado_em.isoformat() + 'Z',
        })
        return d
