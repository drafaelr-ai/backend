from datetime import datetime

from extensions_admin import db


class Imovel(db.Model):
    """Imóveis (centros de custo) - podem vir de obras finalizadas ou cadastro manual"""
    __tablename__ = 'admin_imovel'

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('admin_usuario.id'), nullable=False)

    # Identificação
    nome = db.Column(db.String(200), nullable=False)  # Ex: "Apartamento 101 - Ed. Central"
    tipo = db.Column(db.String(50), nullable=False)   # apartamento, casa, sala_comercial, terreno, escritorio

    # Endereço
    endereco = db.Column(db.String(300))
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    cep = db.Column(db.String(10))

    # Status e uso
    status = db.Column(db.String(30), default='proprio')  # proprio, alugado, a_venda, em_obra
    valor_aluguel = db.Column(db.Float, default=0)        # Se alugado, valor mensal
    valor_mercado = db.Column(db.Float, default=0)        # Valor estimado de mercado

    # Integração com Obraly (obras finalizadas)
    obra_id_origem = db.Column(db.Integer, nullable=True)  # ID da obra no módulo Obras (se importado)
    custo_construcao = db.Column(db.Float, default=0)      # Custo total da obra

    # Metadados
    observacoes = db.Column(db.Text)
    ativo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relacionamentos
    lancamentos = db.relationship('Lancamento', backref='imovel', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'usuario_id': self.usuario_id,
            'nome': self.nome,
            'tipo': self.tipo,
            'endereco': self.endereco,
            'cidade': self.cidade,
            'estado': self.estado,
            'cep': self.cep,
            'status': self.status,
            'valor_aluguel': self.valor_aluguel,
            'valor_mercado': self.valor_mercado,
            'obra_id_origem': self.obra_id_origem,
            'custo_construcao': self.custo_construcao,
            'observacoes': self.observacoes,
            'ativo': self.ativo,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
