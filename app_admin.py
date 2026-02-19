"""
===================================================================================
OBRALY - MÓDULO ADMINISTRATIVO (Gestão Patrimonial)
===================================================================================
Backend independente para gestão de imóveis, despesas e receitas.
Compartilha apenas o domínio com o módulo de Obras.

Autor: Sistema Obraly
Data: 2026
===================================================================================
"""

import os
import traceback
from datetime import datetime, date, timedelta
from functools import wraps

from flask import Flask, request, jsonify, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, 
    get_jwt_identity, verify_jwt_in_request
)
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func, extract

# ===================================================================================
# CONFIGURAÇÃO DO APP
# ===================================================================================

app = Flask(__name__)

# Configuração do banco de dados (usar variável de ambiente em produção)
DATABASE_URL = os.environ.get('DATABASE_URL_ADMIN', 'sqlite:///obraly_admin.db')

# Correção para PostgreSQL no Railway/Heroku
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# Configuração JWT
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY_ADMIN', 'obraly-admin-secret-key-2026')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=30)

# Inicializar extensões
db = SQLAlchemy(app)
jwt = JWTManager(app)

# CORS - Permitir todas as origens
CORS(app, resources={r'/*': {'origins': '*'}}, supports_credentials=False)

@app.after_request
def apply_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response

print(f"--- [ADMIN] Backend iniciado ---")
print(f"--- [ADMIN] Database: {DATABASE_URL[:50]}... ---")

# ===================================================================================
# MODELOS DE DADOS
# ===================================================================================

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


