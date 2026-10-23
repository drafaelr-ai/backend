"""Trilha de auditoria das importações históricas do e-Frotas."""
from datetime import datetime

from extensions import db


class FrotaEFrotasSincronizacao(db.Model):
    """Uma tentativa manual de carregar infrações já existentes.

    O webhook atende eventos novos. Esta tabela torna as buscas retrospectivas
    visíveis no painel, sem confundir uma falha de credencial com ausência de
    infrações.
    """
    __tablename__ = 'frota_efrotas_sincronizacao'

    id = db.Column(db.Integer, primary_key=True)
    solicitado_por_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    data_inicio = db.Column(db.Date, nullable=False)
    data_fim = db.Column(db.Date, nullable=False)
    placas = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='em_andamento')
    # em_andamento | concluida | parcial | erro
    eventos_recebidos = db.Column(db.Integer, nullable=False, default=0)
    importadas = db.Column(db.Integer, nullable=False, default=0)
    atualizadas = db.Column(db.Integer, nullable=False, default=0)
    duplicadas = db.Column(db.Integer, nullable=False, default=0)
    ignoradas = db.Column(db.Integer, nullable=False, default=0)
    erros = db.Column(db.Integer, nullable=False, default=0)
    mensagem = db.Column(db.String(500), nullable=True)
    iniciado_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finalizado_em = db.Column(db.DateTime, nullable=True)

    solicitado_por = db.relationship('User', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'solicitado_por_id': self.solicitado_por_id,
            'solicitado_por_nome': self.solicitado_por.username if self.solicitado_por else None,
            'data_inicio': self.data_inicio.isoformat() if self.data_inicio else None,
            'data_fim': self.data_fim.isoformat() if self.data_fim else None,
            'placas': self.placas or [],
            'status': self.status,
            'eventos_recebidos': self.eventos_recebidos,
            'importadas': self.importadas,
            'atualizadas': self.atualizadas,
            'duplicadas': self.duplicadas,
            'ignoradas': self.ignoradas,
            'erros': self.erros,
            'mensagem': self.mensagem,
            'iniciado_em': self.iniciado_em.isoformat() if self.iniciado_em else None,
            'finalizado_em': self.finalizado_em.isoformat() if self.finalizado_em else None,
        }
