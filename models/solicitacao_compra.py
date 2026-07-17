from datetime import datetime

from extensions import db


class SolicitacaoCompra(db.Model):
    """Solicitação de compra de materiais/insumos/equipamentos de uma obra.

    Fluxo: Aberta -> Em cotação -> Aguardando aprovação -> Aprovada | Rejeitada | Cancelada.
    Ao aprovar/efetivar, é criado um PagamentoFuturo ('Previsto') na obra e o elo
    fica em `pagamento_futuro_id` (referência fraca — não altera schema existente).
    `token_publico` permite visualização read-only sem login (compartilhável no WhatsApp).
    """
    __tablename__ = 'solicitacao_compra'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='CASCADE'), nullable=False,
    )
    solicitante_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_necessidade = db.Column(db.Date, nullable=True)
    tipo = db.Column(db.String(30), nullable=False, default='Material')  # Material | Equipamentos | Mão de Obra | Despesa
    status = db.Column(db.String(30), nullable=False, default='Aberta')
    observacao = db.Column(db.Text, nullable=True)
    token_publico = db.Column(db.String(64), nullable=False, unique=True)

    # Referências fracas (sem FK) — preenchidas na decisão
    cotacao_aprovada_id = db.Column(db.Integer, nullable=True)
    pagamento_futuro_id = db.Column(db.Integer, nullable=True)
    aprovador_id = db.Column(db.Integer, nullable=True)
    data_decisao = db.Column(db.DateTime, nullable=True)
    motivo_rejeicao = db.Column(db.String(300), nullable=True)

    obra = db.relationship('Obra', lazy=True)
    solicitante = db.relationship('User', foreign_keys=[solicitante_id], lazy=True)
    itens = db.relationship(
        'SolicitacaoItem', lazy=True, cascade='all, delete-orphan',
        backref='solicitacao', order_by='SolicitacaoItem.id',
    )
    cotacoes = db.relationship(
        'SolicitacaoCotacao', lazy=True, cascade='all, delete-orphan',
        backref='solicitacao', order_by='SolicitacaoCotacao.id',
    )

    def _aprovador_nome(self):
        if not self.aprovador_id:
            return None
        from models.user import User
        u = User.query.get(self.aprovador_id)
        return u.username if u else None

    def to_dict(self, incluir_detalhes=False):
        out = {
            'id': self.id,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'solicitante_id': self.solicitante_id,
            'solicitante_nome': self.solicitante.username if self.solicitante else 'usuário removido',
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
            'data_necessidade': self.data_necessidade.isoformat() if self.data_necessidade else None,
            'tipo': self.tipo,
            'status': self.status,
            'observacao': self.observacao,
            'token_publico': self.token_publico,
            'qtd_itens': len(self.itens),
            'resumo': (self.itens[0].descricao
                       + (f' (+{len(self.itens) - 1})' if len(self.itens) > 1 else '')
                       ) if self.itens else None,
            'qtd_cotacoes': len(self.cotacoes),
            'cotacao_aprovada_id': self.cotacao_aprovada_id,
            'pagamento_futuro_id': self.pagamento_futuro_id,
            'aprovador_nome': self._aprovador_nome(),
            'data_decisao': self.data_decisao.isoformat() if self.data_decisao else None,
            'motivo_rejeicao': self.motivo_rejeicao,
        }
        if incluir_detalhes:
            out['itens'] = [i.to_dict() for i in self.itens]
            out['cotacoes'] = [c.to_dict() for c in self.cotacoes]
        return out

    def to_dict_publico(self):
        """Snapshot público (link compartilhável) — nunca expõe cotações/valores."""
        return {
            'obra_nome': self.obra.nome if self.obra else None,
            'solicitante_nome': self.solicitante.username if self.solicitante else None,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
            'data_necessidade': self.data_necessidade.isoformat() if self.data_necessidade else None,
            'tipo': self.tipo,
            'status': self.status,
            'observacao': self.observacao,
            'itens': [i.to_dict() for i in self.itens],
        }
