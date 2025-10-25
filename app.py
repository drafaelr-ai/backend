# app.py
# Backend Flask para "Minhas Obras"
# - CORS liberado para Vercel (prod + previews) e localhost
# - Postgres via DATABASE_URL (preferido) ou variáveis individuais + sslmode=require
# - Modelos: Obra, Lancamento (com prioridade 0–5), Empreitada, EmpreitadaPagamento
# - Endpoints principais usados pelo frontend

import os
import re
import io
import csv
import datetime as dt
from urllib.parse import quote_plus

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

# -----------------------------------------------------------------------------
# App / CORS
# -----------------------------------------------------------------------------
app = Flask(__name__)

FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "https://frontend-43udpzafm-drafaelr-ais-projects.vercel.app",
).strip()

allowed_origins = [
    FRONTEND_URL,
    re.compile(r"^https://.*-ais-projects\.vercel\.app$"),  # previews do Vercel
    "http://localhost:3000",
]

CORS(
    app,
    resources={r"/**": {"origins": allowed_origins}},
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["Content-Disposition"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    max_age=86400,
)

# -----------------------------------------------------------------------------
# DB (Postgres)
# -----------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

def normalize_db_url(url: str) -> str:
    # Corrige prefixo antigo e garante SSL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url

if DATABASE_URL:
    DATABASE_URL = normalize_db_url(DATABASE_URL)
else:
    # Alternativa: montar a URL a partir de variáveis individuais
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = quote_plus(os.getenv("DB_PASSWORD", ""))
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "postgres")
    DATABASE_URL = (
        f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?sslmode=require"
    )

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -----------------------------------------------------------------------------
# Constantes
# -----------------------------------------------------------------------------
TIPOS_VALIDOS = {"Material", "Mão de Obra", "Serviço", "Equipamentos"}
STATUS_VALIDOS = {"Pago", "A Pagar"}

# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------
class Obra(db.Model):
    __tablename__ = "obras"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    cliente = db.Column(db.String(120), nullable=True)
    criado_em = db.Column(db.DateTime, nullable=False, default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "cliente": self.cliente,
            "criado_em": self.criado_em.isoformat(),
        }


class Lancamento(db.Model):
    __tablename__ = "lancamentos"
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey("obras.id"), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)  # Material / Mão de Obra / Serviço / Equipamentos
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    data = db.Column(db.Date, nullable=False, default=func.current_date())
    status = db.Column(db.String(20), nullable=False, default="A Pagar")  # Pago | A Pagar
    pix = db.Column(db.String(120), nullable=True)
    # NOVO: prioridade (0–5) — só faz sentido quando status == "A Pagar"
    prioridade = db.Column(db.Integer, nullable=True, default=5)

    obra = db.relationship("Obra", backref=db.backref("lancamentos", lazy=True))

    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "tipo": self.tipo,
            "descricao": self.descricao,
            "valor": self.valor,
            "data": self.data.isoformat(),
            "status": self.status,
            "pix": self.pix,
            "prioridade": self.prioridade,
        }


class Empreitada(db.Model):
    __tablename__ = "empreitadas"
    id = db.Column(db.Integer, primary_key=True)
    obra_id = db.Column(db.Integer, db.ForeignKey("obras.id"), nullable=False)
    titulo = db.Column(db.String(160), nullable=False)
    responsavel = db.Column(db.String(160), nullable=True)
    valor_total = db.Column(db.Float, nullable=False, default=0.0)

    obra = db.relationship("Obra", backref=db.backref("empreitadas", lazy=True))

    def to_dict(self):
        return {
            "id": self.id,
            "obra_id": self.obra_id,
            "titulo": self.titulo,
            "responsavel": self.responsavel,
            "valor_total": self.valor_total,
        }


