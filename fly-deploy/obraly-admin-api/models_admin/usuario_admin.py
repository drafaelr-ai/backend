from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash

from extensions_admin import db


class Usuario(db.Model):
    """Usuários do módulo administrativo (independente do módulo Obras)"""
    __tablename__ = 'admin_usuario'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=True)
    role = db.Column(db.String(20), default='operador')  # admin, operador
    ativo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamentos
    imoveis = db.relationship('Imovel', backref='proprietario', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'nome': self.nome,
            'email': self.email,
            'role': self.role,
            'ativo': self.ativo,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
