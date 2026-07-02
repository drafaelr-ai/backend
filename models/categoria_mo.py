from extensions import db


class CategoriaMO(db.Model):
    """Lista estável e global de funções de mão de obra (Pedreiro, Servente,
    Carpinteiro...). Match por nome (case-insensitive) na confirmação da CCT;
    categorias faltantes são criadas nesse momento."""
    __tablename__ = 'categoria_mo'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False, index=True)
    descricao = db.Column(db.String(200), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'descricao': self.descricao,
        }
