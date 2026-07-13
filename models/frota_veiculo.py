from datetime import datetime

from extensions import db


class FrotaVeiculo(db.Model):
    """Veículo da frota.

    Alocação atual denormalizada (`local_tipo`/`obra_id`/`imovel_id`/`imovel_nome`);
    o histórico vive em `frota_movimentacao` e toda mudança de local passa por lá
    (mesma transação). `imovel_id` é referência fraca ao banco admin (`admin_imovel`),
    com snapshot do nome — padrão do `admin_imovel.obra_id_origem`.
    """
    __tablename__ = 'frota_veiculo'

    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(10), nullable=False)  # normalizada: uppercase, sem hífen
    renavam = db.Column(db.String(20), nullable=True)
    chassi = db.Column(db.String(30), nullable=True)
    marca = db.Column(db.String(60), nullable=True)
    modelo = db.Column(db.String(80), nullable=False)
    ano_fabricacao = db.Column(db.Integer, nullable=True)
    ano_modelo = db.Column(db.Integer, nullable=True)
    tipo = db.Column(db.String(30), nullable=False, default='carro')
    # carro | caminhonete | caminhao | moto | maquina | outro
    cor = db.Column(db.String(30), nullable=True)
    combustivel = db.Column(db.String(20), nullable=True)
    # flex | gasolina | diesel | etanol | gnv | eletrico
    km_atual = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='ativo')
    # ativo | em_manutencao | vendido | inativo
    condutor_atual_id = db.Column(
        db.Integer, db.ForeignKey('frota_condutor.id', ondelete='SET NULL'), nullable=True,
    )
    local_tipo = db.Column(db.String(10), nullable=True)  # 'obra' | 'imovel' | NULL
    obra_id = db.Column(
        db.Integer, db.ForeignKey('obra.id', ondelete='SET NULL'), nullable=True,
    )
    imovel_id = db.Column(db.Integer, nullable=True)       # referência fraca (banco admin)
    imovel_nome = db.Column(db.String(160), nullable=True)  # snapshot
    observacao = db.Column(db.String(300), nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    condutor_atual = db.relationship('FrotaCondutor', lazy=True)
    obra = db.relationship('Obra', lazy=True)

    def local_nome(self):
        if self.local_tipo == 'obra':
            return self.obra.nome if self.obra else None
        if self.local_tipo == 'imovel':
            return self.imovel_nome
        return None

    def to_dict(self):
        return {
            'id': self.id,
            'placa': self.placa,
            'renavam': self.renavam,
            'chassi': self.chassi,
            'marca': self.marca,
            'modelo': self.modelo,
            'ano_fabricacao': self.ano_fabricacao,
            'ano_modelo': self.ano_modelo,
            'tipo': self.tipo,
            'cor': self.cor,
            'combustivel': self.combustivel,
            'km_atual': self.km_atual,
            'status': self.status,
            'condutor_atual_id': self.condutor_atual_id,
            'condutor_atual_nome': self.condutor_atual.nome if self.condutor_atual else None,
            'local_tipo': self.local_tipo,
            'obra_id': self.obra_id,
            'obra_nome': self.obra.nome if self.obra else None,
            'imovel_id': self.imovel_id,
            'imovel_nome': self.imovel_nome,
            'local_nome': self.local_nome(),
            'observacao': self.observacao,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
