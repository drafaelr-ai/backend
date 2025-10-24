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
            "data": self.data.isoformat(),
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
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "nome": self.nome,
            "responsavel": self.responsavel,
            "valor_global": self.valor_global,
            "pix": self.pix,
            "pagamentos": [p.to_dict() for p in self.pagamentos]
        }

class PagamentoEmpreitada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empreitada_id = db.Column(db.Integer, db.ForeignKey('empreitada.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Pago')
    
    def to_dict(self):
        return {
            "id": self.id,
            "data": self.data.isoformat(),
            "valor": self.valor,
            "status": self.status
        }

# --- FUNÇÃO AUXILIAR PARA FORMATAÇÃO BRASILEIRA ---
def formatar_real(valor):
    """Formata valor para padrão brasileiro: R$ 9.915,00"""
    return f"R$ {valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

# --- ROTAS DA API ---

# --- ROTA DE ADMINISTRAÇÃO (NOVA) ---
# Esta rota é para criar as tabelas no banco de dados.
@app.route('/admin/create_tables', methods=['GET'])
def create_tables():
    print("--- [LOG] Rota /admin/create_tables (GET) acessada ---")
    try:
        with app.app_context():
            db.create_all()
        print("--- [LOG] db.create_all() executado com sucesso. ---")
        return jsonify({"sucesso": "Tabelas criadas no banco de dados."}), 200
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /admin/create_tables: {str(e)}\n{error_details} ---")
        return jsonify({"erro": "Falha ao criar tabelas.", "details": error_details}), 500
# ------------------------------------

@app.route('/', methods=['GET'])
def home():
    print("--- [LOG] Rota / (home) acessada ---")
    return jsonify({"message": "Backend rodando com sucesso!", "status": "OK"}), 200

@app.route('/obras', methods=['GET'])
def get_obras():
    print("--- [LOG] Rota /obras (GET) acessada ---")
    try:
        obras = Obra.query.order_by(Obra.nome).all()
        return jsonify([obra.to_dict() for obra in obras])
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras', methods=['POST'])
def add_obra():
    print("--- [LOG] Rota /obras (POST) acessada ---")
    try:
        dados = request.json
        nova_obra = Obra(
            nome=dados['nome'],
            cliente=dados.get('cliente')
        )
        db.session.add(nova_obra)
        db.session.commit()
        return jsonify(nova_obra.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>', methods=['GET'])
def get_obra_detalhes(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id} (GET) acessada ---")
    try:
        obra = Obra.query.get_or_404(obra_id)
        
        sumarios_lancamentos = db.session.query(
            func.sum(Lancamento.valor).label('total_geral'),
            func.sum(db.case((Lancamento.status == 'Pago', Lancamento.valor), else_=0)).label('total_pago'),
            func.sum(db.case((Lancamento.status == 'A Pagar', Lancamento.valor), else_=0)).label('total_a_pagar')
        ).filter(Lancamento.obra_id == obra_id).first()
        
        sumarios_empreitadas = db.session.query(
            func.sum(PagamentoEmpreitada.valor).label('total_empreitadas_pago')
        ).join(Empreitada).filter(
            Empreitada.obra_id == obra_id,
            PagamentoEmpreitada.status == 'Pago'
        ).first()
        
        total_lancamentos = sumarios_lancamentos.total_geral or 0.0
        total_pago_lancamentos = sumarios_lancamentos.total_pago or 0.0
        total_pago_empreitadas = sumarios_empreitadas.total_empreitadas_pago or 0.0
        total_pago_geral = total_pago_lancamentos + total_pago_empreitadas
        
        total_empreitadas_global = db.session.query(
            func.sum(Empreitada.valor_global)
        ).filter(Empreitada.obra_id == obra_id).scalar() or 0.0
        
        total_geral = total_lancamentos + total_empreitadas_global
        total_a_pagar = total_geral - total_pago_geral
        
        total_por_segmento = db.session.query(
            Lancamento.tipo,
            func.sum(Lancamento.valor)
        ).filter(Lancamento.obra_id == obra_id).group_by(Lancamento.tipo).all()
        
        total_por_mes = db.session.query(
            func.to_char(Lancamento.data, 'MM/YYYY').label('mes_ano'),
            func.sum(Lancamento.valor)
        ).filter(Lancamento.obra_id == obra_id).group_by('mes_ano').all()
        
        sumarios_dict = {
            "total_geral": total_geral,
            "total_pago": total_pago_geral,
            "total_a_pagar": total_a_pagar,
            "total_por_segmento": {tipo: valor for tipo, valor in total_por_segmento},
            "total_por_mes": {mes: valor for mes, valor in total_por_mes}
        }
        
        return jsonify({
            "obra": obra.to_dict(),
            "lancamentos": sorted([l.to_dict() for l in obra.lancamentos], key=lambda x: x['data'], reverse=True),
            "empreitadas": [e.to_dict() for e in obra.empreitadas],
            "sumarios": sumarios_dict
        })
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id} (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>', methods=['DELETE'])
def deletar_obra(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id} (DELETE) acessada ---")
    try:
        obra = Obra.query.get_or_404(obra_id)
        db.session.delete(obra)
        db.session.commit()
        return jsonify({"sucesso": "Obra deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/lancamentos', methods=['POST'])
def add_lancamento(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id}/lancamentos (POST) acessada ---")
    try:
        dados = request.json
        novo_lancamento = Lancamento(
            obra_id=obra_id,
            tipo=dados['tipo'],
            descricao=dados['descricao'],
            valor=float(dados['valor']),
            data=datetime.date.fromisoformat(dados['data']),
            status=dados['status'],
            pix=dados['pix']
        )
        db.session.add(novo_lancamento)
        db.session.commit()
        return jsonify(novo_lancamento.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/lancamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>/pago', methods=['PATCH'])
def marcar_como_pago(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id}/pago (PATCH) acessada ---")
    try:
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        lancamento.status = 'Pago'
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id}/pago (PATCH): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['PUT'])
def editar_lancamento(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (PUT) acessada ---")
    try:
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        dados = request.json
        lancamento.data = datetime.date.fromisoformat(dados['data'])
        lancamento.descricao = dados['descricao']
        lancamento.valor = float(dados['valor'])
        lancamento.tipo = dados['tipo']
        lancamento.status = dados['status']
        lancamento.pix = dados['pix']
        db.session.commit()
        return jsonify(lancamento.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE'])
def deletar_lancamento(lancamento_id):
    print(f"--- [LOG] Rota /lancamentos/{lancamento_id} (DELETE) acessada ---")
    try:
        lancamento = Lancamento.query.get_or_404(lancamento_id)
        db.session.delete(lancamento)
        db.session.commit()
        return jsonify({"sucesso": "Lançamento deletado"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /lancamentos/{lancamento_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/empreitadas', methods=['POST'])
def add_empreitada(obra_id):
    print(f"--- [LOG] Rota /obras/{obra_id}/empreitadas (POST) acessada ---")
    try:
        dados = request.json
        nova_empreitada = Empreitada(
            obra_id=obra_id,
            nome=dados['nome'],
            responsavel=dados['responsavel'],
            valor_global=float(dados['valor_global']),
            pix=dados['pix']
        )
        db.session.add(nova_empreitada)
        db.session.commit()
        return jsonify(nova_empreitada.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras/{obra_id}/empreitadas (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>', methods=['PUT'])
def editar_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id} (PUT) acessada ---")
    try:
        empreitada = Empreitada.query.get_or_404(empreitada_id)
        dados = request.json
        empreitada.nome = dados.get('nome', empreitada.nome)
        empreitada.responsavel = dados.get('responsavel', empreitada.responsavel)
        empreitada.valor_global = float(dados.get('valor_global', empreitada.valor_global))
        empreitada.pix = dados.get('pix', empreitada.pix)
        db.session.commit()
        return jsonify(empreitada.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id} (PUT): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>', methods=['DELETE'])
def deletar_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id} (DELETE) acessada ---")
    try:
        empreitada = Empreitada.query.get_or_404(empreitada_id)
        db.session.delete(empreitada)
        db.session.commit()
        return jsonify({"sucesso": "Empreitada deletada com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id} (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>/pagamentos', methods=['POST'])
def add_pagamento_empreitada(empreitada_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id}/pagamentos (POST) acessada ---")
    try:
        dados = request.json
        novo_pagamento = PagamentoEmpreitada(
            empreitada_id=empreitada_id,
            data=datetime.date.fromisoformat(dados['data']),
            valor=float(dados['valor']),
            status=dados.get('status', 'Pago')
        )
        db.session.add(novo_pagamento)
        db.session.commit()
        empreitada_atualizada = Empreitada.query.get(empreitada_id)
        return jsonify(empreitada_atualizada.to_dict())
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/{empreitada_id}/pagamentos (POST): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/empreitadas/<int:empreitada_id>/pagamentos/<int:pagamento_id>', methods=['DELETE'])
def deletar_pagamento_empreitada(empreitada_id, pagamento_id):
    print(f"--- [LOG] Rota /empreitadas/{empreitada_id}/pagamentos/{pagamento_id} (DELETE) acessada ---")
    try:
        pagamento = PagamentoEmpreitada.query.filter_by(
            id=pagamento_id, 
            empreitada_id=empreitada_id
        ).first_or_404()
        db.session.delete(pagamento)
        db.session.commit()
        return jsonify({"sucesso": "Pagamento deletado com sucesso"}), 200
    except Exception as e:
        db.session.rollback()
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /empreitadas/.../pagamentos (DELETE): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/export/csv', methods=['GET'])
def export_csv(obra_id):
    print(f"--- [LOG] Rota /export/csv (GET) para obra_id={obra_id} ---")
    try:
        obra = Obra.query.get_or_404(obra_id)
        items = obra.lancamentos
        
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(['Data', 'Descricao', 'Tipo', 'Valor', 'Status', 'PIX'])
        
        for item in items:
            cw.writerow([
                item.data.isoformat(),
                item.descricao,
                item.tipo,
                item.valor,
                item.status,
                item.pix
            ])
        
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename=relatorio_obra_{obra.id}.csv"
        output.headers["Content-type"] = "text/csv"
        return output
    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /export/csv: {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500

@app.route('/obras/<int:obra_id>/export/pdf_pendentes', methods=['GET'])
def export_pdf_pendentes(obra_id):
    print(f"--- [LOG] Rota /export/pdf_pendentes (GET) para obra_id={obra_id} ---")
    try:
        obra = Obra.query.get_or_404(obra_id)
        items = Lancamento.query.filter_by(obra_id=obra.id, status='A Pagar').all()
        
        buffer = io.BytesIO()
        
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            topMargin=2*cm,
            bottomMargin=2*cm,
            leftMargin=2*cm,
            rightMargin=2*cm
        )
        elements = []
        
        styles = getSampleStyleSheet()
        
        title_text = f"<b>Relatorio de Pagamentos Pendentes</b><br/><br/>Obra: {obra.nome}<br/>Cliente: {obra.cliente or 'N/A'}"
        title = Paragraph(title_text, styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 1*cm))
        
        if not items:
            no_items = Paragraph("Nenhum pagamento pendente nesta obra.", styles['Normal'])
            elements.append(no_items)
        else:
            data = [['Data', 'Tipo', 'Descricao', 'Valor', 'PIX']]
            total_pendente = 0
            
            for item in items:
                data.append([
                    item.data.strftime('%d/%m/%Y'),
                    item.tipo[:15] if item.tipo else 'N/A',
                    item.descricao[:35] if item.descricao else 'N/A',
                    formatar_real(item.valor),
                    (item.pix or 'Nao informado')[:20]
                ])
                total_pendente += item.valor
            
            data.append(['', '', 'TOTAL A PAGAR', formatar_real(total_pendente), ''])
            
            table = Table(data, colWidths=[3*cm, 3*cm, 6*cm, 3*cm, 4*cm])
            
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#007bff')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('TOPPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -2), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
                ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f8f9fa')]),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#28a745')),
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.whitesmoke),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, -1), (-1, -1), 11),
                ('ALIGN', (2, -1), (3, -1), 'RIGHT'),
            ]))
            
            elements.append(table)
        
        elements.append(Spacer(1, 1*cm))
        data_geracao = f"Gerado em: {datetime.datetime.now().strftime('%d/%m/%Y as %H:%M')}"
        footer = Paragraph(data_geracao, styles['Normal'])
        elements.append(footer)
        
        doc.build(elements)
        
        buffer.seek(0)
        pdf_data = buffer.read()
        buffer.close()
        
        response = make_response(pdf_data)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=pagamentos_pendentes_obra_{obra.id}.pdf'
        
        return response
        
    except Exception as e:
        # Erro de digitação corrigido aqui (era 'error_detail')
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"--- [LOG] Iniciando servidor Flask na porta {port} (debug=True) ---")
    # debug=True nos dará logs de erro mais detalhados no Railway
    app.run(host='0.0.0.0', port=port, debug=True)

