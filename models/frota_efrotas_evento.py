from datetime import datetime

from extensions import db


class FrotaEFrotasEvento(db.Model):
    """Auditoria e deduplicação dos callbacks enviados pelo e-Frotas."""
    __tablename__ = 'frota_efrotas_evento'

    id = db.Column(db.Integer, primary_key=True)
    id_rastreamento = db.Column(db.String(100), nullable=True, unique=True)
    tipo_evento = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='recebido')
    # recebido | processado | ignorado | erro
    erro = db.Column(db.String(500), nullable=True)
    payload = db.Column(db.JSON, nullable=False)
    multa_id = db.Column(db.Integer, db.ForeignKey('frota_multa.id', ondelete='SET NULL'), nullable=True)
    recebido_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    processado_em = db.Column(db.DateTime, nullable=True)

    multa = db.relationship('FrotaMulta', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'id_rastreamento': self.id_rastreamento,
            'tipo_evento': self.tipo_evento,
            'status': self.status,
            'erro': self.erro,
            'multa_id': self.multa_id,
            'placa': self.multa.veiculo.placa if self.multa and self.multa.veiculo else None,
            'recebido_em': self.recebido_em.isoformat() if self.recebido_em else None,
            'processado_em': self.processado_em.isoformat() if self.processado_em else None,
        }
