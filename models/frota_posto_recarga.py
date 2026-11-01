from datetime import datetime

from extensions import db


TIPOS = ('recarga', 'ajuste')


class FrotaPostoRecarga(db.Model):
    """Movimento de crédito na conta de um posto (pré-pago).

    O saldo não tem coluna própria de propósito: ele é sempre
    `soma dos movimentos - soma dos abastecimentos` daquele posto. Um saldo
    gravado ficaria desatualizado no primeiro abastecimento lançado por
    fora desta tela (link do motorista, lançamento manual, e-Frotas).

    Duas naturezas, na coluna `tipo`:

    - `recarga`: crédito comprado. Quantidade sempre positiva.
    - `ajuste`: correção de saldo. É o que o operador gera quando digita o
      saldo que o posto diz que ele tem: o sistema grava a DIFERENÇA, para o
      saldo passar a bater sem apagar o histórico de quem lançou o quê. Pode
      ser negativo.

    `valor` e `litros` coexistem porque a unidade é do posto: em posto de
    dinheiro o saldo anda por `valor`; em posto de litros, por `litros` — e
    aí `valor` segue servindo para registrar quanto se pagou, sem entrar na
    conta do saldo.
    """

    __tablename__ = 'frota_posto_recarga'

    id = db.Column(db.Integer, primary_key=True)
    posto_id = db.Column(
        db.Integer,
        db.ForeignKey('frota_posto_combustivel.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    data = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(10), nullable=False, default='recarga')
    # Nulável: em posto de litros, informar o valor pago é opcional.
    valor = db.Column(db.Numeric(12, 2), nullable=True)
    litros = db.Column(db.Numeric(10, 2), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    # Referência fraca ao usuário: o histórico financeiro sobrevive à
    # exclusão da conta de quem lançou.
    criado_por_id = db.Column(db.Integer, nullable=True)
    data_criacao = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    posto = db.relationship('FrotaPostoCombustivel', lazy=True)

    def quantidade(self):
        """O movimento na unidade do posto — é este número que move o saldo."""
        if self.posto is not None and self.posto.unidade == 'litros':
            return float(self.litros) if self.litros is not None else None
        return float(self.valor) if self.valor is not None else None

    def to_dict(self, criado_por_nome=None):
        return {
            'id': self.id,
            'posto_id': self.posto_id,
            'posto_nome': self.posto.nome if self.posto else None,
            'data': self.data.isoformat() if self.data else None,
            'tipo': self.tipo or 'recarga',
            'unidade': self.posto.unidade if self.posto else 'reais',
            'valor': float(self.valor) if self.valor is not None else None,
            'litros': float(self.litros) if self.litros is not None else None,
            'quantidade': self.quantidade(),
            'observacao': self.observacao,
            'criado_por_id': self.criado_por_id,
            'criado_por_nome': criado_por_nome,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
