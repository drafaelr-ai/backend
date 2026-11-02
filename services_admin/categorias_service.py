import logging

from extensions_admin import db
from models_admin import Categoria

logger = logging.getLogger(__name__)


def criar_categorias_padrao():
    """Cria categorias padrão se não existirem.

    `icone` guarda uma classe do Tabler Icons (docs/CONVENCOES.md proíbe
    emoji na interface). Bancos antigos ainda têm emoji nessa coluna; o
    front traduz os dois formatos em `src/utils/iconeCategoria.js`, então
    não é preciso migrar os dados existentes.
    """
    categorias_padrao = [
        # Despesas
        {'nome': 'IPTU', 'tipo': 'despesa', 'icone': 'ti-building-bank', 'cor': '#ef4444', 'ordem': 1},
        {'nome': 'Condomínio', 'tipo': 'despesa', 'icone': 'ti-building-community', 'cor': '#f97316', 'ordem': 2},
        {'nome': 'Energia', 'tipo': 'despesa', 'icone': 'ti-bulb', 'cor': '#eab308', 'ordem': 3},
        {'nome': 'Água', 'tipo': 'despesa', 'icone': 'ti-droplet', 'cor': '#3b82f6', 'ordem': 4},
        {'nome': 'Gás', 'tipo': 'despesa', 'icone': 'ti-flame', 'cor': '#f59e0b', 'ordem': 5},
        {'nome': 'Internet/TV', 'tipo': 'despesa', 'icone': 'ti-wifi', 'cor': '#8b5cf6', 'ordem': 6},
        {'nome': 'Seguro', 'tipo': 'despesa', 'icone': 'ti-shield-check', 'cor': '#06b6d4', 'ordem': 7},
        {'nome': 'Manutenção', 'tipo': 'despesa', 'icone': 'ti-tool', 'cor': '#64748b', 'ordem': 8},
        {'nome': 'Limpeza', 'tipo': 'despesa', 'icone': 'ti-sparkles', 'cor': '#10b981', 'ordem': 9},
        {'nome': 'Jardinagem', 'tipo': 'despesa', 'icone': 'ti-shovel', 'cor': '#22c55e', 'ordem': 10},
        {'nome': 'Empregados', 'tipo': 'despesa', 'icone': 'ti-hard-hat', 'cor': '#0ea5e9', 'ordem': 11},
        {'nome': 'Diarista', 'tipo': 'despesa', 'icone': 'ti-user', 'cor': '#14b8a6', 'ordem': 12},
        {'nome': 'Taxa Extra', 'tipo': 'despesa', 'icone': 'ti-clipboard-list', 'cor': '#a855f7', 'ordem': 13},
        {'nome': 'Reforma', 'tipo': 'despesa', 'icone': 'ti-bricks', 'cor': '#ec4899', 'ordem': 14},
        {'nome': 'Outras Despesas', 'tipo': 'despesa', 'icone': 'ti-package', 'cor': '#6b7280', 'ordem': 99},

        # Receitas
        {'nome': 'Aluguel', 'tipo': 'receita', 'icone': 'ti-home', 'cor': '#10b981', 'ordem': 1},
        {'nome': 'Reembolso', 'tipo': 'receita', 'icone': 'ti-cash-banknote', 'cor': '#22c55e', 'ordem': 2},
        {'nome': 'Venda', 'tipo': 'receita', 'icone': 'ti-shopping-cart', 'cor': '#059669', 'ordem': 3},
        {'nome': 'Outras Receitas', 'tipo': 'receita', 'icone': 'ti-coins', 'cor': '#34d399', 'ordem': 99},
    ]

    for cat_data in categorias_padrao:
        existe = Categoria.query.filter_by(nome=cat_data['nome'], tipo=cat_data['tipo']).first()
        if not existe:
            categoria = Categoria(**cat_data)
            db.session.add(categoria)

    db.session.commit()
    logger.info("Categorias padrão criadas/verificadas")
