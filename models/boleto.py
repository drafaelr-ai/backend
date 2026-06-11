import logging
from datetime import date, datetime
from sqlalchemy.orm import deferred
from extensions import db

logger = logging.getLogger(__name__)


class Boleto(db.Model):
    """Modelo para gestão de boletos com upload de PDF e extração automática"""
    __tablename__ = 'boleto'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    # Dados do boleto
    codigo_barras = db.Column(db.String(60), nullable=True)
    descricao = db.Column(db.String(255), nullable=True)
    beneficiario = db.Column(db.String(255), nullable=True)
    valor = db.Column(db.Float, nullable=True)
    data_vencimento = db.Column(db.Date, nullable=False)

    # Controle
    status = db.Column(db.String(20), default='Pendente')  # Pendente, Pago, Vencido
    data_pagamento = db.Column(db.Date, nullable=True)
    vinculado_servico_id = db.Column(db.Integer, nullable=True)
    orcamento_item_id = db.Column(db.Integer, nullable=True)

    # Arquivo PDF
    arquivo_nome = db.Column(db.String(255), nullable=True)
    arquivo_pdf = deferred(db.Column(db.Text, nullable=True))  # Base64 do PDF

    # Alertas enviados
    alerta_7dias = db.Column(db.Boolean, default=False)
    alerta_3dias = db.Column(db.Boolean, default=False)
    alerta_hoje = db.Column(db.Boolean, default=False)
    alerta_vencido = db.Column(db.Boolean, default=False)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relacionamentos
    usuario = db.relationship('User', backref='boletos_cadastrados')
    # Nota: vinculado_servico_id não tem ForeignKey, busca manual no to_dict()

    def to_dict(self):
        from models.servico import Servico
        from models.orcamento_eng_item import OrcamentoEngItem

        # Calcular dias para vencimento
        hoje = date.today()
        dias_para_vencer = (self.data_vencimento - hoje).days if self.data_vencimento else 0

        # Determinar urgência
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

        # Buscar nome do serviço vinculado
        servico_nome = None
        if self.vinculado_servico_id:
            try:
                servico = db.session.get(Servico, self.vinculado_servico_id)
                servico_nome = servico.nome if servico else None
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)
                pass

        orcamento_item_id = self.orcamento_item_id
        orcamento_item_nome = None
        if orcamento_item_id:
            try:
                item = OrcamentoEngItem.query.get(orcamento_item_id)
                if item:
                    orcamento_item_nome = f"{item.codigo} - {item.descricao}"
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)

        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "usuario_id": self.usuario_id,
            "usuario_nome": self.usuario.username if self.usuario else None,
            "codigo_barras": self.codigo_barras,
            "descricao": self.descricao,
            "beneficiario": self.beneficiario,
            "valor": self.valor,
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "status": self.status,
            "data_pagamento": self.data_pagamento.isoformat() if self.data_pagamento else None,
            "vinculado_servico_id": self.vinculado_servico_id,
            "servico_nome": servico_nome,
            "orcamento_item_id": orcamento_item_id,
            "orcamento_item_nome": orcamento_item_nome,
            "arquivo_nome": self.arquivo_nome,
            "tem_pdf": bool(self.arquivo_nome),
            "dias_para_vencer": dias_para_vencer,
            "urgencia": urgencia,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
