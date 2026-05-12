from extensions import db


class Servico(db.Model):
    __tablename__ = 'servico'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    responsavel = db.Column(db.String(150))
    valor_global_mao_de_obra = db.Column(db.Float, nullable=False, default=0.0)
    valor_global_material = db.Column(db.Float, nullable=False, default=0.0)
    pix = db.Column(db.String(100))
    concluido = db.Column(db.Boolean, default=False)  # NOVO: Marcar serviço como concluído
    data_conclusao = db.Column(db.Date, nullable=True)  # NOVO: Data da conclusão
    pagamentos = db.relationship('PagamentoServico', backref='servico', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id, "obra_id": self.obra_id, "nome": self.nome,
            "responsavel": self.responsavel,
            "valor_global_mao_de_obra": self.valor_global_mao_de_obra,
            "valor_global_material": self.valor_global_material,
            "pix": self.pix,
            "concluido": self.concluido or False,
            "data_conclusao": self.data_conclusao.isoformat() if self.data_conclusao else None,
            "pagamentos": [p.to_dict() for p in self.pagamentos]
        }
