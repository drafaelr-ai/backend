from datetime import datetime

from extensions import db


class FrotaAbastecimentoSolicitacao(db.Model):
    """Autorização de abastecimento enviada ao motorista por link público.

    Fluxo (molde SICOP): o motorista pede pra abastecer → a compradora gera o
    link e compartilha (WhatsApp) → o motorista abre sem login, informa KM e
    litros e anexa o comprovante → o serviço de OCR lê o cupom e preenche
    preço/litro e valor total → ao enviar, nasce o FrotaAbastecimento de fato.

    `token` é o segredo do link (não indexável) e `expira_em` limita a janela.
    `abastecimento_id` é referência fraca ao registro criado na conclusão.
    """
    __tablename__ = 'frota_abastecimento_solicitacao'

    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(
        db.Integer, db.ForeignKey('frota_veiculo.id', ondelete='CASCADE'), nullable=False,
    )
    condutor_id = db.Column(
        db.Integer, db.ForeignKey('frota_condutor.id', ondelete='SET NULL'), nullable=True,
    )
    token = db.Column(db.String(64), nullable=False, unique=True)
    status = db.Column(db.String(20), nullable=False, default='pendente')
    # pendente | concluida | cancelada

    combustivel = db.Column(db.String(20), nullable=True)   # esperado, do veículo
    limite_valor = db.Column(db.Numeric(12, 2), nullable=True)  # teto autorizado
    observacao = db.Column(db.String(300), nullable=True)    # instrução da compradora
    criado_por_id = db.Column(db.Integer, nullable=True)     # referência fraca ao user
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    expira_em = db.Column(db.DateTime, nullable=False)

    # --- preenchido pelo motorista na página pública
    km = db.Column(db.Integer, nullable=True)
    litros = db.Column(db.Numeric(10, 2), nullable=True)
    preco_litro = db.Column(db.Numeric(10, 3), nullable=True)
    valor_total = db.Column(db.Numeric(12, 2), nullable=True)
    posto = db.Column(db.String(160), nullable=True)
    data_abastecimento = db.Column(db.Date, nullable=True)
    comprovante_url = db.Column(db.String(500), nullable=True)
    observacao_motorista = db.Column(db.String(300), nullable=True)
    enviado_em = db.Column(db.DateTime, nullable=True)

    # --- OCR do comprovante
    ocr_status = db.Column(db.String(20), nullable=True)  # ok | falhou | nao_processado
    ocr_dados = db.Column(db.JSON, nullable=True)
    ocr_tentativas = db.Column(db.Integer, nullable=False, default=0)

    abastecimento_id = db.Column(db.Integer, nullable=True)  # referência fraca

    veiculo = db.relationship('FrotaVeiculo', lazy=True)
    condutor = db.relationship('FrotaCondutor', lazy=True)

    def is_expirado(self):
        return datetime.utcnow() > self.expira_em

    def situacao(self):
        """Status efetivo — 'expirada' é derivado, nunca gravado."""
        if self.status == 'pendente' and self.is_expirado():
            return 'expirada'
        return self.status

    def to_dict(self):
        return {
            'id': self.id,
            'veiculo_id': self.veiculo_id,
            'veiculo_placa': self.veiculo.placa if self.veiculo else None,
            'veiculo_modelo': self.veiculo.modelo if self.veiculo else None,
            'condutor_id': self.condutor_id,
            'condutor_nome': self.condutor.nome if self.condutor else None,
            'token': self.token,
            'status': self.situacao(),
            'combustivel': self.combustivel,
            'limite_valor': float(self.limite_valor) if self.limite_valor is not None else None,
            'observacao': self.observacao,
            'criado_em': self.criado_em.isoformat() if self.criado_em else None,
            'expira_em': self.expira_em.isoformat() if self.expira_em else None,
            'km': self.km,
            'litros': float(self.litros) if self.litros is not None else None,
            'preco_litro': float(self.preco_litro) if self.preco_litro is not None else None,
            'valor_total': float(self.valor_total) if self.valor_total is not None else None,
            'posto': self.posto,
            'data_abastecimento': (self.data_abastecimento.isoformat()
                                   if self.data_abastecimento else None),
            'observacao_motorista': self.observacao_motorista,
            'tem_comprovante': bool(self.comprovante_url),
            'enviado_em': self.enviado_em.isoformat() if self.enviado_em else None,
            'ocr_status': self.ocr_status,
            'abastecimento_id': self.abastecimento_id,
        }

    def to_dict_publico(self):
        """Snapshot para a página do motorista — sem ids internos nem custos
        de outras solicitações. O KM anterior orienta o preenchimento."""
        return {
            'status': self.situacao(),
            'veiculo_placa': self.veiculo.placa if self.veiculo else None,
            'veiculo_modelo': self.veiculo.modelo if self.veiculo else None,
            'veiculo_marca': self.veiculo.marca if self.veiculo else None,
            'condutor_nome': self.condutor.nome if self.condutor else None,
            'combustivel': self.combustivel,
            'limite_valor': float(self.limite_valor) if self.limite_valor is not None else None,
            'observacao': self.observacao,
            'km_anterior': self.veiculo.km_atual if self.veiculo else None,
            'expira_em': self.expira_em.isoformat() if self.expira_em else None,
            'km': self.km,
            'litros': float(self.litros) if self.litros is not None else None,
            'preco_litro': float(self.preco_litro) if self.preco_litro is not None else None,
            'valor_total': float(self.valor_total) if self.valor_total is not None else None,
            'posto': self.posto,
            'data_abastecimento': (self.data_abastecimento.isoformat()
                                   if self.data_abastecimento else None),
        }
