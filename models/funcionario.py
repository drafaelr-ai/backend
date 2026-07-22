from datetime import datetime

from extensions import db


class Funcionario(db.Model):
    """Funcionário centralizado (fora de qualquer obra) ou vinculado a uma obra.

    `obra_id` nulo = centralizado / "Sem obra". O salário começa no piso da
    categoria mas é sempre persistido (editável por funcionário).
    """
    __tablename__ = 'funcionario'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    cpf = db.Column(db.String(14), nullable=True, index=True)
    categoria_id = db.Column(
        db.Integer, db.ForeignKey('categoria_mo.id'), nullable=False,
    )
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    salario = db.Column(db.Numeric(12, 2), nullable=False)
    data_admissao = db.Column(db.Date, nullable=True)
    data_demissao = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='ativo')  # ativo | inativo | demitido
    # Jornada usada pela apuração do ponto. Defaults preservam os funcionários
    # existentes e podem ser ajustados no módulo RH/Ponto.
    carga_horaria_diaria = db.Column(db.Numeric(5, 2), nullable=False, default=8)
    horario_entrada = db.Column(db.Time, nullable=True)
    intervalo_minutos = db.Column(db.Integer, nullable=False, default=60)
    dias_trabalho = db.Column(db.JSON, nullable=True, default=lambda: [0, 1, 2, 3, 4])
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    categoria = db.relationship('CategoriaMO', lazy=True)
    obra = db.relationship('Obra', lazy=True)

    def to_dict(self, piso_lookup=None):
        """`piso_lookup`: dict opcional {(categoria_id, uf): piso} pré-calculado
        em lote pelo chamador (ex.: listagens) para evitar 1-2 queries extras
        de piso vigente por funcionário (N+1). Se omitido, calcula na hora
        (comportamento original, usado nas rotas de item único)."""
        acima_do_piso = None
        try:
            if piso_lookup is not None:
                uf = self.obra.uf if self.obra else None
                piso = piso_lookup.get((self.categoria_id, uf)) if uf else None
            else:
                # Resolução do piso vigente vive no rh_service (RH-2). Importação
                # tardia evita ciclo e mantém to_dict resiliente antes do service.
                from services.rh_service import piso_vigente_funcionario
                piso = piso_vigente_funcionario(self)
            if piso is not None and self.salario is not None:
                acima_do_piso = float(self.salario) > float(piso)
        except Exception:
            acima_do_piso = None

        return {
            'id': self.id,
            'nome': self.nome,
            'cpf': self.cpf,
            'categoria_id': self.categoria_id,
            'categoria_nome': self.categoria.nome if self.categoria else None,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'salario': float(self.salario) if self.salario is not None else None,
            'data_admissao': self.data_admissao.isoformat() if self.data_admissao else None,
            'data_demissao': self.data_demissao.isoformat() if self.data_demissao else None,
            'status': self.status,
            'carga_horaria_diaria': float(self.carga_horaria_diaria or 8),
            'horario_entrada': self.horario_entrada.strftime('%H:%M') if self.horario_entrada else '08:00',
            'intervalo_minutos': self.intervalo_minutos if self.intervalo_minutos is not None else 60,
            'dias_trabalho': self.dias_trabalho if self.dias_trabalho is not None else [0, 1, 2, 3, 4],
            'acima_do_piso': acima_do_piso,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
