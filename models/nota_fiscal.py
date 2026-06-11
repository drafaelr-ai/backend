from sqlalchemy.orm import deferred
from extensions import db


class NotaFiscal(db.Model):
    __tablename__ = 'nota_fiscal'
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)

    filename = db.Column(db.String(255), nullable=False)
    mimetype = db.Column(db.String(100), nullable=False)
    data = deferred(db.Column(db.LargeBinary, nullable=False))

    item_id = db.Column(db.Integer, nullable=False)
    item_type = db.Column(db.String(50), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "filename": self.filename,
            "mimetype": self.mimetype,
            "item_id": self.item_id,
            "item_type": self.item_type
        }
