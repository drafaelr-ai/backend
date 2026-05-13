# Arquitetura — Obraly

Estado pós-refactor das Fases 1-6 (atualizado 12/05/2026).

---

## Backend — Módulo Principal

### Estrutura de arquivos

```
backend-fase4/
├── app.py                  165 linhas — factory create_app() + CORS handlers globais
├── auto_migration.py       472 linhas — startup schema migrations (psycopg2 direto, idempotente)
├── config.py               configuração por ambiente
├── extensions.py           db, jwt, cors, limiter compartilhados (Flask-SQLAlchemy, JWT, flask-cors)
├── logging_setup.py        setup centralizado de logging
├── utils.py                helpers globais (formatar_real)
├── models/                 25 SQLAlchemy models
│   ├── __init__.py
│   ├── agenda_demanda.py
│   ├── anexo_orcamento.py
│   ├── boleto.py
│   ├── caixa_obra.py
│   ├── cronograma_etapa.py
│   ├── cronograma_obra.py
│   ├── diario_imagem.py
│   ├── diario_obra.py
│   ├── fechamento_caixa.py
│   ├── lancamento.py
│   ├── movimentacao_caixa.py
│   ├── nota_fiscal.py
│   ├── notificacao.py
│   ├── obra.py
│   ├── orcamento.py
│   ├── orcamento_eng_etapa.py
│   ├── orcamento_eng_item.py
│   ├── pagamento_futuro.py
│   ├── pagamento_parcelado.py
│   ├── pagamento_servico.py
│   ├── parcela_individual.py
│   ├── servico.py
│   ├── servico_base.py
│   ├── servico_usuario.py
│   └── user.py
├── services/               7 helpers reutilizáveis
│   ├── __init__.py
│   ├── auth_service.py         get_current_user, user_has_access_to_obra, check_permission
│   └── notificacao_service.py  criar_notificacao, notificar_masters/operadores/administradores
└── routes/                 13 blueprints — 186 rotas
    ├── __init__.py
    ├── admin.py
    ├── auth.py
    ├── bi.py
    ├── boletos.py
    ├── caixa.py            url_prefix=/obras/<id>/caixa
    ├── cronograma.py       30 rotas
    ├── diario.py
    ├── lancamentos.py
    ├── notificacoes.py
    ├── obras.py            40 rotas — CRUD principal de obras
    ├── orcamento_eng.py    url_prefix=/obras/<id>/orcamento-eng — 14 rotas
    ├── servicos.py
    └── sid.py              url_prefix=/sid
```

### Blueprints registrados

| Blueprint | Arquivo | Rotas | url_prefix |
|---|---|---|---|
| `notificacoes_bp` | routes/notificacoes.py | 7 | — |
| `bi_bp` | routes/bi.py | 3 | — |
| `diario_bp` | routes/diario.py | 8 | — |
| `auth_bp` | routes/auth.py | 6 | — |
| `admin_bp` | routes/admin.py | 10 | — |
| `sid_bp` | routes/sid.py | 10 | `/sid` |
| `caixa_bp` | routes/caixa.py | 5 | `/obras/<int:obra_id>/caixa` |
| `servicos_bp` | routes/servicos.py | 11 | — |
| `boletos_bp` | routes/boletos.py | 9 | — |
| `lancamentos_bp` | routes/lancamentos.py | 9 | — |
| `cronograma_bp` | routes/cronograma.py | 30 | — |
| `orcamento_eng_bp` | routes/orcamento_eng.py | 14 | `/obras/<int:obra_id>/orcamento-eng` |
| `obras_bp` | routes/obras.py | 40 | — |

### Padrões de arquitetura

**Factory pattern**

```python
# app.py
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    db.init_app(app)
    jwt.init_app(app)
    cors.init_app(app, ...)
    app.after_request(apply_cors_headers)
    app.register_blueprint(obras_bp)
    # ...
    return app

app = create_app()  # instância global para Gunicorn/Fly.io
```

**3 Camadas de CORS (todas preservadas)**

| Camada | Mecanismo | Cobre |
|---|---|---|
| 1 | `flask-cors` via `cors.init_app()` | Protocolo CORS completo |
| 2 | `@app.after_request apply_cors_headers` | Respostas 4xx/5xx sem CORS nativo |
| 3 | `@app.route('/<path>', methods=['OPTIONS'])` | Preflights de rotas dinâmicas |

