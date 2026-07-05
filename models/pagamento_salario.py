from datetime import datetime

from extensions import db


class PagamentoSalario(db.Model):
    """Pagamento de salário / vale / outro a um funcionário.

    `obra_id` é SNAPSHOT: copiado do funcionário no momento do POST. Se o
    funcionário migrar de obra depois, o custo histórico de cada obra continua
    correto.
    """
    __tablename__ = 'pagamento_salario'

    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(
        db.Integer, db.ForeignKey('funcionario.id'), nullable=False,
    )
    competencia = db.Column(db.String(7), nullable=False)    # 'YYYY-MM'
    tipo = db.Column(db.String(20), nullable=False)          # salario | vale | outro
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    data_pagamento = db.Column(db.Date, nullable=False)
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    comprovante_url = db.Column(db.String(500), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', lazy=True)
    obra = db.relationship('Obra', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'funcionario_id': self.funcionario_id,
            'funcionario_nome': self.funcionario.nome if self.funcionario else None,
            'competencia': self.competencia,
            'tipo': self.tipo,
            'valor': float(self.valor) if self.valor is not None else None,
            'data_pagamento': self.data_pagamento.isoformat() if self.data_pagamento else None,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'comprovante_url': self.comprovante_url,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
