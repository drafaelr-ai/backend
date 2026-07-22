from datetime import datetime

from extensions import db


class PontoMarcacao(db.Model):
    """Marcação imutável de jornada, manual ou recebida de um REP.

    `referencia_externa` recebe, por exemplo, o NSR do relógio. Quando existe,
    é única e torna importações repetidas idempotentes.
    """
    __tablename__ = 'ponto_marcacao'

    id = db.Column(db.Integer, primary_key=True)
    funcionario_id = db.Column(
        db.Integer, db.ForeignKey('funcionario.id', ondelete='CASCADE'), nullable=False,
    )
    data_hora = db.Column(db.DateTime, nullable=False, index=True)
    tipo = db.Column(db.String(30), nullable=False, default='entrada')
    origem = db.Column(db.String(20), nullable=False, default='manual')
    referencia_externa = db.Column(db.String(120), nullable=True, unique=True)
    observacao = db.Column(db.String(300), nullable=True)
    registrada_por_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    funcionario = db.relationship('Funcionario', lazy=True)
    registrada_por = db.relationship('User', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'funcionario_id': self.funcionario_id,
            'funcionario_nome': self.funcionario.nome if self.funcionario else None,
            'data_hora': self.data_hora.isoformat() if self.data_hora else None,
            'tipo': self.tipo,
            'origem': self.origem,
            'referencia_externa': self.referencia_externa,
            'observacao': self.observacao,
            'registrada_por_nome': self.registrada_por.username if self.registrada_por else None,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
