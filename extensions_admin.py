from flask import request
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS

db = SQLAlchemy()
jwt = JWTManager()
cors = CORS()

# Mesma whitelist do app principal (app.py) — o painel admin roda no mesmo
# domínio front-end (obraly.uk), então usamos a mesma lista de origens.
ALLOWED_ORIGINS = [
    'https://obraly.uk',
    'https://www.obraly.uk',
    'http://localhost:3000',
    'http://localhost:3001',
    'https://localhost',       # Capacitor Android (androidScheme: https)
    'capacitor://localhost',   # Capacitor Android (scheme padrão)
    'ionic://localhost',       # fallback Ionic/Capacitor
]


def apply_cors_headers(response):
    origin = request.headers.get('Origin', '')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response
