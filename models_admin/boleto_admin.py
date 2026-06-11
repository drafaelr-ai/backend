from datetime import datetime, date

from extensions_admin import db


class AdminBoleto(db.Model):
    """Boletos vinculados a imóveis do módulo patrimonial"""
    __tablename__ = 'admin_boleto'

    id = db.Column(db.Integer, primary_key=True)
    imovel_id = db.Column(db.Integer, db.ForeignKey('admin_imovel.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('admin_usuario.id'), nullable=True)

    # Dados do boleto
    codigo_barras = db.Column(db.String(100), nullable=True)
    descricao = db.Column(db.String(255), nullable=False)
    beneficiario = db.Column(db.String(255), nullable=True)
    valor = db.Column(db.Float, nullable=True)
    data_vencimento = db.Column(db.Date, nullable=False)

    # Controle
    status = db.Column(db.String(20), default='Pendente')  # Pendente, Pago, Vencido
    data_pagamento = db.Column(db.Date, nullable=True)
    orcamento_item_id = db.Column(db.Integer, nullable=True)

    # Arquivo PDF (base64)
    arquivo_nome = db.Column(db.String(255), nullable=True)
    arquivo_pdf = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relacionamento — definido aqui (corrige monkey-patch que estava em ln 1482 do monolito)
    imovel = db.relationship('Imovel', backref='boletos', lazy=True)

    def to_dict(self):
        hoje = date.today()
        dias_para_vencer = (self.data_vencimento - hoje).days if self.data_vencimento else 0

        if self.status == 'Pago':
            urgencia = 'pago'
        elif dias_para_vencer < 0:
            urgencia = 'vencido'
        elif dias_para_vencer == 0:
            urgencia = 'hoje'
        elif dias_para_vencer <= 3:
            urgencia = 'urgente'
        elif dias_para_vencer <= 7:
            urgencia = 'atencao'
        else:
            urgencia = 'normal'

        return {
            'id': self.id,
            'imovel_id': self.imovel_id,
            'imovel_nome': self.imovel.nome if self.imovel else None,
            'usuario_id': self.usuario_id,
            'codigo_barras': self.codigo_barras,
            'descricao': self.descricao,
            'beneficiario': self.beneficiario,
            'valor': self.valor,
            'data_vencimento': self.data_vencimento.isoformat() if self.data_vencimento else None,
            'status': self.status,
            'data_pagamento': self.data_pagamento.isoformat() if self.data_pagamento else None,
            'arquivo_nome': self.arquivo_nome,
            'tem_pdf': bool(self.arquivo_pdf),
            'dias_para_vencer': dias_para_vencer,
            'urgencia': urgencia,
            'orcamento_item_id': self.orcamento_item_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
