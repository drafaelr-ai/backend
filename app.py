# Forçando novo deploy com correções 24/10
import os
import traceback  # Importado para log de erros detalhado
import re  # Importado para o CORS com regex
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from urllib.parse import quote_plus
import datetime
from sqlalchemy import func
import io
import csv
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

print("--- [LOG] Iniciando app.py ---")

app = Flask(__name__)

# --- CONFIGURAÇÃO DE CORS (Cross-Origin Resource Sharing) ---
# Implementando a sugestão de regex (image_3fb581.png) para aceitar previews do Vercel
prod_url = os.environ.get('FRONTEND_URL', "").strip()  # URL de produção principal (opcional)
allowed_origins = [
    re.compile(r"https://.*-ais-projects\.vercel\.app$"),  # Regex para todos os previews
    "http://localhost:3000"  # Desenvolvimento local
]
if prod_url:
    allowed_origins.append(prod_url)

CORS(app, resources={r"/*": {"origins": allowed_origins}}, supports_credentials=True)
print(f"--- [LOG] CORS configurado com regex e {len(allowed_origins)} padrões ---")


# --- CONFIGURAÇÃO DA CONEXÃO (COM VARIÁVEIS DE AMBIENTE) ---
DB_USER = "postgres.kwmuiviyqjcxawuiqkrl"
DB_HOST = "aws-1-sa-east-1.pooler.supabase.com"
DB_PORT = "5432"
DB_NAME = "postgres"

print("--- [LOG] Lendo variável de ambiente DB_PASSWORD... ---")
DB_PASSWORD = os.environ.get('DB_PASSWORD')

if not DB_PASSWORD:
    print("--- [ERRO CRÍTICO] Variável de ambiente DB_PASSWORD não foi encontrada! ---")
    raise ValueError("Variável de ambiente DB_PASSWORD não definida.")
else:
    print("--- [LOG] Variável DB_PASSWORD carregada com sucesso. ---")

encoded_password = quote_plus(DB_PASSWORD)

# Implementando a sugestão de SSL (image_3fb5ba.png)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode=require"

print(f"--- [LOG] String de conexão criada para usuário {DB_USER} (com sslmode=require) ---")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
    'pool_timeout': 30,
    'pool_size': 5,
    'max_overflow': 10
}

db = SQLAlchemy(app)
print("--- [LOG] SQLAlchemy inicializado ---")

# --- MODELOS DO BANCO DE DADOS ---
class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))
    lancamentos = db.relationship('Lancamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    empreitadas = db.relationship('Empreitada', backref='obra', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "cliente": self.cliente
        }

class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    pix = db.Column(db.String(100))

    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "tipo": self.tipo,
            "descricao": self.descricao,
            "valor": self.valor,
            "data": self.data.isoformat() if self.data else None, # Trata data nula
            "status": self.status,
            "pix": self.pix
        }

class Empreitada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    responsavel = db.Column(db.String(150))
    valor_global = db.Column(db.Float, nullable=False)
    pix = db.Column(db.String(100))
    pagamentos = db.relationship('PagamentoEmpreitada', backref='empreitada', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        # Garante que pagamentos é sempre uma lista
        pagamentos_list = self.pagamentos if self.pagamentos else []
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "nome": self.nome,
            "responsavel": self.responsavel,
            "valor_global": self.valor_global,
            "pix": self.pix,
            "pagamentos": [p.to_dict() for p in pagamentos_list]
        }


