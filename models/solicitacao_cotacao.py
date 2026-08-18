from datetime import datetime

from extensions import db


class SolicitacaoCotacao(db.Model):
    """Cotação (pesquisa de preços) de uma solicitação de compra."""
    __tablename__ = 'solicitacao_cotacao'

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(
        db.Integer, db.ForeignKey('solicitacao_compra.id', ondelete='CASCADE'), nullable=False,
    )
    fornecedor = db.Column(db.String(150), nullable=False)
    valor_total = db.Column(db.Numeric(12, 2), nullable=False)
    condicao_pagamento = db.Column(db.String(200), nullable=True)
    prazo_entrega = db.Column(db.String(100), nullable=True)
    observacao = db.Column(db.String(300), nullable=True)
    # `arquivo_url` fica por compatibilidade com as cotações antigas. Novas
    # cotações guardam todos os anexos (inclusive o primeiro) em `arquivos`.
    arquivo_url = db.Column(db.String(500), nullable=True)
    arquivos_json = db.Column('arquivos', db.JSON, nullable=True)
    criado_por_id = db.Column(
        db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True,
    )
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

    criado_por = db.relationship('User', foreign_keys=[criado_por_id], lazy=True)

    def anexos_storage(self):
        """Lista normalizada de anexos, incluindo o legado `arquivo_url`."""
        anexos = []
        vistos = set()
        for item in self.arquivos_json or []:
            if isinstance(item, str):
                path, nome = item, None
            elif isinstance(item, dict):
                path, nome = item.get('path'), item.get('nome')
            else:
                continue
            if not path or path in vistos:
                continue
            vistos.add(path)
            anexos.append({'path': path, 'nome': nome or self._nome_do_path(path)})

        if self.arquivo_url and self.arquivo_url not in vistos:
            anexos.insert(0, {
                'path': self.arquivo_url,
                'nome': self._nome_do_path(self.arquivo_url),
            })
        return anexos

    @staticmethod
    def _nome_do_path(path):
        nome = str(path).rsplit('/', 1)[-1]
        # storage_service prefixa UUID hexadecimal de 32 caracteres.
        if len(nome) > 33 and nome[32] == '_':
            return nome[33:]
        return nome or 'Anexo'

    def to_dict(self):
        anexos = self.anexos_storage()
        return {
            'id': self.id,
            'solicitacao_id': self.solicitacao_id,
            'fornecedor': self.fornecedor,
            'valor_total': float(self.valor_total) if self.valor_total is not None else None,
            'condicao_pagamento': self.condicao_pagamento,
            'prazo_entrega': self.prazo_entrega,
            'observacao': self.observacao,
            'tem_arquivo': bool(anexos),
            'quantidade_arquivos': len(anexos),
            'arquivos': [
                {'indice': indice, 'nome': anexo['nome']}
                for indice, anexo in enumerate(anexos)
            ],
            'criado_por_id': self.criado_por_id,
            'criado_por_nome': self.criado_por.username if self.criado_por else None,
            'data_criacao': self.data_criacao.isoformat() if self.data_criacao else None,
        }
