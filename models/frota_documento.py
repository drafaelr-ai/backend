from datetime import date, datetime

from extensions import db


class FrotaDocumento(db.Model):
    """Documento de veículo (CRLV, seguro, IPVA, licenciamento) com vencimento.

    O status (vencido / a_vencer / ok) é derivado em runtime no `to_dict()`,
    nunca persistido — evita job de atualização.
    """
    __tablename__ = 'frota_documento'

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(
        db.Integer, db.ForeignKey('frota_veiculo.id', ondelete='CASCADE'), nullable=False,
    )
    tipo = db.Column(db.String(20), nullable=False)  # crlv | seguro | ipva | licenciamento | outro
    descricao = db.Column(db.String(160), nullable=True)
    data_vencimento = db.Column(db.Date, nullable=True)
    valor = db.Column(db.Numeric(12, 2), nullable=True)
    arquivo_url = db.Column(db.String(500), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    veiculo = db.relationship('FrotaVeiculo', lazy=True)

    def _status(self):
        if not self.data_vencimento:
            return None
        dias = (self.data_vencimento - date.today()).days
        if dias < 0:
            return 'vencido'
        if dias <= 30:
            return 'a_vencer'
        return 'ok'

    def to_dict(self):
        return {
            'id': self.id,
            'veiculo_id': self.veiculo_id,
            'veiculo_placa': self.veiculo.placa if self.veiculo else None,
            'tipo': self.tipo,
            'descricao': self.descricao,
            'data_vencimento': self.data_vencimento.isoformat() if self.data_vencimento else None,
            'status': self._status(),
            'valor': float(self.valor) if self.valor is not None else None,
            'arquivo_url': self.arquivo_url,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
