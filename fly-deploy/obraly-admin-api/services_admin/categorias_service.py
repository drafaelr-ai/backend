import logging

from extensions_admin import db
from models_admin import Categoria

logger = logging.getLogger(__name__)


def criar_categorias_padrao():
    """Cria categorias padrão se não existirem"""
    categorias_padrao = [
        # Despesas
        {'nome': 'IPTU', 'tipo': 'despesa', 'icone': '🏛️', 'cor': '#ef4444', 'ordem': 1},
        {'nome': 'Condomínio', 'tipo': 'despesa', 'icone': '🏢', 'cor': '#f97316', 'ordem': 2},
        {'nome': 'Energia', 'tipo': 'despesa', 'icone': '⚡', 'cor': '#eab308', 'ordem': 3},
        {'nome': 'Água', 'tipo': 'despesa', 'icone': '💧', 'cor': '#3b82f6', 'ordem': 4},
        {'nome': 'Gás', 'tipo': 'despesa', 'icone': '🔥', 'cor': '#f59e0b', 'ordem': 5},
        {'nome': 'Internet/TV', 'tipo': 'despesa', 'icone': '📡', 'cor': '#8b5cf6', 'ordem': 6},
        {'nome': 'Seguro', 'tipo': 'despesa', 'icone': '🛡️', 'cor': '#06b6d4', 'ordem': 7},
        {'nome': 'Manutenção', 'tipo': 'despesa', 'icone': '🔧', 'cor': '#64748b', 'ordem': 8},
        {'nome': 'Limpeza', 'tipo': 'despesa', 'icone': '🧹', 'cor': '#10b981', 'ordem': 9},
        {'nome': 'Jardinagem', 'tipo': 'despesa', 'icone': '🌳', 'cor': '#22c55e', 'ordem': 10},
        {'nome': 'Empregados', 'tipo': 'despesa', 'icone': '👷', 'cor': '#0ea5e9', 'ordem': 11},
        {'nome': 'Diarista', 'tipo': 'despesa', 'icone': '🧽', 'cor': '#14b8a6', 'ordem': 12},
        {'nome': 'Taxa Extra', 'tipo': 'despesa', 'icone': '📋', 'cor': '#a855f7', 'ordem': 13},
        {'nome': 'Reforma', 'tipo': 'despesa', 'icone': '🏗️', 'cor': '#ec4899', 'ordem': 14},
        {'nome': 'Outras Despesas', 'tipo': 'despesa', 'icone': '📦', 'cor': '#6b7280', 'ordem': 99},

        # Receitas
        {'nome': 'Aluguel', 'tipo': 'receita', 'icone': '🏠', 'cor': '#10b981', 'ordem': 1},
        {'nome': 'Reembolso', 'tipo': 'receita', 'icone': '💵', 'cor': '#22c55e', 'ordem': 2},
        {'nome': 'Venda', 'tipo': 'receita', 'icone': '🤝', 'cor': '#059669', 'ordem': 3},
        {'nome': 'Outras Receitas', 'tipo': 'receita', 'icone': '💰', 'cor': '#34d399', 'ordem': 99},
    ]

    for cat_data in categorias_padrao:
        existe = Categoria.query.filter_by(nome=cat_data['nome'], tipo=cat_data['tipo']).first()
        if not existe:
            categoria = Categoria(**cat_data)
            db.session.add(categoria)

    db.session.commit()
    logger.info("Categorias padrão criadas/verificadas")
