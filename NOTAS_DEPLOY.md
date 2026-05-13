# Obraly API — Deploy Notes

## Fase 4 — Backend Refactor (FECHADA — 12/05/2026)

### Objetivo
Refatorar o monolito `app.py` de 19.516 linhas para uma arquitetura modular com
factory pattern, blueprints por domínio, e separação limpa de responsabilidades.

### Resultado
- **app.py**: 19.516 → **165 linhas** (-99%)
- **186 rotas** em 13 blueprints
- **25 models** extraídos para `models/`
- **7 helpers** em `services/`
- **Factory pattern** com `create_app()` + 3 camadas CORS
- **Deploy estável em produção: v26**

---

### Estrutura final

```
backend-fase4/
├── app.py                  # 165 linhas — factory + global handlers
├── auto_migration.py       # startup schema migrations (idempotente)
├── config.py               # configuração por ambiente
├── extensions.py           # SQLAlchemy, JWT, CORS, Limiter compartilhados
├── logging_setup.py
├── utils.py                # formatar_real e outros helpers
├── models/                 # 25 SQLAlchemy models
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
├── services/               # 7 funções helper
│   ├── __init__.py
│   ├── auth_service.py     # get_current_user, user_has_access_to_obra, check_permission
│   └── notificacao_service.py  # criar_notificacao, notificar_masters/operadores/admins
├── routes/                 # 13 blueprints
│   ├── __init__.py
│   ├── admin.py
│   ├── auth.py
│   ├── bi.py
│   ├── boletos.py
│   ├── caixa.py            # url_prefix=/obras/<id>/caixa
│   ├── cronograma.py       # 30 rotas
│   ├── diario.py
│   ├── lancamentos.py
│   ├── notificacoes.py
│   ├── obras.py            # 40 rotas — CRUD principal
│   ├── orcamento_eng.py    # url_prefix=/obras/<id>/orcamento-eng — 14 rotas
│   ├── servicos.py
│   └── sid.py              # url_prefix=/sid
└── fly-deploy/
    └── obraly-api/
        ├── fly.toml
        └── Dockerfile
```

---

### Blueprints registrados

| Blueprint | Arquivo | Rotas | url_prefix |
|---|---|---|---|
| `notificacoes_bp` | routes/notificacoes.py | 7 | — |
| `bi_bp` | routes/bi.py | 3 | — |
| `diario_bp` | routes/diario.py | 8 | — |
| `auth_bp` | routes/auth.py | 6 | — |
| `admin_bp` | routes/admin.py | 10 | — |
| `sid_bp` | routes/sid.py | 10 | `/sid` |
| `caixa_bp` | routes/caixa.py | 5 | `/obras/<id>/caixa` |
| `servicos_bp` | routes/servicos.py | 11 | — |
| `boletos_bp` | routes/boletos.py | 9 | — |
| `lancamentos_bp` | routes/lancamentos.py | 9 | — |
| `cronograma_bp` | routes/cronograma.py | 30 | — |
| `orcamento_eng_bp` | routes/orcamento_eng.py | 14 | `/obras/<id>/orcamento-eng` |
| `obras_bp` | routes/obras.py | 40 | — |

---

### CORS (3 camadas)

1. **flask-cors** — `cors.init_app(app, resources={r'/*': {'origins': ALLOWED_ORIGINS}})`
2. **after_request** — `apply_cors_headers()` garante headers em toda resposta
3. **catch-all OPTIONS** — `@app.route('/<path:any_path>', methods=['OPTIONS'])` retorna 200

---

### Incidentes recuperados

| Versão | Incidente | Correção |
|---|---|---|
| v20 | Dockerfile sem `.dockerignore`, `models/` não copiado | `.dockerignore` criado + `COPY . .` |
| v21 | `IN (%(id_1)s, ...)` bind error no `/orcamento-eng` | Substituído por `ANY(:ids)` com array |
| v22 | Deploy estabilizado após fix v21 | — |

---

### Sub-lotes da Fase 4

| Sub-lote | Descrição | Deploy |
|---|---|---|
| A | Modelos extraídos para `models/` | — |
| B | Services extraídos para `services/` | — |
| C | Factory pattern `create_app()` | v20 |
| D | Fixes pós-factory (v21/v22) | v22 |
| E (Batch 1) | `notificacoes_bp`, `bi_bp`, `diario_bp` | v23 |
| E (Batch 1+) | `auth_bp`, `admin_bp`, `sid_bp` | v24 |
| E (Batch 2) | `caixa_bp`, `servicos_bp`, `boletos_bp`, `lancamentos_bp` | v25 |
| E (Batch 3) | `cronograma_bp`, `orcamento_eng_bp`, `obras_bp` | **v26** |
| F | Cleanup: imports, docstrings, `auto_migration.py` | — (sem redeploy) |

---

### Deploy

```bash
# Sempre a partir do diretório com fly.toml:
Set-Location "C:\PROJETOS\obraly\backend-fase4\fly-deploy\obraly-api"
fly deploy --no-cache --app obraly-api
```

**Rollback:**
```bash
fly releases rollback --app obraly-api
```
