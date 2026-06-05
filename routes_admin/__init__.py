from routes_admin.health import health_bp
from routes_admin.auth_admin import auth_admin_bp
from routes_admin.usuarios_admin import usuarios_admin_bp
from routes_admin.categorias_admin import categorias_admin_bp
from routes_admin.imoveis_admin import imoveis_admin_bp
from routes_admin.lancamentos_admin import lancamentos_admin_bp
from routes_admin.dashboard_admin import dashboard_admin_bp
from routes_admin.importar_obra import importar_obra_bp
from routes_admin.boletos_admin import boletos_admin_bp
from routes_admin.superlink_admin import superlink_admin_bp

__all__ = [
    'health_bp',
    'auth_admin_bp',
    'usuarios_admin_bp',
    'categorias_admin_bp',
    'imoveis_admin_bp',
    'lancamentos_admin_bp',
    'dashboard_admin_bp',
    'importar_obra_bp',
    'boletos_admin_bp',
    'superlink_admin_bp',
]