class Imovel(db.Model):
    """Imóveis (centros de custo) - podem vir de obras finalizadas ou cadastro manual"""
    __tablename__ = 'admin_imovel'
    
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('admin_usuario.id'), nullable=False)
    
    # Identificação
    nome = db.Column(db.String(200), nullable=False)  # Ex: "Apartamento 101 - Ed. Central"
    tipo = db.Column(db.String(50), nullable=False)   # apartamento, casa, sala_comercial, terreno, escritorio
    
    # Endereço
    endereco = db.Column(db.String(300))
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    cep = db.Column(db.String(10))
    
    # Status e uso
    status = db.Column(db.String(30), default='proprio')  # proprio, alugado, a_venda, em_obra
    valor_aluguel = db.Column(db.Float, default=0)        # Se alugado, valor mensal
    valor_mercado = db.Column(db.Float, default=0)        # Valor estimado de mercado
    
    # Integração com Obraly (obras finalizadas)
    obra_id_origem = db.Column(db.Integer, nullable=True)  # ID da obra no módulo Obras (se importado)
    custo_construcao = db.Column(db.Float, default=0)      # Custo total da obra
    
    # Metadados
    observacoes = db.Column(db.Text)
    ativo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    lancamentos = db.relationship('Lancamento', backref='imovel', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'usuario_id': self.usuario_id,
            'nome': self.nome,
            'tipo': self.tipo,
            'endereco': self.endereco,
            'cidade': self.cidade,
            'estado': self.estado,
            'cep': self.cep,
            'status': self.status,
            'valor_aluguel': self.valor_aluguel,
            'valor_mercado': self.valor_mercado,
            'obra_id_origem': self.obra_id_origem,
            'custo_construcao': self.custo_construcao,
            'observacoes': self.observacoes,
            'ativo': self.ativo,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Categoria(db.Model):
    """Categorias de lançamentos (despesas/receitas)"""
    __tablename__ = 'admin_categoria'
    
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # despesa, receita
    icone = db.Column(db.String(10), default='💰')
    cor = db.Column(db.String(7), default='#6b7280')  # Cor hex
    ordem = db.Column(db.Integer, default=0)
    ativo = db.Column(db.Boolean, default=True)
    
    # Relacionamentos
    lancamentos = db.relationship('Lancamento', backref='categoria', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'nome': self.nome,
            'tipo': self.tipo,
            'icone': self.icone,
            'cor': self.cor,
            'ordem': self.ordem,
            'ativo': self.ativo
        }


class Lancamento(db.Model):
    """Lançamentos de despesas e receitas por imóvel"""
    __tablename__ = 'admin_lancamento'
    
    id = db.Column(db.Integer, primary_key=True)
    imovel_id = db.Column(db.Integer, db.ForeignKey('admin_imovel.id'), nullable=False)
    categoria_id = db.Column(db.Integer, db.ForeignKey('admin_categoria.id'), nullable=False)
    
    # Dados do lançamento
    descricao = db.Column(db.String(300), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # despesa, receita
    
    # Datas
    data_lancamento = db.Column(db.Date, nullable=False, default=date.today)
    data_vencimento = db.Column(db.Date, nullable=True)
    data_pagamento = db.Column(db.Date, nullable=True)
    
    # Status
    status = db.Column(db.String(20), default='pendente')  # pendente, pago, cancelado
    
    # Recorrência (para lançamentos mensais como aluguel, condomínio)
    recorrente = db.Column(db.Boolean, default=False)
    recorrencia_meses = db.Column(db.Integer, default=1)  # A cada X meses
    
    # Metadados
    observacoes = db.Column(db.Text)
    comprovante_url = db.Column(db.String(500))  # URL do comprovante (se houver)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'imovel_id': self.imovel_id,
            'imovel_nome': self.imovel.nome if self.imovel else None,
            'categoria_id': self.categoria_id,
            'categoria_nome': self.categoria.nome if self.categoria else None,
            'categoria_icone': self.categoria.icone if self.categoria else '💰',
            'descricao': self.descricao,
            'valor': self.valor,
            'tipo': self.tipo,
            'data_lancamento': self.data_lancamento.isoformat() if self.data_lancamento else None,
            'data_vencimento': self.data_vencimento.isoformat() if self.data_vencimento else None,
            'data_pagamento': self.data_pagamento.isoformat() if self.data_pagamento else None,
            'status': self.status,
            'recorrente': self.recorrente,
            'recorrencia_meses': self.recorrencia_meses,
            'observacoes': self.observacoes,
            'comprovante_url': self.comprovante_url,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ===================================================================================
# FUNÇÕES AUXILIARES
# ===================================================================================

def get_current_user():
    """Retorna o usuário atual baseado no token JWT"""
    try:
        user_id = get_jwt_identity()
        if user_id:
            return Usuario.query.get(int(user_id))
    except:
        pass
    return None


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
    print("--- [ADMIN] Categorias padrão criadas/verificadas ---")


# ===================================================================================
# ROTAS - SISTEMA
# ===================================================================================

@app.route('/', methods=['GET'])
def index():
    """Rota raiz - health check"""
    return jsonify({
        'status': 'online',
        'modulo': 'Obraly Admin - Gestão Patrimonial',
        'versao': '1.0.0',
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/health', methods=['GET'])
def health():
    """Health check para monitoramento"""
    return jsonify({'status': 'healthy', 'module': 'admin'})


@app.route('/init-db', methods=['GET', 'POST'])
def init_db():
    """Inicializa o banco de dados e cria categorias padrão"""
    try:
        db.create_all()
        criar_categorias_padrao()
        
        # Criar usuário admin padrão se não existir
        admin = Usuario.query.filter_by(username='admin').first()
        if not admin:
            admin = Usuario(
                username='admin',
                nome='Administrador',
                email='admin@obraly.uk',
                role='admin'
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("--- [ADMIN] Usuário admin criado (senha: admin123) ---")
        
        return jsonify({
            'status': 'success',
            'message': 'Banco de dados inicializado com sucesso',
            'categorias': Categoria.query.count(),
            'usuarios': Usuario.query.count()
        })
    except Exception as e:
        print(f"[ADMIN] Erro ao inicializar DB: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ===================================================================================
# ROTAS - AUTENTICAÇÃO
# ===================================================================================

@app.route('/login', methods=['POST', 'OPTIONS'])
def login():
    """Login de usuário"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({}), 200)
    
    try:
        dados = request.get_json()
        username = dados.get('username', '').strip()
        password = dados.get('password', '')
        
        if not username or not password:
            return jsonify({'erro': 'Usuário e senha são obrigatórios'}), 400
        
        usuario = Usuario.query.filter_by(username=username).first()
        
        if not usuario or not usuario.check_password(password):
            return jsonify({'erro': 'Usuário ou senha inválidos'}), 401
        
        if not usuario.ativo:
            return jsonify({'erro': 'Usuário inativo'}), 403
        
        # Gerar token
        access_token = create_access_token(identity=str(usuario.id))
        
        print(f"--- [ADMIN] Login: {username} ---")
        
        return jsonify({
            'access_token': access_token,
            'user': usuario.to_dict()
        })
        
    except Exception as e:
        print(f"[ADMIN] Erro no login: {e}")
        traceback.print_exc()
        return jsonify({'erro': 'Erro interno no servidor'}), 500


@app.route('/register', methods=['POST', 'OPTIONS'])
def register():
    """Registro de novo usuário (apenas admin pode registrar)"""
    if request.method == 'OPTIONS':
        return make_response(jsonify({}), 200)
    
    try:
        dados = request.get_json()
        
        username = dados.get('username', '').strip()
        password = dados.get('password', '')
        nome = dados.get('nome', '').strip()
        email = dados.get('email', '').strip() or None
        
        if not username or not password or not nome:
            return jsonify({'erro': 'Username, senha e nome são obrigatórios'}), 400
        
        if len(password) < 6:
            return jsonify({'erro': 'Senha deve ter pelo menos 6 caracteres'}), 400
        
        # Verificar se já existe
        if Usuario.query.filter_by(username=username).first():
            return jsonify({'erro': 'Username já está em uso'}), 400
        
        if email and Usuario.query.filter_by(email=email).first():
            return jsonify({'erro': 'Email já está em uso'}), 400
        
        # Criar usuário
        usuario = Usuario(
            username=username,
            nome=nome,
            email=email,
            role='operador'
        )
        usuario.set_password(password)
        
        db.session.add(usuario)
        db.session.commit()
        
        print(f"--- [ADMIN] Novo usuário registrado: {username} ---")
        
        return jsonify({
            'message': 'Usuário criado com sucesso',
            'user': usuario.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"[ADMIN] Erro no registro: {e}")
        traceback.print_exc()
        return jsonify({'erro': 'Erro interno no servidor'}), 500


# ===================================================================================
# ROTAS - GERENCIAMENTO DE USUÁRIOS (Admin Only)
# ===================================================================================

@app.route('/usuarios', methods=['GET'])
@jwt_required()
def listar_usuarios():
    """Lista todos os usuários (apenas admin)"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    
    usuarios = Usuario.query.filter_by(ativo=True).order_by(Usuario.nome).all()
    return jsonify([u.to_dict() for u in usuarios])


@app.route('/usuarios', methods=['POST'])
@jwt_required()
def criar_usuario():
    """Cria um novo usuário (apenas admin)"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    
    try:
        dados = request.get_json()
        
        username = dados.get('username', '').strip()
        password = dados.get('password', '')
        nome = dados.get('nome', '').strip()
        email = dados.get('email', '').strip() or None
        role = dados.get('role', 'operador')
        
        if not username or not password or not nome:
            return jsonify({'erro': 'Username, senha e nome são obrigatórios'}), 400
        
        if len(password) < 6:
            return jsonify({'erro': 'Senha deve ter pelo menos 6 caracteres'}), 400
        
        if role not in ['admin', 'operador']:
            return jsonify({'erro': 'Role inválido. Use: admin ou operador'}), 400
        
        if Usuario.query.filter_by(username=username).first():
            return jsonify({'erro': 'Username já está em uso'}), 400
        
        if email and Usuario.query.filter_by(email=email).first():
            return jsonify({'erro': 'Email já está em uso'}), 400
        
        usuario = Usuario(
            username=username,
            nome=nome,
            email=email,
            role=role
        )
        usuario.set_password(password)
        
        db.session.add(usuario)
        db.session.commit()
        
        print(f"--- [ADMIN] Usuário criado por {user.username}: {username} ({role}) ---")
        
        return jsonify({
            'message': 'Usuário criado com sucesso',
            'user': usuario.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"[ADMIN] Erro ao criar usuário: {e}")
        return jsonify({'erro': str(e)}), 500


@app.route('/usuarios/<int:usuario_id>', methods=['PUT'])
@jwt_required()
def atualizar_usuario(usuario_id):
    """Atualiza um usuário (apenas admin)"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    
    usuario = Usuario.query.get_or_404(usuario_id)
    
    try:
        dados = request.get_json()
        
        if dados.get('nome'):
            usuario.nome = dados['nome'].strip()
        
        if dados.get('email'):
            # Verificar se email já existe em outro usuário
            existing = Usuario.query.filter(Usuario.email == dados['email'], Usuario.id != usuario_id).first()
            if existing:
                return jsonify({'erro': 'Email já está em uso'}), 400
            usuario.email = dados['email'].strip()
        
        if dados.get('role') and dados['role'] in ['admin', 'operador']:
            usuario.role = dados['role']
        
        if dados.get('password') and len(dados['password']) >= 6:
            usuario.set_password(dados['password'])
        
        if 'ativo' in dados:
            usuario.ativo = dados['ativo']
        
        db.session.commit()
        
        return jsonify({
            'message': 'Usuário atualizado com sucesso',
            'user': usuario.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


@app.route('/usuarios/<int:usuario_id>', methods=['DELETE'])
@jwt_required()
def deletar_usuario(usuario_id):
    """Desativa um usuário (apenas admin)"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    
    if user.id == usuario_id:
        return jsonify({'erro': 'Você não pode desativar seu próprio usuário'}), 400
    
    usuario = Usuario.query.get_or_404(usuario_id)
    
    try:
        usuario.ativo = False
        db.session.commit()
        
        print(f"--- [ADMIN] Usuário desativado: {usuario.username} ---")
        
        return jsonify({'message': 'Usuário desativado com sucesso'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


@app.route('/usuarios/<int:usuario_id>/reset-senha', methods=['POST'])
@jwt_required()
def reset_senha_usuario(usuario_id):
    """Reseta a senha de um usuário (apenas admin)"""
    user = get_current_user()
    if not user or user.role != 'admin':
        return jsonify({'erro': 'Acesso negado. Apenas administradores.'}), 403
    
    usuario = Usuario.query.get_or_404(usuario_id)
    
    try:
        dados = request.get_json()
        nova_senha = dados.get('nova_senha', '')
        
        if len(nova_senha) < 6:
            return jsonify({'erro': 'Nova senha deve ter pelo menos 6 caracteres'}), 400
        
        usuario.set_password(nova_senha)
        db.session.commit()
        
        print(f"--- [ADMIN] Senha resetada para usuário: {usuario.username} ---")
        
        return jsonify({'message': 'Senha alterada com sucesso'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


@app.route('/me', methods=['GET'])
@jwt_required()
def get_me():
    """Retorna dados do usuário logado"""
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Usuário não encontrado'}), 404
    return jsonify(user.to_dict())


# ===================================================================================
# ROTAS - CATEGORIAS
# ===================================================================================

@app.route('/categorias', methods=['GET'])
@jwt_required()
def listar_categorias():
    """Lista todas as categorias"""
    tipo = request.args.get('tipo')  # despesa, receita ou None (todas)
    
    query = Categoria.query.filter_by(ativo=True)
    if tipo:
        query = query.filter_by(tipo=tipo)
    
    categorias = query.order_by(Categoria.tipo, Categoria.ordem).all()
    return jsonify([c.to_dict() for c in categorias])


# ===================================================================================
# ROTAS - IMÓVEIS
# ===================================================================================

@app.route('/imoveis', methods=['GET'])
@jwt_required()
def listar_imoveis():
    """Lista todos os imóveis do usuário"""
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    
    # Admin vê todos, operador vê apenas os seus
    if user.role == 'admin':
        imoveis = Imovel.query.filter_by(ativo=True).order_by(Imovel.nome).all()
    else:
        imoveis = Imovel.query.filter_by(usuario_id=user.id, ativo=True).order_by(Imovel.nome).all()
    
    # Calcular totais para cada imóvel
    resultado = []
    for imovel in imoveis:
        imovel_dict = imovel.to_dict()
        
        # Calcular total de despesas e receitas
        despesas = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id == imovel.id,
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado'
        ).scalar() or 0
        
        receitas = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id == imovel.id,
            Lancamento.tipo == 'receita',
            Lancamento.status != 'cancelado'
        ).scalar() or 0
        
        imovel_dict['total_despesas'] = float(despesas)
        imovel_dict['total_receitas'] = float(receitas)
        imovel_dict['saldo'] = float(receitas - despesas)
        
        resultado.append(imovel_dict)
    
    return jsonify(resultado)


@app.route('/imoveis', methods=['POST'])
@jwt_required()
def criar_imovel():
    """Cria um novo imóvel"""
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    
    try:
        dados = request.get_json()
        
        imovel = Imovel(
            usuario_id=user.id,
            nome=dados.get('nome'),
            tipo=dados.get('tipo', 'apartamento'),
            endereco=dados.get('endereco'),
            cidade=dados.get('cidade'),
            estado=dados.get('estado'),
            cep=dados.get('cep'),
            status=dados.get('status', 'proprio'),
            valor_aluguel=float(dados.get('valor_aluguel', 0)),
            valor_mercado=float(dados.get('valor_mercado', 0)),
            custo_construcao=float(dados.get('custo_construcao', 0)),
            observacoes=dados.get('observacoes')
        )
        
        db.session.add(imovel)
        db.session.commit()
        
        print(f"--- [ADMIN] Imóvel criado: {imovel.nome} (user: {user.username}) ---")
        
        return jsonify(imovel.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"[ADMIN] Erro ao criar imóvel: {e}")
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


@app.route('/imoveis/<int:imovel_id>', methods=['GET'])
@jwt_required()
def obter_imovel(imovel_id):
    """Obtém detalhes de um imóvel"""
    user = get_current_user()
    imovel = Imovel.query.get_or_404(imovel_id)
    
    # Verificar permissão
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    return jsonify(imovel.to_dict())


@app.route('/imoveis/<int:imovel_id>', methods=['PUT'])
@jwt_required()
def atualizar_imovel(imovel_id):
    """Atualiza um imóvel"""
    user = get_current_user()
    imovel = Imovel.query.get_or_404(imovel_id)
    
    # Verificar permissão
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    try:
        dados = request.get_json()
        
        imovel.nome = dados.get('nome', imovel.nome)
        imovel.tipo = dados.get('tipo', imovel.tipo)
        imovel.endereco = dados.get('endereco', imovel.endereco)
        imovel.cidade = dados.get('cidade', imovel.cidade)
        imovel.estado = dados.get('estado', imovel.estado)
        imovel.cep = dados.get('cep', imovel.cep)
        imovel.status = dados.get('status', imovel.status)
        imovel.valor_aluguel = float(dados.get('valor_aluguel', imovel.valor_aluguel))
        imovel.valor_mercado = float(dados.get('valor_mercado', imovel.valor_mercado))
        imovel.custo_construcao = float(dados.get('custo_construcao', imovel.custo_construcao))
        imovel.observacoes = dados.get('observacoes', imovel.observacoes)
        
        db.session.commit()
        
        print(f"--- [ADMIN] Imóvel atualizado: {imovel.nome} ---")
        
        return jsonify(imovel.to_dict())
        
    except Exception as e:
        db.session.rollback()
        print(f"[ADMIN] Erro ao atualizar imóvel: {e}")
        return jsonify({'erro': str(e)}), 500


@app.route('/imoveis/<int:imovel_id>', methods=['DELETE'])
@jwt_required()
def deletar_imovel(imovel_id):
    """Deleta (desativa) um imóvel"""
    user = get_current_user()
    imovel = Imovel.query.get_or_404(imovel_id)
    
    # Verificar permissão
    if user.role != 'admin' and imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    try:
        imovel.ativo = False
        db.session.commit()
        
        print(f"--- [ADMIN] Imóvel desativado: {imovel.nome} ---")
        
        return jsonify({'message': 'Imóvel removido com sucesso'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


# ===================================================================================
# ROTAS - LANÇAMENTOS
# ===================================================================================

@app.route('/lancamentos', methods=['GET'])
@jwt_required()
def listar_lancamentos():
    """Lista lançamentos com filtros"""
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    
    # Filtros
    imovel_id = request.args.get('imovel_id', type=int)
    tipo = request.args.get('tipo')  # despesa, receita
    status = request.args.get('status')  # pendente, pago, cancelado
    mes = request.args.get('mes', type=int)  # 1-12
    ano = request.args.get('ano', type=int)  # 2024, 2025...
    
    # Query base
    query = Lancamento.query.join(Imovel)
    
    # Admin vê todos, operador vê apenas os seus
    if user.role != 'admin':
        query = query.filter(Imovel.usuario_id == user.id)
    
    # Aplicar filtros
    if imovel_id:
        query = query.filter(Lancamento.imovel_id == imovel_id)
    if tipo:
        query = query.filter(Lancamento.tipo == tipo)
    if status:
        query = query.filter(Lancamento.status == status)
    if mes:
        query = query.filter(extract('month', Lancamento.data_lancamento) == mes)
    if ano:
        query = query.filter(extract('year', Lancamento.data_lancamento) == ano)
    
    lancamentos = query.order_by(Lancamento.data_lancamento.desc()).all()
    
    return jsonify([l.to_dict() for l in lancamentos])


@app.route('/lancamentos', methods=['POST'])
@jwt_required()
def criar_lancamento():
    """Cria um novo lançamento"""
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    
    try:
        dados = request.get_json()
        
        # Verificar se o imóvel pertence ao usuário
        imovel = Imovel.query.get(dados.get('imovel_id'))
        if not imovel:
            return jsonify({'erro': 'Imóvel não encontrado'}), 404
        if user.role != 'admin' and imovel.usuario_id != user.id:
            return jsonify({'erro': 'Acesso negado ao imóvel'}), 403
        
        # Dados do lançamento
        data_lanc = date.fromisoformat(dados.get('data_lancamento', date.today().isoformat()))
        data_venc = date.fromisoformat(dados['data_vencimento']) if dados.get('data_vencimento') else None
        recorrente = dados.get('recorrente', False)
        recorrencia_meses = int(dados.get('recorrencia_meses', 1))
        qtd_parcelas = int(dados.get('qtd_parcelas', 1)) if recorrente else 1
        
        lancamentos_criados = []
        
        # Criar lançamento(s)
        for i in range(qtd_parcelas):
            # Calcular datas para cada parcela
            if i > 0:
                # Adicionar meses para parcelas subsequentes
                mes_offset = i * recorrencia_meses
                ano_offset = mes_offset // 12
                mes_novo = data_lanc.month + (mes_offset % 12)
                if mes_novo > 12:
                    mes_novo -= 12
                    ano_offset += 1
                try:
                    data_lanc_parcela = data_lanc.replace(
                        year=data_lanc.year + ano_offset,
                        month=mes_novo
                    )
                except ValueError:
                    # Último dia do mês se o dia não existir
                    import calendar
                    ultimo_dia = calendar.monthrange(data_lanc.year + ano_offset, mes_novo)[1]
                    data_lanc_parcela = data_lanc.replace(
                        year=data_lanc.year + ano_offset,
                        month=mes_novo,
                        day=min(data_lanc.day, ultimo_dia)
                    )
                
                # Mesma lógica para data de vencimento
                if data_venc:
                    try:
                        data_venc_parcela = data_venc.replace(
                            year=data_venc.year + ano_offset,
                            month=mes_novo
                        )
                    except ValueError:
                        import calendar
                        ultimo_dia = calendar.monthrange(data_venc.year + ano_offset, mes_novo)[1]
                        data_venc_parcela = data_venc.replace(
                            year=data_venc.year + ano_offset,
                            month=mes_novo,
                            day=min(data_venc.day, ultimo_dia)
                        )
                else:
                    data_venc_parcela = None
            else:
                data_lanc_parcela = data_lanc
                data_venc_parcela = data_venc
            
            # Descrição com número da parcela se recorrente
            descricao = dados.get('descricao')
            if recorrente and qtd_parcelas > 1:
                descricao = f"{descricao} ({i+1}/{qtd_parcelas})"
            
            lancamento = Lancamento(
                imovel_id=dados.get('imovel_id'),
                categoria_id=dados.get('categoria_id'),
                descricao=descricao,
                valor=float(dados.get('valor', 0)),
                tipo=dados.get('tipo', 'despesa'),
                data_lancamento=data_lanc_parcela,
                data_vencimento=data_venc_parcela,
                data_pagamento=date.fromisoformat(dados['data_pagamento']) if dados.get('data_pagamento') and i == 0 else None,
                status=dados.get('status', 'pendente') if i == 0 else 'pendente',
                recorrente=recorrente,
                recorrencia_meses=recorrencia_meses,
                observacoes=dados.get('observacoes')
            )
            
            db.session.add(lancamento)
            lancamentos_criados.append(lancamento)
        
        db.session.commit()
        
        print(f"--- [ADMIN] {len(lancamentos_criados)} lançamento(s) criado(s): {dados.get('descricao')} ---")
        
        if len(lancamentos_criados) == 1:
            return jsonify(lancamentos_criados[0].to_dict()), 201
        else:
            return jsonify({
                'message': f'{len(lancamentos_criados)} lançamentos criados',
                'lancamentos': [l.to_dict() for l in lancamentos_criados]
            }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"[ADMIN] Erro ao criar lançamento: {e}")
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


@app.route('/alertas-vencimento', methods=['GET'])
@jwt_required()
def alertas_vencimento():
    """Retorna lançamentos próximos do vencimento ou vencidos"""
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    
    try:
        dias_alerta = request.args.get('dias', type=int, default=7)  # Alertar X dias antes
        
        hoje = date.today()
        data_limite = hoje + timedelta(days=dias_alerta)
        
        # Query base de imóveis do usuário
        if user.role == 'admin':
            imoveis_ids = [i.id for i in Imovel.query.filter_by(ativo=True).all()]
        else:
            imoveis_ids = [i.id for i in Imovel.query.filter_by(usuario_id=user.id, ativo=True).all()]
        
        # Buscar lançamentos pendentes com vencimento
        lancamentos = Lancamento.query.filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.status == 'pendente',
            Lancamento.data_vencimento.isnot(None),
            Lancamento.data_vencimento <= data_limite
        ).order_by(Lancamento.data_vencimento.asc()).all()
        
        # Separar em vencidos e a vencer
        vencidos = []
        a_vencer = []
        
        for lanc in lancamentos:
            lanc_dict = lanc.to_dict()
            dias_para_vencer = (lanc.data_vencimento - hoje).days
            lanc_dict['dias_para_vencer'] = dias_para_vencer
            
            if dias_para_vencer < 0:
                lanc_dict['status_alerta'] = 'vencido'
                lanc_dict['dias_vencido'] = abs(dias_para_vencer)
                vencidos.append(lanc_dict)
            else:
                lanc_dict['status_alerta'] = 'a_vencer'
                a_vencer.append(lanc_dict)
        
        # Totais
        total_vencido = sum(l['valor'] for l in vencidos)
        total_a_vencer = sum(l['valor'] for l in a_vencer)
        
        return jsonify({
            'vencidos': vencidos,
            'a_vencer': a_vencer,
            'resumo': {
                'qtd_vencidos': len(vencidos),
                'qtd_a_vencer': len(a_vencer),
                'total_vencido': total_vencido,
                'total_a_vencer': total_a_vencer,
                'total_geral': total_vencido + total_a_vencer
            }
        })
        
    except Exception as e:
        print(f"[ADMIN] Erro ao buscar alertas: {e}")
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


@app.route('/lancamentos/<int:lancamento_id>', methods=['PUT'])
@jwt_required()
def atualizar_lancamento(lancamento_id):
    """Atualiza um lançamento"""
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    
    # Verificar permissão
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    try:
        dados = request.get_json()
        
        lancamento.categoria_id = dados.get('categoria_id', lancamento.categoria_id)
        lancamento.descricao = dados.get('descricao', lancamento.descricao)
        lancamento.valor = float(dados.get('valor', lancamento.valor))
        lancamento.tipo = dados.get('tipo', lancamento.tipo)
        lancamento.status = dados.get('status', lancamento.status)
        lancamento.observacoes = dados.get('observacoes', lancamento.observacoes)
        
        if dados.get('data_lancamento'):
            lancamento.data_lancamento = date.fromisoformat(dados['data_lancamento'])
        if dados.get('data_vencimento'):
            lancamento.data_vencimento = date.fromisoformat(dados['data_vencimento'])
        if dados.get('data_pagamento'):
            lancamento.data_pagamento = date.fromisoformat(dados['data_pagamento'])
        
        db.session.commit()
        
        return jsonify(lancamento.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE'])
@jwt_required()
def deletar_lancamento(lancamento_id):
    """Deleta um lançamento"""
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    
    # Verificar permissão
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    try:
        db.session.delete(lancamento)
        db.session.commit()
        
        return jsonify({'message': 'Lançamento removido com sucesso'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


@app.route('/lancamentos/<int:lancamento_id>/pagar', methods=['POST'])
@jwt_required()
def marcar_pago(lancamento_id):
    """Marca um lançamento como pago, opcionalmente com comprovante"""
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    
    # Verificar permissão
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    try:
        dados = request.get_json() or {}
        
        lancamento.status = 'pago'
        lancamento.data_pagamento = date.fromisoformat(dados.get('data_pagamento', date.today().isoformat()))
        
        # Se veio URL do comprovante, salvar
        if dados.get('comprovante_url'):
            lancamento.comprovante_url = dados.get('comprovante_url')
        
        db.session.commit()
        
        return jsonify(lancamento.to_dict())
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


@app.route('/lancamentos/<int:lancamento_id>/comprovante', methods=['POST'])
@jwt_required()
def upload_comprovante(lancamento_id):
    """Upload de comprovante de pagamento (base64)"""
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    
    # Verificar permissão
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    try:
        dados = request.get_json()
        
        if not dados.get('comprovante_base64'):
            return jsonify({'erro': 'Comprovante não enviado'}), 400
        
        # Salvar como data URL (base64) - em produção seria melhor usar S3/Cloudinary
        # Formato: data:image/jpeg;base64,/9j/4AAQ...
        comprovante_base64 = dados.get('comprovante_base64')
        
        # Validar tamanho (máximo ~5MB em base64)
        if len(comprovante_base64) > 7000000:
            return jsonify({'erro': 'Arquivo muito grande. Máximo 5MB.'}), 400
        
        lancamento.comprovante_url = comprovante_base64
        db.session.commit()
        
        print(f"--- [ADMIN] Comprovante salvo para lançamento {lancamento_id} ---")
        
        return jsonify({
            'message': 'Comprovante salvo com sucesso',
            'lancamento': lancamento.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"[ADMIN] Erro ao salvar comprovante: {e}")
        return jsonify({'erro': str(e)}), 500


@app.route('/lancamentos/<int:lancamento_id>/comprovante', methods=['DELETE'])
@jwt_required()
def remover_comprovante(lancamento_id):
    """Remove o comprovante de um lançamento"""
    user = get_current_user()
    lancamento = Lancamento.query.get_or_404(lancamento_id)
    
    # Verificar permissão
    if user.role != 'admin' and lancamento.imovel.usuario_id != user.id:
        return jsonify({'erro': 'Acesso negado'}), 403
    
    try:
        lancamento.comprovante_url = None
        db.session.commit()
        
        return jsonify({'message': 'Comprovante removido com sucesso'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'erro': str(e)}), 500


# ===================================================================================
# ROTAS - DASHBOARD
# ===================================================================================

@app.route('/dashboard', methods=['GET'])
@jwt_required()
def dashboard():
    """Retorna dados consolidados para o dashboard"""
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    
    try:
        # Filtro por período (default: mês atual)
        mes = request.args.get('mes', type=int, default=date.today().month)
        ano = request.args.get('ano', type=int, default=date.today().year)
        
        # Query base de imóveis
        if user.role == 'admin':
            imoveis_ids = [i.id for i in Imovel.query.filter_by(ativo=True).all()]
        else:
            imoveis_ids = [i.id for i in Imovel.query.filter_by(usuario_id=user.id, ativo=True).all()]
        
        # Total de imóveis
        total_imoveis = len(imoveis_ids)
        
        # Despesas do mês
        despesas_mes = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).scalar() or 0
        
        # Receitas do mês
        receitas_mes = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'receita',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).scalar() or 0
        
        # Pendentes (a vencer)
        pendentes = db.session.query(func.sum(Lancamento.valor)).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.status == 'pendente',
            Lancamento.tipo == 'despesa'
        ).scalar() or 0
        
        # Despesas por categoria (do mês)
        despesas_por_categoria = db.session.query(
            Categoria.nome,
            Categoria.icone,
            Categoria.cor,
            func.sum(Lancamento.valor).label('total')
        ).join(Lancamento).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).group_by(Categoria.id).order_by(func.sum(Lancamento.valor).desc()).all()
        
        # Despesas por imóvel (do mês)
        despesas_por_imovel = db.session.query(
            Imovel.nome,
            func.sum(Lancamento.valor).label('total')
        ).join(Lancamento).filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.tipo == 'despesa',
            Lancamento.status != 'cancelado',
            extract('month', Lancamento.data_lancamento) == mes,
            extract('year', Lancamento.data_lancamento) == ano
        ).group_by(Imovel.id).order_by(func.sum(Lancamento.valor).desc()).all()
        
        # Últimos lançamentos
        ultimos_lancamentos = Lancamento.query.filter(
            Lancamento.imovel_id.in_(imoveis_ids)
        ).order_by(Lancamento.created_at.desc()).limit(10).all()
        
        # Alertas de vencimento (próximos 7 dias + vencidos)
        hoje = date.today()
        data_limite = hoje + timedelta(days=7)
        
        lancamentos_alerta = Lancamento.query.filter(
            Lancamento.imovel_id.in_(imoveis_ids),
            Lancamento.status == 'pendente',
            Lancamento.data_vencimento.isnot(None),
            Lancamento.data_vencimento <= data_limite
        ).order_by(Lancamento.data_vencimento.asc()).all()
        
        alertas_vencidos = []
        alertas_a_vencer = []
        
        for lanc in lancamentos_alerta:
            lanc_dict = lanc.to_dict()
            dias = (lanc.data_vencimento - hoje).days
            lanc_dict['dias_para_vencer'] = dias
            
            if dias < 0:
                lanc_dict['status_alerta'] = 'vencido'
                alertas_vencidos.append(lanc_dict)
            else:
                lanc_dict['status_alerta'] = 'a_vencer'
                alertas_a_vencer.append(lanc_dict)
        
        return jsonify({
            'periodo': {'mes': mes, 'ano': ano},
            'resumo': {
                'total_imoveis': total_imoveis,
                'despesas_mes': float(despesas_mes),
                'receitas_mes': float(receitas_mes),
                'saldo_mes': float(receitas_mes - despesas_mes),
                'pendentes': float(pendentes)
            },
            'alertas': {
                'vencidos': alertas_vencidos,
                'a_vencer': alertas_a_vencer,
                'total_vencido': sum(l['valor'] for l in alertas_vencidos),
                'total_a_vencer': sum(l['valor'] for l in alertas_a_vencer)
            },
            'despesas_por_categoria': [
                {'nome': d.nome, 'icone': d.icone, 'cor': d.cor, 'total': float(d.total)}
                for d in despesas_por_categoria
            ],
            'despesas_por_imovel': [
                {'nome': d.nome, 'total': float(d.total)}
                for d in despesas_por_imovel
            ],
            'ultimos_lancamentos': [l.to_dict() for l in ultimos_lancamentos]
        })
        
    except Exception as e:
        print(f"[ADMIN] Erro no dashboard: {e}")
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


# ===================================================================================
# ROTAS - INTEGRAÇÃO COM OBRALY (Obras)
# ===================================================================================

@app.route('/importar-obra', methods=['POST'])
@jwt_required()
def importar_obra():
    """
    Importa uma obra finalizada do módulo Obraly como um novo imóvel.
    Recebe os dados da obra e cria um imóvel com o custo de construção.
    """
    user = get_current_user()
    if not user:
        return jsonify({'erro': 'Não autorizado'}), 401
    
    try:
        dados = request.get_json()
        
        # Verificar se já foi importado
        obra_id = dados.get('obra_id')
        if obra_id:
            existente = Imovel.query.filter_by(obra_id_origem=obra_id).first()
            if existente:
                return jsonify({
                    'erro': 'Esta obra já foi importada',
                    'imovel_id': existente.id,
                    'imovel_nome': existente.nome
                }), 400
        
        # Criar imóvel a partir dos dados da obra
        imovel = Imovel(
            usuario_id=user.id,
            nome=dados.get('nome', 'Imóvel importado'),
            tipo=dados.get('tipo', 'apartamento'),
            endereco=dados.get('endereco'),
            cidade=dados.get('cidade'),
            estado=dados.get('estado'),
            cep=dados.get('cep'),
            status='proprio',  # Obra finalizada = imóvel próprio
            valor_mercado=float(dados.get('valor_mercado', 0)),
            obra_id_origem=obra_id,
            custo_construcao=float(dados.get('custo_total', 0)),
            observacoes=f"Importado do módulo Obras em {datetime.now().strftime('%d/%m/%Y')}"
        )
        
        db.session.add(imovel)
        db.session.commit()
        
        print(f"--- [ADMIN] Obra importada como imóvel: {imovel.nome} (obra_id: {obra_id}) ---")
        
        return jsonify({
            'message': 'Obra importada com sucesso',
            'imovel': imovel.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"[ADMIN] Erro ao importar obra: {e}")
        traceback.print_exc()
        return jsonify({'erro': str(e)}), 500


# ===================================================================================
# INICIALIZAÇÃO
# ===================================================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        criar_categorias_padrao()
        print("--- [ADMIN] Tabelas criadas ---")
    
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
