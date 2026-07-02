from datetime import datetime

from extensions import db


class ConvencaoColetiva(db.Model):
    """Convenção coletiva de trabalho (CCT) por estado (UF)."""
    __tablename__ = 'convencao_coletiva'

    id = db.Column(db.Integer, primary_key=True)
    uf = db.Column(db.String(2), nullable=False)              # 'CE', 'SP'...
    sindicato = db.Column(db.String(160), nullable=True)
    vigencia_inicio = db.Column(db.Date, nullable=False)
    vigencia_fim = db.Column(db.Date, nullable=False)
    arquivo_url = db.Column(db.String(500), nullable=True)    # Supabase Storage (path)
    status = db.Column(db.String(20), nullable=False, default='rascunho')  # rascunho | confirmada
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

    valores = db.relationship(
        'ConvencaoValor', backref='convencao', lazy=True,
        cascade='all, delete-orphan',
    )

    def to_dict(self):
        return {
            'id': self.id,
            'uf': self.uf,
            'sindicato': self.sindicato,
            'vigencia_inicio': self.vigencia_inicio.isoformat() if self.vigencia_inicio else None,
            'vigencia_fim': self.vigencia_fim.isoformat() if self.vigencia_fim else None,
            'arquivo_url': self.arquivo_url,
            'status': self.status,
            'data_upload': self.data_upload.isoformat() if self.data_upload else None,
        }
