from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from urllib.parse import quote_plus
import datetime
from sqlalchemy import func
import io
import csv
from fpdf import FPDF
import os

app = Flask(__name__)
# Permite conexões do seu frontend local E do seu futuro site na Vercel
CORS(app, origins=["http://localhost:3000", "https://frontend-exjt85rjo-drafaelr-ais-projects.vercel.app"], supports_credentials=True)

# --- CONFIGURAÇÃO DA CONEXÃO (A SUA VERSÃO FUNCIONAL) ---
DB_USER = "postgres.kwmuiviyqjcxawuiqkrl"
DB_PASSWORD = "Controledeobras2025"
DB_HOST = "aws-1-sa-east-1.pooler.supabase.com"
DB_PORT = "5432"
DB_NAME = "postgres"

encoded_password = quote_plus(DB_PASSWORD)
DATABASE_URL = f"postgresql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# --- FIM DA CONFIGURAÇÃO ---

# --- MODELOS DO BANCO DE DADOS (COM INDENTAÇÃO CORRIGIDA) ---
class Obra(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cliente = db.Column(db.String(150))
    lancamentos = db.relationship('Lancamento', backref='obra', lazy=True, cascade="all, delete-orphan")
    empreitadas = db.relationship('Empreitada', backref='obra', lazy=True, cascade="all, delete-orphan")
    def to_dict(self): return {"id": self.id, "nome": self.nome, "cliente": self.cliente}

class Lancamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='A Pagar')
    pix = db.Column(db.String(100))
    def to_dict(self): return {"id": self.id, "obra_id": self.obra_id, "tipo": self.tipo, "descricao": self.descricao, "valor": self.valor, "data": self.data.isoformat(), "status": self.status, "pix": self.pix}

class Empreitada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey('obra.id'), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    responsavel = db.Column(db.String(150))
    valor_global = db.Column(db.Float, nullable=False)
    pix = db.Column(db.String(100))
    pagamentos = db.relationship('PagamentoEmpreitada', backref='empreitada', lazy=True, cascade="all, delete-orphan")
    def to_dict(self): return {"id": self.id, "obra_id": self.obra_id, "nome": self.nome, "responsavel": self.responsavel, "valor_global": self.valor_global, "pix": self.pix, "pagamentos": [p.to_dict() for p in self.pagamentos]}

