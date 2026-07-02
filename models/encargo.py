from datetime import datetime, date

from extensions import db


class Encargo(db.Model):
    """Encargo / guia (FGTS, INSS·DARF, eSocial·DAE, outro).

    `obra_id` nulo = "Geral" (empresa toda). `status` é derivado em to_dict:
    `pago` se data_pagamento preenchida; senão `vencido` se vencimento < hoje;
    senão `a_vencer`.
    """
    __tablename__ = 'encargo'

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), nullable=False)         # fgts | inss_darf | esocial_dae | outro
    competencia = db.Column(db.String(7), nullable=False)   # 'YYYY-MM'
    vencimento = db.Column(db.Date, nullable=True)
    data_pagamento = db.Column(db.Date, nullable=True)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    arquivo_url = db.Column(db.String(500), nullable=True)
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    funcionario_id = db.Column(
        db.Integer, db.ForeignKey('funcionario.id', ondelete='SET NULL'), nullable=True,
    )
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    obra = db.relationship('Obra', lazy=True)
    funcionario = db.relationship('Funcionario', lazy=True)

    def _status(self):
        if self.data_pagamento:
            return 'pago'
        if self.vencimento and self.vencimento < date.today():
            return 'vencido'
        return 'a_vencer'

    def to_dict(self):
        return {
            'id': self.id,
            'tipo': self.tipo,
            'competencia': self.competencia,
            'vencimento': self.vencimento.isoformat() if self.vencimento else None,
            'data_pagamento': self.data_pagamento.isoformat() if self.data_pagamento else None,
            'valor': float(self.valor) if self.valor is not None else None,
            'arquivo_url': self.arquivo_url,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'funcionario_id': self.funcionario_id,
            'funcionario_nome': self.funcionario.nome if self.funcionario else None,
            'observacao': self.observacao,
            'status': self._status(),
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