class PagamentoEmpreitada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empreitada_id = db.Column(db.Integer, db.ForeignKey('empreitada.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pago') # Default pode ser 'Pago' ou 'A Pagar' dependendo da regra

    def to_dict(self):
        return {
            "id": self.id,
            "empreitada_id": self.empreitada_id, # Adicionado para referência
            "data": self.data.isoformat() if self.data else None, # Trata data nula
            "valor": self.valor,
            "status": self.status
        }

# --- FUNÇÃO AUXILIAR PARA FORMATAÇÃO BRASILEIRA ---
def formatar_real(valor):
    """Formata valor para padrão brasileiro: R$ 9.915,00"""
    # Adiciona verificação se valor é None
    if valor is None:
        valor = 0.0
    try:
        # Garante que é float antes de formatar
        return f"R$ {float(valor):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        print(f"Erro ao formatar valor: {valor}, tipo: {type(valor)}")
        return "R$ 0,00"


# --- ROTAS DA API ---

# --- ROTA DE ADMINISTRAÇÃO (PARA CRIAR TABELAS) ---
@app.route('/admin/create_tables', methods=['GET'])
def create_tables():
    print("--- [LOG] Rota /admin/create_tables (GET) acessada ---")
    try:
        with app.app_context():
            db.create_all()
        print("--- [LOG] db.create_all() executado com sucesso. ---")
        return jsonify({"sucesso": "Tabelas criadas ou já existentes no banco de dados."}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/create_tables: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Falha ao criar tabelas.", "details": error_details}), 500
# ------------------------------------

@app.route('/', methods=['GET'])
def home():
    print("--- [LOG] Rota / (home) acessada ---")
    return jsonify({"message": "Backend rodando com sucesso!", "status": "OK"}), 200

# --- ROTAS DE OBRAS ---
@app.route('/obras', methods=['GET'])
def get_obras():
    print("--- [LOG] Rota /obras (GET) acessada ---")
    try:
        obras = Obra.query.order_by(Obra.nome).all()
        return jsonify([obra.to_dict() for obra in obras])
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao buscar obras", "details": str(e)}), 500


@app.route('/obras', methods=['POST'])
def add_obra():
    print("--- [LOG] Rota /obras (POST) acessada ---")
    try:
        dados = request.json
        if not dados or 'nome' not in dados or not dados['nome']:
             return jsonify({"erro": "Nome da obra é obrigatório."}), 400
        nova_obra = Obra(
            nome=dados['nome'],
            cliente=dados.get('cliente') # Cliente é opcional
        )
        db.session.add(nova_obra)
        db.session.commit()
        return jsonify(nova_obra.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao adicionar obra", "details": str(e)}), 500


@app.route('/obras/<int:obra_id>', methods=['GET'])
def get_obra_detalhes(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id} (GET) acessada ---")
    try:
        obra = db.session.get(Obra, obra_id) # Usando db.session.get para buscar pela PK
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404

        # Sumários de Lançamentos
        sumarios_lancamentos = db.session.query(
            func.sum(Lancamento.valor).label('total_geral_lanc'),
            func.sum(db.case((Lancamento.status == 'Pago', Lancamento.valor), else_=0)).label('total_pago_lanc'),
            func.sum(db.case((Lancamento.status == 'A Pagar', Lancamento.valor), else_=0)).label('total_a_pagar_lanc')
        ).filter(Lancamento.obra_id == obra_id).first()

        # Sumários de Pagamentos de Empreitadas
        sumarios_empreitadas_pag = db.session.query(
            func.sum(PagamentoEmpreitada.valor).label('total_pago_emp')
        ).join(Empreitada).filter(
            Empreitada.obra_id == obra_id,
            PagamentoEmpreitada.status == 'Pago'
        ).first()

        sumarios_empreitadas_a_pagar = db.session.query(
            func.sum(PagamentoEmpreitada.valor).label('total_a_pagar_emp')
        ).join(Empreitada).filter(
            Empreitada.obra_id == obra_id,
            PagamentoEmpreitada.status == 'A Pagar'
        ).first()


        # Valor Global das Empreitadas
        total_empreitadas_global_scalar = db.session.query(
            func.sum(Empreitada.valor_global)
        ).filter(Empreitada.obra_id == obra_id).scalar()

        # Tratamento de valores nulos
        total_lancamentos_geral = sumarios_lancamentos.total_geral_lanc or 0.0
        total_pago_lancamentos = sumarios_lancamentos.total_pago_lanc or 0.0
        total_a_pagar_lancamentos = sumarios_lancamentos.total_a_pagar_lanc or 0.0

        total_pago_empreitadas = sumarios_empreitadas_pag.total_pago_emp or 0.0
        total_a_pagar_empreitadas = sumarios_empreitadas_a_pagar.total_a_pagar_emp or 0.0
        total_empreitadas_global = total_empreitadas_global_scalar or 0.0

        # Cálculo dos totais combinados
        total_pago_geral = total_pago_lancamentos + total_pago_empreitadas
        # Total a pagar = (Total Lancamentos A Pagar) + (Total Pagamentos Empreitada A Pagar)
        total_a_pagar_geral = total_a_pagar_lancamentos + total_a_pagar_empreitadas

        # Total Geral (Considerando apenas Lançamentos + Valor Global das Empreitadas)
        total_geral_calculado = total_lancamentos_geral + total_empreitadas_global

        # Ajuste para garantir que total geral >= total pago + total a pagar
        total_geral_final = max(total_geral_calculado, total_pago_geral + total_a_pagar_geral)


        # Total por Segmento (Apenas Lançamentos)
        total_por_segmento_query = db.session.query(
            Lancamento.tipo,
            func.sum(Lancamento.valor)
        ).filter(Lancamento.obra_id == obra_id).group_by(Lancamento.tipo).all()
        total_por_segmento = {tipo: (valor or 0.0) for tipo, valor in total_por_segmento_query}

        # Total por Mês (Apenas Lançamentos)
        total_por_mes_query = db.session.query(
            func.to_char(Lancamento.data, 'YYYY-MM').label('mes_ano'), # Formato YYYY-MM para ordenação
            func.sum(Lancamento.valor)
        ).filter(Lancamento.obra_id == obra_id).group_by('mes_ano').order_by('mes_ano').all()
        total_por_mes = {mes: (valor or 0.0) for mes, valor in total_por_mes_query}


        sumarios_dict = {
            "total_geral": total_geral_final,
            "total_pago": total_pago_geral,
            "total_a_pagar": total_a_pagar_geral,
            "total_por_segmento": total_por_segmento,
            "total_por_mes": total_por_mes
        }

        # Garante que lancamentos e empreitadas são listas
        lancamentos_list = obra.lancamentos if obra.lancamentos else []
        empreitadas_list = obra.empreitadas if obra.empreitadas else []

        return jsonify({
            "obra": obra.to_dict(),
            "lancamentos": sorted([l.to_dict() for l in lancamentos_list], key=lambda x: x.get('data', '1900-01-01'), reverse=True), # Fallback para data
            "empreitadas": [e.to_dict() for e in empreitadas_list],
            "sumarios": sumarios_dict
        })

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao buscar detalhes da obra", "details": str(e)}), 500


@app.route('/obras/<int:obra_id>', methods=['DELETE'])
def deletar_obra(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id} (DELETE) acessada ---")
    try:
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada"}), 404
        db.session.delete(obra)
        db.session.commit()
        return jsonify({"sucesso": "Obra deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao deletar obra", "details": str(e)}), 500


# --- ROTAS DE LANCAMENTOS ---
@app.route('/obras/<int:obra_id>/lancamentos', methods=['POST'])
def add_lancamento(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id}/lancamentos (POST) acessada ---")
    try:
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada para adicionar lançamento"}), 404

        dados = request.json
        # Validação básica dos dados recebidos
        campos_obrigatorios = ['tipo', 'descricao', 'valor', 'data', 'status']
        # Verifica se 'data' existe e não está vazia
        if not dados or not all(campo in dados and dados[campo] is not None and dados[campo] != '' for campo in campos_obrigatorios):
             return jsonify({"erro": "Campos obrigatórios ausentes ou inválidos para o lançamento."}), 400

        try:
            valor_float = float(dados['valor'])
            # Tenta converter a data, aceita formatos diferentes se necessário, mas ISO é o padrão
            try:
                data_date = datetime.date.fromisoformat(dados['data'])
            except ValueError:
                 # Poderia tentar outros formatos aqui se necessário
                 raise ValueError("Formato de data inválido. Use YYYY-MM-DD.")

            status_valido = dados['status'] in ['Pago', 'A Pagar']
            if not status_valido: raise ValueError("Status inválido")
        except (ValueError, TypeError) as ve:
             print(f"--- [ERRO VALIDAÇÃO] add_lancamento: {str(ve)} ---")
             return jsonify({"erro": f"Dados inválidos: {str(ve)}"}), 400

        novo_lancamento = Lancamento(
            obra_id=obra_id,
            tipo=dados['tipo'],
            descricao=dados['descricao'],
            valor=valor_float,
            data=data_date,
            status=dados['status'],
            pix=dados.get('pix') # PIX é opcional
        )
        db.session.add(novo_lancamento)
        db.session.commit()
        return jsonify(novo_lancamento.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/lancamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno ao adicionar lançamento", "details": str(e)}), 500


@app.route('/lancamentos/<int:lancamento_id>/pago', methods=['PATCH'])
def marcar_como_pago(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id}/pago (PATCH) acessada ---")
    try:
        lancamento = db.session.get(Lancamento, lancamento_id)
        if not lancamento:
            return jsonify({"erro": "Lançamento não encontrado"}), 404
        lancamento.status = 'Pago'
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id}/pago (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao marcar como pago", "details": str(e)}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['PUT'])
def editar_lancamento(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (PUT) acessada ---")
    try:
        lancamento = db.session.get(Lancamento, lancamento_id)
        if not lancamento:
            return jsonify({"erro": "Lançamento não encontrado"}), 404

        dados = request.json
        campos_obrigatorios = ['tipo', 'descricao', 'valor', 'data', 'status']
        if not dados or not all(campo in dados and dados[campo] is not None and dados[campo] != '' for campo in campos_obrigatorios):
             return jsonify({"erro": "Campos obrigatórios ausentes ou inválidos para editar o lançamento."}), 400

        try:
            lancamento.data = datetime.date.fromisoformat(dados['data'])
            lancamento.valor = float(dados['valor'])
            status_valido = dados['status'] in ['Pago', 'A Pagar']
            if not status_valido: raise ValueError("Status inválido")
        except (ValueError, TypeError) as ve:
             print(f"--- [ERRO VALIDAÇÃO] editar_lancamento: {str(ve)} ---")
             return jsonify({"erro": f"Dados inválidos: {str(ve)}"}), 400

        lancamento.descricao = dados['descricao']
        lancamento.tipo = dados['tipo']
        lancamento.status = dados['status']
        lancamento.pix = dados.get('pix') # PIX é opcional

        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno ao editar lançamento", "details": str(e)}), 500


@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE'])
def deletar_lancamento(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (DELETE) acessada ---")
    try:
        lancamento = db.session.get(Lancamento, lancamento_id)
        if not lancamento:
            return jsonify({"erro": "Lançamento não encontrado"}), 404
        db.session.delete(lancamento)
        db.session.commit()
        return jsonify({"sucesso": "Lançamento deletado"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao deletar lançamento", "details": str(e)}), 500

# --- ROTAS DE EMPREITADAS ---
@app.route('/obras/<int:obra_id>/empreitadas', methods=['POST'])
def add_empreitada(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id}/empreitadas (POST) acessada ---")
    try:
        obra = db.session.get(Obra, obra_id)
        if not obra:
            return jsonify({"erro": "Obra não encontrada para adicionar empreitada"}), 404

        dados = request.json
        campos_obrigatorios = ['nome', 'valor_global']
        if not dados or not all(campo in dados and dados[campo] is not None and dados[campo] != '' for campo in campos_obrigatorios):
             return jsonify({"erro": "Campos 'nome' e 'valor_global' são obrigatórios para a empreitada."}), 400


        try:
            valor_global_float = float(dados['valor_global'])
            if valor_global_float < 0:
                 raise ValueError("Valor global não pode ser negativo.")
        except (ValueError, TypeError):
             return jsonify({"erro": "Valor global inválido."}), 400

        nova_empreitada = Empreitada(
            obra_id=obra_id,
            nome=dados['nome'],
            responsavel=dados.get('responsavel'), # Opcional
            valor_global=valor_global_float,
            pix=dados.get('pix') # Opcional
        )
        db.session.add(nova_empreitada)
        db.session.commit()
        # Retorna o objeto completo, incluindo a lista (vazia) de pagamentos
        return jsonify(nova_empreitada.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/empreitadas (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno ao adicionar empreitada", "details": str(e)}), 500


@app.route('/empreitadas/<int:empreitada_id>', methods=['PUT'])
def editar_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id} (PUT) acessada ---")
    try:
        empreitada = db.session.get(Empreitada, empreitada_id)
        if not empreitada:
            return jsonify({"erro": "Empreitada não encontrada"}), 404

        dados = request.json
        if not dados:
             return jsonify({"erro": "Nenhum dado fornecido para atualização."}), 400

        empreitada.nome = dados.get('nome', empreitada.nome) # Mantém o valor se não for fornecido
        try:
            # Atualiza valor global apenas se fornecido e válido
            if 'valor_global' in dados and dados['valor_global'] is not None:
                valor_global_float = float(dados['valor_global'])
                if valor_global_float < 0:
                     raise ValueError("Valor global não pode ser negativo.")
                empreitada.valor_global = valor_global_float
        except (ValueError, TypeError):
             return jsonify({"erro": "Valor global inválido."}), 400

        # Atualiza campos opcionais se presentes nos dados
        if 'responsavel' in dados:
             empreitada.responsavel = dados['responsavel'] # Permite string vazia ou null
        if 'pix' in dados:
             empreitada.pix = dados['pix'] # Permite string vazia ou null

        db.session.commit()
        return jsonify(empreitada.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno ao editar empreitada", "details": str(e)}), 500


@app.route('/empreitadas/<int:empreitada_id>', methods=['DELETE'])
def deletar_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id} (DELETE) acessada ---")
    try:
        empreitada = db.session.get(Empreitada, empreitada_id)
        if not empreitada:
            return jsonify({"erro": "Empreitada não encontrada"}), 404
        db.session.delete(empreitada)
        db.session.commit()
        return jsonify({"sucesso": "Empreitada deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao deletar empreitada", "details": str(e)}), 500


# --- ROTAS DE PAGAMENTOS DE EMPREITADAS ---
@app.route('/empreitadas/<int:empreitada_id>/pagamentos', methods=['POST'])
def add_pagamento_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id}/pagamentos (POST) acessada ---")
    try:
        empreitada = db.session.get(Empreitada, empreitada_id)
        if not empreitada:
            return jsonify({"erro": "Empreitada não encontrada para adicionar pagamento"}), 404

        dados = request.json
        campos_obrigatorios = ['data', 'valor', 'status']
        if not dados or not all(campo in dados and dados[campo] is not None and dados[campo] != '' for campo in campos_obrigatorios):
             return jsonify({"erro": "Campos 'data', 'valor' e 'status' são obrigatórios para o pagamento."}), 400


        try:
            valor_float = float(dados['valor'])
            if valor_float < 0: raise ValueError("Valor não pode ser negativo")
            data_date = datetime.date.fromisoformat(dados['data'])
            status_valido = dados['status'] in ['Pago', 'A Pagar']
            if not status_valido: raise ValueError("Status inválido")
        except (ValueError, TypeError) as ve:
             print(f"--- [ERRO VALIDAÇÃO] add_pagamento_empreitada: {str(ve)} ---")
             return jsonify({"erro": f"Dados inválidos: {str(ve)}"}), 400

        novo_pagamento = PagamentoEmpreitada(
            empreitada_id=empreitada_id,
            data=data_date,
            valor=valor_float,
            status=dados['status'] # Usa o status enviado ('Pago' ou 'A Pagar')
        )
        db.session.add(novo_pagamento)
        db.session.commit()
        # É importante retornar a empreitada atualizada para o frontend
        # Reconsulta para garantir que a relação foi atualizada
        db.session.refresh(empreitada)
        return jsonify(empreitada.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id}/pagamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro interno ao adicionar pagamento", "details": str(e)}), 500


@app.route('/empreitadas/<int:empreitada_id>/pagamentos/<int:pagamento_id>', methods=['DELETE'])
def deletar_pagamento_empreitada(empreitada_id, pagamento_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id}/pagamentos/{pagamento_id} (DELETE) acessada ---")
    try:
        pagamento = db.session.query(PagamentoEmpreitada).filter_by(
            id=pagamento_id,
            empreitada_id=empreitada_id
        ).first()
        if not pagamento:
             return jsonify({"erro": "Pagamento não encontrado nesta empreitada"}), 404

        db.session.delete(pagamento)
        db.session.commit()
        return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/.../pagamentos (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao deletar pagamento", "details": str(e)}), 500


# --- NOVA ROTA PARA ALTERAR STATUS DO PAGAMENTO DA EMPREITADA ---
@app.route('/pagamentos_empreitada/<int:pagamento_id>/toggle_status', methods=['PATCH'])
def toggle_pagamento_empreitada_status(pagamento_id):
    print(f"--- [LOG] Rota /pagamentos_empreitada/{pagamento_id}/toggle_status (PATCH) acessada ---")
    try:
        pagamento = db.session.get(PagamentoEmpreitada, pagamento_id)
        if not pagamento:
            return jsonify({"erro": "Pagamento de empreitada não encontrado"}), 404

        # Alterna o status
        pagamento.status = 'A Pagar' if pagamento.status == 'Pago' else 'Pago'

        db.session.commit()
        print(f"--- [LOG] Status do pagamento {pagamento_id} alterado para {pagamento.status} ---")
        # Retorna o pagamento atualizado
        return jsonify(pagamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /pagamentos_empreitada/{pagamento_id}/toggle_status (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao alterar status do pagamento", "details": str(e)}), 500

# -------------------------------------------------------------------

# --- ROTAS DE EXPORTAÇÃO ---
# (As rotas /export/csv e /export/pdf_pendentes permanecem as mesmas)
@app.route('/obras/<int:obra_id>/export/csv', methods=['GET'])
def export_csv(obra_id):
    print(f"--- [LOG] Rota /export/csv (GET) para obra_id={obra_id} ---")
    try:
        obra = db.session.get(Obra, obra_id)
        if not obra: return jsonify({"erro": "Obra não encontrada"}), 404
        items = obra.lancamentos if obra.lancamentos else [] # Apenas lançamentos gerais

        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['Data', 'Descricao', 'Tipo', 'Valor', 'Status', 'PIX'])

        for item in items:
            cw.writerow([
                item.data.isoformat() if item.data else '',
                item.descricao,
                item.tipo,
                item.valor,
                item.status,
                item.pix or ''
            ])

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename=relatorio_obra_{obra.id}_lancamentos_gerais.csv" # Nome mais específico
        output.headers["Content-type"] = "text/csv"
        return output
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /export/csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Erro ao gerar CSV", "details": str(e)}), 500


@app.route('/obras/<int:obra_id>/export/pdf_pendentes', methods=['GET'])
def export_pdf_pendentes(obra_id):
    print(f"--- [LOG] Rota /export/pdf_pendentes (GET) para obra_id={obra_id} ---")
    try:
        obra = db.session.get(Obra, obra_id)
        if not obra: return jsonify({"erro": "Obra não encontrada"}), 404

        # Busca lançamentos gerais pendentes
        lancamentos_pendentes = db.session.query(Lancamento).filter_by(obra_id=obra.id, status='A Pagar').all()
        # Busca pagamentos de empreitadas pendentes
        pagamentos_emp_pendentes = db.session.query(PagamentoEmpreitada).join(Empreitada).filter(
            Empreitada.obra_id == obra.id,
            PagamentoEmpreitada.status == 'A Pagar'
        ).all()

        # Combina e ordena por data (opcional)
        items_pendentes = []
        if lancamentos_pendentes:
             items_pendentes.extend(lancamentos_pendentes)
        if pagamentos_emp_pendentes:
             # Adiciona informação da empreitada para o PDF
             for pag in pagamentos_emp_pendentes:
                 # Acessa a relação backref para obter a empreitada
                 empreitada_nome = pag.empreitada.nome if pag.empreitada else 'Empreitada Desconhecida'
                 empreitada_pix = pag.empreitada.pix if pag.empreitada else 'N/I'
                 pag.descricao_pdf = f"Pag. Emp.: {empreitada_nome}" # Descrição para PDF
                 pag.tipo_pdf = "Empreitada"
                 pag.pix_pdf = empreitada_pix
                 items_pendentes.append(pag)


        # Ordena por data (mais recente primeiro)
        items_pendentes.sort(key=lambda x: x.data if x.data else datetime.date.min, reverse=True)


        buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            topMargin=2*cm,
            bottomMargin=2*cm,
            leftMargin=1.5*cm, # Ajuste de margem
            rightMargin=1.5*cm
        )
        elements = []

        styles = getSampleStyleSheet()

        title_text = f"<b>Relatório de Pagamentos Pendentes (Geral + Empreitadas)</b><br/><br/>Obra: {obra.nome}<br/>Cliente: {obra.cliente or 'N/A'}"
        title = Paragraph(title_text, styles['h1']) # Usar h1 para título maior
        elements.append(title)
        elements.append(Spacer(1, 0.8*cm))

        if not items_pendentes:
            no_items = Paragraph("Nenhum pagamento pendente nesta obra.", styles['Normal'])
            elements.append(no_items)
        else:
            data = [['Data', 'Tipo', 'Descrição', 'Valor', 'PIX']]
            total_pendente = 0.0

            for item in items_pendentes:
                # Determina os campos com base no tipo de item
                is_empreitada = hasattr(item, 'descricao_pdf') # Verifica se tem o atributo adicionado
                data_str = item.data.strftime('%d/%m/%Y') if item.data else 'N/A'
                tipo_str = item.tipo_pdf if is_empreitada else (item.tipo or 'N/A')
                desc_str = item.descricao_pdf if is_empreitada else (item.descricao or 'N/A')
                valor_num = item.valor or 0.0
                pix_str = item.pix_pdf if is_empreitada else (item.pix or 'N/I')

                data.append([
                    data_str,
                    tipo_str[:15], # Limita tamanho
                    desc_str[:35], # Limita tamanho
                    formatar_real(valor_num),
                    pix_str[:20] # Limita tamanho
                ])
                total_pendente += valor_num

            # Linha Total
            data.append(['', '', 'TOTAL A PAGAR', formatar_real(total_pendente), ''])

            # Ajusta larguras das colunas
            table = Table(data, colWidths=[2.5*cm, 3*cm, 7*cm, 3*cm, 3*cm])

            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dc3545')), # Vermelho para pendentes
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'), # Centraliza tudo inicialmente
                ('ALIGN', (2, 1), (2, -2), 'LEFT'), # Alinha descrição à esquerda
                ('ALIGN', (4, 1), (4, -2), 'LEFT'), # Alinha PIX à esquerda
                ('ALIGN', (3, 1), (3, -1), 'RIGHT'), # Alinha valor à direita
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), # Fonte negrito no total
                ('FONTSIZE', (0, 0), (-1, -1), 9), # Tamanho de fonte menor
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('TOPPADDING', (0, 0), (-1, 0), 8),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 6), # Padding menor nas linhas
                ('TOPPADDING', (0, 1), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey), # Grid mais fino
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                # Estilo específico para a linha de total
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8d7da')), # Fundo rosa claro
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor('#721c24')), # Texto vermelho escuro
                ('ALIGN', (2, -1), (3, -1), 'RIGHT'), # Alinha TOTAL A PAGAR e valor à direita
            ]))

            elements.append(table)

        elements.append(Spacer(1, 1*cm))
        data_geracao = f"Gerado em: {datetime.datetime.now().strftime('%d/%m/%Y às %H:%M')}"
        footer = Paragraph(data_geracao, styles['Normal'])
        footer.style.alignment = 2 # Alinha à direita
        elements.append(footer)

        doc.build(elements)

        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()

        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        # Nome do arquivo mais descritivo
        response.headers['Content-Disposition'] = f'attachment; filename=relatorio_pendentes_obra_{obra.id}_{datetime.date.today().isoformat()}.pdf'

        return response

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"=" * 80)
        print(f"ERRO ao gerar PDF para obra_id={obra_id}")
        print(f"Erro: {str(e)}")
        print(f"Traceback completo:")
        print(error_details)
        print(f"=" * 80)
        return jsonify({
            "erro": "Erro ao gerar PDF",
            "mensagem": str(e),
            "obra_id": obra_id,
            "details": error_details
        }), 500


# --- INICIALIZAÇÃO DO SERVIDOR ---
# (O if __name__ == '__main__': é removido pois o Gunicorn chama 'app' diretamente)
# Se precisar rodar localmente com `python app.py`, descomente as linhas abaixo
# if __name__ == '__main__':
#     port = int(os.environ.get('PORT', 5000))
#     print(f"--- [LOG] Iniciando servidor Flask LOCALMENTE na porta {port} (debug=True) ---")
#     # debug=True é útil para desenvolvimento local
#     app.run(host='0.0.0.0', port=port, debug=True)