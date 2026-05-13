import logging
from sqlalchemy.sql import func
from extensions import db

logger = logging.getLogger(__name__)


class CronogramaObra(db.Model):
    __tablename__ = 'cronograma_obra'

    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)

    servico_nome = db.Column(db.String(200), nullable=False)
    ordem = db.Column(db.Integer, nullable=False, default=1)

    data_inicio = db.Column(db.Date, nullable=False)
    data_fim_prevista = db.Column(db.Date, nullable=False)

    data_inicio_real = db.Column(db.Date, nullable=True)
    data_fim_real = db.Column(db.Date, nullable=True)
    percentual_conclusao = db.Column(db.Float, nullable=False, default=0.0)

    tipo_medicao = db.Column(db.String(20), default='empreitada')  # 'area', 'empreitada' ou 'etapas'
    area_total = db.Column(db.Float)
    area_executada = db.Column(db.Float, default=0)
    unidade_medida = db.Column(db.String(10), default='m²')

    observacoes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=func.now())
    updated_at = db.Column(db.DateTime, default=func.now(), onupdate=func.now())

    etapas = db.relationship('CronogramaEtapa', backref='cronograma', lazy='dynamic', cascade="all, delete-orphan")

    def calcular_percentual_por_etapas(self):
        from models.cronograma_etapa import CronogramaEtapa
        try:
            etapas_list = self.etapas.order_by(CronogramaEtapa.ordem).all() if self.etapas else []
            if not etapas_list:
                return 0.0

            total_dias = 0
            soma_ponderada = 0

            for etapa in etapas_list:
                dias = etapa.duracao_dias or 1
                total_dias += dias
                soma_ponderada += (etapa.percentual_conclusao or 0) * dias

            if total_dias == 0:
                return 0.0

            return round(soma_ponderada / total_dias, 2)
        except Exception as e:
            logger.exception(f"[AVISO] Erro ao calcular percentual por etapas: {str(e)}")
            return 0.0

    def atualizar_datas_por_etapas(self):
        from models.cronograma_etapa import CronogramaEtapa
        try:
            etapas_list = self.etapas.order_by(CronogramaEtapa.ordem).all() if self.etapas else []
            if not etapas_list:
                return

            if etapas_list:
                self.data_inicio = etapas_list[0].data_inicio
                self.data_fim_prevista = etapas_list[-1].data_fim
        except Exception as e:
            logger.exception(f"[AVISO] Erro ao atualizar datas por etapas: {str(e)}")

    def to_dict(self):
        from models.cronograma_etapa import CronogramaEtapa
        from models.orcamento_eng_etapa import OrcamentoEngEtapa

        percentual = self.percentual_conclusao
        etapas_list = []

        try:
            etapas_query = CronogramaEtapa.query.filter_by(
                cronograma_id=self.id,
                etapa_pai_id=None
            ).order_by(CronogramaEtapa.ordem).all()

            if etapas_query:
                etapas_list = [etapa.to_dict() for etapa in etapas_query]
                if self.tipo_medicao == 'etapas':
                    percentual = self.calcular_percentual_por_etapas()
        except Exception as e:
            try:
                etapas_query = self.etapas.order_by(CronogramaEtapa.ordem).all() if self.etapas else []
                if etapas_query:
                    etapas_list = [etapa.to_dict() for etapa in etapas_query]
                    if self.tipo_medicao == 'etapas':
                        percentual = self.calcular_percentual_por_etapas()
            except Exception:
                logger.exception(f"[AVISO] Não foi possível carregar etapas: {str(e)}")
                etapas_list = []

        orcamento_etapa_id = None
        orcamento_etapa_nome = None
        orcamento_etapa_codigo = None
        try:
            result = db.session.execute(db.text(
                f"SELECT orcamento_etapa_id FROM cronograma_obra WHERE id = {self.id}"
            )).fetchone()
            if result and result[0]:
                orcamento_etapa_id = result[0]
                etapa = OrcamentoEngEtapa.query.get(orcamento_etapa_id)
                if etapa:
                    orcamento_etapa_nome = etapa.nome
                    orcamento_etapa_codigo = etapa.codigo
        except Exception:
            logger.debug("Coluna orcamento_etapa_id nao existe ainda, ignorando", exc_info=True)
            pass

        return {
            'id': self.id,
            'obra_id': self.obra_id,
            'servico_nome': self.servico_nome,
            'ordem': self.ordem,
            'orcamento_etapa_id': orcamento_etapa_id,
            'orcamento_etapa_nome': orcamento_etapa_nome,
            'orcamento_etapa_codigo': orcamento_etapa_codigo,
            'data_inicio': self.data_inicio.isoformat() if self.data_inicio else None,
            'data_fim_prevista': self.data_fim_prevista.isoformat() if self.data_fim_prevista else None,
            'data_inicio_real': self.data_inicio_real.isoformat() if self.data_inicio_real else None,
            'data_fim_real': self.data_fim_real.isoformat() if self.data_fim_real else None,
            'percentual_conclusao': float(percentual),
            'tipo_medicao': self.tipo_medicao,
            'area_total': self.area_total,
            'area_executada': self.area_executada,
            'unidade_medida': self.unidade_medida,
            'observacoes': self.observacoes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'etapas': etapas_list,
        }
