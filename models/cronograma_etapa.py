import logging
from datetime import timedelta
from sqlalchemy.sql import func
from extensions import db

logger = logging.getLogger(__name__)


class CronogramaEtapa(db.Model):
    """
    Etapas e Subetapas do cronograma (estrutura hierárquica)
    - etapa_pai_id = NULL → É uma ETAPA (agrupador: Infraestrutura, Revestimento)
    - etapa_pai_id = X → É uma SUBETAPA (item: Escavação, Tubulação)
    """
    __tablename__ = 'cronograma_etapa'

    id = db.Column(db.Integer, primary_key=True)
    cronograma_id = db.Column(db.Integer, db.ForeignKey('cronograma_obra.id'), nullable=False)

    etapa_pai_id = db.Column(db.Integer, db.ForeignKey('cronograma_etapa.id'), nullable=True)

    nome = db.Column(db.String(200), nullable=False)
    ordem = db.Column(db.Integer, nullable=False, default=1)

    duracao_dias = db.Column(db.Integer, nullable=True, default=1)
    data_inicio = db.Column(db.Date, nullable=True)
    data_fim = db.Column(db.Date, nullable=True)

    inicio_ajustado_manualmente = db.Column(db.Boolean, default=False)

    etapa_anterior_id = db.Column(db.Integer, db.ForeignKey('cronograma_etapa.id'), nullable=True)
    tipo_condicao = db.Column(db.String(20), nullable=True)  # 'apos_termino', 'dias_apos', 'dias_antes', 'manual'
    dias_offset = db.Column(db.Integer, nullable=True, default=0)

    percentual_conclusao = db.Column(db.Float, nullable=False, default=0.0)

    observacoes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    subetapas = db.relationship('CronogramaEtapa',
                                backref=db.backref('etapa_pai', remote_side=[id]),
                                lazy='dynamic',
                                foreign_keys=[etapa_pai_id])

    def is_etapa_pai(self):
        return self.etapa_pai_id is None

    def calcular_data_fim(self):
        if self.data_inicio and self.duracao_dias:
            self.data_fim = self.data_inicio + timedelta(days=self.duracao_dias - 1)
        return self.data_fim

    def calcular_datas_das_subetapas(self):
        try:
            subs = self.subetapas.order_by(CronogramaEtapa.ordem).all()
            if subs:
                datas_inicio = [s.data_inicio for s in subs if s.data_inicio]
                datas_fim = [s.data_fim for s in subs if s.data_fim]
                if datas_inicio:
                    self.data_inicio = min(datas_inicio)
                if datas_fim:
                    self.data_fim = max(datas_fim)
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            pass

    def calcular_percentual_das_subetapas(self):
        try:
            subs = self.subetapas.all()
            if not subs:
                return self.percentual_conclusao or 0.0

            total_dias = 0
            soma_ponderada = 0

            for sub in subs:
                dias = sub.duracao_dias or 1
                total_dias += dias
                soma_ponderada += (sub.percentual_conclusao or 0) * dias

            if total_dias == 0:
                return 0.0

            return round(soma_ponderada / total_dias, 2)
        except Exception:
            logger.warning("Excecao suprimida em ", exc_info=True)
            return self.percentual_conclusao or 0.0

    def total_dias_subetapas(self):
        try:
            subs = self.subetapas.all()
            return sum(s.duracao_dias or 0 for s in subs)
        except Exception:
            logger.warning("Excecao suprimida em total_dias_subetapas", exc_info=True)
            return self.duracao_dias or 0

    def to_dict(self):
        subetapas_list = []
        total_dias = self.duracao_dias or 0
        percentual = float(self.percentual_conclusao or 0)

        if self.is_etapa_pai():
            try:
                subetapas_list = [s.to_dict() for s in self.subetapas.order_by(CronogramaEtapa.ordem).all()]
                total_dias = self.total_dias_subetapas()
                percentual = self.calcular_percentual_das_subetapas()
            except Exception:
                logger.warning("Excecao suprimida em ", exc_info=True)
                pass

        return {
            'id': self.id,
            'cronograma_id': self.cronograma_id,
            'etapa_pai_id': self.etapa_pai_id,
            'is_etapa_pai': self.is_etapa_pai(),
            'nome': self.nome,
            'ordem': self.ordem,
            'duracao_dias': self.duracao_dias,
            'total_dias': total_dias,
            'data_inicio': self.data_inicio.isoformat() if self.data_inicio else None,
            'data_fim': self.data_fim.isoformat() if self.data_fim else None,
            'inicio_ajustado_manualmente': self.inicio_ajustado_manualmente,
            'etapa_anterior_id': self.etapa_anterior_id,
            'tipo_condicao': self.tipo_condicao,
            'dias_offset': self.dias_offset,
            'percentual_conclusao': percentual,
            'observacoes': self.observacoes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'subetapas': subetapas_list,
        }
