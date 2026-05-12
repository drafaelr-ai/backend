from datetime import datetime
from extensions import db


class DiarioImagem(db.Model):
    """Imagens do diário de obras"""
    __tablename__ = 'diario_imagens'

    id = db.Column(db.Integer, primary_key=True)
    diario_id = db.Column(db.Integer, db.ForeignKey('diario_obra.id'), nullable=False)
    arquivo_nome = db.Column(db.String(255), nullable=False)
    arquivo_base64 = db.Column(db.Text, nullable=False)  # Armazena imagem em base64
    legenda = db.Column(db.String(500))
    ordem = db.Column(db.Integer, default=0)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self, include_base64=False):
        """Retorna dict. Por padrao NAO inclui base64 para economizar banda"""
        result = {
            'id': self.id,
            'diario_id': self.diario_id,
            'arquivo_nome': self.arquivo_nome,
            'legenda': self.legenda,
            'ordem': self.ordem,
            'criado_em': self.criado_em.strftime('%Y-%m-%d %H:%M:%S') if self.criado_em else None,
            'has_image': bool(self.arquivo_base64)
        }
        if include_base64:
            result['arquivo_base64'] = self.arquivo_base64
        return result

    def to_dict_full(self):
        """Retorna dict COM base64 - usar apenas quando necessario"""
        return {
            'id': self.id,
            'diario_id': self.diario_id,
            'arquivo_nome': self.arquivo_nome,
            'arquivo_base64': self.arquivo_base64,
            'legenda': self.legenda,
            'ordem': self.ordem,
            'criado_em': self.criado_em.strftime('%Y-%m-%d %H:%M:%S') if self.criado_em else None
        }
