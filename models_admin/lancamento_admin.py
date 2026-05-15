from datetime import datetime, date

from extensions_admin import db


class Lancamento(db.Model):
    """Lançamentos de despesas e receitas por imóvel"""
    __tablename__ = 'admin_lancamento'

    id = db.Column(db.Integer, primary_key=True)
    imovel_id = db.Column(db.Integer, db.ForeignKey('admin_imovel.id'), nullable=False)
    categoria_id = db.Column(db.Integer, db.ForeignKey('admin_categoria.id'), nullable=False)

    # Dados do lançamento
    descricao = db.Column(db.String(300), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # despesa, receita

    # Datas
    data_lancamento = db.Column(db.Date, nullable=False, default=date.today)
    data_vencimento = db.Column(db.Date, nullable=True)
    data_pagamento = db.Column(db.Date, nullable=True)

    # Status
    status = db.Column(db.String(20), default='pendente')  # pendente, pago, cancelado

    # Recorrência (para lançamentos mensais como aluguel, condomínio)
    recorrente = db.Column(db.Boolean, default=False)
    recorrencia_meses = db.Column(db.Integer, default=1)  # A cada X meses

    # Metadados
    observacoes = db.Column(db.Text)
    comprovante_url = db.Column(db.String(500))  # URL do comprovante (se houver)

    # Dados de pagamento
    pix_chave = db.Column(db.String(300))        # Chave PIX (CPF, CNPJ, email, telefone, aleatória)
    codigo_barras = db.Column(db.String(100))    # Código de barras / linha digitável do boleto

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'imovel_id': self.imovel_id,
            'imovel_nome': self.imovel.nome if self.imovel else None,
            'categoria_id': self.categoria_id,
            'categoria_nome': self.categoria.nome if self.categoria else None,
            'categoria_icone': self.categoria.icone if self.categoria else '💰',
            'descricao': self.descricao,
            'valor': self.valor,
            'tipo': self.tipo,
            'data_lancamento': self.data_lancamento.isoformat() if self.data_lancamento else None,
            'data_vencimento': self.data_vencimento.isoformat() if self.data_vencimento else None,
            'data_pagamento': self.data_pagamento.isoformat() if self.data_pagamento else None,
            'status': self.status,
            'recorrente': self.recorrente,
            'recorrencia_meses': self.recorrencia_meses,
            'observacoes': self.observacoes,
            'comprovante_url': self.comprovante_url,
            'pix_chave': self.pix_chave,
            'codigo_barras': self.codigo_barras,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
