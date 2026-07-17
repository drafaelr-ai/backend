from datetime import datetime

from extensions import db


class SolicitacaoConfig(db.Model):
    """Configuração única (linha id=1) do módulo Solicitações.

    - alertados_ids: usuários notificados quando uma solicitação é criada
      (quem faz a pesquisa de preços). None/[] = ninguém alertado.
    - aprovadores_ids: usuários aptos a aprovar compras (master sempre pode).
    - limite_valor: compras com cotação escolhida acima do limite exigem
      aprovador; abaixo, quem cotou pode efetivar direto. None = toda compra
      exige aprovação.
    """
    __tablename__ = 'solicitacao_config'

    id = db.Column(db.Integer, primary_key=True)
    alertados_ids = db.Column(db.JSON, nullable=True)
    aprovadores_ids = db.Column(db.JSON, nullable=True)
    limite_valor = db.Column(db.Float, nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls):
        return cls.query.get(1)

    def to_dict(self):
        return {
            'alertados_ids': self.alertados_ids or [],
            'aprovadores_ids': self.aprovadores_ids or [],
            'limite_valor': self.limite_valor,
            'atualizado_em': self.atualizado_em.isoformat() if self.atualizado_em else None,
        }
