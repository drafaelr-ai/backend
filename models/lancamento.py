import logging
from extensions import db

logger = logging.getLogger(__name__)


class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)

    valor_total = db.Column(db.Float, nullable=False)
    valor_pago = db.Column(db.Float, nullable=False, default=0.0)

    data = db.Column(db.Date, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    pix = db.Column(db.String(100))
    prioridade = db.Column(db.Integer, nullable=False, default=0)
    fornecedor = db.Column(db.String(150), nullable=True)

    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='lancamentos_vinculados', lazy=True)

    # Vínculo com item do orçamento (orcamento_eng_item). Coluna+FK já existem no banco.
    orcamento_item_id = db.Column(db.Integer, nullable=True)

    def to_dict(self, orcamento_item_nome_map=None):
        """
        orcamento_item_nome_map (opcional): dict {orcamento_item_id: "codigo - descricao"}
        pré-carregado pelo chamador para evitar 1 query extra por lançamento (N+1).
        Se não for passado, mantém o comportamento antigo (query individual).
        """
        # Trata segmento dinamicamente (não está no modelo)
        segmento_value = 'Material'
        try:
            if hasattr(self, 'segmento') and self.segmento:
                segmento_value = self.segmento
        except Exception:
            logger.warning("Excecao suprimida em to_dict", exc_info=True)
            pass

        # Nome do item de orçamento vinculado (lê via coluna mapeada)
        orcamento_item_nome = None
        if self.orcamento_item_id:
            if orcamento_item_nome_map is not None:
                orcamento_item_nome = orcamento_item_nome_map.get(self.orcamento_item_id)
            else:
                from models.orcamento_eng_item import OrcamentoEngItem
                item = OrcamentoEngItem.query.get(self.orcamento_item_id)
                if item:
                    orcamento_item_nome = f"{item.codigo} - {item.descricao}"

        return {
            "id": self.id, "obra_id": self.obra_id, "tipo": self.tipo,
            "descricao": self.descricao,
            "valor_total": self.valor_total,
            "valor_pago": self.valor_pago,
            "data": self.data.isoformat(),
            "data_vencimento": self.data_vencimento.isoformat() if self.data_vencimento else None,
            "status": self.status, "pix": self.pix,
            "prioridade": self.prioridade,
            "fornecedor": self.fornecedor,
            "servico_id": self.servico_id,
            "servico_nome": self.servico.nome if self.servico else None,
            "orcamento_item_id": self.orcamento_item_id,
            "orcamento_item_nome": orcamento_item_nome,
            "segmento": segmento_value,
            "lancamento_id": self.id
        }
