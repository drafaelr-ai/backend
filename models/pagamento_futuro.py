import logging
from extensions import db

logger = logging.getLogger(__name__)


class PagamentoFuturo(db.Model):
    """Pagamentos únicos planejados para o futuro"""
    __tablename__ = 'pagamento_futuro'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Previsto')  # Previsto/Pago/Cancelado
    fornecedor = db.Column(db.String(150), nullable=True)
    pix = db.Column(db.String(100), nullable=True)  # Chave PIX para pagamento
    codigo_barras = db.Column(db.String(100), nullable=True)  # Código de barras / linha digitável
    observacoes = db.Column(db.Text, nullable=True)

    # NOVOS CAMPOS: Para vincular pagamentos futuros a serviços
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    tipo = db.Column(db.String(50), nullable=True)  # 'Mão de Obra', 'Material', ou 'Despesa'

    # Vínculo com item do orçamento (orcamento_eng_item). Coluna+FK já existem no banco.
    orcamento_item_id = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        from models.orcamento_eng_item import OrcamentoEngItem

        # Nome do item de orçamento vinculado (lê via coluna mapeada)
        orcamento_item_nome = None
        if self.orcamento_item_id:
            item = OrcamentoEngItem.query.get(self.orcamento_item_id)
            if item:
                orcamento_item_nome = f"{item.codigo} - {item.descricao}"

        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "descricao": self.descricao,
            "valor": self.valor,
            "data_vencimento": self.data_vencimento.isoformat(),
            "status": self.status,
            "fornecedor": self.fornecedor,
            "pix": self.pix,
            "codigo_barras": self.codigo_barras,
            "observacoes": self.observacoes,
            "servico_id": self.servico_id,
            "tipo": self.tipo,
            "orcamento_item_id": self.orcamento_item_id,
            "orcamento_item_nome": orcamento_item_nome
        }
