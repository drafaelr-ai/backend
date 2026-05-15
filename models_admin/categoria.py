from extensions_admin import db


class Categoria(db.Model):
    """Categorias de lançamentos (despesas/receitas)"""
    __tablename__ = 'admin_categoria'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # despesa, receita
    icone = db.Column(db.String(10), default='💰')
    cor = db.Column(db.String(7), default='#6b7280')  # Cor hex
    ordem = db.Column(db.Integer, default=0)
    ativo = db.Column(db.Boolean, default=True)

    # Relacionamentos
    lancamentos = db.relationship('Lancamento', backref='categoria', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'tipo': self.tipo,
            'icone': self.icone,
            'cor': self.cor,
            'ordem': self.ordem,
            'ativo': self.ativo
        }