class EmpreitadaPagamento(db.Model):
    __tablename__ = "empreitada_pagamentos"
    id = db.Column(db.Integer, primary_key=True)
    empreitada_id = db.Column(db.Integer, db.ForeignKey("empreitadas.id"), nullable=False)
    valor = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(20), nullable=False, default="A Pagar")  # Pago | A Pagar
    data = db.Column(db.Date, nullable=False, default=func.current_date())

    empreitada = db.relationship(
        "Empreitada",
        backref=db.backref("pagamentos", lazy=True, cascade="all,delete"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "empreitada_id": self.empreitada_id,
            "valor": self.valor,
            "status": self.status,
            "data": self.data.isoformat(),
        }

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def parse_date_yyyy_mm_dd(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def totais_por_segmento(obra_id: int):
    base = (
        db.session.query(Lancamento.tipo, func.coalesce(func.sum(Lancamento.valor), 0.0))
        .filter(Lancamento.obra_id == obra_id)
        .group_by(Lancamento.tipo)
    )
    total = {k: 0.0 for k in TIPOS_VALIDOS}
    for tipo, soma in base:
        total[tipo] = float(soma or 0.0)
    return total


def resumo_obra(obra: Obra):
    soma_lanc = (
        db.session.query(func.coalesce(func.sum(Lancamento.valor), 0.0))
        .filter(Lancamento.obra_id == obra.id)
        .scalar()
        or 0.0
    )
    soma_emp = (
        db.session.query(func.coalesce(func.sum(EmpreitadaPagamento.valor), 0.0))
        .join(Empreitada, EmpreitadaPagamento.empreitada_id == Empreitada.id)
        .filter(Empreitada.obra_id == obra.id)
        .scalar()
        or 0.0
    )

    total_geral = float(soma_lanc + soma_emp)

    pagos_lanc = (
        db.session.query(func.coalesce(func.sum(Lancamento.valor), 0.0))
        .filter(Lancamento.obra_id == obra.id, Lancamento.status == "Pago")
        .scalar()
        or 0.0
    )
    pagos_emp = (
        db.session.query(func.coalesce(func.sum(EmpreitadaPagamento.valor), 0.0))
        .join(Empreitada, EmpreitadaPagamento.empreitada_id == Empreitada.id)
        .filter(Empreitada.obra_id == obra.id, EmpreitadaPagamento.status == "Pago")
        .scalar()
        or 0.0
    )

    total_pago = float(pagos_lanc + pagos_emp)
    total_a_pagar = float(total_geral - total_pago)

    return {
        "total_geral": total_geral,
        "total_pago": total_pago,
        "total_a_pagar": total_a_pagar,
        "por_segmento": totais_por_segmento(obra.id),
    }

# -----------------------------------------------------------------------------
# Rotas utilitárias (health, upgrade e create_tables)
# -----------------------------------------------------------------------------
@app.get("/")
def root():
    return jsonify({"ok": True, "service": "obras-backend"})

@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.post("/admin/upgrade")
def admin_upgrade():
    """
    Garante a coluna 'prioridade' em 'lancamentos' (idempotente).
    Use uma vez após o deploy.
    """
    try:
        with db.engine.connect() as conn:
            conn.exec_driver_sql(
                "ALTER TABLE lancamentos ADD COLUMN IF NOT EXISTS prioridade INTEGER DEFAULT 5"
            )
        return jsonify({"ok": True, "msg": "Coluna 'prioridade' garantida."}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/admin/create_tables")
def create_tables():
    db.create_all()
    return jsonify({"ok": True, "msg": "tables created"})

# -----------------------------------------------------------------------------
# Obras
# -----------------------------------------------------------------------------
@app.get("/obras")
def listar_obras():
    obras = Obra.query.order_by(Obra.criado_em.desc()).all()
    return jsonify([o.to_dict() for o in obras])

@app.post("/obras")
def criar_obra():
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    cliente = (data.get("cliente") or "").strip()
    if not nome:
        return jsonify({"erro": "nome é obrigatório"}), 400
    o = Obra(nome=nome, cliente=cliente)
    db.session.add(o)
    db.session.commit()
    return jsonify(o.to_dict()), 201

@app.delete("/obras/<int:obra_id>")
def remover_obra(obra_id):
    obra = Obra.query.get_or_404(obra_id)
    Lancamento.query.filter_by(obra_id=obra.id).delete()
    Empreitada.query.filter_by(obra_id=obra.id).delete()
    db.session.delete(obra)
    db.session.commit()
    return jsonify({"ok": True})

@app.get("/obras/<int:obra_id>")
def detalhe_obra(obra_id):
    obra = Obra.query.get_or_404(obra_id)

    # Lançamentos
    lanc_list = [l.to_dict() for l in obra.lancamentos]

    # Empreitadas + pagamentos
    emp_list = []
    for e in obra.empreitadas:
        emp_list.append(
            {**e.to_dict(), "pagamentos": [p.to_dict() for p in e.pagamentos]}
        )

    # Histórico unificado (lancamentos + pagamentos de empreitada)
    historico = []
    for l in obra.lancamentos:
        historico.append({
            "id": f"lanc-{l.id}",
            "tipo_registro": "lancamento",
            "data": l.data.isoformat(),
            "descricao": l.descricao,
            "tipo": l.tipo,
            "valor": l.valor,
            "status": l.status,
            "pix": l.pix,
            "prioridade": l.prioridade,  # aparece no front só quando status == "A Pagar"
        })
    for e in obra.empreitadas:
        for p in e.pagamentos:
            historico.append({
                "id": f"emp-{p.id}",
                "tipo_registro": "pagamento_empreitada",
                "data": p.data.isoformat(),
                "descricao": f"Empreitada: {e.titulo}",
                "tipo": "Empreitada",
                "valor": p.valor,
                "status": p.status,
                "pix": None,
                "prioridade": None,  # pagamentos de empreitada não têm prioridade
            })

    # ordena por data desc
    historico.sort(key=lambda x: x["data"], reverse=True)

    return jsonify({
        "obra": obra.to_dict(),
        "lancamentos": lanc_list,
        "empreitadas": emp_list,
        "historico": historico,
        "resumo": resumo_obra(obra),
    })

# -----------------------------------------------------------------------------
# Lançamentos
# -----------------------------------------------------------------------------
@app.post("/obras/<int:obra_id>/lancamentos")
def novo_lancamento(obra_id):
    Obra.query.get_or_404(obra_id)
    data = request.get_json(force=True)

    tipo = data.get("tipo")
    if tipo not in TIPOS_VALIDOS:
        return jsonify({"erro": f"tipo inválido. Use um de {sorted(TIPOS_VALIDOS)}"}), 400

    descricao = (data.get("descricao") or "").strip()
    if not descricao:
        return jsonify({"erro": "descricao é obrigatória"}), 400

    try:
        valor = float(data.get("valor") or 0)
    except Exception:
        return jsonify({"erro": "valor inválido"}), 400

    status = data.get("status") or "A Pagar"
    if status not in STATUS_VALIDOS:
        return jsonify({"erro": "status inválido: use 'Pago' ou 'A Pagar'"}), 400

    data_str = data.get("data")
    d = parse_date_yyyy_mm_dd(data_str) if data_str else dt.date.today()

    # prioridade só faz sentido quando "A Pagar"
    prioridade = data.get("prioridade")
    if status != "A Pagar":
        prioridade = None
    else:
        try:
            prioridade = int(prioridade) if prioridade is not None else 5
            if prioridade < 0 or prioridade > 5:
                raise ValueError
        except Exception:
            prioridade = 5

    l = Lancamento(
        obra_id=obra_id,
        tipo=tipo,
        descricao=descricao,
        valor=valor,
        status=status,
        data=d,
        pix=(data.get("pix") or None),
        prioridade=prioridade,
    )
    db.session.add(l)
    db.session.commit()
    return jsonify(l.to_dict()), 201

@app.put("/lancamentos/<int:lanc_id>")
def editar_lancamento(lanc_id):
    l = Lancamento.query.get_or_404(lanc_id)
    data = request.get_json(force=True)

    if "tipo" in data:
        if data["tipo"] not in TIPOS_VALIDOS:
            return jsonify({"erro": f"tipo inválido. Use um de {sorted(TIPOS_VALIDOS)}"}), 400
        l.tipo = data["tipo"]

    if "descricao" in data:
        desc = (data.get("descricao") or "").strip()
        if not desc:
            return jsonify({"erro": "descricao é obrigatória"}), 400
        l.descricao = desc

    if "valor" in data:
        try:
            l.valor = float(data.get("valor") or 0)
        except Exception:
            return jsonify({"erro": "valor inválido"}), 400

    if "data" in data and data.get("data"):
        l.data = parse_date_yyyy_mm_dd(data["data"])

    if "status" in data:
        st = data["status"]
        if st not in STATUS_VALIDOS:
            return jsonify({"erro": "status inválido: use 'Pago' ou 'A Pagar'"}), 400
        l.status = st

    # prioridade: só se status final for "A Pagar"
    prioridade = data.get("prioridade")
    if l.status != "A Pagar":
        l.prioridade = None
    else:
        if prioridade is not None:
            try:
                prioridade = int(prioridade)
                if not (0 <= prioridade <= 5):
                    raise ValueError
                l.prioridade = prioridade
            except Exception:
                pass  # mantém a anterior

    if "pix" in data:
        l.pix = data.get("pix")

    db.session.commit()
    return jsonify(l.to_dict())

@app.delete("/lancamentos/<int:lanc_id>")
def deletar_lancamento(lanc_id):
    l = Lancamento.query.get_or_404(lanc_id)
    db.session.delete(l)
    db.session.commit()
    return jsonify({"ok": True})

# -----------------------------------------------------------------------------
# Empreitadas
# -----------------------------------------------------------------------------
@app.post("/empreitadas")
def criar_empreitada():
    data = request.get_json(force=True)
    obra_id = int(data.get("obra_id"))
    Obra.query.get_or_404(obra_id)

    titulo = (data.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"erro": "titulo é obrigatório"}), 400

    e = Empreitada(
        obra_id=obra_id,
        titulo=titulo,
        responsavel=(data.get("responsavel") or "").strip() or None,
        valor_total=float(data.get("valor_total") or 0.0),
    )
    db.session.add(e)
    db.session.commit()
    return jsonify(e.to_dict()), 201

@app.post("/empreitadas/<int:emp_id>/pagamentos")
def adicionar_pagamento_empreitada(emp_id):
    e = Empreitada.query.get_or_404(emp_id)
    data = request.get_json(force=True)

    valor = float(data.get("valor") or 0.0)
    status = data.get("status") or "A Pagar"
    if status not in STATUS_VALIDOS:
        return jsonify({"erro": "status inválido: use 'Pago' ou 'A Pagar'"}), 400

    data_str = data.get("data")
    d = parse_date_yyyy_mm_dd(data_str) if data_str else dt.date.today()

    p = EmpreitadaPagamento(
        empreitada_id=e.id,
        valor=valor,
        status=status,
        data=d,
    )
    db.session.add(p)
    db.session.commit()
    return jsonify(p.to_dict()), 201

# -----------------------------------------------------------------------------
# Exportações
# -----------------------------------------------------------------------------
@app.get("/obras/<int:obra_id>/csv")
def export_csv(obra_id):
    obra = Obra.query.get_or_404(obra_id)
    output = io.StringIO()
    w = csv.writer(output, delimiter=";")
    w.writerow(["Data", "Descrição", "Tipo", "Status", "Prioridade", "Valor"])

    # Lançamentos
    for l in obra.lancamentos:
        w.writerow([
            l.data.isoformat(),
            l.descricao,
            l.tipo,
            l.status,
            "" if l.status != "A Pagar" else (l.prioridade if l.prioridade is not None else 5),
            f"{l.valor:.2f}",
        ])

    # Pagamentos de empreitada (sem prioridade)
    for e in obra.empreitadas:
        for p in e.pagamentos:
            w.writerow([p.data.isoformat(), f"Empreitada: {e.titulo}", "Empreitada", p.status, "", f"{p.valor:.2f}"])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"obra_{obra_id}.csv",
    )

# -----------------------------------------------------------------------------
# Run (Railway usa $PORT)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"[BOOT] Running on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
