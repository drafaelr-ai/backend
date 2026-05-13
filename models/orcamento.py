from extensions import db


class Orcamento(db.Model):
    __tablename__ = 'orcamento'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)

    descricao = db.Column(db.String(255), nullable=False)
    fornecedor = db.Column(db.String(150), nullable=True)
    valor = db.Column(db.Float, nullable=False)
    dados_pagamento = db.Column(db.String(150), nullable=True)
    tipo = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pendente')

    # NOVOS CAMPOS - Condições de Pagamento
    data_vencimento = db.Column(db.Date, nullable=True)
    numero_parcelas = db.Column(db.Integer, nullable=True, default=1)
    periodicidade = db.Column(db.String(20), nullable=True, default='Mensal')  # Semanal, Quinzenal, Mensal

    observacoes = db.Column(db.Text, nullable=True)

    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='orcamentos_vinculados', lazy=True)

    anexos = db.relationship('AnexoOrcamento', backref='orcamento', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "fornecedor": self.fornecedor,
            "valor": self.valor,
            "dados_pagamento": self.dados_pagamento,
            "tipo": self.tipo,
            "status": self.status,
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "numero_parcelas": self.numero_parcelas or 1,
            "periodicidade": self.periodicidade or 'Mensal',
            "observacoes": self.observacoes,
            "servico_id": self.servico_id,
            "servico_nome": self.servico.nome if self.servico else None,
            "anexos_count": len(self.anexos)
        }
