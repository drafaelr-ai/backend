from datetime import date, datetime

from extensions import db


class FrotaCondutor(db.Model):
    """Condutor/motorista da frota, opcionalmente vinculado a um funcionário do RH."""
    __tablename__ = 'frota_condutor'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    cpf = db.Column(db.String(14), nullable=True, index=True)
    telefone = db.Column(db.String(20), nullable=True)
    cnh_numero = db.Column(db.String(20), nullable=True)
    cnh_categoria = db.Column(db.String(5), nullable=True)   # A | B | AB | C | D | E
    cnh_validade = db.Column(db.Date, nullable=True)
    funcionario_id = db.Column(
        db.Integer, db.ForeignKey('funcionario.id', ondelete='SET NULL'), nullable=True,
    )
    status = db.Column(db.String(20), nullable=False, default='ativo')  # ativo | inativo
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', lazy=True)

    def _cnh_status(self):
        if not self.cnh_validade:
            return None
        dias = (self.cnh_validade - date.today()).days
        if dias < 0:
            return 'vencida'
        if dias <= 30:
            return 'a_vencer'
        return 'ok'

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'cpf': self.cpf,
            'telefone': self.telefone,
            'cnh_numero': self.cnh_numero,
            'cnh_categoria': self.cnh_categoria,
            'cnh_validade': self.cnh_validade.isoformat() if self.cnh_validade else None,
            'cnh_status': self._cnh_status(),
            'funcionario_id': self.funcionario_id,
            'funcionario_nome': self.funcionario.nome if self.funcionario else None,
            'status': self.status,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
