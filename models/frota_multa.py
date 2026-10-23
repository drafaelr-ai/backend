from datetime import datetime

from extensions import db


class FrotaMulta(db.Model):
    """Multa de trânsito de um veículo, com condutor responsável opcional."""
    __tablename__ = 'frota_multa'

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(
        db.Integer, db.ForeignKey('frota_veiculo.id', ondelete='CASCADE'), nullable=False,
    )
    data_infracao = db.Column(db.Date, nullable=False)
    descricao = db.Column(db.String(300), nullable=True)
    valor = db.Column(db.Numeric(12, 2), nullable=False)
    pontos = db.Column(db.Integer, nullable=True)
    condutor_id = db.Column(
        db.Integer, db.ForeignKey('frota_condutor.id', ondelete='SET NULL'), nullable=True,
    )
    status_pagamento = db.Column(db.String(20), nullable=False, default='pendente')
    # pendente | paga | contestada
    data_pagamento = db.Column(db.Date, nullable=True)
    arquivo_url = db.Column(db.String(500), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    origem = db.Column(db.String(30), nullable=False, default='manual')
    codigo_orgao_autuador = db.Column(db.String(30), nullable=True)
    numero_auto_infracao = db.Column(db.String(80), nullable=True)
    codigo_infracao = db.Column(db.String(30), nullable=True)
    gravidade = db.Column(db.String(30), nullable=True)
    local_autuacao = db.Column(db.String(300), nullable=True)
    municipio_autuacao = db.Column(db.String(120), nullable=True)
    uf_autuacao = db.Column(db.String(2), nullable=True)
    data_notificacao_autuacao = db.Column(db.Date, nullable=True)
    data_notificacao_penalidade = db.Column(db.Date, nullable=True)
    data_limite_defesa = db.Column(db.Date, nullable=True)
    data_vencimento_penalidade = db.Column(db.Date, nullable=True)
    situacao_efrotas = db.Column(db.String(20), nullable=True)
    # ativa | suspensa | cancelada — separada do pagamento local
    fase_efrotas = db.Column(db.String(20), nullable=True)
    # autuacao | penalidade
    motivo_situacao_efrotas = db.Column(db.String(300), nullable=True)
    atualizado_efrotas_em = db.Column(db.DateTime, nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    veiculo = db.relationship('FrotaVeiculo', lazy=True)
    condutor = db.relationship('FrotaCondutor', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'veiculo_id': self.veiculo_id,
            'veiculo_placa': self.veiculo.placa if self.veiculo else None,
            'veiculo_modelo': self.veiculo.modelo if self.veiculo else None,
            'data_infracao': self.data_infracao.isoformat() if self.data_infracao else None,
            'descricao': self.descricao,
            'valor': float(self.valor) if self.valor is not None else None,
            'pontos': self.pontos,
            'condutor_id': self.condutor_id,
            'condutor_nome': self.condutor.nome if self.condutor else None,
            'status_pagamento': self.status_pagamento,
            'data_pagamento': self.data_pagamento.isoformat() if self.data_pagamento else None,
            'arquivo_url': self.arquivo_url,
            'observacao': self.observacao,
            'origem': self.origem,
            'codigo_orgao_autuador': self.codigo_orgao_autuador,
            'numero_auto_infracao': self.numero_auto_infracao,
            'codigo_infracao': self.codigo_infracao,
            'gravidade': self.gravidade,
            'local_autuacao': self.local_autuacao,
            'municipio_autuacao': self.municipio_autuacao,
            'uf_autuacao': self.uf_autuacao,
            'data_notificacao_autuacao': self.data_notificacao_autuacao.isoformat() if self.data_notificacao_autuacao else None,
            'data_notificacao_penalidade': self.data_notificacao_penalidade.isoformat() if self.data_notificacao_penalidade else None,
            'data_limite_defesa': self.data_limite_defesa.isoformat() if self.data_limite_defesa else None,
            'data_vencimento_penalidade': self.data_vencimento_penalidade.isoformat() if self.data_vencimento_penalidade else None,
            'situacao_efrotas': self.situacao_efrotas,
            'fase_efrotas': self.fase_efrotas,
            'motivo_situacao_efrotas': self.motivo_situacao_efrotas,
            'atualizado_efrotas_em': self.atualizado_efrotas_em.isoformat() if self.atualizado_efrotas_em else None,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