**Services pattern**

Helpers usados 2+ vezes são extraídos para `services/`. Helpers com 1 único caller ficam no próprio blueprint (ex: `extrair_dados_boleto_pdf` em `routes/boletos.py`).

**Imports em blueprints**

```python
from extensions import db
from services import get_current_user, user_has_access_to_obra
from models.obra import Obra
from models.lancamento import Lancamento
```

**auto_migration.py**

Roda a cada cold start, antes de `create_app()`. Usa `psycopg2` diretamente (sem ORM) para garantir que todas as colunas e tabelas existam. Todos os steps são idempotentes (`IF NOT EXISTS`, `column_name` check).

### Hosting

| Item | Detalhe |
|---|---|
| API | `obraly-api.fly.dev` — 2 machines (Fly.io) |
| DB | Supabase — pooler 6543 (atenção: senhas com special chars → usar `quote_plus`) |
| Deploy dir | `backend-fase4/fly-deploy/obraly-api/` (onde `fly.toml` vive) |
| Deploy cmd | `fly deploy --no-cache --app obraly-api` |

---

## Backend — Módulo Patrimonial

### Estado atual

`backend-fase4/app_admin.py` — monolítico, PRÉ-refactor. Fase 8 planejada para espelhar o refactor do main.

### Hosting

`obraly-admin-api.fly.dev` — Supabase project separado.

---

## Frontend — Módulo Principal

### Estrutura de arquivos

```
frontend-fase6/src/
├── App.js                  85 linhas — roteamento por estado + URL params
├── AppAdmin.js             módulo patrimonial (pré-refactor)
├── config.js               constantes (API_URL, MAX_FILE_SIZE, etc.)
├── styles/
│   └── tokens.css          design tokens v2.0 (variáveis CSS globais)
├── utils/
│   ├── notify.jsx          toasts unificados (substituiu 199 alert/confirm)
│   ├── logger.js           logging centralizado (substituiu 177 console.*)
│   ├── format.js           formatação de R$, datas
│   └── imageCompression.js compressão de imagens antes do upload
├── auth/
│   └── AuthContext.jsx     contexto de autenticação (user, logout, onBackToSelector)
├── layout/
│   ├── WindowsNavBar.jsx   navbar lateral (módulo obras)
│   └── NotificacoesDropdown.jsx  sino de notificações in-app
├── components/
│   ├── Modal/              wrapper unificado (substitui os 26 modais inline de App.js)
│   │   ├── Modal.jsx
│   │   ├── Modal.css
│   │   ├── ModalConfirm.jsx
│   │   ├── ModalView.jsx
│   │   └── useModalKeyboard.js
│   └── modals/             26 modais migrados pro wrapper
│       ├── EditPrioridadeModal.jsx
│       ├── AddLancamentoModal.jsx
│       └── ... (26 arquivos)
└── screens/
    ├── Dashboard/          dashboard panorâmico (NOVO — Fase 6)
    │   ├── index.jsx
    │   ├── Dashboard.css
    │   └── components/
    │       ├── StatCard.jsx
    │       ├── StatCardCompact.jsx
    │       ├── AlertStatCard.jsx
    │       ├── ProgressBar.jsx
    │       ├── ActivityItem.jsx
    │       ├── DashboardHeader.jsx
    │       └── ObraCardActions.jsx
    ├── ObraDetalhe/        detalhe de obra (era "Dashboard" antes da Fase 6)
    │   └── index.jsx
    ├── ModuleSelector/     tela de seleção de módulos
    └── auth/
        └── LoginScreen.jsx
```

### Padrões de roteamento

```
?obra=X   → ObraDetalhe (visão de uma obra)
(sem ?obra) → Dashboard (lista panorâmica de obras)
selectedModule='admin' → AppAdmin.js
```

### Modal pattern

```jsx
import Modal from '../Modal/Modal';

<Modal
  isOpen={isOpen}
  onClose={onClose}
  title="Título"
  width="default"          // default | small | large | xlarge
  footer={
    <>
      <button className="m-btn-cancel" onClick={onClose}>Cancelar</button>
      <button className="m-btn-primary" onClick={handleSave}>Salvar</button>
    </>
  }
>
  {/* corpo */}
</Modal>
```

### Hosting

Vercel — `obraly.uk` + `www.obraly.uk`
