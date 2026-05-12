from extensions import db


class AnexoOrcamento(db.Model):
    __tablename__ = 'anexo_orcamento'
    id = db.Column(db.Integer, primary_key=True)
    orcamento_id = db.Column(db.Integer, db.ForeignKey('orcamento.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    mimetype = db.Column(db.String(100), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "orcamento_id": self.orcamento_id,
            "filename": self.filename,
            "mimetype": self.mimetype
        }
