from extensions import db


class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))
    concluida = db.Column(db.Boolean, default=False, nullable=False)  # NOVO: Marca obra como concluída
    bdi = db.Column(db.Float, default=0)  # BDI do orçamento de engenharia (%)
    area = db.Column(db.Float, nullable=True)  # Área da obra em m²
    lancamentos = db.relationship('Lancamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    servicos = db.relationship('Servico', backref='obra', lazy=True, cascade="all, delete-orphan")
    orcamentos = db.relationship('Orcamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    notas_fiscais = db.relationship('NotaFiscal', backref='obra', lazy=True, cascade="all, delete-orphan")
    cronograma_items = db.relationship('CronogramaObra', backref='obra', lazy=True, cascade="all, delete-orphan")
    pagamentos_futuros = db.relationship('PagamentoFuturo', backref='obra', lazy=True, cascade="all, delete-orphan")
    pagamentos_parcelados = db.relationship('PagamentoParcelado', backref='obra', lazy=True, cascade="all, delete-orphan")
    diarios = db.relationship('DiarioObra', backref='obra', lazy=True, cascade="all, delete-orphan")
    boletos = db.relationship('Boleto', backref='obra', lazy=True, cascade="all, delete-orphan")
    orcamento_eng_etapas = db.relationship('OrcamentoEngEtapa', backref='obra', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        try:
            bdi_val = self.bdi if hasattr(self, 'bdi') and self.bdi is not None else 0
            area_val = self.area if hasattr(self, 'area') else None
        except Exception:
            bdi_val = 0
            area_val = None
        return {
            "id": self.id,
            "nome": self.nome,
            "cliente": self.cliente,
            "concluida": self.concluida if hasattr(self, 'concluida') else False,
            "bdi": bdi_val,
            "area": area_val
        }
