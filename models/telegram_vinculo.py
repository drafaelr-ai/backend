from datetime import datetime

from extensions import db


class TelegramVinculo(db.Model):
    """Vínculo do usuário Obraly com o chat do bot no Telegram.

    Fluxo: o app gera `link_code`, o usuário abre t.me/<bot>?start=<code> e dá
    Start; a confirmação lê o getUpdates do bot, casa o código e grava o
    `chat_id`. Com chat_id preenchido, toda notificação do sino também é
    enviada pro Telegram (best-effort, nunca bloqueia o fluxo principal).
    Tabela própria — a tabela `user` não é alterada.
    """
    __tablename__ = 'telegram_vinculo'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
        nullable=False, unique=True,
    )
    chat_id = db.Column(db.String(32), nullable=True)      # preenchido na confirmação
    chat_nome = db.Column(db.String(120), nullable=True)   # nome no Telegram (exibição)
    link_code = db.Column(db.String(32), nullable=True)    # código pendente do deep-link
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'vinculado': bool(self.chat_id),
            'chat_nome': self.chat_nome,
        }
