from datetime import datetime
from extensions import db


class OrcamentoEngItem(db.Model):
    """
    Itens do orçamento de engenharia
    Cada item pode ser vinculado a um Serviço (Kanban)
    """
    __tablename__ = 'orcamento_eng_item'

    id = db.Column(db.Integer, primary_key=True)
    etapa_id = db.Column(db.Integer, db.ForeignKey('orcamento_eng_etapa.id'), nullable=False)

    codigo = db.Column(db.String(20))  # 01.01, 01.02...
    descricao = db.Column(db.String(500), nullable=False)
    unidade = db.Column(db.String(20), nullable=False)
    quantidade = db.Column(db.Float, default=0)

    # Tipo de composição
    tipo_composicao = db.Column(db.String(20), default='separado')  # separado | composto | fornecimento

    # Se separado
    preco_mao_obra = db.Column(db.Float, nullable=True)
    preco_material = db.Column(db.Float, nullable=True)

    # Se composto
    preco_unitario = db.Column(db.Float, nullable=True)
    rateio_mo = db.Column(db.Float, default=50)
    rateio_mat = db.Column(db.Float, default=50)

    # Vinculação com Serviço (Kanban)
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'), nullable=True)
    servico = db.relationship('Servico', backref='orcamento_itens', lazy=True)

    # Valores pagos (calculados a partir dos pagamentos do Serviço)
    valor_pago_mo = db.Column(db.Float, default=0)
    valor_pago_mat = db.Column(db.Float, default=0)

    ordem = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def calcular_totais(self):
        """Calcula totais do item baseado no tipo de composição"""
        if self.tipo_composicao == 'composto':
            total = (self.preco_unitario or 0) * (self.quantidade or 0)
            # Composto: NÃO rateia entre MO e Material, vai para "Serviço"
            total_mo = 0
            total_mat = 0
            total_servico = total
            total_fornecimento = 0
        elif self.tipo_composicao == 'fornecimento':
            total = (self.preco_unitario or 0) * (self.quantidade or 0)
            # Fornecimento/locação: sem mão de obra própria da obra (ex: aluguel de andaime).
            # Bucket próprio, separado de "Serviço" (empreitada) e de MO/Material rateado.
            total_mo = 0
            total_mat = 0
            total_servico = 0
            total_fornecimento = total
        else:
            total_mo = (self.preco_mao_obra or 0) * (self.quantidade or 0)
            total_mat = (self.preco_material or 0) * (self.quantidade or 0)
            total_servico = 0
            total_fornecimento = 0
            total = total_mo + total_mat

        return {
            'total_mao_obra': total_mo,
            'total_material': total_mat,
            'total_servico': total_servico,
            'total_fornecimento': total_fornecimento,
            'total': total
        }

    def to_dict(self):
        totais = self.calcular_totais()
        total_pago = (self.valor_pago_mo or 0) + (self.valor_pago_mat or 0)
        percentual = (total_pago / totais['total'] * 100) if totais['total'] > 0 else 0

        return {
            'id': self.id,
            'etapa_id': self.etapa_id,
            'codigo': self.codigo,
            'descricao': self.descricao,
            'unidade': self.unidade,
            'quantidade': self.quantidade,
            'tipo_composicao': self.tipo_composicao,
            'preco_mao_obra': self.preco_mao_obra,
            'preco_material': self.preco_material,
            'preco_unitario': self.preco_unitario,
            'rateio_mo': self.rateio_mo,
            'rateio_mat': self.rateio_mat,
            'servico_id': self.servico_id,
            'servico_nome': self.servico.nome if self.servico else None,
            'valor_pago_mo': self.valor_pago_mo or 0,
            'valor_pago_mat': self.valor_pago_mat or 0,
            'total_mao_obra': totais['total_mao_obra'],
            'total_material': totais['total_material'],
            'total_servico': totais['total_servico'],
            'total_fornecimento': totais['total_fornecimento'],
            'total': totais['total'],
            'total_pago': total_pago,
            'percentual_executado': round(percentual, 1),
            'ordem': self.ordem
        }