class PagamentoEmpreitada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empreitada_id = db.Column(db.Integer, db.ForeignKey('empreitada.id'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    def to_dict(self): return {"data": self.data.isoformat(), "valor": self.valor}
# --- FIM DOS MODELOS ---

# --- COMANDO PARA CRIAR AS TABELAS ---

# --- FIM DO COMANDO ---

# --- ROTAS DA API (COM INDENTAÇÃO CORRIGIDA) ---
@app.route('/obras', methods=['GET'])
def get_obras():
    obras = Obra.query.order_by(Obra.nome).all()
    return jsonify([obra.to_dict() for obra in obras])

@app.route('/obras', methods=['POST'])
def add_obra():
    dados = request.json
    nova_obra = Obra(nome=dados['nome'], cliente=dados.get('cliente'))
    db.session.add(nova_obra); db.session.commit()
    return jsonify(nova_obra.to_dict()), 201

@app.route('/obras/<int:obra_id>', methods=['GET'])
def get_obra_detalhes(obra_id):
    obra = Obra.query.get_or_404(obra_id)
    sumarios_query = db.session.query(func.sum(Lancamento.valor).label('total_geral'), func.sum(db.case((Lancamento.status == 'Pago', Lancamento.valor), else_=0)).label('total_pago'), func.sum(db.case((Lancamento.status == 'A Pagar', Lancamento.valor), else_=0)).label('total_a_pagar')).filter(Lancamento.obra_id == obra_id).first()
    total_por_segmento = db.session.query(Lancamento.tipo, func.sum(Lancamento.valor)).filter(Lancamento.obra_id == obra_id).group_by(Lancamento.tipo).all()
    total_por_mes = db.session.query(func.to_char(Lancamento.data, 'MM/YYYY').label('mes_ano'), func.sum(Lancamento.valor)).filter(Lancamento.obra_id == obra_id).group_by('mes_ano').all()
    sumarios_dict = {"total_geral": sumarios_query.total_geral or 0.0, "total_pago": sumarios_query.total_pago or 0.0, "total_a_pagar": sumarios_query.total_a_pagar or 0.0, "total_por_segmento": {tipo: valor for tipo, valor in total_por_segmento}, "total_por_mes": {mes: valor for mes, valor in total_por_mes}}
    return jsonify({"obra": obra.to_dict(), "lancamentos": sorted([l.to_dict() for l in obra.lancamentos], key=lambda x: x['data'], reverse=True), "empreitadas": [e.to_dict() for e in obra.empreitadas], "sumarios": sumarios_dict})

@app.route('/obras/<int:obra_id>/lancamentos', methods=['POST'])
def add_lancamento(obra_id):
    dados = request.json
    novo_lancamento = Lancamento(obra_id=obra_id, tipo=dados['tipo'], descricao=dados['descricao'], valor=float(dados['valor']), data=datetime.date.fromisoformat(dados['data']), status=dados['status'], pix=dados['pix'])
    db.session.add(novo_lancamento); db.session.commit()
    return jsonify(novo_lancamento.to_dict()), 201

@app.route('/lancamentos/<int:lancamento_id>/pago', methods=['PATCH'])
def marcar_como_pago(lancamento_id):
    lancamento = Lancamento.query.get_or_404(lancamento_id); lancamento.status = 'Pago'; db.session.commit()
    return jsonify(lancamento.to_dict())

@app.route('/lancamentos/<int:lancamento_id>', methods=['PUT'])
def editar_lancamento(lancamento_id):
    lancamento = Lancamento.query.get_or_404(lancamento_id); dados = request.json
    lancamento.data = datetime.date.fromisoformat(dados['data']); lancamento.descricao = dados['descricao']; lancamento.valor = float(dados['valor']); lancamento.tipo = dados['tipo']; lancamento.status = dados['status']; lancamento.pix = dados['pix']; db.session.commit()
    return jsonify(lancamento.to_dict())

@app.route('/lancamentos/<int:lancamento_id>', methods=['DELETE'])
def deletar_lancamento(lancamento_id):
    lancamento = Lancamento.query.get_or_404(lancamento_id); db.session.delete(lancamento); db.session.commit()
    return jsonify({"sucesso": "Lançamento deletado"})

@app.route('/obras/<int:obra_id>/empreitadas', methods=['POST'])
def add_empreitada(obra_id):
    dados = request.json
    nova_empreitada = Empreitada(obra_id=obra_id, nome=dados['nome'], responsavel=dados['responsavel'], valor_global=float(dados['valor_global']), pix=dados['pix'])
    db.session.add(nova_empreitada); db.session.commit()
    return jsonify(nova_empreitada.to_dict()), 201

@app.route('/empreitadas/<int:empreitada_id>', methods=['PUT'])
def editar_empreitada(empreitada_id):
    empreitada = Empreitada.query.get_or_404(empreitada_id); dados = request.json
    empreitada.nome = dados.get('nome', empreitada.nome); empreitada.responsavel = dados.get('responsavel', empreitada.responsavel); empreitada.valor_global = float(dados.get('valor_global', empreitada.valor_global)); empreitada.pix = dados.get('pix', empreitada.pix); db.session.commit()
    return jsonify(empreitada.to_dict())

@app.route('/empreitadas/<int:empreitada_id>/pagamentos', methods=['POST'])
def add_pagamento_empreitada(empreitada_id):
    dados = request.json
    novo_pagamento = PagamentoEmpreitada(empreitada_id=empreitada_id, data=datetime.date.fromisoformat(dados['data']), valor=float(dados['valor']))
    db.session.add(novo_pagamento); db.session.commit()
    empreitada_atualizada = Empreitada.query.get(empreitada_id)
    return jsonify(empreitada_atualizada.to_dict())

# Adicionadas as rotas de exportação que estavam faltando
@app.route('/obras/<int:obra_id>/export/csv', methods=['GET'])
def export_csv(obra_id):
    obra = Obra.query.get_or_404(obra_id)
    items = obra.lancamentos
    si = io.StringIO(); cw = csv.writer(si)
    cw.writerow(['Data', 'Descricao', 'Tipo', 'Valor', 'Status', 'PIX'])
    for item in items: cw.writerow([item.data.isoformat(), item.descricao, item.tipo, item.valor, item.status, item.pix])
    output = make_response(si.getvalue()); output.headers["Content-Disposition"] = f"attachment; filename=relatorio_obra_{obra.id}.csv"; output.headers["Content-type"] = "text/csv"; return output

@app.route('/obras/<int:obra_id>/export/pdf_pendentes', methods=['GET'])
def export_pdf_pendentes(obra_id):
    obra = Obra.query.get_or_404(obra_id)
    items = Lancamento.query.filter_by(obra_id=obra.id, status='A Pagar').all()
    
    pdf = FPDF(); pdf.add_page(); pdf.add_font('Arial', '', 'fonts/Arial.ttf', uni=True)
    pdf.set_font("Arial", 'B', 16); pdf.cell(0, 10, "Relatório de Pagamentos Pendentes", 0, 1, 'C')
    pdf.set_font("Arial", '', 12); pdf.cell(0, 10, f"Obra: {obra.nome}", 0, 1, 'C'); pdf.ln(10)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(20, 10, 'Data', 1); pdf.cell(30, 10, 'Tipo', 1); pdf.cell(50, 10, 'Descricao', 1); pdf.cell(25, 10, 'Valor (R$)', 1); pdf.cell(65, 10, 'Dados para Pagamento (PIX)', 1); pdf.ln()
    pdf.set_font("Arial", '', 10); total_pendente = 0
    for item in items:
        data_formatada = item.data.strftime('%d/%m/%Y')
        pix_do_item = item.pix or 'Não informado'
        pdf.cell(20, 10, data_formatada, 1); pdf.cell(30, 10, item.tipo, 1); pdf.cell(50, 10, item.descricao, 1); pdf.cell(25, 10, f"{item.valor:.2f}", 1); pdf.cell(65, 10, pix_do_item, 1); pdf.ln()
        total_pendente += item.valor
    pdf.set_font("Arial", 'B', 10); pdf.cell(125, 10, 'Total a Pagar', 1); pdf.cell(65, 10, f"{total_pendente:.2f}", 1); pdf.ln(20)
    response = make_response(bytes(pdf.output())); response.headers['Content-Type'] = 'application/pdf'; response.headers['Content-Disposition'] = f'attachment; filename=pagamentos_pendentes_obra_{obra.id}.pdf'; return response

# --- NOVA ROTA PARA DELETAR OBRA ---
@app.route('/obras/<int:obra_id>', methods=['DELETE'])
def deletar_obra(obra_id):
    obra = Obra.query.get_or_404(obra_id)
    # SQLAlchemy configurado com 'cascade="all, delete-orphan"' nos relacionamentos
    # irá deletar automaticamente os lançamentos e empreitadas associados.
    db.session.delete(obra)
    db.session.commit()
    return jsonify({"sucesso": f"Obra {obra_id} deletada com sucesso"})
# --- FIM DA NOVA ROTA ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)