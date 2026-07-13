from datetime import datetime

from extensions import db


class FrotaManutencao(db.Model):
    """Manutenção de veículo (preventiva/corretiva).

    Local (`local_tipo`/`obra_id`/`imovel_id`/`local_nome`) é SNAPSHOT copiado
    do veículo no momento do POST — o custo histórico do local permanece correto
    mesmo se o veículo mudar de obra depois (padrão `pagamento_salario.obra_id`).
    """
    __tablename__ = 'frota_manutencao'

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(
        db.Integer, db.ForeignKey('frota_veiculo.id', ondelete='CASCADE'), nullable=False,
    )
    tipo = db.Column(db.String(20), nullable=False)  # preventiva | corretiva
    descricao = db.Column(db.String(300), nullable=True)
    data = db.Column(db.Date, nullable=False)
    km = db.Column(db.Integer, nullable=True)
    custo = db.Column(db.Numeric(12, 2), nullable=False)
    oficina = db.Column(db.String(160), nullable=True)
    arquivo_url = db.Column(db.String(500), nullable=True)
    local_tipo = db.Column(db.String(10), nullable=True)  # snapshot: obra | imovel | NULL
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    imovel_id = db.Column(db.Integer, nullable=True)
    local_nome = db.Column(db.String(160), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    veiculo = db.relationship('FrotaVeiculo', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'veiculo_id': self.veiculo_id,
            'veiculo_placa': self.veiculo.placa if self.veiculo else None,
            'veiculo_modelo': self.veiculo.modelo if self.veiculo else None,
            'tipo': self.tipo,
            'descricao': self.descricao,
            'data': self.data.isoformat() if self.data else None,
            'km': self.km,
            'custo': float(self.custo) if self.custo is not None else None,
            'oficina': self.oficina,
            'arquivo_url': self.arquivo_url,
            'local_tipo': self.local_tipo,
            'obra_id': self.obra_id,
            'imovel_id': self.imovel_id,
            'local_nome': self.local_nome,
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
